"""Capture screenshots for the tutorial:  chimerax --script tests_chimerax/tutorial_shots.py"""
import os, time
from Qt.QtCore import QTimer
from chimerax.core.commands import run

OUT = "/tmp/pellaeon_tut"; os.makedirs(OUT, exist_ok=True)
from chimerax.pellaeon.settings import PellaeonSettings
_s = PellaeonSettings(session, "Pellaeon"); _s.configured = False; _s.save()   # start as a first run
run(session, "ui tool hide Log; ui tool hide Models")
run(session, "ui tool show Pellaeon")
from chimerax.pellaeon.tool import _INSTANCES
inst = _INSTANCES[id(session)]
log = session.logger.info

def grab(tag, main=False):
    # the web view's own grab() can return a stale frame; crop the panel out of the main window instead
    from Qt.QtCore import QPoint
    mw = session.ui.main_window
    pix = mw.grab()
    v = inst.html_view
    tl = v.mapTo(mw, QPoint(0, 0))
    ratio = pix.devicePixelRatio() or 1
    pix.copy(int(tl.x() * ratio), int(tl.y() * ratio), int(v.width() * ratio), int(v.height() * ratio)).save(os.path.join(OUT, tag + ".png"))
    if main:
        pix.save(os.path.join(OUT, tag + "_main.png"))
    log("TUT shot " + tag)

def later(sec, fn):
    QTimer.singleShot(int(sec * 1000), fn)

def submit_and_wait(text, then, timeout=180):
    inst.submit(text)
    t0 = time.time()
    seen_busy = [False]
    def poll():
        if inst._busy:
            seen_busy[0] = True
        if (seen_busy[0] and not inst._busy) or time.time() - t0 > timeout:
            later(3.0, then)
        else:
            later(0.4, poll)
    later(0.3, poll)

def wait_confirm(then, timeout=60):
    t0 = time.time()
    def poll():
        if inst._confirms:
            later(3.0, then)
        elif time.time() - t0 > timeout or not inst._busy:
            log("TUT no confirm card"); then()
        else:
            later(0.4, poll)
    poll()

def s0():
    if not inst._page_ready:
        return later(0.5, s0)
    later(3.0, s1)
def s1():
    grab("01_settings")
    inst.settings.preset = "ollama"; inst.settings.provider = "ollama"; inst.settings.model = "qwen3:8b"
    inst.settings.configured = True; inst.settings.save(); inst.agent = None
    inst.push({"type": "config", "config": inst._config_summary()}); inst.push(inst._settings_message())
    later(2.0, lambda: (grab("02_empty_chat", main=True), s2()))
def s2():
    submit_and_wait("open 4hhb, color it by chain and show the heme as spheres", s3)
def s3():
    grab("03_first_request", main=True); run(session, "select #1/A:87"); later(1.5, s4)
def s4():
    grab("04_click_to_ask"); run(session, "select clear"); later(0.5, lambda: (inst.submit("close everything"), wait_confirm(s5)))
def s5():
    inst.html_view.page().runJavaScript("document.querySelectorAll('.card').length", lambda v: log("TUT cards in DOM: %s" % v))
    inst.html_view.page().runJavaScript("(function(){var t=document.getElementById('transcript'); t.scrollTop=t.scrollHeight;})()")
    inst.html_view.update(); later(1.5, lambda: grab("05_confirm"))
    later(2.0, lambda: inst._confirms and inst._act_confirm({"confirm_id": list(inst._confirms)[0], "decision": "run"}, None))
    t0 = time.time()
    def poll():
        if not inst._busy or time.time() - t0 > 60: later(1.0, s6)
        else: later(0.4, poll)
    poll()
def s6():
    inst._act_new_chat({}, None); run(session, "close; open 4ake; open 1ake")
    later(1.5, lambda: submit_and_wait("compare these two models and tell me what changed", s7))
def s7():
    grab("06_compare", main=True); inst._act_new_chat({}, None); run(session, "close; open alphafold:P68871")
    t0 = time.time()
    def poll():
        if session.models.list() and time.time() - t0 > 3 or time.time() - t0 > 60:
            submit_and_wait("show the disease variants on this model", s8)
        else:
            later(0.5, poll)
    poll()
def s8():
    grab("07_annotate", main=True); log("TUT done"); later(0.5, lambda: run(session, "exit"))

later(3.0, s0)
