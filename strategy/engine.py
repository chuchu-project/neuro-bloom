#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""策略层：在「脑电 → 四键」这条连续映射**上面**加一层氛围调制。

两层结构（这是整个设计最要紧的一句话）
------------------------------------
    底层 —— analysis/relax_axis.world_from_relax()：放松度 → 四个键。
            负责「一动就变」：人一松，花开、雾散、天亮。这是即时反馈，
            也是这套东西的意义所在。

    上层 —— 本文件：一轮之内用哪套氛围（八套策略表里的一套）。
            负责「这一轮整体偏暗偏静 / 整体明亮鲜艳」。

上层**只能调制，绝不能替换**底层：

    · 一旦替换，画面就和脑电脱钩了 —— 那正是接入文档里 autoPlay=true
      那个我们拒掉的坑：画面看着一直在变，其实跟人没关系，
      而且现场没人能一眼看出来。
    · 所以这里对每个键都保留一个「呼吸权重」BREATH，
      策略只是把画面朝它的目标值**拉一部分**，剩下那部分永远跟着脑电走。
    · bloom（花开）**完全不调制** —— 它是「跟着脑电动」的主信号，
      任何策略下它都必须忠实反映放松度。

P8（不干预对照）在这套设计里 = 不加任何调制 = 画面照常跟着脑电走。
所以演示时看到 P8 那几轮画面仍在动，是**对的**行为，不是 bug。
（如果按文档字面做成「P8 画面完全不动」，现场看起来就像死机了。）

本文件刻意不 import analysis/relax_axis：它只做字典到字典的算术，
这样它能被单独测，也不会把两条链路（现场/回放）的判定逻辑搅进来。
"""
from __future__ import annotations

import json
import os
import time

from .policies import POLICIES, FALLBACK_ID, Policy, check_table, get_policy

# ------------------------------------------------------------------
# 旋钮 → 场景参数的落点
# ------------------------------------------------------------------
# 「呼吸权重」：这一部分**永远**跟着脑电走，任何策略都不得覆盖。
# 取 0.55 而不是更高，是为了让八套之间的画面差异真能一眼看出来
# （文档第 4 节的硬要求）；取 0.55 而不是更低，是为了任何策略下
# 画面都还在跟脑电呼吸，不会变成一段循环动画。
BREATH = 0.55

# visual：画面偏暗↔鲜艳 → 阳光亮度 + 雾浓度（这两个最能一眼看出来，
# 而且 NeuroWorldController.cs 里它们直接驱动光照、天空盒与环境音）。
VISUAL_TARGET = {
    "dim":     {"sunlight": 0.25, "fog": 0.78},
    "neutral": {"sunlight": 0.55, "fog": 0.45},
    "vivid":   {"sunlight": 0.92, "fog": 0.15},
}

# pace：节奏快慢 → 水晶辉光的增益。
# ⚠️ 这是**借**来的落点：我们的场景里没有任务、没有动画速度可调。
#    慢档把水晶压暗（收敛、安静），快档抬亮（有劲儿、往前推）。
#    对外说「画面节奏」，不要说成「节奏训练」——我们并没有在做节奏训练。
# 幅度要拉开：P2（mid）和 P4（slow）的 visual 都是 neutral，
# 两者的差别**全在**这一项上。0.50↔1.00↔1.50 在实测数据的中段
# （放松度 0.4~0.6）已经把水晶拉开一倍以上，一眼能看出来。
# 两端会饱和到 0~1，这是对的：水晶本来就是「做到了」的奖励通道。
PACE_CRYSTAL_GAIN = {"slow": 0.50, "mid": 1.00, "fast": 1.50}

# 这两个旋钮我们**不接**，不是漏了：
#   load  —— 文档建议它管「任务数量/目标复杂度」。我们的场景里一个任务都没有；
#            而且「给偏紧张的人加难度」方向是反的。留到有任务设计时再说。
#   audio —— 文档建议它管「BGM 音量/明亮度」。Unity 场景里唯一能被程序动的
#            是环境音音量，而它现在已经由 sunlight 键驱动
#            （NeuroWorldController.cs 第 132 行 ambient.volume = Lerp(0.05,0.5,_sun)）。
#            要单独接 audio 必须改 Unity，那是第二阶段的事。
#            现在硬接只会让「音景」和「阳光」抢同一个音量 —— 一种看不见的打架。
UNWIRED_KNOBS = ("load", "audio")

# ------------------------------------------------------------------
# 用单轴放松度怎么触发那几套「可用」的策略
# ------------------------------------------------------------------
# ⚠️ 这是**借用**：文档每套的设计意图写的是心理学状态（偏紧张/中等/疲惫），
#    我们手上只有一条觉醒度轴，够不着那些状态本身。
#    所以我们只声明「在这种放松度下用这套」，不宣称「我们识别出了疲惫」。
STATE_TO_POLICY = {
    "tense":   "P1",   # 放松度低 → 轻缓陪伴式
    "neutral": "P2",   # 放松度中等 → 节奏引导式（中性档）
    "relaxed": "P4",   # 放松度高 → 静息恢复式（借来表示「维持安静恢复」）
    # 其余标签（calibrating / invalid）走透传分支，不会走到这里
}

# 轮次长度：文档建议 45 秒 × 20 轮。我们不用那个 ——
# 现场 45 秒 × 开头 3 轮对照 = 135 秒画面「几乎没在按策略动」，
# 评委看到的是死机。20 秒一轮，面板上能看清每一轮在干什么。
DEFAULT_ROUND_SECONDS = 20.0
DEFAULT_CONTROL_ROUNDS = 3      # 开局固定 3 轮 P8（文档要求）

# 自评小工具（tools/feedback.py）靠这个小文件知道「现在是第几轮、用的哪套」
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_STATUS_PATH = os.path.join(_PROJECT_ROOT, ".data", "last_state.json")


# ------------------------------------------------------------------
# 调制
# ------------------------------------------------------------------
def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _mix(a: float, b: float, p: float) -> float:
    """朝 b 拉 p 那么多（p=0 完全不动，p=1 完全变成 b）。"""
    return float(a) + (float(b) - float(a)) * float(p)


def modulate(base_world: dict, policy: Policy | None) -> dict:
    """底层四键 + 一轮的策略 → 最后推给 Unity 的四键。

    base_world 必须是 world_from_relax() 的输出（0~1 的四个键）。
    policy 为 None 或是 P8（null arm）时**原样返回** ——
    「不干预」的准确含义就是这个，不能只是「改得少一点」。
    """
    if policy is None or policy.is_null_arm:
        return dict(base_world)

    out = dict(base_world)
    pull = 1.0 - BREATH

    # 画面偏暗↔鲜艳：把底层的值朝策略目标拉一部分。
    # 保留 BREATH 的比例仍然跟着脑电 → 策略换了画面会明显变，
    # 但人放松时画面依旧会亮起来、雾依旧会散，不会「定死」。
    tgt = VISUAL_TARGET[policy.visual]
    out["sunlight"] = _mix(base_world["sunlight"], tgt["sunlight"], pull)
    out["fog"] = _mix(base_world["fog"], tgt["fog"], pull)

    # 节奏：只缩放水晶辉光的幅度（跟着脑电动，但慢档更收敛、快档更亮）
    out["crystal"] = _clamp01(base_world["crystal"] * PACE_CRYSTAL_GAIN[policy.pace])

    # bloom 保持不动 —— 它是「跟着脑电动」的主信号（见文件头）
    out["bloom"] = base_world["bloom"]

    return {k: round(_clamp01(v), 3) for k, v in out.items()}


# ------------------------------------------------------------------
# 轮次
# ------------------------------------------------------------------
class RoundTimer:
    """按**数据时间**走的轮次计时器（不是墙上时间）。

    为什么不用墙上时间：回放有倍速。--speed 2 时每 20 秒数据只花 10 秒播完，
    用墙上时间的话轮次会和画面内容对不上。所有调用方都按「这个窗口值多少秒」
    喂进来（现场 = EEG_STEP，回放 = --step），两条路就都能对上。
    """

    def __init__(self, round_seconds: float = DEFAULT_ROUND_SECONDS,
                 control_rounds: int = DEFAULT_CONTROL_ROUNDS):
        self.round_seconds = max(1.0, float(round_seconds))
        self.control_rounds = max(0, int(control_rounds))
        self.round_index = 1
        self.elapsed = 0.0

    def advance(self, dt: float) -> bool:
        """推进 dt 秒，跨过边界就进入下一轮，返回 True。"""
        self.elapsed += max(0.0, float(dt))
        crossed = False
        while self.elapsed >= self.round_seconds:
            self.elapsed -= self.round_seconds
            self.round_index += 1
            crossed = True
        return crossed

    @property
    def in_control(self) -> bool:
        """是否还在开局的 P8 对照段（文档要求前几轮固定 P8）。"""
        return self.round_index <= self.control_rounds


# ------------------------------------------------------------------
# 引擎
# ------------------------------------------------------------------
class StrategyEngine:
    """一个进程一个实例；每个窗口喂一次，拿到「这一帧该推什么」。

    用法（两条链路完全一样）：
        out = engine.feed(base_world_params, payload["label"], dt, payload.get("relax"))
        payload["world_params"] = out["world_params"]
        payload.update({k: v for k, v in out.items() if k != "world_params"})
        engine.dump()          # 给自评小工具读「现在第几轮」
    """

    def __init__(self, round_seconds: float = DEFAULT_ROUND_SECONDS,
                 control_rounds: int = DEFAULT_CONTROL_ROUNDS,
                 enabled: bool = True):
        self.enabled = bool(enabled)
        self.timer = RoundTimer(round_seconds, control_rounds)
        self.policy: Policy | None = None
        self.last_label: str | None = None
        self.last_relax: float | None = None
        self.rounds: list[dict] = []     # [{round, strategy, windows}] 供成绩单/小结用
        self.n_passthrough = 0           # 因标定/坏数据而原样透传的窗口数

    # ---------------- 选策略 ----------------
    def _pick(self, label: str) -> Policy:
        if self.timer.in_control:
            # 开局固定几轮 P8：先量「什么都不做时情绪自己会漂多少」。
            # 这几轮画面照常跟着脑电走，只是不额外干预。
            return get_policy(FALLBACK_ID)          # type: ignore[return-value]
        pid = STATE_TO_POLICY.get(label, FALLBACK_ID)
        pol = get_policy(pid) or get_policy(FALLBACK_ID)
        assert pol is not None
        return pol

    # ---------------- 主入口 ----------------
    def feed(self, base_world: dict, label: str, dt: float,
             relax: float | None = None) -> dict:
        self.last_label = label
        if relax is not None:
            self.last_relax = float(relax)

        # 标定期 / 坏数据：不下结论、不推进轮次、**也不调制**。
        # 不调制是必须的：底层这时候给的就是「上一帧的值」，
        # 在这里再套一次调制，画面会朝策略目标慢慢漂过去 ——
        # 看着像有人偷偷在拧旋钮，实际什么都没发生。
        if not self.enabled or label in ("calibrating", "invalid"):
            self.n_passthrough += 1
            return self._out(base_world, applied=False, round_changed=False)

        round_changed = self.timer.advance(dt)
        if self.policy is None or round_changed:
            # 策略**只在轮次边界上决定一次**，一轮之内不再改：
            # 每个窗口都重新选，画面会跟着判定在几套之间来回跳。
            self.policy = self._pick(label)
            self.rounds.append({"round": self.timer.round_index,
                                "strategy": self.policy.id, "windows": 0})
        if self.rounds:
            self.rounds[-1]["windows"] += 1

        return self._out(base_world, applied=True, round_changed=round_changed)

    def _out(self, base_world: dict, applied: bool, round_changed: bool) -> dict:
        p = self.policy
        return {
            "world_params": modulate(base_world, p) if applied else dict(base_world),
            "strategy": p.id if p else None,
            "strategy_name": p.name if p else None,
            # 没有策略（标定期 / 坏数据 / 关掉策略层）时给 None，意思是「不适用」。
            # 不要给 False —— 否则自评统计会把它算进「干预轮」，
            # 把对照轮 vs 干预轮那个对比悄悄搞坏。
            "null_arm": bool(p.is_null_arm) if p else None,
            "round": self.timer.round_index,
            "round_seconds": self.timer.round_seconds,
            "round_changed": bool(round_changed),
            "modulated": bool(applied and p is not None and not p.is_null_arm),
        }

    # ---------------- 对外状态 ----------------
    def status(self) -> dict:
        p = self.policy
        return {
            "ts": time.time(),
            "enabled": self.enabled,
            "round": self.timer.round_index,
            "round_elapsed": round(self.timer.elapsed, 2),
            "round_seconds": self.timer.round_seconds,
            "control_rounds": self.timer.control_rounds,
            "in_control": self.timer.in_control,
            "strategy_id": p.id if p else None,
            "strategy_name": p.name if p else None,
            # 没有策略（标定期 / 坏数据 / 关掉策略层）时给 None，意思是「不适用」。
            # 不要给 False —— 否则自评统计会把它算进「干预轮」，
            # 把对照轮 vs 干预轮那个对比悄悄搞坏。
            "null_arm": bool(p.is_null_arm) if p else None,
            "state": self.last_label,
            "relax": self.last_relax,
            "n_passthrough": self.n_passthrough,
        }

    def dump(self, path: str | None = None) -> None:
        """把当前轮次写到小文件里，给自评小工具读。

        写不进去（只读目录等）不该拖垮主链路 —— 所以这里吞掉所有 IO 异常。
        """
        path = path or DEFAULT_STATUS_PATH
        try:
            d = os.path.dirname(path) or "."
            os.makedirs(d, exist_ok=True)
            tmp = os.path.join(d, ".last_state.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.status(), f, ensure_ascii=False)
            os.replace(tmp, path)          # 原子替换，读的人不会读到半截
        except OSError:
            pass

    def banner(self) -> str:
        """轮次切换时打给人看的一行。"""
        p = self.policy
        if p is None:
            return ""
        if p.is_null_arm:
            return (f"── 第 {self.timer.round_index} 轮 · {p.id} 不干预对照"
                    f"（现在什么都不额外做，量自然漂移；画面照常跟着脑电动）──")
        return (f"── 第 {self.timer.round_index} 轮 · {p.id} {p.name}"
                f"（pace={p.pace} load={p.load} audio={p.audio} "
                f"visual={p.visual}）──")

    def summary(self) -> list[str]:
        """一轮一行的回执，回放结束时打印（也算是给评委看的过程记录）。"""
        if not self.rounds:
            return []
        lines = ["  轮次   策略   窗口   这套是什么"]
        for r in self.rounds:
            p = get_policy(r["strategy"])
            if p is None:
                continue
            tag = "（对照：不额外干预）" if p.is_null_arm else ""
            lines.append(f"  {r['round']:>4}   {p.id}   {r['windows']:>4}   {p.name}{tag}")
        return lines


# ------------------------------------------------------------------
# 环境变量：回退开关必须是一行就能按下去的
# ------------------------------------------------------------------
def enabled_from_env() -> bool:
    """EEG_STRATEGY=0 → 退回纯连续映射（策略层整个不生效）。"""
    v = os.getenv("EEG_STRATEGY", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def round_seconds_from_env(default: float = DEFAULT_ROUND_SECONDS) -> float:
    try:
        return float(os.getenv("EEG_ROUND_SECONDS", str(default)))
    except ValueError:
        return default


def control_rounds_from_env(default: int = DEFAULT_CONTROL_ROUNDS) -> int:
    try:
        return int(os.getenv("EEG_CONTROL_ROUNDS", str(default)))
    except ValueError:
        return default


# ==================================================================
# 自检
# ==================================================================
def _self_test() -> int:
    import math

    print("=" * 66)
    print("  strategy 自检：策略表 / P8 逐位一致 / 轮次 / 透传")
    print("=" * 66)
    ok = True

    # ---- 1. 表 ----
    bad = check_table()
    print(f"  策略表        ：{len(bad) and '有问题 ✗' or '通过 ✓'}"
          f"（{len(POLICIES)} 套）")
    for b in bad:
        print("    ✗ " + b)
    ok &= not bad

    base = {"bloom": 0.5, "sunlight": 0.625, "fog": 0.5, "crystal": 0.25}

    # ---- 2. P8 逐位一致 ----
    p8 = get_policy("P8")
    same = modulate(base, p8) == dict(base)
    also = modulate(base, None) == dict(base)
    print(f"  P8 不改造画面  ：{'通过 ✓' if same and also else '不通过 ✗'}"
          f"（P8={'一样' if same else '被改了'}，无策略={'一样' if also else '被改了'}）")
    ok &= same and also

    # ---- 3. 调制幅度 & 边界 ----
    legal = True
    for p in POLICIES.values():
        out = modulate(base, p)
        if set(out) != set(base):
            print(f"    ✗ {p.id}: 键变了 {sorted(out)}")
            legal = False
        if any(not (0.0 <= v <= 1.0) for v in out.values()):
            print(f"    ✗ {p.id}: 出界 {out}")
            legal = False
        if out["bloom"] != base["bloom"]:
            print(f"    ✗ {p.id}: bloom 被改了（它是跟着脑电走的主信号）")
            legal = False
    # dim 与 vivid 必须一眼看得出来（文档第 4 节的硬要求）
    dim = modulate(base, get_policy("P1"))
    viv = modulate(base, get_policy("P5"))
    gap = abs(viv["sunlight"] - dim["sunlight"])
    if gap < 0.25:
        print(f"    ✗ dim 与 vivid 差别太小（{gap:.2f} < 0.25），现场看不出来")
        legal = False
    print(f"  八套都合法    ：{'通过 ✓' if legal else '不通过 ✗'}"
          f"　dim↔vivid 阳光差 {gap:.2f}")
    ok &= legal

    # ---- 4. 轮次：开局固定 3 轮 P8 ----
    # 49 窗 × 2s = 98s，落在第 5 轮里（第 6 轮还没开始）
    st = StrategyEngine(round_seconds=20.0, control_rounds=3)
    seq = []
    for i in range(49):
        out = st.feed(base, "tense", 2.0, relax=0.2)
        seq.append((out["round"], out["strategy"], out["modulated"]))
    rounds = {r: s for r, s, _ in seq}
    expect = {1: "P8", 2: "P8", 3: "P8", 4: "P1", 5: "P1"}
    good = rounds == expect
    still = all(not m for r, s, m in seq if r <= 3)
    print(f"  开局 3 轮对照 ：{'通过 ✓' if good else '不通过 ✗'}  实际 {rounds}")
    print(f"  对照轮不调制   ：{'通过 ✓' if still else '不通过 ✗'}")
    ok &= good and still

    # ---- 5. 标签 → 策略 ----
    mapped = True
    for label, pid in (("tense", "P1"), ("neutral", "P2"), ("relaxed", "P4")):
        st2 = StrategyEngine(round_seconds=20.0, control_rounds=0)
        got = st2.feed(base, label, 2.0)["strategy"]
        if got != pid:
            print(f"    ✗ {label} 应是 {pid}，实际 {got}")
            mapped = False
    print(f"  标签→策略映射  ：{'通过 ✓' if mapped else '不通过 ✗'}")
    ok &= mapped

    # ---- 6. 标定/坏数据：不推进轮次、不调制 ----
    st3 = StrategyEngine(round_seconds=20.0, control_rounds=0)
    st3.feed(base, "tense", 2.0)                       # 第 1 轮，P1
    before = (st3.timer.round_index, st3.timer.elapsed)
    for lb in ("calibrating", "invalid") * 20:
        o = st3.feed(base, lb, 2.0)
        if o["world_params"] != dict(base) or o["modulated"]:
            print(f"    ✗ {lb} 时不该调制，实际 modulated={o['modulated']}")
            ok = False
            break
    after = (st3.timer.round_index, st3.timer.elapsed)
    same_time = before == after
    print(f"  标定/坏数据透传：{'通过 ✓' if same_time else '不通过 ✗'}"
          f"（轮次 {before} → {after}）")
    ok &= same_time

    # ---- 7. 呼吸感：策略换了，人一松画面仍要亮起来 ----
    calm_base = {"bloom": 0.95, "sunlight": 0.9625, "fog": 0.05, "crystal": 1.0}
    tense_base = {"bloom": 0.05, "sunlight": 0.2875, "fog": 0.95, "crystal": 0.0}
    breathe_ok = True
    for pid in ("P1", "P2", "P4", "P5"):
        p = get_policy(pid)
        # 人一松，雾必须变小（而不是不变、更不能反着来）
        if not modulate(calm_base, p)["fog"] < modulate(tense_base, p)["fog"]:
            print(f"    ✗ {pid}: 雾不再跟着脑电动（松了雾没散）")
            breathe_ok = False
        # 而且不能「定死」：两种状态下画面必须看得出差别
        if abs(modulate(calm_base, p)["fog"] - modulate(tense_base, p)["fog"]) < 0.1:
            print(f"    ✗ {pid}: 画面被策略定死了（松紧两种状态差别 < 0.1）")
            breathe_ok = False
    print(f"  松→雾散仍成立 ：{'通过 ✓' if breathe_ok else '不通过 ✗'}"
          f"（任何策略下画面都还在跟脑电呼吸）")
    ok &= breathe_ok

    # ---- 8. bloom 与数值健全 ----
    p1 = get_policy("P1")
    m = modulate(base, p1)
    print(f"  P1 示例       ：基础 {base} → {m}")
    finite = all(math.isfinite(v) for v in m.values())
    print(f"  数值健全      ：{'通过 ✓' if finite else '不通过 ✗'}")
    ok &= finite

    print("=" * 66)
    print(f"  结果：{'通过 ✓' if ok else '不通过 ✗'}")
    print("=" * 66)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_self_test())
