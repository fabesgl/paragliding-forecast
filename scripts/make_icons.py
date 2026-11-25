"""Draw the app icons into pwa/icons/. Needs Pillow (pip install pillow); the PNGs are committed, so the daily job does not need it.
Usage: python scripts/make_icons.py"""
from pathlib import Path
from PIL import Image, ImageDraw
OUT = Path(__file__).resolve().parent.parent / "pwa" / "icons"; OUT.mkdir(parents=True, exist_ok=True)
BG, BACK, FRONT, SNOW, SUN = "#0f1722", "#2d4a7c", "#5b84c4", "#e8edf3", "#ef9f27"

def draw(size, safe):
    """safe = fraction of the square kept clear around the artwork (maskable icons may be cropped to a circle, so they need ~20%)."""
    S = 1024; im = Image.new("RGB", (S, S), BG); d = ImageDraw.Draw(im)
    m = S * safe; w = S - 2 * m
    p = lambda x, y: (m + x * w, m + y * w)                                    # artwork lives in a 0-1 box inside the safe area
    d.ellipse([*p(0.68, 0.10), *p(0.88, 0.30)], fill=SUN)                      # sun
    d.polygon([p(0.00, 0.86), p(0.30, 0.38), p(0.52, 0.62), p(0.70, 0.46), p(1.00, 0.86)], fill=BACK)       # back range
    d.polygon([p(0.12, 0.90), p(0.46, 0.22), p(0.80, 0.90)], fill=FRONT)       # main peak
    d.polygon([p(0.46, 0.22), p(0.385, 0.35), p(0.43, 0.32), p(0.46, 0.37), p(0.50, 0.32), p(0.54, 0.35)], fill=SNOW)   # snow cap
    d.rectangle([*p(0, 0.86), *p(1, 0.93)], fill="#16253d")                    # valley floor
    return im.resize((size, size), Image.LANCZOS)

for name, size, safe in [("icon-192.png", 192, 0.06), ("icon-512.png", 512, 0.06), ("maskable-192.png", 192, 0.20), ("maskable-512.png", 512, 0.20),
                         ("apple-touch-icon.png", 180, 0.08), ("favicon-32.png", 32, 0.04)]:
    draw(size, safe).save(OUT / name, optimize=True); print("wrote", name)
