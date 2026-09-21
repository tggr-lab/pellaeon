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
    def is_bubble(y):   # the user bubble (--user #263248) is right-aligned; enough of its right part must show the bubble color
        xs = range(w - 64, w - 12, 2)
        hit = 0
        for x in xs:
            r, g, b = px[x, y][:3]
            if abs(r - 38) <= 14 and abs(g - 50) <= 14 and abs(b - 72) <= 16:
                hit += 1
        return hit >= 0.35 * len(xs)
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
    y = end - 1
    while y > 100:
        if is_bubble(y):
            top = y
            while top > 100 and is_bubble(top - 1): top -= 1
            if y - top >= 5:                     # a padding band of the bubble; text rows may break the band
                changed = True
                while changed:                   # merge bands separated by text lines (gaps up to 26 px)
                    changed = False
                    for gap in range(1, 27):
                        yy = top - gap
                        if yy > 100 and is_bubble(yy):
                            t2 = yy
                            while t2 > 100 and is_bubble(t2 - 1): t2 -= 1
                            top = t2; changed = True
                            break
                yb = top
                break
            y = top - 1
        else:
            y -= 1
    start = max(0, (yb - 6) if yb is not None else 96)
    return im.crop((0, start, w, min(h, end + 12)))

def crop43(im):
    w, h = im.size
    tw = int(h * 4 / 3)
    return im.crop(((w - tw) // 2, 0, (w - tw) // 2 + tw, h)) if w > tw else im


def two_up(left, right, captions, out, width=900):
    """One image showing two named views side by side, each captioned under its own panel.

    Step 6c is about bookmarks: a single still cannot show that a view was left and returned to,
    so the whole-model view and the restored pocket view are shown together, captioned, with a
    white gutter between them.
    """
    from PIL import ImageDraw, ImageFont
    gut, bar = 14, 34
    ims = [crop43(Image.open(p).convert("RGB")) for p in (left, right)]
    pw = (width - gut) // 2
    ph = int(pw * ims[0].height / ims[0].width)
    ims = [im.resize((pw, ph), Image.LANCZOS) for im in ims]
    canvas = Image.new("RGB", (width, ph + bar), "white")
    d = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    for k, (im, cap) in enumerate(zip(ims, captions)):
        x = k * (pw + gut)
        canvas.paste(im, (x, 0))
        d.rectangle([x, 0, x + pw - 1, ph - 1], outline="#d7dbde")
        tw = d.textbbox((0, 0), cap, font=font)[2]
        d.text((x + (pw - tw) // 2, ph + 8), cap, fill="#333b42", font=font)
    canvas.save(out, optimize=True)
    print("ok two-up", os.path.basename(out))


SKIP = {"render_06c_zoom.png", "render_06c_wide.png"}    # only used to build the 6c pair below

for name in sorted(os.listdir(SRC)):
    if not name.endswith(".png") or name in SKIP:
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

wide, pocket = os.path.join(SRC, "render_06c_wide.png"), os.path.join(SRC, "render_06c.png")
if os.path.exists(wide) and os.path.exists(pocket):
    two_up(wide, pocket, ("the whole receptor", "back at the “pocket” bookmark"),
           os.path.join(DST, "06c.png"))
