#!/usr/bin/env python3
"""确保 Unity 正在推画面；没画面就自动让它进 Play。

判断依据不是去猜 Unity 的按钮状态（菜单项是个"切换"，看不出现状），
而是直接问桥接：/ws/video/sub 能不能在两秒内收到一帧。
收到 = Unity 正在播放；收不到 = 没在播放（或 Unity 没开），再去点菜单。

用法：
    python tools/ensure_unity_playing.py          # 没画面就点一次 Play
    python tools/ensure_unity_playing.py --check  # 只检查，不动 Unity
"""
from __future__ import annotations

import subprocess
import sys
import time

VIDEO_URL = "ws://127.0.0.1:8765/ws/video/sub"
MENU = ["NeuroBloom", "切换 Play 状态"]


def frames_alive(timeout: float = 2.5) -> bool:
    """桥接上有没有画面在流。"""
    try:
        import asyncio

        import websockets

        async def probe() -> bool:
            try:
                async with websockets.connect(VIDEO_URL, open_timeout=2) as ws:
                    await asyncio.wait_for(ws.recv(), timeout=timeout)
                    return True
            except Exception:
                return False

        return asyncio.run(probe())
    except Exception:
        return False


def click_play() -> bool:
    """用菜单点 Play（快捷键在 Unity 里不生效，菜单是可靠的路子）。"""
    script = (
        'tell application "System Events" to tell process "Unity" to '
        f'click menu item "{MENU[1]}" of menu 1 of menu bar item "{MENU[0]}" of menu bar 1'
    )
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        print(f"  点菜单失败：{err}")
        if "not found" in err or "-1728" in err:
            print("  → Unity 可能没开着，或者还没加载完。先打开 Unity 工程再试。")
        return False
    return True


def main() -> int:
    check_only = "--check" in sys.argv

    if frames_alive():
        print("  画面：Unity 正在播放，视频已在推流")
        return 0

    print("  画面：还没收到帧")
    if check_only:
        return 1

    print("  → 让 Unity 进 Play")
    if not click_play():
        return 2

    print("  等 Unity 启动播放（最多 25 秒）…")
    for _ in range(25):
        time.sleep(1)
        if frames_alive(1.5):
            print("  画面：已经通了")
            return 0
    print("  画面：25 秒内没等到帧。Unity 可能还在编译脚本，稍等一下再双击一次。")
    return 3


if __name__ == "__main__":
    sys.exit(main())
