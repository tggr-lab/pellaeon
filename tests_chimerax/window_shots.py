import os, time
from Qt.QtCore import QTimer
from chimerax.core.commands import run
OUT = "/tmp/pellaeon_win"; os.makedirs(OUT, exist_ok=True)
run(session, "ui tool hide Log; ui tool hide Models")
mw = session.ui.main_window
mw.showNormal()
run(session, "windowsize 1000 700", log=False)
dbg = open("/tmp/win_sizes.txt", "w")
def later(s, f): QTimer.singleShot(int(s * 1000), f)
def shot(tag):
    try:
        _shot(tag)
    except Exception:
        import traceback; dbg.write(traceback.format_exc()); dbg.flush()
def _shot(tag):
    from Qt.QtCore import QPoint
    gw = session.ui.main_window.graphics_window.widget
    tl = gw.mapTo(mw, QPoint(0, 0))
    mw.grab().save(os.path.join(OUT, tag + ".png"))
    run(session, 'save "%s" width %d height %d supersample 2' % (os.path.join(OUT, tag + "_3d.png"), gw.width(), gw.height()), log=False)
    run(session, 'save "%s" width 1154 height %d supersample 2' % (os.path.join(OUT, tag + "_3d_narrow.png"), gw.height()), log=False)
    run(session, 'save "%s" width 1154 height 700 supersample 2' % os.path.join(OUT, tag + "_3d_short.png"), log=False)
    dbg.write("%s window %dx%d gfx %d,%d %dx%d\n" % (tag, mw.width(), mw.height(), tl.x(), tl.y(), gw.width(), gw.height())); dbg.flush()
def s0():
    run(session, "ui tool show Pellaeon"); later(3.0, s1)
def s1():
    from chimerax.pellaeon.tool import _INSTANCES
    global inst; inst = _INSTANCES[id(session)]
    run(session, "windowsize 1000 700", log=False)
    dbg.write("after panel window %dx%d\n" % (mw.width(), mw.height())); dbg.flush()
    shot("01_docked_empty"); later(2.0, lambda: (inst.submit("open 4hhb, color it by chain and show the heme groups as spheres"), wait_idle(s2)))
def wait_idle(then, timeout=180):
    t0 = time.time(); seen = [False]
    def poll():
        if inst._busy: seen[0] = True
        if inst._confirms: inst._act_confirm({"confirm_id": list(inst._confirms)[0], "decision": "run"}, None)
        if (seen[0] and not inst._busy) or time.time() - t0 > timeout: later(3, then)
        else: later(0.4, poll)
    later(0.3, poll)
def s2():
    run(session, "lighting soft; graphics silhouettes true; view", log=False); later(1.5, lambda: (shot("02_docked_4hhb"), later(2.0, s3)))
def s3():
    run(session, "close; open 4ake; open 1ake", log=False); later(1.5, lambda: (inst._act_new_chat({}, None), inst.submit("compare these two models and tell me what changed"), wait_idle(s4)))
def s4():
    run(session, "view", log=False); later(1.0, lambda: (shot("03_docked_compare"), later(2.0, lambda: run(session, "exit"))))
later(4, s0)
