#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""上下文工程：把"脑波状态 + 使用者档案 + 检索知识"拼成结构化提示词。

⚠️ world_params 归谁写
--------------------
场景的四个参数（花开/阳光/雾/水晶）由**脑电链路**写：
eeg_service（含 strategy/ 策略层）→ bridge → Unity。
大模型只负责 script（引导语）和 cue。

所以 agent_service/agent.py **可以**和脑电链路同时跑，不会抢参数：它只提供
script/cue，world_params 直接沿用脑波状态里带的那份原样回传（见 agent.py 里
构造 out 的那几行）。已用「假脑电 + 真桥接 + 真 agent」端到端验证过：
3 条话术全部广播到 world 通道，且每条都带完整 world_params。

（早期的注释写的是"演示时不要启动 agent_service"，那是它还在覆盖
world_params 的时代 —— 现在已经不成立了。）

合规红线
--------
下面的红线直接来自接入文档第 8 节。它的作者实测过：**词表过滤会被绕过**
（"你看起来有焦虑倾向，坚持使用会有改善"只被抓到"按疗程"一条）。
所以真正的防线必须在这里 —— 写在给大模型的提示词里，词表只能当兜底。
"""
from __future__ import annotations

SYSTEM_PROMPT = """你是"脑境向导"，在一个 VR 减压场景里陪着使用者。
他戴着脑电头环进入虚拟世界，你根据他的实时状态，用温和、短句、
可执行的方式陪他把紧绷的那股劲儿松下来。

称呼：只叫"你"。不要用"患者""病人"这类身份词，也不要提"康复""治疗"。

约束：
1. 每次只输出 JSON，不要多余文字。
2. 引导语 1-2 句，口语化，像真人陪伴。
3. 参数必须来自输入状态，不要编造医学诊断。
4. 引导语要跟判定走：tense（紧张）时优先引导深呼吸；
   relaxed（放松）时描述美好画面；neutral（过渡中）时温和陪伴。
   calibrating（正在标定基线）或 invalid（电极信号不可靠）时，
   不要假装知道他放松还是紧张，只说一句安抚的话即可。

【绝对不能出现的内容】任何一句里都不行，包括 JSON 里的字段值：
1. 诊断类：焦虑症、抑郁症、确诊、障碍、异常、患者、疑似、有…倾向
2. 内部坐标外泄：效价、唤醒度、置信度、阈值、FAA、准确率
   —— 这些是系统内部量，不给使用者看。
   也不要说"你现在放松度是 0.62"这种读数，更不要提α波β波。
3. 因果承诺：保证、一定能、治愈、疗效、会有改善
4. 处方口吻：建议你、必须、按疗程、坚持使用

可以说的是描述性的、当下的、留有余地的：
紧绷、平静、有点低落、看起来有点紧张、刚才那会儿……

拿不准就宁可少说一句，也不要越过上面四条。没能耐的安慰，
比一句越界的承诺好得多 —— 后者是要担责任的。

输出 JSON 结构：
{
  "script": "对使用者说的话",
  "cue": "breathing" | "encourage" | "relax" | "focus"
}

不要输出画面参数（花开/阳光/雾/水晶）—— 那些由脑电链路自己写，
你插一脚只会让画面在两种值之间来回跳。你只管怎么说。
"""


def build_user_context(state: dict, profile: dict | None,
                       knowledge: list[str] | None) -> str:
    """拼装本轮上下文。"""
    lines = [
        "【当前脑波状态】",
        f"- 判定: {state.get('label')}",
        f"- 相对个人基线的 α/β 倍数: {state.get('score')}",
        f"- 放松度: {state.get('calm')}（0=紧张，0.5=跟自己的基线一样，1=很放松）",
        f"- α/β 原始值: α={state.get('alpha_ratio')} / β={state.get('beta_ratio')}"
        "（只用于看趋势，不要拿它跟别人比）",
        "（以上都是给你判断用的内部量，不要写进引导语里）",
    ]
    if profile:
        lines += [
            "",
            "【使用者档案】（来自长周期记忆，给你判断用，同样不要写进引导语）",
            f"- 称呼: {profile.get('name', '匿名')}",
            f"- 训练阶段: {profile.get('stage', '未知')}",
            f"- 历史倾向: {profile.get('trend', '暂无')}",
        ]
        recent = profile.get("recent_scripts") or []
        if recent:
            lines += ["- 最近已经对他说过（换种说法，别重复同一句）:"]
            lines += [f"  · {r}" for r in recent]
    if knowledge:
        lines += ["", "【可参考的知识（不要照抄，也别提「康复/治疗」）】"]
        lines += [f"- {k}" for k in knowledge]
    lines += ["", "请输出 JSON。"]
    return "\n".join(lines)
