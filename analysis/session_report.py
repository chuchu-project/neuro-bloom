#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroBloom 训练会话报告

一次训练跑完，把「这段时间里发生了什么」压成一页纸：
时长、有效判定比例、放松度走势、专注度、左右半球对称性、频带构成、电极合格率。

两种用法：
  1) 桥接在会话结束时自动生成（bridge/server.py 调用 SessionRecorder）
  2) 手动重出：拿一份会话记录再渲染一遍

    python -m analysis.session_report --in sessions/session-20261021-143000.jsonl
    python -m analysis.session_report --in xxx.jsonl --out /tmp/report.html

会话记录是 JSONL：一行一条状态快照（桥接每收到一条就追加一行）。
报告是单文件 HTML，不联外网、不带脚本、直接打印或存 PDF 都行。
"""
from __future__ import annotations

import argparse
import html
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "sessions"

BAND_LABELS = [
    ("delta", "δ 1–4 Hz", "深睡"),
    ("theta", "θ 4–8 Hz", "困倦"),
    ("alpha", "α 8–13 Hz", "放松"),
    ("beta", "β 13–30 Hz", "警觉"),
    ("gamma", "γ 30–45 Hz", "高负荷"),
]

# 判定的三种有效标签；calibrating / invalid 不算「一次有效的训练判定」
VALID_LABELS = ("relaxed", "tense", "neutral")


# ────────────────────────── 记录 ──────────────────────────
class SessionRecorder:
    """把流过来的每条状态压成一行写进 JSONL；会话结束渲染一页报告。

    什么时候算「一次会话」：
      - 第一条消息开一个文件
      - EEG 断开、或者超过 idle_timeout 秒没有新消息，就收尾
    文件名带开始时间，重复启动不会互相覆盖。
    """

    def __init__(self, out_dir: Path | str = DEFAULT_DIR, idle_timeout: float = 120.0):
        self.out_dir = Path(out_dir)
        self.idle_timeout = idle_timeout
        self.path: Path | None = None
        self._fh = None
        self.started_at: float | None = None
        self.last_at: float | None = None
        self.count = 0
        self.report_path: Path | None = None

    # -- 写 --
    def record(self, payload: dict[str, Any]) -> None:
        now = payload.get("t_win_end")
        now = float(now) if isinstance(now, (int, float)) else datetime.now().timestamp()

        if self._fh is not None and self.last_at is not None and now - self.last_at > self.idle_timeout:
            self.finish()          # 中间空了太久，上一段算一次独立训练
        if self._fh is None:
            self._open(now)

        row = {
            "t": round(now, 3),
            "relax": _num(payload.get("relax")),
            "label": payload.get("label"),
            "focus": _num(payload.get("focus")),
            "bt": _num(payload.get("bt")),
            "n_good": _num(payload.get("n_good")),
            "bands": payload.get("bands") if isinstance(payload.get("bands"), dict) else None,
            "li": _num((payload.get("lr") or {}).get("li")) if isinstance(payload.get("lr"), dict) else None,
            "notable": bool((payload.get("lr") or {}).get("notable")) if isinstance(payload.get("lr"), dict) else None,
            # 临床口径指标（IAF / α 不对称对数比 / 复杂度-熵）。
            # 采集端每 5~10 秒才重算一次，所以很多帧是重复值 —— 趋势看得慢，没关系。
            "iaf": _num((payload.get("markers") or {}).get("iaf")),
            "alpha_asym_log": _num((payload.get("markers") or {}).get("alpha_asym_log")),
            "lzc": _num((payload.get("markers") or {}).get("lzc")),
            "sample_entropy": _num((payload.get("markers") or {}).get("sample_entropy")),
            "world": payload.get("world_params") if isinstance(payload.get("world_params"), dict) else None,
            "cue": payload.get("cue"),
            "guidance": payload.get("guidance"),
        }
        self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._fh.flush()
        self.last_at = now
        self.count += 1

    def _open(self, started_at: float) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(started_at).strftime("%Y%m%d-%H%M%S")
        self.path = self.out_dir / f"session-{stamp}.jsonl"
        self._fh = self.path.open("w", encoding="utf-8")
        self.started_at = started_at
        self.count = 0
        self.report_path = None
        print(f"[session] 开始记录 {self.path.name}", flush=True)

    # -- 收尾 --
    def finish(self) -> Path | None:
        if self._fh is None:
            return None
        self._fh.close()
        self._fh = None
        if self.path is None:
            return None
        try:
            records = load_session(self.path)
        except Exception as exc:            # 记录坏了也不能把桥接带崩
            print(f"[session] 读取失败，跳过报告：{exc}", flush=True)
            return None
        if len(records) < 2:
            print("[session] 太短，不出报告", flush=True)
            return None
        summary = summarize(records)
        out = self.path.with_name(self.path.name.replace("session-", "report-").replace(".jsonl", ".html"))
        out.write_text(render_html(summary, records), encoding="utf-8")
        self.report_path = out
        print(f"[session] 结束，报告已生成 {out}", flush=True)
        return out

    def close(self) -> Path | None:
        return self.finish()


# ────────────────────────── 读取 ──────────────────────────
def load_session(path: Path | str) -> list[dict[str, Any]]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if isinstance(r, dict) and isinstance(r.get("t"), (int, float)):
            rows.append(r)
    rows.sort(key=lambda r: r["t"])
    return rows


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and v == v else None


def _series(rows: Iterable[dict], key: str) -> list[float]:
    return [r[key] for r in rows if isinstance(r.get(key), (int, float))]


def _mean(xs: Iterable[float]) -> float | None:
    xs = list(xs)
    return round(statistics.fmean(xs), 3) if xs else None


def _median(xs: Iterable[float]) -> float | None:
    xs = list(xs)
    return round(statistics.median(xs), 3) if xs else None


# ────────────────────────── 统计 ──────────────────────────
def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"empty": True, "frames": 0}

    t0, t1 = rows[0]["t"], rows[-1]["t"]
    valid = [r for r in rows if r.get("label") in VALID_LABELS]
    relaxed = [r for r in valid if r.get("label") == "relaxed"]
    tense = [r for r in valid if r.get("label") == "tense"]
    neutral = [r for r in valid if r.get("label") == "neutral"]

    relax = _series(valid, "relax")
    focus = _series(valid, "focus")
    li = _series(valid, "li")
    abs_li = [abs(x) for x in li]
    n_good = _series(rows, "n_good")

    bands: dict[str, float] = {}
    band_rows = [r["bands"] for r in valid if isinstance(r.get("bands"), dict)]
    if band_rows:
        for key, _, _ in BAND_LABELS:
            vals = [float(b[key]) for b in band_rows if isinstance(b.get(key), (int, float))]
            if vals:
                bands[key] = round(statistics.fmean(vals), 2)

    # 前半段 / 后半段对比：只用来回答「这次训练里对称性有没有变好」
    half = max(1, len(valid) // 2)
    first_li = [abs(r["li"]) for r in valid[:half] if isinstance(r.get("li"), (int, float))]
    second_li = [abs(r["li"]) for r in valid[half:] if isinstance(r.get("li"), (int, float))]

    guidance = []
    for r in rows:
        g = (r.get("guidance") or "").strip()
        if g and (not guidance or guidance[-1] != g):
            guidance.append(g)

    cue_counts: dict[str, int] = {}
    for r in rows:
        c = r.get("cue")
        if isinstance(c, str) and c:
            cue_counts[c] = cue_counts.get(c, 0) + 1

    total_good = 0.0
    for r in rows:
        b = r.get("bands")
        if isinstance(b, dict):
            total_good += sum(float(b.get(k, 0)) for k in ("delta", "theta", "alpha", "beta", "gamma"))

    iaf = _series(valid, "iaf")
    asym = _series(valid, "alpha_asym_log")
    lzc = _series(valid, "lzc")
    sampen = _series(valid, "sample_entropy")
    mk_summary = {
        "n": len(iaf),
        "iaf_median": _median(iaf),
        "iaf_last": round(iaf[-1], 2) if iaf else None,
        "iaf_band": ([round(statistics.median(iaf) - 2, 1),
                      round(statistics.median(iaf) + 2, 1)] if iaf else None),
        "asym_mean": _mean(asym),
        "asym_last": round(asym[-1], 3) if asym else None,
        "lzc_mean": _mean(lzc),
        "sample_entropy_mean": _mean(sampen),
    }

    return {
        "empty": False,
        "markers": mk_summary,
        "frames": len(rows),
        "valid_frames": len(valid),
        "invalid_frames": len(rows) - len(valid),
        "valid_ratio": round(len(valid) / len(rows), 3),
        "start": t0,
        "end": t1,
        "duration": round(t1 - t0, 1),
        "relax_mean": _mean(relax),
        "relax_max": round(max(relax), 3) if relax else None,
        "relax_min": round(min(relax), 3) if relax else None,
        "relax_last": round(relax[-1], 3) if relax else None,
        "relax_q": {
            "relaxed": round(len(relaxed) / len(valid), 3) if valid else None,
            "neutral": round(len(neutral) / len(valid), 3) if valid else None,
            "tense": round(len(tense) / len(valid), 3) if valid else None,
        },
        "focus_mean": _mean(focus),
        "focus_max": round(max(focus), 3) if focus else None,
        "li_mean": _mean(li),
        "abs_li_mean": _mean(abs_li),
        "li_notable_ratio": round(sum(1 for r in valid if r.get("notable")) / len(valid), 3) if valid else None,
        "li_left_dominant": sum(1 for x in li if x > 0.02),
        "li_right_dominant": sum(1 for x in li if x < -0.02),
        "li_first_half": _mean(first_li),
        "li_second_half": _mean(second_li),
        "n_good_mean": _mean(n_good),
        "n_good_min": int(min(n_good)) if n_good else None,
        "bands": bands,
        "band_total": round(total_good / len(band_rows), 1) if band_rows else None,
        "guidance": guidance,
        "cue_counts": cue_counts,
    }


# ────────────────────────── 画图（内联 SVG，不依赖网络） ──────────────────────────
def _sparkline(points: list[tuple[float, float]], w: int = 720, h: int = 130,
               lo: float | None = None, hi: float | None = None) -> str:
    """把 (x, y) 折成一条线。y 轴范围可指定，不指定就按数据自适应。"""
    if len(points) < 2:
        return '<p class="note">样本太少，画不出曲线。</p>'
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1 = min(xs), max(xs)
    y0 = lo if lo is not None else min(ys)
    y1 = hi if hi is not None else max(ys)
    if x1 == x0:
        x1 = x0 + 1
    if y1 == y0:
        y1 = y0 + 1
    pad = 6
    coords = []
    for x, y in points:
        px = pad + (x - x0) / (x1 - x0) * (w - 2 * pad)
        py = h - pad - (y - y0) / (y1 - y0) * (h - 2 * pad)
        coords.append(f"{px:.1f},{py:.1f}")
    grid = "".join(
        f'<line x1="{pad}" y1="{h - pad - (h - 2 * pad) * f:.1f}" x2="{w - pad}" '
        f'y2="{h - pad - (h - 2 * pad) * f:.1f}" stroke="#e6e4de" stroke-width="1"/>'
        for f in (0.25, 0.5, 0.75)
    )
    return (
        f'<svg class="spark" viewBox="0 0 {w} {h}" width="100%" height="{h}" '
        f'preserveAspectRatio="none" role="img">'
        f'{grid}<polyline fill="none" stroke="#2f6f5f" stroke-width="1.8" '
        f'stroke-linejoin="round" stroke-linecap="round" points="{" ".join(coords)}"/></svg>'
    )


def _pct(v: float | None, digits: int = 0) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def _num_s(v: float | None, digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _duration_text(sec: float) -> str:
    sec = int(round(sec))
    m, s = divmod(sec, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h} 小时 {m} 分 {s} 秒"
    return f"{m} 分 {s} 秒"


def render_html(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    if summary.get("empty"):
        return "<!doctype html><meta charset='utf-8'><title>会话报告</title><p>这次没有数据。</p>"

    e = html.escape
    t0 = summary["start"]
    pts_relax = [(r["t"] - t0, r["relax"]) for r in rows
                 if isinstance(r.get("relax"), (int, float)) and r.get("label") in VALID_LABELS]
    pts_li = [(r["t"] - t0, abs(r["li"])) for r in rows
              if isinstance(r.get("li"), (int, float)) and r.get("label") in VALID_LABELS]

    q = summary["relax_q"]
    band_total = summary["band_total"] or 0.0
    band_rows = ""
    for key, label, hint in BAND_LABELS:
        v = summary["bands"].get(key)
        share = (v / band_total * 100.0) if (v is not None and band_total) else None
        band_rows += (
            f"<tr><td>{e(label)}</td><td class='num'>{_num_s(v, 2)}</td>"
            f"<td class='num'>{'—' if share is None else f'{share:.1f}%'}</td>"
            f"<td class='hint'>{e(hint)}</td></tr>"
        )

    if summary["li_first_half"] is not None and summary["li_second_half"] is not None:
        delta = summary["li_second_half"] - summary["li_first_half"]
        if abs(delta) < 0.02:
            li_trend = "两半段基本持平"
        elif delta < 0:
            li_trend = f"后半段更对称了（差值的绝对值减少 {abs(delta):.2f}）"
        else:
            li_trend = f"后半段更不对称了（差值的绝对值增加 {abs(delta):.2f}）"
    else:
        li_trend = "左右半球数据不足，看不出走势"

    mk = summary.get("markers") or {}
    if mk.get("iaf_median") is not None:
        _iaf_txt = f"{mk['iaf_median']:.2f} Hz"
        lo, hi = mk["iaf_band"]
        _iaf_band_txt = f"{lo}–{hi} Hz"
    else:
        _iaf_txt = "—"
        _iaf_band_txt = "—"

    guidance_html = "".join(f"<li>{e(g)}</li>" for g in summary["guidance"][:12]) or "<li>这次没有记录到引导语。</li>"
    cue_text = "、".join(f"{e(k)} {v} 次" for k, v in sorted(summary["cue_counts"].items(), key=lambda kv: -kv[1])) or "—"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NeuroBloom 训练会话报告 · {e(_clock(t0))}</title>
<style>
  :root{{--ink:#1c1f24;--ink2:#4b525c;--ink3:#7b838f;--line:#e2e0d8;--paper:#fbfaf6;--accent:#2f6f5f}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:#efece4;color:var(--ink);
    font:15px/1.75 -apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}}
  .sheet{{max-width:860px;margin:32px auto 64px;background:var(--paper);padding:52px 56px 60px;
    box-shadow:0 1px 2px rgba(0,0,0,.06),0 12px 32px rgba(0,0,0,.05)}}
  header{{border-bottom:2px solid var(--ink);padding-bottom:16px;margin-bottom:28px}}
  .kicker{{font-size:12px;letter-spacing:.18em;color:var(--accent);text-transform:uppercase}}
  h1{{font-size:26px;line-height:1.35;margin:8px 0 6px;font-weight:700;letter-spacing:-.2px}}
  .meta{{font-size:13px;color:var(--ink3)}}
  .lede{{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);
    border:1px solid var(--line);margin:26px 0 30px}}
  .lede div{{background:var(--paper);padding:14px 16px}}
  .lede b{{display:block;font-size:24px;font-weight:700;letter-spacing:-.4px;font-variant-numeric:tabular-nums}}
  .lede span{{font-size:12px;color:var(--ink3)}}
  h2{{font-size:14px;letter-spacing:.12em;color:var(--ink2);margin:34px 0 10px;
    padding-bottom:6px;border-bottom:1px solid var(--line);text-transform:uppercase}}
  p{{margin:8px 0}}
  .note{{font-size:13px;color:var(--ink3)}}
  .spark{{display:block;margin:6px 0 4px}}
  .axis{{display:flex;justify-content:space-between;font-size:11.5px;color:var(--ink3)}}
  table{{width:100%;border-collapse:collapse;font-size:14px;margin-top:6px}}
  th{{text-align:left;font-weight:600;font-size:12px;color:var(--ink3);letter-spacing:.04em;
    border-bottom:1px solid var(--line);padding:7px 8px}}
  td{{padding:8px;border-bottom:1px solid #efede6;vertical-align:top}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
  td.hint{{color:var(--ink3);font-size:12.5px}}
  .two{{display:grid;grid-template-columns:1fr 1fr;gap:32px}}
  .kv{{font-size:14px}}
  .kv div{{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #efede6}}
  .kv span:last-child{{font-variant-numeric:tabular-nums;font-weight:600}}
  ul{{margin:6px 0 0 18px;padding:0}}
  li{{margin:5px 0}}
  footer{{margin-top:40px;padding-top:14px;border-top:1px solid var(--line);
    font-size:12px;color:var(--ink3);line-height:1.9}}
  @media print{{body{{background:#fff}}.sheet{{box-shadow:none;margin:0;padding:0 0 20px}}}}
</style>
</head>
<body>
<main class="sheet">
  <header>
    <div class="kicker">NeuroBloom · 训练会话报告</div>
    <h1>卒中后情绪与注意力辅助训练 · 第 {e(datetime.fromtimestamp(t0).strftime('%m月%d日 %H:%M'))} 次</h1>
    <div class="meta">开始 {e(_clock(t0))} ｜ 时长 {e(_duration_text(summary['duration']))} ｜ 判定帧 {summary['valid_frames']} 条</div>
  </header>

  <section class="lede">
    <div><b>{_pct(q['relaxed'])}</b><span>处于放松态</span></div>
    <div><b>{_num_s(summary['relax_mean'], 2)}</b><span>平均放松度 0–1</span></div>
    <div><b>{_num_s(summary['abs_li_mean'], 2)}</b><span>左右不对称均值</span></div>
    <div><b>{_pct(summary['valid_ratio'])}</b><span>信号有效占比</span></div>
  </section>

  <h2>放松度走势</h2>
  {_sparkline(pts_relax, lo=0.0, hi=1.0)}
  <div class="axis"><span>开始</span><span>{e(_duration_text(summary['duration']))}</span></div>
  <p class="note">纵轴 0–1，越高越放松。中途掉下来的地方，通常对应动作、说话或者电极松动。</p>

  <h2>两条轴各自的表现</h2>
  <div class="two">
    <div class="kv">
      <div><span>放松度 平均 / 最高 / 最低</span><span>{_num_s(summary['relax_mean'])} / {_num_s(summary['relax_max'])} / {_num_s(summary['relax_min'])}</span></div>
      <div><span>结束时的放松度</span><span>{_num_s(summary['relax_last'])}</span></div>
      <div><span>放松 / 平稳 / 紧绷</span><span>{_pct(q['relaxed'])} / {_pct(q['neutral'])} / {_pct(q['tense'])}</span></div>
    </div>
    <div class="kv">
      <div><span>专注度 平均 / 最高</span><span>{_num_s(summary['focus_mean'])} / {_num_s(summary['focus_max'])}</span></div>
      <div><span>左右不对称 平均</span><span>{_num_s(summary['li_mean'], 3)}</span></div>
      <div><span>明显不对称的帧占比</span><span>{_pct(summary['li_notable_ratio'])}</span></div>
    </div>
  </div>
  <p class="note">专注度是 β/θ 相对个人基线算的，和放松度是两个方向，可以「放松且专注」（比如闭眼静坐），也可以「紧张又分心」。</p>

  <h2>左右半球对称性</h2>
  {_sparkline(pts_li, lo=0.0, hi=max([0.3] + [p[1] for p in pts_li]) if pts_li else 0.3)}
  <div class="axis"><span>开始</span><span>{e(_duration_text(summary['duration']))}</span></div>
  <div class="kv">
    <div><span>前半段 / 后半段 平均不对称</span><span>{_num_s(summary['li_first_half'])} → {_num_s(summary['li_second_half'])}</span></div>
    <div><span>左强帧 / 右强帧</span><span>{summary['li_left_dominant']} / {summary['li_right_dominant']}</span></div>
  </div>
  <p>{e(li_trend)}。不对称指数是 (右 − 左) / (右 + 左)，0 表示两侧一样，绝对值越大越偏。</p>

  <h2>频带构成（各窗口平均）</h2>
  <table>
    <thead><tr><th>频带</th><th class="num">平均功率</th><th class="num">占比</th><th>通常对应</th></tr></thead>
    <tbody>{band_rows}</tbody>
  </table>
  <p class="note">功率单位是 μV²/Hz。占比比绝对值更稳，适合跨次对比。</p>

  <h2>临床口径指标</h2>
  <div class="two">
    <div class="kv">
      <div><span>IAF 个体 α 峰值频率（中位）</span><span>{_iaf_txt}</span></div>
      <div><span>按 IAF 收窄的 α 带</span><span>{_iaf_band_txt}</span></div>
      <div><span>α 不对称对数比 ln(右)−ln(左)</span><span>{_num_s(mk['asym_mean'], 3)}</span></div>
    </div>
    <div class="kv">
      <div><span>LZc 复杂度（均值）</span><span>{_num_s(mk['lzc_mean'], 2)}</span></div>
      <div><span>样本熵（均值）</span><span>{_num_s(mk['sample_entropy_mean'], 2)}</span></div>
      <div><span>本次参与统计的帧</span><span>{mk['n']} 条</span></div>
    </div>
  </div>
  <p class="note">IAF 是这个人静息时 α 最明显的频率，人跟人能差 2~3 Hz，所以「按 IAF 收窄 α 带」比固定 8~13 Hz 更贴合。
  α 不对称对数比与文献里的额区 α 不对称（FAA）同源，但我们的左右分组是按通道排布（前 8 路 / 后 8 路），
  并非 F3/F4 那对电极，所以只作为带符号的数值和跨次变化来看，不做情绪判断。</p>

  <h2>信号质量与引导</h2>
  <div class="two">
    <div class="kv">
      <div><span>合格电极 平均 / 最少</span><span>{_num_s(summary['n_good_mean'], 1)} / {summary['n_good_min'] if summary['n_good_min'] is not None else '—'} 路</span></div>
      <div><span>无效帧（标定或接触差）</span><span>{summary['invalid_frames']} 条</span></div>
      <div><span>引导语类型</span><span>{cue_text}</span></div>
    </div>
    <div>
      <p class="note" style="margin-top:0">这次对佩戴者说过的话（去重，按顺序）：</p>
      <ul>{guidance_html}</ul>
    </div>
  </div>

  <footer>
    本报告是辅助训练过程的记录，不构成医学诊断或治疗建议。<br>
    数据来源：16 路脑电，窗口 4 秒，判定刷新 {e(str(len(rows)))} 条快照；生成时间 {e(_clock(datetime.now().timestamp()))}。
  </footer>
</main>
</body>
</html>
"""


# ────────────────────────── 命令行 ──────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把一次训练的会话记录渲染成一页 HTML 报告")
    ap.add_argument("--in", dest="src", required=True, help="会话 JSONL 路径")
    ap.add_argument("--out", dest="dst", default=None, help="输出 HTML 路径（默认同名 .html）")
    args = ap.parse_args(argv)

    src = Path(args.src)
    if not src.exists():
        print(f"找不到文件：{src}", file=sys.stderr)
        return 2
    rows = load_session(src)
    if not rows:
        print("这份记录里没有可用的行。", file=sys.stderr)
        return 3
    summary = summarize(rows)
    dst = Path(args.dst) if args.dst else src.with_suffix(".html")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(render_html(summary, rows), encoding="utf-8")
    print(f"报告已生成：{dst}")
    print(f"  时长 {_duration_text(summary['duration'])}，判定帧 {summary['valid_frames']} 条，"
          f"有效占比 {_pct(summary['valid_ratio'])}")
    print(f"  放松度均值 {_num_s(summary['relax_mean'])}，不对称均值 {_num_s(summary['abs_li_mean'])}")
    mk = summary.get("markers") or {}
    if mk.get("n"):
        print(f"  IAF 中位 {_num_s(mk['iaf_median'])} Hz，"
              f"α 不对称对数比 {_num_s(mk['asym_mean'], 3)}，"
              f"LZc {_num_s(mk['lzc_mean'], 2)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
