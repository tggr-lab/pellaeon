"""Photo shoot for the table-overlay tutorial step: chimerax --script tests_chimerax/table_shot.py"""
import os, time
from Qt.QtCore import QTimer, QPoint
from chimerax.core.commands import run
OUT = "/tmp/pellaeon_table"; os.makedirs(OUT, exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(ROOT, "docs", "examples", "ubiquitin_hydropathy.csv")
run(session, "ui tool hide Log; ui tool hide Models")
mw = session.ui.main_window
def later(s, f): QTimer.singleShot(int(s * 1000), f)
def grab(tag):
    gw = mw.graphics_window.widget; tl = gw.mapTo(mw, QPoint(0, 0))
    mw.grab().save(os.path.join(OUT, tag + ".png"))
    open(os.path.join(OUT, "geom.txt"), "a").write("%s %d %d %d %d %d %d\n" % (tag, mw.width(), mw.height(), tl.x(), tl.y(), gw.width(), gw.height()))
    run(session, 'save "%s" width 1154 height 700 supersample 2' % os.path.join(OUT, tag + "_3d.png"), log=False)
def s0():
    run(session, "ui tool show Pellaeon"); later(3, s1)
def s1():
    from chimerax.pellaeon.tool import _INSTANCES
    global inst; inst = _INSTANCES[id(session)]
    inst._act_new_chat({}, None)
    run(session, "open 1ubq; lighting soft; graphics silhouettes true; view; zoom 1.9", log=False)
    later(1.5, s2)
def s2():
    inst._act_table_import({"path": CSV}, None); later(1.5, lambda: (grab("11_table_preview"), s3()))
def s3():
    inst._act_table_apply({"dataset": "ubiquitin_hydropathy", "column": "hydropathy", "model": "#1", "chain": "", "palette": "blue-white-red", "accession": "", "label": "0"}, None)
    later(4, lambda: (run(session, "key blue:-4.5 white:0 red:4.5 pos 0.72,0.06 size 0.24,0.035 ; 2dlabels text 'Kyte-Doolittle hydropathy' xpos 0.72 ypos 0.105 size 20", log=False), later(1.0, lambda: (grab("11_table_applied"), s4()))))
def s4():
    inst.submit("label the residues with hydropathy above 3 and show them as sticks")
    t0 = [time.time()]; seen = [False]
    def poll():
        if inst._busy: seen[0] = True
        if inst._confirms: inst._act_confirm({"confirm_id": list(inst._confirms)[0], "decision": "run"}, None)
        if (seen[0] and not inst._busy) or time.time() - t0[0] > 200: later(2, lambda: (grab("11_table_request"), later(1, lambda: run(session, "exit"))))
        else: later(0.5, poll)
    later(0.5, poll)
later(4, s0)
