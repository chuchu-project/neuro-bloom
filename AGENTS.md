# AGENTS.md

给在本仓库工作的 AI 编码助手的说明。人读也可以，内容与 [CONTRIBUTING.md](CONTRIBUTING.md) 不重复。

## 项目是什么

脑境 NeuroBloom：16 通道脑电 → 相对本人基线的 α/β 判定 → Unity 三维花园实时反馈 → 治疗师控制台与随访记录。面向卒中后居家康复期。

## 权威文档（改动前先读）

| 文档 | 管什么 |
| --- | --- |
| [docs/proposal.md](docs/proposal.md) | 问题、用户、证据边界、角色 |
| [docs/prd.md](docs/prd.md) | 功能范围、FR 编号、验收标准、排除项 |
| [docs/tech-design.md](docs/tech-design.md) | 结构、栈选择、数据格式、失败降级 |
| [docs/verification.md](docs/verification.md) | 每条需求的最终状态与已知限制 |
| [docs/decisions.md](docs/decisions.md) | 已定架构决策，不要推翻而不说明 |

文档与代码冲突时，以代码的**实际行为**为准，并在同一次提交里修正文档。

## 硬约束

1. `analysis/relax_axis.py` 是判定的唯一实现。不要在别处复制换算公式。
2. `world_params` 的四个键 `bloom` / `sunlight` / `fog` / `crystal` 是跨端契约，**不可改名**；改名 Unity 会全部收到 0。
3. 画面参数只由脑电链路写入；`agent_service` 只输出引导语文本。
4. 无采集板时不得伪造数字。必须走 `blocked: no-board` + 空态。
5. 演示数据必须带 `SIMULATED` 水印，关闭演示立即回到空态。
6. 不得声称疗效或诊断结论；指标一律写「参考」。
7. 会话记录、量表、录音、`.env` 不进仓库（`.gitignore` 已排除）。
8. 界面指标名用「α/β 指数」，不用「放松度」。

## 常用命令

```bash
bash setup/macos-dev-setup.sh      # 建 venv、装依赖
source .venv/bin/activate
bash start.sh                      # bridge + 采集 + agent；bash stop.sh 停
./.venv/bin/python analysis/check_board.py        # 板卡连通
./.venv/bin/python tools/latency_probe.py         # 端到端延迟
```

无设备时：打开 `http://127.0.0.1:8765/app.html`，点「演示（非脑电）」。

## 验证期望

改判定或映射逻辑后，至少跑：

```bash
./.venv/bin/python -c "
from analysis.relax_axis import relax_from_score, world_from_relax
for m in (0.5, 1.0, 2.0):
    r = relax_from_score(m, 1.0); assert 0 <= r <= 1; print(m, round(r,3), world_from_relax(r))
"
```

期望：0.5→0.0、1.0→0.5、2.0→1.0，四参数随指数单调。

改前端后确认：无板状态下不出现任何数字；演示水印可见；关闭演示回到空态。

## 提交

遵循 [CONTRIBUTING.md](CONTRIBUTING.md)：Conventional Commits、一 PR 一件事、贡献用 `@username` 标注。

## 成员

| GitHub | 负责 |
| --- | --- |
| [@chuchu-project](https://github.com/chuchu-project) | 软件设计与实现、3D 场景与推流、技术文档 |
| [@chefzs929-prog](https://github.com/chefzs929-prog) | 脑电数据采集与实验、基线标定流程、信号质量 |
