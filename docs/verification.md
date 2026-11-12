# 最终提交：实现状态与验证结果

> 版本 v1.0 · 2026-11-12 · 对应课程 Project-based Course 1105
> 关联：[Proposal](proposal.md) · [PRD](prd.md) · [技术设计](tech-design.md)

本文件回答三个问题：每条需求最后做到什么程度、有什么证据、还剩什么限制。

## 1. 需求追踪表

| 需求 | 组件 / 模块 | 验证证据 | 最终状态 |
| --- | --- | --- | --- |
| FR-01 设备连接与信号质量 | `eeg_service/` + `dashboard/` 设备页 | 16 路波形 25 Hz 刷新、每路 `Railed%`/`µVrms`；无板时 `/control/start` 返回 `blocked: no-board`，界面为「—」 | Implemented |
| FR-02 一人一基线标定 | `analysis/relax_axis.py`（`RelaxTracker`） | 前 60 秒不出分、不驱动画面；基线取该段中位数；新会话重新标定 | Implemented |
| FR-03 α/β、β/θ 实时判定 | `analysis/relax_axis.py`、`alpha_beta_state.py` | 公式自检（见 §2）；同一 BDF 回放两次序列一致 | Implemented |
| FR-04 三维花园实时反馈 | `UnityVR/Assets/Scripts/` + `bridge/` | 四参数实测：指数 0.80→0.96 全程晴天，花开/雾散/光照上升；演示视频见 `docs/assets/` | Implemented |
| FR-05 训练会话与报告 | `bridge/server.py`、`analysis/session_report.py` | `/report`、`/report?download=1` 可用；报告含走势、通道质量、频带构成、左右不对称 | Implemented |
| FR-06 随访量表与趋势 | `analysis/scales.py`、`trend.py`、`bridge/` | PHQ-2/9、GAD-7 计分与分层；第 9 题红线；`/follow.csv` 导出；趋势四级风险 | Implemented |
| FR-07 演示模式与水印 | `eeg_service/`、`dashboard/` | 演示全程 `SIMULATED` 水印 + 橙色横幅；关闭即空态，无残留数字 | Implemented |
| 引导语（支撑） | `agent_service/agent.py` | 大模型生成 script，失败走 `pick_fallback`；只出文本，不写画面参数 | Implemented（降级路径已验证） |
| 呼吸引导（支撑） | `dashboard/` 训练页 | 吸 4 s / 呼 6 s，6 次/分；倒计时为剩余秒数 | Implemented |

## 2. 判定公式自检（2026-11-12 实跑）

```
RELAX_SPAN 1.0  CRYSTAL_LO 0.4  CRYSTAL_SPAN 0.4
ratio=0.5  relax=0.0000  bloom=0.00 sunlight=0.25 fog=1.00 crystal=0.00
ratio=1.0  relax=0.5000  bloom=0.50 sunlight=0.63 fog=0.50 crystal=0.25
ratio=2.0  relax=1.0000  bloom=1.00 sunlight=1.00 fog=0.00 crystal=1.00
monotonic: True
```

对应 AC-03.1 与 AC-04.1：0.5 倍基线 → 0，1 倍 → 0.5，2 倍 → 1.0；四参数随指数单调。

## 3. 性能实测（演示样本，2026-10-24）

| 指标 | 数值 |
| --- | --- |
| 判定周期 | 4000 ms（4 秒窗，物理下限） |
| 分析耗时 | 1.0 ms |
| 传输延迟 | 中位 2.3 ms，95 分位 3.7 ms |
| 画面推流 | 18 fps，1600×900 JPEG |

## 4. 未取得的结果（诚实清单）

| 项 | 现状 | 原因 |
| --- | --- | --- |
| 患者 / 治疗师访谈 | **未开展** | 无伦理批件与入组渠道 |
| L1 跨天重复性 ICC | 未测 | 脚本已就绪（`tools/reliability.py`），需 2 天 × 5 人 |
| L2 闭眼/睁眼区分度 | 未测 | 脚本已就绪（`tools/state_probe.py`） |
| L3 可用性（SUS、任务完成率） | 未测 | 计划 5 人，含 1–2 名中年 |
| 真实患者数据 | 未接入 | 本阶段仅健康成人自测与合成信号 |
| 疗效结论 | **不做** | 超出课程范围，也不具备证据条件 |

因此，仓库中出现的任何"放松占比""趋势改善"类数字，只能理解为**链路自洽性检查**，不能读作效果证据。

## 5. 计划变更说明（Scope changes）

| 原计划 | 实际 | 原因 |
| --- | --- | --- |
| 大模型同时生成引导语并驱动画面 | 改为只生成文本，画面参数由脑电链路单独写入 | 两个写入者会互相覆盖同一场景，行为不可复现 |
| 使用 F3/F4 计算额区不对称（FAA） | 改为左右半球分组不对称，并在报告中说明不是 F3/F4 | 设备帽位无 F3/F4，套用名称会造成误读 |
| 以「放松度」作为界面主指标 | 改为「α/β 指数」 | 与量表的效度关系未经验证，不宜用结论性命名 |
| 计划中的云端多用户 | 移除 | 单机单治疗师场景已覆盖 MVP，账号体系带来合规负担 |

## 6. 复现方式

```bash
bash setup/macos-dev-setup.sh
source .venv/bin/activate
bash start.sh                 # bridge + 采集 + agent
# 浏览器打开 http://127.0.0.1:8765/app.html
# 无板：点「演示（非脑电）」，全程带 SIMULATED 水印
```

判定自检（无需设备）：

```bash
.venv/bin/python -c "
from analysis.relax_axis import relax_from_score, world_from_relax
for m in (0.5, 1.0, 2.0):
    r = relax_from_score(m, 1.0); print(m, round(r,3), world_from_relax(r))
"
```
