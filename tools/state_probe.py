#!/usr/bin/env python
"""判据区分度实测：四个已知状态，看你的 α/β 指数能不能把它们分开。

这是方案赛里最该先做的一件事。不需要患者，也不需要证明「放松有效」——
只需要证明：换一个已知会改变脑电的条件，指数跟着变，而且变得比噪声大。

四个条件（都有文献支撑，属于「阳性对照」）
    睁眼静息  eyes_open    α 被视觉输入压制，作为参照
    闭眼静息  eyes_closed  α 明显上升（正常人都能测到，测不到说明链路有问题）
    心算      math         β 上升、α 下降，用来验证另一个方向
    慢呼吸    breathe      4–6 次/分，副交感激活，探索性条件

用法
    # 先把桥接和采集跑起来（没有板子就开演示）
    .venv/bin/python tools/state_probe.py --subject s01
    # 只做一轮、每段 60 秒、跳过提问
    .venv/bin/python tools/state_probe.py --subject s01 --seconds 60 --cycles 1 --auto

输出
    sessions/probe-<被试>-<时间>.jsonl   原始记录（带条件标签）
    终端：各条件中位数、闭眼 vs 睁眼 AUC、Cohen's d、置换检验 p

判读
    AUC ≥ 0.80 且 p < 0.05：判据对状态敏感，可以用。
    AUC 0.65–0.80：方向对但噪声大，先查电极接触与坏道。
    AUC < 0.65 或方向反了：链路有问题（带通、参考、通道映射），别急着讲故事。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SESSIONS = ROOT / "sessions"

CONDITIONS = {
    "eyes_open": ("睁眼静息", "看着屏幕中央，不要刻意放松，正常眨眼"),
    "eyes_closed": ("闭眼静息", "闭上眼，什么都不想，保持清醒"),
    "math": ("心算", "从 1000 开始连续减 7，心里默算，尽量不要停"),
    "breathe": ("慢呼吸", "吸气 4 秒、呼气 6 秒，跟着节拍，不要憋气"),
}


# ── 采集 ────────────────────────────────────────────────────
class Collector:
    def __init__(self, url: str):
        self.url = url
        self.records: list[dict] = []
        self.condition: str | None = None
        self.connected = False
        self.error: str | None = None

    async def run(self, stop: asyncio.Event):
        import websockets

        try:
            async with websockets.connect(self.url, open_timeout=8) as ws:
                self.connected = True
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.5)
                    except asyncio.TimeoutError:
                        continue
                    if self.condition is None:
                        continue
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("type") not in (None, "state"):
                        continue
                    self.records.append({
                        "t": msg.get("t", time.time()),
                        "cond": self.condition,
                        "relax": msg.get("relax"),
                        "focus": msg.get("focus"),
                        "bt": msg.get("bt"),
                        "li": msg.get("li"),
                        "bands": msg.get("bands") or {},
                        "board": msg.get("board_id"),
                    })
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"


async def countdown(label: str, hint: str, seconds: int, auto: bool) -> None:
    print(f"\n▶ {label}（{seconds} 秒）")
    print(f"  {hint}")
    if not auto:
        await asyncio.to_thread(input, "  准备好按回车开始…")
    step = 10 if seconds >= 30 else 5
    for left in range(seconds, 0, -step):
        print(f"  …还剩 {left} 秒", end="\r", flush=True)
        await asyncio.sleep(min(step, left))
    print(" " * 24, end="\r")


def latin_order(cycles: int) -> list[list[str]]:
    """每轮换一个起点，抵消顺序效应。"""
    base = list(CONDITIONS)
    out = []
    for c in range(cycles):
        shift = c % len(base)
        out.append(base[shift:] + base[:shift])
    return out


# ── 统计 ────────────────────────────────────────────────────
def auc(pos: list[float], neg: list[float]) -> float:
    """正类得分高于负类的概率（Mann-Whitney 形式）。"""
    if not pos or not neg:
        return float("nan")
    allv = np.array(pos + neg, dtype=float)
    order = allv.argsort().argsort() + 1  # 1-based ranks
    ranks = order.astype(float)
    n1, n2 = len(pos), len(neg)
    r1 = ranks[:n1].sum()
    return (r1 - n1 * (n1 + 1) / 2) / (n1 * n2)


def cohens_d(a: list[float], b: list[float]) -> float:
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    a, b = np.array(a), np.array(b)
    pooled = math.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1))
                       / (len(a) + len(b) - 2))
    return float((a.mean() - b.mean()) / pooled) if pooled else float("nan")


def perm_p(a: list[float], b: list[float], n: int = 5000, seed: int = 7) -> float:
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    obs = abs(np.mean(a) - np.mean(b))
    pool = list(a) + list(b)
    rng = random.Random(seed)
    hits = 0
    for _ in range(n):
        rng.shuffle(pool)
        if abs(np.mean(pool[:len(a)]) - np.mean(pool[len(a):])) >= obs:
            hits += 1
    return (hits + 1) / (n + 1)


def summarize(records: list[dict]) -> None:
    by: dict[str, dict[str, list[float]]] = {}
    for r in records:
        if not isinstance(r.get("relax"), (int, float)):
            continue
        d = by.setdefault(r["cond"], {"relax": [], "alpha": [], "beta": [], "bt": []})
        d["relax"].append(float(r["relax"]))
        bands = r.get("bands") or {}
        if isinstance(bands.get("alpha"), (int, float)):
            d["alpha"].append(float(bands["alpha"]))
        if isinstance(bands.get("beta"), (int, float)):
            d["beta"].append(float(bands["beta"]))
        if isinstance(r.get("bt"), (int, float)):
            d["bt"].append(float(r["bt"]))

    print("\n四、结果")
    print(f"{'条件':<10}{'样本':<7}{'指数中位':<10}{'α 中位':<10}{'β 中位':<10}")
    print("-" * 50)
    for key, label in [(k, v[0]) for k, v in CONDITIONS.items()]:
        d = by.get(key)
        if not d or not d["relax"]:
            print(f"{label:<10}{'-':<7}{'—':<10}{'—':<10}{'—':<10}")
            continue
        print(f"{label:<10}{len(d['relax']):<7}{np.median(d['relax']):<10.3f}"
              f"{np.median(d['alpha']) if d['alpha'] else float('nan'):<10.3f}"
              f"{np.median(d['beta']) if d['beta'] else float('nan'):<10.3f}")

    closed, opened = by.get("eyes_closed", {}).get("relax", []), by.get("eyes_open", {}).get("relax", [])
    print("\n五、阳性对照：闭眼 vs 睁眼（α 应该更高，正常人都能测到）")
    if closed and opened:
        a_closed = by["eyes_closed"].get("alpha", [])
        a_open = by["eyes_open"].get("alpha", [])
        ratio = (np.median(a_closed) / np.median(a_open)) if a_open and np.median(a_open) > 0 else float("nan")
        print(f"  α 中位倍数：{ratio:.2f}×   （1.5–3× 之间属正常范围）")
        print(f"  指数 AUC：{auc(closed, opened):.3f}   Cohen's d：{cohens_d(closed, opened):+.2f}   "
              f"置换 p：{perm_p(closed, opened):.4f}")

        verdict = "判据对状态敏感，可以用"
        if not (auc(closed, opened) >= 0.8):
            verdict = "方向对但噪声大，先查电极接触与坏道"
        if auc(closed, opened) < 0.65:
            verdict = "链路可疑（带通/参考/通道映射），先别讲故事"
        print(f"  判断：{verdict}")
    else:
        print("  缺少闭眼或睁眼条件，无法做阳性对照。")

    math_r, open_r = by.get("math", {}).get("relax", []), opened
    if math_r and open_r:
        a = auc(math_r, open_r)
        print("\n六、反方向验证：心算 vs 睁眼（心算时指数应更低）")
        print(f"  指数 AUC：{a:.3f}   Cohen's d：{cohens_d(math_r, open_r):+.2f}   "
              f"置换 p：{perm_p(math_r, open_r):.4f}")
        print(f"  方向：{'符合预期（心算低于睁眼）' if a < 0.5 else '不符预期，检查 β/θ 与通道映射'}")

    print("\n注意：这是健康成人的状态区分度，不是疗效，也不能外推到卒中患者。")


# ── 主流程 ──────────────────────────────────────────────────
async def main_async(args) -> int:
    url = args.url
    collector = Collector(url)
    stop = asyncio.Event()
    task = asyncio.create_task(collector.run(stop))
    await asyncio.sleep(1.2)
    if collector.error:
        print(f"连不上 {url}\n  {collector.error}")
        print("先把桥接起来：.venv/bin/python bridge/server.py；没有板子就在控制台点「演示」。")
        stop.set()
        await task
        return 2

    order = latin_order(args.cycles)
    plan = [c for block in order for c in block]
    total = len(plan) * args.seconds
    print(f"被试 {args.subject}｜{len(plan)} 段 × {args.seconds} 秒 ≈ {total // 60} 分 {total % 60} 秒")
    print("全程保持不动、不说话；每段之间可以眨眼、活动一下。")

    for i, cond in enumerate(plan, 1):
        label, hint = CONDITIONS[cond]
        print(f"\n[{i}/{len(plan)}]")
        collector.condition = None
        await countdown(label, hint, args.seconds, args.auto)
        collector.condition = cond
        await asyncio.sleep(args.seconds)
    collector.condition = None
    await asyncio.sleep(1.0)
    stop.set()
    await task

    print(f"\n三、已记录 {len(collector.records)} 条状态")
    if not collector.records:
        print("没收到状态消息——采集服务没在发数据，先确认 /device 里 mode 不是 idle。")
        return 3

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out_dir / f"probe-{args.subject}-{stamp}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "meta", "subject": args.subject, "seconds": args.seconds,
                             "cycles": args.cycles, "order": plan, "ts": time.time()},
                            ensure_ascii=False) + "\n")
        for r in collector.records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    try:
        print(f"存档：{out.relative_to(ROOT)}")
    except ValueError:
        print(f"存档：{out}")

    summarize(collector.records)
    print(f"\n下一步：把这条记录发给治疗师看一遍，并写进 docs/验证方案.md 的结果表。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="四条件判据区分度实测")
    ap.add_argument("--subject", default="s01", help="被试编号，如 s01；不要用真名")
    ap.add_argument("--url", default="ws://127.0.0.1:8765/ws/world")
    ap.add_argument("--seconds", type=int, default=90, help="每个条件多少秒（默认 90）")
    ap.add_argument("--cycles", type=int, default=1, help="重复几轮（默认 1，建议 2）")
    ap.add_argument("--auto", action="store_true", help="不按回车，自动连续跑")
    ap.add_argument("--out-dir", default=str(SESSIONS), help="存档目录（默认 sessions/）")
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n已中止，没有存档。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
