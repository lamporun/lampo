#!/usr/bin/env python3
"""Turns a real recording (cast.json from demo-record.py) into a short MP4 for X.

Timings are the real ones, with long waits shortened (never stretched) and said
so on screen. Usage: demo-render.py cast.json out.mp4
"""
import json
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1280, 720, 30
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SANS_B = "/usr/share/fonts/truetype/ubuntu/Ubuntu[wdth,wght].ttf"
F = ImageFont.truetype(MONO, 23)
FB = ImageFont.truetype(MONO_B, 23)
BG, BAR, LINE = (10, 12, 15), (19, 23, 28), (35, 42, 49)
COL = {"prompt": (139, 148, 158), "cmd": (230, 237, 243), "out": (201, 209, 217), "note": (139, 148, 158),
       "paid": (247, 181, 0), "tok": (255, 255, 255), "ok": (63, 185, 80)}
CW = F.getbbox("M")[2]
COLS = (W - 80) // CW
LH = 34
MAX_LINES = (H - 150) // LH


def title_font(size, weight="Bold"):
    try:
        f = ImageFont.truetype(SANS_B, size)
        f.set_variation_by_name(weight)
        return f
    except Exception:
        return ImageFont.truetype(MONO_B, size)


def wrap(text, first_prefix=0):
    out, cur, width = [], "", COLS - first_prefix
    for word in text.split(" "):
        while len(word) > COLS:
            if cur:
                out.append(cur)
                cur, width = "", COLS
            out.append(word[:width])
            word, width = word[width:], COLS
        if len(cur) + len(word) + (1 if cur else 0) > width:
            out.append(cur)
            cur, width = word, COLS
        else:
            cur = f"{cur} {word}" if cur else word
    out.append(cur)
    return out


def frame(lines, cursor, caption):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 44], fill=BAR)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([20 + i * 24, 15, 34 + i * 24, 29], fill=c)
    d.text((W // 2, 22), caption, font=title_font(18, "Regular"), fill=(160, 168, 176), anchor="mm")
    y = 70
    for segs in lines[-MAX_LINES:]:
        x = 40
        for kind, text in segs:
            d.text((x, y), text, font=FB if kind in ("cmd", "paid", "ok") else F, fill=COL[kind])
            x += CW * len(text)
        y += LH
    if cursor:
        last = lines[-MAX_LINES:][-1] if lines else []
        x = 40 + CW * sum(len(t) for _, t in last)
        yy = 70 + LH * (min(len(lines), MAX_LINES) - 1)
        d.rectangle([x, yy + 3, x + CW - 2, yy + 25], fill=(201, 209, 217))
    d.text((W - 40, H - 34), "lampo.run", font=title_font(22), fill=(247, 181, 0), anchor="rm")
    d.text((40, H - 34), "real purchase on Bitcoin mainnet · waits shortened", font=title_font(18, "Regular"), fill=(110, 118, 126), anchor="lm")
    return img


def card(top, bottom):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((W // 2, H // 2 - 40), top, font=title_font(54), fill=(232, 234, 237), anchor="mm")
    d.text((W // 2, H // 2 + 40), bottom, font=title_font(34, "Regular"), fill=(247, 181, 0), anchor="mm")
    return img


def main():
    cast = json.load(open(sys.argv[1]))
    out = sys.argv[2]
    caption = "An agent buys AI inference over Lightning"
    frames = []  # (image, seconds)

    def add(img, secs):
        frames.append((img, max(1 / FPS, secs)))

    add(card("AI inference for agents", "paid in sats · no account · no API key"), 2.2)
    lines, prev_t = [], 0.0
    for e in cast:
        gap = e["t"] - prev_t
        prev_t = e["t"]
        k, s = e["k"], e["s"]
        if k == "cmd":
            if lines:
                lines.append([])
            add(frame(lines + [[("prompt", "$ ")]], True, caption), 0.5)
            typed = ""
            parts = wrap(s, 2)
            done = []
            for pi, part in enumerate(parts):
                for i in range(0, len(part) + 1, 3):
                    cur = done + [[("prompt", "$ ") if pi == 0 and not done else ("cmd", ""), ("cmd", part[:i])]]
                    add(frame(lines + cur, True, caption), 1 / 45 * 3)
                done.append([("prompt", "$ ") if pi == 0 else ("cmd", ""), ("cmd", part)])
            lines += done
            add(frame(lines, False, caption), 0.3)
        elif k == "tok":
            # Real streaming: append tokens to the answer, wrapping and honouring new lines.
            add_wait = min(gap, 0.35)
            if not lines or lines[-1] and lines[-1][0][0] != "tok":
                lines.append([("tok", "")])
            for n, piece in enumerate(s.split("\n")):
                if n:
                    lines.append([("tok", "")])
                cur = lines[-1][0][1] + piece
                wrapped = wrap(cur)
                lines[-1] = [("tok", wrapped[0])]
                for extra in wrapped[1:]:
                    lines.append([("tok", extra)])
            add(frame(lines, True, caption), add_wait)
        else:
            wait = min(gap, 1.6)
            add(frame(lines, True, caption), wait)
            kind = {"out": "out", "note": "note", "ok": "ok"}.get(k, "out")
            if s.startswith("⚡"):
                kind = "paid"
            for part in wrap(s):
                lines.append([(kind, part)])
            add(frame(lines, False, caption), 0.25)
    add(frame(lines, False, caption), 2.5)
    add(card("lampo.run", "Kimi · GLM · DeepSeek — paid per token over Lightning"), 3.0)

    with tempfile.TemporaryDirectory() as tmp:
        listing = []
        for i, (img, secs) in enumerate(frames):
            p = os.path.join(tmp, f"f{i:05d}.png")
            img.save(p)
            listing += [f"file '{p}'", f"duration {secs:.4f}"]
        listing.append(f"file '{os.path.join(tmp, f'f{len(frames) - 1:05d}.png')}'")
        lst = os.path.join(tmp, "list.txt")
        open(lst, "w").write("\n".join(listing) + "\n")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
                        "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-crf", "20",
                        "-movflags", "+faststart", out], check=True)
    total = sum(s for _, s in frames)
    print(f"video: {out} ({total:.1f} s, {os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    main()
