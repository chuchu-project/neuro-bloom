#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""单轴「放松度」判定 —— 现场采集和离线回放共用的唯一一套逻辑。

为什么要有这个文件
------------------
一开始项目里其实长出了两套判定：

    · 现场采集（eeg_service/main.py + state.py）
        三分类(放松/专注/焦虑) + 写死的固定阈值
    · 离线回放（eeg_service/replay_service.py）
        单轴「放松度」+ 相对个人基线

结果是：同一个人、同一段脑电，两条路给出的画面完全不一样。
而且固定阈值那条路本身就不准 —— 每个人的 α/β 绝对水平差好几倍
（实测同一个人不同时段都能差 3 倍），拿一个固定数字去套所有人，
等于拿别人的尺子量自己。

所以把「怎么算放松度、怎么映射成画面」抽到这里，两边都 import 这里。
谁也不能再自己偷偷长出一套判定 —— 那正是上面那个 bug 的成因。

两边唯一该有的区别是**平滑方式**，而且必须不同：

    回放 —— 数据早就录好了，允许看前后邻居 → 居中平滑，没有滞后
    现场 —— 只有过去的数据               → 因果平滑，否则就是偷看未来

数据单位约定
------------
喂进来的 seg 必须是**微伏(µV)**，形状 (通道, 采样点)。
MNE 读出来的原始数据是伏特(约 1e-6)，用之前要 ×1e6；
BrainFlow 出来的本来就是 µV。两边都归一到这里，判据才能是同一把尺子。
"""
from __future__ import annotations

import math
import time
from collections import deque

import numpy as np

# 复用 alpha_beta_state 里那套「先挑好通道、再算功率」的底层 ——
# 尤其是 channel_quality()，它是整个判定能不能信的关键：
# 一个饱和通道的功率能顶掉十几个好通道（实测剔不剔坏通道，
# α/β 的波动是 65% 和 47% 的差别）。
from .alpha_beta_state import (  # noqa: E402
    ALPHA_BAND,
    BETA_BAND,
    MIN_GOOD_CHANNELS,
    band_power,
    channel_quality,
    window_spectrum,
)
# 频带功率和左右半球不对称由 lateralization 一次性算完，
# 这里不再重复做一次 Welch —— 同一个窗口算两遍谱纯属浪费。
from .lateralization import metrics_from_segment  # noqa: E402

# ------------------------------------------------------------------
# 换算常数
# ------------------------------------------------------------------
# 「相对基线的 α/β 倍数」要换成 0~1 的放松度，用 log2 尺度：
# 0.5 倍（减半）和 2 倍（翻倍）离基线的距离一样，符合直觉。
# RELAX_SPAN=1.0 → 翻一倍 = 完全放松，减半 = 完全紧张。
RELAX_SPAN = 1.0

# crystal 不是跟 bloom 一样的线性曲线，它在 0.4 以下一直贴着 0，
# 0.8 以上全亮 —— 当作「我做到了」的正反馈通道，而不是四盏灯一起动。
# 起点取 0.4 而不是 0.5，是因为实测干净数据里放松度大多落在 0.4~0.8，
# 起点定太高水晶会一直趴在 0 上不动，白扔一个通道。
CRYSTAL_LO = 0.4
CRYSTAL_SPAN = 0.4

# label 的两个分界（只影响 Unity 里那行提示文字，不影响画面）
RELAXED_AT = 0.65
TENSE_AT = 0.35

# 第二条轴：专注度（β/θ）。和放松度一样用 log2 相对个人基线，
# 翻倍 = 1.0、减半 = 0.0。两条轴分开算，才能出现
# 「又放松又专注」这种真实存在的状态（老版只有一条轴，把两者混成了一个数）。
FOCUS_SPAN = 1.0


# ==================================================================
# 一、放松度本身
# ==================================================================
def relax_from_score(score: float, span: float = RELAX_SPAN) -> float:
    """把「相对基线的 α/β 倍数」换算成 0~1 的放松度。

        score = 1.0 → 0.50（跟自己的基线一样）
        score = 2.0 → 1.00（α/β 翻倍，明显放松）
        score = 0.5 → 0.00（α/β 减半，明显紧张）

    span 越小越灵敏（span=0.5 时 1.41 倍就算完全放松）。
    """
    if score is None or not np.isfinite(score) or score <= 0:
        return 0.5
    r = 0.5 + 0.5 * (math.log2(score) / span)
    return float(max(0.0, min(1.0, r)))


def focus_from_bt(bt: float, base_bt: float, span: float = FOCUS_SPAN) -> float:
    """β/θ 相对个人基线的倍数 → 0~1 的专注度。

    为什么用 β/θ 而不是单看 β：
        β 高也可能是紧张（肌电、焦虑都会顶高 β）。
        拿 θ 做分母，等于先扣掉「困倦/走神」那一部分，
        剩下的才是「清醒且投入」，这也是文献里常用的注意力指标。
    """
    if not base_bt or base_bt <= 0 or not math.isfinite(bt) or bt <= 0:
        return 0.5
    return float(min(1.0, max(0.0, 0.5 + 0.5 * math.log2(bt / base_bt) / span)))


def label_from_relax(relax: float) -> str:
    if relax >= RELAXED_AT:
        return "relaxed"
    if relax <= TENSE_AT:
        return "tense"
    return "neutral"


def scene_words(relax: float) -> str:
    """给终端打印用的人话描述，方便对着 Unity 画面看。"""
    if relax >= RELAXED_AT:
        return "天亮 · 花开 · 无雾"
    if relax <= TENSE_AT:
        return "昏暗 · 花小 · 浓雾"
    return "过渡中"


# ==================================================================
# 二、放松度 → Unity 的四个键
# ==================================================================
def world_from_relax(relax: float) -> dict:
    """放松度 → Unity 的四个键（0~1）。

    视觉上两端要拉得足够开，否则「可区分」就是空话：

        放松(1.0) → 花开满、阳光足、没有雾、水晶发亮
        紧张(0.0) → 花很小、光线暗、浓雾、水晶暗淡

    四个键在 Unity 端各管什么（见 NeuroWorldController.cs）：
        sunlight → 主光源强度与光色、环境光、环境音音量 …… 画面亮不亮
        fog      → 雾密度 + 天空盒颜色（天空的 storm 值就是它）…… 天阴不阴
        bloom    → 花朵整体缩放
        crystal  → 水晶缩放 + 水晶辉光强度 …… 拿来当「奖励」

    ⚠️ 这四个键名是和 Unity 场景绑定死的（WorldParams 结构体），
       可以改数值，但**不要改键名**，否则 Unity 那边解析出来全是 0。
    """
    relax = float(max(0.0, min(1.0, relax)))
    return {
        "bloom": round(relax, 3),                             # 花/植被 0~1
        "sunlight": round(0.25 + 0.75 * relax, 3),            # 阳光 0.25~1
        "fog": round(1.0 - relax, 3),                         # 雾 1~0
        "crystal": round(                            # 奖励通道：放松才亮
            max(0.0, min(1.0, (relax - CRYSTAL_LO) / CRYSTAL_SPAN)), 3),
    }


# ==================================================================
# 三、从一段数据算出 α/β —— 现场和回放共用
# ==================================================================
def ratio_from_segment(seg: np.ndarray, sfreq: float):
    """一段数据 → (α/β 比值, 合格通道数, α功率, β功率)。

    seg 形状 (通道, 采样点)，单位 µV。
    数据不合格时第一项返回 None（这就是「不下结论」的入口）。
    """
    good = channel_quality(seg)
    n_good = int(good.sum())
    if n_good < MIN_GOOD_CHANNELS:
        return None, n_good, float("nan"), float("nan")

    try:
        psds, freqs = window_spectrum(seg[good], sfreq)
    except ValueError:
        return None, n_good, float("nan"), float("nan")

    a = band_power(psds, freqs, ALPHA_BAND)
    b = band_power(psds, freqs, BETA_BAND)
    if not np.isfinite(a) or not np.isfinite(b) or b <= 0:
        return None, n_good, a, b
    return a / b, n_good, a, b


# ==================================================================
# 四、个人基线
# ==================================================================
def pick_baseline(results, seconds: float = 60.0):
    """（离线用）从整段记录里挑最干净的连续一段当基线，返回 (基线比值, 起止秒)。

    为什么不直接用开头那段：
        真实录的数据开头往往正好最脏 —— 刚连上、电极还没稳、凝胶还没铺开。
        实测那份 874 秒的记录，开头 138 秒全部饱和，
        拿它当基线等于用一把坏尺子去量后面所有数据。
    """
    if not results:
        return None, None

    step = (results[1].start - results[0].start) if len(results) > 1 else 2.0
    need = max(1, int(seconds / step))
    if need > len(results):
        need = len(results)

    best_i, best_n = 0, -1
    for i in range(len(results) - need + 1):
        n = sum(1 for r in results[i:i + need] if r.valid)
        if n > best_n:
            best_n, best_i = n, i

    seg = [r for r in results[best_i:best_i + need] if r.valid]
    if not seg:
        return None, None

    base = float(np.median([r.ratio for r in seg]))
    return base, (results[best_i].start, results[best_i + need - 1].end)


# ==================================================================
# 五、平滑：两条路各用各的
# ==================================================================
def moving_average(values, n: int):
    """（离线用）居中滑动平均。values 里可以混 None（无效窗口），跳过不计。

    为什么离线必须平滑：
        单窗口 α/β 的噪声底实测有 30%~60%。不平滑直接推给 Unity，
        画面会在「天亮」和「浓雾」之间每 2 秒闪一次，看着像坏了。
    """
    if n <= 1:
        return list(values)
    half = n // 2
    out = []
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        win = [v for v in values[lo:hi] if v is not None]
        out.append(sum(win) / len(win) if win else None)
    return out


def causal_average(values, n: int):
    """（现场用）因果滑动平均：第 i 个只由第 i-n+1..i 个决定，不碰未来。

    现场没有「未来」可看。用居中平滑等于偷看后面几百毫秒的数据，
    演示时看着更好看，但那是假的 —— 真做产品时不可能实现。
    """
    if n <= 1:
        return list(values)
    out = []
    buf: deque = deque(maxlen=n)
    for v in values:
        if v is not None:
            buf.append(v)
        out.append(sum(buf) / len(buf) if buf else None)
    return out


# ==================================================================
# 六、现场在线判定器
# ==================================================================
class RelaxTracker:
    """现场用的在线判定：喂进一个窗口的数据，吐出一个推送包。

    生命周期：
        标定期 —— 前 baseline_seconds 秒只量基线，不下结论，
                  这段时间画面停在中间色（不冒充「你现在很放松」）。
        判定期 —— 每个窗口算相对基线的倍数 → 放松度 → 因果平滑 → world_params。

    和离线回放的差别只有两处，都是本质差别、不是实现偷懒：
        · 基线只能取「开头这段」（离线可以回头挑最干净的一段）
        · 平滑只能用因果的（离线可以用居中）
    """

    def __init__(self, window: float = 4.0, step: float = 2.0, smooth: int = 5,
                 baseline_seconds: float = 60.0, relax_span: float = RELAX_SPAN):
        self.window = float(window)              # 判定窗口秒数
        self.step = float(step)                  # 窗口步长秒数
        self.smooth = max(1, int(smooth))        # 因果平滑窗口个数
        self.baseline_seconds = float(baseline_seconds)
        self.relax_span = float(relax_span)

        self._baseline = None
        self._calib_ratios: list[float] = []
        self._calib_bt: list[float] = []
        self._calib_seconds = 0.0
        self._history: deque = deque(maxlen=self.smooth)
        self._focus_history: deque = deque(maxlen=self.smooth)
        self._bt_baseline = None
        self._prev_params = None

        self.n_windows = 0
        self.n_valid = 0
        self.n_invalid = 0

    # ---------------- 状态查询 ----------------
    @property
    def baseline(self):
        return self._baseline

    @property
    def calibrating(self) -> bool:
        return self._baseline is None

    @property
    def calib_progress(self) -> float:
        if self.baseline_seconds <= 0:
            return 1.0
        return float(min(1.0, self._calib_seconds / self.baseline_seconds))

    # ---------------- 内部 ----------------
    def _hold(self, label: str, n_good: int, note: str | None = None) -> dict:
        """不下结论时的输出：世界参数保持上一个值，场景就不动。

        这比「随便给个值让画面乱跳」诚实得多 ——
        数据坏的时候，让场景停着，比让它演一场假戏强。
        """
        p = {
            "t": time.time(),
            "label": label,
            "n_good": n_good,
            "world_params": self._prev_params or world_from_relax(0.5),
        }
        if note:
            p["note"] = note
        return p

    # ---------------- 主入口 ----------------
    def feed(self, seg: np.ndarray, sfreq: float) -> dict:
        self.n_windows += 1
        m = metrics_from_segment(seg, float(sfreq))
        n_good = int(m.get("n_good", 0))

        if not m.get("ok"):
            self.n_invalid += 1
            reason = m.get("reason")
            note = (f"电极问题，只有 {n_good} 个通道合格"
                    if reason in ("few_channels", "shape")
                    else f"这一窗算不出频谱（{reason}）")
            return self._hold("invalid", n_good, note)

        a, b = m["alpha"], m["beta"]
        ratio = a / b                      # 与 ratio_from_segment 完全同一个式子
        theta = m.get("theta", float("nan"))
        bt = b / theta if np.isfinite(theta) and theta > 0 else float("nan")

        # ---- 标定期 ----
        if self._baseline is None:
            self._calib_ratios.append(ratio)
            if np.isfinite(bt) and bt > 0:
                self._calib_bt.append(bt)
            self._calib_seconds += self.step
            if self._calib_seconds < self.baseline_seconds or len(self._calib_ratios) < 3:
                return self._hold("calibrating", n_good)

            self._baseline = float(np.median(self._calib_ratios))
            if self._calib_bt:
                self._bt_baseline = float(np.median(self._calib_bt))

            # 把刚用来标定的这些窗口按新基线重算，补进平滑历史，
            # 这样标定一结束画面就是连续的，不会从中间突然开始
            for r0 in self._calib_ratios:
                self._history.append(
                    relax_from_score(r0 / self._baseline, self.relax_span))
            if self._bt_baseline:
                for bt0 in self._calib_bt:
                    self._focus_history.append(
                        focus_from_bt(bt0, self._bt_baseline))

        # ---- 判定期 ----
        self.n_valid += 1
        score = ratio / self._baseline if self._baseline else ratio
        relax = relax_from_score(score, self.relax_span)
        self._history.append(relax)
        smooth = sum(self._history) / len(self._history)

        # 专注度是第二条独立的轴：它不参与 relax 的计算，
        # 也不改 world_params，只是多给一个可看的数（和可记录的指标）。
        focus = None
        if np.isfinite(bt) and bt > 0 and self._bt_baseline:
            self._focus_history.append(focus_from_bt(bt, self._bt_baseline))
            focus = sum(self._focus_history) / len(self._focus_history)

        params = world_from_relax(smooth)
        self._prev_params = params
        payload = {
            "t": time.time(),
            "label": label_from_relax(smooth),
            "alpha_ratio": round(a, 3),
            "beta_ratio": round(b, 3),
            "score": round(score, 3),
            "relax": round(smooth, 3),
            "calm": round(smooth, 3),          # 兼容老字段名，Unity 不看这个
            "n_good": n_good,
            "world_params": params,
            "bands": m.get("bands"),           # δ/θ/α/β/γ 五频带功率
            "lr": m.get("lr"),                 # 左右半球不对称
            "bt": round(float(bt), 3) if np.isfinite(bt) else None,
        }
        if focus is not None:
            payload["focus"] = round(focus, 3)
        return payload


# ==================================================================
# 七、自检：合成一段已知答案的数据，看能不能判对
# ==================================================================
def _synth(seconds: float, sfreq: float, alpha_amp: float, beta_amp: float,
           seed: int = 0) -> np.ndarray:
    """造一段 (1 通道, 采样点) 的假脑电：10Hz 当 α，20Hz 当 β。

    功率比 ≈ (α幅值 / β幅值)²，所以想让 α/β 翻倍，幅值乘 √2 就行。
    """
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sfreq)) / sfreq
    sig = alpha_amp * np.sin(2 * np.pi * 10.0 * t) + beta_amp * np.sin(2 * np.pi * 20.0 * t)
    return (sig + rng.normal(0, 5.0, sig.shape))[None, :]


def _self_test() -> int:
    sfreq = 250.0
    window, step = 4.0, 2.0
    sfreq_int = int(sfreq)
    print("=" * 62)
    print("  relax_axis 自检：基线 60s → 放松 30s → 紧张 30s")
    print("=" * 62)

    # α/β 目标：基线 1.0 → 放松 2.0 → 紧张 0.5
    # 幅值比 = sqrt(比值)，固定 β 幅值，α 幅值就是 30*sqrt(比值)。
    # 幅值取 30µV 是为了让 std 落在真实 EEG 的量级（几十 µV）——
    # 太小会被 channel_quality 当「通道卡死」（去趋势后 std < FLAT_STD=1）挡掉。
    #
    # ⚠ 这里断言的是「比值 ÷ 基线」这个相对量，不是 α/β 的绝对值。
    # 绝对值会带上 band_power() 的带宽偏差：它算的是频带内 PSD 的「平均」，
    # α 带（8-13Hz）5 个频点、β 带（13-30Hz）17 个频点，所以两个幅值相等的
    # 正弦，原始 α/β 也是 17/5 ≈ 3.4 而不是 1.0。这个常数在除以个人基线时
    # 精确抵消，放松度判定因此不受影响 —— 但它意味着 α/β 的「数值」不能
    # 拿去跟别的软件比，只能跟自己比。这是选定的口径，不改。
    phases = [("基线", 60.0, 1.0, 60), ("放松", 30.0, 2.0, 31), ("紧张", 30.0, 0.5, 32)]

    tracker = RelaxTracker(window=window, step=step, smooth=5,
                           baseline_seconds=60.0)
    win_n = int(window * sfreq)

    print(f"  {'阶段':<6}{'窗口数':>7}{'比值/基线':>10}{'放松度均值':>12}{'判定':>10}")
    print("  " + "-" * 46)
    ok = True
    for name, secs, target_ratio, seed in phases:
        data = _synth(secs, sfreq,
                      30.0 * math.sqrt(target_ratio), 30.0, seed=seed)
        # 单通道不满足 MIN_GOOD_CHANNELS，所以复制成 16 通道（各自独立噪声）
        data = np.repeat(data, 16, axis=0)
        data += np.random.default_rng(7).normal(0, 5.0, data.shape)

        vals, labels, scores = [], [], []
        for i in range(0, data.shape[1] - win_n + 1, int(step * sfreq)):
            out = tracker.feed(data[:, i:i + win_n], sfreq_int)
            if out["label"] in ("invalid", "calibrating"):
                continue
            vals.append(out["relax"])
            labels.append(out["label"])
            scores.append(out["score"])

        if not vals:
            print(f"  {name:<6}{0:>7}{'—':>10}{'—':>12}{'标定期/无数据':>10}")
            continue
        mean_relax = float(np.mean(vals))
        mean_score = float(np.mean(scores))
        print(f"  {name:<6}{len(vals):>7}{mean_score:>10.3f}"
              f"{mean_relax:>12.3f}"
              f"{sorted(set(labels))[0] if len(set(labels)) == 1 else 'mixed':>10}")

        # 核心不变量：量出来的「比值/基线」要接近造数据时设定的倍数
        if abs(mean_score - target_ratio) > 0.2 * target_ratio:
            print(f"    ✗ {name}段 比值/基线 应在 {target_ratio} 附近，"
                  f"实测 {mean_score:.3f}")
            ok = False
        if name == "放松" and mean_relax <= 0.5:
            ok = False
        if name == "紧张" and mean_relax >= 0.5:
            ok = False

    print("  " + "-" * 40)
    print(f"  基线 α/β = {tracker.baseline:.3f}"
          if tracker.baseline else "  基线没标定出来")
    print(f"  结果：{'通过 ✓' if ok else '不通过 ✗'}")
    print("=" * 62)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_self_test())
