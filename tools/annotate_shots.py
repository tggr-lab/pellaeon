"""Draw numbered callouts on tutorial screenshots.  python tools/annotate_shots.py /tmp/pellaeon_tut docs/img"""
import os, sys
from PIL import Image, ImageDraw, ImageFont

SRC, DST = sys.argv[1], sys.argv[2]
os.makedirs(DST, exist_ok=True)
ACCENT = (255, 140, 0)


def font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def draw_marks(im, marks, scale):
    d = ImageDraw.Draw(im)
    f = font(int(13 * scale))
    r = int(11 * scale)
    for n, (x, y) in enumerate(marks, 1):
        x, y = int(x * scale), int(y * scale)
        d.ellipse((x - r, y - r, x + r, y + r), fill=ACCENT, outline=(255, 255, 255), width=int(1.5 * scale))
        tw = d.textlength(str(n), font=f)
        d.text((x - tw / 2, y - f.size * 0.6), str(n), fill=(20, 20, 20), font=f)
    return im


def shot(name, marks=(), scale=2, crop=None, cut=None, out=None, max_width=None):
    """cut=(top, bottom): remove the empty band between those rows (marks below are shifted up)."""
    im = Image.open(os.path.join(SRC, name)).convert("RGB")
    if crop:
        im = im.crop(crop)
    marks = list(marks)
    if cut:
        top, bottom = cut
        a = im.crop((0, 0, im.width, top)); b = im.crop((0, bottom, im.width, im.height))
        joined = Image.new("RGB", (im.width, a.height + b.height + 8), (110, 110, 110))
        joined.paste(a, (0, 0)); joined.paste(b, (0, a.height + 8))
        im = joined
        marks = [(x, y if y < top else y - (bottom - top) + 8) for x, y in marks]
    im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS) if scale != 1 else im
    draw_marks(im, marks, scale)
    if max_width and im.width > max_width:
        im = im.resize((max_width, int(im.height * max_width / im.width)), Image.LANCZOS)
    im.save(os.path.join(DST, out or name), optimize=True)
    print("wrote", out or name, im.size)


shot("01_settings.png", [(115, 245), (170, 680), (403, 821), (70, 861), (380, 935)], crop=(0, 0, 440, 960))
shot("03_first_request.png", [(240, 95), (105, 142), (120, 176), (55, 56), (100, 30), (235, 22), (60, 1040), (397, 1088)], cut=(220, 1000))
shot("04_click_to_ask.png", [(85, 1014), (203, 1014), (296, 1014), (380, 1014)], cut=(220, 990))
shot("05_confirm.png", [(150, 354), (60, 384), (380, 436)], cut=(480, 1000))
shot("06_compare.png", crop=(0, 0, 440, 330))
shot("07_annotate.png", crop=(0, 0, 440, 210))
shot("02_empty_chat_main.png", scale=1, max_width=1800)
shot("06_compare_main.png", scale=1, max_width=1800)
shot("07_annotate_main.png", scale=1, max_width=1800)
