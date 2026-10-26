#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
α波(8-13Hz) / β波(13-30Hz) 功率比值 → 判定「放松 / 紧张」

用法一（推荐，自动从 MOABB 取公开数据）：
    python alpha_beta_state.py

用法二（读你自己的脑电文件，支持 .edf .fif .bdf .set .gdf 等）：
    python alpha_beta_state.py /path/to/your.eeg.edf

常用参数：
    --subject 1           MOABB 用第几号被试
    --runs 3              用几个 run（越小下载越快）
    --window 4            每个判定窗口多少秒
    --step 2              窗口滑动步长（秒）
    --threshold 1.0       α/β 大于多少算「放松」
    --baseline 30         用开头多少秒当「个人基线」（0 = 不校准）

为什么要「个人基线」：
    每个人的 α/β 绝对水平差很多（有人天生 α 就高）。
    直接用一个固定阈值，等于拿别人的尺子量自己。
    所以先测一段自己的静息数据当基准，之后都跟自己的基准比。

⚠️ 重要提醒（别自欺欺人）：
    这个脚本只做「机制演示」。MOABB 的公开数据大多是运动想象任务，
    **里面没有「放松/紧张」的真实标签**，所以它算出来的判断结果
    无法用来评估准确率。要验证准确率，只能用你自己采集、
    且带自评标签的数据。详见脚本末尾的输出说明。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

import numpy as np

# ------------------------------------------------------------------
# 数据统一放在项目目录，不要散落到系统目录
# ------------------------------------------------------------------
ROOT = "/Users/chujingyue/DeepWorks/NeuroBloom"
os.environ.setdefault("MNE_DATA", os.path.join(ROOT, ".data", "mne_data"))
os.makedirs(os.environ["MNE_DATA"], exist_ok=True)

import mne
from mne.time_frequency import psd_array_welch
from scipy.signal import detrend

mne.set_log_level("ERROR")

# 频段定义（Hz）
ALPHA_BAND = (8.0, 13.0)    # α 波：闭眼放松时最明显
BETA_BAND = (13.0, 30.0)    # β 波：警觉、紧张、思考时增强
FILTER_BAND = (1.0, 45.0)   # 先带通，去掉漂移和高频肌电

# ------------------------------------------------------------------
# 电极「信不信得过」的判据
# ------------------------------------------------------------------
# 这些数字不是拍脑袋来的，是在真设备录的数据上量出来的：
# 好通道的 std 大约 110~130 µV；一旦电极饱和，波形会被顶到满量程
# 变成一条直线。还有一类坏通道是「没接上」：波形恒为常数，std ≈ 0。
#
# ⚠️ 2026-10-21 干电极实测修正（旧阈值是按湿电极定的，用在本设备上必然全挂）：
#   干电极不打导电膏，天生带很大的极化直流偏置 —— 实测常态 -4000 µV 上下，
#   接触差的通道能到 -100000 µV 以上。把「直流偏置」当「饱和」是错的：
#   照旧判据（|mean| < 2000）判，16 路里只有 1 路合格，服务会永远报
#   「电极问题，只有 1 个通道合格」而画面根本不动。
#   真正的饱和是「顶到量程」，本设备实测 rail ≈ ±187500 µV。
#
# 所以判 std 之前一定先把直流和慢漂移去掉（detrend）：
#   一是让「信号死没死」跟电极偏置无关；
#   二是 BrainFlow 的 IIR 带通遇到大直流阶跃会进入长达数秒的巨型瞬态
#      （纯合成信号实测：带 -180000 µV 直流时输出 std 冲到 20315，
#      而去掉直流后精确回到 7.06），不先去掉会把好通道也判成坏通道。
#
# 为什么必须先把坏通道挑出去：
#   饱和通道的功率是好几千，好通道只有一百出头。
#   如果直接对所有通道求平均，一个饱和通道就能把结论整个带偏 ——
#   实测中，剔除坏通道能把 α/β 的波动从 65% 降到 47%。
FLAT_STD = 1.0          # µV：去直流后仍低于此 = 通道卡死 / 没接上
#   ↑ 原来是 3.0，对「去趋势后的数据」太严：真人安静坐着时，干净的通道
#     1-45Hz RMS 也就 2.5~4 µV，正好被 3.0 卡掉；而真「没接上」的通道
#     实测 std 精确为 0.0（ADS1299 输出恒定的 rail 值）。
#     实测四份真机记录，改 1.0 后「够 8 路合格」的窗口比例全部上升、无一倒退：
#     19%→85%、54%→71%、60%→84%、52%→65%。
NOISY_STD = 300.0       # µV：去直流后高于此 = 大伪迹（或没去干净的饱和残留）
SAT_MEAN = 100000.0     # µV：直流偏到这么大 = 电极顶到量程（rail 实测 ±187500）
MIN_GOOD_CHANNELS = 8   # 合格通道少于这个数，这一窗就不该判


# ==================================================================
# 结果容器
# ==================================================================
@dataclass
class WindowResult:
    """一个时间窗的分析结果"""
    run: int
    start: float
    end: float
    alpha: float
    beta: float
    ratio: float
    score: float      # 用于判定的数值（可能是相对基线的比值）
    state: str
    valid: bool = True    # False = 这一窗坏数据太多，不该下结论
    n_good: int = 0       # 这一窗有几个通道是合格的


def channel_quality(seg: np.ndarray) -> np.ndarray:
    """判断每一路电极是否「信得过」。seg 形状 (通道, 采样点)，单位必须是 µV。

    为什么需要这个：
        真设备上最坑的数据不是「没信号」，而是电极饱和 ——
        波形被顶到满量程、压成一条直线。这种通道自己看着挺平稳，
        但它的功率是正常通道的几十倍，混进平均后得出的 α/β 完全是假的。
        所以判定之前，先按「卡死 / 饱和 / 直流过偏」把坏通道挑出去。

    ⚠️ 干电极修正（2026-10-21）：判 std 之前先去直流和慢漂移。
        干电极天生的极化偏置（实测 -4000 µV 常态、坏电极 -180000 µV）
        不是饱和；不先去掉，16 路里只有 1 路合格，整条链路永远报「无效」。
        去完之后，「这一路信号死没死」才和电极偏置无关。
    """
    std = detrend(seg, axis=1).std(axis=1)   # 去直流+慢漂移：这才是「信号活没活」
    mean = seg.mean(axis=1)                  # 原始直流：只用来看有没有顶到量程
    return (std > FLAT_STD) & (std < NOISY_STD) & (np.abs(mean) < SAT_MEAN)


# ==================================================================
# 一、读数据
# ==================================================================
def load_from_file(path: str) -> list[mne.io.BaseRaw]:
    """读本地脑电文件。MNE 会按扩展名自动选解析器。"""
    if not os.path.exists(path):
        sys.exit(f"[错误] 文件不存在：{path}")
    raw = mne.io.read_raw(path, preload=True, verbose="ERROR")
    return [raw]


def load_from_moabb(subject: int, max_runs: int) -> list[mne.io.BaseRaw]:
    """
    从 MOABB 取公开数据。

    注意：zenodo.org 在很多校园网被 DNS 屏蔽（解析成 0.0.0.0），
    所以这里默认选 PhysionetMI —— 它的数据存在 physionet.org，能下。
    """
    import moabb
    moabb.set_log_level("warning")          # 屏蔽 MOABB 的啰嗦日志
    from moabb.datasets import PhysionetMI

    ds = PhysionetMI()
    print(f"[数据] MOABB / PhysionetMI / 被试 {subject}")
    print(f"[数据] 正在下载或读取本地缓存…（第一次会慢，之后走缓存）")

    data = ds.get_data(subjects=[subject])

    runs: list[mne.io.BaseRaw] = []
    for session in data[subject].values():
        runs.extend(session.values())
    return runs[:max_runs]


def prepare_raw(raw: mne.io.BaseRaw) -> mne.io.BaseRaw:
    """只保留脑电通道，并做基础滤波。"""
    try:
        raw.pick(picks="eeg", verbose="ERROR")
    except Exception:
        pass
    if len(raw.ch_names) == 0:
        sys.exit("[错误] 这份数据里没有找到脑电(EEG)通道")
    raw.filter(*FILTER_BAND, verbose="ERROR")
    return raw


def describe(raw: mne.io.BaseRaw) -> None:
    print(f"       通道数 {len(raw.ch_names)} | "
          f"采样率 {raw.info['sfreq']:.0f} Hz | "
          f"时长 {raw.times[-1]:.1f} 秒")


# ==================================================================
# 二、算功率：α、β、比值
# ==================================================================
def band_power(psds: np.ndarray, freqs: np.ndarray, band: tuple[float, float]) -> float:
    """
    psds: 形状 (通道数, 频率点)
    返回该频段的平均功率（对所有通道求平均）。

    为什么用「平均」而不是「求和」：
        求比值时分子分母同一个尺度，用平均更直观，
        也避免频率点越多数值越大的干扰。
    """
    lo, hi = band
    idx = (freqs >= lo) & (freqs < hi)
    if not idx.any():
        return float("nan")
    return float(psds[:, idx].mean())


def window_spectrum(seg: np.ndarray, sfreq: float) -> tuple[np.ndarray, np.ndarray]:
    """对一个时间窗做 Welch 功率谱估计。"""
    n = seg.shape[-1]
    # n_fft 决定「频率分辨率」：点越多，分得越细。
    # 固定用 256 是有问题的 —— 在 250Hz / 1000Hz 的板子上，
    # 256 点只有 1~4 Hz 的分辨率，α(8-13) 和 β(13-30) 会糊在一起。
    # 所以按采样率自适应：取不小于采样率 2 的幂次，保证分辨率优于 ~1Hz。
    target = 256
    while target < sfreq:
        target *= 2
    n_fft = int(min(target, n))
    if n_fft < 32:
        raise ValueError("窗口太短，无法做频谱估计")
    psds, freqs = psd_array_welch(
        seg, sfreq,
        fmin=FILTER_BAND[0], fmax=FILTER_BAND[1],
        n_fft=n_fft, n_overlap=n_fft // 2,
        verbose="ERROR",
    )
    return psds, freqs


# ==================================================================
# 三、逐窗口判定
# ==================================================================
def analyze(raw: mne.io.BaseRaw, run_idx: int,
            window: float, step: float,
            threshold: float, baseline_seconds: float):
    """在整段记录上滑动窗口，每个窗口给出 α/β 与判定。
    返回 (窗口结果列表, 个人基线比值或 None)。"""
    # 单位换算：原始数据是「伏特」，数值约 1e-6，直接算功率会小到打印不出来。
    # 换成「微伏(µV)」后，功率单位是 µV²/Hz，数值才是可读的常见量级。
    data = raw.get_data() * 1e6
    sfreq = float(raw.info["sfreq"])
    total = data.shape[-1] / sfreq

    win_n = int(window * sfreq)
    step_n = int(step * sfreq)
    if win_n <= 0 or win_n > data.shape[-1]:
        print(f"[跳过] run {run_idx} 太短（{total:.1f} 秒），不够一个窗口")
        return [], None

    results: list[WindowResult] = []
    for start_n in range(0, data.shape[-1] - win_n + 1, step_n):
        seg = data[:, start_n:start_n + win_n]

        # 先挑出信得过的通道，再算功率。这一步不能省 ——
        # 一个饱和通道的功率能顶掉十几个好通道。
        good = channel_quality(seg)
        n_good = int(good.sum())
        if n_good < MIN_GOOD_CHANNELS:
            results.append(WindowResult(
                run=run_idx,
                start=start_n / sfreq,
                end=(start_n + win_n) / sfreq,
                alpha=float("nan"), beta=float("nan"), ratio=float("nan"),
                score=float("nan"), state="无效",
                valid=False, n_good=n_good,
            ))
            continue
        seg = seg[good]

        try:
            psds, freqs = window_spectrum(seg, sfreq)
        except ValueError:
            continue

        a = band_power(psds, freqs, ALPHA_BAND)
        b = band_power(psds, freqs, BETA_BAND)
        if not np.isfinite(a) or not np.isfinite(b) or b <= 0:
            continue

        results.append(WindowResult(
            run=run_idx,
            start=start_n / sfreq,
            end=(start_n + win_n) / sfreq,
            alpha=a, beta=b, ratio=a / b,
            score=a / b,            # 稍后可能被基线换成相对值
            state="",
            valid=True, n_good=n_good,
        ))

    # ---- 个人基线校准 ----
    baseline_ratio = None
    if baseline_seconds > 0 and results:
        base = [r for r in results if r.valid and r.start < baseline_seconds]
        if base:
            baseline_ratio = float(np.mean([r.ratio for r in base]))
            for r in results:
                if r.valid:
                    r.score = r.ratio / baseline_ratio if baseline_ratio > 0 else r.ratio

    # ---- 判定 ----
    for r in results:
        if not r.valid:                 # 坏数据不下结论，也不放进投票
            continue
        r.state = "放松" if r.score >= threshold else "紧张"

    return results, baseline_ratio


# ==================================================================
# 四、打印
# ==================================================================
def print_table(results: list[WindowResult], max_rows: int = 20) -> None:
    print(f"  {'时间窗':<16}{'α功率':>11}{'β功率':>11}{'α/β':>9}   {'判定':<6}")
    print("  （功率单位：µV²/Hz）")
    print("  " + "-" * 58)

    show = results
    if len(results) > max_rows:               # 太长就抽样显示
        stride = len(results) // max_rows + 1
        show = results[::stride][:max_rows]

    for r in show:
        if not r.valid:
            print(f"  {r.start:6.1f} - {r.end:6.1f}s"
                  f"{'—':>11}{'—':>11}{'—':>9}   无效（只 {r.n_good} 通道合格）")
            continue
        print(f"  {r.start:6.1f} - {r.end:6.1f}s"
              f"{r.alpha:11.3f}{r.beta:11.3f}{r.ratio:9.3f}   {r.state:<6}")
    if len(results) > len(show):
        print(f"  …（共 {len(results)} 个窗口，这里抽样显示 {len(show)} 行）")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="用 α/β 功率比判断放松 / 紧张",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("file", nargs="?", default=None,
                    help="本地脑电文件路径；不填则自动从 MOABB 取公开数据")
    ap.add_argument("--subject", type=int, default=1, help="MOABB 被试编号（默认 1）")
    ap.add_argument("--runs", type=int, default=3, help="用几个 run（默认 3，越小下载越快）")
    ap.add_argument("--window", type=float, default=4.0, help="判定窗口秒数（默认 4）")
    ap.add_argument("--step", type=float, default=2.0, help="滑动步长秒数（默认 2）")
    ap.add_argument("--threshold", type=float, default=1.0,
                    help="α/β 达到多少算放松（默认 1.0）")
    ap.add_argument("--baseline", type=float, default=0.0,
                    help="开头多少秒作为个人基线（默认 0，不校准）")
    args = ap.parse_args()

    print("=" * 62)
    print("  α波(8-13Hz) / β波(13-30Hz) 比值 → 放松 / 紧张")
    print("=" * 62)

    # ---------- 1. 取数据 ----------
    if args.file:
        print(f"[数据] 本地文件：{args.file}")
        raws = load_from_file(args.file)
    else:
        raws = load_from_moabb(args.subject, args.runs)

    if not raws:
        sys.exit("[错误] 没有读到任何数据")

    print(f"[数据] 共 {len(raws)} 段记录")

    # ---------- 2. 逐段分析 ----------
    all_results: list[WindowResult] = []
    baseline_values: list[float] = []

    for i, raw in enumerate(raws, start=1):
        raw = prepare_raw(raw)
        print(f"\n--- 第 {i} 段 ---")
        describe(raw)
        res, base = analyze(raw, i, args.window, args.step,
                            args.threshold, args.baseline)
        if base is not None:
            baseline_values.append(base)
        all_results.extend(res)

    if not all_results:
        sys.exit("\n[结果] 没有产生任何有效的判定窗口，请检查数据长度或采样率")

    # ---------- 3. 汇总 ----------
    print()
    print("=" * 62)
    print("  判定明细")
    print("=" * 62)
    print_table(all_results)

    usable = [r for r in all_results if r.valid]
    ratios = np.array([r.ratio for r in usable])
    scores = np.array([r.score for r in usable])
    relaxed = sum(1 for r in usable if r.state == "放松")
    n = len(all_results)
    nu = len(usable)

    if baseline_values:
        print(f"\n  个人基线 α/β = {np.mean(baseline_values):.3f}"
              f"  （取自开头 {args.baseline:.0f} 秒）")
        print(f"  判定口径：相对基线 ≥ {args.threshold:.2f} → 放松")
    else:
        print(f"\n  判定口径：α/β ≥ {args.threshold:.2f} → 放松")

    print()
    print("=" * 62)
    print("  汇总")
    print("=" * 62)
    print(f"  窗口总数      ：{n}")
    print(f"  其中可用窗口  ：{nu}"
          f"   （另外 {n - nu} 窗因电极饱和/脱落被判为「无效」）")
    if nu:
        print(f"  α/β 平均      ：{ratios.mean():.3f}")
        print(f"  α/β 范围      ：{ratios.min():.3f} ~ {ratios.max():.3f}")
        print(f"  判为「放松」  ：{relaxed} / {nu}  ({relaxed / nu * 100:.1f}%)")
        print(f"  判为「紧张」  ：{nu - relaxed} / {nu}"
              f"  ({(nu - relaxed) / nu * 100:.1f}%)")

    overall = "放松" if (nu and scores.mean() >= args.threshold) else "紧张"
    print()
    if nu:
        print(f"  ▶ 整段结论：这段脑电整体判定为【{overall}】")
    else:
        print("  ▶ 整段结论：没有可用窗口，无法判定。")
        print("     （先查电极接触和 Cyton 增益，见 README 的排障部分）")

    # ---------- 4. 诚实说明 ----------
    print()
    print("=" * 62)
    print("  ⚠️  这个结果能说明什么、不能说明什么")
    print("=" * 62)
    print("  能说明：α/β 这条管线通了 —— 读数据、算功率、出判定。")
    print()
    print("  不能说明：判定「准不准」。原因有二：")
    print("    1. 公开数据里没有「放松/紧张」的真实标签，")
    print("       所以这里没有『正确答案』可以对答案。")
    print("    2. α/β 的绝对水平因人而异，固定阈值等于拿别人的尺子量自己。")
    print()
    print("  要真的谈准确率，必须：")
    print("    ① 用你自己的设备采数据；")
    print("    ② 采的时候做任务诱发（放松=呼吸引导，紧张=限时心算）；")
    print("    ③ 每个阶段让被试自评 1~10 分，当『标准答案』；")
    print("    ④ 再用 --baseline 做个人校准，然后算命中率。")
    print("=" * 62)


if __name__ == "__main__":
    main()
