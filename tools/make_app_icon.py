#!/usr/bin/env python3
"""生成 NeuroBloom 桌面 App 的图标（.icns）。

为什么要自己画：这台机器没有设计软件，也不想为一张图标去生成大图。
配色和 dashboard/index.html 完全一致（青 → 紫 → 粉），一眼能认出是同一个东西。

用法：
    .venv/bin/python tools/make_app_icon.py
产出：
    dashboard/AppIcon.icns
"""
from __future__ import annotations

import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT_ICNS = ROOT / "dashboard" / "AppIcon.icns"

SIZE = 1024
SS = 2                      # 超采样倍数，边缘才不会有锯齿
W = SIZE * SS

CYAN = (34, 211, 238)
VIOLET = (168, 85, 247)
PINK = (244, 114, 182)
DEEP = (8, 9, 22)


def lerp(c1, c2, t):
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))


def pick(t: float):
    """青 → 紫 → 粉 的三段渐变。"""
    t = max(0.0, min(1.0, t))
    if t < 0.55:
        return lerp(CYAN, VIOLET, t / 0.55)
    return lerp(VIOLET, PINK, (t - 0.55) / 0.45)


def rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return m


def main() -> int:
    # ---------- 底板：深色圆角方块 + 斜向暗渐变 ----------
    base = Image.new("RGB", (W, W), DEEP)
    px = np.zeros((W, W, 3), dtype=np.float32)
    ys, xs = np.mgrid[0:W, 0:W]
    diag = (xs + ys) / (2.0 * W)                     # 0 左上 → 1 右下
    for i in range(3):
        px[..., i] = DEEP[i] + (28 - DEEP[i]) * diag
    # 左上角补一点紫，右下补一点青，避免死板
    glow = np.clip(1.0 - ((xs - W * 0.18) ** 2 + (ys - W * 0.15) ** 2) ** 0.5 / (W * 0.62), 0, 1) ** 2
    for i in range(3):
        px[..., i] += (VIOLET[i] - px[..., i]) * glow * 0.30
    glow2 = np.clip(1.0 - ((xs - W * 0.84) ** 2 + (ys - W * 0.88) ** 2) ** 0.5 / (W * 0.55), 0, 1) ** 2
    for i in range(3):
        px[..., i] += (CYAN[i] - px[..., i]) * glow2 * 0.22
    base = Image.fromarray(np.clip(px, 0, 255).astype(np.uint8), "RGB").convert("RGBA")

    # ---------- 中间的光晕：放松度圆环的内辉 ----------
    halo = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    cx = cy = W / 2
    for r in range(int(W * 0.30), 0, -6):
        t = 1 - r / (W * 0.30)
        col = pick(0.35 + 0.25 * math.sin(t * 3))
        hd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col + (int(16 * t ** 1.6),))
    halo = halo.filter(ImageFilter.GaussianBlur(W * 0.02))
    base = Image.alpha_composite(base, halo)

    # ---------- 主圆环：250° 的渐变弧，开口在右上 ----------
    ring = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    R = W * 0.335
    width = int(W * 0.072)
    start_deg, sweep = 128.0, 252.0
    steps = 720
    for i in range(steps):
        a0 = start_deg + sweep * i / steps
        a1 = start_deg + sweep * (i + 1) / steps + 0.45      # 轻微重叠，避免接缝
        col = pick(i / steps)
        rd.arc(
            (cx - R, cy - R, cx + R, cy + R),
            start=a0, end=a1, fill=col + (255,), width=width,
        )
    # 弧的光晕
    bloom = ring.filter(ImageFilter.GaussianBlur(W * 0.035))
    bloom.putalpha(bloom.getchannel("A").point(lambda v: int(v * 0.85)))
    base = Image.alpha_composite(base, bloom)
    base = Image.alpha_composite(base, ring)

    # ---------- 端点小圆头（更像仪表盘） ----------
    caps = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    cd = ImageDraw.Draw(caps)
    for ang, col in ((start_deg, pick(0.0)), (start_deg + sweep, pick(1.0))):
        rad = math.radians(ang)
        x, y = cx + R * math.cos(rad), cy + R * math.sin(rad)
        rr = width * 0.62
        cd.ellipse((x - rr, y - rr, x + rr, y + rr), fill=col + (255,))
        # 内白点，像灯
        rr2 = width * 0.24
        cd.ellipse((x - rr2, y - rr2, x + rr2, y + rr2), fill=(255, 255, 255, 235))
    base = Image.alpha_composite(base, caps.filter(ImageFilter.GaussianBlur(W * 0.002)))
    base = Image.alpha_composite(base, caps)

    # ---------- 中心：一瓣很淡的花心 ----------
    core = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    kd = ImageDraw.Draw(core)
    petals = 6
    for k in range(petals):
        ang = math.pi * 2 * k / petals - math.pi / 2
        px_, py_ = cx + math.cos(ang) * W * 0.055, cy + math.sin(ang) * W * 0.055
        rx, ry = W * 0.062, W * 0.030
        box = (px_ - rx, py_ - ry, px_ + rx, py_ + ry)
        layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.ellipse(box, fill=(255, 255, 255, 190))
        layer = layer.rotate(math.degrees(ang) + 90, center=(cx, cy), resample=Image.BICUBIC)
        core = Image.alpha_composite(core, layer)
    kd.ellipse((cx - W * 0.026, cy - W * 0.026, cx + W * 0.026, cy + W * 0.026),
               fill=pick(0.4) + (255,))
    base = Image.alpha_composite(base, core.filter(ImageFilter.GaussianBlur(W * 0.004)))
    base = Image.alpha_composite(base, core)

    # ---------- 裁成 macOS 的圆角 + 缩小 ----------
    mask = rounded_mask(W, int(W * 0.225))
    base.putalpha(Image.composite(base.getchannel("A"), Image.new("L", (W, W), 0), mask))
    icon = base.resize((SIZE, SIZE), Image.LANCZOS)

    # ---------- 打包成 .icns ----------
    with tempfile.TemporaryDirectory() as td:
        iconset = Path(td) / "AppIcon.iconset"
        iconset.mkdir()
        for s in (16, 32, 128, 256, 512):
            icon.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
            icon.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
        OUT_ICNS.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT_ICNS)], check=True)

    print(f"图标已生成: {OUT_ICNS}  ({OUT_ICNS.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
