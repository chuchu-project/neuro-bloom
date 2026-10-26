# ExpandableEEG 在 MacBook 上的接入与实验数据采集指南

> 适用：macOS（Apple Silicon / Intel）、ExpandableEEG 8/16 通道采集板、出厂默认 WiFi AP 模式。
> 本指南不依赖 OpenBCI GUI，也不依赖 Windows 工具。

---

## 0. 先澄清：商家说的“串口助手”是什么

说明书整本是按 **Windows** 写的（`ExpandableEEG_Config.exe`、`COM8`、`boardController.dll`）。
“串口助手”在这套流程里只是**辅助工具**，只用于两件事：

1. **蓝牙模式**下，Windows 把设备映射成一个 `COM` 口，需要串口工具去连那个口；
2. 查看设备**开机时从串口输出的 IP 地址**（说明书标注波特率 `115200`）。

它**不是**采集脑电数据的主工具。用 BrainFlow 直接读数据时，连串口助手都不需要。

Mac 上的对应关系：

| 说明书（Windows） | Mac 上的做法 |
| --- | --- |
| `ExpandableEEG_Config.exe` 配置通信方式 | ❌ 无法运行。**出厂默认就是 WiFi AP，不改也能用**；要改需借 Windows 或虚拟机 |
| OpenBCI GUI（商家修改版 openbci32） | ⚠️ 官方有 macOS 版，但“支持 32 通道”的修改版只有 Windows exe |
| 串口助手（SSCOM 等） | ✅ 可用已安装的 **Serial Studio Pro**，或系统自带 `screen` |
| BrainFlow 读数据 | ✅ **推荐**，Mac 原生可用，本项目已集成 |

---

## 1. 三条连接路线对比

| 方式 | BrainFlow 板卡 id | Mac 可行性 | 说明 |
| --- | --- | --- | --- |
| **WiFi AP**（出厂默认） | `6`(16ch) / `5`(8ch) | ✅ **推荐** | 板子发热点，Mac 直连，不需要任何 Windows 工具 |
| WiFi client | `6` / `5` | ⚠️ 可行 | 需先用 Windows 工具改模式；之后用设备输出的 IP |
| 蓝牙 | `0` | ⚠️ 一般 | 续航好，但 macOS 蓝牙串口不稳定；设备会出现在 `/dev/tty.*` |

> 32 通道：BrainFlow 需要 `BoardId=56` 并替换 `boardController.dll`（Windows 专用），
> Mac 上没有对应 `.dylib`，**32 通道在 Mac 上暂不可行**。

---

## 2. 推荐路线：WiFi AP + BrainFlow（操作步骤）

### 步骤 1：给采集板开机
装上电池/接通电源，开机。**不需要**插 Type-C 数据线。

### 步骤 2：Mac 连接采集板热点
- 点右上角 Wi‑Fi → 选择 **`ExpandableEEG AP`**
- 密码：**`abcd1234`**
- 连上后 Mac 暂时没有外网（MacBook 无网口属正常现象），不影响采集

### 步骤 3：探测设备，确认板卡 id 与采样率
```bash
cd NeuroBloom
.venv/bin/python eeg_service/probe_device.py
```
脚本会自动依次尝试 `CYTON_DAISY_WIFI(16ch, id=6)` 与 `CYTON_WIFI(8ch, id=5)`，
并在 TCP / UDP 之间重试，最后打印：

- 可用板卡 id
- EEG 通道号列表
- **实测采样率**（很关键，见下）
- 各通道峰峰值
- 存一份原始数据到 `NeuroBloom/data/probe_*.csv`

### 步骤 4：用真设备驱动 NeuroBloom（替代合成板）
```bash
EEG_BOARD_ID=6 EEG_IP=192.168.4.1 EEG_SAMPLE_RATE=125 \
    .venv/bin/python eeg_service/main.py
```
把 `EEG_BOARD_ID` / `EEG_SAMPLE_RATE` 换成步骤 3 探测出的实际值。

### 步骤 5：单独录制实验数据
```bash
EEG_BOARD_ID=6 EEG_IP=192.168.4.1 EEG_SAMPLE_RATE=125 \
EEG_SECONDS=120 EEG_SUBJECT=s01 \
    .venv/bin/python eeg_service/record_session.py
```
- 数据存到 `NeuroBloom/data/eeg_<被试>_<时间>.csv`
- 每行一个采样点，第 1 列 `timestamp`，其余列为通道
- `Ctrl-C` 可提前结束，已采数据会保留

---

## 3. 环境变量速查

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `EEG_BOARD_ID` | `-1` | `6`=Cyton+Daisy WiFi(16ch)、`5`=Cyton WiFi(8ch)、`0`=串口/蓝牙、`-1`=合成板 |
| `EEG_IP` | 空 | AP 模式填 `192.168.4.1`；client 填设备输出的 IP |
| `EEG_IP_PORT` | `3` | 说明书示例端口 |
| `EEG_IP_PROTOCOL` | `1` | `1`=TCP、`0`=UDP |
| `EEG_SERIAL_PORT` | 空 | 蓝牙/串口模式，例 `/dev/tty.usbserial-XXXX` |
| `EEG_SAMPLE_RATE` | `0` | **实测采样率**；`0` 表示用板卡标称值 |
| `EEG_SECONDS` | `60` | `record_session.py` 采集秒数 |
| `EEG_SUBJECT` | `sub` | 被试/会话标识，参与文件名 |

> ⚠️ **务必设置 `EEG_SAMPLE_RATE`**：BrainFlow 对 WiFi 板卡标称的采样率是 `1000 Hz`，
> 与设备实际输出（通常 125 或 250 Hz）不一致，不设置会导致频谱分析频率轴错误。

---

## 4. 常见问题

**Q：连上热点后打不开网页？**
A：正常。AP 模式下 Mac 走的是采集板的局域网，没有外网。需要上网时切回原 Wi‑Fi。

**Q：`probe_device.py` 所有候选都连不上？**
A：依次检查 ① 板子已开机且红灯/指示灯正常；② Mac 确实连的是 `ExpandableEEG AP`；
③ 板子是否被改成过 client 模式（若是，`EEG_IP` 要填设备输出的 IP，可用 Serial Studio
连接串口、波特率 115200 查看）。

**Q：还是想用图形界面看波形？**
A：可下载 OpenBCI GUI 官方 macOS 版（[GitHub Releases](https://github.com/OpenBCI/OpenBCI_GUI/releases)，
选 `*_macosx.dmg`），选 `Cyton → WiFi → IP 192.168.4.1`。但本项目用 BrainFlow 已足够，
且 GUI 无法读 32 通道的商家修改版特性。

**Q：16 通道到底该用 6 还是 5？**
A：以 `probe_device.py` 的实测结果为准。理论上 16 通道对应 Cyton+Daisy（id=6），
但设备固件的呈现方式可能不同，本机实测才是准的。

---

## 5. 验收清单

- [ ] Mac 已连上 `ExpandableEEG AP`
- [ ] `probe_device.py` 打印出可用板卡 id 与实测采样率
- [ ] `data/probe_*.csv` 有数据，手指刷电极时通道峰峰值有明显变化
- [ ] `record_session.py` 能生成带表头的 CSV
- [ ] `main.py` 用真设备启动后，bridge 端能看到实时状态推送