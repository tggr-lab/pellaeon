"""Compose the landing-page GIF from the hero_gif_shoot.py frames: python tools/hero_gif.py  (SCALE=0.8 by default)"""
import os, glob
from PIL import Image
SRC = "/tmp/pellaeon_gif"; PX0, PX1, GY0, GY1 = 2994, 3440, 158, 1311
SCALE = float(os.environ.get("SCALE", "0.8"))
def P(n): return os.path.join(SRC, n)
base = Image.open(P("p_empty.png")).convert("RGB")
toolbar = base.crop((0, 0, 1600, GY0)); cmdline = base.crop((0, GY1, 1600, 1371))
_cache = {}
def panel(name):
    if name in _cache: return _cache[name]
    col = Image.open(P(name)).convert("RGB").crop((PX0, GY0, PX1, GY1))     # 446 x 1153
    h = col.height; px = col.load(); bg = px[6, 700]
    def content(y):
        return any(abs(px[x, y][0]-bg[0]) + abs(px[x, y][1]-bg[1]) + abs(px[x, y][2]-bg[2]) > 60 for x in range(8, col.width-8, 3))
    cb = h - 261
    while cb > 100 and not content(cb): cb -= 1
    out = Image.new("RGB", (col.width, 700))
    if cb + 16 <= 440:
        out.paste(col.crop((0, 0, col.width, 440)), (0, 0))
    else:
        out.paste(col.crop((0, 0, col.width, 95)), (0, 0))
        out.paste(col.crop((0, cb + 16 - 345, col.width, cb + 16)), (0, 95))
    out.paste(col.crop((0, h - 260, col.width, h)), (0, 440))
    _cache[name] = out; return out
_r = {}
def render(name):
    if name not in _r: _r[name] = Image.open(P(name)).convert("RGB")
    return _r[name]
def frame(pname, rname):
    f = Image.new("RGB", (1600, 918)); f.paste(toolbar, (0, 0)); f.paste(render(rname), (0, GY0)); f.paste(panel(pname), (1154, GY0)); f.paste(cmdline, (0, 858))
    return f
seq = []   # (panel grab, render, ms)
seq.append(("p_empty.png", "r_empty_3d.png", 1400))
t1 = sorted(glob.glob(P("t1_*.png")))
for t in t1: seq.append((os.path.basename(t), "r_empty_3d.png", 70))
seq[-1] = (seq[-1][0], seq[-1][1], 600)
b1 = sorted(glob.glob(P("b1_*.png")))
def content_bottom(name):
    col = Image.open(P(name)).convert("RGB").crop((PX0, GY0, PX1, GY1)); px = col.load(); bg = px[6, 700]
    cb = col.height - 261
    while cb > 100 and not any(abs(px[x, cb][0]-bg[0]) + abs(px[x, cb][1]-bg[1]) + abs(px[x, cb][2]-bg[2]) > 60 for x in range(8, col.width-8, 3)): cb -= 1
    return cb
cbs = [content_bottom(os.path.basename(b)) for b in b1]
first_card = next((i for i, c in enumerate(cbs) if c > cbs[0] + 30), len(b1) - 1)
for b in b1[:first_card]: seq.append((os.path.basename(b), "r_empty_3d.png", 400))
card = os.path.basename(b1[first_card])
seq.append((card, "r1_00_3d.png", 500)); seq.append((card, "r1_01_3d.png", 500)); seq.append((card, "r1_04_3d.png", 400))
for b in b1[first_card + 1:]: seq.append((os.path.basename(b), "r1_04_3d.png", 400))
seq.append(("p_reply1.png", "r1_04_3d.png", 2200))
for t in sorted(glob.glob(P("t2_*.png"))): seq.append((os.path.basename(t), "r1_04_3d.png", 90))
seq[-1] = (seq[-1][0], seq[-1][1], 500)
b2 = sorted(glob.glob(P("b2_*.png")))
for b in b2: seq.append((os.path.basename(b), "r1_04_3d.png", 350))
spins = sorted(glob.glob(P("spin_*_3d.png")))[::2]
for s in spins: seq.append(("p_reply2.png", os.path.basename(s), 110))
seq[-1] = (seq[-1][0], seq[-1][1], 900)
frames = [frame(p, r) for p, r, _ in seq]
durs = [ms for _, _, ms in seq]
W = int(1600 * SCALE); H = int(918 * SCALE)
frames = [f.resize((W, H), Image.LANCZOS) for f in frames]
mosaic = Image.new("RGB", (W * 2, H)); mosaic.paste(frames[len(t1) + 12], (0, 0)); mosaic.paste(frames[-5], (W, 0))
pal = mosaic.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
q = [f.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG) for f in frames]
out = os.path.join(SRC, "out", "hero.gif")
q[0].save(out, save_all=True, append_images=q[1:], duration=durs, loop=0, optimize=True, disposal=1)
frames[len(t1) + 12].save(os.path.join(SRC, "out", "hero_poster.png"), optimize=True)
frames[-5].save(os.path.join(SRC, "out", "check_spin.png")); frames[len(t1)+6].save(os.path.join(SRC, "out", "check_busy.png"))
print(len(frames), "frames", sum(durs)/1000, "s", os.path.getsize(out)//1024, "KB", W, H)
