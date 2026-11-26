#!/usr/bin/env python3
"""Generate crisp multi-resolution Windows .ico icon for Colab Canvas Studio."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from PIL import Image, ImageDraw


def generate_icon(out_ico: Path) -> None:
    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()

    # Draw dark obsidian -> indigo -> violet gradient inside a rounded square
    pad = 24
    radius = 112
    for y in range(size):
        for x in range(size):
            # Rounded-rect signed distance test
            dx = max(pad + radius - x, 0, x - (size - 1 - pad - radius))
            dy = max(pad + radius - y, 0, y - (size - 1 - pad - radius))
            dist = math.hypot(dx, dy)
            if dist > radius:
                continue
            alpha = 255 if dist <= radius - 1.2 else int(max(0, min(255, (radius - dist) * 210)))
            t = (x + (size - y)) / (2.0 * size)
            r = int(14 + t * 40)
            g = int(16 + t * 18)
            b = int(32 + t * 52)
            # Soft central aurora glow
            cd = math.hypot(x - size / 2, y - size / 2) / (size * 0.45)
            glow = max(0.0, 1.0 - cd) ** 2
            r = min(255, int(r + glow * 95))
            g = min(255, int(g + glow * 72))
            b = min(255, int(b + glow * 165))
            px[x, y] = (r, g, b, alpha)

    draw = ImageDraw.Draw(img, "RGBA")

    # Subtle infinite canvas dot grid
    for gx in range(72, size - 64, 34):
        for gy in range(72, size - 64, 34):
            draw.ellipse((gx - 2, gy - 2, gx + 2, gy + 2), fill=(255, 255, 255, 38))

    # 4-point AI sparkle star (polygon approximation with smooth concave waist)
    def draw_star(cx: float, cy: float, r_out: float, r_in: float, fill: tuple[int, int, int, int]):
        pts = []
        steps = 48
        for i in range(steps):
            ang = (i / steps) * 2 * math.pi
            # Astroid-like concave 4-point star
            c, s = math.cos(ang), math.sin(ang)
            k = (abs(c) ** 0.55 + abs(s) ** 0.55)
            rad = r_out / max(1.0, k ** 1.45)
            rad = max(r_in, rad)
            pts.append((cx + c * rad, cy + s * rad))
        draw.polygon(pts, fill=fill)

    draw_star(256, 256, 138, 28, (238, 242, 255, 255))
    draw_star(360, 152, 38, 8, (52, 211, 153, 245))

    # Subtle glass border
    draw.rounded_rectangle(
        (pad, pad, size - 1 - pad, size - 1 - pad),
        radius=radius,
        outline=(255, 255, 255, 52),
        width=4,
    )

    out_ico.parent.mkdir(parents=True, exist_ok=True)
    img.save(
        out_ico,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print(f"Generated Windows icon: {out_ico}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("windows/AppIcon.ico")
    generate_icon(target)
