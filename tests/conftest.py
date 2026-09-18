import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
# Make "core" importable without ChimeraX (src/__init__.py imports chimerax, so add src itself).
if SRC not in sys.path:
    sys.path.insert(0, SRC)

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
DATA = os.path.join(SRC, "data")
