#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
离线一键启动：自检 → 开实时窗口 → 自动存档

★ 这个脚本全程不需要网络。
   连上 OpenBCI-XXXX 之后，Mac 上不了网 —— 那没关系。
   脑电数据、判断、窗口变色，全在本机完成，跟有没有网一点关系都没有。
   唯一会"断"的是你跟助手说话，所以本脚本把过程全部写成文件，
   等你切回正常 Wi-Fi 之后，把文件拿回来就行。

用法（在 NeuroBloom 目录下）：
    ./.venv/bin/python analysis/offline_start.py

它会自己做完这几件事：
    1. 检查本机有没有连上板子的网段
    2. ping 板子
    3. 扫端口
    4. 用 BrainFlow 真读一段数据（自动试端口/协议/8通道/16通道）
    5. 通了就直接开实时窗口，并且把每个窗口的结果存成 CSV

常用参数：
    --baseline 60      标定多少秒（0 = 不标定；和现场链路默认一致）
    --no-record        不存档
    --ip 192.168.4.1   板子地址
    --protocol auto|tcp|udp
    --bdf              方案B：不直连，改跟 OpenBCI GUI 录的 BDF（自动找最新）

关于「方案B」：
    有些时候 BrainFlow 直连读不到数据，但 OpenBCI 官方 GUI 却能连上。
    那就让 GUI 负责读板子，我们只跟着它录下来的文件走 ——
    GUI 录的是 BDF，本脚本能边录边跟。（--source bdf）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import check_board as cb        # noqa: E402  复用同一套自检，不重写一遍

REC_DIR = os.path.join(ROOT, ".data", "recordings")
LOG_DIR = os.path.join(ROOT, ".data", "logs")

# OpenBCI 官方 GUI 默认把「录制」存到这里（BDF 格式）
GUI_REC_DIR = os.path.expanduser("~/Documents/OpenBCI_GUI/Recordings")


def find_gui_bdf() -> str | None:
    """找 OpenBCI GUI 最近录的那个 BDF，返回路径（没有就返回 None）。"""
    if not os.path.isdir(GUI_REC_DIR):
        return None
    cands = [os.path.join(GUI_REC_DIR, n) for n in os.listdir(GUI_REC_DIR)
             if n.startswith("OpenBCI-BDF-") and n.endswith(".bdf")]
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


class Tee:
    """一份输出，同时写到屏幕和日志文件。"""

    def __init__(self, real, file):
        self.real = real
        self.file = file

    def write(self, s):
        self.real.write(s)
        self.file.write(s)
        return len(s)

    def flush(self):
        self.real.flush()
        self.file.flush()

    def isatty(self):
        return False


def stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def main() -> int:
    ap = argparse.ArgumentParser(description="离线一键启动（自检 + 实时窗口 + 存档）")
    ap.add_argument("--ip", default="192.168.4.1")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--protocol", choices=["auto", "tcp", "udp"], default="auto")
    ap.add_argument("--baseline", type=float, default=60.0,
                    help="标定多少秒（默认 60，和 eeg_service/main.py 一致）")
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--bdf", nargs="?", const="auto", default=None,
                    help="方案B：不直连板子，改跟 OpenBCI GUI 录的 BDF。"
                         "不给值=自动找最新的，也可以直接给路径")
    args = ap.parse_args()

    os.makedirs(REC_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = stamp()
    log_path = os.path.join(LOG_DIR, f"realtime_{ts}.log")
    rec_path = os.path.join(REC_DIR, f"realtime_{ts}.csv")

    log = open(log_path, "w", encoding="utf-8")
    # 把 stdout 同时接到屏幕和日志文件 ——
    # 这样连 check_board 打印的那些内容也一并进日志。
    # 断网之后你手上只有这个文件，所以它必须是完整的。
    sys.stdout = Tee(sys.__stdout__, log)
    sys.stderr = Tee(sys.__stderr__, log)

    def say(text: str = "") -> None:
        print(text, flush=True)

    def finish(code: int) -> int:
        """收尾：恢复标准输出、关掉日志。"""
        sys.stdout.flush()
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
        log.close()
        return code

    say("=" * 62)
    say("  离线一键启动　（全程无需联网）")
    say("=" * 62)
    say()
    say(f"  这次的过程会存到：{log_path}")
    say(f"  这次的数据会存到：{rec_path if not args.no_record else '（本次不存档）'}")
    say()

    # ---------- 开实时窗口（两种数据源共用的收尾）----------
    def launch(src_args: list[str], notes: list[str]) -> int:
        cmd = [sys.executable, "-u", os.path.join(HERE, "live_state_gui.py")]
        cmd += src_args + ["--baseline", str(args.baseline)]
        if not args.no_record:
            cmd += ["--record", rec_path]

        say()
        say("=" * 62)
        say("  现在打开实时状态窗口")
        say("=" * 62)
        for n in notes:
            say(n)
        say()

        # 子进程输出同时打到屏幕和日志
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)      # Tee 会自动同时写进日志
            sys.stdout.flush()
        proc.wait()

        say()
        say("=" * 62)
        say("  结束")
        say("=" * 62)
        if not args.no_record:
            say(f"  数据已存到：{rec_path}")
            say("  （每一行是一个判定窗口。等你要算准确率时，")
            say("    在旁边补一列你自己的主观评分 1~10，就能对答案了。）")
        say(f"  过程日志：{log_path}")
        say()
        say("  切回正常 Wi-Fi 之后，把上面这两个文件拿给助手看就行。")
        return finish(0)

    NOTES_HOWTO = [
        "  接下来请照做：",
        f"    1. 先安静坐好 {args.baseline:.0f} 秒（窗口是浅灰的，写着「正在标定…」）",
        "    2. 然后试着闭眼放松 —— 应该会变浅绿",
        "    3. 再试着做心算（比如从 1000 连减 7）—— 应该会变浅红",
        "       （浅蓝＝中间：不上不下、或者变化还没超出噪声）",
        "    4. 想结束时，在窗口里按 Esc",
    ]
    NOTES_BOARD = NOTES_HOWTO + [
        "",
        "  窗口关掉后，本脚本会把结果存档并告诉你路径。",
    ]
    NOTES_BDF = NOTES_HOWTO + [
        "       （按 Esc 只关我们这个小窗口，OpenBCI GUI 会继续录）",
        "",
        "  这个窗口会比 GUI 慢 1~2 秒，属正常。",
        "  窗口关掉后，本脚本会把结果存档并告诉你路径。",
    ]

    # ---------- 方案B：跟着 OpenBCI GUI 录下来的 BDF ----------
    def plan_b(reason: str, path_hint: str | None = None) -> int:
        """板子直连不通、但 GUI 能连时走这条路：跟 GUI 录制文件。"""
        say()
        say("=" * 62)
        say("  方案B：跟着 OpenBCI GUI 录下来的文件走")
        say("=" * 62)
        say(f"  （走这条路的原因：{reason}）")

        if path_hint and path_hint != "auto":
            p = os.path.expanduser(path_hint)
            if not os.path.exists(p):
                say(f"  你指定的文件不存在：{p}")
                return finish(1)
        else:
            p = find_gui_bdf()
            if not p:
                say()
                say("  没找到 OpenBCI GUI 的录制文件。")
                say(f"  找的位置：{GUI_REC_DIR}")
                say("  → 打开 OpenBCI GUI，连上板子，点「开始录制」，再重跑本脚本。")
                return finish(1)

        size = os.path.getsize(p)
        age = time.time() - os.path.getmtime(p)
        say(f"  文件：{p}")
        say(f"  大小 {size:,} 字节　　最后一次写入：{age:.0f} 秒前")
        if age > 30 or size < 100_000:
            say()
            say("  ⚠️ 这个文件看起来没在录（很久没变化 / 太小）。")
            say("     → 回到 OpenBCI GUI 点一下「开始录制」，再重跑本脚本。")
            say("     → 或者手动指定文件：")
            say("        ./.venv/bin/python analysis/live_state_gui.py \\")
            say("            --source bdf --file <那个文件>")
            return finish(1)

        say("  ✓ 它正在录。板子继续交给 GUI 读，我们只从文件里跟。")
        return launch(["--source", "bdf", "--file", p], NOTES_BDF)

    # 用户明确要用方案B → 直接走，不用过前 4 关
    if args.bdf is not None:
        return plan_b("你指定了 --bdf", args.bdf)

    # ---------- 提醒 GUI 抢占 ----------
    r = subprocess.run(["pgrep", "-f", "OpenBCI_GUI"], capture_output=True, text=True)
    if r.stdout.strip():
        say(f"  ⚠️ OpenBCI GUI 还开着（PID {r.stdout.split()[0]}）。")
        say("     直连板子这条路，它可能会抢设备。建议先完全退出它（⌘Q）再重跑。")
        say("     （如果你打算走「方案B」跟 GUI 的文件，那就让它开着。）")
        say()

    # ---------- 第 1 关：网段 ----------
    if not cb.check_network(args.ip):
        say()
        say("  → 还没连上板子的 Wi-Fi。")
        return plan_b("本机不在板子的网段里", args.bdf)

    say()

    # ---------- 第 2、3 关 ----------
    ping_ok = cb.check_ping(args.ip)
    if args.port:
        ports = [args.port]
        say(f"\n【第 3 关】跳过扫描（指定端口 {args.port}）")
    else:
        ports = cb.check_ports(args.ip)

    if not ping_ok and not ports:
        say()
        say("  ping 和端口扫描都没通。")
        return plan_b("ping / 端口都没通", args.bdf)

    # ---------- 第 4 关：真读数据 ----------
    found = cb.check_brainflow(args.ip, ports, args.protocol)
    if not found:
        say()
        say("  BrainFlow 直连读不到数据。")
        say("  这一趟没白来：日志已经存好了，切回正常网之后拿给我看。")
        return plan_b("BrainFlow 直连没成功", args.bdf)

    bid, port, proto = found
    board_key = {6: "daisy_wifi", 5: "cyton_wifi"}.get(bid, "daisy_wifi")
    proto_name = "tcp" if proto == 2 else "udp"
    return launch(["--source", "board", "--board", board_key,
                   "--ip", args.ip, "--port", str(port), "--protocol", proto_name],
                  NOTES_BOARD)


if __name__ == "__main__":
    sys.exit(main())
