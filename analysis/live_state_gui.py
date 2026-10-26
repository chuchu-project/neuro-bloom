#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
实时脑电 → 实时判断「放松 / 中间 / 紧张」→ 窗口实时变色

★ 重要：这个程序不联网、不经过任何大模型、不需要你复制粘贴任何数据。
  它就是老老实实读设备、本地算、本地显示。网络断了它照样跑。

数据源两种（任选其一，都不需要人工搬运）：

  ① 直连设备（最理想）
     python analysis/live_state_gui.py --source board --board daisy_wifi
     支持：cyton_wifi / daisy_wifi / ganglion_wifi / synthetic（模拟，用来先验证）

  ② 盯住一个正在变长的 CSV 文件（设备连不上时的备用通道）
     python analysis/live_state_gui.py --source csv --file ~/Desktop/OpenBCI-RAW-xxx.csv
     原理：OpenBCI GUI 一边录，我们一边读它新写进来的行 —— 延迟只有一两秒。
     这不是"复制粘贴"，是真的实时跟随。

界面：放松 → 浅绿；中间 → 浅蓝；紧张 → 浅红；标定中 / 信号无效 → 浅灰

判定逻辑**不在这个文件里** —— 它统一放在 analysis/relax_axis.py，
和现场采集（eeg_service/main.py）、离线回放（eeg_service/replay_service.py）
用的是同一个 RelaxTracker。这里只负责「把最新的数据窗口喂进去、
把结果画出来」。

为什么要把判定挪走：这个文件以前自带一套（「相对基线 + 最近 N 次多数票」），
和离线那套（单轴放松度 + 因果平滑）不是一个判据 ——
结果是同一个人、同一段脑电，两条路给出的画面不一样。
「怎么算放松度」这件事只该有一个答案，所以那套判定已经被删掉了。

为什么要「标定」：α/β 的绝对值因人而异（实测同一个人不同时段能差 3 倍），
先让程序看你自己安静坐 60 秒（默认，和现场链路一致），把那段当你的基准，
之后只跟自己的基准比。不标定的话 α/β 恒大于 1，窗口会永远绿色。

其他有用的开关：
    --headless            不开窗口，只在终端打印（用来验证管线）
    --duration 10         配合 --headless，跑 10 秒后自动停
    --window 4            判定窗口秒数
    --step 2              每隔几秒挪一个新窗口（判定节奏）
    --refresh 0.5         界面每 0.5 秒重画一次（不会因此重算频谱）
    --baseline 60         标定多少秒（0 = 不标定）
    --span 1.0            放松度灵敏度：1.0 = α/β 翻倍算完全放松，
                          0.5 = 1.41 倍就算（越小越灵敏）
    --smooth 3            放松度取最近 N 个窗口的均值（防止颜色乱闪）
    --speed 4             倍速（仅 --source csv 回放已完成的文件时有用）
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import threading
import time

import numpy as np

# ---- 复用已有的判断逻辑与配色，绝不重复实现一遍 ----
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # 项目根：relax_axis 只能按包名导入（见下）
for _p in (HERE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import state_gui as sgui             # noqa: E402  配色和字体

# 判定的唯一来源。注意必须按「包」导入（analysis.relax_axis）：
# relax_axis.py 内部用的是 `from .alpha_beta_state import ...` 这种相对导入，
# 直接 import relax_axis 会报 "attempted relative import with no known parent"。
from analysis.relax_axis import (    # noqa: E402
    RELAX_SPAN,
    RelaxTracker,
    label_from_relax,
    relax_from_score,
    ratio_from_segment,
    scene_words,
)
from analysis.alpha_beta_state import MIN_GOOD_CHANNELS  # noqa: E402

COLOR_WAIT = "#e9e9ef"       # 还没数据 / 标定中：浅灰
FG_WAIT = "#3a3a45"
COLOR_MID = "#e3ecfa"        # 中间（不进不退）：浅蓝
FG_MID = "#1c3a63"
COLOR_ERROR = "#ffe0e0"      # 出错：浅红
FG_ERROR = "#5a1414"

# 放松度的三个标签 → 中文 + 配色。
# 为什么中间要单独给一个颜色：以前只有「放松/紧张」两种，判不准的时候
# 只能硬塞进其中一边；而「没感觉」和「信号坏了」是两件事，
# 灰色已经被「标定中/无效」占了，所以中间用蓝色。
_ZH = {"relaxed": "放松", "neutral": "中间", "tense": "紧张"}
_STATE_COLOR = {
    "放松": (sgui.COLOR_RELAX, sgui.FG_RELAX),
    "中间": (COLOR_MID, FG_MID),
    "紧张": (sgui.COLOR_TENSE, sgui.FG_TENSE),
}


# ==================================================================
# 环形缓冲：只保留最近若干秒的数据
# ==================================================================
class RingBuffer:
    """线程安全的小缓冲区：新数据挤进来，旧的挤出去。"""

    def __init__(self, channels: int, capacity: int):
        self.capacity = capacity
        self.channels = channels
        self.buf = np.zeros((channels, capacity), dtype=np.float64)
        self.count = 0                     # 累计写入多少采样点
        self.lock = threading.Lock()

    def write(self, block: np.ndarray) -> None:
        """block 形状 (通道数, 采样点数)"""
        if block.size == 0:
            return
        if block.shape[0] != self.channels:      # 通道数变了就重开
            self.channels = block.shape[0]
            self.buf = np.zeros((self.channels, self.capacity), dtype=np.float64)
            self.count = 0
        with self.lock:
            m = block.shape[1]
            if m >= self.capacity:
                self.buf[:, :] = block[:, -self.capacity:]
                self.count += self.capacity
                return
            self.buf[:, :-m] = self.buf[:, m:]      # 左移
            self.buf[:, -m:] = block
            self.count += m

    def last(self, n: int) -> np.ndarray | None:
        n = min(n, self.capacity)
        with self.lock:
            if self.count < n:
                return None
            return self.buf[:, -n:].copy()

    def resize(self, channels: int, capacity: int) -> None:
        """
        等设备就绪、知道真实采样率和通道数之后，重新开一块大小合适的缓冲。
        为什么要这样做：WiFi 板（Cyton/Daisy）跑 1000 Hz，而串口板只有 250 Hz，
        一开始不知道是哪块，只能先按大的开，知道之后再调准。
        """
        with self.lock:
            if channels == self.channels and capacity == self.capacity:
                return
            self.channels = channels
            self.capacity = capacity
            self.buf = np.zeros((channels, capacity), dtype=np.float64)
            self.count = 0          # 刚就绪时数据本来就少，直接重开更干净


# ==================================================================
# 数据源 ①：直连设备
# ==================================================================
class BoardSource(threading.Thread):
    """用 BrainFlow 直连 OpenBCI 设备，持续把数据丢进环形缓冲。"""

    def __init__(self, ring: RingBuffer, board_id: int, ip: str,
                 port: int, protocol: int, poll: float = 0.2,
                 stall_timeout: float = 10.0):
        super().__init__(daemon=True)
        self.ring = ring
        self.board_id = board_id
        self.ip = ip
        self.port = port
        self.protocol = protocol
        self.poll = poll
        self.stall_timeout = stall_timeout
        self.error: str | None = None
        self.ready = threading.Event()
        self._stop = threading.Event()
        self.sfreq = None
        self.n_ch = None

    def stop(self):
        self._stop.set()

    def run(self):
        from brainflow.board_shim import BoardShim, BrainFlowInputParams
        from brainflow.utils import LogLevels

        # BrainFlow 默认会把它收到的参数以 JSON 形式打到 stdout，
        # 刷屏且容易盖住我们自己的输出，这里直接关掉。
        BoardShim.set_log_level(LogLevels.LEVEL_OFF)

        params = BrainFlowInputParams()
        if self.ip:
            params.ip_address = self.ip
        if self.port:
            params.ip_port = self.port
        if self.protocol:
            params.ip_protocol = self.protocol
        params.timeout = 15

        board = BoardShim(self.board_id, params)
        try:
            board.prepare_session()
        except Exception as e:
            self.error = (f"连接设备失败：{e}\n"
                          f"  → 先跑一下自检：python analysis/check_board.py")
            return

        self.sfreq = float(BoardShim.get_sampling_rate(self.board_id))
        eeg_ch = BoardShim.get_eeg_channels(self.board_id)
        self.n_ch = len(eeg_ch)

        # 起流失败必须抓住 —— 否则这个线程会悄无声息地死掉，
        # 界面上只会永远停在「等待数据…」，你根本不知道哪里错了。
        # （OpenBCI GUI 占着设备时，报的正是 "Failed to start data stream"。）
        try:
            board.start_stream()
        except Exception as e:
            self.error = (
                f"设备连上了，但数据流启动失败：{e}\n"
                f"  → 最常见原因：OpenBCI GUI 还开着，它占住了板子，先 ⌘Q 退出它。\n"
                f"  → 其次：板子电池没电 / 电源开关没拨到 PC；WiFi Shield 的 "
                f"拨码开关或天线没插好。")
            try:
                board.release_session()
            except Exception:
                pass
            return

        self.ready.set()
        last_recv = time.time()
        try:
            while not self._stop.is_set():
                data = board.get_board_data()        # 取走并清空板子缓存
                if data.shape[1]:
                    # OpenBCI 出来的就是 µV；α/β 是比值，单位不影响判定
                    self.ring.write(data[eeg_ch, :])
                    last_recv = time.time()
                elif time.time() - last_recv > self.stall_timeout:
                    # 连上了、流也起了，但一直没数据 —— 这种「假活着」最坑人
                    self.error = (
                        f"已经 {self.stall_timeout:.0f} 秒没有收到任何采样点了。\n"
                        f"  → 板子可能掉线、没电，或者刚被其他程序抢走。\n"
                        f"  → 检查板子电源指示灯；重新跑一次自检："
                        f"python analysis/check_board.py")
                    break
                time.sleep(self.poll)
        finally:
            try:
                board.stop_stream()
            except Exception:
                pass
            try:
                board.release_session()
            except Exception:
                pass


# ==================================================================
# 数据源 ②：盯住正在变长的 CSV
# ==================================================================
class CsvTailSource(threading.Thread):
    """
    盯着一个 CSV 文件，谁往里追加新行，我们就读新行。

    适配 OpenBCI GUI 录出来的 CSV：
        第 1 行是表头（含 "EXG Channel 0" 之类）
        第 2 行是单位（uV），不是数字，自动跳过
    """

    def __init__(self, ring: RingBuffer, path: str, sfreq: float,
                 col_names: list[str] | None = None,
                 poll: float = 0.3, scale: float = 1.0):
        super().__init__(daemon=True)
        self.ring = ring
        self.path = os.path.expanduser(path)
        self.sfreq = sfreq
        self.col_names = col_names
        self.poll = poll
        self.scale = scale
        self.error: str | None = None
        self.ready = threading.Event()
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        import csv as _csv

        # 等文件出现（录制的常用做法是先开程序、后开录制）
        waited = 0.0
        while not os.path.exists(self.path):
            if self._stop.is_set():
                return
            if waited > 120:
                self.error = f"等了 2 分钟，文件还没出现：{self.path}"
                return
            self.ready.set()
            time.sleep(0.5)
            waited += 0.5

        cols: list[int] | None = None
        try:
            with open(self.path, "r", newline="", errors="ignore") as f:
                header_line = f.readline()
                header = next(_csv.reader([header_line]), [])
                # 自动找脑电列：优先含 "EXG"，其次含 "eeg" / "ch"
                idx = [i for i, name in enumerate(header)
                       if "exg" in name.lower() or "eeg" in name.lower()]
                if self.col_names:
                    want = [c.strip().lower() for c in self.col_names]
                    idx = [i for i, name in enumerate(header)
                           if name.strip().lower() in want]
                cols = idx or None

                if cols:
                    print(f"[CSV] 表头识别到 {len(cols)} 个脑电列："
                          f"{[header[i].strip() for i in cols]}")
                else:
                    print("[CSV] 没认出表头里的脑电列，就按「除第1列外全是脑电」处理")

                self.ready.set()

                while not self._stop.is_set():
                    line = f.readline()
                    if not line:
                        time.sleep(self.poll)
                        continue
                    fields = next(_csv.reader([line]), [])
                    if len(fields) < 2:
                        continue
                    try:
                        # 第 2 行单位说明（uV）会在这里被挡掉
                        row = [float(x) for x in fields]
                    except ValueError:
                        continue

                    values = [row[i] for i in cols] if cols else row[1:]
                    self.ring.write(np.asarray(values, dtype=np.float64)
                                    .reshape(-1, 1) * self.scale)
        except Exception as e:
            self.error = f"读 CSV 出错：{e}"


# ==================================================================
# 数据源 ③：盯住 OpenBCI GUI 正在写的 BDF
# ==================================================================
class BdfTailSource(threading.Thread):
    """
    OpenBCI GUI 录下来的是 BDF，不是 CSV。BDF 是定长记录的二进制文件，
    一「记录」通常正好 1 秒 —— 所以可以按记录边界一小段一小段地读，
    做到真正的边录边看。

    做法：反复重新打开文件、只读新增的那一段。
    为什么要这样绕：BDF 文件头里写着「一共有多少条记录」，文件在长，
    头里的数字却一直是旧的。每次重新解析一次头（很便宜，只读几 KB），
    再只读新增的采样点，就能绕过这个矛盾 —— 而且不用自己手写
    24 位小端解码，那活儿交给 MNE 更保险。
    """

    def __init__(self, ring: RingBuffer, path: str,
                 poll: float = 0.5, stall_timeout: float = 15.0):
        super().__init__(daemon=True)
        self.ring = ring
        self.path = os.path.expanduser(path)
        self.poll = poll
        self.stall_timeout = stall_timeout
        self.error: str | None = None
        self.ready = threading.Event()
        self._stop = threading.Event()
        self.sfreq = None
        self.n_ch = None
        self._picks: list[str] = []
        self._pos = 0                 # 已经读掉多少采样点

    def stop(self):
        self._stop.set()

    def run(self):
        import mne

        # 等文件出现（很常见的顺序是：先开 GUI，后点录制）
        waited = 0.0
        while not os.path.exists(self.path):
            if self._stop.is_set():
                return
            if waited > 120:
                self.error = (f"等了 2 分钟也没等到这个文件：{self.path}\n"
                              f"  → 确认 OpenBCI GUI 里已经点了「开始录制」")
                return
            time.sleep(0.5)
            waited += 0.5

        last_growth = time.time()
        first = True
        while not self._stop.is_set():
            try:
                raw = mne.io.read_raw_bdf(self.path, preload=False,
                                          verbose="ERROR")
            except Exception as e:
                # 文件可能正在写、头还没写完，稍后重试
                if time.time() - last_growth > self.stall_timeout:
                    self.error = (f"{self.stall_timeout:.0f} 秒没能从 BDF 里读出新数据。\n"
                                  f"  最后一次报错：{e}\n"
                                  f"  → OpenBCI GUI 那边可能已经停了录制。")
                    return
                time.sleep(self.poll)
                continue

            if self.sfreq is None:
                self.sfreq = float(raw.info["sfreq"])
                eeg = [c for c in raw.ch_names
                       if c.lower().startswith("eeg") or "exg" in c.lower()]
                self._picks = eeg or [c for c in raw.ch_names
                                      if "accel" not in c.lower()]
                self.n_ch = len(self._picks)
                print(f"[BDF] 识别到 {self.n_ch} 个脑电通道，"
                      f"{self.sfreq:.0f} Hz")
                self.ready.set()

            n = raw.n_times
            if first:
                # 第一次只回头拿最近 30 秒，没必要把整份历史都读进来
                self._pos = max(0, n - int(30 * self.sfreq))
                first = False

            if n > self._pos:
                seg = raw.get_data(picks=self._picks,
                                   start=self._pos, stop=n) * 1e6
                self.ring.write(seg)
                self._pos = n
                last_growth = time.time()
            elif time.time() - last_growth > self.stall_timeout:
                self.error = (f"这个 BDF 已经 {self.stall_timeout:.0f} 秒没有新增内容了。\n"
                              f"  → OpenBCI GUI 那边大概已经停止录制了；"
                              f"在 GUI 里重新点一下录制就能继续。")
                return
            time.sleep(self.poll)


# ==================================================================
# 实时判断器 —— 只是 relax_axis.RelaxTracker 的一层「翻译」
# ==================================================================
class LiveJudge:
    """把 relax_axis.RelaxTracker 的输出，翻译成界面要的字段。

    这里**不再自己算判定**。以前这个类自带一套（相对基线 + 多数票），
    和离线回放那套不是一个判据，导致同一个人同一段脑电两条路画面不同。
    现在判定的唯一来源是 analysis/relax_axis.py，两类共用：
        · 现场（这里 / eeg_service/main.py）—— RelaxTracker，因果平滑
        · 离线（replay_service.py）—— pick_baseline + 居中平滑
    两边唯一该有的差别是平滑方式，而那由 relax_axis 内部保证。
    """

    def __init__(self, window: float, baseline_seconds: float, smooth: int,
                 step: float = 2.0, span: float = RELAX_SPAN):
        self.window = float(window)
        self.step = float(step)
        self.tracker = RelaxTracker(window=self.window, step=self.step,
                                    smooth=smooth, relax_span=float(span),
                                    baseline_seconds=baseline_seconds)
        self._last: dict | None = None
        self._next_at = 0.0

    @property
    def baseline(self):
        return self.tracker.baseline

    def evaluate(self, ring: RingBuffer, sfreq: float, elapsed: float):
        n = int(self.window * sfreq)
        seg = ring.last(n)
        if seg is None:
            return None                      # 数据还不够一个窗口

        # 界面每 0.5 秒重画一次，而判定窗口是 4 秒、每 2 秒才挪一格 ——
        # 所以这里要节流。不节流的后果不只是白算 4 倍（4 秒 @1000Hz 的频谱
        # 每秒算两遍），更糟的是「标定已经过了多久」会算快 4 倍：
        # 30 秒的标定 7 秒就"完成"了，基线的中位数只由几个窗口决定。
        if self._last is not None and elapsed < self._next_at:
            return self._last

        self._next_at = elapsed + self.step
        self._last = self._to_result(self.tracker.feed(seg, sfreq), seg, sfreq)
        return self._last

    def _to_result(self, out: dict, seg, sfreq: float) -> dict:
        label = out["label"]
        # 不下结论时把原因写进「判定」列。以前一律写「放松/紧张」，
        # 标定期和坏窗都硬塞一边，事后翻 CSV 根本看不出哪几行不算数。
        plain = {"invalid": "无效", "calibrating": "标定中"}.get(label, "—")
        res = {
            # 注意：RelaxTracker 里这两个字段叫 alpha_ratio / beta_ratio，
            # 但装的其实是 α、β 的**功率**（不是比值）—— 字段名是它那边的历史
            # 遗留，别被名字骗了。这里沿用界面原来的键名 alpha / beta。
            "alpha": float(out.get("alpha_ratio", float("nan"))),
            "beta": float(out.get("beta_ratio", float("nan"))),
            "ratio": None, "relax": None, "score": None,
            "state": plain, "raw_state": plain, "scene": "",
            "baseline": self.tracker.baseline,
            "progress": self.tracker.calib_progress,
            "invalid": label == "invalid",
            "calibrating": label == "calibrating",
            "n_good": int(out.get("n_good", 0)),
        }
        if label in ("invalid", "calibrating"):
            # 标定期 RelaxTracker 只回「不下结论」，不带功率 —— 但标定那几十个
            # 窗口本身就是数据（阶段实验的「基线」段就靠它），得补算一次，
            # 否则存出来的 CSV 会缺一列。补算只在标定期发生，代价可忽略。
            if label == "calibrating":
                r, _n, a, b = ratio_from_segment(seg, float(sfreq))
                res.update(alpha=float(a), beta=float(b), ratio=r)
            return res

        res["score"] = float(out["score"])
        res["relax"] = float(out["relax"])
        res["ratio"] = float(out["alpha_ratio"]) / float(out["beta_ratio"])
        # 平滑之后的判定才是结论；未平滑的那个只在界面上给操作者看
        # 「这一窗单独看是什么」—— 两者差太多说明信号在抖。
        res["state"] = _ZH[label]
        res["raw_state"] = _ZH[label_from_relax(relax_from_score(res["score"]))]
        res["scene"] = scene_words(res["relax"])
        return res


# ==================================================================
# 边跑边存：把每个窗口的结果落成 CSV
# ==================================================================
class WindowRecorder:
    """
    为什么一定要存：
        判定「准不准」以后要对答案。对答案需要两样东西 ——
        ① 程序当时算出的 α/β 和判定（就是这里存的）
        ② 你当时的主观感受（自评 1~10，事后补一列）
        两者对齐才能算命中率。不存的话，这一趟就白采了。
    """

    HEADER = ["已运行秒", "时刻", "α功率", "β功率", "α/β",
              "相对基线", "判定", "未平滑判定", "个人基线", "标定中",
              "放松度", "场景"]
    # 上面 10 列是原来的列，位置一个都没动 —— 后面两列是新加的
    # （放松度 0~1、场景人话），万一有脚本按列号读过老 CSV 也不会错位。

    def __init__(self, path: str):
        self.path = os.path.expanduser(path)
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.f = open(self.path, "w", newline="", encoding="utf-8-sig")
        self.w = csv.writer(self.f)
        self.w.writerow(self.HEADER)
        self.f.flush()
        self.n = 0

    @staticmethod
    def _num(v, fmt: str = "{:.3f}") -> str:
        """None / NaN 一律留空 —— 标定期还没有「相对基线」这种东西，
        写个 0 会被人当成真值读走。"""
        if v is None:
            return ""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return ""
        return "" if f != f else fmt.format(f)      # f != f 就是 NaN

    def write(self, elapsed: float, res: dict) -> None:
        self.w.writerow([
            f"{elapsed:.1f}",
            time.strftime("%H:%M:%S"),
            self._num(res["alpha"]), self._num(res["beta"]),
            self._num(res["ratio"]), self._num(res["score"]),
            res["state"], res["raw_state"],
            self._num(res["baseline"], "{:.3f}"),
            "是" if res["calibrating"] else "",
            self._num(res["relax"]), res["scene"],
        ])
        self.n += 1
        self.f.flush()          # 每秒才两行，每次都刷盘；突然断电也只丢最后一行

    def close(self) -> None:
        try:
            self.f.close()
        except Exception:
            pass


# ==================================================================
# 界面
# ==================================================================
class LiveWindow:
    def __init__(self, root, ring, judge, sfreq, refresh_ms,
                 baseline_seconds, topmost, recorder=None, src=None):
        import tkinter as tk

        self.root = root
        self.tk = tk
        self.ring = ring
        self.judge = judge
        self.sfreq = sfreq
        self.refresh_ms = refresh_ms
        self.baseline_seconds = baseline_seconds
        self.recorder = recorder
        self.src = src                # 要盯着它有没有报错
        self._err_shown = False
        self.t0 = time.time()
        self.last_print = 0.0

        font = sgui.pick_cjk_font()
        root.title("实时脑电状态 — 放松度（判定来自 relax_axis）")
        root.geometry("780x470")
        root.minsize(540, 340)

        self.bg = tk.Frame(root, bg=COLOR_WAIT)
        self.bg.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.main_label = tk.Label(self.bg, text="等待数据…",
                                   font=(font, 50, "bold"),
                                   bg=COLOR_WAIT, fg=FG_WAIT)
        self.main_label.place(relx=0.5, rely=0.44, anchor="center")

        self.sub_label = tk.Label(self.bg, text="", font=(font, 15),
                                  bg=COLOR_WAIT, fg=FG_WAIT,
                                  wraplength=660, justify="center")
        self.sub_label.place(relx=0.5, rely=0.64, anchor="center")

        self.top_label = tk.Label(self.bg, text="", font=(font, 12),
                                  bg=COLOR_WAIT, fg=FG_WAIT)
        self.top_label.place(relx=0.5, rely=0.08, anchor="center")

        if topmost:
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass

        root.bind("<Escape>", lambda _e: root.destroy())
        root.after(self.refresh_ms, self.tick)

    def tick(self):
        # 设备出问题就当场把原因写在窗口上 —— 断网的时候没人能问你，
        # 窗口必须自己说清楚"错在哪"，否则你只能对着一片空白干瞪眼。
        if self.src is not None and self.src.error:
            if not self._err_shown:
                self._err_shown = True
                print("[错误]", self.src.error, flush=True)
            self._paint(COLOR_ERROR, FG_ERROR, "读取设备出错",
                        self.src.error, "按 Esc 关闭 · 详情见终端/日志文件")
            return

        elapsed = time.time() - self.t0
        res = self.judge.evaluate(self.ring, self.sfreq, elapsed)

        if res is None:
            if self.ring.count == 0:
                text, sub = "等待数据…", "还没收到任何采样点"
            else:
                text, sub = "正在积累数据…", "还需要几秒才能凑满一个窗口"
            self._paint(COLOR_WAIT, FG_WAIT, text, sub, "实时读取中 · Esc 退出")
        else:
            if res["invalid"]:
                text = "信号无效"
                color, fg = COLOR_WAIT, FG_WAIT
                sub = (f"这一窗只有 {res['n_good']} 个通道合格"
                       f"（需要 {MIN_GOOD_CHANNELS} 个）\n"
                       "多半是电极没贴好，或 Cyton 增益太高 → 检查电极")
            elif res["calibrating"]:
                text = "正在标定…"
                color, fg = COLOR_WAIT, FG_WAIT
                sub = (f"请安静坐好 {self.baseline_seconds:.0f} 秒 · "
                       f"已完成 {res['progress'] * 100:.0f}%\n"
                       "这几十秒就是你的个人基线，之后只跟它比")
            else:
                text = f"当前状态：{res['state']}"
                color, fg = _STATE_COLOR[res["state"]]
                base = (f"个人基线 α/β = {res['baseline']:.2f}"
                        if res["baseline"] else "未做个人基线（用绝对阈值）")
                sub = (f"放松度 = {res['relax']:.2f}"
                       f"（相对基线 {res['score']:.2f} 倍）\n"
                       f"α/β = {res['ratio']:.2f}  ·  画面：{res['scene']}\n"
                       f"{base}  ·  这一窗单独看：{res['raw_state']}")

            top = (f"已运行 {int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
                   f"　浅绿=放松　浅蓝=中间　浅红=紧张　Esc 退出")
            self._paint(color, fg, text, sub, top)

            # 终端也打一份，方便事后看日志、也方便你截图
            if elapsed - self.last_print > 1.0:
                self.last_print = elapsed
                if res["invalid"]:
                    print(f"  {int(elapsed):>4}s  "
                          f"信号无效（仅 {res['n_good']} 通道合格）")
                elif res["calibrating"]:
                    r = "" if res["ratio"] is None else f"α/β={res['ratio']:6.2f}  "
                    print(f"  {int(elapsed):>4}s  {r}[标定中 "
                          f"{res['progress'] * 100:3.0f}%]")
                else:
                    print(f"  {int(elapsed):>4}s  α/β={res['ratio']:6.2f}  "
                          f"相对={res['score']:5.2f}  放松度={res['relax']:4.2f}  "
                          f"→ {res['state']}")

            if self.recorder is not None:
                self.recorder.write(elapsed, res)

        self.root.after(self.refresh_ms, self.tick)

    def _paint(self, color, fg, text, sub, top):
        self.bg.config(bg=color)
        self.main_label.config(text=text, bg=color, fg=fg)
        self.sub_label.config(text=sub, bg=color, fg=fg)
        self.top_label.config(text=top, bg=color, fg=fg)


# ==================================================================
# 主流程
# ==================================================================
BOARD_CHOICES = {
    "synthetic":    ("SYNTHETIC_BOARD", None),
    "cyton_wifi":   ("CYTON_WIFI_BOARD", 1112),
    "daisy_wifi":   ("CYTON_DAISY_WIFI_BOARD", 1112),
    "ganglion_wifi": ("GANGLION_WIFI_BOARD", 1112),
    "cyton":        ("CYTON_BOARD", 0),
    "daisy":        ("CYTON_DAISY_BOARD", 0),
}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="实时脑电状态显示（放松 / 中间 / 紧张，判定来自 relax_axis）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--source", choices=["board", "csv", "bdf"], default="board",
                    help="数据源：board=直连设备，csv/bdf=盯住正在变长的录制文件")
    ap.add_argument("--board", choices=list(BOARD_CHOICES), default="synthetic",
                    help="设备型号（--source board 时用）")
    ap.add_argument("--file", default=None, help="CSV 路径（--source csv 时用）")
    ap.add_argument("--ip", default="192.168.4.1", help="WiFi 板子 IP")
    ap.add_argument("--port", type=int, default=None, help="端口（默认按型号）")
    ap.add_argument("--protocol", choices=["tcp", "udp"], default="tcp")
    ap.add_argument("--sfreq", type=float, default=None,
                    help="采样率（--source csv 时建议手动给，如 250）")
    ap.add_argument("--window", type=float, default=4.0, help="判定窗口秒数")
    ap.add_argument("--refresh", type=float, default=0.5, help="每几秒重画一次界面")
    ap.add_argument("--step", type=float, default=2.0,
                    help="每隔几秒挪一个新窗口（判定节奏，和现场链路一致）")
    ap.add_argument("--span", type=float, default=RELAX_SPAN,
                    help="放松度灵敏度：1.0=α/β 翻倍算完全放松，越小越灵敏")
    ap.add_argument("--baseline", type=float, default=60.0,
                    help="标定多少秒（0=不标定，直接用绝对比值）")
    ap.add_argument("--smooth", type=int, default=3,
                    help="放松度取最近 N 个窗口的均值（防止颜色乱闪）")
    ap.add_argument("--scale", type=float, default=1.0, help="CSV 数值缩放")
    ap.add_argument("--record", default=None,
                    help="把每个窗口的结果存成 CSV（建议填；事后才能对答案）")
    ap.add_argument("--headless", action="store_true",
                    help="不开窗口，只在终端打印")
    ap.add_argument("--duration", type=float, default=0,
                    help="配合 --headless：跑多少秒后自动停")
    ap.add_argument("--no-topmost", action="store_true")
    args = ap.parse_args()

    print("=" * 62)
    print("  实时脑电状态显示  ·  放松 / 中间 / 紧张")
    print("=" * 62)

    # ---------- 数据源 ----------
    if args.source == "board":
        from brainflow.board_shim import BoardIds, IpProtocolTypes
        name, default_port = BOARD_CHOICES[args.board]
        board_id = getattr(BoardIds, name).value
        port = args.port if args.port is not None else (default_port or 0)
        proto = (IpProtocolTypes.TCP.value if args.protocol == "tcp"
                 else IpProtocolTypes.UDP.value)
        is_wifi = "wifi" in args.board
        if is_wifi:
            port = args.port if args.port is not None else 1112

        sfreq_guess = 250.0 if args.board == "synthetic" else None
        # WiFi 板最高 1000 Hz，一开始不知道手上是哪块，先按 1000 Hz 开够；
        # 等就绪、知道真实采样率后再收窄（见下面的 ring.resize）。
        cap = int(max(args.window + 5, 12) * 1000)

        ring = RingBuffer(16, cap)
        src = BoardSource(ring, board_id, args.ip if is_wifi else "",
                          port if is_wifi else 0, proto if is_wifi else 0)
        print(f"[数据源] 设备 {name}（id={board_id}）"
              + (f"  目标 {args.ip}:{port}" if is_wifi else "  本地/串口"))
        src.start()
        # 等设备就绪（最多 20 秒）
        t_wait = time.time()
        while not src.ready.is_set() and time.time() - t_wait < 20:
            if src.error:
                sys.exit("\n[错误] " + src.error)
            time.sleep(0.2)
        if src.error:
            sys.exit("\n[错误] " + src.error)
        if not src.ready.is_set():
            sys.exit("\n[错误] 20 秒内没等到设备就绪。"
                     "先跑 python analysis/check_board.py 排查。")
        sfreq = src.sfreq or sfreq_guess or 250.0
        # 把缓冲调准到真实采样率/通道数：否则 1000 Hz 的 WiFi 板
        # 会把 4 秒的判定窗口悄悄截成 3 秒，算出来的功率就偏了。
        ring.resize(src.n_ch or 16, int(max(args.window + 5, 12) * sfreq))
        print(f"[数据源] 已就绪：{sfreq:.0f} Hz，{src.n_ch} 个脑电通道")
    else:
        if not args.file:
            sys.exit("[错误] --source csv/bdf 必须给 --file 路径")
        sfreq = args.sfreq or 250.0
        cap = int(max(args.window + 5, 12) * sfreq)
        ring = RingBuffer(16, cap)
        if args.source == "bdf":
            src = BdfTailSource(ring, args.file)
            print(f"[数据源] 跟随 BDF：{os.path.expanduser(args.file)}")
        else:
            src = CsvTailSource(ring, args.file, sfreq, scale=args.scale)
            print(f"[数据源] 跟随 CSV：{os.path.expanduser(args.file)}")
            print(f"[数据源] 假定采样率 {sfreq:.0f} Hz"
                  f"（不对的话用 --sfreq 指定）")
        src.start()
        src.ready.wait(timeout=30)
        if src.error:
            sys.exit("\n[错误] " + src.error)
        if not src.ready.is_set():
            sys.exit("\n[错误] 30 秒内没能从文件里读出数据。\n"
                     "  → 确认 OpenBCI GUI 已经在录制，文件正在变长。")
        if args.source == "bdf":
            sfreq = src.sfreq or sfreq
            ring.resize(src.n_ch or 16, int(max(args.window + 5, 12) * sfreq))
            print(f"[数据源] 已就绪：{sfreq:.0f} Hz，{src.n_ch} 个脑电通道")

    judge = LiveJudge(args.window, args.baseline, args.smooth,
                      step=args.step, span=args.span)

    # ---------- 无窗口模式 ----------
    if args.headless:
        rec = WindowRecorder(args.record) if args.record else None
        t0 = time.time()
        try:
            while True:
                el = time.time() - t0
                if args.duration and el > args.duration:
                    break
                if src.error:
                    print("[错误]", src.error)
                    break
                res = judge.evaluate(ring, sfreq, el)
                if res is None:
                    print(f"  {int(el):>4}s  等待数据…（已收 {ring.count} 点）")
                elif res["invalid"]:
                    print(f"  {int(el):>4}s  信号无效"
                          f"（仅 {res['n_good']} 通道合格）")
                    if rec is not None:
                        rec.write(el, res)
                elif res["calibrating"]:
                    r = "" if res["ratio"] is None else f"α/β={res['ratio']:6.2f}  "
                    print(f"  {int(el):>4}s  {r}[标定中 {res['progress'] * 100:3.0f}%]")
                    if rec is not None:
                        rec.write(el, res)
                else:
                    print(f"  {int(el):>4}s  α/β={res['ratio']:6.2f}  "
                          f"相对={res['score']:5.2f}  放松度={res['relax']:4.2f}  "
                          f"→ {res['state']}  画面：{res['scene']}")
                    if rec is not None:
                        rec.write(el, res)
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        src.stop()
        if rec is not None:
            rec.close()
            print(f"[记录] 已存到 {rec.path}（{rec.n} 行）")
        print("\n[完成] 实时管线跑通")
        return

    # ---------- 开窗口 ----------
    try:
        import tkinter as tk
    except ImportError:
        sys.exit("[错误] 这个 Python 没有 tkinter，先用 --headless 验证。")

    recorder = WindowRecorder(args.record) if args.record else None
    root = tk.Tk()
    LiveWindow(root, ring, judge, sfreq,
               int(args.refresh * 1000), args.baseline,
               topmost=not args.no_topmost, recorder=recorder, src=src)
    root.lift()
    root.focus_force()
    try:
        root.mainloop()
    finally:
        src.stop()
        if recorder is not None:
            recorder.close()
            print(f"[记录] 已存到 {recorder.path}（{recorder.n} 行）")


if __name__ == "__main__":
    main()
