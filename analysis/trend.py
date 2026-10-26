"""跨会话趋势与风险提示。

为什么要有这个文件：赛题要的是「长周期健康管理」，单次训练报告只能证明
"这一次怎么样"，回答不了"这周比上周好还是差、要不要提醒复诊"。
这里把 sessions/*.jsonl 按会话聚合成一条时间线，用可解释的规则给出风险级别。

刻意不用机器学习：数据只有几十次训练时，任何模型都是过拟合。
规则写死、每条都能追溯到具体数字，答辩时才能讲清楚"为什么提示风险"。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"
MAX_SESSIONS = 12

# 阈值：全部是"相对个人基线"的变化量，不是人群常模。
# 依据是重复测量里同一患者两次训练的波动通常 <0.08（我们自己的复测数据），
# 取 0.12 / 0.08 作为"超过噪声"的门槛。
RELAX_DROP = 0.12
LI_RISE = 0.08
VALID_FLOOR = 0.70


@dataclass
class SessionStat:
    name: str
    started_at: float
    duration_s: float
    frames: int
    relax_mean: float
    relax_last: float
    focus_mean: float
    li_abs_mean: float
    valid_ratio: float | None
    synthetic: bool = False
    labels: dict = field(default_factory=dict)


def _load(path: Path) -> SessionStat | None:
    rows = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None
    rows = [r for r in rows if isinstance(r.get("relax"), (int, float))]
    if not rows:
        return None

    relax = [float(r["relax"]) for r in rows]
    focus = [float(r["focus"]) for r in rows if isinstance(r.get("focus"), (int, float))]
    li = [abs(float(r["li"])) for r in rows if isinstance(r.get("li"), (int, float))]
    n_good = [r.get("n_good") for r in rows]
    valid = [v for v in n_good if isinstance(v, (int, float))]
    labels: dict[str, int] = {}
    for r in rows:
        lab = r.get("label")
        if lab:
            labels[lab] = labels.get(lab, 0) + 1

    return SessionStat(
        name=path.stem,
        started_at=float(rows[0].get("t") or path.stat().st_mtime),
        duration_s=round(float(rows[-1].get("t", 0)) - float(rows[0].get("t", 0)), 1),
        frames=len(rows),
        relax_mean=round(sum(relax) / len(relax), 3),
        relax_last=round(relax[-1], 3),
        focus_mean=round(sum(focus) / len(focus), 3) if focus else 0.0,
        li_abs_mean=round(sum(li) / len(li), 3) if li else 0.0,
        valid_ratio=(round(sum(1 for v in valid if v > 0) / len(n_good), 3)
                     if n_good else None),
        # 合成板的 n_good 恒为 0（根本没有通道），不能算成"电极没贴好"。
        synthetic=bool(n_good) and all(v is None or v <= 0 for v in n_good),
        labels=labels,
    )


def session_stats(limit: int = MAX_SESSIONS) -> list[SessionStat]:
    files = sorted(SESSIONS_DIR.glob("session-*.jsonl"),
                   key=lambda p: p.stat().st_mtime)[-limit:]
    out = []
    for f in files:
        st = _load(f)
        if st:
            out.append(st)
    return out


def trend() -> dict:
    stats = session_stats()
    if not stats:
        return {"ok": True, "sessions": [], "risk": {
            "level": "unknown", "title": "还没有训练记录",
            "reasons": [], "advice": "先完成一次训练，这里会出现趋势。"}}

    base_pool = stats[:2] if len(stats) >= 2 else stats[:1]
    base_relax = sum(s.relax_mean for s in base_pool) / len(base_pool)
    base_li = sum(s.li_abs_mean for s in base_pool) / len(base_pool)
    recent = stats[-3:]
    now_relax = sum(s.relax_mean for s in recent) / len(recent)
    now_li = sum(s.li_abs_mean for s in recent) / len(recent)

    d_relax = round(now_relax - base_relax, 3)
    d_li = round(now_li - base_li, 3)

    reasons: list[str] = []
    if len(stats) >= 3 and d_relax <= -RELAX_DROP:
        reasons.append(f"放松度较基线下降 {abs(d_relax):.2f}（阈值 {RELAX_DROP}）")
    if len(stats) >= 3 and d_li >= LI_RISE:
        reasons.append(f"左右不对称 |LI| 较基线上升 {d_li:.2f}（阈值 {LI_RISE}）")
    low_valid = [s for s in recent
                 if not s.synthetic and s.valid_ratio is not None
                 and s.valid_ratio < VALID_FLOOR]
    if low_valid:
        reasons.append(f"近 {len(low_valid)} 次有效通道不足 {int(VALID_FLOOR * 100)}%，"
                       "先排除电极接触问题再看趋势")

    if any("有效通道" in r for r in reasons) and len(reasons) == 1:
        level, title = "quality", "先修数据质量"
        advice = "重新贴电极、把阻抗降到 5kΩ 以下，再练一次；这时的趋势不可信。"
    elif len(reasons) >= 2:
        level, title = "watch", "建议安排复诊"
        advice = "连续多次放松度下降且不对称加重，把这页趋势带给医生，别自行加练。"
    elif reasons:
        level, title = "mild", "轻度波动，继续观察"
        advice = "单指标变化可能只是当天状态；保持每周 3 次、固定时段训练。"
    else:
        level, title = "steady", "趋势平稳"
        advice = "按当前节奏继续，每周复盘一次即可。"

    n_demo = sum(1 for s in stats if s.synthetic)
    return {
        "ok": True,
        "demo_sessions": n_demo,
        "note": (f"其中 {n_demo} 次是合成板演示，只走流程，不参与风险判定"
                 if n_demo else ""),
        "baseline": {"relax": round(base_relax, 3), "li": round(base_li, 3),
                     "sessions": len(base_pool)},
        "recent": {"relax": round(now_relax, 3), "li": round(now_li, 3),
                   "sessions": len(recent)},
        "delta": {"relax": d_relax, "li": d_li},
        "risk": {"level": level, "title": title, "reasons": reasons, "advice": advice},
        "sessions": [
            {"name": s.name, "relax": s.relax_mean, "focus": s.focus_mean,
             "li": s.li_abs_mean, "valid": s.valid_ratio, "frames": s.frames,
             "duration_s": s.duration_s}
            for s in stats
        ],
    }


if __name__ == "__main__":
    print(json.dumps(trend(), ensure_ascii=False, indent=2))
