"""【已废弃 · 保留仅为对照】脑波状态判定：α/β 功率比 -> 放松 / 专注 / 焦虑。

⚠️ 这个文件已经不是判定逻辑的来源了，请不要再 import 它。
   现场采集（eeg_service/main.py）和离线回放（eeg_service/replay_service.py）
   现在共用 analysis/relax_axis.py —— 一份逻辑，两处使用。

为什么废弃：
    1. 固定阈值（β 占比 > 0.75 判焦虑、α > β 判放松）。
       每个人的 α/β 绝对水平能差好几倍，拿一个固定数字套所有人，
       等于拿别人的尺子量自己。
    2. world_params 里 fog 恒为 0、放松时 crystal 恒为 0.1，
       水晶这个「奖励」通道实际上一直不动，白扔一个视觉反馈。
    3. 完全没有坏通道拒判 —— 一个饱和电极的功率能顶掉十几个好通道，
       实测剔不剔坏通道，α/β 的波动是 65% 和 47% 的差别。

保留它的原因只有一个：里面的四个世界参数键名
（bloom / sunlight / fog / crystal）是和 Unity 绑定的契约。
留在这里当个说明，换实现时键名不能改。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NeuroState:
    label: str          # relaxed | focused | anxious
    alpha_ratio: float  # α 功率占比
    beta_ratio: float   # β 功率占比
    focus: float        # 0-1，综合专注度
    calm: float         # 0-1，综合放松度


def classify(alpha_power: float, beta_power: float,
             anxiety_threshold: float = 0.75) -> NeuroState:
    """根据 α/β 功率判定状态。

    - β 占比过高  -> 焦虑（高 β）
    - α 占比占优  -> 放松（α 主导）
    - 其余且 β 中等偏高 -> 专注（β 主导）
    """
    total = alpha_power + beta_power + 1e-9
    a = alpha_power / total
    b = beta_power / total

    if b > anxiety_threshold and b > a:
        label = "anxious"
    elif a > b:
        label = "relaxed"
    else:
        label = "focused"

    focus = max(0.0, min(1.0, b))
    calm = max(0.0, min(1.0, a))
    return NeuroState(label=label, alpha_ratio=a, beta_ratio=b,
                      focus=focus, calm=calm)


def world_params(state: NeuroState) -> dict:
    """把状态映射为 Unity 世界参数（0-1）。"""
    if state.label == "relaxed":
        return {"bloom": state.calm, "sunlight": 0.7 + 0.3 * state.calm,
                "fog": 0.0, "crystal": 0.1}
    if state.label == "focused":
        return {"bloom": 0.2, "sunlight": 0.8, "fog": 0.0,
                "crystal": state.focus}
    return {"bloom": 0.05, "sunlight": 0.25, "fog": 1.0 - state.calm,
            "crystal": 0.0}


if __name__ == "__main__":
    for a, b in [(0.8, 0.2), (0.3, 0.6), (0.1, 0.9)]:
        s = classify(a, b)
        print(s.label, "->", world_params(s))
