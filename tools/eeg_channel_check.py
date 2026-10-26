"""实时通道体检：一边压紧/分开头发调整电极，一边看哪一路变干净。

本品（ExpandableEEG）是**干电极**：电极直接接触头皮，不打导电膏、不用注射器。
所以这里能做的调整只有三件事——压紧电极、把头发拨开、擦干净电极和头皮。

用法（在项目根目录）：
    .venv/bin/python tools/eeg_channel_check.py
    EEG_SECONDS=120 .venv/bin/python tools/eeg_channel_check.py

端口说明：EEG_IP_PORT 是**本机（电脑）的监听端口**（默认 6677），不是板子的端口。
板子的网页在 192.168.4.1:80。协议默认 0。

判据（1-40Hz 带宽内 RMS，已挖掉 50Hz 工频）：
    ✓   <= 60 uV    脑电正常
    ⚠   60-200 uV   偏高：电极没压实 / 头发夹在中间
    ✗   > 200 uV    接触不良：该电极基本没贴到头皮
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
from brainflow.board_shim import (BoardIds, BoardShim, BrainFlowInputParams,
                                  IpProtocolTypes)

BOARD = int(os.getenv("EEG_BOARD", "6"))
IP = os.getenv("EEG_IP", "192.168.4.1")
PORT = int(os.getenv("EEG_IP_PORT", "6677"))          # 本机监听口，不是板子的口
PROTO = int(os.getenv("EEG_IP_PROTOCOL", "0"))        # 0=不用 IP 协议族（本设备用这个）
SECONDS = float(os.getenv("EEG_SECONDS", "120"))
COMMAND = os.getenv("EEG_BOARD_COMMAND",
                    "~6" if BOARD in (5, 6) else "").strip()
WIFI_FS = int(os.getenv("EEG_WIFI_SAMPLE_RATE", "250"))

BAR = "█"


def band_rms(x: np.ndarray, fs: float, lo: float, hi: float,
             notch: bool = True) -> float:
    n = len(x)
    f = np.fft.rfftfreq(n, 1 / fs)
    X = np.fft.rfft(x - x.mean())
    m = (f >= lo) & (f <= hi)
    if notch:
        m &= ~((f >= 48) & (f <= 52))
    X2 = np.zeros_like(X)
    X2[m] = X[m]
    return float(np.sqrt(np.mean(np.fft.irfft(X2, n=n) ** 2)))


def main() -> int:
    BoardShim.disable_board_logger()
    params = BrainFlowInputParams()
    params.ip_address = IP
    params.ip_port = PORT
    params.ip_protocol = PROTO
    params.timeout = 10

    print(f"连接板子 {IP}:80（本机监听口 {PORT}）proto={PROTO} board={BOARD} …",
          flush=True)
    board = BoardShim(BOARD, params)
    board.prepare_session()
    # WiFi 板必须设到 250Hz，否则通道整体错位、RMS 全是十万级。
    if COMMAND:
        try:
            board.config_board(COMMAND)
            print(f"已设置板卡：{COMMAND}（250Hz 标准封包）", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 设置板卡失败（{type(e).__name__}: {e}）", flush=True)
    board.start_stream()
    fs = float(BoardShim.get_sampling_rate(BOARD))
    if BOARD in (5, 6):
        fs = float(WIFI_FS)     # 标称 1000Hz 与实测（约 250）不符，以实测为准
    eeg_ch = BoardShim.get_eeg_channels(BOARD)
    print(f"已连接，采样率 {fs:.0f} Hz。每 4 秒刷新一次，Ctrl-C 结束。", flush=True)
    time.sleep(2)
    board.get_board_data()  # 丢掉开头的不稳定数据

    t_end = time.time() + SECONDS
    while time.time() < t_end:
        time.sleep(4)  # 4 秒窗口：FFT 分辨率 0.25Hz，够把 50Hz 挖干净
        data = board.get_board_data()
        n = data.shape[1]
        if n < fs * 3:
            continue
        eeg = data[eeg_ch, :]
        rows, ok = [], 0
        for i in range(eeg.shape[0]):
            x = eeg[i]
            dc = float(x.mean())
            y = x - np.polyval(np.polyfit(np.arange(n), x, 1), np.arange(n))
            rms = band_rms(y, fs, 4, 40)
            rms50 = band_rms(y, fs, 48, 52, notch=False)
            if rms50 > 30:
                mark = "✗"
            elif rms <= 60:
                mark, ok = "✓", ok + 1
            elif rms <= 200:
                mark = "⚠"
            else:
                mark = "✗"
            bar = BAR * max(1, min(18, int(round(np.log10(max(rms, 1)) * 6))))
            rows.append(f"CH{i + 1:<2} {dc / 1000:>+7.1f}mV EEG{rms:>7.1f}uV "
                        f"50Hz{rms50:>6.1f}uV {bar:<18} {mark}")

        out = ["\033[2J\033[H", f"实时通道体检   {time.strftime('%H:%M:%S')}   "
               f"合格 {ok}/{eeg.shape[0]}   (✓ EEG<=60uV 且 50Hz<=30uV)"]
        out.append("    偏置        4-40Hz      工频")
        for r in rows[:8]:
            out.append(r)
        out.append("")
        for r in rows[8:]:
            out.append(r)
        out.append("")
        out.append("提示：✗ 的通道压紧电极、拨开头发、擦干净电极再试（干电极不打膏）；"
                   "50Hz 偏高说明该电极没贴住（在拾取空气里的电干扰）。")
        print("\n".join(out), flush=True)

    board.stop_stream()
    board.release_session()
    print("结束。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
