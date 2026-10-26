"""EEG 采集服务：从脑电设备读取信号，判定放松度，实时广播到 bridge。

判定逻辑**不在这个文件里** —— 它和离线回放（replay_service.py）共用
analysis/relax_axis.py 那一份。这样接真设备时，画面行为和你回放那段录好的
数据时是一致的；在这之前两条路各写了一套判定，同一个人、同一段脑电，
现场和回放给出的画面完全不一样。

和回放唯一的差别是平滑方式，而且必须不同（这是本质差别，不是偷懒）：
    现场 —— 只有过去的数据   → 因果平滑（RelaxTracker 内部就是）
    回放 —— 数据早已录好     → 居中平滑（prepare_series 里用）

另外现场必须在开头花 baseline_seconds 秒标定「个人基线」：
每个人的 α/β 绝对水平能差好几倍，拿一个固定数字去套所有人
等于拿别人的尺子量自己。标定期间画面停在中间色，不冒充「你现在很放松」。

默认使用 BrainFlow 合成板（SyntheticBoard），无需真设备即可跑通链路。
接真设备时改环境变量即可，不用改代码：
    EEG_BOARD_ID=6 EEG_IP=192.168.4.1 EEG_IP_PORT=6677 EEG_IP_PROTOCOL=0 \
    DEMO_MODE=0 ./.venv/bin/python eeg_service/main.py

想把原始数据全程存下来，再加一个目录（每 60 秒一个 npz）：
    EEG_RECORD_DIR=.data/rec/2026-10-21

协议号就是 BrainFlow 的 IpProtocolTypes，别记反：
    0 = NO_IP_PROTOCOL（板卡默认，本设备用这个）    1 = UDP            2 = TCP

⚠️ EEG_IP_PORT 是本机（电脑）的监听端口，不是板子的端口。
   板子的网页在 192.168.4.1:80，数据走 BrainFlow 时由电脑在 EEG_IP_PORT 上等它连过来。
   所以这个端口只要本机空闲即可，默认 6677（和 OpenBCI GUI 用的一致）。
   之前默认写 1112、协议写 2 是错的，会导致「端口通了但拿不到数据」。

⚠️ 采样率：板子只在 250Hz 下用标准封包。BrainFlow 对 board 6 标称的 1000Hz
   既不真实（实测 ~250）又会把板子切到不兼容的封包格式，所以这里连着板子后
   会先发一条 EEG_BOARD_COMMAND（默认 "~6" = 250Hz），并把采样率按 250 处理。
   端口和协议拿不准时，先跑 eeg_service/probe_device.py（它会把三种协议依次试一遍），
   把成功那组原样填回来即可。

策略层（strategy/）默认开着：每 EEG_ROUND_SECONDS 秒（默认 20）换一轮氛围，
开局 EEG_CONTROL_ROUNDS 轮（默认 3）固定用 P8 不干预对照。
它只**调制**画面，绝不代替脑电驱动画面 —— 对照那几轮画面照常在跟脑电动。
想退回「纯连续映射」：EEG_STRATEGY=0。
"""
from __future__ import annotations

import asyncio
import json
import math
import os

import numpy as np
import signal
import sys
import time

from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds
from brainflow.data_filter import DataFilter, FilterTypes
from scipy.signal import detrend

# 本文件在 eeg_service/ 下面，而判定逻辑在上一层的 analysis/ 里。
# 把项目根目录塞进 sys.path，才能 import 回放那边用的同一份逻辑。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from analysis.alpha_beta_state import FILTER_BAND, channel_quality  # noqa: E402
# 临床口径指标（IAF / α 不对称对数比 / 复杂度-熵）。
# 这些量需要原始波形，所以只能在采集端的内存窗口里算 —— 落盘的仍然只有特征。
from analysis.eeg_markers import MarkerTracker  # noqa: E402
from analysis.relax_axis import (  # noqa: E402
    RelaxTracker,
    focus_from_bt,
    label_from_relax,
    world_from_relax,
)
# 策略层：八套氛围套餐，只**调制**画面，不代替脑电驱动画面。
# 回放（replay_service.py）用的是同一个引擎、同一张表，所以现场和回放
# 的场景行为是一致的。想关掉：EEG_STRATEGY=0。
from strategy.engine import (  # noqa: E402
    StrategyEngine,
    control_rounds_from_env,
    enabled_from_env,
    round_seconds_from_env,
)
# 原始数据落盘。以脚本方式直跑时是 `import recorder`，
# 被当模块导入时是 `import eeg_service.recorder`，两种都兜住。
try:  # noqa: E402
    from eeg_service.recorder import Recorder
except ImportError:  # noqa: E402
    from recorder import Recorder

# 目标 bridge 地址（广播 EEG 状态）
BRIDGE_WS = os.getenv("BRIDGE_WS", "ws://127.0.0.1:8765/ws/eeg")
# 16 通道原始波形的独立通道：控制台「信号与判据」页那 16 条波形用它。
# 走单独一条 WS，是因为它是 25Hz 的高频小包，混进 2 秒一次的状态流里
# 会把状态消息冲得又大又慢。
WAVE_WS = os.getenv("WAVE_WS", "ws://127.0.0.1:8765/ws/wave/pub")
WAVE_HZ = float(os.getenv("EEG_WAVE_HZ", "25"))     # 每秒推几次
WAVE_FS = int(os.getenv("EEG_WAVE_FS", "125"))      # 显示用采样率（250Hz 抽取 2 倍）

# ---- 判定参数 ----
# 窗口取 4 秒（不是 1 秒）：1 秒的频谱估计太不稳，α 和 β 会糊在一起。
# 这几个默认值和回放保持一致，两边看到的节奏才是同一种节奏。
WINDOW_SECONDS = float(os.getenv("EEG_WINDOW", "4"))
STEP_SECONDS = float(os.getenv("EEG_STEP", "2"))
BASELINE_SECONDS = float(os.getenv("EEG_BASELINE", "60"))
SMOOTH_WINDOWS = int(os.getenv("EEG_SMOOTH", "5"))

# ---- 策略层参数 ----
STRATEGY_ON = enabled_from_env()
ROUND_SECONDS = round_seconds_from_env()
CONTROL_ROUNDS = control_rounds_from_env()

# ---- 设备配置 ----
# 默认合成板跑通闭环；接真设备用环境变量切换，无需改代码：
#   Cyton+Daisy WiFi (16 通道): EEG_BOARD_ID=6 EEG_IP=192.168.4.1
#   Cyton WiFi      ( 8 通道): EEG_BOARD_ID=5 EEG_IP=192.168.4.1
#   Cyton 蓝牙/串口           : EEG_BOARD_ID=0 EEG_SERIAL_PORT=/dev/tty.usbserial-XXXX
# 先用 eeg_service/probe_device.py 探测出可用的板卡 id 与实测采样率。
BOARD_ID = int(os.getenv("EEG_BOARD_ID", str(BoardIds.SYNTHETIC_BOARD.value)))
EEG_IP = os.getenv("EEG_IP", "")
# ⚠️ 这是本机（电脑）的监听端口 —— 不是板子的端口。板子的网页在 192.168.4.1:80。
# 默认 6677，与 OpenBCI GUI 成功时用的端口一致。
EEG_IP_PORT = int(os.getenv("EEG_IP_PORT", "6677"))
# 注意 BrainFlow 的编号：0=不用 IP（板卡默认，本设备用它），1=UDP，2=TCP。
EEG_IP_PROTOCOL = int(os.getenv("EEG_IP_PROTOCOL", "0"))
SERIAL_PORT = os.getenv("EEG_SERIAL_PORT", "")             # 例: "/dev/tty.usbserial-XXXX"
SAMPLE_RATE_FALLBACK = 250
SAMPLE_RATE_OVERRIDE = int(os.getenv("EEG_SAMPLE_RATE", "0"))   # 实测采样率；0=用下面的口径

# WiFi 版 Cyton 的实测口径：必须显式把板子设成 250Hz，包格式才对得上。
# （默认 1000Hz 时 BrainFlow 解析出来的通道会整体错位，数值大到十万级；250Hz 下正常。）
WIFI_CYTON_BOARDS = {
    int(BoardIds.CYTON_WIFI_BOARD.value),
    int(BoardIds.CYTON_DAISY_WIFI_BOARD.value),
}
BOARD_COMMAND = os.getenv(
    "EEG_BOARD_COMMAND", "~6" if BOARD_ID in WIFI_CYTON_BOARDS else "").strip()
WIFI_SAMPLE_RATE = int(os.getenv("EEG_WIFI_SAMPLE_RATE", "250"))

# ---- 演示模式：合成板默认开启，让状态缓慢循环，世界可见地变化 ----
# ⚠️ 这条曲线是编出来的，不含脑电。接真数据时必须 DEMO_MODE=0。
DEMO_MODE = os.getenv("DEMO_MODE", "1") == "1" and BOARD_ID == BoardIds.SYNTHETIC_BOARD.value
DEMO_CYCLE = float(os.getenv("DEMO_CYCLE", "60"))   # 一个循环的秒数

# 合成板的「个人基线」—— relax 与 bands 必须由同一把尺子算出来，
# 否则控制台上把公式和数字并排放，一眼就能看出对不上。
#   基线 α/β = 1.35；基线的 β/θ 由 relax=0.5 时那组频带推出来。
DEMO_BASE_AB = 1.35
DEMO_BASE_BT = (20.0 + 60.0 * 0.5) / DEMO_BASE_AB / (12.0 - 4.0 * 0.5)

# ---- 原始数据落盘 ----
# 设了这个目录，就把每一路每一毫秒都存下来（每 60 秒一个 npz）。
# 不设（默认）= 不存，行为和以前完全一样。试验当天一定要设。
RECORD_DIR = os.getenv("EEG_RECORD_DIR", "").strip()

# 循环锚点：(相位, 放松度) —— 从紧张 → 放松 → 紧张，平滑过渡
_DEMO_KEYS = [(0.0, 0.15), (0.34, 0.95), (0.67, 0.10), (1.0, 0.15)]


def demo_relax(t: float) -> float:
    """按时间给出平滑循环的放松度（0~1），用于无真设备时的画面演示。"""
    ph = (t % DEMO_CYCLE) / DEMO_CYCLE
    for i in range(len(_DEMO_KEYS) - 1):
        p0, r0 = _DEMO_KEYS[i]
        p1, r1 = _DEMO_KEYS[i + 1]
        if p0 <= ph <= p1:
            k = (ph - p0) / (p1 - p0)
            k = k * k * (3 - 2 * k)          # 平滑（smoothstep）
            return r0 + (r1 - r0) * k
    return 0.5


def demo_payload(t: float) -> dict:
    """演示模式的推送包：形状和 RelaxTracker.feed() 的输出保持一致。

    为了让控制台在没有板子时也能演示「频带图 / 不对称 / 专注度」，
    这里按同样的口径编一组自洽的假数（note 里已经明说是编的）。
    真机上这些字段来自 lateralization.metrics_from_segment()。
    """
    relax = demo_relax(t)
    ph = (t % DEMO_CYCLE) / DEMO_CYCLE

    # 五频带：**由放松度反推**，而不是各算各的。
    #   已知 relax → score = 2^(2*(relax-0.5)) → α/β = 基线 × score
    # 这样界面上「α/β → score → relax」这条链子代入数字后必然闭环。
    alpha = 20.0 + 60.0 * relax
    score = 2.0 ** (2.0 * (relax - 0.5))
    beta = alpha / (DEMO_BASE_AB * score)
    # θ 单独摆一点，让 β/θ（专注轴）不至于和放松轴绑死
    theta = 12.0 - 4.0 * relax + 2.5 * math.sin(
        2 * math.pi * (t % (DEMO_CYCLE * 1.3)) / (DEMO_CYCLE * 1.3))
    bands = {
        "delta": round(3.0 + 1.0 * math.sin(2 * math.pi * ph), 4),
        "theta": round(theta, 4),
        "alpha": round(alpha, 4),
        "beta": round(beta, 4),
        "gamma": round(2.0 + 1.0 * ph, 4),
    }
    # 不对称：绕 0 缓慢漂移，演示「偏侧在变小」的观感
    li = 0.18 * math.sin(2 * math.pi * (t % (DEMO_CYCLE * 2)) / (DEMO_CYCLE * 2))
    lr = {
        "li": round(li, 3),
        "imbalance": round(abs(li), 3),
        "left": round(bands["alpha"] * (1 - li / 2), 4),
        "right": round(bands["alpha"] * (1 + li / 2), 4),
        "n_left": 8,
        "n_right": 8,
        "notable": bool(abs(li) >= 0.15),
        "band": "8-13Hz",
    }
    focus = focus_from_bt(bands["beta"] / bands["theta"], DEMO_BASE_BT)
    # 临床指标的演示值：和上面的 lr 自洽（ln右−ln左），IAF 在 9.5~10.5 之间慢慢漂。
    # 同样是编的，engine 字段直接写 demo，免得被当成真算出来的。
    iaf = 10.0 + 0.4 * math.sin(2 * math.pi * ph)
    markers = {
        "ok": True, "engine": "demo",
        "iaf": round(iaf, 2),
        "iaf_band": [round(iaf - 2, 2), round(iaf + 2, 2)],
        "alpha_asym_log": round(math.log(1 + li / 2) - math.log(1 - li / 2), 4)
                           if abs(li) < 1 else 0.0,
        "lzc": round(0.42 + 0.06 * math.sin(2 * math.pi * ph), 4),
        "higuchi_fd": round(1.55 + 0.05 * math.sin(2 * math.pi * ph), 4),
        "sample_entropy": round(0.72 + 0.05 * math.sin(2 * math.pi * ph), 4),
        "perm_entropy": round(0.86 + 0.03 * math.sin(2 * math.pi * ph), 4),
        "spectral_entropy": round(0.51 + 0.04 * ph, 4),
        "iaf_age_s": 0.0, "cx_age_s": 0.0, "compute_ms": 0.0,
    }
    return {
        "t": time.time(),
        "label": label_from_relax(relax),
        "relax": round(relax, 3),
        "calm": round(relax, 3),          # 兼容老字段名
        "focus": round(min(1.0, max(0.0, focus)), 3),
        "bands": bands,
        "lr": lr,
        "bt": round(bands["beta"] / bands["theta"], 3),
        "world_params": world_from_relax(relax),
        "markers": markers,
        "board_id": BOARD_ID,             # 前端靠它标出「这是合成板」
        "note": "DEMO_MODE：这条曲线是编出来的，不含脑电",
    }


# ---- 16 通道原始波形（控制台「信号与判据」页） ----
# 控制台要的是 OpenBCI GUI 那种一条一条的时域波形：16 路、固定 5 秒窗、
# 每路标着量程和「有没有贴顶」。它跟 2 秒一次的状态流是两种东西：
#   * 状态流：慢、大、每 2 秒一包，用来算判据；
#   * 波形流：快、小、每秒 25 包，只用来画线。
# 所以单独走一条 WS（/ws/wave），不塞进状态消息里互相拖累。
WAVE_DECIM = max(1, int(round(250 / max(1, WAVE_FS))))   # 250Hz → 125Hz 取 2
WAVE_MAX_UV = 400.0
DEMO_WAVE_GAIN = float(os.getenv("EEG_DEMO_WAVE_GAIN", "3.0"))
# 没有真板时**一个数都不许往外发**。合成数据只在内部测试时用
# （EEG_DEMO_OUTPUT=1 手动打开），默认关——界面上不能出现任何编造的数字。
DEMO_OUTPUT = os.getenv("EEG_DEMO_OUTPUT", "0") == "1"                                      # 超过就当坏点砍掉，别把梁画穿


def _quant(chunk) -> list:
    """浮点 µV → 整数 µV。波形只要形状对，用整数能把包压小一半。"""
    return [int(round(max(-WAVE_MAX_UV, min(WAVE_MAX_UV, v))))
            for v in chunk.tolist()]


def demo_wave(n_frames: int) -> tuple[list, float]:
    """演示模式的 16 通道波形：由当前 relax 反推的五频带合成。

    真机上是电极电压；这里是「按频带功率反推的正弦叠加 + 电极噪声」，
    只为让曲线在 ±200µV 的格子里长得像个正常的 EEG。note 里已注明是编的。
    """
    now = time.time()
    p = demo_payload(now)
    b = p["bands"]
    amp = {k: math.sqrt(max(v, 0.0)) for k, v in b.items()}
    # 每个频带的中心频率（Hz）—— 用中心频率而不是带宽，画出来才像脑电
    spec = (("delta", 2.0), ("theta", 5.5), ("alpha", 10.0),
            ("beta", 20.0), ("gamma", 38.0))
    out_fs = WAVE_FS
    t0 = now - (n_frames - 1) / out_fs
    tt = np.arange(n_frames) / out_fs + t0
    rng = np.random.default_rng(int(now * 1000) & 0xFFFF)
    chans = []
    for j in range(16):
        y = np.zeros(n_frames, dtype=np.float64)
        for i, (k, f) in enumerate(spec):
            # 每路给不同相位/权重：16 条线不能长得一模一样，否则一眼假
            w = 0.55 + 0.35 * math.sin(j * 1.7 + i * 2.1)
            y += amp[k] * w * np.sin(2 * np.pi * f * tt + j * 0.9 + i * 1.3)
        y += rng.normal(0.0, 2.2 + 0.25 * j, n_frames)   # 电极噪声，越往后越大
        # 真实记录的 α 波幅一般在 10–40µV 上下；按频带功率开方算出来的只有几 µV，
        # 直接画在 ±200µV 的格子里几乎是一条直线。这里给一个固定的显示增益，
        # 让演示波形落在「像真的」的量级（note 里已注明是合成，不是电极电压）。
        y *= DEMO_WAVE_GAIN * (1.0 + 0.02 * j)           # 通道间灵敏度差
        chans.append(_quant(y))
    return chans, now


def board_wave(board, eeg_channels, sampling_rate: int,
               dc: list, n_frames: int) -> tuple[list, float]:
    """真机：取最近一小段原始电压，去掉直流偏置后抽稀成显示帧。

    Cyton 的原始通道带着几十毫伏的直流偏置，不扣掉的话曲线会整体贴在
    格子上沿——OpenBCI GUI 里「Railed 100%」那种样子。这里用慢速 EMA
    估偏置（而不是每段减自己的均值），免得把慢波一起减掉。
    """
    now = time.time()
    n = max(2, int(round(n_frames * sampling_rate / WAVE_FS)))
    data = board.get_current_board_data(n)      # 非破坏性读取，不抢 4 秒窗
    if data.shape[1] < 2:
        return [], now
    chans = []
    for k, ch in enumerate(eeg_channels):
        seg = np.asarray(data[ch, ::WAVE_DECIM], dtype=np.float64)
        if seg.size < 2:
            continue
        m = float(seg.mean())
        # 首帧直接对齐，之后慢慢跟：既不抖，又不会让漂移把曲线顶出格子
        dc[k] = m if dc[k] is None else dc[k] * 0.9 + m * 0.1
        chans.append(_quant(seg - dc[k]))
    return chans, now


async def wave_loop(board, sampling_rate: int, eeg_channels) -> None:
    """把 16 通道波形持续推给桥接；断了自己重连，永不退出。"""
    import websockets

    dc: list = [None] * len(eeg_channels)
    n_frames = max(1, int(round(WAVE_FS / WAVE_HZ)))
    while True:
        try:
            async with websockets.connect(WAVE_WS, max_size=None) as ws:
                print("[eeg] 16 通道波形流已连上桥接", flush=True)
                dc = [None] * len(eeg_channels)
                while True:
                    await asyncio.sleep(1.0 / WAVE_HZ)
                    if DEMO_MODE and not DEMO_OUTPUT:
                        continue
                    if DEMO_MODE:
                        chans, t0 = demo_wave(n_frames)
                    else:
                        chans, t0 = board_wave(board, eeg_channels,
                                               sampling_rate, dc, n_frames)
                    if not chans:
                        continue
                    await ws.send(json.dumps(
                        {"type": "wave", "fs": WAVE_FS, "t": round(t0, 3),
                         "board": int(BOARD_ID), "sim": bool(DEMO_MODE), "ch": chans},
                        ensure_ascii=False, separators=(",", ":")))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[eeg] 波形流断开（{type(e).__name__}: {e}），2 秒后重连…",
                  flush=True)
            await asyncio.sleep(2.0)


def bandpass(seg, sampling_rate: int) -> None:
    """就地带通滤波。和离线用同一个频段（FILTER_BAND），判据才是同一把尺子。

    ⚠️ 必须先去掉直流和慢漂移再滤（实测踩过的坑）：
        干电极的极化偏置可达 -180000 µV，带着它做 IIR 会让滤波器进入
        长达数秒的巨型瞬态 —— 纯合成信号实测，一个干净的 10Hz 正弦
        （std 7.07）叠加 -180000 µV 直流后直接带通，输出 std 冲到 20315，
        一直不收敛；先去掉直流再带通，精确回到 7.06。
        不先去掉，好通道会被这股瞬态整体判成「坏通道」。
    """
    seg[:] = detrend(seg, axis=1)
    for ch in range(seg.shape[0]):
        DataFilter.perform_bandpass(
            seg[ch], sampling_rate, FILTER_BAND[0], FILTER_BAND[1], 4,
            FilterTypes.BUTTERWORTH.value, 0)


def log_payload(payload: dict, tracker: RelaxTracker) -> None:
    label = payload["label"]
    if label == "calibrating":
        print(f"[eeg] 标定基线中 {tracker.calib_progress * 100:3.0f}%"
              f"（请安静坐好，画面暂时停在中间色）", flush=True)
        return
    if label == "invalid":
        print(f"[eeg] 无效      {payload.get('note', '')}"
              f"（场景保持不动）", flush=True)
        return
    w = payload["world_params"]
    pid = payload.get("strategy") or "----"
    extra = ""
    if "focus" in payload:
        extra += f" 专注={payload['focus']:.2f}"
    lr = payload.get("lr")
    if lr:
        extra += f" 偏侧={lr['li']:+.2f}"
    mk = payload.get("markers") or {}
    if mk.get("ok") and mk.get("iaf") is not None:
        extra += f" IAF={mk['iaf']:.2f}Hz"
        if mk.get("lzc") is not None:
            extra += f" LZc={mk['lzc']:.2f}"
    print(f"[eeg] {label:<9} {pid} 放松度={payload['relax']:.2f}{extra}  "
          f"花开={w['bloom']:.2f} 阳光={w['sunlight']:.2f} "
          f"雾={w['fog']:.2f} 水晶={w['crystal']:.2f}", flush=True)


async def stream(board, sampling_rate: int, eeg_channels,
                 tracker: RelaxTracker, engine: StrategyEngine,
                 recorder: Recorder | None = None,
                 markers: MarkerTracker | None = None) -> None:
    """连接桥接并持续推送状态；连接断开时抛异常，由外层重连。"""
    import websockets

    n_samples = int(WINDOW_SECONDS * sampling_rate)

    if DEMO_MODE and not DEMO_OUTPUT:
        # 连上桥接又立刻退出会让桥接反复重连，所以干脆不连，安静待着。
        print("[eeg] 未连接采集板：不产生任何状态数据（演示静默）", flush=True)
        while True:
            await asyncio.sleep(3600)

    async with websockets.connect(BRIDGE_WS) as ws:
        print("[eeg] 已连接桥接，开始推送状态", flush=True)
        while True:
            await asyncio.sleep(STEP_SECONDS)
            data = board.get_current_board_data(n_samples)
            # 先落盘，再判断「够不够一个窗口」——
            # 刚启动的那几秒还没攒满 4 秒，但那些样点同样是数据，不能丢。
            if recorder is not None:
                recorder.write(data)
            if data.shape[1] < n_samples:
                continue

            # 打时间戳：t_win_end = 这个 4 秒窗口最后一个样点到手的时刻。
            # 下游拿它算「从数据到手到界面显示」花了多久（tools/latency_probe.py 读这两个字段）。
            t_win_end = time.time()

            if DEMO_MODE:
                payload = demo_payload(time.time())
            else:
                raw = data[eeg_channels, :]
                seg = raw.copy()
                bandpass(seg, sampling_rate)
                # 每通道的质量判定：用带通后的数据判 —— 干电极的直流偏置不是
                # 「坏」，去掉它之后才谈得上「这一路信号死没死」。判定层内部
                # 用的是同一份数据、同一个 channel_quality，免得界面上显示
                # 「合格」、判定层却说「无效」。
                good = channel_quality(seg)
                # 剩下的全交给共用逻辑：挑好通道 → Welch → α/β → 相对基线
                # → 因果平滑 → 四个世界参数。现场和回放走的是同一个函数。
                payload = tracker.feed(seg, sampling_rate)
                payload["channels"] = [
                    {"i": int(ch),
                     # 原始直流偏置照样报出来：它是看「电极接触好不好」的线索
                     "mean": round(float(raw[k].mean()), 1),
                     "std": round(float(raw[k].std()), 1),
                     "ok": bool(good[k])}
                    for k, ch in enumerate(eeg_channels)
                ]
                payload["board_id"] = BOARD_ID
                payload["fs"] = sampling_rate
                # 临床口径指标：IAF / α 不对称对数比 / 复杂度-熵。
                # 内部按 5s、10s 节流，所以绝大多数窗口只加上一次缓存值，
                # 不会把每窗口的判定耗时（约 1ms）拖起来。
                if markers is not None:
                    try:
                        payload["markers"] = markers.feed(
                            seg, sampling_rate, good=good, lr=payload.get("lr"))
                    except Exception as e:  # noqa: BLE001
                        # 指标算不出来不能影响画面：记下原因，继续跑
                        payload["markers"] = {"ok": False,
                                              "reason": f"{type(e).__name__}: {e}"}
            payload["rec"] = recorder.stats() if recorder is not None else {"on": False}

            # 策略层只调制 world_params；relax / label 一概不动
            # （它们是内部坐标，不对外，也不参与画面）。
            # dt 按窗口步长走，不按墙上时间 —— 轮次必须和「数据时间」对齐。
            out = engine.feed(payload["world_params"], payload["label"],
                              STEP_SECONDS, payload.get("relax"))
            payload["world_params"] = out["world_params"]
            payload.update({k: v for k, v in out.items()
                            if k != "world_params"})
            # 给自评小工具读「现在是第几轮、用的哪套」
            engine.dump()

            payload["t_win_end"] = round(t_win_end, 6)
            payload["t_analysis_ms"] = round((time.time() - t_win_end) * 1000.0, 2)
            payload["win_seconds"] = round(n_samples / float(sampling_rate), 3)

            await ws.send(json.dumps(payload, ensure_ascii=False))
            if out["round_changed"] and engine.banner():
                print("\n[eeg] " + engine.banner() + "\n", flush=True)
            log_payload(payload, tracker)


def _install_signal_handlers() -> None:
    """把 SIGTERM / SIGHUP 转成正常退出。

    桥接点「停止」发的是 SIGTERM，Python 默认行为是立刻死掉，
    finally 不执行 —— 正在录的原始数据就来不及收尾。转成
    KeyboardInterrupt 之后，asyncio.run 会取消任务并跑完 finally。
    """
    def _raise(signum, frame):
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _raise)
        except (ValueError, OSError):
            pass


async def run() -> None:
    BoardShim.disable_board_logger()
    params = BrainFlowInputParams()
    if SERIAL_PORT:
        params.serial_port = SERIAL_PORT
    if EEG_IP:
        params.ip_address = EEG_IP
        params.ip_port = EEG_IP_PORT
        params.ip_protocol = EEG_IP_PROTOCOL
    board = BoardShim(BOARD_ID, params)
    board.prepare_session()
    # 必须在开始取流之前把板子设到 250Hz：WiFi 版在 1000Hz 下用的是另一套封包，
    # BrainFlow 会按 1000Hz 的格式去解，结果通道整体错位（表现为数值十万级、
    # 第 1 路是包序号、最后两路恒为 -187500）。
    if BOARD_COMMAND:
        try:
            board.config_board(BOARD_COMMAND)
            print(f"[eeg] 已设置板卡：{BOARD_COMMAND}"
                  f"（{'250Hz' if BOARD_COMMAND == '~6' else BOARD_COMMAND} 标准封包）",
                  flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[eeg] ⚠️ 设置板卡失败（{type(e).__name__}: {e}），"
                  "采样率可能不对，凑合着用。", flush=True)
    board.start_stream()
    eeg_channels = BoardShim.get_eeg_channels(BOARD_ID)
    try:
        sampling_rate = BoardShim.get_sampling_rate(BOARD_ID)
    except Exception:
        sampling_rate = SAMPLE_RATE_FALLBACK
    if BOARD_ID in WIFI_CYTON_BOARDS:
        # BrainFlow 对 WiFi 版标称 1000Hz，与实测（约 250）不符；以实测口径为准。
        # 不覆盖的话后面所有滤波、窗口长度都会按 1000Hz 算，等于全部跑偏 4 倍。
        sampling_rate = WIFI_SAMPLE_RATE
    if SAMPLE_RATE_OVERRIDE > 0:
        sampling_rate = SAMPLE_RATE_OVERRIDE

    _install_signal_handlers()

    # 判定器只建一次，跨断线重连保留 —— 基线是「这个人的」，不是「这条连接的」
    tracker = RelaxTracker(window=WINDOW_SECONDS, step=STEP_SECONDS,
                           smooth=SMOOTH_WINDOWS,
                           baseline_seconds=BASELINE_SECONDS)
    # 策略引擎同理只建一次：断线重连不该把轮次打回第 1 轮。
    engine = StrategyEngine(round_seconds=ROUND_SECONDS,
                            control_rounds=CONTROL_ROUNDS,
                            enabled=STRATEGY_ON)
    # 临床指标器也只建一次：IAF 是「这个人的」特征，重连不该重算。
    markers = MarkerTracker(
        iaf_every=float(os.getenv("EEG_IAF_EVERY", "5")),
        complexity_every=float(os.getenv("EEG_CX_EVERY", "10")))

    print(f"[eeg] 板卡 {BOARD_ID} 启动，采样率 {sampling_rate}Hz，"
          f"通道 {eeg_channels}", flush=True)
    print(f"[eeg] 窗口 {WINDOW_SECONDS:g}s / 步长 {STEP_SECONDS:g}s / "
          f"平滑 {SMOOTH_WINDOWS} 个窗口", flush=True)
    if STRATEGY_ON:
        print(f"[eeg] 策略层 {ROUND_SECONDS:g}s 一轮，开局 {CONTROL_ROUNDS} 轮固定 "
              f"P8 不干预对照（那几轮画面照常在跟脑电动，是对的）", flush=True)
    else:
        print("[eeg] 策略层已关闭（EEG_STRATEGY=0）—— 只有脑电 → 四键", flush=True)
    if DEMO_MODE:
        print("[eeg] ⚠️ DEMO_MODE=1：画面走的是编好的曲线，不含脑电。"
              "接真数据请设 DEMO_MODE=0。", flush=True)
    else:
        print(f"[eeg] 开头 {BASELINE_SECONDS:g} 秒在标定你的个人基线，"
              "请安静坐好、别乱动。标定完画面才会开始跟你走。", flush=True)

    # 录制：只在设了 EEG_RECORD_DIR 时才建。建不起来就退化成「不录」，
    # 绝不能让「存不了盘」把「采不到数据」一起拖下水。
    recorder = None
    if RECORD_DIR:
        try:
            recorder = Recorder(
                RECORD_DIR, BOARD_ID, sampling_rate, eeg_channels,
                BoardShim.get_timestamp_channel(BOARD_ID))
            print(f"[eeg] 原始数据将存到 {os.path.abspath(RECORD_DIR)}"
                  f"（每 {60} 秒一个 npz）", flush=True)
        except Exception as e:
            print(f"[eeg] ⚠️ 录制没起来（{type(e).__name__}: {e}），"
                  "本次不存盘，采集照常。", flush=True)

    # 波形流是独立协程：它挂了不影响判定，判定断了它照推（控制台能看出是画面问题）。
    asyncio.create_task(wave_loop(board, sampling_rate, eeg_channels))

    try:
        # 断线自动重连：桥接重启也不会让本服务退出
        while True:
            try:
                await stream(board, sampling_rate, eeg_channels, tracker,
                             engine, recorder, markers)
            except Exception as e:
                print(f"[eeg] 与桥接断开（{type(e).__name__}: {e}），3 秒后重连…",
                      flush=True)
                await asyncio.sleep(3)
    finally:
        if recorder is not None:
            recorder.close()
        board.stop_stream()
        board.release_session()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[eeg] 已停止采集。")
