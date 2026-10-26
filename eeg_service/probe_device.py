"""ExpandableEEG 采集板连接探测（Mac / Windows 通用，基于 BrainFlow）。

作用：不依赖 OpenBCI GUI，直接确认 BrainFlow 能否连上采集板，并自动识别：
  - 板子被识别成 8 通道(Cyton) 还是 16 通道(Cyton+Daisy)
  - 实际采样率、EEG 通道号、信号峰峰值
  - 顺手把几秒原始数据存成 CSV

用法（先让 Mac 连上热点 ExpandableEEG AP，密码 abcd1234）：
    python eeg_service/probe_device.py
    EEG_IP=192.168.4.1 EEG_SECONDS=5 python eeg_service/probe_device.py

可用环境变量（与 eeg_service/main.py 同名；旧的 EEG_PORT / EEG_PROTOCOL 仍兼容）：
    EEG_IP           采集板 IP。AP 模式固定 192.168.4.1；client 模式填设备输出的 IP
    EEG_IP_PORT      本机（电脑）监听端口，默认 6677。不是板子的端口，板子网页在 <IP>:80
    EEG_IP_PROTOCOL  BrainFlow 协议号：0=NO_IP_PROTOCOL(板卡默认，本设备用这个)、
                     1=UDP、2=TCP。默认 0
    EEG_BOARD        只测指定 BrainFlow 板卡 id（16 通道试 6，8 通道试 5，-1 为合成板自测）
    EEG_SECONDS      采集秒数，默认 3
    EEG_OUTDIR       CSV 输出目录，默认 <项目>/data

注意：本脚本会把 板卡默认 / TCP / UDP 三种协议依次试一遍，所以不用自己猜协议。
      WiFi 板（5/6）连上后会先发 EEG_BOARD_COMMAND（默认 "~6" = 250Hz）——
      因为板子只在 250Hz 下用标准封包，默认 1000Hz 时通道会整体错位。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from brainflow.board_shim import (BoardIds, BoardShim, BrainFlowInputParams,
                                  IpProtocolTypes)

# 16 通道优先（Cyton+Daisy），再退到 8 通道（Cyton）
DEFAULT_CANDIDATES = [
    (BoardIds.CYTON_DAISY_WIFI_BOARD.value, "CYTON_DAISY_WIFI(16ch)"),
    (BoardIds.CYTON_WIFI_BOARD.value, "CYTON_WIFI(8ch)"),
]

# BrainFlow 真实协议号（IpProtocolTypes），别写反：
#   0 = NO_IP_PROTOCOL（板卡默认；WiFi 板会走 ip_port / +1 / +2 三个 UDP 口）
#   1 = UDP
#   2 = TCP
PROTO_NAMES = {0: "NO_IP_PROTOCOL(板卡默认)", 1: "UDP", 2: "TCP"}
# 依次尝试的顺序：用户指定的优先，再补上另外两种。
# 本设备实测成功的是 0（板卡默认），所以放第一个，省得白等两次超时。
PROTO_TRY_ORDER = (0, 2, 1)


def proto_name(proto: int) -> str:
    return PROTO_NAMES.get(proto, f"未知({proto})")


def env_first(*names: str, default: str) -> str:
    """按顺序取第一个「已设置且非空」的环境变量；旧的变量名作为兜底。"""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def build_params(bid: int, ip: str, port: int, proto: int) -> BrainFlowInputParams:
    params = BrainFlowInputParams()
    if bid >= 0 and ip:
        params.ip_address = ip
        params.ip_port = port
        params.ip_protocol = proto
    params.timeout = 10
    return params


def probe_one(board_id: int, label: str, ip: str, port: int, proto: int,
              seconds: float, outdir: Path) -> bool:
    print(f"\n>>> 尝试板卡 {label} (id={board_id}) "
          f"ip={ip or '-'}:{port} proto={proto_name(proto)}", flush=True)
    params = build_params(board_id, ip, port, proto)
    board = BoardShim(board_id, params)
    try:
        board.prepare_session()
    except Exception as e:
        print(f"    ✗ 连接失败：{type(e).__name__}: {e}", flush=True)
        return False

    try:
        # WiFi 板先设到 250Hz：默认 1000Hz 时 BrainFlow 解出来的通道整体错位。
        command = os.getenv("EEG_BOARD_COMMAND",
                            "~6" if board_id in (5, 6) else "").strip()
        if command:
            try:
                board.config_board(command)
                print(f"    ✓ 已设置板卡：{command}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"    ⚠️ 设置板卡失败：{type(e).__name__}: {e}", flush=True)
        board.start_stream()
        print(f"    ✓ 已连接，采集 {seconds:.0f} 秒 …", flush=True)
        t0 = time.time()
        time.sleep(seconds)
        elapsed = time.time() - t0
        data = board.get_current_board_data(int(seconds * 1000))  # 上限，返回实际可用样本

        import numpy as np

        n_samples = data.shape[1]
        eeg_ch = BoardShim.get_eeg_channels(board_id)
        nominal_fs = BoardShim.get_sampling_rate(board_id)
        measured_fs = n_samples / elapsed if elapsed > 0 else 0

        print(f"    ✓ 板卡标称采样率: {nominal_fs} Hz")
        print(f"    ✓ 实测采样率    : {measured_fs:.1f} Hz  （{n_samples} 样本 / {elapsed:.1f}s）")
        print(f"    ✓ EEG 通道      : {len(eeg_ch)} 个 -> {eeg_ch}")
        print(f"    ✓ 数据矩阵形状  : {data.shape}  (行=时间戳/EEG/其它, 列=样本)")

        if n_samples:
            eeg = data[eeg_ch, :]
            p2p = np.ptp(eeg, axis=1)
            for i, ch in enumerate(eeg_ch):
                print(f"        通道 {ch:>2}: 峰峰值 {p2p[i]:>12.1f} uV")

        outdir.mkdir(parents=True, exist_ok=True)
        csv = outdir / f"probe_{label.split('(')[0]}_{int(time.time())}.csv"
        from brainflow.data_filter import DataFilter

        DataFilter.write_file(data, str(csv), "w")
        print(f"    ✓ 原始数据已保存: {csv}", flush=True)
        print(f"    >>> 结论：板卡 id={board_id} 可用，实测采样率约 {round(measured_fs)} Hz", flush=True)
        return True
    except Exception as e:
        print(f"    ✗ 采集过程出错：{type(e).__name__}: {e}", flush=True)
        return False
    finally:
        try:
            board.stop_stream()
        except Exception:
            pass
        try:
            board.release_session()
        except Exception:
            pass


def main() -> int:
    BoardShim.disable_board_logger()
    ip = env_first("EEG_IP", default="192.168.4.1")
    # 变量名与 main.py 对齐；旧的 EEG_PORT / EEG_PROTOCOL 仍作为兜底可用
    port = int(env_first("EEG_IP_PORT", "EEG_PORT", default="6677"))
    proto = int(env_first("EEG_IP_PROTOCOL", "EEG_PROTOCOL", default="2"))
    seconds = float(env_first("EEG_SECONDS", default="3"))
    root = Path(__file__).resolve().parent.parent
    outdir = Path(env_first("EEG_OUTDIR", default=str(root / "data")))

    only = env_first("EEG_BOARD", default="")
    is_synthetic = bool(only) and int(only) < 0
    if only:
        bid = int(only)
        label = {6: "CYTON_DAISY_WIFI(16ch)", 5: "CYTON_WIFI(8ch)",
                 -1: "SYNTHETIC(合成板自测)"}.get(bid, f"BOARD_{bid}")
        candidates = [(bid, label)]
    elif not ip:
        print("未提供 EEG_IP，跳过真设备探测。", flush=True)
        return 1
    else:
        candidates = DEFAULT_CANDIDATES

    # 真设备：三种协议依次试，自己会找到能用的那个；合成板不看协议，只跑一遍
    proto_try = [proto]
    if ip and not is_synthetic:
        proto_try += [p for p in PROTO_TRY_ORDER if p != proto]

    print("=" * 60)
    print("ExpandableEEG 连接探测")
    print(f"  目标 IP  : {ip or '(合成板)'}  端口: {port}")
    print("  协议顺序 : " + " -> ".join(proto_name(p) for p in proto_try))
    print("  提示：AP 模式请先连上 Wi-Fi 热点 ExpandableEEG AP（密码 abcd1234）")
    print("=" * 60)

    for bid, label in candidates:
        for idx, p in enumerate(proto_try):
            if probe_one(bid, label, ip, port, p, seconds, outdir):
                print("\n✅ 探测成功。接下来请这样配置：")
                print(f"     EEG_BOARD_ID={bid} EEG_IP_PORT={port} EEG_IP_PROTOCOL={p}")
                return 0
            if idx + 1 < len(proto_try):
                print("    · 换下一种协议重试 …", flush=True)

    print("\n❌ 所有候选都连不上。请检查：")
    print("   1) 采集板已上电，且是 WiFi AP 模式（出厂默认）")
    print("   2) Mac 已连上热点 ExpandableEEG AP")
    print("   3) 若板子改过 client 模式，请把 EEG_IP 换成设备输出的 IP")
    return 2


if __name__ == "__main__":
    sys.exit(main())
