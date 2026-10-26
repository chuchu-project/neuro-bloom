#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OpenBCI WiFi 板子连接自检

作用：连上 `OpenBCI-XXXX` 这个 Wi-Fi 之后，先跑这个脚本，
      它会从「网络通不通」到「能不能读到脑电数据」一路查下来，
      并且**自动试**哪种端口/协议组合能用（不靠猜）。

用法：
    python analysis/check_board.py              # 自动检查
    python analysis/check_board.py --ip 192.168.4.1
    python analysis/check_board.py --port 1112 --protocol tcp

检查顺序（每一关都告诉你过没过）：
    第1关 本机有没有拿到 192.168.4.x 的 IP  ← 判断 Wi-Fi 连没连上
    第2关 ping 得通吗
    第3关 板子上哪些端口是开的
    第4关 BrainFlow 能不能真的读到数据（会试 8 通道和 16 通道两种板子）

⚠️ 跑之前先把 OpenBCI GUI 退出 —— 它可能已经占着板子的连接，
   两个程序同时抢，后连的会失败。
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time

# 常见候选端口：1112 是 OpenBCI WiFi 扩展板的数据端口，
# 其余几个只是顺手扫一下，便于判断板子到底是"没开"还是"开了但不对"
CANDIDATE_PORTS = [1112, 6677, 23, 80, 3000, 5000]
# 6677 是 OpenBCI GUI 实际使用的端口（从 GUI 的 Brainflow 日志里读到的）。

OK = "  ✅"
NO = "  ❌"


# ==================================================================
# 第 1 关：本机网络
# ==================================================================
def local_addresses() -> list[str]:
    """列出本机所有 IPv4 地址"""
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return []
    addrs = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("inet "):
            addrs.append(line.split()[1])
    return addrs


def check_network(ip: str) -> bool:
    print("【第 1 关】本机 IP 地址")
    addrs = local_addresses()
    for a in addrs:
        print(f"      - {a}")
    subnet = ".".join(ip.split(".")[:3]) + "."
    hit = [a for a in addrs if a.startswith(subnet)]
    if hit:
        print(f"{OK} 已接入板子所在网段（{subnet}x）：{', '.join(hit)}")
        return True
    print(f"{NO} 本机没有 {subnet}x 的地址 —— 说明还没连上 OpenBCI 那个 Wi-Fi")
    print("      → 去 Mac 右上角 Wi-Fi 菜单，连上 OpenBCI-XXXX 再重跑")
    return False


# ==================================================================
# 第 2 关：ping
# ==================================================================
def check_ping(ip: str) -> bool:
    print("\n【第 2 关】ping 板子")
    r = subprocess.run(["ping", "-c", "2", "-t", "2", ip],
                       capture_output=True, text=True)
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            if "round-trip" in line or "bytes from" in line:
                print(f"      {line.strip()}")
        print(f"{OK} 通！")
        return True
    print(f"{NO} ping 不通")
    print("      → 可能原因：板子没通电 / Wi-Fi 连的是别的网络 / 防火墙拦 ICMP")
    print("      → 别急，继续看第 3 关（有些板子就是屏蔽 ping）")
    return False


# ==================================================================
# 第 3 关：端口扫描
# ==================================================================
def tcp_open(ip: str, port: int, timeout: float = 1.5) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def check_ports(ip: str) -> list[int]:
    print("\n【第 3 关】扫板子开了哪些端口")
    opened = []
    for p in CANDIDATE_PORTS:
        good = tcp_open(ip, p)
        print(f"      {'✅' if good else '  '} 端口 {p}")
        if good:
            opened.append(p)
    if opened:
        print(f"{OK} 有端口响应：{opened}")
    else:
        print(f"{NO} 没有任何端口响应")
        print("      → 板子上的 WiFi 扩展板可能没在推数据，")
        print("        或者两个开关/跳线没设对（WiFi 扩展板需要正确供电和拨码）")
    return opened


# ==================================================================
# 第 4 关：用 BrainFlow 真读一次数据
# ==================================================================
def try_brainflow(ip: str, port: int, protocol: int,
                  board_id: int, board_name: str, seconds: float = 3.0) -> bool:
    from brainflow.board_shim import BoardShim, BrainFlowInputParams

    params = BrainFlowInputParams()
    params.ip_address = ip
    params.ip_port = port
    params.ip_protocol = protocol
    params.timeout = 15           # 别让它无限等

    board = BoardShim(board_id, params)
    try:
        board.prepare_session()
    except Exception as e:
        print(f"      {board_name} 连接失败：{str(e)[:120]}")
        return False

    try:
        board.start_stream()
        time.sleep(seconds)
        data = board.get_board_data()
        sfreq = BoardShim.get_sampling_rate(board_id)
        eeg_ch = BoardShim.get_eeg_channels(board_id)
        print(f"{OK} {board_name} 连上了！")
        print(f"      采样率 {sfreq} Hz | EEG 通道 {len(eeg_ch)} 个 | "
              f"拿到 {data.shape[1]} 个采样点（约 {data.shape[1]/sfreq:.1f} 秒）")
        if data.shape[1] > 0:
            import numpy as np
            vals = data[eeg_ch, -200:]
            print(f"      最近 200 点的通道均值(µV)："
                  f"{np.round(vals.mean(axis=1), 1).tolist()}")
            print(f"      数据里有非零值 → 板子在真的输出信号 ✅")
        else:
            print("      ⚠️ 没拿到采样点：连上了但没数据流，"
                  "检查 WiFi 扩展板是否在推数据")
        return True
    finally:
        try:
            board.stop_stream()
        except Exception:
            pass
        try:
            board.release_session()
        except Exception:
            pass


def check_brainflow(ip: str, ports: list[int], protocol_choice: str):
    """返回 (板子id, 端口, 协议) 或 None。"""
    from brainflow.board_shim import BoardIds, IpProtocolTypes

    print("\n【第 4 关】用 BrainFlow 试着真读一段数据")

    # 端口：优先用扫出来的，否则试 1112
    try_ports = [p for p in ports if p in CANDIDATE_PORTS] or [1112]

    protocols = {
        "tcp": [IpProtocolTypes.TCP.value],
        "udp": [IpProtocolTypes.UDP.value],
        "auto": [IpProtocolTypes.TCP.value, IpProtocolTypes.UDP.value],
    }[protocol_choice]

    # 16 通道先试：OpenBCI GUI 里显示的是 16 CHANNELS，多半插了 Daisy
    boards = [
        (BoardIds.CYTON_DAISY_WIFI_BOARD.value, "Cyton+Daisy WiFi（16 通道）"),
        (BoardIds.CYTON_WIFI_BOARD.value,       "Cyton WiFi（8 通道）"),
    ]

    for port in try_ports:
        for proto in protocols:
            for bid, name in boards:
                pname = {1: "UDP", 2: "TCP"}.get(proto, str(proto))
                print(f"\n      → 试：端口 {port} / {pname} / {name}")
                if try_brainflow(ip, port, proto, bid, name):
                    proto_name = "tcp" if proto == IpProtocolTypes.TCP.value else "udp"
                    board_key = {
                        BoardIds.CYTON_DAISY_WIFI_BOARD.value: "daisy_wifi",
                        BoardIds.CYTON_WIFI_BOARD.value: "cyton_wifi",
                    }.get(bid, "daisy_wifi")

                    print("\n" + "=" * 62)
                    print("  🎉 通了！参数确认：")
                    print(f"     IP={ip}  端口={port}  协议={proto_name}  板子={name}")
                    print("=" * 62)
                    print("\n  下一步：复制下面这一整行去跑，实时状态窗口就开起来了")
                    print()
                    print("     ./.venv/bin/python analysis/live_state_gui.py "
                          f"--source board --board {board_key} "
                          f"--ip {ip} --port {port} --protocol {proto_name}")
                    print()
                    return (bid, port, proto)

    print("\n" + "=" * 62)
    print("  ❌ 都试过了，没连上")
    print("=" * 62)
    print("  最可能的原因（按概率排序）：")
    print("   1. OpenBCI GUI 还开着，把板子占住了 → 完全退出它再试")
    print("   2. WiFi 扩展板没在推数据 → 打开浏览器访问 http://%s" % ip)
    print("      看一眼扩展板的配置页，确认它开着数据流")
    print("   3. 板子电池没电 / 主开关没拨到正确位置")
    return False


# ==================================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="OpenBCI WiFi 板子连接自检")
    ap.add_argument("--ip", default="192.168.4.1", help="板子 IP（默认 192.168.4.1）")
    ap.add_argument("--port", type=int, default=None, help="直接指定端口，跳过扫描")
    ap.add_argument("--protocol", choices=["tcp", "udp", "auto"], default="auto",
                    help="连接协议（默认 auto：TCP 和 UDP 都试）")
    args = ap.parse_args()

    print("=" * 62)
    print("  OpenBCI WiFi 板子连接自检")
    print(f"  目标：{args.ip}")
    print("=" * 62)
    print()

    # 顺手提醒 GUI 抢占问题
    r = subprocess.run(["pgrep", "-f", "OpenBCI_GUI"], capture_output=True, text=True)
    if r.stdout.strip():
        print(f"  ⚠️ 检测到 OpenBCI GUI 正在运行（PID {r.stdout.split()[0]}）。")
        print("     它可能占着板子，建议先退出它。\n")

    if not check_network(args.ip):
        sys.exit(1)

    ping_ok = check_ping(args.ip)

    if args.port:
        ports = [args.port]
        print(f"\n【第 3 关】跳过扫描（你指定了端口 {args.port}）")
    else:
        ports = check_ports(args.ip)

    if not ping_ok and not ports:
        print("\n  前 3 关都没过，第 4 关就不用试了。")
        print("  先去把 Wi-Fi 连上、确认板子通电，再重跑本脚本。")
        sys.exit(1)

    found = check_brainflow(args.ip, ports, args.protocol)
    sys.exit(0 if found else 1)


if __name__ == "__main__":
    main()
