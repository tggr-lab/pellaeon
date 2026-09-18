"""Package Pellaeon Classic as a self-contained folder + zip:  python classic/tools/build_classic.py
Result: dist/pellaeon-classic/ (run with `python run.py`) and dist/pellaeon-classic.zip
"""
import os, shutil, sys, zipfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(ROOT, "dist", "pellaeon-classic")
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "chimera_docs")
shutil.copytree(os.path.join(ROOT, "src", "core"), os.path.join(OUT, "src", "core"), ignore=ignore)
shutil.copytree(os.path.join(ROOT, "src", "ui"), os.path.join(OUT, "src", "ui"), ignore=ignore)
shutil.copy(os.path.join(ROOT, "src", "panel_base.py"), os.path.join(OUT, "src", "panel_base.py"))
shutil.copytree(os.path.join(ROOT, "classic", "pellaeon_classic"), os.path.join(OUT, "pellaeon_classic"), ignore=ignore)
shutil.copy(os.path.join(ROOT, "LICENSE"), OUT)
for name in os.listdir(os.path.join(ROOT, "classic", "dist_files")):
    shutil.copy(os.path.join(ROOT, "classic", "dist_files", name), OUT)
shutil.copy(os.path.join(OUT, "run.py"), os.path.join(OUT, "run.pyw"))   # Windows: no console window
zpath = os.path.join(ROOT, "dist", "pellaeon-classic.zip")
with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
    for dp, dn, fn in os.walk(OUT):
        for f in fn:
            full = os.path.join(dp, f)
            z.write(full, os.path.relpath(full, os.path.join(ROOT, "dist")))
print("built", OUT, "and", zpath, "%.1f MB" % (os.path.getsize(zpath) / 1e6))
