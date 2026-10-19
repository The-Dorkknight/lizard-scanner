#!/usr/bin/env python3
# Lizard Scanner: draws the app icon (no picture files needed).
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""A hand-held scanner seen from the front: two lenses, a dot projector, a glowing ring.
Writes icons/icon-*.png and the web page's icons in docs/."""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def icon(size=512):
    s = size / 512.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((16 * s, 16 * s, 496 * s, 496 * s), radius=110 * s, fill=(36, 92, 64, 255))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((96 * s, 96 * s, 416 * s, 416 * s), outline=(255, 214, 140, 255), width=int(30 * s))
    glow = glow.filter(ImageFilter.GaussianBlur(14 * s))
    img.alpha_composite(glow)
    d = ImageDraw.Draw(img)
    d.ellipse((110 * s, 110 * s, 402 * s, 402 * s), outline=(255, 236, 196, 255), width=int(14 * s))
    d.ellipse((130 * s, 130 * s, 382 * s, 382 * s), fill=(22, 26, 24, 255))
    rng = np.random.default_rng(7)
    for _ in range(int(140 * min(1, s * 2))):
        a, r = rng.uniform(0, 2 * np.pi), 118 * s * np.sqrt(rng.uniform(0, 1))
        x, y = 256 * s + r * np.cos(a), 256 * s + r * np.sin(a)
        rr = max(1, 2.6 * s)
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(214, 74, 60, 255))
    for cx in (196, 316):
        d.ellipse(((cx - 34) * s, 222 * s, (cx + 34) * s, 290 * s), fill=(8, 10, 12, 255),
                  outline=(140, 150, 160, 255), width=max(1, int(5 * s)))
        d.ellipse(((cx - 12) * s, 236 * s, (cx + 2) * s, 250 * s), fill=(220, 230, 240, 200))
    return img


def main():
    os.makedirs(os.path.join(ROOT, "icons"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "docs", "images"), exist_ok=True)
    big = icon(512)
    for n in (16, 32, 48, 64, 128, 256, 512):
        (big if n == 512 else icon(512).resize((n, n), Image.LANCZOS)).save(os.path.join(ROOT, "icons", f"icon-{n}.png"))
    big.save(os.path.join(ROOT, "docs", "images", "icon-512.png"))
    big.resize((180, 180), Image.LANCZOS).save(os.path.join(ROOT, "docs", "images", "apple-touch-icon.png"))
    big.resize((64, 64), Image.LANCZOS).save(os.path.join(ROOT, "docs", "images", "favicon-64.png"))


if __name__ == "__main__":
    main()
