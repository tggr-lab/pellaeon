"""Compose the landing-page GIF from the hero_gif_shoot.py frames: python tools/hero_gif.py  (SCALE=0.8 by default)"""
import os, glob
from PIL import Image
SRC = "/tmp/pellaeon_gif"
# Window geometry comes from the shoot's own log line ("geom window WxH gfx X,Y WxH") rather than
# being pinned to the numbers one window happened to have: the panel column and the viewport box move
# whenever the dock is a different width, and a stale constant silently crops the wrong strip.
PX0, PX1, GY0, GY1, WIN_H = 2994, 3440, 158, 1311, 1371
try:
    import re as _re
    for _line in open(os.path.join(SRC, "log.txt")):
        _m = _re.match(r"geom window (\d+)x(\d+) gfx (\d+),(\d+) (\d+)x(\d+)", _line)
        if _m:
            _w, WIN_H, _gx, GY0, _gw, _gh = (int(g) for g in _m.groups())
            PX0, PX1, GY1 = _gx + _gw, _w, GY0 + _gh
            break
except OSError:
    pass
SCALE = float(os.environ.get("SCALE", "0.8"))
def P(n): return os.path.join(SRC, n)
base = Image.open(P("p_empty.png")).convert("RGB")
R_W, R_H = Image.open(P("r_empty_3d.png")).size          # the 3D renders' own size
FW = R_W + (PX1 - PX0); FH = GY0 + R_H + (WIN_H - GY1)   # composed frame: render + panel column
toolbar = base.crop((0, 0, FW, GY0)); cmdline = base.crop((0, GY1, FW, WIN_H))
_cache = {}
def panel(name):
    if name in _cache: return _cache[name]
    col = Image.open(P(name)).convert("RGB").crop((PX0, GY0, PX1, GY1))     # 446 x 1153
    h = col.height; px = col.load(); bg = px[6, 700]
    def content(y):
        return any(abs(px[x, y][0]-bg[0]) + abs(px[x, y][1]-bg[1]) + abs(px[x, y][2]-bg[2]) > 60 for x in range(8, col.width-8, 3))
    cb = h - 261
    while cb > 100 and not content(cb): cb -= 1
    out = Image.new("RGB", (col.width, R_H))
    if cb + 16 <= 440:
        out.paste(col.crop((0, 0, col.width, 440)), (0, 0))
    else:
        out.paste(col.crop((0, 0, col.width, 95)), (0, 0))
        out.paste(col.crop((0, cb + 16 - 345, col.width, cb + 16)), (0, 95))
    out.paste(col.crop((0, h - 260, col.width, h)), (0, R_H - 260))
    _cache[name] = out; return out
_r = {}
def render(name):
    if name not in _r: _r[name] = Image.open(P(name)).convert("RGB")
    return _r[name]
def frame(pname, rname):
    f = Image.new("RGB", (FW, FH)); f.paste(toolbar, (0, 0)); f.paste(render(rname), (0, GY0))
    f.paste(panel(pname), (R_W, GY0)); f.paste(cmdline, (0, GY0 + R_H))
    return f
# Timing rule for the landing-page loop: a reader decides in the first couple of seconds, so the
# protein has to be on screen by then. The typing is played fast and the waiting is compressed to a
# beat or two rather than replayed in real time; nothing is added that did not happen, and the loop
# ends holding the finished result instead of snapping straight back to an empty scene.
seq = []   # (panel grab, render, ms)
seq.append(("p_empty.png", "r_empty_3d.png", 450))
t1 = sorted(glob.glob(P("t1_*.png")))
for t in t1: seq.append((os.path.basename(t), "r_empty_3d.png", 32))
seq[-1] = (seq[-1][0], seq[-1][1], 380)
b1 = sorted(glob.glob(P("b1_*.png")))
def content_bottom(name):
    col = Image.open(P(name)).convert("RGB").crop((PX0, GY0, PX1, GY1)); px = col.load(); bg = px[6, 700]
    cb = col.height - 261
    while cb > 100 and not any(abs(px[x, cb][0]-bg[0]) + abs(px[x, cb][1]-bg[1]) + abs(px[x, cb][2]-bg[2]) > 60 for x in range(8, col.width-8, 3)): cb -= 1
    return cb
cbs = [content_bottom(os.path.basename(b)) for b in b1]
first_card = next((i for i, c in enumerate(cbs) if c > cbs[0] + 30), len(b1) - 1)
WAIT = b1[:first_card]
for b in (WAIT[-2:] if len(WAIT) > 2 else WAIT): seq.append((os.path.basename(b), "r_empty_3d.png", 300))
card = os.path.basename(b1[first_card])
seq.append((card, "r1_00_3d.png", 500)); seq.append((card, "r1_01_3d.png", 500)); seq.append((card, "r1_04_3d.png", 400))
# the model's remaining thinking time is a beat, not a replay: two frames, then the finished reply
for b in b1[first_card + 1:][-2:]: seq.append((os.path.basename(b), "r1_04_3d.png", 300))
seq.append(("p_reply1.png", "r1_04_3d.png", 1600))
for t in sorted(glob.glob(P("t2_*.png"))): seq.append((os.path.basename(t), "r1_04_3d.png", 90))
seq[-1] = (seq[-1][0], seq[-1][1], 500)
b2 = sorted(glob.glob(P("b2_*.png")))
for b in b2[-3:]: seq.append((os.path.basename(b), "r1_04_3d.png", 300))
spins = sorted(glob.glob(P("spin_*_3d.png")))[::2]
for sp in spins: seq.append(("p_reply2.png", os.path.basename(sp), 110))
seq[-1] = (seq[-1][0], seq[-1][1], 2400)     # hold the finished, spun-to-rest result before looping
frames = [frame(p, r) for p, r, _ in seq]
durs = [ms for _, _, ms in seq]
W = int(FW * SCALE); H = int(FH * SCALE)
frames = [f.resize((W, H), Image.LANCZOS) for f in frames]
# Palette from several frames across the loop, not two: with only the opening and a spin frame in it
# the quantizer had no reason to keep the greens and reds of later scenes, which is how a stated
# green reads back as blue-grey in the encoded file.
def palette_of(fs, colors=255):
    picks = [fs[int(k * (len(fs) - 1) / 5)] for k in range(6)]
    mosaic = Image.new("RGB", (picks[0].width * len(picks), picks[0].height))
    for k, f in enumerate(picks): mosaic.paste(f, (k * f.width, 0))
    return mosaic.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)

def encode(fs, ds, path, poster_path=None, poster_index=-1, colors=255):
    pal = palette_of(fs, colors)
    q = [f.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG) for f in fs]
    q[0].save(path, save_all=True, append_images=q[1:], duration=ds, loop=0, optimize=True, disposal=1)
    if poster_path: fs[poster_index].save(poster_path, optimize=True)
    print(os.path.basename(path), len(fs), "frames", sum(ds) / 1000, "s",
          os.path.getsize(path) // 1024, "KB", fs[0].size)

OUTDIR = os.path.join(SRC, "out"); os.makedirs(OUTDIR, exist_ok=True)
# The poster is the settled end of the loop: the finished structure, the reply that produced it and
# an empty composer - not a mid-turn frame with "Nothing open" over a protein that is already there.
encode(frames, durs, os.path.join(OUTDIR, "hero.gif"), os.path.join(OUTDIR, "hero_poster.png"), -1)
frames[-1].save(os.path.join(OUTDIR, "check_spin.png"))

# ---------------------------------------------------------------- phone composition
# Not a crop of the desktop frame: a portrait stack, molecule on top at full width, the request and
# the reply underneath at a size a phone can read. Built from the same verified frames.
MW, M3D, MPANEL = 640, 430, 470
def mobile(pname, rname):
    f = Image.new("RGB", (MW, M3D + MPANEL), "#101418")
    r = render(rname)
    sc = MW / r.width
    r2 = r.resize((MW, int(r.height * sc)), Image.LANCZOS)
    top = max(0, (r2.height - M3D) // 2)
    f.paste(r2.crop((0, top, MW, min(r2.height, top + M3D))), (0, 0))
    col = Image.open(P(pname)).convert("RGB").crop((PX0, GY0, PX1, GY1))     # the panel at 2x, so it scales up cleanly
    cb = content_bottom(pname)          # measured before scaling: the resample blurs the edge pixels the probe reads
    ps = MW / col.width
    body = int((MPANEL - int(150 * ps)) / ps)
    f.paste(col.crop((0, max(0, cb + 8 - body), col.width, max(body, cb + 8)))
              .resize((MW, MPANEL - int(150 * ps)), Image.LANCZOS), (0, M3D))
    f.paste(col.crop((0, col.height - 150, col.width, col.height))
              .resize((MW, int(150 * ps)), Image.LANCZOS), (0, M3D + MPANEL - int(150 * ps)))
    return f

mframes = [mobile(p, r) for p, r, _ in seq]
# a phone carries this over a phone connection: fewer palette entries, same composition
encode(mframes, durs, os.path.join(OUTDIR, "hero_mobile.gif"), os.path.join(OUTDIR, "hero_mobile_poster.png"), -1, colors=96)
