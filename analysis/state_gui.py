#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
「放松 / 紧张」可视化窗口（tkinter）

它自己不判断，判断逻辑全部复用 alpha_beta_state.py：
    读数据 → 滑窗算 α/β → 个人基线校准 → 判定「放松 / 紧张」

界面行为：
    · 判定「放松」→ 整个窗口变浅绿色
    · 判定「紧张」→ 整个窗口变浅红色
    · 窗口中间显示：当前状态：放松 / 紧张

用法：
    # 最简单的用法（用默认的睁眼静息数据回放）
    python analysis/state_gui.py

    # 指定你自己的脑电文件
    python analysis/state_gui.py 你的数据.edf

    # 快放 4 倍速（演示用，默认 1 倍速 = 真实时间流速）
    python analysis/state_gui.py --speed 4

    # 不用个人基线校准（那就会一直显示绿色，见下面的说明）
    python analysis/state_gui.py --baseline 0

    # 只做检查、不开窗口（在终端里看判定序列）
    python analysis/state_gui.py --check

关于「个人基线」为什么重要（第一次用请务必读）：
    α/β 的绝对值因人而异，而且脑电功率天然随频率下降（1/f），
    所以 α/β 通常人人都大于 1。用固定阈值 1.0 判定，结果就是
    「永远显示放松」——窗口一直是绿的，什么也演示不出来。
    先用开头 30 秒当自己的基准，之后比的是「现在的你 vs 刚才的你」，
    判定才会真正有变化。默认已开启 --baseline 30。
"""

from __future__ import annotations

import argparse
import os
import sys

# ------------------------------------------------------------------
# 把自己所在目录加进搜索路径，这样无论从哪个目录运行都能 import 到
# alpha_beta_state.py（判断逻辑的唯一来源）
# ------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import alpha_beta_state as ab  # noqa: E402  ← 复用已有的判断逻辑

# ------------------------------------------------------------------
# 颜色（浅绿 / 浅红，按要求"整个窗口"都变）
# ------------------------------------------------------------------
COLOR_RELAX = "#d7f5d7"    # 浅绿底
FG_RELAX = "#14631a"       # 深绿字
COLOR_TENSE = "#ffd7d7"    # 浅红底
FG_TENSE = "#a61919"       # 深红字
COLOR_INVALID = "#e9e9ef"  # 浅灰底：电极饱和/脱落，不下结论
FG_INVALID = "#3a3a45"

# 默认示例数据：PhysioNet 睁眼静息（前面已经下好的那一段）
DEFAULT_FILE = os.path.join(
    ab.ROOT, ".data", "mne_data",
    "MNE-eegbci-data", "files", "eegmmidb", "1.0.0", "S001", "S001R01.edf",
)


# ==================================================================
# 一、把数据算成长度固定的「判定序列」（与界面无关，方便单测）
# ==================================================================
def build_timeline(file_path, subject, max_runs, window, step, threshold, baseline):
    """
    返回 (windows, baseline_ratio)：一串按时间排好的 WindowResult。
    这段代码几乎照搬 alpha_beta_state.main() 的前半段，只是不打印表格。
    """
    if file_path:
        print(f"[数据] 本地文件：{file_path}")
        raws = ab.load_from_file(file_path)
    else:
        raws = ab.load_from_moabb(subject, max_runs)

    if not raws:
        sys.exit("[错误] 没有读到任何数据")

    print(f"[数据] 共 {len(raws)} 段记录")

    windows = []
    baselines = []
    for i, raw in enumerate(raws, start=1):
        raw = ab.prepare_raw(raw)          # 只留 EEG 通道 + 带通滤波
        res, base = ab.analyze(            # ← 判定逻辑在这一行
            raw, i, window, step, threshold, baseline
        )
        if base is not None:
            baselines.append(base)
        windows.extend(res)

    if not windows:
        sys.exit("[错误] 没产生任何判定窗口，检查数据长度或采样率")

    baseline_ratio = sum(baselines) / len(baselines) if baselines else None
    return windows, baseline_ratio


def smooth_states(windows, n):
    """
    平滑：用「最近 n 个窗口的多数票」决定当前显示的状态。

    为什么需要：单窗口的 α/β 会上下抖，颜色跟着乱闪，看着像坏了。
    取最近几窗的多数票，颜色就稳定多了 —— 这也是真实产品里的常规做法。
    n <= 1 时不平滑。
    """
    if n <= 1:
        return [w.state for w in windows]

    states = []
    for i in range(len(windows)):
        # 多数票只在「有效窗口」里投。坏数据既不算放松、也不算紧张，
        # 它只是没有信息，硬塞进去会污染平滑结果。
        recent = [w for w in windows[max(0, i - n + 1): i + 1] if w.valid]
        if not recent:
            states.append("无效")
            continue
        relaxed = sum(1 for w in recent if w.state == "放松")
        states.append("放松" if relaxed * 2 >= len(recent) else "紧张")
    return states


# ==================================================================
# 二、界面
# ==================================================================
def pick_cjk_font():
    """挑一个能正常显示中文的字体（macOS 上是苹方）。"""
    try:
        import tkinter.font as tkfont
        families = set(tkfont.families())
        for name in ("PingFang SC", "Hiragino Sans GB",
                     "Heiti SC", "STHeiti", "Helvetica"):
            if name in families:
                return name
    except Exception:
        pass
    return "Helvetica"


class StateWindow:
    """一个整屏变色的状态窗"""

    def __init__(self, root, windows, states, baseline_ratio,
                 interval_ms, topmost):
        import tkinter as tk

        self.root = root
        self.tk = tk
        self.windows = windows
        self.states = states
        self.baseline_ratio = baseline_ratio
        self.interval_ms = interval_ms
        self.i = 0

        root.title("脑电状态可视化 — 放松 / 紧张")
        root.geometry("760x460")
        root.minsize(520, 320)

        font_family = pick_cjk_font()

        # 用 place 铺满整个窗口，这样"整个窗口"都会变色
        self.bg = tk.Frame(root, bg=COLOR_RELAX)
        self.bg.place(relx=0, rely=0, relwidth=1, relheight=1)

        # 中间的大字：当前状态
        self.main_label = tk.Label(
            self.bg, text="当前状态：放松",
            font=(font_family, 52, "bold"),
            bg=COLOR_RELAX, fg=FG_RELAX,
        )
        self.main_label.place(relx=0.5, rely=0.45, anchor="center")

        # 副信息：进度、时间、α/β
        self.sub_label = tk.Label(
            self.bg, text="",
            font=(font_family, 15), bg=COLOR_RELAX, fg=FG_RELAX,
        )
        self.sub_label.place(relx=0.5, rely=0.66, anchor="center")

        # 顶部：数据来源
        self.top_label = tk.Label(
            self.bg, text="", font=(font_family, 12),
            bg=COLOR_RELAX, fg=FG_RELAX,
        )
        self.top_label.place(relx=0.5, rely=0.08, anchor="center")

        if topmost:
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass

        root.bind("<Escape>", lambda _e: root.destroy())   # 按 Esc 退出
        root.bind("<space>", lambda _e: self.toggle_pause())

        self.paused = False
        self.after_id = None
        self.tick()

    # ---------------- 每一个窗口到时触发 ----------------
    def tick(self):
        if self.i >= len(self.windows):     # 放完就停下，保持最后一帧
            self.sub_label.config(text=self.sub_label.cget("text") + "   （回放结束，按 Esc 关闭）")
            return

        w = self.windows[self.i]
        state = self.states[self.i]
        self.apply(w, state, self.i + 1, len(self.windows))
        self.i += 1
        self.after_id = self.root.after(self.interval_ms, self.tick)

    def toggle_pause(self):
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
            self.paused = True
            self.sub_label.config(text=self.sub_label.cget("text") + "   [已暂停，空格继续]")
        elif self.paused:
            self.paused = False
            if self.i < len(self.windows):
                self.after_id = self.root.after(0, self.tick)

    # ---------------- 真正"变色"的地方 ----------------
    def apply(self, w, state, idx, total):
        if state == "放松":
            bg, fg = COLOR_RELAX, FG_RELAX
        elif state == "紧张":
            bg, fg = COLOR_TENSE, FG_TENSE
        else:                       # 无效：电极饱和/脱落，不下结论
            bg, fg = COLOR_INVALID, FG_INVALID

        # 三处一起改，才是"整个窗口"变色
        self.bg.config(bg=bg)
        self.main_label.config(text=f"当前状态：{state}", bg=bg, fg=fg)
        self.sub_label.config(bg=bg, fg=fg)
        self.top_label.config(bg=bg, fg=fg)

        base_txt = (f"个人基线 α/β = {self.baseline_ratio:.2f}"
                    if self.baseline_ratio else "未使用个人基线")
        if w.valid:
            detail = f"α/β = {w.ratio:.2f}"
        else:
            detail = f"信号无效（只 {w.n_good} 个通道合格）"
        self.sub_label.config(
            text=(f"第 {idx}/{total} 窗 · 时间 {w.start:.1f}s · "
                  f"{detail}\n{base_txt}")
        )
        self.top_label.config(
            text="浅绿 = 放松　浅红 = 紧张　灰 = 无效　（空格暂停 / Esc 关闭）"
        )


# ==================================================================
# 三、命令行
# ==================================================================
def main() -> None:
    ap = argparse.ArgumentParser(
        description="脑电「放松 / 紧张」可视化窗口（tkinter）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("file", nargs="?", default=None,
                    help="本地脑电文件；不填则用默认示例数据")
    ap.add_argument("--subject", type=int, default=1, help="MOABB 被试编号")
    ap.add_argument("--runs", type=int, default=1, help="MOABB 用几个 run")
    ap.add_argument("--window", type=float, default=4.0, help="判定窗口秒数")
    ap.add_argument("--step", type=float, default=2.0, help="滑动步长秒数")
    ap.add_argument("--threshold", type=float, default=1.0,
                    help="达到多少算放松（配合 --baseline 时是相对基线的倍数）")
    ap.add_argument("--baseline", type=float, default=30.0,
                    help="开头多少秒作为个人基线（默认 30；设 0 则关闭校准）")
    ap.add_argument("--smooth", type=int, default=3,
                    help="颜色平滑：最近 N 个窗口多数票（默认 3；1=不平滑）")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="回放倍速（默认 1 = 真实时间流速）")
    ap.add_argument("--no-topmost", action="store_true",
                    help="不要把窗口钉在最前面")
    ap.add_argument("--check", action="store_true",
                    help="只算不开窗：在终端打印判定序列，用来验证")
    args = ap.parse_args()

    file_path = args.file
    if file_path is None:
        if os.path.exists(DEFAULT_FILE):
            file_path = DEFAULT_FILE
        else:
            print("[提示] 没找到默认示例数据，改为从 MOABB 下载")

    print("=" * 62)
    print("  脑电「放松 / 紧张」可视化窗口")
    print("=" * 62)

    windows, baseline_ratio = build_timeline(
        file_path, args.subject, args.runs,
        args.window, args.step, args.threshold, args.baseline,
    )
    states = smooth_states(windows, args.smooth)

    # ---- 终端预览（--check）----
    if args.check:
        for i, (w, s) in enumerate(zip(windows, states), start=1):
            color = "浅绿" if s == "放松" else "浅红"
            print(f"  第 {i:>2}/{len(windows)} 窗  {w.start:6.1f}s  "
                  f"α/β={w.ratio:6.2f}  → {s}（{color}）")
        n_relax = sum(1 for s in states if s == "放松")
        print(f"\n  共 {len(states)} 窗：放松 {n_relax}，紧张 {len(states) - n_relax}")
        if baseline_ratio:
            print(f"  个人基线 α/β = {baseline_ratio:.3f}")
        print("\n  （--check 只做检查，没有开窗口）")
        return

    # ---- 开窗口 ----
    # 回放节奏：相邻窗口相隔 step 秒，所以每 step/speed 秒推进一窗
    interval_ms = max(50, int(args.step / max(args.speed, 0.01) * 1000))

    try:
        import tkinter as tk
    except ImportError:
        sys.exit("[错误] 这个 Python 没带 tkinter，无法开窗口。"
                 "可以先用 --check 在终端看结果。")

    root = tk.Tk()
    StateWindow(root, windows, states, baseline_ratio,
                interval_ms, topmost=not args.no_topmost)
    root.lift()
    root.focus_force()
    root.mainloop()


if __name__ == "__main__":
    main()
