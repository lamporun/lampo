#!/usr/bin/env python3
"""Generates Lampo's visual identity from one set of shapes.

Outputs:
  site/logo.svg, site/favicon.svg, site/apple-touch-icon.png, site/og-image.png
  brand/avatar-512.png  (Nostr, GitHub, Satring profile picture)
  brand/banner-1500x500.png  (Nostr banner)

Run from the repo root: python3 brand/make_brand.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
BG = (14, 17, 22)          # near-black, matches the site
BG2 = (24, 29, 37)         # slightly lighter, for depth
AMBER_TOP = (255, 205, 60)
AMBER_BOT = (245, 150, 10)
INK = (232, 234, 237)
MUTED = (154, 163, 173)
FONT_B = "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf"
FONT_R = "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf"
FONT_M = "/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf"

# The bolt on a 512 grid: sharp top, two horizontal notches, sharp tip.
BOLT = [(336, 56), (156, 292), (252, 292), (196, 456), (372, 204), (276, 204)]
RADIUS = 112  # corner radius of the 512 tile


def font(path, size, weight):
    """Ubuntu fonts ship as variable fonts: pick the named weight explicitly."""
    f = ImageFont.truetype(path, size)
    try:
        f.set_variation_by_name(weight)
    except (OSError, ValueError):
        pass  # static font: nothing to set
    return f


def bolt_svg(scale=1.0, dx=0.0, dy=0.0):
    pts = " ".join(f"{x * scale + dx:.1f},{y * scale + dy:.1f}" for x, y in BOLT)
    return f'<polygon points="{pts}" fill="url(#amber)"/>'


def write_svgs():
    defs = (
        '<defs><linearGradient id="amber" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="rgb{AMBER_TOP}"/><stop offset="1" stop-color="rgb{AMBER_BOT}"/>'
        "</linearGradient></defs>"
    )
    tile = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="Lampo">'
        f"{defs}"
        f'<rect width="512" height="512" rx="{RADIUS}" fill="rgb{BG}"/>'
        f"{bolt_svg()}</svg>\n"
    )
    (ROOT / "site/logo.svg").write_text(tile)
    (ROOT / "site/favicon.svg").write_text(tile)


def gradient(size):
    w, h = size
    g = Image.new("RGB", size)
    px = g.load()
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(round(a + (b - a) * t) for a, b in zip(AMBER_TOP, AMBER_BOT))
        for x in range(w):
            px[x, y] = c
    return g


def tile(px, rounded=True, ss=4):
    """Square icon of px*px, drawn at ss-times resolution for clean edges."""
    S = px * ss
    k = S / 512
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=round(RADIUS * k), fill=BG)
    else:
        d.rectangle([0, 0, S, S], fill=BG)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).polygon([(x * k, y * k) for x, y in BOLT], fill=255)
    img.paste(gradient((S, S)), (0, 0), mask)
    return img.resize((px, px), Image.LANCZOS)


def canvas(w, h, ss=2):
    img = Image.new("RGB", (w * ss, h * ss), BG)
    d = ImageDraw.Draw(img)
    # Soft diagonal band for depth, very subtle.
    d.polygon([(w * ss * 0.55, 0), (w * ss, 0), (w * ss, h * ss), (w * ss * 0.35, h * ss)], fill=BG2)
    return img, d


def og_image():
    W, H, ss = 1200, 630, 2
    img, d = canvas(W, H, ss)
    icon = tile(300, rounded=True, ss=4).resize((300 * ss, 300 * ss), Image.LANCZOS)
    img.paste(icon, (90 * ss, 165 * ss), icon)
    d.text((440 * ss, 175 * ss), "Lampo", font=font(FONT_B, 120 * ss, "Bold"), fill=INK)
    d.text((444 * ss, 330 * ss), "Top AI models,", font=font(FONT_R, 44 * ss, "Regular"), fill=INK)
    d.text((444 * ss, 384 * ss), "paid in sats.", font=font(FONT_R, 44 * ss, "Regular"), fill=AMBER_TOP)
    d.text((444 * ss, 470 * ss), "No card  ·  no account  ·  lampo.run", font=font(FONT_M, 28 * ss, "Regular"), fill=MUTED)
    img.resize((W, H), Image.LANCZOS).save(ROOT / "site/og-image.png", optimize=True)


def banner():
    W, H, ss = 1500, 500, 2
    img, d = canvas(W, H, ss)
    d.text((560 * ss, 150 * ss), "AI inference for agents,", font=font(FONT_B, 64 * ss, "Bold"), fill=INK)
    d.text((560 * ss, 235 * ss), "paid in sats.", font=font(FONT_B, 64 * ss, "Bold"), fill=AMBER_TOP)
    d.text((562 * ss, 340 * ss), "export L402_PROXY_URL=https://lampo.run/v1", font=font(FONT_M, 30 * ss, "Regular"), fill=MUTED)
    img.resize((W, H), Image.LANCZOS).save(ROOT / "brand/banner-1500x500.png", optimize=True)


def main():
    (ROOT / "brand").mkdir(exist_ok=True)
    write_svgs()
    tile(512, rounded=False).convert("RGB").save(ROOT / "brand/avatar-512.png", optimize=True)
    tile(180, rounded=False).convert("RGB").save(ROOT / "site/apple-touch-icon.png", optimize=True)
    og_image()
    banner()
    print("brand assets written")


if __name__ == "__main__":
    main()
