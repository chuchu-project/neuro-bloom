"""临床口径的脑电指标（三个），全部不需要额外硬件。

为什么要有这个模块（一句话版）：
    原来的指标是「放松度、专注度、偏侧」——自己定义的，医生听不懂。
    这里补的三个是**文献和临床上真的在用**的量，用现有 16 通道帽子就能算：

    1. IAF（个体 α 峰值频率）
       静息闭眼时 α 功率最高的那个频率。人跟人能差 2~3 Hz（8.5 到 11.5 都常见），
       所以固定拿 8~13 Hz 当 α 带，对一部分人等于量错了东西。
       拿到 IAF 之后，α 带可以按人收窄（IAF±2 Hz），跨人比较才成立。
       文献口径：Klimesch 1999 综述、Babiloni 等关于静息 α 频率的工作。

    2. α 不对称对数比 ln(α_右) − ln(α_左)
       这是 Davidson 传统里 front alpha asymmetry(FA A) 的同一族指标：
       取 α 功率的对数差，用「对数」是因为功率分布右偏，取对数后更接近正态。
       ⚠️ 老实说清楚：文献里的 FAA 特指**前额**电极对(F3/F4、F7/F8)，
       我们目前按「前 8 路=左、后 8 路=右」分组（见 lateralization.HEMI_SPLIT），
       电极实际位置取决于帽子怎么戴。所以这里叫它 α 不对称对数比，
       与 FAA 同源、但不冒充 FAA；等电极位置标定后再换成前额对。
       方向解释也要小心：文献里正负号与情绪的关系并不完全一致，
       所以我们只输出**带符号的数值 + 跨会话变化**，不做「你是不是抑郁」的判断。

    3. 复杂度 / 熵（LZc、Higuchi FD、样本熵、排列熵、谱熵）
       LZc（Lempel-Ziv 复杂度）量的是信号里出现了多少「新模式」，
       麻醉、睡眠深度、意识水平下降时都会掉下来；卒中后认知障碍研究里
       也常拿它和熵类指标做组间比较。
       实现优先用开源库 antropy（BSD-3，https://github.com/raphaelvallat/antropy）；
       装不上时用内置等价实现，返回里用 engine 字段说明到底用了哪一套。

数据边界（重要）：
    这三个指标都需要**原始波形**才能算。项目的承诺是「不落盘原始波形」，
    所以计算全部在采集端的内存窗口里完成（4 秒窗口，算完即弃），
    落盘的仍然只有特征值。要复算请用 EEG_RECORD_DIR 单独录原始数据，
    不要指望从会话记录里反推。

成本控制（也重要）：
    IAF 与复杂度比频带功率贵，但不需要每个窗口都算（它们变化慢）。
    默认 IAF 每 5 秒算一次、复杂度每 10 秒算一次，其余窗口沿用上一次的值
    并在 age_seconds 里说明新鲜度。实测耗时见 __main__ 自检输出。
"""
from __future__ import annotations

import math
import time

import numpy as np

from .alpha_beta_state import window_spectrum

# IAF 搜索范围。比常规 α 带(8~13)留一点余量，覆盖慢 α 人群。
IAF_SEARCH = (6.0, 14.0)

# 文献里 α 带按 IAF 收窄时常用 ±2 Hz
IAF_BAND_HALF = 2.0

# 复杂度算多长的数据。用整个窗口（4 秒 @125Hz = 500 点）：
# 再短样本熵就不稳，再长没有额外信息。
try:  # 开源库优先：装了就用，没装就退回内置实现
    import antropy as _antropy
except Exception:  # noqa: BLE001
    _antropy = None


def _clean(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    return x[np.isfinite(x)]


def iaf_from_psd(psds: np.ndarray, freqs: np.ndarray) -> tuple[float, float] | None:
    """从 Welch 功率谱里找 α 峰。

    返回 (峰值频率, 峰值功率)，找不到返回 None。
    频率分辨率有限（4 秒窗约 0.25 Hz），所以对峰做三点抛物线插值，
    把精度提高到大约 0.05 Hz —— 这个精度对「按 IAF 收窄 α 带」够用了。
    """
    psds = np.atleast_2d(psds)
    p = psds.mean(axis=0)                     # 通道间平均谱
    lo, hi = IAF_SEARCH
    band = (freqs >= lo) & (freqs <= hi)
    if band.sum() < 3 or not np.isfinite(p[band]).any():
        return None
    idx_band = np.flatnonzero(band)
    k = int(idx_band[np.nanargmax(p[band])])
    if k <= 0 or k >= len(p) - 1:             # 峰落在边界，不可信
        return None
    y0, y1, y2 = float(p[k - 1]), float(p[k]), float(p[k + 1])
    denom = (y0 - 2 * y1 + y2)
    if not np.isfinite(denom) or abs(denom) < 1e-12:
        return float(freqs[k]), y1
    delta = 0.5 * (y0 - y2) / denom            # 抛物线顶点偏移（单位：频率点）
    df = float(freqs[1] - freqs[0])
    f_peak = float(freqs[k]) + delta * df
    f_peak = float(min(max(f_peak, freqs[k] - df), freqs[k] + df))
    peak = y1 - 0.25 * (y0 - y2) * delta
    return round(f_peak, 3), float(peak)


def alpha_asym_log(left_alpha: float | None,
                   right_alpha: float | None) -> float | None:
    """ln(右) − ln(左)。任一值为空/非正就返回 None（不硬算）。"""
    try:
        l, r = float(left_alpha), float(right_alpha)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(l) and math.isfinite(r)) or l <= 0 or r <= 0:
        return None
    return round(math.log(r) - math.log(l), 4)


def lziv_complexity(x: np.ndarray, normalize: bool = True) -> float | None:
    """Lempel-Ziv(76) 复杂度，中位数二值化后计算。

    优先调用 antropy 的实现（同一个函数名、同一个归一化口径），
    装不上时用内置的 LZ76 复现（Kaspar & Schuster 1987 的算法）。

    normalize=True 时按 c·log2(n)/n 归一化，大致落在 0~1：
    正弦 ≈ 0.2，白噪声 ≈ 1.0 —— 自检里就是这么验的。
    """
    x = _clean(x)
    n = x.size
    if n < 100:
        return None
    seq = (x > np.median(x)).astype(np.uint8)
    if _antropy is not None:
        try:
            c = float(_antropy.lziv_complexity(seq.astype(int), normalize=normalize))
            return round(c, 4)
        except Exception:  # noqa: BLE001
            pass
    c = _lz76(seq)
    if not normalize:
        return float(c)
    denom = n / math.log2(n) if n > 1 else 1.0
    return round(min(c / denom, 4.0), 4)


def _lz76(seq: np.ndarray) -> int:
    """LZ76 复杂度计数（不含归一化）。"""
    n = len(seq)
    i, k, l = 0, 1, 1
    c = 1
    while True:
        if seq[i + l - 1] == seq[k + l - 1]:
            l += 1
            if k + l > n:
                c += 1
                break
        else:
            i += 1
            if i == k:
                c += 1
                k += l
                if k > n:
                    break
                i, l = 0, 1
            else:
                l = 1
    return c


def higuchi_fd(x: np.ndarray, kmax: int = 10) -> float | None:
    """Higuchi 分形维数。越高越「不规则」，睡眠/意识水平下降时会变。"""
    x = _clean(x)
    n = x.size
    if n < 4 * kmax:
        return None
    if _antropy is not None:
        try:
            return round(float(_antropy.higuchi_fd(x, kmax=kmax)), 4)
        except Exception:  # noqa: BLE001
            pass
    lk = []
    for k in range(1, kmax + 1):
        lm = []
        for m in range(k):
            idx = np.arange(m, n, k)
            if idx.size < 2:
                continue
            length = np.abs(np.diff(x[idx])).sum()
            length *= (n - 1) / ((idx.size - 1) * k)
            lm.append(length)
        if lm:
            lk.append(np.mean(lm))
    if len(lk) < 2:
        return None
    ks = np.arange(1, len(lk) + 1)
    lk = np.asarray(lk, dtype=float)
    ok = lk > 0
    if ok.sum() < 2:
        return None
    slope = np.polyfit(np.log(1.0 / ks[ok]), np.log(lk[ok]), 1)[0]
    return round(float(slope), 4)


def sample_entropy(x: np.ndarray, m: int = 2, r_factor: float = 0.2) -> float | None:
    """样本熵（Richman & Moorman 2000）。规则度越高值越小。"""
    x = _clean(x)
    n = x.size
    if n < 100:
        return None
    if _antropy is not None:
        try:
            return round(float(_antropy.sample_entropy(x, order=m, r=r_factor * x.std())), 4)
        except Exception:  # noqa: BLE001
            pass                    # 库出错就退回内置实现，不能让服务崩
    r = r_factor * float(np.std(x))
    if r <= 0:
        return None

    def _count(mm: int) -> int:
        win = np.lib.stride_tricks.sliding_window_view(x, mm)[: n - mm]
        cnt = 0
        for row in win:
            d = np.max(np.abs(win - row), axis=1)
            cnt += int(np.sum(d <= r))
        return cnt

    b, a = _count(m), _count(m + 1)
    if b == 0 or a == 0:
        return None
    return round(-math.log(a / b), 4)


def perm_entropy(x: np.ndarray, order: int = 3, delay: int = 1) -> float | None:
    """排列熵（Bandt & Pompe 2002），归一化到 0~1。"""
    x = _clean(x)
    n = x.size - (order - 1) * delay
    if n < 50:
        return None
    if _antropy is not None:
        try:
            return round(float(_antropy.perm_entropy(x, order=order, delay=delay,
                                                     normalize=True)), 4)
        except Exception:  # noqa: BLE001
            pass
    idx = np.arange(order)
    win = np.stack([x[i * delay + idx] for i in range(n)])
    pat = np.argsort(win, axis=1, kind="stable")
    _, counts = np.unique(pat, axis=0, return_counts=True)
    p = counts / counts.sum()
    h = -np.sum(p * np.log(p))
    return round(float(h / math.log(math.factorial(order))), 4)


def spectral_entropy(psds: np.ndarray, freqs: np.ndarray) -> float | None:
    """谱熵：功率谱归一化后的 Shannon 熵 / log(N)，0~1，越接近 1 越「平」。"""
    psds = np.atleast_2d(psds)
    p = psds.mean(axis=0)
    p = p[np.isfinite(p) & (p > 0)]
    if p.size < 8:
        return None
    p = p / p.sum()
    return round(float(-np.sum(p * np.log(p)) / math.log(p.size)), 4)


class MarkerTracker:
    """跨窗口状态：缓存上一回的 IAF / 复杂度，按时间节流重算。

    和 RelaxTracker 一样，只建一次、跨重连保留 —— 这些量是「这个人的」。
    """

    def __init__(self, iaf_every: float = 5.0, complexity_every: float = 10.0):
        self.iaf_every = float(iaf_every)
        self.complexity_every = float(complexity_every)
        self._last_iaf_at = -1e9
        self._last_cx_at = -1e9
        self._cache: dict = {}

    def feed(self, seg: np.ndarray, sfreq: float, good: np.ndarray | None = None,
             lr: dict | None = None, now: float | None = None) -> dict:
        """一段窗口 → 指标字典。seg 形状 (通道, 采样点)，单位 µV。"""
        t0 = time.perf_counter()
        now = time.time() if now is None else now
        seg = np.asarray(seg, dtype=float)
        out: dict = {}
        if seg.ndim != 2:
            return {"ok": False, "reason": "shape"}

        # 取合格通道；没有掩码就用全部好数据
        if good is not None and np.asarray(good).dtype == bool:
            use = seg[np.asarray(good)]
            if use.size == 0:
                use = seg
        else:
            use = seg

        # ---- α 不对称：很便宜，每个窗口都算 ----
        if lr:
            out["alpha_asym_log"] = alpha_asym_log(lr.get("left"), lr.get("right"))

        # ---- IAF 与复杂度：按节流重算 ----
        psds = freqs = None
        need_spectrum = (now - self._last_iaf_at) >= self.iaf_every
        if need_spectrum:
            try:
                psds, freqs = window_spectrum(use, sfreq)
            except Exception:  # noqa: BLE001
                psds = freqs = None
            if psds is not None:
                peak = iaf_from_psd(psds, freqs)
                if peak:
                    self._cache["iaf"] = peak[0]
                    self._cache["iaf_peak_power"] = peak[1]
                    self._cache["iaf_band"] = [round(peak[0] - IAF_BAND_HALF, 2),
                                               round(peak[0] + IAF_BAND_HALF, 2)]
                self._last_iaf_at = now

        if (now - self._last_cx_at) >= self.complexity_every:
            # 复杂度用「通道平均后的一维序列」：多通道重复计算没有额外信息，
            # 而且通道平均本身能压掉一部分不相关噪声。
            avg = use.mean(axis=0)
            self._cache["lzc"] = lziv_complexity(avg)
            self._cache["higuchi_fd"] = higuchi_fd(avg)
            self._cache["sample_entropy"] = sample_entropy(avg)
            self._cache["perm_entropy"] = perm_entropy(avg)
            if psds is None:
                try:
                    psds, freqs = window_spectrum(use, sfreq)
                except Exception:  # noqa: BLE001
                    psds = None
            if psds is not None:
                self._cache["spectral_entropy"] = spectral_entropy(psds, freqs)
            self._last_cx_at = now

        for k, v in self._cache.items():
            out[k] = v
        out["iaf_age_s"] = round(max(0.0, now - self._last_iaf_at), 1) if self._last_iaf_at > 0 else None
        out["cx_age_s"] = round(max(0.0, now - self._last_cx_at), 1) if self._last_cx_at > 0 else None
        out["engine"] = "antropy" if _antropy is not None else "builtin"
        out["ok"] = True
        out["compute_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        return out


if __name__ == "__main__":
    # 自检：合成信号下，正弦的 α 峰应落在 10 Hz，白噪声的复杂度应明显更高。
    fs = 125.0
    t = np.arange(0, 4, 1 / fs)
    rng = np.random.RandomState(7)
    sine = 20 * np.sin(2 * np.pi * 10 * t) + rng.randn(t.size)
    white = 20 * rng.randn(t.size)
    seg_sine = np.stack([sine] * 16)
    seg_white = np.stack([white] * 16)

    m1 = MarkerTracker(iaf_every=0, complexity_every=0).feed(seg_sine, fs)
    m2 = MarkerTracker(iaf_every=0, complexity_every=0).feed(seg_white, fs)
    print("正弦:", {k: m1[k] for k in ("iaf", "iaf_band", "lzc", "higuchi_fd",
                                       "sample_entropy", "perm_entropy",
                                       "spectral_entropy", "engine", "compute_ms")})
    print("白噪:", {k: m2[k] for k in ("iaf", "lzc", "higuchi_fd",
                                       "sample_entropy", "perm_entropy",
                                       "spectral_entropy", "engine", "compute_ms")})
    assert 9.5 <= m1["iaf"] <= 10.5, f"IAF 应该认出 10Hz，实际 {m1['iaf']}"
    assert m1["lzc"] < m2["lzc"], "正弦的复杂度应低于白噪声"
    assert m1["lzc"] < 0.4 and m2["lzc"] > 0.8, (
        f"LZc 归一化不对：正弦 {m1['lzc']}、白噪 {m2['lzc']}，应约为 0.2 / 1.0")
    assert m1["spectral_entropy"] < m2["spectral_entropy"], "正弦谱熵应更低"
    print("自检通过：IAF 10Hz 识别正确，复杂/熵类指标方向正确")
