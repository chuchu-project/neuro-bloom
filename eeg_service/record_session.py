"""ExpandableEEG 实验数据采集（Mac / Windows 通用，基于 BrainFlow）。

用途：把采集板的原始数据录成 CSV（每行一个采样点，列为各通道），用于实验存档或线下分析。
支持边采边写，长时采集不会受内存缓冲限制；Ctrl-C 可提前结束并保留已采数据。

用法：
    # 合成板自测（不接设备，先验证流程），采 5 秒
    EEG_BOARD_ID=-1 EEG_SECONDS=5 python eeg_service/record_session.py

    # 真设备：WiFi AP 模式，16 通道
    EEG_BOARD_ID=6 EEG_IP=192.168.4.1 EEG_SECONDS=120 EEG_SUBJECT=s01 \
        python eeg_service/record_session.py

环境变量（与 eeg_service/main.py 一致）：
    EEG_BOARD_ID   6=Cyton+Daisy WiFi(16ch) / 5=Cyton WiFi(8ch) / 0=串口蓝牙 / -1=合成板
    EEG_IP         AP 模式 192.168.4.1；client 模式填设备输出的 IP
    EEG_IP_PORT    本机（电脑）监听端口，默认 6677（不是板子的端口）
    EEG_IP_PROTOCOL 0=不用 IP 协议族（本设备用这个）/ 1=UDP / 2=TCP，默认 0
    EEG_BOARD_COMMAND 连上后发给板子的命令，WiFi 板默认 "~6"（250Hz 标准封包）
    EEG_WIFI_SAMPLE_RATE WiFi 板实际采样率，默认 250
    EEG_SERIAL_PORT 蓝牙/串口模式，例 /dev/tty.usbserial-XXXX
    EEG_SAMPLE_RATE 实测采样率；0 或不填用上面的口径
    EEG_SECONDS    采集秒数，默认 60
    EEG_SUBJECT    被试/会话标识，参与文件名，默认 sub
    EEG_OUTDIR     输出目录，默认 <项目>/data

⚠️ 板子只在 250Hz 下用标准封包；默认 1000Hz 时通道会整体错位，所以这里连着板子后
   会先发 "~6" 把板子设到 250Hz，采样率也按 250 算。
"""
from __future__ import annotations

import csv
import os
import sys
import time
from pathlib import Path

from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams


WIFI_CYTON_BOARDS = {
    int(BoardIds.CYTON_WIFI_BOARD.value),
    int(BoardIds.CYTON_DAISY_WIFI_BOARD.value),
}


def build_params() -> BrainFlowInputParams:
    params = BrainFlowInputParams()
    serial = os.getenv("EEG_SERIAL_PORT", "")
    ip = os.getenv("EEG_IP", "")
    if serial:
        params.serial_port = serial
    if ip:
        params.ip_address = ip
        # 本机监听端口，不是板子的端口；板子网页在 192.168.4.1:80
        params.ip_port = int(os.getenv("EEG_IP_PORT", "6677"))
        params.ip_protocol = int(os.getenv("EEG_IP_PROTOCOL", "0"))
    params.timeout = 10
    return params


def row_names(board_id: int, n_rows: int, eeg_rows: list[int]) -> list[str]:
    """给每一行起个名。行号口径来自 BrainFlow，别照着猜：
    0=包序号、EEG 行=1..16、17-19=加速度、20-26=其他、27-29=模拟、30=时间戳(秒)。
    """
    eeg_set = set(eeg_rows)
    try:
        pkg_row = BoardShim.get_package_num_channel(board_id)
        ts_row = BoardShim.get_timestamp_channel(board_id)
    except Exception:  # noqa: BLE001
        pkg_row, ts_row = 0, -1
    names = []
    for i in range(n_rows):
        if i == pkg_row:
            names.append("package_num")
        elif i == ts_row:
            names.append("timestamp")
        elif i in eeg_set:
            names.append(f"eeg_{i}")
        else:
            names.append(f"row_{i}")
    return names


def main() -> int:
    BoardShim.disable_board_logger()
    board_id = int(os.getenv("EEG_BOARD_ID", str(BoardIds.SYNTHETIC_BOARD.value)))
    seconds = float(os.getenv("EEG_SECONDS", "60"))
    subject = os.getenv("EEG_SUBJECT", "sub")
    fs_override = int(os.getenv("EEG_SAMPLE_RATE", "0"))
    root = Path(__file__).resolve().parent.parent
    outdir = Path(os.getenv("EEG_OUTDIR", str(root / "data")))
    outdir.mkdir(parents=True, exist_ok=True)

    csv_path = outdir / f"eeg_{subject}_{time.strftime('%Y%m%d_%H%M%S')}.csv"

    board = BoardShim(board_id, build_params())
    print(f"[record] 正在连接板卡 id={board_id} …", flush=True)
    board.prepare_session()

    # WiFi 板必须在取流前设到 250Hz：默认 1000Hz 时 BrainFlow 解出来的通道整体错位。
    command = os.getenv("EEG_BOARD_COMMAND",
                        "~6" if board_id in WIFI_CYTON_BOARDS else "").strip()
    if command:
        try:
            board.config_board(command)
            print(f"[record] 已设置板卡：{command}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[record] ⚠️ 设置板卡失败（{type(e).__name__}: {e}）", flush=True)
    board.start_stream()

    fs = fs_override or BoardShim.get_sampling_rate(board_id)
    if board_id in WIFI_CYTON_BOARDS and not fs_override:
        fs = int(os.getenv("EEG_WIFI_SAMPLE_RATE", "250"))   # 标称 1000Hz 与实测不符
    eeg_ch = BoardShim.get_eeg_channels(board_id)
    print(f"[record] 采样率 {fs} Hz，EEG 通道 {len(eeg_ch)} 个 {eeg_ch}", flush=True)
    print(f"[record] 采集 {seconds:.0f} 秒（Ctrl-C 提前结束）-> {csv_path}", flush=True)

    t0 = time.time()
    last_report = 0.0
    total = 0
    header_written = False
    try:
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            while time.time() - t0 < seconds:
                chunk = board.get_board_data()          # 消费新数据
                if chunk.shape[1] == 0:
                    time.sleep(0.2)
                    continue
                if not header_written:
                    writer.writerow(row_names(board_id, chunk.shape[0], eeg_ch))
                    header_written = True
                writer.writerows(chunk.T.tolist())
                f.flush()
                total += chunk.shape[1]
                el = time.time() - t0
                if el - last_report >= 1.0:
                    last_report = el
                    print(f"[record] {el:5.1f}s / {seconds:.0f}s   已采 {total} 样本 "
                          f"(约 {total / el:.0f} Hz)", flush=True)
    except KeyboardInterrupt:
        print("\n[record] 收到 Ctrl-C，提前结束并保存已采数据。", flush=True)
    finally:
        try:
            board.stop_stream()
        except Exception:
            pass
        try:
            board.release_session()
        except Exception:
            pass

    el = time.time() - t0
    print(flush=True)
    if total == 0:
        print("[record] 未采到任何样本，请检查设备连接。", flush=True)
        return 1
    print(f"[record] 完成：{total} 样本 / {el:.1f}s，实测约 {total / el:.1f} Hz")
    print(f"[record] 数据已保存：{csv_path}")
    print("[record] CSV 说明：第 1 列为 timestamp(秒)，eeg_N 列为第 N 个 BrainFlow 行"
          "（EEG 数据行号见上方打印），其余 row_N 为加速度/其他/标记行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
