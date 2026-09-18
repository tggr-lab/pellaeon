#!/usr/bin/env python3
"""Start Pellaeon Classic (needs Python 3.9+ and UCSF Chimera 1.x). Double-click or: python run.py [--console]"""
import os
import runpy
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
sys.path.insert(0, os.path.join(here, "src"))
runpy.run_module("pellaeon_classic", run_name="__main__")
