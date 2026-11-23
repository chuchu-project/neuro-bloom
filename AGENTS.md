# AGENTS.md — NeuroBloom 工作约定

给在本仓库工作的 AI 代理与协作者的项目约束。人类读者先看 [README](README.md)。

## 权威文档（动手前先读）

| 文档 | 作用 |
| --- | --- |
| [项目提案](docs/proposal.md) | 问题、用户、证据、角色与里程碑 |
| [需求文档](docs/prd.md) | MVP 范围、需求编号 FR-01…FR-07、验收标准 |
| [技术设计](docs/tech-design.md) | 结构、选型、数据格式、失败降级 |
| [验证结果](docs/verification.md) | 需求追踪表、实测数据、已知限制 |
| [决策记录](docs/decisions.md) | 关键选择的备选项与结论 |

文档冲突时以 PRD 与验证结果为准；不一致本身算缺陷，先修文档再改代码。

## 不可越过的约束

- **不做诊断、不给治疗建议**，所有指标写「参考」。
- **未做临床验证**，不得把合成样本或健康成人自测表述成疗效。
- 演示模式全程 `SIMULATED` 水印，关闭后立即回到空态，不留残留数字。
- `world_params` 四键 `sunlight` / `fog` / `bloom` / `crystal`（0~1）不可改名，改名 Unity 全收 0。
- 判定只有一处：`analysis/relax_axis.py`。不要在其他模块复制或重写公式。
- `agent_service` 只输出引导语文本，不写画面参数。
- `.env`、`sessions/`、`recordings/`、`.data/`、`UnityVR/Library/` 不入库；密钥只走 `.env`。

## 常用命令

```bash
bash setup/macos-dev-setup.sh    # 首次：建 .venv、装依赖
source .venv/bin/activate
cp .env.example .env             # 书生 API Key；留空则引导语走本地兜底
bash start.sh                    # 起 bridge + eeg + agent
bash stop.sh                     # 全部停止
```

- 控制台：`http://127.0.0.1:8765/app.html`；无采集板时点「演示（非脑电）」。
- 回放已录 BDF：双击 `打开演示.command`。
- 延迟自测：`.venv/bin/python tools/latency_probe.py --seconds 40`。
- 离线成果自检：`python3 tools/verify_stage.py`（需本机已有 `UnityVR/render_states/`，该目录不入库）。

## 提交前检查

1. 需求状态若变化，同步更新 [验证结果](docs/verification.md) 的追踪表。
2. 口径统一：延迟说「攒窗 4000 ms / 分析 1.0 ms / 传输中位 2.3 ms」，不要讲「低于 100 ms」。
3. 改到判定公式或画面参数时，用同一份 BDF 回放两次，确认序列一致。
4. 提交信息用中文，前缀取 `docs:` / `feat:` / `fix:` / `chore:`。

## 隐私

仓库公开。不提交患者数据、真实姓名与手机号、录音、量表原始作答、API Key。
