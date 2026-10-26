# NeuroBloom 脑境共生

脑机接口 + 大模型 + VR 减压场景：把使用者的实时脑波状态，翻译成一座会跟着
变亮、变暗、雾气散开又合拢的虚拟花园。

## 目录

| 路径 | 作用 |
| --- | --- |
| `eeg_service/` | 采集（真设备 / 合成板）与本地文件回放，判定的入口 |
| `analysis/` | 判定内核与实验工具（下表） |
| `strategy/` | 演示策略层：按轮次把画面调制成八套氛围（P8 那套是不干预对照） |
| `bridge/` | WebSocket 桥（:8765），EEG / Agent / Unity 三方的唯一交汇点 |
| `agent_service/` | 书生大模型生成引导语。**演示时不要启动**（见下） |
| `tools/` | 自评录入、成绩单、延迟实测（`latency_probe.py`） |
| `sessions/` | 每次训练的会话记录（`.jsonl`）和自动生成的报告（`.html`），本地数据，不进仓库 |
| `UnityVR/` | Unity 工程，接脑电的三个脚本在 `UnityVR/Assets/Scripts/` |
| `unity/` | 同一批 C# 脚本的副本 + 编辑器工具（一键搭场景 / 诊断） |
| `mcp/` | 康复知识库 MCP Server（`query_reha_guide`） |
| `skills/neurobloom/` | 引导语 Skill：提示词口径的说明文档 |
| `setup/` | 环境脚本与 VR 机配置说明 |
| `docs/` | `接真设备-Mac.md` 等操作文档 |

### `analysis/` 里有什么

| 文件 | 作用 |
| --- | --- |
| `relax_axis.py` | **判定的唯一来源**：α/β → 相对个人基线 → 单轴放松度 → 四个画面参数 |
| `live_state_gui.py` | 实时看自己放松度的窗口：浅绿=放松 / 浅蓝=中间 / 浅红=紧张 |
| `offline_start.py` | 离线一键启动：自检 → 开窗口 → 自动存档 |
| `guided_experiment.py` | 引导式实验：基线 → 闭眼放松 → 限时心算，出阶段级报告 |
| `check_board.py` | 板子连通性自检 |
| `session_report.py` | 会话记录 → 单文件 HTML 报告（走势、左右不对称、频带构成、临床口径指标、引导语） |
| `eeg_markers.py` | 临床口径指标：IAF / α 不对称对数比 / LZc / 样本熵（antropy 优先，内置回退） |
| `scales.py` | 随访量表：PHQ-2 / PHQ-9 / GAD-7 题库、计分与低/中/高分层（含第 9 题红线） |
| `trend.py` | 跨会话趋势与四级风险（被 `/trend`、`/risk` 调用） |
| `alpha_beta_state.py` | 频谱 / 频带 / 通道质量的底层实现 |
| `state_gui.py` | 离线回看 Physionet 公开数据（判定走的是旧的离线那套，未同步） |
| `step1_load.py` | 最早的数据加载探索脚本 |

## 当前版本（v1.0 控制台）

- **五页治疗师端**：总览 / 实时训练 / 数据 / 随访 / 设备，浅色、非技术用户可独立操作。
- **16 路波形**：125 Hz 显示、5 秒窗、25 帧/秒、16 路共用自动量程（±10–400 µV），
  每路带贴顶比例 `Railed%` 与 `µVrms` —— 电极接触质量一眼可见。
- **无板即空**：数字为「—」、网格有线不画波形、不补假数据；后端无板直接 `blocked`。
- **演示可开可关**：全程 `SIMULATED` 水印 + 橙色横幅，关掉即回空态。
- **判据同页可复核**：数据页摆出去直流 → 0.5–45 Hz → 4 秒窗 → Welch → 频带积分 →
  α/β 相对基线 → log₂ 映射 → 阈值分档，外加 IAF 交叉验证。

## 判定只有一处

α/β 的绝对值因人而异（同一个人不同时段实测能差 3 倍），所以判定不看绝对值，
只看**相对自己的基线**：`relax = clamp(0.5 + 0.5*log2(score)/span, 0, 1)`。

`analysis/relax_axis.py` 是这套逻辑的唯一实现：现场用「因果平滑 + 开头
60 秒定基线」，回放用「居中平滑 + 自动挑最干净的一段定基线」，
`live_state_gui.py`、`eeg_service/main.py`、`eeg_service/replay_service.py`
都调它，不各写一份。

## 快速开始（开发）

```bash
bash setup/macos-dev-setup.sh
source .venv/bin/activate
cp .env.example .env          # 填书生 API Key
bash start.sh                 # 后台起三个服务（bridge + 读脑 + 动脑）；bash stop.sh 停
```

> `start.sh` 会一起拉 `agent_service/agent.py`。`eeg_service/main.py` 现在**默认不造数**：
> 没有采集板就直接拒绝启动（`/control/start` 返回 `blocked: no-board`），界面保持空态。
> 要跑合成数据，必须显式开演示（控制台横幅按钮，或 `/control/demo?on=1`），
> 且全程带 `SIMULATED` 水印与橙色横幅；关掉立刻回到空态。

## 演示：双击就行

```bash
双击 打开演示.command
```

它会依次：清掉残留进程 → 起 bridge（:8765）→ 用真实录制的 BDF 回放
（`--round-seconds 10`，每 10 秒换一轮氛围，开局 3 轮固定 P8 不干预对照）
→ 实时跟随日志 → 退出时一次收干净（不会误杀 Unity）。

手动等价命令：

```bash
./.venv/bin/python bridge/server.py &
./.venv/bin/python eeg_service/replay_service.py \
    --file ~/Documents/OpenBCI_GUI/Recordings/OpenBCI-BDF-2026-10-14_20-37-57.bdf \
    --from 415 --to 510 --speed 2 --loop --round-seconds 10
```

Unity 侧：打开 `UnityVR/`，确认 Player 设置里 **Run In Background 已勾选**，按 Play。

- **不要**启动 `agent_service/agent.py`：它也往 world 通道推画面参数，会和脑电链路抢同一个值。
- 本工程的 `runInBackground` 是**关**的：Unity 一失焦就暂停游戏循环，录自评或盯日志时画面会冻住。
- 只回放不连 Unity 时，上面那行加 `--dry-run` 就能先看节奏。

## 数据流

```
eeg_service ──(ws :8765/ws/eeg)──► bridge ──(ws :8765/ws/world)──► UnityVR
      │                                  ▲
      └── strategy/ 策略层调制的四键 ──────┘
agent_service（引导语 script，可选）───────┘
```

Unity 订阅 `ws://127.0.0.1:8765/ws/world`，收到 `{state, world_params, script}` 驱动世界。

消息里另有几个**只增不改**的字段：`focus`（β/θ 专注度）、`bands`（五频带功率）、
`lr`（左右半球不对称）、`bt`（β/θ 原始比）。老版本的 Unity 不认识它们也不影响，
因为 `world_params` 一个字没动。

`world_params` 的四个键（`sunlight` / `fog` / `bloom` / `crystal`，都是 0~1）是跨端契约：
**改名 Unity 就全收 0**（`NeuroWorldController.cs` 逐键取值，缺一个就退回默认）。

策略层只**调制**画面、不替换脑电驱动：P8 那几轮画面照常在跟脑电变，那不是卡住。
回退：`--no-strategy` 或 `EEG_STRATEGY=0`；`--control-rounds N` 改开局对照轮数。

## 训练报告（自动生成）

每跑一次训练，桥接把每条状态追加到 `sessions/session-*.jsonl`；脑电服务断开
（或点「停止」）时自动收尾，生成 `sessions/report-*.html`。

- 控制台顶部「导出报告」直接打开最新一份：`http://127.0.0.1:8765/report`
- 想下载：`http://127.0.0.1:8765/report?download=1`
- 手动重出一份：`./.venv/bin/python -m analysis.session_report --in sessions/xxx.jsonl`
- 细节（每个数字怎么解释、答辩怎么说）见 `../docs/neurobloom-session-report.md`

## 设备连接（应用内，不用终端）

桥接启动时会探一次板子（TCP `192.168.4.1:1112`）：通了就自动起真机采集，
不通就保持空闲、不弹窗、不打扰。想手动接：

控制台右上角 **「设备」** → 填 IP / 端口 / 板卡 → **检测**（看毫秒数）→ **连接设备**。
弹窗会直接显示失败原因（`连不上…` / `端口通了，但脑电板握手失败…`），
不需要去翻日志。接口：

| 接口 | 用途 |
| --- | --- |
| `GET /device` | 当前模式（real/demo/idle）、配置、探测结果、上次失败原因 |
| `GET /device/connect?ip=&port=&board_id=` | 改配置并重启采集（仅本机） |

板卡编号：`6` = 16 通道 WiFi（默认）、`5` = 8 通道 WiFi、`0` = 蓝牙/串口、`-1` = 合成板。
`DEVICE_AUTOCONNECT=0` 可关掉开机自动连接。

## 长周期趋势与语音

- `GET /trend`：跨会话趋势。基线取最早 2 次，对比最近 3 次；输出
  放松度/|LI| 的变化与四级风险（`steady` / `mild` / `watch` / `quality`），
  每条结论都带触发原因。合成板会话会被标 `synthetic`，不参与质量判定。
- `GET /tts?text=`：本地 piper 合成中文语音，返回 wav；没有模型时 503。
  控制台「语音」开关打开后，每次引导语自动朗读，全部离线。

必须带的数据前提：**只存特征、不存原始波形**，所以音频/复核都基于特征，
不涉及原始脑电外传。

## 智能随访（量表和脑电合起来看）

控制台右上角 **「随访」**（待机页也有入口，**不接电极也能做**）：

1. 先做 PHQ-2 两题（约 15 秒）；每题可点 **「读题」** 让它念出来。
2. 结果是阳性，会自动展开 PHQ-9（九题）+ GAD-7（七题），继续做完。
3. 提交后给「低 / 中 / 高」三档 + 一句建议 + 触发原因；高风险固定话术为
   "请尽快联系随访医生或精神心理科"（第 9 题阳性时特别提示）。
4. 结论只写量表原始得分与区间，不写病名；脑电趋势只加权，不单独升级到高风险。

| 接口 | 用途 |
| --- | --- |
| `GET /scales` | 题库（PHQ-2/PHQ-9/GAD-7 + 四级选项）+ 最近记录 + 隐私说明 |
| `GET /scales/submit?pid=&phq2=&phq9=&gad7=&note=` | 提交作答；分值用逗号分隔，长度/取值不对直接 400 |
| `GET /risk` | 脑电趋势 + 最新量表合并后的分层（只读，不写文件） |

- 落盘在 `sessions/scales.jsonl`（**只存编号 + 分值 + 分层 + 当时脑电趋势**，
  不存姓名/电话/住院号）。编号只允许 `[A-Za-z0-9_-]`、最长 16 位，其它字符会被过滤。
- 题目文案与阈值只写在 `analysis/scales.py` 一处，前端从 `/scales` 拉取。
- 有版权的量表（如 HADS）不内置；PHQ 系列 / GAD-7 为原作者公开授权免费使用。

## 指标口径（医生看得懂的那几个数）

除了控制台那五根频带柱，每帧还会顺带算几个**临床口径**的数（`analysis/eeg_markers.py`），
日志里能看到，例如 `IAF=10.27Hz LZc=0.46`：

| 指标 | 含义 | 注意 |
| --- | --- | --- |
| IAF | 个体 α 峰频率（6–14 Hz 找峰 + 三点抛物线插值） | 因人而异，8–12 Hz 常见；用来收窄 α 带 |
| α 不对称对数比 | ln(右半球 α) − ln(左半球 α) | 文献里的 FAA 特指 F3/F4、F7/F8，我们是同源代理，**不做情绪判断** |
| LZc / 样本熵 | 复杂度 / 规律性 | 交叉验证用，归一化后白噪 LZc≈1.09、正弦≈0.20 |
| Higuchi / 排列熵 / 谱熵 | 另三种复杂度视角 | 默认关闭，需要时在报告里打开 |

- 复杂度用**通道平均后的一维序列**现算（多通道重复没有额外信息，平均还能压噪声）。
- 为了不拖慢画面：IAF 默认每 5 秒重算一次、复杂度每 10 秒一次
  （`EEG_IAF_EVERY` / `EEG_CX_EVERY` 可改）；单次重算约 10–12 ms。
- 优先用成熟的 `antropy` 实现，**没装就自动回退内置算法**，不会因为环境缺件崩。
- 这些数会写进会话 jsonl（`iaf` / `alpha_asym_log` / `lzc` / `sample_entropy`），
  报告里多一节「临床口径指标」。

## 延迟实测

```bash
./.venv/bin/python tools/latency_probe.py --seconds 40
```

输出三段：攒窗口多久、分析算了多久、传输排队多久，以及端到端。本机演示样本上
实测是 4000 / 1.0 / 2.3 毫秒（95 分位 3.7）。**别对外讲「延迟低于 100ms」**，口径见
[`docs/neurobloom-latency.md`](docs/neurobloom-latency.md)。

## 自评与成绩单

```bash
./.venv/bin/python tools/feedback.py            # 每轮结束后打分：1=更差 2=稍差 3=没变 4=好点 5=好多了（回车跳过，q 退出）
./.venv/bin/python tools/feedback.py --report 4 # 不想交互就一条命令记一条
./.venv/bin/python tools/feedback.py --summary  # 看汇总：对照轮（P8）和干预轮分开算
```

自评落在 `.data/feedback.jsonl`，会连带记下当时是第几轮、哪套策略，所以
「P8 对照轮 vs 干预轮」的分数可以直接对比。

## 说明

- 无真脑电设备时，控制台点「演示（非脑电）」即可跑通整条链路；演示样本一律标注，不当真实数据用。
- 接真设备用**环境变量**切换，不用改代码（详见 `docs/接真设备-Mac.md`）：
  ```bash
  # 1) 先探测：Mac 连上热点 ExpandableEEG AP 后
  .venv/bin/python eeg_service/probe_device.py
  # 2) 按探测结果启动（示例：16 通道 WiFi）
  EEG_BOARD_ID=6 EEG_IP=192.168.4.1 EEG_SAMPLE_RATE=125 .venv/bin/python eeg_service/main.py
  # 3) 单独录一段实验数据
  EEG_BOARD_ID=6 EEG_IP=192.168.4.1 EEG_SECONDS=120 EEG_SUBJECT=s01 \
      .venv/bin/python eeg_service/record_session.py
  ```
- 全部密钥走 `.env`，不要提交到仓库。
