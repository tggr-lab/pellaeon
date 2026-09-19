"""Compose docs/img/classic.png from three real captures in ~/pellaeon_shots/ (see RELEASING.md, "Classic screenshot"):
panel_c.png  = headless Chromium screenshot of the classic panel page (chromium --headless=new --screenshot=... --window-size=470,860 --timeout=10000 http://127.0.0.1:8765/)
launcher_c.png / chimera_c.png = X11 window grabs (python tools/xgrab_window.py "Pellaeon Classic" ...; "UCSF Chimera" ...)
"""
import os
from PIL import Image, ImageFilter, ImageDraw
H = os.path.expanduser("~/pellaeon_shots/")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "img", "classic.png")
panel = Image.open(H + "panel_c.png").convert("RGB"); launcher = Image.open(H + "launcher_c.png").convert("RGB"); chim = Image.open(H + "chimera_c.png").convert("RGB")
px = panel.load(); bg = px[6, 600]; h = panel.height
def content(y): return any(abs(px[x, y][0]-bg[0]) + abs(px[x, y][1]-bg[1]) + abs(px[x, y][2]-bg[2]) > 60 for x in range(8, panel.width - 8, 3))
cb = h - 120
while cb > 100 and not content(cb): cb -= 1
top = panel.crop((0, 0, panel.width, cb + 18)); bot = panel.crop((0, h - 112, panel.width, h))
p2 = Image.new("RGB", (panel.width, top.height + bot.height)); p2.paste(top, (0, 0)); p2.paste(bot, (0, top.height))
ls = launcher.resize((400, int(launcher.height * 400 / launcher.width)), Image.LANCZOS)
GAP = 24
W = GAP + chim.width + GAP + panel.width + GAP; H_ = GAP + max(chim.height, p2.height + 16 + ls.height) + GAP
canvas = Image.new("RGB", (W, H_)); d = ImageDraw.Draw(canvas)
for y in range(H_):
    v = 232 - int(14 * y / H_); d.line([(0, y), (W, y)], fill=(v, v + 2, v + 5))
def drop(im, x, y):
    sh = Image.new("RGBA", (im.width + 40, im.height + 40), (0, 0, 0, 0)); ImageDraw.Draw(sh).rectangle((20, 24, im.width + 20, im.height + 24), fill=(0, 0, 0, 90))
    sh = sh.filter(ImageFilter.GaussianBlur(10)); canvas.paste(sh, (x - 20, y - 20), sh)
    fr = Image.new("RGB", (im.width + 2, im.height + 2), (150, 154, 160)); fr.paste(im, (1, 1)); canvas.paste(fr, (x - 1, y - 1))
drop(chim, GAP, GAP + (H_ - 2 * GAP - chim.height) // 2)
x2 = GAP + chim.width + GAP
drop(p2, x2, GAP); drop(ls, x2, GAP + p2.height + 16)
canvas.save(OUT, optimize=True); print("saved", OUT, canvas.size)
