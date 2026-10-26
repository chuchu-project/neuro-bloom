#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""八套策略表 —— 逐字抄自《NeuroBloom 接入指南》第 2 节。

为什么一个字都不改
-----------------
这张表不是我们编的，是合作方定的**实验设计**：四个旋钮各三档，八套组合。
其中 P8 是「不干预对照」(null arm)，是整个实验的尺子 ——
不测「什么都不做时情绪自己会平复多少」，就无法证明「做的干预有没有用」。

所以我们改的是**怎么用这张表**，不是表里写什么。
抄错一个 pace，或者「顺手优化」一下某套的数值，实验结论就废了。

我们只额外加了两类**标注**，都不碰表里的数值：
    available  —— 以我们现有的信号，能不能自动判断「什么时候该用它」
    why_not    —— 不能自动用的原因（写清楚，免得以后有人手贱打开）
    our_trigger—— 能自动用的那几套，我们实际是用什么信号触发的
"""
from __future__ import annotations

from dataclasses import dataclass

# ------------------------------------------------------------------
# 四个旋钮各自的合法取值（用来校验表没被写坏）
# ------------------------------------------------------------------
KNOB_VALUES = {
    "pace": ("slow", "mid", "fast"),
    "load": ("low", "mid", "high"),
    "audio": ("quiet", "neutral", "bright"),
    "visual": ("dim", "neutral", "vivid"),
}

KNOB_MEANING = {
    "pace": "节奏快慢（slow 慢 / mid 中 / fast 快）",
    "load": "难度高低（low 低 / mid 中 / high 高）",
    "audio": "音景安静→明亮（quiet / neutral / bright）",
    "visual": "画面偏暗→鲜艳（dim / neutral / vivid）",
}


@dataclass(frozen=True)
class Policy:
    """一套策略：四个旋钮 + 文档给的名称与设计意图。"""

    id: str
    name: str
    pace: str
    load: str
    audio: str
    visual: str
    hint: str                       # 文档「设计意图」列，逐字抄
    is_null_arm: bool = False       # 文档 is_null_arm 字段
    available: bool = True          # 我们能不能自动触发它（我们的标注）
    why_not: str = ""               # 不能自动用的原因（我们的标注）
    our_trigger: str = ""           # 自动触发时用的是什么信号（我们的标注）

    @property
    def knobs(self) -> dict:
        return {"pace": self.pace, "load": self.load,
                "audio": self.audio, "visual": self.visual}

    def line(self) -> str:
        return (f"{self.id} {self.name}"
                f"（pace={self.pace} load={self.load} "
                f"audio={self.audio} visual={self.visual}）")

    def __str__(self) -> str:                     # noqa: D105
        return self.line()


# ==================================================================
# 八套（数值必须与文档第 2 节一字不差）
# ==================================================================
POLICIES: dict[str, Policy] = {
    p.id: p for p in (
        Policy(
            id="P1", name="轻缓陪伴式",
            pace="slow", load="low", audio="quiet", visual="dim",
            hint="偏紧张、想被安抚",
            available=True,
            our_trigger="放松度偏低（tense）—— 单轴放松度就能判，这是最稳的一条",
        ),
        Policy(
            id="P2", name="节奏引导式",
            pace="mid", load="mid", audio="quiet", visual="neutral",
            hint="状态中等，愿意跟一点引导",
            available=True,
            our_trigger="放松度中等（neutral）—— 当作「没特别信号」时的中性档",
        ),
        Policy(
            id="P3", name="挑战唤醒式",
            pace="fast", load="high", audio="bright", visual="vivid",
            hint="偏低落、需要被提起来",
            available=False,
            why_not=("要先知道对方「偏低落」。低落/亢奋是情绪**效价**（喜不喜欢）那一维，"
                     "得靠效价指标或 FAA 才分得开；我们手上只有觉醒度（α/β），"
                     "分不出「低落」和「焦虑」——两者在这条轴上都是偏低或偏高。"
                     "硬接会把焦虑的人也拿去「提神」，方向正好反了。"),
        ),
        Policy(
            id="P4", name="静息恢复式",
            pace="slow", load="low", audio="quiet", visual="neutral",
            hint="疲惫，只想安静待着",
            available=True,
            our_trigger=("放松度偏高（relaxed）—— ⚠️ 这是**借用**：文档的设计意图是"
                         "「疲惫」，我们读不出疲惫，只是借这一套的旋钮值来表示"
                         "「此刻已经很松了，维持安静恢复」。对外不要说成「检测到疲惫」。"),
        ),
        Policy(
            id="P5", name="明快轻任务",
            pace="mid", load="low", audio="bright", visual="vivid",
            hint="偏紧张，但受不了太静",
            available=False,
            why_not=("「受不了太静」是**本人的偏好**，不是生理状态，脑电里读不出来。"
                     "这得靠问本人（问卷/选项），不是靠推断。"),
        ),
        Policy(
            id="P6", name="深度沉浸式",
            pace="mid", load="high", audio="quiet", visual="dim",
            hint="反复想事情，需要被吸进去",
            available=False,
            why_not=("「反复想事情」是反刍，同样要效价/内容维度。"
                     "单轴放松度读不出人在想什么，只能读出松紧。"),
        ),
        Policy(
            id="P7", name="短促高频式",
            pace="fast", load="mid", audio="neutral", visual="vivid",
            hint="注意力涣散，需要节奏抓回来",
            available=False,
            why_not=("「注意力涣散」要有专门的注意力指标（如额中线 θ、P300 之类）。"
                     "我们现在只有一条放松度轴，没有注意力轴 —— 这是缺一个信号，"
                     "不是缺一行代码。"),
        ),
        Policy(
            id="P8", name="不干预对照",
            pace="mid", load="mid", audio="neutral", visual="neutral",
            hint="null arm：刻意不改任何东西，用来测情绪自然漂移",
            is_null_arm=True,
            available=True,
            our_trigger="开局固定几轮 + 不确定时的兜底（永远可用，也永远必须可用）",
        ),
    )
}

# 不确定时用的兜底：P8（不加干预，比加错干预好）
FALLBACK_ID = "P8"


def get_policy(pid: str | None) -> Policy | None:
    if pid is None:
        return None
    return POLICIES.get(str(pid).strip().upper())


def is_null_arm(pid: str | None) -> bool:
    p = get_policy(pid)
    return bool(p and p.is_null_arm)


def available_policies() -> list[Policy]:
    """我们能自动触发的几套（其余四套的资料仍在表里，只是不自动用）。"""
    return [p for p in POLICIES.values() if p.available]


def check_table() -> list[str]:
    """检查表有没有被写坏，返回问题列表（空 = 没问题）。"""
    problems: list[str] = []

    if len(POLICIES) != 8:
        problems.append(f"应该是 8 套，实际 {len(POLICIES)} 套")
    if sorted(POLICIES) != [f"P{i}" for i in range(1, 9)]:
        problems.append(f"ID 应该是 P1~P8，实际 {sorted(POLICIES)}")
    if [p for p in POLICIES.values() if p.is_null_arm] != [POLICIES["P8"]]:
        problems.append("null arm 只能有 P8 一套")

    for p in POLICIES.values():
        if p.id != p.id.upper():
            problems.append(f"{p.id}: ID 要大写")
        for knob, val in p.knobs.items():
            if val not in KNOB_VALUES[knob]:
                problems.append(f"{p.id}: {knob}={val!r} 不是合法档位 {KNOB_VALUES[knob]}")
        if not p.available and not p.why_not:
            problems.append(f"{p.id}: 标了不可用却没写原因")
        if p.available and not p.our_trigger:
            problems.append(f"{p.id}: 标了可用却没写触发方式")

    # P8 的值是被点名的：文档说「绝对不能删，也不能改成别的值」
    p8 = POLICIES["P8"]
    if p8.knobs != {"pace": "mid", "load": "mid", "audio": "neutral", "visual": "neutral"}:
        problems.append(f"P8 的值被改了：{p8.knobs}（必须是四个 mid/neutral）")

    return problems


if __name__ == "__main__":
    bad = check_table()
    print("八套策略表：")
    for p in POLICIES.values():
        mark = "" if p.available else "  ← 不自动用"
        null = "  ★对照" if p.is_null_arm else ""
        print(f"  {p.line()}{null}{mark}")
        print(f"     设计意图：{p.hint}")
        if p.available:
            print(f"     我们怎么触发：{p.our_trigger}")
        else:
            print(f"     为什么不自动用：{p.why_not}")
    print()
    print("表检查：" + ("通过 ✓" if not bad else "有问题 ✗"))
    for b in bad:
        print("  ✗ " + b)
    raise SystemExit(0 if not bad else 1)
