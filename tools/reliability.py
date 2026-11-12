#!/usr/bin/env python
"""会话重复性统计：同一个人隔天/隔次测，数字稳不稳。

用途
    方案赛最容易被问的一句是「你今天测 60，明天还测 60 吗」。
    这个脚本把 sessions/ 下的会话记录汇总成「被试 × 会话」矩阵，
    算 ICC(2,1)、Bland-Altman 一致性界限、以及跨会话漂移。

数据来源
    sessions/session-*.jsonl，每行一条状态（含 relax、bands、t）。
    被试归属按以下顺序判定：
      1) 行内 "pid" / "subject" 字段
      2) 文件名形如 session-s01-20261026-101500.jsonl 里的 s01
      3) sessions/subjects.json：{"session-2026....jsonl": "s01"}
      4) 都没有 → anon

用法
    .venv/bin/python tools/reliability.py
    .venv/bin/python tools/reliability.py --map sessions/subjects.json --json
    .venv/bin/python tools/reliability.py --min-samples 10

判读
    ICC(2,1) ≥ 0.75 良好；0.5–0.75 中等；< 0.5 说明两次测的不是同一个东西。
    Bland-Altman 的 LoA 越窄越好；理想是 bias ≈ 0 且 LoA 在 ±0.1 内（0–1 指数）。
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

SESSIONS = Path(__file__).resolve().parent.parent / "sessions"


# ── 读取 ────────────────────────────────────────────────────
@dataclass
class Session:
    path: Path
    subject: str
    stamp: str
    relax: list[float]
    log_ab: list[float]
    n: int

    @property
    def mean_relax(self) -> float:
        return sum(self.relax) / len(self.relax) if self.relax else float("nan")

    @property
    def mean_log_ab(self) -> float:
        return sum(self.log_ab) / len(self.log_ab) if self.log_ab else float("nan")


def _subject_of(path: Path, rows: list[dict], mapping: dict[str, str]) -> str:
    for row in rows:
        for key in ("pid", "subject", "subj"):
            val = row.get(key)
            if isinstance(val, str) and val and val != "anon":
                return val
    if path.name in mapping:
        return str(mapping[path.name])
    m = re.search(r"session-([a-z]{1,6}\d{0,4})-", path.name)
    if m:
        return m.group(1)
    return "anon"


def load_sessions(mapping: dict[str, str], min_samples: int) -> list[Session]:
    out: list[Session] = []
    for path in sorted(SESSIONS.glob("session-*.jsonl")):
        rows: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        relax, log_ab = [], []
        for row in rows:
            r = row.get("relax")
            if isinstance(r, (int, float)):
                relax.append(float(r))
            bands = row.get("bands") or {}
            a, b = bands.get("alpha"), bands.get("beta")
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a > 0 and b > 0:
                log_ab.append(math.log2(a / b))
        if len(relax) < min_samples:
            continue
        stamp = path.stem.replace("session-", "")
        out.append(Session(path, _subject_of(path, rows, mapping), stamp, relax, log_ab, len(relax)))
    return out


# ── 统计 ────────────────────────────────────────────────────
def icc_2_1(matrix: list[list[float]]) -> tuple[float, dict]:
    """两向随机效应、单一测量 ICC(2,1)。matrix: 被试 × 会话，必须等长。"""
    n = len(matrix)
    k = len(matrix[0]) if matrix else 0
    if n < 2 or k < 2:
        return float("nan"), {}
    grand = sum(sum(row) for row in matrix) / (n * k)
    row_means = [sum(row) / k for row in matrix]
    col_means = [sum(matrix[i][j] for i in range(n)) / n for j in range(k)]
    ss_rows = k * sum((m - grand) ** 2 for m in row_means)
    ss_cols = n * sum((m - grand) ** 2 for m in col_means)
    ss_err = 0.0
    for i in range(n):
        for j in range(k):
            ss_err += (matrix[i][j] - row_means[i] - col_means[j] + grand) ** 2
    msr = ss_rows / (n - 1)
    msc = ss_cols / (k - 1) if k > 1 else float("nan")
    mse = ss_err / ((n - 1) * (k - 1)) if (n - 1) * (k - 1) > 0 else float("nan")
    denom = msr + (k - 1) * mse + k * (msc - mse) / n
    val = (msr - mse) / denom if denom else float("nan")
    return val, {"msr": msr, "msc": msc, "mse": mse, "n": n, "k": k}


def bland_altman(pairs: list[tuple[float, float]]) -> dict:
    if len(pairs) < 2:
        return {}
    diffs = [a - b for a, b in pairs]
    means = [(a + b) / 2 for a, b in pairs]
    bias = sum(diffs) / len(diffs)
    sd = math.sqrt(sum((d - bias) ** 2 for d in diffs) / (len(diffs) - 1))
    return {
        "n": len(pairs),
        "bias": bias,
        "sd": sd,
        "loa_low": bias - 1.96 * sd,
        "loa_high": bias + 1.96 * sd,
        "mean_level": sum(means) / len(means),
    }


def slope(values: list[float]) -> float:
    """按会话序号做最小二乘斜率，看有没有系统性漂移。"""
    n = len(values)
    if n < 2:
        return float("nan")
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(values) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, values))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else float("nan")


# ── 主流程 ──────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="会话重复性：ICC + Bland-Altman")
    ap.add_argument("--map", type=Path, help="sessions/subjects.json：文件名 → 被试编号")
    ap.add_argument("--min-samples", type=int, default=10, help="单次会话至少多少条状态才算数")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    mapping: dict[str, str] = {}
    if args.map and args.map.exists():
        mapping = json.loads(args.map.read_text(encoding="utf-8"))

    sessions = load_sessions(mapping, args.min_samples)
    if not sessions:
        print(f"没找到可用的会话记录（{SESSIONS}/session-*.jsonl）。")
        print("先跑一次训练，或用 tools/state_probe.py 采一轮再回来。")
        return 1

    by_subject: dict[str, list[Session]] = {}
    for s in sessions:
        by_subject.setdefault(s.subject, []).append(s)
    for rows in by_subject.values():
        rows.sort(key=lambda s: s.stamp)

    report: dict = {"sessions": len(sessions), "subjects": {}, "icc": {}, "ba": {}}

    print(f"会话 {len(sessions)} 条，被试 {len(by_subject)} 个\n")
    print(f"{'被试':<8}{'会话':<6}{'样本':<7}{'α/β log2 均值':<16}{'指数均值':<10}{'漂移/次':<10}")
    print("-" * 60)
    for subject, rows in by_subject.items():
        logs = [r.mean_log_ab for r in rows]
        rels = [r.mean_relax for r in rows]
        drift = slope(logs)
        print(f"{subject:<8}{len(rows):<6}{sum(r.n for r in rows):<7}"
              f"{sum(logs)/len(logs):<16.3f}{sum(rels)/len(rels):<10.3f}{drift:<10.3f}")
        report["subjects"][subject] = {
            "sessions": len(rows),
            "samples": sum(r.n for r in rows),
            "mean_log_ab": logs,
            "mean_relax": rels,
            "drift": drift,
            "files": [r.path.name for r in rows],
        }

    print()
    # ICC：只在有 ≥2 次会话的被试上算，截取共同次数
    usable = {k: v for k, v in by_subject.items() if len(v) >= 2}
    if len(usable) >= 2:
        k = min(len(v) for v in usable.values())
        for metric, getter in (("relax", lambda s: s.mean_relax), ("log_ab", lambda s: s.mean_log_ab)):
            matrix = [[getter(s) for s in rows[:k]] for rows in usable.values()]
            val, detail = icc_2_1(matrix)
            report["icc"][metric] = {"value": val, "k": k, "n": len(matrix), **detail}
            verdict = "良好" if val >= 0.75 else ("中等" if val >= 0.5 else "偏低")
            print(f"ICC(2,1) · {metric:<7} = {val:6.3f}   （{len(matrix)} 人 × {k} 次，{verdict}）")
    else:
        print("ICC 需要至少 2 名被试、每人至少 2 次会话——现在不够，先补数据。")

    # Bland-Altman：相邻两次会话配对
    pairs_relax, pairs_log = [], []
    for rows in usable.values():
        for a, b in zip(rows, rows[1:]):
            pairs_relax.append((a.mean_relax, b.mean_relax))
            pairs_log.append((a.mean_log_ab, b.mean_log_ab))
    ba = bland_altman(pairs_relax)
    if ba:
        report["ba"] = ba
        print(f"\nBland-Altman（相邻会话，n={ba['n']}）："
              f"偏差 {ba['bias']:+.3f}，95% 一致性界限 "
              f"[{ba['loa_low']:+.3f}, {ba['loa_high']:+.3f}]")
        print("  解读：偏差接近 0 说明没有系统性高/低估；界限宽度决定「变化多少才算真的变了」。")

    print("\n提醒：以上只是重复性，不是效度。判据是否成立要看 tools/state_probe.py 的区分度结果。")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
