"""
第 1 步：认识数据 —— 把一份公开脑电数据"打开看看"

数据集：PhysionetMI（PhysioNet 上的经典运动想象数据集，109 名被试）
为什么选它：zenodo.org 在你这个网络被屏蔽了，而这份数据存在 physionet.org，能下。

这一份代码只做"加载 + 打印信息"，不涉及任何模型。
目的：让你亲眼看到脑电数据到底长什么样。
"""

import os
import time

ROOT = "/Users/chujingyue/DeepWorks/NeuroBloom"
os.environ.setdefault("MNE_DATA", os.path.join(ROOT, ".data", "mne_data"))
os.makedirs(os.environ["MNE_DATA"], exist_ok=True)

import moabb
moabb.set_log_level("warning")

from moabb.datasets import PhysionetMI

print("=" * 62)
print("数据集：PhysionetMI")
print("=" * 62)

ds = PhysionetMI()
print(f"总被试人数：{len(ds.subject_list)}")
print(f"数据存放目录：{os.environ['MNE_DATA']}")
print()

print("开始下载 / 读取（只取第 1 名被试，第一次会慢一点）…")
t0 = time.time()
data = ds.get_data(subjects=[1])
print(f"完成，用时 {time.time() - t0:.1f} 秒")
print()

# ---- 数据结构：被试 -> 场次 -> run -> Raw ----
subj = data[1]
sessions = list(subj.keys())
print(f"场次数：{len(sessions)}  ->  {sessions}")

sess_name = sessions[0]
runs = list(subj[sess_name].keys())
print(f"这个场次里的 run（可以理解成一小段一小段的记录）：{len(runs)} 个")
print()

raw = subj[sess_name][runs[0]]
print("--- 这一小段脑电（Raw）长什么样 ---")
print(f"  通道数    ：{len(raw.ch_names)}")
print(f"  前 8 个通道：{raw.ch_names[:8]}")
print(f"  采样率    ：{raw.info['sfreq']} Hz   （每秒采 {raw.info['sfreq']} 个点）")
print(f"  录制时长  ：{raw.times[-1]:.1f} 秒")
arr = raw.get_data()
print(f"  数据形状  ：{arr.shape}  = 通道数 × 时间点")
print()

print("--- 标了什么『事件』（标签就藏在这里）---")
if len(raw.annotations):
    from collections import Counter
    cnt = Counter(raw.annotations.description)
    print(f"  事件种类与出现次数：{dict(cnt)}")
    print(f"  事件总数：{len(raw.annotations)}")
    print("  前 6 个事件（时间点，类型）：")
    for d, o in list(zip(raw.annotations.description, raw.annotations.onset))[:6]:
        print(f"      {o:8.2f} 秒    {d}")
else:
    print("  这段没有标注")

print()
print("=" * 62)
print("【大白话总结】")
print("  1. 脑电数据 = 一张大表格：横着是时间，竖着是电极。")
print("  2. 64 个通道 = 头上贴了 64 个电极，每个电极记一路信号。")
print("  3. 标签（T0/T1/T2）不是信号自带的，是当时做实验的人手动记下的")
print("     『第几秒我让被试想象了左手/右手』。")
print("  4. 所以：没有事件标注 → 没有标签 → 训不出模型。")
print("     这正是你导师质疑的第 ① 条：标签从哪来？")
print("=" * 62)
