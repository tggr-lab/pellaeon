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
open(os.path.join(OUT, "run.py"), "w").write('''#!/usr/bin/env python3
"""Start Pellaeon Classic (needs Python 3.9+ and UCSF Chimera 1.x). Double-click or: python run.py"""
import os, runpy, sys
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here); sys.path.insert(0, os.path.join(here, "src"))
runpy.run_module("pellaeon_classic", run_name="__main__")
''')
open(os.path.join(OUT, "README.txt"), "w").write('''Pellaeon Classic - talk to UCSF Chimera 1.x in plain English

1. Install Python 3.9 or newer (python.org; on Windows tick "Add Python to PATH").
2. Install Ollama (ollama.com) for a free local model, or have an API key ready.
3. Start Chimera, then Tools > Utilities > RESTServer (note the port in the Reply Log),
   or let Pellaeon start Chimera for you from its settings page.
4. Run:  python run.py     (Windows: double-click run.py)
   Your browser opens http://127.0.0.1:8765 with the Pellaeon panel.
''')
zpath = os.path.join(ROOT, "dist", "pellaeon-classic.zip")
with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
    for dp, dn, fn in os.walk(OUT):
        for f in fn:
            full = os.path.join(dp, f)
            z.write(full, os.path.relpath(full, os.path.join(ROOT, "dist")))
print("built", OUT, "and", zpath, "%.1f MB" % (os.path.getsize(zpath) / 1e6))
