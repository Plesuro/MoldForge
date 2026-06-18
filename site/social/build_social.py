#!/usr/bin/env python3
"""Build brand-matched social/Reddit graphics for MoldForge from the site renders.

Run it from anywhere:  python3 build_social.py
Outputs reddit_square.png / reddit_wide.png / reddit_grid.png next to this file,
sourcing the transparent renders from ../assets. Requires Pillow + numpy.
"""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
AST = os.path.join(HERE, "..", "assets")
OUT = HERE

# brand palette
BG   = (20, 20, 24)
PINK = (255, 92, 124)
PURP = (176, 108, 255)
TEXT = (236, 236, 241)
MUT  = (154, 154, 168)
DIM  = (111, 111, 125)
PANEL= (32, 32, 40)
LINE = (60, 60, 75)

def F(path, sz): return ImageFont.truetype(path, sz)
SANS_B = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
SANS_R = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
MONO_R = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

def glow(W, H, cx, cy, rad, rgb, a):
    yy, xx = np.ogrid[:H, :W]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / rad
    al = np.clip(1 - d, 0, 1) ** 2 * a
    arr = np.zeros((H, W, 4), np.uint8)
    arr[..., 0], arr[..., 1], arr[..., 2] = rgb
    arr[..., 3] = al.astype(np.uint8)
    return Image.fromarray(arr, "RGBA")

def vignette(W, H, strength=0.55):
    yy, xx = np.ogrid[:H, :W]
    cx, cy = W / 2, H / 2
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / np.sqrt(cx ** 2 + cy ** 2)
    al = np.clip(d - 0.45, 0, 1) * strength * 255
    arr = np.zeros((H, W, 4), np.uint8)
    arr[..., 3] = al.astype(np.uint8)
    return Image.fromarray(arr, "RGBA")

def background(W, H):
    bg = Image.new("RGBA", (W, H), BG + (255,))
    bg.alpha_composite(glow(W, H, W * 0.22, H * 0.18, max(W, H) * 0.62, PINK, 130))
    bg.alpha_composite(glow(W, H, W * 0.85, H * 0.92, max(W, H) * 0.66, PURP, 120))
    bg.alpha_composite(vignette(W, H))
    return bg

def hgrad(w, h, c1, c2):
    t = np.linspace(0, 1, max(w, 1))
    arr = np.zeros((h, w, 4), np.uint8)
    for i, c in enumerate(zip(c1, c2)):
        arr[..., i] = (c[0] + (c[1] - c[0]) * t).astype(np.uint8)
    arr[..., 3] = 255
    return Image.fromarray(arr, "RGBA")

def grad_text(text, font, c1=PINK, c2=PURP):
    bb = font.getbbox(text); w, h = bb[2] - bb[0], bb[3] - bb[1]
    pad = 10
    mask = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    ImageDraw.Draw(mask).text((pad - bb[0], pad - bb[1]), text, font=font, fill=255)
    out = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    out.paste(hgrad(*mask.size, c1, c2), (0, 0), mask)
    return out, pad

def paste_shadow(canvas, img, xy, blur=34, off=(0, 30), alpha=150):
    a = img.split()[3]
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sh.paste(Image.new("RGBA", img.size, (0, 0, 0, alpha)), (0, 0), a)
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(sh, (xy[0] + off[0], xy[1] + off[1]))
    canvas.alpha_composite(img, xy)

def scale_w(im, w): return im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
def scale_h(im, h): return im.resize((round(im.width * h / im.height), h), Image.LANCZOS)

def droplet(size, color=PINK):
    s = size * 4
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.ellipse([s * 0.16, s * 0.40, s * 0.84, s * 0.99], fill=color + (255,))
    d.polygon([(s * 0.5, s * 0.02), (s * 0.17, s * 0.56), (s * 0.83, s * 0.56)], fill=color + (255,))
    return im.resize((size, size), Image.LANCZOS)

def wordmark(canvas, x, y, dh=34, fs=38):
    canvas.alpha_composite(droplet(dh), (x, y + 2))
    ImageDraw.Draw(canvas).text((x + dh + 12, y - 4), "MoldForge", font=F(SANS_B, fs), fill=TEXT + (255,))

def pill(canvas, x, y, text, font, pad=(16, 9), fg=TEXT, bg=PANEL, border=LINE, grad=False):
    d = ImageDraw.Draw(canvas)
    bb = d.textbbox((0, 0), text, font=font); tw, th = bb[2] - bb[0], bb[3] - bb[1]
    w, h = tw + 2 * pad[0], th + 2 * pad[1]; r = h // 2
    if grad:
        g = hgrad(w, h, PINK, PURP)
        m = Image.new("L", (w, h), 0); ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255)
        canvas.paste(g, (x, y), m)
    else:
        d.rounded_rectangle([x, y, x + w, y + h], r, fill=bg + (255,), outline=border + (255,), width=1)
    d.text((x + pad[0] - bb[0], y + pad[1] - bb[1]), text, font=font, fill=(fg if not grad else (255, 255, 255)) + (255,))
    return w, h

def make_square():
    W = H = 1080; M = 66
    c = background(W, H); d = ImageDraw.Draw(c)
    wordmark(c, M, 52, 34, 38)
    pw, _ = pill(Image.new("RGBA", (W, H)), 0, 0, "FREE · GPL-3.0", F(MONO_B, 22), grad=True)
    pill(c, W - M - pw, 56, "FREE · GPL-3.0", F(MONO_B, 22), grad=True)
    hf = F(SANS_B, 62)
    d.text((M, 158), "Turn any sculpt into", font=hf, fill=TEXT + (255,))
    d.text((M, 228), "a print-ready", font=hf, fill=TEXT + (255,))
    gt, pad = grad_text("silicone mold.", hf)
    base_w = d.textlength("a print-ready ", font=hf)
    c.alpha_composite(gt, (int(M + base_w) - pad, 228 - pad))
    r = scale_w(Image.open(os.path.join(AST, "hero.png")).convert("RGBA"), 880)
    paste_shadow(c, r, ((W - r.width) // 2, 372))
    tf = F(MONO_R, 24); tag = "auto split · pour funnels · keyed base plate · STL export"
    d.text(((W - d.textlength(tag, font=tf)) // 2, H - 58), tag, font=tf, fill=MUT + (255,))
    c.convert("RGB").save(os.path.join(OUT, "reddit_square.png"), quality=95)

def make_wide():
    W, H = 1600, 900; M = 74
    c = background(W, H); d = ImageDraw.Draw(c)
    wordmark(c, M, 64, 34, 38)
    d.text((M, 150), "FREE · BLENDER 5.1 · GPL-3.0", font=F(MONO_B, 22), fill=PINK + (255,))
    hf = F(SANS_B, 60)
    d.text((M, 206), "Turn any sculpt", font=hf, fill=TEXT + (255,))
    d.text((M, 274), "into a print-ready", font=hf, fill=TEXT + (255,))
    gt, pad = grad_text("silicone mold.", hf)
    c.alpha_composite(gt, (M - pad, 342 - pad))
    cx, cy = M, 430
    for t in ["Auto split", "Pour funnels", "Keyed base plate", "STL export"]:
        w, h = pill(c, cx, cy, t, F(SANS_R, 22)); cx += w + 12
        if cx > 720: cx, cy = M, cy + h + 12
    d.text((M, 560), "moldforge.plesuro.eu", font=F(MONO_R, 24), fill=DIM + (255,))
    r = scale_h(Image.open(os.path.join(AST, "g2_radial.png")).convert("RGBA"), 740)
    if r.width > 820: r = scale_w(r, 820)
    paste_shadow(c, r, (W - r.width - 40, (H - r.height) // 2))
    c.convert("RGB").save(os.path.join(OUT, "reddit_wide.png"), quality=95)

def make_grid():
    W, H = 1600, 1080; M = 60
    c = background(W, H); d = ImageDraw.Draw(c)
    wordmark(c, M, 46, 34, 40)
    d.text((M, 110), "One click: sculpt  →  printable silicone mold", font=F(SANS_B, 40), fill=TEXT + (255,))
    pill(c, M, 172, "FREE BLENDER 5.1 ADD-ON · GPL-3.0", F(MONO_B, 20), grad=True)
    cells = [("hero.png", "Full mold system"), ("g1_section.png", "Funnel & cavity"),
             ("g2_radial.png", "Radial multi-part"), ("g3_plate.png", "Keyed base plate")]
    gap, top = 26, 250
    pw = (W - 2 * M - gap) // 2; ph = (H - top - M - gap) // 2
    for i, (fn, cap) in enumerate(cells):
        col, row = i % 2, i // 2
        x, y = M + col * (pw + gap), top + row * (ph + gap)
        d.rounded_rectangle([x, y, x + pw, y + ph], 18, fill=PANEL + (235,), outline=LINE + (255,), width=1)
        im = Image.open(os.path.join(AST, fn)).convert("RGBA")
        fit = min((pw - 70) / im.width, (ph - 90) / im.height)
        im = im.resize((int(im.width * fit), int(im.height * fit)), Image.LANCZOS)
        paste_shadow(c, im, (x + (pw - im.width) // 2, y + (ph - 50 - im.height) // 2 + 8), blur=22, off=(0, 16), alpha=120)
        cf = F(SANS_B, 24)
        d.text((x + (pw - d.textlength(cap, font=cf)) // 2, y + ph - 42), cap, font=cf, fill=MUT + (255,))
    c.convert("RGB").save(os.path.join(OUT, "reddit_grid.png"), quality=95)

if __name__ == "__main__":
    make_square(); make_wide(); make_grid()
    print("wrote reddit_square.png, reddit_wide.png, reddit_grid.png to", OUT)
