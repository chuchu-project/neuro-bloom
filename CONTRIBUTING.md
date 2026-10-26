# 协作规范

本仓库同时是课程培训的一部分：**仓库管理、提交信息、issue / PR 流程本身就是考核项**。
下面是这个项目的落地做法，照着走即可。

## 一、分支

| 分支 | 用途 |
| --- | --- |
| `main` | 始终可运行。只通过 PR 合入，不直接 push |
| `feat/<简短英文>` | 新功能，例：`feat/wave-16ch` |
| `fix/<简短英文>` | 修缺陷，例：`fix/demo-toggle` |
| `docs/<简短英文>` | 只改文档 |

分支存在时间尽量短，一个 PR 只做一件事。

## 二、提交信息（Conventional Commits）

```
<type>(<scope>): <一句话说清做了什么>

<为什么这么做 / 影响范围，可省略>
```

- `type`：`feat` / `fix` / `docs` / `refactor` / `test` / `chore`
- `scope`：`bridge` / `eeg` / `dashboard` / `unity` / `analysis` / `docs`
- 用祈使句、说结果，不写「update」「修改一下」这类无信息量的词。

示例：

```
feat(bridge): 无采集板时 /control/start 返回 blocked:no-board
fix(dashboard): 空态下不再沿用上一次的量程标签
docs(latency): 延迟口径改为三段实测 4000/1.0/2.3 ms
```

## 三、贡献怎么被记录（@username）

评价看的是可追溯的协作痕迹，所以：

1. **先开 issue**：说清要做什么、验收标准是什么。
2. **开 PR 时在描述里写 `Closes #12`**，并 `@` 一起改的人。
3. **commit 里带上协作者**（成对编程时）：

```
feat(analysis): IAF 交叉验证接入判定页

Co-authored-by: @队友用户名 <队友邮箱>
```

4. 讨论尽量留在 issue / PR 里，不要只在线下说——线上没有记录就等于没发生。

## 四、PR 检查清单

- [ ] 本地跑过：`bash start.sh` 起得来，控制台五页能翻
- [ ] 没提交 `.env`、`.data/`、`sessions/`、`UnityVR/Library/`（`git status` 自查）
- [ ] 改了判定口径 → 同步更新 `docs/`，并在 PR 里写明对旧数据的影响
- [ ] 至少 1 人 review 通过再合并（自己不能 review 自己）

## 五、数据保密（硬约束）

本仓库**不接收**任何可能指向个人的数据：

| 类型 | 例子 | 处理 |
| --- | --- | --- |
| 脑电原始 / 会话记录 | `sessions/*.jsonl`、`.data/recordings/` | 本地保存，`.gitignore` 已排除 |
| 量表与自评 | `scales.jsonl`、`feedback.jsonl` | 同上 |
| 公司或讲师提供的材料 | 说明书 PDF、内部文档、报价单 | **未经书面批准不得上传** |
| 密钥 | `.env` 里的 API Key | 只提交 `.env.example` |

不确定能不能传，就先问，不要先推。

## 六、目录约定

- 代码与文档进仓库；构建产物、依赖、缓存、运行数据不进。
- 新增忽略规则请改根目录 `.gitignore`，并在 PR 里说明原因。
- 大文件（>20 MB）先议，不要直接 `git add`。

## 七、新成员第一次拉代码

```bash
git clone https://github.com/chuchu-project/neuro-bloom.git
cd neuro-bloom

# 署名必须是自己的 GitHub 账号，否则贡献记录算不到你头上
git config user.name  "你的GitHub用户名"
git config user.email "你的数字ID+你的GitHub用户名@users.noreply.github.com"
```

数字 ID 在 `https://api.github.com/users/你的用户名` 的 `"id"` 字段里。
也可以直接 `gh auth login` 后用 `gh api user --jq .id` 查。

拉下来先跑一遍 README 的「快速开始」，确认能起再动手改；跑不起来先开 issue，
不要在自己机器上瞎改依赖版本。

没有采集板是**正常状态**：控制台会保持空态，点「演示（非脑电）」即可看完整链路。
