"""左右半球不对称 + 五频带功率 —— 给康复观测用的两个新指标。

为什么要有这个模块（一句话版）：
    原来系统只会说「你现在放松/紧张」。但康复科真正关心的是
    「两边大脑是不是在慢慢变对称」—— 卒中之后常见一侧活动明显偏弱，
    训练有效时这个差距会缩小。所以除了放松度，还要能量出这个差距。

两个指标：
    1. 五频带功率 δ/θ/α/β/γ —— 给控制台画柱状图，让人看见「数据是真的」。
    2. 半球不对称指数 LI = (右 - 左) / (右 + 左)，范围 -1 ~ +1。
       LI 接近 0 = 两边差不多；偏正 = 右半球更强；偏负 = 左半球更强。
       用相对值而不是绝对值，是因为每个人的绝对功率差好几个数量级，
       只有比值才能跨人比较（这也是「一人一基线」的同一个道理）。

通道怎么分左右：
    16 路电极默认前 8 路贴左半球、后 8 路贴右半球（本工程采集盒的排布）。
    这个假设写在 HEMI_SPLIT 里，将来换帽子只改这一个常数。
    某一边合格通道太少时不硬算 —— 3 个以下的平均值没有意义。
"""
from __future__ import annotations

import numpy as np

from .alpha_beta_state import (
    ALPHA_BAND,
    MIN_GOOD_CHANNELS,
    band_power,
    channel_quality,
    window_spectrum,
)

# 五频带（Hz）。δ 放在 1~4 是因为带通下限就是 1Hz，再低全是漂移。
BANDS: dict[str, tuple[float, float]] = {
    "delta": (1.0, 4.0),
    "theta": (4.0, 8.0),
    "alpha": (8.0, 13.0),   # 与 ALPHA_BAND 保持一致
    "beta": (13.0, 30.0),   # 与 BETA_BAND 保持一致
    "gamma": (30.0, 45.0),
}

# 前 8 路算左半球、后 8 路算右半球
HEMI_SPLIT = 8

# 单侧至少要这么多条合格通道，才允许算不对称
MIN_SIDE_CHANNELS = 3

# 不对称指数超过这个值才算「明显偏侧」，用来给提示语分级
LI_NOTABLE = 0.15


def band_powers(psds: np.ndarray, freqs: np.ndarray) -> dict[str, float]:
    """一次频谱估计 → 五个频段的平均功率（µV²）。"""
    out: dict[str, float] = {}
    for name, band in BANDS.items():
        out[name] = band_power(psds, freqs, band)
    return out


def _li_from_psds(psds: np.ndarray, freqs: np.ndarray,
                  good_idx: np.ndarray, split: int,
                  band: tuple[float, float]) -> dict | None:
    """在已经算好的频谱上求左右差，避免重复做 Welch。"""
    left_sel = good_idx < split
    right_sel = good_idx >= split
    n_left = int(left_sel.sum())
    n_right = int(right_sel.sum())
    if n_left < MIN_SIDE_CHANNELS or n_right < MIN_SIDE_CHANNELS:
        return None

    left = band_power(psds[left_sel], freqs, band)
    right = band_power(psds[right_sel], freqs, band)
    total = left + right
    if not np.isfinite(total) or total <= 0:
        return None

    li = (right - left) / total
    return {
        "li": round(float(li), 3),                 # -1 ~ +1
        "imbalance": round(float(abs(li)), 3),     # 0 = 完全对称
        "left": round(float(left), 4),
        "right": round(float(right), 4),
        "n_left": n_left,
        "n_right": n_right,
        "notable": bool(abs(li) >= LI_NOTABLE),
        "band": f"{band[0]:.0f}-{band[1]:.0f}Hz",
    }


def lateralization(seg: np.ndarray, sfreq: float,
                   band: tuple[float, float] = ALPHA_BAND) -> dict | None:
    """单独求一次不对称指数（自检和离线分析用）。"""
    m = metrics_from_segment(seg, sfreq, band=band)
    return m["lr"] if m and m["ok"] else None


def metrics_from_segment(seg: np.ndarray, sfreq: float,
                         band: tuple[float, float] = ALPHA_BAND) -> dict:
    """一段数据 → 频带功率 + 不对称指数 + α/β（供判定层直接用）。

    seg 形状 (通道, 采样点)，单位 µV。
    数据不合格时 ok=False，其余字段能算多少给多少 —— 不抛异常，
    因为现场每 2 秒来一次，一次坏窗口不该让服务中断。
    """
    seg = np.asarray(seg, dtype=float)
    if seg.ndim != 2:
        return {"ok": False, "n_good": 0, "reason": "shape"}

    good = channel_quality(seg)
    n_good = int(good.sum())
    n_ch = int(seg.shape[0])
    if n_good < MIN_GOOD_CHANNELS:
        return {"ok": False, "n_good": n_good, "reason": "few_channels"}

    try:
        psds, freqs = window_spectrum(seg[good], sfreq)
    except ValueError:
        return {"ok": False, "n_good": n_good, "reason": "spectrum"}

    bands = band_powers(psds, freqs)
    alpha, beta = bands["alpha"], bands["beta"]
    if not np.isfinite(alpha) or not np.isfinite(beta) or beta <= 0:
        return {"ok": False, "n_good": n_good, "reason": "power",
                "bands": bands}

    split = HEMI_SPLIT if n_ch >= HEMI_SPLIT * 2 else n_ch // 2
    lr = _li_from_psds(psds, freqs, np.flatnonzero(good), split, band)

    return {
        "ok": True,
        "n_good": n_good,
        "bands": {k: round(float(v), 4) for k, v in bands.items()},
        "alpha": alpha,
        "beta": beta,
        "theta": bands["theta"],
        "lr": lr,
    }


# ------------------------------------------------------------------
# 自检：造一段「左半球 α 强、右半球 α 弱」的假数据，看 LI 符号对不对
# ------------------------------------------------------------------
def _synth_asym(seconds: float = 4.0, sfreq: float = 500.0,
                left_amp: float = 30.0, right_amp: float = 10.0,
                seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sfreq)) / sfreq
    seg = rng.normal(0.0, 5.0, size=(16, t.size))       # 底噪 5µV
    seg[:8] += left_amp * np.sin(2 * np.pi * 10.0 * t)   # 左边 α 强
    seg[8:] += right_amp * np.sin(2 * np.pi * 10.0 * t)  # 右边 α 弱
    return seg


def _self_test() -> int:
    sfreq = 500.0
    seg = _synth_asym(sfreq=sfreq)
    m = metrics_from_segment(seg, sfreq)
    assert m["ok"], m
    lr = m["lr"]
    assert lr is not None, "单侧通道足够却没算出不对称"
    # 左强右弱 → (右-左)/(右+左) 应为负
    assert lr["li"] < -0.2, f"符号错了: {lr}"
    assert lr["n_left"] == 8 and lr["n_right"] == 8, lr
    print(f"对称性自检通过: LI={lr['li']} 左={lr['left']} 右={lr['right']}")

    # 两边完全一样时 LI 应接近 0
    same = _synth_asym(sfreq=sfreq, left_amp=20.0, right_amp=20.0)
    lr2 = metrics_from_segment(same, sfreq)["lr"]
    assert abs(lr2["li"]) < 0.1, f"对称数据算出偏差: {lr2}"
    print(f"对称数据自检通过: LI={lr2['li']}")

    # 坏数据（全通道饱和）应判不合格而不是硬给数
    bad = np.full((16, int(4 * sfreq)), 3000.0)
    assert metrics_from_segment(bad, sfreq)["ok"] is False
    print("坏数据自检通过: ok=False")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
