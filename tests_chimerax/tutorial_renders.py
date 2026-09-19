"""Tutorial photo shoot: real requests through Pellaeon + 3D renders + panel captures.
    chimerax --script tests_chimerax/tutorial_renders.py      -> /tmp/pellaeon_tut2/
"""
import os, time
from Qt.QtCore import QTimer, QPoint
from chimerax.core.commands import run

OUT = "/tmp/pellaeon_tut2"; os.makedirs(OUT, exist_ok=True)
run(session, "ui tool hide Log; ui tool hide Models; ui tool show Pellaeon")
run(session, "windowsize 1100 800", log=False)
from chimerax.pellaeon.tool import _INSTANCES
inst = _INSTANCES[id(session)]
log = session.logger.info
if not inst.settings.configured:
    inst.settings.preset = "ollama"; inst.settings.provider = "ollama"; inst.settings.model = "qwen3:8b"
    inst.settings.configured = True; inst.settings.save()
inst.settings.autonomy = "auto"
if inst.agent: inst.agent.config.autonomy = "auto"

def later(s, f): QTimer.singleShot(int(s * 1000), f)
def panel(tag):
    mw = session.ui.main_window; pix = mw.grab(); v = inst.html_view; tl = v.mapTo(mw, QPoint(0, 0))
    pix.copy(tl.x(), tl.y(), v.width(), v.height()).save(os.path.join(OUT, "panel_%s.png" % tag)); log("TUT panel " + tag)
def render(tag):
    run(session, 'save "%s" width 1200 height 900 supersample 3' % os.path.join(OUT, "render_%s.png" % tag), log=False); log("TUT render " + tag)
def wait_idle(then, timeout=240):
    t0 = time.time(); seen = [False]
    def poll():
        if inst._busy: seen[0] = True
        if inst._confirms and not getattr(inst, "_tut_hold", False):
            cid = list(inst._confirms)[0]; inst._act_confirm({"confirm_id": cid, "decision": "run"}, None)
        if (seen[0] and not inst._busy) or time.time() - t0 > timeout: later(2.5, then)
        else: later(0.4, poll)
    later(0.3, poll)

STEPS = [
    # tag, request (or None), pre-commands, render?, panel?
    ("01_open",      "open 4hhb", [], True, True),
    ("02_chains",    "color it by chain and show the heme groups as spheres", [], True, True),
    ("03_nearby",    None, ["select #1/A:87"], False, True),   # selection bar visible
    ("03_nearby_b",  "What residues and ligands are within 5 A of residue 87 (HIS) of chain A in #1? Show them as sticks and label them.", [], True, True),
    ("04_pub",       "make it look publication ready and focus on the heme of chain A", [], True, True),
    ("05_distance",  "measure the distance between the iron of the heme in chain A and the CA of residue 87 in chain A", [], True, True),
    ("06_af",        "close everything, then open the AlphaFold model of the gene F2RL1", [], True, True),
    ("07_tm",        "color the transmembrane helices orange and the rest white", [], True, True),
    ("08_open_hbb",  "close everything and open the AlphaFold model of the gene HBB", [], False, False),
    ("08_clinvar",   "show the ClinVar disease variants on it", [], True, True),
    ("09_compare",   None, ["close", "open 4ake", "open 1ake", "view"], False, False),
    ("09_compare_b", "compare these two models and tell me what changed", [], True, True),
    ("10_close",     "close everything", [], False, None),     # confirmation card (autonomy back to auto)
]

def go(i):
    if i >= len(STEPS):
        panel("11_final"); log("TUT done"); later(1, lambda: run(session, "exit")); return
    tag, req, pre, do_render, do_panel = STEPS[i]
    for c in pre:
        try: run(session, c, log=False)
        except Exception as e: log("TUT pre failed %s: %s" % (c, e))
    if tag == "10_close":
        inst._tut_hold = True
        inst.submit(req)
        t0 = time.time()
        def poll():
            if inst._confirms: later(2.5, lambda: (panel(tag), setattr(inst, "_tut_hold", False), inst._act_confirm({"confirm_id": list(inst._confirms)[0], "decision": "run"}, None), wait_idle(lambda: go(i + 1))))
            elif time.time() - t0 > 90 or not inst._busy: log("TUT no confirm"); go(i + 1)
            else: later(0.4, poll)
        later(0.5, poll); return
    def after():
        if do_render: render(tag)
        if do_panel: panel(tag)
        go(i + 1)
    if req:
        inst.submit(req); wait_idle(after)
    else:
        later(2.0, after)

def start():
    if not inst._page_ready: return later(0.5, start)
    later(2.0, lambda: go(0))
later(3, start)
