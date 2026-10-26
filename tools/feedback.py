#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自评采集（成绩单的原料）：每轮结束时记一下本人当时的感受。

为什么要有这个
-------------
没有它，我们只能拿「放松度涨了」说事 —— 但那可能只是电极漂了、人睡着了，
或者干脆什么都没干情绪自己就平复了（这正是 P8 对照轮存在的意义）。
一个「本人说刚才那轮感觉怎么样」的数字，是唯一能把脑电信号往
「对他有没有用」上靠的东西。它也是以后算准确率时唯一的**标准答案**。

落在哪
------
    .data/feedback.jsonl     一行一条，追加，不覆盖
        ts           epoch 秒（和脑电包的 "t" 同一个时钟，可直接对齐）
        iso          本地时间，给人看
        session      这个小工具这一次启动的标识（方便只看这一场）
        round        第几轮     ┐ 来自策略层写的 .data/last_state.json
        strategy_id  哪套策略   │ 所以它能知道「你正在评的是哪一轮」
        strategy_name          │
        null_arm     是不是 P8 对照轮 ┘
        state        当时的判定（tense/neutral/relaxed）
        relax        当时的放松度
        self_report  自评 1~5（1=更差 2=稍差 3=没变 4=好点 5=好多了）
        completed    这一轮有没有走完（中途退出要如实填 false）

★ 自评必须如实填。填假数据比不填更糟 —— 后面的算法会照着假数据学错。

怎么用（演示时另开一个终端窗口）
    cd ~/DeepWorks/NeuroBloom
    ./.venv/bin/python tools/feedback.py

    然后一轮结束时按一下数字键再回车。回车直接跳过，q 退出。
    退出时会把这一场的记录汇总成一张小表打出来。

不想手动敲（比如用脚本批量评）：
    ./.venv/bin/python tools/feedback.py --report 4
看历史记录：
    ./.venv/bin/python tools/feedback.py --summary

⚠️ 第一版在**电脑键盘**上录入，不是 VR 里的界面。
   头显里让本人自己按，要另外做交互，那是后面的事。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from strategy.engine import DEFAULT_STATUS_PATH  # noqa: E402

DEFAULT_OUT = os.path.join(_ROOT, ".data", "feedback.jsonl")

# 用文档给的那套说法（1=更差 … 5=好多了），不要自己换一套。
SCALE = "1=更差  2=稍差  3=没变  4=好点  5=好多了"


def read_status(path: str) -> dict | None:
    """读策略层写的小文件，知道「现在是第几轮、用的哪套」。
    读不到不算错 —— 策略层没跑的时候照样能记自评，只是少几个字段。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def build_entry(self_report: int, completed: bool, session: str,
                status: dict | None) -> dict:
    now = time.time()
    st = status or {}
    return {
        "ts": now,
        "iso": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
        "session": session,
        "round": st.get("round"),
        "strategy_id": st.get("strategy_id"),
        "strategy_name": st.get("strategy_name"),
        "null_arm": st.get("null_arm"),
        "state": st.get("state"),
        "relax": st.get("relax"),
        "self_report": int(self_report),
        "completed": bool(completed),
    }


def append_entry(path: str, entry: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_entries(path: str, session: str | None = None) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if session and e.get("session") != session:
                    continue
                out.append(e)
    except OSError:
        pass
    return out


def _mean(vals) -> str:
    vals = [v for v in vals if isinstance(v, (int, float))]
    return f"{sum(vals) / len(vals):.2f}" if vals else "—"


def summarize(entries: list[dict]) -> list[str]:
    """成绩单：一行一轮 + 对照轮 vs 干预轮的对比。

    为什么一定要分开看对照轮：P8 那几轮我们什么都没做，
    如果它们的自评和干预轮一样好，那说明「好了」跟干预没关系。
    这张表的作用就是让这件事没法糊过去。
    """
    if not entries:
        return ["  （还没有记录）"]

    lines = ["  轮次   策略   自评   当时状态      放松度   这套是什么"]
    for e in entries:
        sid = e.get("strategy_id") or "----"
        rnd = e.get("round")
        mark = "★" if e.get("null_arm") else " "
        relax = e.get("relax")
        relax_s = f"{relax:.2f}" if isinstance(relax, (int, float)) else "—"
        lines.append(
            f"  {str(rnd) if rnd is not None else '?':>4}   {sid:<4} "
            f"{e.get('self_report', '?'):>4}   "
            f"{str(e.get('state') or '?'):<12} {relax_s:>6}   "
            f"{mark}{e.get('strategy_name') or '?'}")

    nulls = [e for e in entries if e.get("strategy_id") == "P8"]
    others = [e for e in entries
              if e.get("strategy_id") and e.get("strategy_id") != "P8"]
    unknown = [e for e in entries if not e.get("strategy_id")]
    lines += [
        "",
        f"  共 {len(entries)} 条：自评平均 {_mean([e.get('self_report') for e in entries])} / 5",
        f"  对照轮（P8，什么都不做）  {len(nulls):>2} 条，自评平均 "
        f"{_mean([e.get('self_report') for e in nulls])}",
        f"  干预轮（有做东西）        {len(others):>2} 条，自评平均 "
        f"{_mean([e.get('self_report') for e in others])}",
        "",
        "  ★ 对照轮和干预轮要分开比 —— 如果两边一样好，说明「变好了」跟干预无关。",
    ]
    if unknown:
        lines.append(f"  （另有 {len(unknown)} 条没接上轮次——评的时候策略层没在跑，没法归类）")
    if len(nulls) < 3 or len(others) < 3:
        lines.append("  （样本太少，别急着下结论；这类东西要十几个人、每人若干轮才谈得上趋势）")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(
        description="每轮结束记一下本人自评（成绩单的原料）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=DEFAULT_OUT, help="落盘文件（默认 .data/feedback.jsonl）")
    ap.add_argument("--status", default=DEFAULT_STATUS_PATH,
                    help="策略层写的状态文件（默认 .data/last_state.json）")
    ap.add_argument("--report", type=int, default=None, metavar="1-5",
                    help="非交互：直接记一条自评然后退出")
    ap.add_argument("--summary", action="store_true", help="只打印历史记录，不录入")
    ap.add_argument("--session", default=None,
                    help="只看某一场的记录（--summary 时用）")
    args = ap.parse_args()

    if args.summary:
        entries = load_entries(args.out, args.session)
        print(f"自评记录 {args.out}")
        for line in summarize(entries):
            print(line)
        return

    session = datetime.now().strftime("%Y%m%d-%H%M%S")

    if args.report is not None:
        if not 1 <= args.report <= 5:
            sys.exit("  [错误] 自评只能是 1~5：" + SCALE)
        st = read_status(args.status)
        append_entry(args.out, build_entry(args.report, True, session, st))
        print(f"  已记下：第 {st.get('round') if st else '?'} 轮 / "
              f"{(st or {}).get('strategy_id') or '?'} / 自评 {args.report}")
        return

    # ---------------- 交互录入 ----------------
    print("=" * 68)
    print("  自评录入 —— 一轮结束时打一个数字再回车")
    print("=" * 68)
    print(f"  评什么 ： {SCALE}")
    print("  回车跳过这一轮；q 或 Ctrl-C 退出（退出时打汇总表）")
    print(f"  记到哪 ： {args.out}")
    if read_status(args.status) is None:
        print("  ⚠ 没读到策略层的状态文件 —— 说明策略层现在没在跑。")
        print("    照样能记自评，但记不下「这是第几轮、用的哪套」。")
    print("=" * 68)

    mine: list[dict] = []
    try:
        while True:
            st = read_status(args.status)
            if st and st.get("strategy_id"):
                ctx = (f"[第 {st.get('round')} 轮 · {st.get('strategy_id')} "
                       f"{st.get('strategy_name')} · 现在 {st.get('state')} "
                       f"放松度 {st.get('relax')}]")
                if st.get("null_arm"):
                    ctx += "（这一轮是对照，我们什么都没做）"
            else:
                ctx = "[没读到轮次 —— 策略层没在跑？]"

            try:
                raw = input(f"\n{ctx}\n本轮感觉？> ").strip()
            except EOFError:
                break

            if raw.lower() in ("q", "quit", "exit"):
                break
            if not raw:
                print("  （跳过）")
                continue
            try:
                val = int(raw.split()[0])
            except (ValueError, IndexError):
                print("  请打 1~5 之间的一个数字。" + SCALE)
                continue
            if not 1 <= val <= 5:
                print("  只能 1~5。" + SCALE)
                continue

            entry = build_entry(val, True, session, st)
            append_entry(args.out, entry)
            mine.append(entry)
            print(f"  已记下：第 {entry['round']} 轮 / "
                  f"{entry['strategy_id']} / 自评 {val}"
                  f"（{entry['strategy_name']}）")
    except KeyboardInterrupt:
        print()
    finally:
        if mine:
            print()
            print("=" * 68)
            print(f"  这一场（{session}）的自评：")
            for line in summarize(mine):
                print(line)
            print("=" * 68)
        else:
            print("\n  这一场没记下任何自评。")


if __name__ == "__main__":
    main()
