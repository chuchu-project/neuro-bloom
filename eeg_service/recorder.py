"""把原始脑电落盘。

为什么要单独一个模块：
    判定层每 2 秒只读最近 4 秒的窗口，读到就丢。试验当天如果想回头
    重新分析（换滤波、换判据、算别的指标），没有原始数据就只能干瞪眼。
    所以这里把「每一路每一毫秒」都存下来，一次不漏。

为什么不是另起一个程序去连板子：
    这块板子同一时间只能给一个客户端供数（OpenBCI GUI 开着的时候我们
    就抢不到流）。所以录制必须长在 eeg_service 这条已有的连接里。

存什么：
    .data/rec/<时间戳>/chunk_0000.npz  每 60 秒一个文件，里面
        eeg  (16, n) float32  微伏
        ts   (n,)   float64  板卡时间戳（Unix 秒）
    .data/rec/<时间戳>/meta.json       板卡型号、采样率、通道、开始时间、丢样统计

怎么读：
    d = np.load("chunk_0000.npz")
    d["eeg"]   # 形状 (通道, 样点)
    d["ts"]    # 和 eeg 第 2 维一一对应
"""

import json
import os
import time

import numpy as np

CHUNK_SECONDS = 60.0      # 一个文件装多少秒
GAP_WARN_SECONDS = 1.0    # 两次读之间超过这么久没数据，就记一次「丢了」


class Recorder:
    """边采边存。线程不安全，只在 eeg_service 的单一循环里用。"""

    def __init__(self, out_dir: str, board_id: int, sampling_rate: int,
                 eeg_channels, ts_channel: int):
        self.out_dir = out_dir
        self.fs = float(sampling_rate)
        self.channels = [int(c) for c in eeg_channels]
        self.ts_channel = int(ts_channel)
        self.board_id = int(board_id)

        os.makedirs(out_dir, exist_ok=True)
        self._meta_path = os.path.join(out_dir, "meta.json")
        self._chunk = 0
        self._eeg_parts = []
        self._ts_parts = []
        self._in_chunk = 0
        self._chunk_limit = max(1, int(self.fs * CHUNK_SECONDS))

        self._last_ts = None
        self.total_samples = 0
        self.gaps = []            # [(时间戳, 估计丢了多少样点), ...]

        self._write_meta(started_at=time.time())

    # ------------------------------------------------------------------
    def write(self, data: np.ndarray) -> None:
        """喂一整块 BrainFlow 原始数据（行=通道，列=样点）。

        用时间戳去重：上一次已经写过的样点不会写第二遍，
        所以「每 2 秒读一次、每次读最近 4 秒」不会重复也不会漏。
        """
        if data is None or data.shape[1] == 0:
            return
        ts = data[self.ts_channel]
        eeg = data[self.channels]

        if self._last_ts is not None:
            keep = ts > self._last_ts
            if not keep.any():
                return
            if not keep.all():
                ts = ts[keep]
                eeg = eeg[:, keep]
            gap = float(ts[0] - self._last_ts)
            if gap > GAP_WARN_SECONDS:
                lost = int(round(gap * self.fs)) - 1
                self.gaps.append((float(ts[0]), lost))
                print(f"[rec] ⚠️ 中间丢了约 {lost} 个样点"
                      f"（{gap:.1f} 秒没读到），已记到 meta.json", flush=True)

        self._eeg_parts.append(np.asarray(eeg, dtype=np.float32))
        self._ts_parts.append(np.asarray(ts, dtype=np.float64))
        self._in_chunk += eeg.shape[1]
        self.total_samples += eeg.shape[1]
        self._last_ts = float(ts[-1])

        if self._in_chunk >= self._chunk_limit:
            self.flush()

    # ------------------------------------------------------------------
    def flush(self) -> None:
        if not self._eeg_parts:
            return
        eeg = np.concatenate(self._eeg_parts, axis=1)
        ts = np.concatenate(self._ts_parts, axis=0)
        path = os.path.join(self.out_dir, f"chunk_{self._chunk:04d}.npz")
        np.savez_compressed(path, eeg=eeg, ts=ts,
                            channels=np.array(self.channels, dtype=np.int16))
        secs = eeg.shape[1] / self.fs
        print(f"[rec] 已存 {os.path.basename(path)}"
              f"（{eeg.shape[1]} 样点 / {secs:.0f} 秒，累计 "
              f"{self.total_samples / self.fs / 60:.1f} 分钟）", flush=True)
        self._chunk += 1
        self._eeg_parts = []
        self._ts_parts = []
        self._in_chunk = 0
        self._write_meta()

    # ------------------------------------------------------------------
    def close(self) -> None:
        self.flush()
        self._write_meta(finished_at=time.time())
        print(f"[rec] 录制结束：共 {self.total_samples} 样点"
              f"（{self.total_samples / self.fs / 60:.1f} 分钟），"
              f"丢样 {len(self.gaps)} 次", flush=True)

    # ------------------------------------------------------------------
    def stats(self) -> dict:
        """给前端看的录制状态（别把整条路径拼得太长，界面上放不下）。"""
        return {
            "on": True,
            "dir": os.path.basename(os.path.normpath(self.out_dir)),
            "seconds": round(self.total_samples / self.fs, 1) if self.fs else 0.0,
            "chunks": self._chunk,
            "gaps": len(self.gaps),
        }

    # ------------------------------------------------------------------
    def _write_meta(self, **extra) -> None:
        meta = {
            "board_id": self.board_id,
            "sampling_rate": self.fs,
            "channels": self.channels,
            "unit": "uV",
            "chunk_seconds": CHUNK_SECONDS,
            "total_samples": self.total_samples,
            "gaps": [{"ts": t, "lost": n} for t, n in self.gaps],
            "dir": self.out_dir,
        }
        meta.update(extra)
        try:
            with open(self._meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except OSError as e:      # 磁盘满 / 没权限时不要让采集跟着挂
            print(f"[rec] ⚠️ meta.json 写不进去：{e}", flush=True)
