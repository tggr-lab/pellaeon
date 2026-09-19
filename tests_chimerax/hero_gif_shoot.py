"""Photo shoot for the landing-page GIF: typing in the panel, the model working, the protein changing.
chimerax --script tests_chimerax/hero_gif_shoot.py   (needs Ollama qwen3:8b; frames land in /tmp/pellaeon_gif)
"""
import os, time, json
from Qt.QtCore import QTimer
from chimerax.core.commands import run
OUT = "/tmp/pellaeon_gif"; os.makedirs(OUT, exist_ok=True)
run(session, "ui tool hide Log; ui tool hide Models")
mw = session.ui.main_window
mw.showNormal()
run(session, "windowsize 1000 700", log=False)
dbg = open(os.path.join(OUT, "log.txt"), "w")
def log(s): dbg.write(s + "\n"); dbg.flush()
def later(s, f): QTimer.singleShot(int(s * 1000), f)
def grab(tag):
    mw.grab().save(os.path.join(OUT, tag + ".png")); log("grab " + tag)
def render(tag):
    run(session, 'save "%s" width 1154 height 700 supersample 2' % os.path.join(OUT, tag + "_3d.png"), log=False); log("render " + tag)
def js(code): inst.html_view.runJavaScript(code)
def set_input(text):
    js("(function(){var i=document.getElementById('input'); i.value=%s; i.focus(); i.dispatchEvent(new Event('input'));})();" % json.dumps(text))
REQ1 = "open 4hhb, color it white and show the heme groups as red spheres"
REQ2 = "make it spin"
def type_text(text, prefix, then, step=2):
    frames = []
    n = [0]
    def tick():
        i = min(len(text), n[0] + step)
        set_input(text[:i]); n[0] = i
        later(0.15, lambda: (grab("%s_%02d" % (prefix, i)), later(0.05, tick) if i < len(text) else later(0.6, then)))
    tick()
def wait_idle(prefix, then, timeout=240):
    t0 = time.time(); seen = [False]; k = [0]
    def poll():
        if inst._busy: seen[0] = True
        if inst._confirms: inst._act_confirm({"confirm_id": list(inst._confirms)[0], "decision": "run"}, None)
        if inst._busy:
            grab("%s_%03d" % (prefix, k[0])); k[0] += 1
        if (seen[0] and not inst._busy) or time.time() - t0 > timeout: later(1.5, then)
        else: later(0.5, poll)
    later(0.3, poll)
def s0():
    run(session, "ui tool show Pellaeon"); later(3.0, s1)
def s1():
    from chimerax.pellaeon.tool import _INSTANCES
    global inst; inst = _INSTANCES[id(session)]
    inst._act_new_chat({}, None)
    js("(function(){var st=document.createElement('style'); st.textContent='.cmd-info{display:none}'; document.head.appendChild(st);})();")
    from Qt.QtCore import QPoint
    gw = mw.graphics_window.widget; tl = gw.mapTo(mw, QPoint(0, 0))
    log("geom window %dx%d gfx %d,%d %dx%d" % (mw.width(), mw.height(), tl.x(), tl.y(), gw.width(), gw.height()))
    run(session, "windowsize 1000 700; lighting soft; graphics silhouettes true", log=False)
    later(1.5, lambda: (grab("p_empty"), render("r_empty"), type_text(REQ1, "t1", s2)))
def s2():
    set_input(""); inst.submit(REQ1); wait_idle("b1", s3)
def s3():
    js("document.querySelectorAll('details.tool').forEach(function(d){d.open=true;})")
    later(0.8, lambda: (grab("p_reply1"), s3b()))
def s3b():
    # replay what actually ran, one command at a time, rendering after each
    cmds = [j["command"] for j in inst.agent.journal if j.get("ok")]
    log("journal1 " + json.dumps(cmds))
    run(session, "close", log=False)
    for i, c in enumerate(cmds):
        try: run(session, c, log=False)
        except Exception as e: log("replay fail %s: %s" % (c, e))
        if c.strip().startswith("open"): run(session, "view; zoom 1.45", log=False)
        render("r1_%02d" % i)
    later(1.0, lambda: type_text(REQ2, "t2", s4))
def s4():
    set_input(""); inst.submit(REQ2); wait_idle("b2", s5)
def s5():
    js("document.querySelectorAll('details.tool').forEach(function(d){d.open=true;})")
    cmds = [j["command"] for j in inst.agent.journal if j.get("ok")]
    log("journal2 " + json.dumps(cmds))
    later(0.8, lambda: (grab("p_reply2"), s5b()))
def s5b():
    run(session, "stop", log=False)   # render the spin ourselves, frame by frame
    for i in range(36):
        render("spin_%02d" % i); run(session, "turn y 5", log=False)
    later(1.0, lambda: run(session, "exit"))
later(4, s0)
