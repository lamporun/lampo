#!/usr/bin/env python3
"""Profile picture for @LampoGuard_bot (nwc-guard): Lampo's bolt inside a shield.

Same palette and bolt as make_brand.py. Telegram shows profile pictures as circles,
so the background is full bleed and the shield stays inside the central circle.

Outputs brand/guard-avatar-640.png and site/guard-avatar.png (512).
Run from the repo root: python3 brand/make_guard_avatar.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
BG = (14, 17, 22)
SHIELD_FILL = (24, 29, 37)
AMBER_TOP = (255, 205, 60)
AMBER_BOT = (245, 150, 10)
BOLT = [(336, 56), (156, 292), (252, 292), (196, 456), (372, 204), (276, 204)]  # 512 grid


def gradient(size, top=AMBER_TOP, bot=AMBER_BOT):
    w, h = size
    col = Image.new("RGB", (1, h))
    px = col.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bot))
    return col.resize((w, h))


def quad(p0, p1, p2, n=40):
    """Points of a quadratic Bezier curve."""
    out = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        out.append((x, y))
    return out


def shield(cx=256, top=88, w=300, shoulder=150, tip=448):
    """A heater shield on the 512 grid: gently arched top, straight sides that
    curve into a point."""
    l, r = cx - w / 2, cx + w / 2
    pts = []
    pts += quad((l, top + 26), (cx, top - 22), (r, top + 26))          # arched top edge
    pts += [(r, top + shoulder)]                                        # right side
    pts += quad((r, top + shoulder), (r, tip - 70), (cx, tip))[1:]      # right curve to the tip
    pts += quad((cx, tip), (l, tip - 70), (l, top + shoulder))[1:]      # left curve back up
    return pts


def render(px, ss=4):
    S = px * ss
    k = S / 512
    img = Image.new("RGB", (S, S), BG)

    # A soft amber glow behind the shield, for depth.
    glow = Image.new("L", (S, S), 0)
    ImageDraw.Draw(glow).ellipse([S * 0.18, S * 0.16, S * 0.82, S * 0.84], fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(S * 0.09))
    img.paste(Image.new("RGB", (S, S), (120, 78, 8)), (0, 0), glow)

    outer = [(x * k, y * k) for x, y in shield()]
    inner = [(x * k, y * k) for x, y in shield(top=88 + 24, w=300 - 48, shoulder=150 - 12, tip=448 - 34)]

    # Amber rim: the outer shield in the gradient, then the dark inner face.
    rim = Image.new("L", (S, S), 0)
    ImageDraw.Draw(rim).polygon(outer, fill=255)
    img.paste(gradient((S, S)), (0, 0), rim)
    face = Image.new("L", (S, S), 0)
    ImageDraw.Draw(face).polygon(inner, fill=255)
    img.paste(Image.new("RGB", (S, S), SHIELD_FILL), (0, 0), face)

    # The Lampo bolt, scaled into the shield's face.
    scale, cx, cy = 0.56, 256, 268
    bx = [(cx + (x - 264) * scale, cy + (y - 256) * scale) for x, y in BOLT]
    bolt = Image.new("L", (S, S), 0)
    ImageDraw.Draw(bolt).polygon([(x * k, y * k) for x, y in bx], fill=255)
    img.paste(gradient((S, S)), (0, 0), bolt)

    return img.resize((px, px), Image.LANCZOS)


def main():
    render(640).save(ROOT / "brand/guard-avatar-640.png", optimize=True)
    render(512).save(ROOT / "site/guard-avatar.png", optimize=True)
    # A circular preview, as Telegram shows it.
    big = render(640)
    mask = Image.new("L", big.size, 0)
    ImageDraw.Draw(mask).ellipse([0, 0, 639, 639], fill=255)
    preview = Image.new("RGB", big.size, (255, 255, 255))
    preview.paste(big, (0, 0), mask)
    preview.save(ROOT / "brand/guard-avatar-preview.png", optimize=True)


if __name__ == "__main__":
    main()
