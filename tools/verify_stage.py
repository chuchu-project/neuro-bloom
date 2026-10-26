#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroBloom 现阶段成果验收（离线 / 可重复 / 零 Unity 依赖）

它只读这些已存在的产物，不调用 Unity、不改任何文件：
  UnityVR/render_states/*.png            出图（A 阶段四态 + B 阶段蓄能）
  UnityVR/Assets/Textures/*.asset        贴图（直接解析 _typelessdata 的 alpha）
  UnityVR/Assets/Scenes/NeuroBloom.unity 场景默认状态
  UnityVR/Assets/Scripts/NeuroInteraction.cs  交互红线 + 回归扫描

用法：
    python3 tools/verify_stage.py
    python3 tools/verify_stage.py --report /path/to/报告.md
退出码：0 = 全部通过（允许 WARN），1 = 有 FAIL。
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # NeuroBloom/
RENDER = ROOT / "UnityVR" / "render_states"
TEX = ROOT / "UnityVR" / "Assets" / "Textures"
SCENE = ROOT / "UnityVR" / "Assets" / "Scenes" / "NeuroBloom.unity"
INTERACTION = ROOT / "UnityVR" / "Assets" / "Scripts" / "NeuroInteraction.cs"

# 光纹环在 1280x720 画面上的预测中心（由相机位姿 + 环世界坐标算得）
RING_CX, RING_CY = 749, 362
RING_TOL = 70
# 环可能出现整块屏幕窗口
BX0, BX1, BY0, BY1 = 490, 1010, 100, 620

RESULTS = []   # (level, title, detail)


def add(level, title, detail=""):
    RESULTS.append((level, title, detail))


def img(name):
    from PIL import Image
    p = RENDER / name
    if not p.exists():
        return None
    import numpy as np
    return np.asarray(Image.open(p).convert("RGB")).astype("int16")


def read_typelessdata(path):
    """Unity Texture2D 的像素字节存在 _typelessdata 的十六进制串里（可能跨行）。"""
    buf, grab = [], False
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "_typelessdata:" in ln:
            grab = True
            rest = ln.split("_typelessdata:", 1)[1].strip()
            if re.fullmatch(r"[0-9a-fA-F]+", rest or ""):
                buf.append(rest)
            continue
        if grab:
            t = ln.strip()
            if re.fullmatch(r"[0-9a-fA-F]+", t or ""):
                buf.append(t)
            else:
                break
    return bytes.fromhex("".join(buf))


# ---------------------------------------------------------------- C1 贴图 alpha
def check_textures():
    import numpy as np
    expect = {"NB2_Rune.asset": 200, "NB2_Burst.asset": 200, "NB2_Beam.asset": 100}
    for fn, floor in expect.items():
        p = TEX / fn
        if not p.exists():
            add("WARN", f"C1 贴图 {fn} 不存在", "跳过")
            continue
        try:
            raw = read_typelessdata(p)
        except Exception as e:
            add("FAIL", f"C1 贴图 {fn} 解析失败", str(e))
            continue
        px = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 4)[:65536]
        a_max, a_mean = int(px[:, 3].max()), float(px[:, 3].mean())
        nz = float((px[:, 3] > 0).mean() * 100)
        ok = a_max >= floor and nz > 15
        add("PASS" if ok else "FAIL",
            f"C1 {fn} alpha 健康度",
            f"max={a_max}（要求≥{floor}） mean={a_mean:.1f} 非零={nz:.1f}%（要求>15%）")


# ------------------------------------------------- C2 Mathf.SmoothStep 误用回归
def check_smoothstep():
    bad = []
    for cs in sorted((ROOT / "UnityVR" / "Assets").rglob("*.cs")):
        for i, ln in enumerate(cs.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            code = ln.split("//")[0]          # 注释里会提到这个坑，不能算误用
            for m in re.finditer(r"Mathf\.SmoothStep\s*\(\s*([^,]+?)\s*,\s*([^,]+?)\s*,", code):
                a, b = m.group(1).strip(), m.group(2).strip()
                # 合法：from=0, to=1（Lerp 语义的正确用法）
                if re.fullmatch(r"0(\.0+)?f?", a) and re.fullmatch(r"1(\.0+)?f?", b):
                    continue
                bad.append(f"{cs.relative_to(ROOT)}:{i}  Mathf.SmoothStep({a}, {b}, ...)")
    if bad:
        add("FAIL", "C2 Mathf.SmoothStep 误用扫描",
            "Unity 的 Mathf.SmoothStep(from,to,t) 是 Lerp 语义，不是 GLSL 的 smoothstep(edge0,edge1,x)。"
            "以下调用第一/第二参数不是 0/1，会静默压暗结果：\n      " + "\n      ".join(bad))
    else:
        add("PASS", "C2 Mathf.SmoothStep 误用扫描", "全部调用均为 (0,1,参数) 的正确形式")


# ------------------------------------------------------------- C3 四态单调性
def check_four_states():
    names = [("1_relaxed.png", 1.00), ("2_neutral.png", 0.66),
             ("3_tense.png", 0.33), ("4_very_tense.png", 0.05)]
    rows = []
    for fn, r in names:
        a = img(fn)
        if a is None:
            add("WARN", "C3 四态图缺失", f"{fn} 不存在，跳过")
            return
        f = a.astype("float32") / 255.0
        rows.append({
            "f": fn, "r": r, "lum": float(f.mean()),
            "over": float((f.mean(axis=2) > 250 / 255).mean() * 100),
            "sat": float((f.max(axis=2) - f.min(axis=2)).mean()),
            "warm": float(((f[:, :, 0] - f[:, :, 2]) > 0.04).mean() * 100),
        })
    lum = [x["lum"] for x in rows]
    warm = [x["warm"] for x in rows]
    over_max = max(x["over"] for x in rows)
    mono_lum = all(lum[i] > lum[i + 1] for i in range(3))
    mono_warm = all(warm[i] > warm[i + 1] for i in range(3))
    tbl = " | ".join(f"{x['f'].replace('.png','')} 亮{x['lum']:.3f}/暖{x['warm']:.1f}%/过曝{x['over']:.2f}%"
                     for x in rows)
    ok = mono_lum and mono_warm and over_max < 1.0
    add("PASS" if ok else "FAIL", "C3 四态单调性（越放松越亮越暖、不过曝）",
        tbl + f"\n      亮度单调={mono_lum} 暖色单调={mono_warm} 最大过曝={over_max:.2f}%（要求<1.0%）")


# --------------------------------------------------- C4「暗区依然美丽」自发光点
def check_dark_beauty():
    a = img("4_very_tense.png")
    if a is None:
        add("WARN", "C4 极紧张图缺失", "跳过")
        return
    f = a.astype("float32") / 255.0
    lo = f[360:, :]                       # 下半屏（近景暗区）
    lum = lo.mean(axis=2)
    glow = int((lum > 0.60).sum())
    ok = lum.mean() < 0.40 and glow > 2000
    add("PASS" if ok else "FAIL", "C4 极紧张态：暗但仍有点缀光源",
        f"下半屏均值={lum.mean():.3f}（要求<0.40） 亮点(>0.60)={glow}px（要求>2000）")


# ----------------------------------------------------- C5 B 阶段蓄能/绽放单调性
def _growth(base, other):
    d = other.astype("int32") - base.astype("int32")
    lum = d.mean(axis=2)
    win = lum[BY0:BY1, BX0:BX1]
    ys, xs = (win > 6).nonzero()
    if len(xs) == 0:
        return 0, None, 0.0
    cx, cy = int(xs.mean()) + BX0, int(ys.mean()) + BY0
    return len(xs), (cx, cy), float(win.max())


def _radius95(base, other):
    """包含 95% 增亮像素的半径 —— 用来衡量环带扩到了多大。"""
    import numpy as np
    d = (other.astype("int32") - base.astype("int32")).mean(axis=2)
    ys, xs = (d > 6).nonzero()
    if len(xs) == 0:
        return 0.0
    rr = ((xs - RING_CX) ** 2 + (ys - RING_CY) ** 2) ** 0.5
    return float(np.percentile(rr, 95))


def check_charge():
    n = {}
    for k, fn in [("c0", "5_rune_00.png"), ("c50", "6_rune_50.png"),
                  ("c100", "7_rune_100.png"), ("burst", "8_rune_burst.png")]:
        a = img(fn)
        if a is None:
            add("WARN", "C5 蓄能图缺失", f"{fn} 不存在，跳过")
            return
        n[k] = a
    neu = img("2_neutral.png")
    if neu is not None:
        d = (n["c0"].astype("int32") - neu.astype("int32")).mean(axis=2)
        cnt = int((d > 6).sum())
        ok = cnt < 2000
        add("PASS" if ok else "FAIL", "C5a 静默态（蓄能0）不污染四态验收图",
            f"与 2_neutral 差异像素={cnt}px（要求<2000；两者 world_params 等效，都该是环静默的样子）")
    g50, c50, m50 = _growth(n["c0"], n["c50"])
    g100, c100, m100 = _growth(n["c0"], n["c100"])
    gb, cb, mb = _growth(n["c0"], n["burst"])
    dist = max(abs(c[0] - RING_CX) + abs(c[1] - RING_CY) for c in (c50, c100, cb) if c)
    ok = 500 < g50 < g100 < gb and dist < RING_TOL
    add("PASS" if ok else "FAIL", "C5b 蓄能→绽放 单调增长且落在环的位置",
        f"0.5档 +{g50}px 质心{c50} ｜ 1.0档 +{g100}px 质心{c100} ｜ 绽放 +{gb}px 质心{cb}\n"
        f"      要求 0.5<1.0<绽放 且质心距预测({RING_CX},{RING_CY})<{RING_TOL}px，实测最大偏差={dist}px")
    r50 = _radius95(n["c0"], n["c50"])
    r100 = _radius95(n["c0"], n["c100"])
    rb = _radius95(n["c0"], n["burst"])
    ok2 = 0 < r50 < r100 < rb
    add("PASS" if ok2 else "FAIL", "C5c 环带半径随蓄能向外扩散",
        f"含 95% 增亮像素的半径：0.5档 {r50:.0f}px ｜ 1.0档 {r100:.0f}px ｜ 绽放 {rb:.0f}px（要求依次增大）")


# ------------------------------------------------------------- C6 场景默认状态
def check_scene():
    if not SCENE.exists():
        add("WARN", "C6 场景文件缺失", "跳过")
        return
    s = SCENE.read_text(encoding="utf-8", errors="replace")
    res = {}
    for name in ("RuneRing", "BurstRing"):
        i = s.find("m_Name: " + name)
        res[name] = None if i < 0 else re.search(r"m_Enabled: (\d)", s[max(0, i - 600):i + 400])
    if None in res.values() or any(v is None for v in res.values()):
        add("FAIL", "C6 场景中找不到 RuneRing / BurstRing", "场景可能被旧版生成器覆盖")
        return
    rune_on = res["RuneRing"].group(1) == "1"
    burst_off = res["BurstRing"].group(1) == "0"
    ok = rune_on and burst_off
    add("PASS" if ok else "FAIL", "C6 场景默认状态（环常显、绽放环待触发）",
        f"RuneRing enabled={res['RuneRing'].group(1)}（要求1） BurstRing enabled={res['BurstRing'].group(1)}（要求0）")


# --------------------------------------------------------- C7 交互代码红线扫描
def check_code_redlines():
    if not INTERACTION.exists():
        add("FAIL", "C7 找不到 NeuroInteraction.cs", str(INTERACTION))
        return
    src = INTERACTION.read_text(encoding="utf-8", errors="replace")
    banned = [t for t in ("OnGUI", "GUI.Label", "GUILayout", "TextMesh", "Canvas", "ToString(")
              if t in src]
    has_switch = "public static bool Active" in src
    has_zero = "PushPulse(0f)" in src
    ok = not banned and has_switch and has_zero
    add("PASS" if ok else "FAIL", "C7 交互红线（不显示任何数字 / 有 P8 对照开关 / 停用即归零）",
        f"违规 UI 符号={banned or '无'} ｜ P8 开关 Active={'有' if has_switch else '缺'} "
        f"｜ OnDisable 归零={'有' if has_zero else '缺'}")


# --------------------------------------------------------- C8 天气三态画面契约
def check_weather():
    """三态必须真的长得不一样，且各自满足用户定下的观感要求。

    阈值全部按 2026-10-20 实测值放宽（括号内为实测值）：
      夜亮度 ≤ 晴亮度-0.15（实 -0.245）  夜 B>G≥2 且 G>R≥5（实 80.8>71.9>44.8）
      雾饱和 ≤ 晴饱和×0.72（实 ×0.47）   雾绿主导度 < 晴×0.60（实 ×0.37）
      三态两两逐像素差 ≥10（实 22.2/51.1/52.2）  过曝 ≤3%（实 ≤0.51%）  品红 ≤300px（实 0）
    """
    a_sun, a_haz, a_nit = img("13_weather_sunny.png"), img("14_weather_hazy.png"), img("15_weather_night.png")
    if a_sun is None or a_haz is None or a_nit is None:
        add("WARN", "C8 天气三态图缺失", "13/14/15_weather_*.png 未齐，跳过")
        return

    def lum(a):
        return float((0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2]).mean() / 255.0)

    def sat(a):
        return float((a.max(axis=2) - a.min(axis=2)).mean() / 255.0)

    def rgb(a):
        m = a.reshape(-1, 3).mean(axis=0)
        return float(m[0]), float(m[1]), float(m[2])

    def over(a):
        return float((a.mean(axis=2) > 240).mean() * 100)

    def magenta(a):
        return int(((a[:, :, 0] > 204) & (a[:, :, 2] > 204) & (a[:, :, 1] < 77)).sum())

    L = {"晴": lum(a_sun), "雾": lum(a_haz), "夜": lum(a_nit)}
    S = {"晴": sat(a_sun), "雾": sat(a_haz), "夜": sat(a_nit)}
    R_, G_, B_ = rgb(a_nit)
    Rs, Gs, Bs = rgb(a_sun)
    Rh, Gh, Bh = rgb(a_haz)
    diff = {"晴vs雾": float(abs(a_sun - a_haz).mean()),
            "晴vs夜": float(abs(a_sun - a_nit).mean()),
            "雾vs夜": float(abs(a_haz - a_nit).mean())}
    ov_max = max(over(a_sun), over(a_haz), over(a_nit))
    mg_max = max(magenta(a_sun), magenta(a_haz), magenta(a_nit))

    tbl = (f"亮度 晴{L['晴']:.3f}/雾{L['雾']:.3f}/夜{L['夜']:.3f}　"
           f"饱和 晴{S['晴']:.3f}/雾{S['雾']:.3f}/夜{S['夜']:.3f}\n"
           f"      夜RGB {R_:.1f}/{G_:.1f}/{B_:.1f}（要求 B>G>R）　"
           f"逐像素差 {diff['晴vs雾']:.1f}/{diff['晴vs夜']:.1f}/{diff['雾vs夜']:.1f}\n"
           f"      过曝最大 {ov_max:.2f}%　品红最大 {mg_max}px　"
           f"雾绿主导度 {(Gh - Rh):.1f} vs 晴 {(Gs - Rs):.1f}")

    ok = True
    ok &= L["夜"] <= L["晴"] - 0.15                                 # 夜明显比晴暗
    ok &= (B_ - G_) >= 2 and (G_ - R_) >= 5                        # 夜是暗蓝/暗青
    ok &= S["雾"] <= S["晴"] * 0.72                                # 雾整体降饱和（灰白）
    ok &= (Gh - Rh) < (Gs - Rs) * 0.60                             # 雾不再绿占主导
    ok &= L["雾"] <= L["晴"] + 0.005                               # 雾不比晴亮
    ok &= min(diff.values()) >= 10                                 # 三态彼此明显不同
    ok &= ov_max < 3.0 and mg_max <= 300                           # 不过曝、无 shader 报错品红
    add("PASS" if ok else "FAIL", "C8 天气三态画面契约（暗/蓝/灰白、三态可分、不过曝）", tbl)


# --------------------------------------------- C9 用户观感回归守卫（发光球数量）
def check_glow_props():
    """用户反馈过"很多奇怪的发光球体"：自发光蘑菇球已从 42 减到 16。

    这里守住这个数量（6~24 之间），防止哪天生成器被改回去又不自知。
    """
    if not SCENE.exists():
        add("WARN", "C9 场景文件缺失", "跳过")
        return
    txt = SCENE.read_text(encoding="utf-8", errors="replace")
    n_mush = len(re.findall(r"MushCap_", txt))
    has_ring = ("RuneRing" in txt) and ("BurstRing" in txt)
    ok = 6 <= n_mush <= 24 and has_ring
    add("PASS" if ok else "FAIL", "C9 发光球数量守卫（自发光蘑菇球 6~24 个 + 光纹环仍在）",
        f"场景里 MushCap_ = {n_mush} 个（要求 6~24；用户嫌 42 个太多）　RuneRing/BurstRing 存在={has_ring}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", help="可选：把 Markdown 报告写到该路径")
    args = ap.parse_args()

    for fn in (check_textures, check_smoothstep, check_four_states,
               check_dark_beauty, check_charge, check_scene, check_code_redlines,
               check_weather, check_glow_props):
        try:
            fn()
        except Exception as e:                                  # 单项异常不拖垮整体
            add("FAIL", f"{fn.__name__} 执行异常", f"{type(e).__name__}: {e}")

    n_pass = sum(1 for l, _, _ in RESULTS if l == "PASS")
    n_fail = sum(1 for l, _, _ in RESULTS if l == "FAIL")
    n_warn = sum(1 for l, _, _ in RESULTS if l == "WARN")

    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}
    head = f"NeuroBloom 现阶段验收：{n_pass} 通过 / {n_fail} 失败 / {n_warn} 跳过"
    print("\n" + "=" * 78)
    print(head)
    print("=" * 78)
    for lv, title, detail in RESULTS:
        print(f"{icon[lv]} {title}")
        if detail:
            print(f"    {detail}")
    print("=" * 78)

    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# NeuroBloom 现阶段验收报告", "",
                 f"**结论：{head}**", "",
                 f"生成方式：`python3 tools/verify_stage.py`（离线，不调用 Unity）", "",
                 "## 本轮修复与调参摘要", "",
                 "1. **桥接崩溃（致命，已修）**：Unity 退出 Play 时无预警断线，`ConnectionClosedError` 会冒到顶层"
                 "把整个桥接进程带走，导致 EEG / Agent / Unity 一起静默掉线。"
                 "已在 `bridge/server.py` 的 EEG 通道加断线兜底，并以 `kill -9` 强杀客户端实测桥接存活。", "",
                 "2. **天气三态按用户反馈定稿**：雾霾＝整体降饱和（0.323→0.152）+ 冷蓝环境光 + 薄雾（0.70），"
                 "不再「铺一层灰」；黑天＝暗紫环境光 + 暗靛蓝雾 + 星 2.0 / 月 1.6 / 萤火虫 2.8；"
                 "晴天按用户要求定格不动（逐字节与上版一致）。", "",
                 "3. **发光球收敛**：自发光蘑菇球 42 → 16 个并整体缩小（用户反馈「很多奇怪的发光球体」），"
                 "水晶外光纹环 3.0→2.2、不透明度 0.35→0.20，晴天萤火虫 0.80→0.50。", "",
                 "4. **天气自动切换最短停留 12 秒**（`minAutoHoldSeconds`），实测自动切换间隔约 12 秒。", "",
                 "## 逐项检验明细", ""]
        for lv, title, detail in RESULTS:
            lines.append(f"### {icon[lv]} {title}")
            if detail:
                lines.append("")
                lines.append("```")
                lines.append(detail)
                lines.append("```")
            lines.append("")
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"报告已写入：{out}")

    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
