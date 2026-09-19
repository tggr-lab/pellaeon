"""Grab one X11 window (works under XWayland where root grabs fail): python xgrab.py <name-substring> out.png"""
import ctypes, ctypes.util, subprocess, sys, re
from PIL import Image
name, out = sys.argv[1], sys.argv[2]
tree = subprocess.run(["xwininfo", "-root", "-tree"], capture_output=True, text=True).stdout
cands = [l for l in tree.splitlines() if name.lower() in l.lower() and re.search(r"\d+x\d+\+", l) and "mutter-x11-frames" not in l]
if not cands: sys.exit("no window matching %r" % name)
# biggest matching window
def size(l):
    m = re.search(r"(\d+)x(\d+)\+", l); return int(m.group(1)) * int(m.group(2))
line = max(cands, key=size); wid = int(line.split()[0], 16)
m = re.search(r"(\d+)x(\d+)\+", line); w, h = int(m.group(1)), int(m.group(2))
X = ctypes.CDLL(ctypes.util.find_library("X11"))
X.XOpenDisplay.restype = ctypes.c_void_p; X.XGetImage.restype = ctypes.c_void_p
X.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
X.XGetPixel.restype = ctypes.c_ulong; X.XGetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
d = X.XOpenDisplay(None)
img = X.XGetImage(d, wid, 0, 0, w, h, 0xFFFFFFFF, 2)   # ZPixmap
if not img: sys.exit("XGetImage failed for %s" % line.strip())
class XImage(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int), ("format", ctypes.c_int),
                ("data", ctypes.c_void_p), ("byte_order", ctypes.c_int), ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int),
                ("bitmap_pad", ctypes.c_int), ("depth", ctypes.c_int), ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int)]
xi = ctypes.cast(img, ctypes.POINTER(XImage)).contents
buf = ctypes.string_at(xi.data, xi.bytes_per_line * xi.height)
im = Image.frombuffer("RGBX" if xi.bits_per_pixel == 32 else "RGB", (w, h), buf, "raw", "BGRX" if xi.bits_per_pixel == 32 else "BGR", xi.bytes_per_line, 1).convert("RGB")
im.save(out); print("saved", out, im.size, line.strip()[:80])
