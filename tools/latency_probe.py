#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroBloom 端到端延迟实测

它干的事很简单：连到桥接的 ws/world，收一会儿状态消息，然后回答一个问题——
「从脑电窗口最后一个样点到手，到我这边收到这条消息，中间花了多久？」

延迟其实分三段，脚本会分开报，别混成一个数：
  1. 窗口时长   win_seconds  —— 判定要攒满 4 秒数据，这是物理下限，改不掉
  2. 分析耗时   t_analysis_ms —— Welch + 判定 + 策略层算完花的时间
  3. 传输 + 排队 t_transport_ms —— 从算完到消息落到客户端
  端到端 ≈ 1 + 2 + 3

前置：eeg_service 起来时打过时间戳（t_win_end / t_analysis_ms / win_seconds）。
老版本服务没有这几个字段，脚本会直接说明，不会瞎猜。

用法：
    python tools/latency_probe.py
    python tools/latency_probe.py --url ws://127.0.0.1:8799/ws/world --seconds 30
    python tools/latency_probe.py --json          # 给机器读
退出码：0 = 有数据且字段齐全，2 = 连不上，3 = 服务端没打时间戳。
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

try:
    import websockets
except ImportError:  # pragma: no cover - 只在没装依赖时触发
    print("缺少依赖 websockets：请用项目 venv 跑，或者 pip install websockets", file=sys.stderr)
    raise SystemExit(2)

DEFAULT_URL = os.environ.get("WORLD_WS_URL", "ws://127.0.0.1:8765/ws/world")


def _pct(values, q):
    """分位数（线性插值）。样本少的时候也稳，不依赖 numpy。"""
    if not values:
        return float("nan")
    if len(values) == 1:
        return float(values[0])
    pos = (len(values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    frac = pos - lo
    return values[lo] * (1 - frac) + values[hi] * frac


def _fmt(v, unit="ms"):
    return "—" if v != v else f"{v:.1f} {unit}"


async def probe(url: str, seconds: float) -> dict:
    msgs = []
    deadline = time.time() + seconds
    async with websockets.connect(url) as ws:
        while time.time() < deadline:
            timeout = max(0.2, deadline - time.time())
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                break
            now = time.time()
            try:
                m = json.loads(raw)
            except Exception:
                continue
            if m.get("type") not in (None, "state"):
                continue
            msgs.append((now, m))

    return {"url": url, "raw_count": len(msgs), "samples": msgs}


def summarise(result: dict, skip: int = 1) -> dict:
    # 连上的一瞬间桥接会先把「上一条缓存状态」推过来，那条天生是旧的
    # （可能差好几秒），算进去会把最慢值拉爆，所以默认丢掉前 skip 条。
    msgs = result["samples"][skip:]
    stamps = [(now, m) for now, m in msgs if isinstance(m.get("t_win_end"), (int, float))]
    out = {
        "url": result["url"],
        "messages": result["raw_count"],
        "skipped": skip,
        "timestamped": len(stamps),
        "ok": False,
        "reason": "",
    }
    if result["raw_count"] == 0:
        out["reason"] = "一条状态消息都没收到"
        return out
    if not stamps:
        out["reason"] = "服务端没打时间戳（老版本 eeg_service？）"
        return out

    transport, analysis, windows, gaps = [], [], [], []
    prev = None
    for now, m in stamps:
        transport.append((now - float(m["t_win_end"])) * 1000.0)
        if isinstance(m.get("t_analysis_ms"), (int, float)):
            analysis.append(float(m["t_analysis_ms"]))
        if isinstance(m.get("win_seconds"), (int, float)):
            windows.append(float(m["win_seconds"]))
        if prev is not None:
            gaps.append(now - prev)
        prev = now

    transport.sort()
    analysis.sort()
    win = statistics.median(windows) if windows else 4.0
    a_med = statistics.median(analysis) if analysis else 0.0
    t_med = statistics.median(transport)
    out.update({
        "ok": True,
        "window_s": round(win, 3),
        "analysis_median_ms": round(a_med, 1),
        "transport_median_ms": round(t_med, 1),
        "transport_p95_ms": round(_pct(transport, 0.95), 1),
        "transport_max_ms": round(max(transport), 1),
        "e2e_median_ms": round(win * 1000.0 + a_med + t_med, 1),
        "message_interval_s": round(statistics.median(gaps), 2) if gaps else None,
    })
    return out


def report(out: dict) -> str:
    if not out["ok"]:
        return f"没测出来：{out['reason']}\n地址：{out['url']}（收到 {out['messages']} 条消息）"
    lines = [
        f"地址：{out['url']}",
        f"样本：{out['timestamped']} 条带时间戳的消息（共收到 {out['messages']} 条，"
        f"已跳过接入瞬间的 {out['skipped']} 条缓存快照）",
        f"消息间隔：{out['message_interval_s']} 秒一条",
        "",
        "三段延迟：",
        f"  1) 攒窗口     {_fmt(out['window_s'] * 1000.0)}  ← 要满 4 秒数据才能判，固定成本",
        f"  2) 分析计算   {_fmt(out['analysis_median_ms'])}    ← Welch + 判定 + 策略层",
        f"  3) 传输排队   {_fmt(out['transport_median_ms'])}    ← 中位；95 分位 {_fmt(out['transport_p95_ms'])}，最慢 {_fmt(out['transport_max_ms'])}",
        "",
        f"端到端（1+2+3）中位：{_fmt(out['e2e_median_ms'])}",
        "",
        "怎么看这个数：第 1 段占了大头，这是「每 4 秒给一次判定」的设计，不是卡顿。",
        "真正要盯的是第 2、3 段——它们大了，才是代码或网络的问题。",
    ]
    return "\n".join(lines)


async def main_async(args) -> int:
    print(f"正在连 {args.url}，采集 {args.seconds:.0f} 秒…", flush=True)
    raw = await probe(args.url, args.seconds)
    out = summarise(raw, max(0, args.skip))
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print()
        print(report(out))
    if out["messages"] == 0:
        return 2
    return 0 if out["ok"] else 3


def main() -> int:
    ap = argparse.ArgumentParser(description="NeuroBloom 端到端延迟实测")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"world 的 ws 地址，默认 {DEFAULT_URL}")
    ap.add_argument("--seconds", type=float, default=30.0, help="采集多少秒，默认 30")
    ap.add_argument("--skip", type=int, default=1,
                    help="丢掉最前面几条（接入时的缓存快照），默认 1")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args))
    except (OSError, ConnectionRefusedError) as exc:
        print(f"连不上 {args.url}：{exc}\n先确认 bridge/server.py 在跑。", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
