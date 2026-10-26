#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地脑电文件回放：读数据 → 分析状态 → 驱动 Unity 场景。

为什么需要它
------------
Unity 那条链路（bridge → Unity）本来是通的，但一直喂的是合成板假数据，
或者被 DEMO_MODE 的假曲线覆盖。这个脚本把「你真实录下来的脑电文件」
当数据源，按时间顺序一个窗口一个窗口推给 bridge，
于是 Unity 场景是真的随脑电在变 —— 不是随 demo_ratios() 在变。

推送的 JSON 格式和 eeg_service/main.py 完全一致，
所以 Unity 端一行都不用改（NeuroWorldController 只认那四个键）。

用法
----
    cd ~/DeepWorks/NeuroBloom
    ./.venv/bin/python eeg_service/replay_service.py --file <你的.bdf>

先干跑一遍看节奏（不连 Unity、不等待）：
    ./.venv/bin/python eeg_service/replay_service.py --file <你的.bdf> --dry-run

想快点播完（默认 5 倍速）：
    ./.venv/bin/python eeg_service/replay_service.py --file <你的.bdf> --speed 10

策略层（八套氛围套餐）默认是开的：每 --round-seconds 秒换一轮，开局几轮固定
P8 不干预对照。想退回「纯连续映射」：
    ./.venv/bin/python eeg_service/replay_service.py --file <你的.bdf> --no-strategy
或设环境变量 EEG_STRATEGY=0。策略层只**调制**画面，绝不代替脑电驱动画面 ——
P8 那几轮画面照常跟着脑电在变，那是正常行为。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

# 本文件在 eeg_service/ 下面，而分析模块在上一层的 analysis/ 里。
# 把项目根目录塞进 sys.path，才能直接 import 复用那套已经验证过的逻辑
# （尤其是坏通道拒判 —— 它是整个判定能不能信的关键）。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from analysis.alpha_beta_state import analyze, load_from_file, prepare_raw  # noqa: E402
# 判定逻辑一律从 analysis/relax_axis.py 拿 —— 现场采集（eeg_service/main.py）
# import 的是同一个文件。所以这个文件里**不允许再出现** relax_from_score /
# world_from_relax / label_from_relax / pick_baseline 的本地定义：
# 之前就是因为两条路各写了一套，同一个人、同一段脑电，两边画面完全不一样。
from analysis.relax_axis import (  # noqa: E402
    label_from_relax,
    moving_average,
    pick_baseline,
    relax_from_score,
    scene_words,
    world_from_relax,
)
# 策略层：在「脑电 → 四键」上面加一层氛围调制。现场（main.py）用的是同一个。
from strategy.engine import (  # noqa: E402
    StrategyEngine,
    control_rounds_from_env,
    enabled_from_env,
    round_seconds_from_env,
)

DEFAULT_BRIDGE = os.getenv("BRIDGE_WS", "ws://127.0.0.1:8765/ws/eeg")


# ==================================================================
# 长时平均：为什么必须做这一步
# ==================================================================
def prepare_series(results, baseline, smooth_n: int):
    """先算出每个窗口的放松度，再做滑动平均，返回与 results 等长的列表。

    为什么非平滑不可：
        单窗口的 α/β 噪声底实测有 30%~60%。不平滑直接推给 Unity，
        画面会在「天亮」和「浓雾」之间每 2 秒闪一次 —— 看着像坏了。
        这和「阶段级比较」是同一个道理：单窗口不可信，长时平均才可信。

    为什么用居中而不是只看过去：
        这是回放已经录好的数据，不是实时流，允许看前后邻居。
        居中平滑既没有滞后，曲线也更稳，演示效果最好。
        （以后真接设备时应该改成因果平滑，那时候没有「未来」可看。）
    """
    raw = []
    scores = []
    for r in results:
        if not r.valid:
            raw.append(None)
            scores.append(None)
            continue
        score = (r.ratio / baseline) if baseline else r.ratio
        scores.append(score)
        raw.append(relax_from_score(score))
    return moving_average(raw, smooth_n), scores


# ==================================================================
# 打成一个窗口的推送包
# ==================================================================
def make_payload(r, relax, score, prev_params):
    if not r.valid or relax is None:
        # 坏数据：不下结论。世界参数保持上一个值，场景就不动 ——
        # 这比「随便给个值让画面乱跳」诚实得多。
        return {
            "t": time.time(),
            "label": "invalid",
            "n_good": r.n_good,
            "window": [round(r.start, 1), round(r.end, 1)],
            "note": f"电极问题，只有 {r.n_good} 个通道合格",
            "world_params": prev_params or world_from_relax(0.5),
        }

    return {
        "t": time.time(),
        "label": label_from_relax(relax),
        "alpha_ratio": round(r.alpha, 3),
        "beta_ratio": round(r.beta, 3),
        "score": round(score, 3) if score is not None else None,
        "relax": round(relax, 3),
        "calm": round(relax, 3),          # 兼容老字段名
        "n_good": r.n_good,
        "window": [round(r.start, 1), round(r.end, 1)],
        "world_params": world_from_relax(relax),
    }


# ==================================================================
# 主流程
# ==================================================================
def build_playlist(args):
    print("=" * 68)
    print("  本地脑电回放 → Unity 场景")
    print("=" * 68)

    raw = load_from_file(args.file)[0]
    raw = prepare_raw(raw)
    dur = raw.times[-1]
    sfreq = float(raw.info["sfreq"])
    print(f"  文件      ：{args.file}")
    print(f"  通道 {len(raw.ch_names)} | 采样率 {sfreq:.0f} Hz | 时长 {dur:.1f} 秒")

    # 这里的 analyze() 只用来**切窗口 + 取每个窗口的 α/β**（results 里的
    # start/end/alpha/beta/valid）。它的判定字段（state/score）一概不用 ——
    # 判定统一在 relax_axis：基线由下面的 pick_baseline 挑，再 relax_from_score。
    # 所以那几个参数传什么都行，传 1.0/0.0 只是为了别去动 alpha_beta_state。
    results, _ = analyze(raw, 1, args.window, args.step, 1.0, 0.0)
    if not results:
        sys.exit("  [错误] 没产生任何窗口，检查文件长度/采样率")

    # 只保留选定时间段（默认整段）。
    # 真实录制里常有一大段电极饱和的废数据，演示时没必要把它也播出来 ——
    # 那段时间场景会一动不动，看着像程序挂了，其实是在如实说"这段数据不可信"。
    n_before = len(results)
    if args.t_from is not None:
        results = [r for r in results if r.start >= args.t_from]
    if args.t_to is not None:
        results = [r for r in results if r.start <= args.t_to]
    if not results:
        sys.exit("  [错误] 你选的时间段里一个窗口都没有，把范围放宽些")

    baseline, span = pick_baseline(results, args.baseline_seconds)
    n_all = len(results)
    n_ok = sum(1 for r in results if r.valid)
    print(f"  窗口      ：{n_all} 个（{args.window:.0f}s 窗 / {args.step:.0f}s 步）"
          f"，可用 {n_ok} 个（{n_ok / n_all * 100:.1f}%）")
    if baseline:
        print(f"  个人基线  ：α/β = {baseline:.3f}"
              f"（取 {span[0]:.1f}~{span[1]:.1f}s 这段最干净的数据）")
    else:
        print("  个人基线  ：没找到可用数据，只能用原始 α/β 比值判定")
    play_span = results[-1].end - results[0].start
    if n_before != len(results):
        print(f"  时间段    ：只回放 {results[0].start:.1f}~{results[-1].start:.1f}s"
              f"（整段有 {n_before} 个窗口，这里挑了 {len(results)} 个）")
    print(f"  播放速度  ：{args.speed:g} 倍"
          f"（约 {play_span / args.speed / 60:.1f} 分钟播完）")
    print(f"  平滑      ：{args.smooth} 个窗口"
          f"（约 {args.smooth * args.step:.0f} 秒）"
          f"　← 不平滑的话画面会疯狂闪烁")

    # 策略层：谁在这一轮说了算的是「氛围」，不是「画面跟不跟脑电走」。
    # 画面永远由脑电驱动（底层），策略只往它的目标值上拉一部分（上层）。
    engine = StrategyEngine(round_seconds=args.round_seconds,
                            control_rounds=args.control_rounds,
                            enabled=not args.no_strategy)
    if engine.enabled:
        print(f"  策略层    ：开（{engine.timer.round_seconds:g}s 一轮，"
              f"开局 {engine.timer.control_rounds} 轮固定 P8 不干预对照）")
        print("              对照那几轮画面照常在跟脑电动 —— 那是对的，不是卡住")
        print("              想关掉：加 --no-strategy 或设 EEG_STRATEGY=0")
    else:
        print("  策略层    ：关（退回纯连续映射：只有脑电 → 四键）")
    print("=" * 68)
    series, scores = prepare_series(results, baseline, args.smooth)
    return results, series, scores, engine


async def stream(results, series, scores, args, engine: StrategyEngine):
    ws = None
    if not args.dry_run:
        try:
            import websockets
        except ImportError:
            sys.exit("  [错误] 没装 websockets，跑不了推送")
        try:
            ws = await websockets.connect(args.bridge)
            print(f"  已连上桥接：{args.bridge}", flush=True)
            print("  （Unity 端现在应该开始跟着变了）\n", flush=True)
        except Exception as e:
            print(f"\n  [错误] 连不上桥接 {args.bridge}")
            print(f"         原因：{type(e).__name__}: {e}")
            print("         先把 bridge 起起来再试（见 README / start.sh）")
            return

    prev_params = None
    sent = 0
    passes = 0
    try:
        while True:
            passes += 1
            if passes > 1:
                # 循环时在两遍中间留个空档，不然画面会从结尾瞬间弹回开头
                print(f"\n  —— 第 {passes} 遍（按 Ctrl-C 停止）——\n", flush=True)
                if ws is not None:
                    await asyncio.sleep(1.5)

            for r, relax, sc in zip(results, series, scores):
                payload = make_payload(r, relax, sc, prev_params)

                # 策略层只调制 payload 里的 world_params；relax / label 一概不动
                # （它们是「内部坐标」，不对外，也不参与画面）。
                # dt 用 --step 而不是墙上时间：回放有倍速，轮次必须按数据时间走。
                out = engine.feed(payload["world_params"], payload["label"],
                                  args.step, payload.get("relax"))
                payload["world_params"] = out["world_params"]
                payload.update({k: v for k, v in out.items()
                                if k != "world_params"})
                # 坏窗口要让场景「停在上一帧的样子」——那必须是调制后的样子，
                # 否则坏数据一来画面会自己往回跳一下，看着像闪屏。
                prev_params = payload["world_params"]
                engine.dump()

                if ws is not None:
                    await ws.send(json.dumps(payload, ensure_ascii=False))

                if out["round_changed"] and engine.banner():
                    print("\n  " + engine.banner(), flush=True)

                if payload["label"] == "invalid":
                    print(f"  [{r.start:6.1f}s] 无效      "
                          f"（只 {r.n_good} 通道合格 → 场景保持不动）", flush=True)
                else:
                    w = payload["world_params"]
                    pid = payload.get("strategy") or "----"
                    print(f"  [{r.start:6.1f}s] {payload['label']:<8} {pid} "
                          f"放松度={payload['relax']:.2f}  "
                          f"花开={w['bloom']:.2f} 阳光={w['sunlight']:.2f} "
                          f"雾={w['fog']:.2f} 水晶={w['crystal']:.2f}"
                          f"  → {scene_words(payload['relax'])}", flush=True)
                    sent += 1

                if ws is not None and not args.dry_run:
                    await asyncio.sleep(args.step / max(args.speed, 0.1))

            if not args.loop or args.dry_run:
                break
    finally:
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    print()
    print("=" * 68)
    print(f"  播放结束：共 {passes} 遍 / {passes * len(results)} 个窗口，"
          f"其中 {sent} 个有效窗口驱动了场景。")
    print("  （无效窗口不改动场景 —— 数据坏的时候，让它停着比让它乱跳诚实。）")
    if engine.enabled and engine.rounds:
        print()
        for line in engine.summary():
            print(line)
    print("=" * 68)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="回放本地脑电文件，驱动 Unity 场景",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--file", required=True, help="脑电文件路径（.bdf/.edf/.fif 等）")
    ap.add_argument("--bridge", default=DEFAULT_BRIDGE, help="桥接地址")
    ap.add_argument("--window", type=float, default=4.0, help="判定窗口秒数（默认 4）")
    ap.add_argument("--step", type=float, default=2.0, help="窗口步长秒数（默认 2）")
    ap.add_argument("--baseline-seconds", type=float, default=60.0,
                    help="用多少秒当个人基线（默认 60）")
    ap.add_argument("--speed", type=float, default=5.0, help="播放倍速（默认 5）")
    ap.add_argument("--smooth", type=int, default=5,
                    help="滑动平均的窗口个数（默认 5，即约 10 秒）")
    ap.add_argument("--from", dest="t_from", type=float, default=None,
                    help="只回放这段时间之后的数据（秒；默认从开头）")
    ap.add_argument("--to", dest="t_to", type=float, default=None,
                    help="只回放到这段时间为止（秒；默认到结尾）")
    ap.add_argument("--loop", action="store_true",
                    help="循环播放，直到按 Ctrl-C（演示用：免得你还没按 Play 它就播完了）")
    ap.add_argument("--dry-run", action="store_true",
                    help="不连 Unity，也不按时间等待，快速扫一遍看结果")
    ap.add_argument("--round-seconds", type=float,
                    default=round_seconds_from_env(),
                    help="策略层一轮多少秒（默认 20；环境变量 EEG_ROUND_SECONDS）")
    ap.add_argument("--control-rounds", type=int,
                    default=control_rounds_from_env(),
                    help="开局几轮固定用 P8 不干预对照（默认 3；环境变量 EEG_CONTROL_ROUNDS）")
    ap.add_argument("--no-strategy", action="store_true",
                    default=not enabled_from_env(),
                    help="关掉策略层，退回纯连续映射（等效 EEG_STRATEGY=0）")
    args = ap.parse_args()

    results, series, scores, engine = build_playlist(args)
    try:
        asyncio.run(stream(results, series, scores, args, engine))
    except KeyboardInterrupt:
        print("\n  已停止播放。")


if __name__ == "__main__":
    main()
