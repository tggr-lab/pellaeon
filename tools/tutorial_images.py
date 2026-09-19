"""Turn the photo-shoot output into tutorial images.  python tools/tutorial_images.py /tmp/pellaeon_tut2 docs/img/tut
For each step: render_<tag>.png -> tut/<tag>.png (900 px wide) and panel_<tag>.png -> tut/<tag>_panel.png (last part of the panel).
"""
import os, sys
from PIL import Image
SRC, DST = sys.argv[1], sys.argv[2]
os.makedirs(DST, exist_ok=True)
for name in sorted(os.listdir(SRC)):
    im = Image.open(os.path.join(SRC, name)).convert("RGB")
    if name.startswith("render_"):
        tag = name[len("render_"):-4]
        im.thumbnail((900, 900)); im.save(os.path.join(DST, tag + ".png"), optimize=True)
    elif name.startswith("panel_"):
        tag = name[len("panel_"):-4]
        h = im.height
        # the newest turn sits just above the chips/composer (bottom ~120 px); keep the 300 px above it
        if tag == "03_nearby":
            crop = im.crop((0, max(0, h - 190), im.width, h - 60))      # the selection bar above the chips
        else:
            # find the bottom of the newest content above the composer (rows that differ from the background)
            bg = im.getpixel((6, h - 300))
            px = im.load()
            def has_content(y):
                return any(abs(px[x, y][0] - bg[0]) + abs(px[x, y][1] - bg[1]) + abs(px[x, y][2] - bg[2]) > 60 for x in range(10, im.width - 10, 3))
            y_end = h - 118
            while y_end > 120 and not has_content(y_end):
                y_end -= 1
            crop = im.crop((0, max(0, y_end - 330), im.width, min(h - 100, y_end + 10)))
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
        crop.save(os.path.join(DST, tag + "_panel.png"), optimize=True)
    print("ok", name)
