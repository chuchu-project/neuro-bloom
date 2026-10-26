#!/usr/bin/env python3
"""
引导式实验工具：把「基线 → 放松 → 紧张」这套带标签的流程跑起来，并给出阶段级报告。

为什么需要这个工具
------------------
单窗口的 α/β 抖动很大（在真实设备数据上实测噪声底 CV 还有 30%~60%），
所以「看一眼某个 4 秒窗口就下结论」是不可靠的。
但「安静放松 3 分钟」和「做限时心算 3 分钟」这两段，平均下来是有区别的
—— 这就叫「阶段级」比较。这个工具做的就是把流程固定下来：
    ① 先录一段基线（自己的尺子）
    ② 依次做几个阶段，每段结束后自己给一个 1~10 的主观评分
    ③ 最后把「程序的判断」和「你的自评」摆在一起对账，算出一致率

这样得到的数字才有意义：它有标签、有对照、有噪声平均。

三种用法
--------
1) 采集（要连设备或跟 GUI 的文件）：
       ./.venv/bin/python analysis/guided_experiment.py collect --source bdf --file <正在录的bdf>
       ./.venv/bin/python analysis/guided_experiment.py collect --source board
2) 对采集结果出报告：
       ./.venv/bin/python analysis/guided_experiment.py report --manifest .data/runs/run_xxx.json
3) 对已有的文件段出报告（用来做交叉验证，比如睁眼/闭眼两个已知状态的文件）：
       ./.venv/bin/python analysis/guided_experiment.py report --phases phases.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alpha_beta_state as ab        # noqa: E402  判定逻辑的唯一来源

RUN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", ".data", "runs")

# 阶段级统计至少要这么多秒才肯出结论（太短就等于单窗口）
MIN_PHASE_SECONDS = 30.0

# 两个阶段的中位数要差这么多（相对值），才承认它们「确实不一样」。
# 依据：真实设备实测的单窗口噪声底波动还有 30%~60%，
# 比这更小的差别只能算噪声，不能当结论。
MIN_EFFECT = 0.30

# 一个阶段至少要留下这么多个「有效窗口」才出判定
MIN_GOOD_WINDOWS = 10

# 报告用的统计判读阈值：某阶段的 α/β 中位数 ≥ 基线的这么多倍，才算「这一段放松了」。
# 注意它**不是**实时画面的判定 —— 画面怎么变由 analysis/relax_axis.py 统一决定，
# 这里只是离线统计「这一段整体有没有变」的提问方式（默认 1.0 = 没降就算放松）。
JUDGE_THRESHOLD = 1.0

# 一个阶段的窗口里坏数据占比超过这个数，就直接判「数据太差」。
# 依据：实测中饱和段只剩 4% 的可用窗口，那几窗算出来的中位数是假象。
MAX_INVALID_FRAC = 0.5

# 默认的主观评分问题
RATING_PROMPT = "  刚才这一段，你自己觉得「放松」程度打几分？（1~10，1=很紧张）"


# ==================================================================
# 实验协议
# ==================================================================
@dataclass
class PhaseSpec:
    key: str            # 内部代号
    name: str           # 显示名字
    seconds: float      # 持续多久
    instruction: str    # 开始前提示用户做什么
    expect: str | None  # "relax" / "tense" / None（None = 只描述，不判方向）
    ask_rating: bool = True


DEFAULT_PROTOCOL = [
    PhaseSpec("baseline", "基线（安静睁眼）", 60,
              "放松肩膀，睁开眼睛看着屏幕，什么都别想。\n"
              "  这 60 秒是你的「自己的尺子」，用来当后面所有比较的基准。",
              expect=None, ask_rating=False),
    PhaseSpec("relax", "闭眼深呼吸放松", 180,
              "闭上眼睛，用鼻子慢慢吸气 4 秒、嘴巴慢慢吐气 6 秒。\n"
              "  走神了就轻轻拉回来，不用较劲。",
              expect="relax"),
    PhaseSpec("tense", "限时心算（1000 减 7）", 180,
              "睁开眼睛。从 1000 开始，每次减 7，大声把结果念出来：\n"
              "  1000、993、986……　算错了也继续往下，我会计时，尽量算快、算对。",
              expect="tense"),
]


# ==================================================================
# 采集
# ==================================================================
class PhaseRecorder:
    """把每个窗口连同「它属于哪个阶段」一起写进 CSV。"""

    HEADER = ["阶段", "阶段内秒", "总秒", "α功率", "β功率", "α/β",
              "有效", "合格通道数", "判定"]

    def __init__(self, path: str):
        self.path = path
        self.n = 0
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.f = open(path, "w", newline="", encoding="utf-8-sig")
        self.w = csv.writer(self.f)
        self.w.writerow(self.HEADER)
        self.f.flush()

    def write(self, phase: str, in_phase: float, total: float, res: dict):
        if res["invalid"]:
            self.w.writerow([phase, f"{in_phase:.1f}", f"{total:.1f}",
                             "", "", "", "否", res["n_good"], "无效"])
        else:
            self.w.writerow([phase, f"{in_phase:.1f}", f"{total:.1f}",
                             f"{res['alpha']:.4f}", f"{res['beta']:.4f}",
                             f"{res['ratio']:.4f}", "是", res["n_good"],
                             res["state"]])
        self.n += 1
        self.f.flush()

    def close(self):
        self.f.close()


def cmd_collect(args) -> int:
    import live_state_gui as L

    protocol = _protocol_from_args(args)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.abspath(os.path.join(RUN_DIR, f"run_{stamp}"))
    os.makedirs(run_dir, exist_ok=True)
    csv_path = os.path.join(run_dir, "windows.csv")
    json_path = os.path.join(run_dir, "run.json")

    sfreq = args.sfreq
    ring = L.RingBuffer(16, int(max(args.window + 5, 12) * sfreq))
    src = None

    print("=" * 62)
    print("  引导式实验 · 采集")
    print("=" * 62)

    if args.source == "board":
        from brainflow.board_shim import BoardIds, IpProtocolTypes
        name, default_port = L.BOARD_CHOICES[args.board]
        board_id = getattr(BoardIds, name).value
        is_wifi = "wifi" in args.board
        port = args.port if args.port is not None else (default_port or 0)
        proto = (IpProtocolTypes.TCP.value if args.protocol == "tcp"
                 else IpProtocolTypes.UDP.value)
        if is_wifi and args.port is None:
            port = 1112
        sfreq = 250.0 if args.board == "synthetic" else sfreq
        ring = L.RingBuffer(16, int(max(args.window + 5, 12) * 1000))
        src = L.BoardSource(ring, board_id, args.ip if is_wifi else "",
                            port if is_wifi else 0, proto if is_wifi else 0)
    elif args.source == "bdf":
        src = L.BdfTailSource(ring, args.file)
    else:
        src = L.CsvTailSource(ring, args.file, sfreq, scale=args.scale)
    src.start()
    src.ready.wait(timeout=args.wait)
    if src.error:
        sys.exit("\n[错误] " + src.error)
    if not src.ready.is_set():
        sys.exit(f"\n[错误] {args.wait:.0f} 秒内没读到数据。")
    sfreq = getattr(src, "sfreq", None) or sfreq
    n_ch = getattr(src, "n_ch", None) or ring.channels
    ring.resize(n_ch, int(max(args.window + 5, 12) * sfreq))
    print(f"[数据源] 已就绪：{sfreq:.0f} Hz，{n_ch} 个脑电通道")

    # 判定统一走 relax_axis（L.LiveJudge 现在是 RelaxTracker 的适配层）。
    # 基线长度直接用协议第一段（「基线 安静睁眼」60 秒）的时长 ——
    # 这一段本来就是设计出来当"你自己的尺子"的，正好当个人基线用。
    # 以前这里传的是「绝对阈值 + 基线 0 秒」，即 α/β 大于 1.0 就算放松：
    # α/β 因人而异（实测同一人能差 3 倍），那个判据对多数人恒为真或恒为假。
    judge = L.LiveJudge(args.window, baseline_seconds=protocol[0].seconds,
                        smooth=args.smooth, step=args.step)
    print(f"[判定] 统一 relax_axis · 基线取「{protocol[0].name}」"
          f"{protocol[0].seconds:.0f} 秒 · 窗口 {args.window:.0f}s/"
          f"步长 {args.step:.0f}s · 平滑 {args.smooth} 窗")
    rec = PhaseRecorder(csv_path)

    presets = ([float(x) for x in args.rating.split(",")]
               if getattr(args, "rating", None) else None)
    ri = 0
    ratings: dict[str, float | None] = {}
    t_start = time.time()
    try:
        for spec in protocol:
            print()
            print("-" * 62)
            print(f"  【{spec.name}】　{spec.seconds:.0f} 秒")
            print("-" * 62)
            print(spec.instruction)
            print()
            if not args.fast:
                for k in (3, 2, 1):
                    print(f"  {k} 秒后开始…", flush=True)
                    time.sleep(1.0)
            print("  >>> 开始！", flush=True)

            t0 = time.time()
            last = 0.0
            while True:
                in_phase = time.time() - t0
                if in_phase >= spec.seconds:
                    break
                if src.error:
                    print("[错误]", src.error)
                    break
                res = judge.evaluate(ring, sfreq, time.time() - t_start)
                if res is not None:
                    rec.write(spec.key, in_phase, time.time() - t_start, res)
                    # 每 5 秒在终端回显一次，让用户看得见程序在工作
                    if in_phase - last >= 5.0:
                        last = in_phase
                        if res["invalid"]:
                            msg = f"信号无效（仅 {res['n_good']} 通道合格）"
                        else:
                            msg = (f"α/β = {res['ratio']:5.2f}"
                                   f"　{res['state']}")
                        left = spec.seconds - in_phase
                        print(f"    还剩 {left:5.0f}s　{msg}", flush=True)
                time.sleep(0.5)

            print(f"  <<< 本段结束", flush=True)
            if spec.ask_rating:
                if presets is not None:
                    ratings[spec.key] = presets[ri] if ri < len(presets) else None
                    ri += 1
                    print(f"  [自评（预设）] {ratings[spec.key]}")
                else:
                    ratings[spec.key] = _ask_rating()
    except KeyboardInterrupt:
        print("\n[中断] 用户按了 Ctrl-C，已保存到目前的数据。")
    finally:
        src.stop()
        rec.close()

    manifest = {
        "kind": "guided_run",
        "created": datetime.now().isoformat(timespec="seconds"),
        "source": args.source,
        "file": args.file,
        "sfreq": sfreq,
        "n_ch": n_ch,
        "window": args.window,
        "step": args.step,
        "threshold": JUDGE_THRESHOLD,      # 报告侧的统计判读口径
        "smooth": args.smooth,
        "windows_csv": os.path.basename(csv_path),
        "phases": [
            {"key": p.key, "name": p.name, "seconds": p.seconds,
             "expect": p.expect, "rating": ratings.get(p.key)}
            for p in protocol
        ],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print()
    print(f"[记录] 窗口明细：{csv_path}（{rec.n} 行）")
    print(f"[记录] 实验清单：{json_path}")
    print()
    print("下一步：")
    print(f"  ./.venv/bin/python analysis/guided_experiment.py report --manifest {json_path}")
    return 0


def _ask_rating() -> float | None:
    """问用户这一段的主观放松度（1~10）。回车跳过。"""
    try:
        while True:
            print(RATING_PROMPT)
            s = input("  > ").strip()
            if not s:
                return None
            try:
                v = float(s)
            except ValueError:
                print("  请输入 1~10 的数字（直接回车跳过）。")
                continue
            if 1 <= v <= 10:
                return v
            print("  请输 1~10 之间的数。")
    except (EOFError, KeyboardInterrupt):
        return None


# ==================================================================
# 报告
# ==================================================================
@dataclass
class PhaseData:
    key: str
    name: str
    ratios: list[float] = field(default_factory=list)
    n_windows: int = 0
    n_invalid: int = 0
    seconds: float = 0.0
    expect: str | None = None
    rating: float | None = None

    @property
    def invalid_frac(self) -> float:
        return self.n_invalid / self.n_windows if self.n_windows else 1.0

    def median(self) -> float:
        return float(np.median(self.ratios)) if self.ratios else float("nan")


def _rank(x: np.ndarray) -> np.ndarray:
    """手写排名，避免为了一个相关系数去依赖 scipy。"""
    order = x.argsort()
    r = np.empty(len(x), dtype=float)
    r[order] = np.arange(len(x), dtype=float)
    return r


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2:
        return float("nan")
    return _pearson(_rank(a), _rank(b))


def _phase_stats_from_rows(rows) -> list[PhaseData]:
    """从采集出来的 CSV 行算阶段统计。"""
    order: list[str] = []
    by_key: dict[str, PhaseData] = {}
    for row in rows:
        key = row["阶段"]
        if key not in by_key:
            order.append(key)
            by_key[key] = PhaseData(key=key, name=key)
        p = by_key[key]
        p.n_windows += 1
        try:
            in_phase = float(row["阶段内秒"])
            p.seconds = max(p.seconds, in_phase)
        except (ValueError, KeyError):
            pass
        if row["有效"] == "是":
            try:
                p.ratios.append(float(row["α/β"]))
            except ValueError:
                pass
        else:
            p.n_invalid += 1
    return [by_key[k] for k in order]


def _phase_stats_from_files(phases_spec: list[dict]) -> list[PhaseData]:
    """从「文件 + 区间」直接算阶段统计（用于交叉验证已知状态的数据）。"""
    out: list[PhaseData] = []
    for spec in phases_spec:
        raw = ab.prepare_raw(ab.load_from_file(spec["file"])[0])
        t0 = spec.get("start", 0.0)
        t1 = spec.get("end")
        if t1 is not None:
            raw = raw.copy().crop(tmin=t0, tmax=t1)
        # 这里的 analyze() 只用来**切窗口 + 取 α/β**（下面只读 r.ratio/r.valid）。
        # 它的判定字段（r.state/r.score）一概不用 —— 实时判定统一在 relax_axis，
        # 报告里的"放松/紧张"是另一回事：阶段级的统计判读（中位数 ≥ 基线 X 倍）。
        res, _ = ab.analyze(raw, 1, spec.get("window", 4.0),
                            spec.get("step", 2.0), 1.0, 0.0)
        p = PhaseData(key=spec.get("key", spec["name"]),
                      name=spec["name"],
                      expect=spec.get("expect"),
                      rating=spec.get("rating"),
                      seconds=(t1 - t0) if t1 is not None else raw.times[-1])
        p.n_windows = len(res)
        p.ratios = [r.ratio for r in res if r.valid]
        p.n_invalid = sum(1 for r in res if not r.valid)
        out.append(p)
    return out


def _print_report(phases: list[PhaseData], threshold: float,
                  baseline_key: str | None, min_effect: float = MIN_EFFECT) -> None:
    if not phases:
        print("没有可用的阶段数据。")
        return

    base = next((p for p in phases if p.key == baseline_key), None)
    if base is None or not base.ratios:
        base = phases[0]
    baseline_ratio = np.median(base.ratios) if base.ratios else float("nan")

    print()
    print("=" * 74)
    print("  阶段级报告")
    print("=" * 74)
    print(f"  基线取自「{base.name}」，其 α/β 中位数 = {baseline_ratio:.3f}")
    print(f"  统计判读口径：某阶段 α/β 中位数 ≥ 基线的 {threshold:.2f} 倍 → 放松")
    print("    （这是离线统计，回答「这一段整体有没有变」；实时画面怎么变"
          "由 analysis/relax_axis.py 统一决定，两者不是同一件事）")
    print()
    print(f"  {'阶段':<18}{'段长':>7}{'可用窗':>8}{'无效窗':>8}"
          f"{'α/β中位':>10}{'相对基线':>10}{'判定':>8}{'自评':>6}")
    print("  " + "-" * 70)

    for p in phases:
        rel = p.median() / baseline_ratio if baseline_ratio else float("nan")
        if p.key == base.key:
            verdict = "基线"       # 基线自己就是尺子，不该被「判」
        elif p.seconds < MIN_PHASE_SECONDS or len(p.ratios) < MIN_GOOD_WINDOWS:
            verdict = "样本不足"
        elif p.invalid_frac > MAX_INVALID_FRAC:
            # 大部分窗口都是坏数据时，剩下那几窗的「中位数」没有意义 ——
            # 实测里饱和段就出现过「只剩 7 窗、却算出偏放松」的假象。
            verdict = "数据太差"
        else:
            verdict = "放松" if rel >= threshold else "紧张"
        rating = f"{p.rating:.0f}" if p.rating is not None else "—"
        print(f"  {p.name:<18}{p.seconds:>6.0f}s{p.n_windows:>8}{p.n_invalid:>8}"
              f"{p.median():>10.3f}{rel:>10.3f}{verdict:>8}{rating:>6}")

    # ---- 阶段间方向是否说得通 ----
    # 关键：光看「谁大谁小」是不够的。真实设备上单窗口的噪声底 CV 还有
    # 30%~60%，所以两个阶段哪怕差 5%，也完全是噪声，不能拿来当结论。
    # 必须要求差别超过噪声底，才承认「这两个阶段确实不一样」。
    print()
    print("  【方向检查】阶段两两比较（比的是「相对基线」的大小）")
    print("  " + "-" * 70)
    print(f"  判据：两阶段的 α/β 中位数要差 {min_effect * 100:.0f}% 以上才认。")
    print("        （依据：真实设备实测的噪声底波动还有 30%~60%，"
          "更小的差别不能当结论）")
    decisive = 0
    ok = 0
    weak = 0
    for i in range(len(phases)):
        for j in range(i + 1, len(phases)):
            a, b = phases[i], phases[j]
            if a.expect is None or b.expect is None:
                continue
            if a.expect == b.expect or not a.ratios or not b.ratios:
                continue
            if a.seconds < MIN_PHASE_SECONDS or b.seconds < MIN_PHASE_SECONDS:
                continue
            relax, tense = (a, b) if a.expect == "relax" else (b, a)
            eff = relax.median() / tense.median() - 1.0
            print(f"  {relax.name} ({relax.median():.2f})  vs  "
                  f"{tense.name} ({tense.median():.2f})")
            if abs(eff) < min_effect:
                weak += 1
                print(f"      → 只差 {eff * 100:+.0f}%，在噪声底之内，"
                      f"**不下结论**")
                continue
            decisive += 1
            better = eff > 0
            ok += int(better)
            print(f"      → 放松阶段高出 {eff * 100:+.0f}%　"
                  f"{'✔ 符合预期' if better else '✘ 与预期相反'}")
    if decisive:
        print(f"  明确可比的比较：{decisive} 组，方向符合预期 {ok}/{decisive}")
    if weak:
        print(f"  另有 {weak} 组差异过小，未计入（这是诚实，不是失败）。")
    if not decisive and not weak:
        print("  没有可用于比较的阶段（需要至少两个方向相反、且够长的阶段）。")

    # ---- 与自评对账 ----
    rated = [p for p in phases if p.rating is not None and p.ratios]
    if len(rated) >= 3:
        r = np.array([p.rating for p in rated], dtype=float)
        rel = np.array([p.median() / baseline_ratio for p in rated])
        print()
        print("  【与自评对账】")
        print("  " + "-" * 70)
        print(f"  {'阶段':<18}{'自评(放松度)':>14}{'程序(相对基线)':>16}")
        for p, rr in zip(rated, rel):
            print(f"  {p.name:<18}{p.rating:>14.0f}{rr:>16.3f}")
        print()
        print(f"  相关系数（自评 vs 程序）：Pearson {_pearson(rel, r):+.2f}　"
              f"Spearman {_spearman(rel, r):+.2f}")
        print("  注意：阶段数只有个位数时，相关系数非常不稳定，")
        print("        看看方向就行，别把具体数值当结论。")
    elif len(rated) >= 2:
        print()
        print("  【与自评对账】")
        print("  " + "-" * 70)
        for p in rated:
            print(f"  {p.name:<18}自评 {p.rating:.0f}　"
                  f"程序相对基线 {p.median() / baseline_ratio:.3f}")
        print("  只有 2 个阶段有自评 → 不算相关系数（两点必然连成一条线，")
        print("  算出来永远是 ±1.00，那是假象）。看方向就够。")

    # ---- 诚实说明 ----
    print()
    print("=" * 74)
    print("  这个报告能说明什么、不能说明什么")
    print("=" * 74)
    print("  能说明：管线通了，而且是「阶段级」比较 —— 有基线、有对照、")
    print("          有噪声平均。这比单窗口判定靠谱得多。")
    if decisive:
        print(f"  能说明：明确可比的 {decisive} 组里，{ok}/{decisive} 组的")
        print("          方向符合预期 —— α/β 确实抓到了一部分区别。")
    elif weak:
        print("  能说明：这次各阶段之间没有出现超过噪声底的差别。")
        print("          这不代表方法错，更可能是：电极没贴好 / 阶段太短 /")
        print("          这次的心算没真的紧张起来。先修数据质量。")
    print()
    print("  不能说明：准确率。因为：")
    print("    1. 自评（1~10）是一个很粗的尺子，本身就有主观漂移；")
    print("    2. 阶段只有个位数，样本太少；")
    print("    3. α/β 的绝对水平因人而异 —— 所以这里一律用「相对自己基线」。")
    print("  想谈准确率，需要：多个人、每人重复测、把「放松/紧张」标签")
    print("  用任务（而不是自评）来定义。这是下一个阶段的事。")


def cmd_report(args) -> int:
    if args.manifest:
        with open(args.manifest, encoding="utf-8") as f:
            man = json.load(f)
        csv_path = os.path.join(os.path.dirname(os.path.abspath(args.manifest)),
                                man["windows_csv"])
        with open(csv_path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        phases = _phase_stats_from_rows(rows)
        name_map = {p["key"]: p for p in man.get("phases", [])}
        for p in phases:
            m = name_map.get(p.key)
            if m:
                p.name = m["name"]
                p.expect = m.get("expect")
                p.rating = m.get("rating")
                p.seconds = p.seconds or m.get("seconds", 0.0)
        threshold = man.get("threshold", args.threshold)
        baseline_key = man.get("phases", [{}])[0].get("key") if man.get("phases") else None
        print(f"[来源] 引导式实验 {man.get('created', '')}　"
              f"数据源 {man.get('source')}")
    elif args.phases:
        with open(args.phases, encoding="utf-8") as f:
            cfg = json.load(f)
        phases = _phase_stats_from_files(cfg["phases"])
        threshold = cfg.get("threshold", args.threshold)
        baseline_key = cfg.get("baseline", cfg["phases"][0].get("key"))
        print(f"[来源] 阶段配置 {os.path.basename(args.phases)}")
    else:
        sys.exit("请用 --manifest 或 --phases 指定要出报告的东西。")

    _print_report(phases, threshold, baseline_key, args.min_effect)
    return 0


def _protocol_from_args(args) -> list[PhaseSpec]:
    """允许用 --seconds 覆盖三个阶段的时长（调试用）。"""
    proto = DEFAULT_PROTOCOL
    if getattr(args, "seconds", None):
        parts = [float(x) for x in args.seconds.split(",")]
        if len(parts) != len(proto):
            sys.exit(f"--seconds 需要 {len(proto)} 个逗号分隔的值")
        proto = [PhaseSpec(p.key, p.name, parts[i], p.instruction,
                           p.expect, p.ask_rating)
                 for i, p in enumerate(proto)]
        if getattr(args, "_phase_order", None) is None:
            args._phase_order = [p.key for p in proto]
    return proto


def main() -> None:
    ap = argparse.ArgumentParser(
        description="引导式实验工具：基线 → 放松 → 紧张，出阶段级报告")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="按引导跑一遍实验并记录")
    c.add_argument("--source", choices=["board", "csv", "bdf"], default="bdf")
    c.add_argument("--file", help="csv / bdf 模式下要跟随的文件")
    c.add_argument("--board", default="synthetic",
                   choices=["synthetic", "cyton_wifi", "daisy_wifi",
                            "ganglion_wifi", "cyton", "daisy"],
                   help="设备型号（--source board 时用）")
    c.add_argument("--ip", default="192.168.4.1", help="WiFi 板子 IP")
    c.add_argument("--port", type=int, default=None, help="端口（默认按型号）")
    c.add_argument("--protocol", choices=["tcp", "udp"], default="tcp")
    c.add_argument("--sfreq", type=float, default=250.0)
    c.add_argument("--scale", type=float, default=1.0)
    c.add_argument("--window", type=float, default=4.0)
    c.add_argument("--step", type=float, default=2.0)
    c.add_argument("--smooth", type=int, default=3)
    # 这里刻意不再有 --threshold：采集时的实时判定已统一走 relax_axis，
    # 阈值只剩下「报告怎么读这段数据」的含义，所以它只出现在 report 子命令上。
    c.add_argument("--wait", type=float, default=60.0)
    c.add_argument("--seconds", help="覆盖阶段时长，如 10,20,20（调试用）")
    c.add_argument("--rating", help="非交互：按阶段给自评，如 4,7,8")
    c.add_argument("--fast", action="store_true",
                   help="跳过倒计时提示音式等待（调试用）")

    r = sub.add_parser("report", help="出阶段级报告")
    r.add_argument("--manifest", help="collect 产出的 run.json")
    r.add_argument("--phases", help="阶段配置 json（直接读文件/区间）")
    r.add_argument("--threshold", type=float, default=JUDGE_THRESHOLD,
                   help="报告里的统计判读口径（不影响采集时的实时判定）")
    r.add_argument("--min-effect", type=float, default=MIN_EFFECT,
                   help="两阶段差异小于此比例就不下结论（默认 0.30）")

    args = ap.parse_args()
    args._phase_order = [p.key for p in DEFAULT_PROTOCOL]
    if args.cmd == "collect":
        sys.exit(cmd_collect(args))
    sys.exit(cmd_report(args))


if __name__ == "__main__":
    main()
