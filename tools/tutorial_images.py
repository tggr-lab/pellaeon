"""Turn the photo-shoot output into tutorial images.  python tools/tutorial_images.py /tmp/pellaeon_tut2 docs/img/tut
render_<tag>.png -> tut/<tag>.png (center-cropped to 4:3, 900 px wide); panel_<tag>.png -> tut/<tag>_panel.png (newest turn only).
"""
import os, sys
from PIL import Image
SRC, DST = sys.argv[1], sys.argv[2]
os.makedirs(DST, exist_ok=True)

def newest_turn(im):
    """Crop from just above the newest user bubble down to the end of the content above the composer."""
    w, h = im.size; px = im.load(); bg = px[6, h // 2]
    def is_bubble(y):   # the user bubble is bluish and right-aligned
        r, g, b = px[w - 30, y][:3]
        return b > r + 18 and b > g + 8 and b > 60
    def has_content(y):
        return any(abs(px[x, y][0] - bg[0]) + abs(px[x, y][1] - bg[1]) + abs(px[x, y][2] - bg[2]) > 60 for x in range(10, w - 10, 3))
    rows = [has_content(y) for y in range(h)]
    # composer block: the last blank run of >= 40 rows separates transcript from composer
    y = h - 1; end = h - 118
    while y > 100:
        if not rows[y]:
            top = y
            while top > 100 and not rows[top - 1]: top -= 1
            if y - top >= 40: end = top; break
            y = top - 1
        else:
            y -= 1
    # newest bubble above `end`
    yb = None
    for y in range(end - 1, 100, -1):
        if is_bubble(y):
            yb = y
            while yb > 100 and is_bubble(yb - 1): yb -= 1
            break
    start = max(0, (yb - 14) if yb is not None else 96)
    return im.crop((0, start, w, min(h, end + 12)))

for name in sorted(os.listdir(SRC)):
    if not name.endswith(".png"):
        continue
    im = Image.open(os.path.join(SRC, name)).convert("RGB")
    if name.startswith("render_"):
        tag = name[len("render_"):-4]
        w, h = im.size
        tw = int(h * 4 / 3)
        if w > tw:
            im = im.crop(((w - tw) // 2, 0, (w - tw) // 2 + tw, h))
        im.thumbnail((900, 900)); im.save(os.path.join(DST, tag + ".png"), optimize=True)
    elif name.startswith("panel_"):
        tag = name[len("panel_"):-4]
        h = im.height
        if tag == "03_nearby":
            crop = im.crop((0, max(0, h - 190), im.width, h - 60))      # the selection bar above the chips
        elif tag == "00_settings":
            crop = im.crop((0, 0, im.width, min(h, 760)))
        else:
            crop = newest_turn(im)
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
        crop.save(os.path.join(DST, tag + "_panel.png"), optimize=True)
    print("ok", name)
