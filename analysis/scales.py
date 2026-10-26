"""卒中后情绪随访量表（PHQ-2 / PHQ-9 / GAD-7）与风险分层。

为什么要有这个模块（一句话版）：
    脑电是客观的，但它不是诊断工具。临床上看「卒中后抑郁」靠的是量表
    （PHQ-9 这类），而指南要求出院后**常规筛查**——现实里这一步经常漏掉。
    我们的做法：把这个流程电子化，并让量表结果与脑电趋势放在同一张随访单上：
    量表提供临床锚点，脑电提供每周都在变、且不需要患者自述的客观曲线。

临床依据（都是公开、可核查的一手来源）：
    · PHQ-9：Kroenke, Spitzer & Williams, 2001, J Gen Intern Med 16(9):606-613
      （PMID 11556941）。9 题、0~3 计分、总分 0~27；常用切点 5/10/15/20。
    · PHQ-2：Kroenke, Spitzer & Williams, 2003, Med Care 41(11):1284-1292
      （PMID 14583691）。取 PHQ-9 的前两题，总分 ≥3 为阳性，用于快速初筛。
    · GAD-7：Spitzer et al., 2006, Arch Intern Med 166(10):1092-1097
      （PMID 16717171）。7 题、0~21；切点 5/10/15。
    · 卒中后筛查是常规动作：AHA/ASA 科学声明（Towfighi et al., 2017,
      Stroke 48(2):e30-e43, PMID 27932603）建议对卒中后患者常规筛查抑郁。
    （英国 NICE CG91 我们也想引，但抓取时返回 403，未读到正文，所以不引。）

版权说明（避免踩坑）：
    PHQ 系列与 GAD-7 由原作者免费提供，可自由复制、翻译、用于临床与科研，
    无需许可费，因此内置题库是合规的。
    HADS 有版权（需向出版商购买授权），所以**不内置**；如果合作医院已经在用
    HADS，我们只在记录里留一个「外部量表得分」字段，不复制题目。

使用边界（写在代码里，省得以后忘）：
    · 本模块只做**筛查分层提示**，不做诊断，也不替代医生判断。
    · 卒中人群的躯体症状条目（疲惫、睡眠、食欲）可能与卒中本身重叠，
      单项高不一定代表抑郁 —— 报告里会明确提示这一点。
    · PHQ-9 第 9 题（自伤念头）只要不为 0，立即按最高风险处理并提示联系医生。
"""
from __future__ import annotations

# 选项文字与计分（PHQ 系列和 GAD-7 用的是同一套 0~3 频率选项）
OPTIONS = [
    ("完全不会", 0),
    ("好几天", 1),
    ("一半以上的天数", 2),
    ("几乎每天", 3),
]

PHQ2_ITEMS = [
    "做事时提不起劲或没有兴趣",
    "感到心情低落、沮丧或绝望",
]

PHQ9_ITEMS = [
    "做事时提不起劲或没有兴趣",
    "感到心情低落、沮丧或绝望",
    "入睡困难、睡不安稳或睡眠过多",
    "感觉疲倦或没有活力",
    "食欲不振或吃太多",
    "觉得自己很糟，或觉得自己很失败，或让自己和家人失望",
    "对事物专注有困难，例如阅读或看电视时",
    "动作或说话速度缓慢到别人已经察觉；或正好相反，烦躁、坐立不安、动来动去",
    "有不如死掉或用某种方式伤害自己的念头",
]

GAD7_ITEMS = [
    "感到紧张、焦虑或急切",
    "不能够停止或控制担忧",
    "对各种各样的事情担忧过多",
    "很难放松下来",
    "由于不安而无法静坐",
    "变得容易烦恼或急躁",
    "感到似乎将有可怕的事情发生而害怕",
]

# 躯体症状条目在 PHQ-9 里的下标（0 基）：睡眠、疲惫、食欲
SOMATIC_IDX = (2, 3, 4)

# 自伤念头条目下标
SI_IDX = 8

# 分级用切点（各来源的常用值）
PHQ2_POSITIVE = 3
PHQ9_CUTS = (5, 10, 15, 20)
GAD7_CUTS = (5, 10, 15)


def _score(values: list[int] | None) -> int | None:
    """把一串 0~3 的作答求和；含非法值时返回 None（不猜）。"""
    if values is None:
        return None
    if any(v not in (0, 1, 2, 3) for v in values):
        return None
    return int(sum(values))


def score_phq2(values: list[int]) -> dict:
    total = _score(values)
    if total is None:
        return {"ok": False, "reason": "作答需要 2 个 0~3 的整数"}
    return {"ok": True, "scale": "PHQ-2", "total": total, "max": 6,
            "positive": total >= PHQ2_POSITIVE,
            "cut": PHQ2_POSITIVE,
            "note": "阳性（≥3）建议继续做 PHQ-9" if total >= PHQ2_POSITIVE
                    else "初筛阴性"}


def score_phq9(values: list[int]) -> dict:
    total = _score(values)
    if total is None or len(values) != 9:
        return {"ok": False, "reason": "作答需要 9 个 0~3 的整数"}
    if total < PHQ9_CUTS[0]:
        level, label = "minimal", "无明显抑郁症状"
    elif total < PHQ9_CUTS[1]:
        level, label = "mild", "轻度"
    elif total < PHQ9_CUTS[2]:
        level, label = "moderate", "中度"
    elif total < PHQ9_CUTS[3]:
        level, label = "moderately_severe", "中重度"
    else:
        level, label = "severe", "重度"
    return {
        "ok": True, "scale": "PHQ-9", "total": total, "max": 27,
        "level": level, "label": label, "cuts": list(PHQ9_CUTS),
        "somatic_sum": int(sum(values[i] for i in SOMATIC_IDX)),
        "item9_positive": bool(values[SI_IDX] > 0),
        "note": ("卒中人群的睡眠/疲惫/食欲三项可能与卒中本身重叠，"
                 "不要只凭这三项判断情绪"),
    }


def score_gad7(values: list[int]) -> dict:
    total = _score(values)
    if total is None or len(values) != 7:
        return {"ok": False, "reason": "作答需要 7 个 0~3 的整数"}
    if total < GAD7_CUTS[0]:
        level, label = "minimal", "无明显焦虑症状"
    elif total < GAD7_CUTS[1]:
        level, label = "mild", "轻度"
    elif total < GAD7_CUTS[2]:
        level, label = "moderate", "中度"
    else:
        level, label = "severe", "重度"
    return {"ok": True, "scale": "GAD-7", "total": total, "max": 21,
            "level": level, "label": label, "cuts": list(GAD7_CUTS)}


def triage(phq2: dict | None = None, phq9: dict | None = None,
           gad7: dict | None = None, eeg_level: str | None = None,
           eeg_reasons: list[str] | None = None) -> dict:
    """量表 + 脑电趋势 → 随访分层。

    三层，语义是对「下一步动作」的建议，不是对病情的判定：
        red    尽快复诊（一般 1 周内），必要时当日联系随访医生
        yellow 2 周内复评 + 保持/加强训练
        green  按计划继续，4 周后复评

    脑电趋势只做**加权**，不单独升级到 red —— 脑电是筛查辅助，
    不能因为一条曲线把患者推去急诊。
    """
    reasons: list[str] = []
    eeg_watch = (eeg_level == "watch")
    eeg_quality = (eeg_level == "quality")
    if eeg_watch:
        reasons.append("脑电趋势提示近期放松度下降/偏侧增大"
                       + (f"（{('；'.join(eeg_reasons))}）" if eeg_reasons else ""))
    if eeg_quality:
        reasons.append("近期有效通道率不足，脑电数据可信度低，建议复测")

    p9 = phq9.get("total") if phq9 and phq9.get("ok") else None
    g7 = gad7.get("total") if gad7 and gad7.get("ok") else None
    p2 = phq2.get("total") if phq2 and phq2.get("ok") else None
    item9 = bool(phq9 and phq9.get("ok") and phq9.get("item9_positive"))

    level, label, action = "green", "低风险", "按计划继续训练，4 周后复评"

    if item9:
        level, label = "red", "高风险"
        action = "PHQ-9 第 9 题不为 0：请尽快联系随访医生或精神心理科；若出现自伤风险请立即就近就医"
        reasons.insert(0, "PHQ-9 第 9 题（自伤念头）阳性")
    elif p9 is not None and p9 >= PHQ9_CUTS[2]:
        level, label = "red", "高风险"
        action = "建议 1 周内复诊，由医生评估是否需要心理/药物干预"
        reasons.insert(0, f"PHQ-9 总分 {p9}（≥{PHQ9_CUTS[2]}，中重度及以上）")
    elif g7 is not None and g7 >= GAD7_CUTS[2]:
        level, label = "red", "高风险"
        action = "建议 1 周内复诊，评估焦虑相关症状"
        reasons.insert(0, f"GAD-7 总分 {g7}（≥{GAD7_CUTS[2]}）")
    elif p9 is not None and p9 >= PHQ9_CUTS[1]:
        if eeg_watch:
            level, label = "red", "高风险"
            action = "量表与脑电趋势同时提示异常，建议 1 周内复诊"
            reasons.insert(0, f"PHQ-9 总分 {p9}（≥{PHQ9_CUTS[1]}）且脑电趋势异常")
        else:
            level, label = "yellow", "中风险"
            action = "2 周内复评 PHQ-9，保持每周 3 次训练"
            reasons.insert(0, f"PHQ-9 总分 {p9}（{PHQ9_CUTS[1]}~{PHQ9_CUTS[2] - 1}，中度阈值以上）")
    elif g7 is not None and g7 >= GAD7_CUTS[1]:
        level, label = "yellow", "中风险"
        action = "2 周内复评 GAD-7，保持训练"
        reasons.insert(0, f"GAD-7 总分 {g7}（{GAD7_CUTS[1]}~{GAD7_CUTS[2] - 1}）")
    elif p9 is not None and p9 >= PHQ9_CUTS[0]:
        if eeg_watch:
            level, label = "yellow", "中风险"
            action = "2 周内复评，并关注训练依从性"
            reasons.insert(0, f"PHQ-9 总分 {p9}（轻度）且脑电趋势异常")
        else:
            reasons.insert(0, f"PHQ-9 总分 {p9}（轻度）")
    elif p2 is not None and p2 >= PHQ2_POSITIVE:
        level, label = "yellow", "中风险"
        action = "PHQ-2 阳性，建议完成 PHQ-9 全量表复评"
        reasons.insert(0, f"PHQ-2 总分 {p2}（≥{PHQ2_POSITIVE}）")
    elif eeg_watch:
        level, label = "yellow", "中风险"
        action = "量表未见异常，但脑电趋势异常：2 周内复评并核对训练依从性"
    elif eeg_quality:
        level, label = "yellow", "中风险"
        action = "重测一次脑电（检查电极接触），再判断趋势"
    else:
        reasons.append("量表与脑电趋势均未见异常")

    if not reasons:
        reasons.append("无可判定依据：请先完成量表或采集至少 3 次脑电会话")

    return {
        "ok": True,
        "level": level, "label": label, "action": action, "reasons": reasons,
        "inputs": {"phq2": p2, "phq9": p9, "gad7": g7, "eeg_level": eeg_level},
        "disclaimer": "本提示为随访筛查辅助，不构成诊断，也不替代医生判断",
    }


def questionnaire() -> dict:
    """给前端渲染用的题库（题目文本只在这里维护一份）。"""
    return {
        "options": [{"label": t, "value": v} for t, v in OPTIONS],
        "phq2": {"name": "PHQ-2", "items": PHQ2_ITEMS, "cut": PHQ2_POSITIVE,
                 "note": "前两题，任一得分都算；总分 >= 3 需做 PHQ-9"},
        "phq9": {"name": "PHQ-9", "items": PHQ9_ITEMS, "cuts": list(PHQ9_CUTS),
                 "note": "近两周，0~3 计分，总分 0~27"},
        "gad7": {"name": "GAD-7", "items": GAD7_ITEMS, "cuts": list(GAD7_CUTS),
                 "note": "近两周，0~3 计分，总分 0~21"},
        "sources": [
            "PHQ-9 Kroenke 2001 J Gen Intern Med (PMID 11556941)",
            "PHQ-2 Kroenke 2003 Med Care (PMID 14583691)",
            "GAD-7 Spitzer 2006 Arch Intern Med (PMID 16717171)",
            "AHA/ASA 卒中后抑郁筛查声明 Towfighi 2017 Stroke (PMID 27932603)",
        ],
    }


if __name__ == "__main__":
    # 自检：四个典型场景，分层方向必须正确
    g = triage(phq2=score_phq2([0, 1]), eeg_level="steady")
    assert g["level"] == "green", g
    y = triage(phq2=score_phq2([1, 2]), eeg_level="steady")
    assert y["level"] == "yellow", y
    # 11 分属中度阈值以上 → 黄；15 分及以上才进红
    y11 = triage(phq9=score_phq9([2, 2, 2, 2, 1, 1, 1, 0, 0]), eeg_level="steady")
    assert y11["level"] == "yellow", y11
    r = triage(phq9=score_phq9([3, 3, 3, 3, 1, 1, 1, 0, 0]), eeg_level="steady")
    assert r["level"] == "red" and "PHQ-9" in r["reasons"][0], r
    si = triage(phq9=score_phq9([0, 0, 0, 0, 0, 0, 0, 0, 1]), eeg_level="steady")
    assert si["level"] == "red" and "第 9 题" in si["reasons"][0], si
    yb = triage(phq9=score_phq9([1, 1, 0, 0, 0, 0, 0, 0, 0]), eeg_level="watch",
                eeg_reasons=["放松度较基线下降 0.15"])
    assert yb["level"] == "yellow", yb
    print("量表自检通过：绿/黄/红与第 9 题升级逻辑均正确")
    print("示例（中度 + 脑电异常）：", triage(
        phq9=score_phq9([2, 2, 1, 2, 1, 2, 1, 0, 0]), eeg_level="watch")["action"])
