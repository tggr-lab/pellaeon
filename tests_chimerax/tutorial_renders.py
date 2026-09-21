"""Tutorial photo shoot: real requests through Pellaeon + 3D renders + panel captures.
    chimerax --script tests_chimerax/tutorial_renders.py      -> /tmp/pellaeon_tut2/
Renders are saved at the window's aspect ratio (tools/tutorial_images.py center-crops them to 4:3 so the molecule fills the frame).
"""
import os, time
from Qt.QtCore import QTimer, QPoint
from chimerax.core.commands import run

OUT = "/tmp/pellaeon_tut2"; os.makedirs(OUT, exist_ok=True)
run(session, "ui tool hide Log; ui tool hide Models; ui tool show Pellaeon")
run(session, "windowsize 1100 800", log=False)
from chimerax.pellaeon.tool import _INSTANCES
inst = _INSTANCES[id(session)]
_LOGF = open(os.path.join(OUT, "run.log"), "a")
def log(msg):
    session.logger.info(msg)
    _LOGF.write(msg + "\n"); _LOGF.flush()
if not inst.settings.configured:
    inst.settings.preset = "ollama"; inst.settings.provider = "ollama"; inst.settings.model = "qwen3:8b"
    inst.settings.configured = True; inst.settings.save()
inst.settings.autonomy = "auto"
if inst.agent: inst.agent.config.autonomy = "auto"
LOOK = "set bgColor white; lighting soft; graphics silhouettes true"

def later(s, f): QTimer.singleShot(int(s * 1000), f)
NOT_BUSY_JS = "!(document.getElementById('send') && document.getElementById('send').classList.contains('stop'))"
HAS_CONFIRM_JS = "!!document.querySelector('.card[data-confirm]')"
def panel(tag, then=None, ready_js=NOT_BUSY_JS, tries=0, max_tries=25):
    """Grab the panel widget once the DOM (checked via JS, the actual source of truth for what will be
    painted) reports `ready_js` true, not just once our Python-side state says so: the web view's
    rendered texture can lag a beat behind both its DOM and our busy flag, so trusting either alone
    risks a stale or mid-turn frame."""
    inst.html_view.runJavaScript("var t=document.getElementById('transcript'); if(t) t.scrollTop = t.scrollHeight;")
    def cb(ready):
        if not ready and tries < max_tries:
            later(0.4, lambda: panel(tag, then, ready_js, tries + 1, max_tries)); return
        def grab():
            mw = session.ui.main_window; pix = mw.grab(); v = inst.html_view; tl = v.mapTo(mw, QPoint(0, 0))
            pix.copy(tl.x(), tl.y(), v.width(), v.height()).save(os.path.join(OUT, "panel_%s.png" % tag)); log("TUT panel " + tag)
            if then: then()
        later(0.3, grab)
    inst.html_view.runJavaScript("(function(){return (%s);})()" % ready_js, cb)
def render(tag):
    gw = session.ui.main_window.graphics_window.widget
    w, h = gw.width(), gw.height()
    scale = 1000.0 / h
    run(session, 'save "%s" width %d height %d supersample 3' % (os.path.join(OUT, "render_%s.png" % tag), int(w * scale), 1000), log=False); log("TUT render " + tag)
def wait_idle(then, timeout=240):
    t0 = time.time(); seen = [False]
    def poll():
        if inst._busy: seen[0] = True
        if inst._confirms and not getattr(inst, "_tut_hold", False):
            cid = list(inst._confirms)[0]; inst._act_confirm({"confirm_id": cid, "decision": "run"}, None)
        if (seen[0] and not inst._busy) or time.time() - t0 > timeout: later(6.0, then)
        else: later(0.4, poll)
    later(0.3, poll)

STEPS = [
    # tag, request (or None), pre-commands, render?, panel?, post-commands (camera only, before the render)
    ("00_settings",  None, [], False, "settings", []),
    ("01_open",      "open 4hhb", [], True, True, [LOOK, "view"]),
    ("02_chains",    "color it by chain and show the heme groups as spheres", [], True, True, ["view"]),
    ("03_nearby",    None, ["select #1/A:87"], False, True, []),   # selection bar visible
    ("03_nearby_b",  "What residues and ligands are within 5 A of residue 87 (HIS) of chain A in #1? Show them as sticks and label them.", [], True, True,
                     ["view #1/A:87 | (#1/A:87 :<5)", "zoom 0.85"]),
    ("03c_why",      "Why is residue 87 (HIS) of chain A in 4hhb this color?", ["select #1/A:87"], False, True, []),
    ("04_pub",       "make it look publication ready and focus on the heme of chain A", [], True, True, []),
    ("05_distance",  "measure the distance between the iron of the heme in chain A and the CA of residue 87 in chain A", [], True, True,
                     ["distance style radius 0.15 color gold decimalPlaces 2", "label height 0.7", "view #1/A:87 #1/A:HEM", "zoom 0.8"]),
    ("12_figure",    None, [], False, "figure", []),     # the Save figure form + result card
    ("06_af",        "close everything, then open the AlphaFold model of the gene ADRB2", [], True, True, [LOOK, "view"]),
    ("06b_map",      "where is UniProt residue 159 in this structure?", [], True, True, []),
    ("06c_zoom",     "highlight residue 113, pull back so the whole receptor is visible, and bookmark this view as pocket", [], False, False, []),
    ("06c",          "now show the whole thing, then go back to the pocket view", [], True, True, []),
    ("07_tm",        "color the transmembrane helices orange and the rest white", [], True, True, ["view"]),
    ("08_open_hbb",  None, ["close", "open alphafold:P68871", LOOK, "view"], False, False, []),   # HBB, deterministic setup for the ClinVar step
    ("08_clinvar",   "show the ClinVar disease variants of HBB on it", [], True, True, ["view"]),
    ("08b_tidy",     "the labels overlap, tidy them", [], True, True, ["view"]),
    ("07b_open",     None, ["close"], False, False, []),   # clean scene before re-using the saved figure's style
    ("07b_reuse",    "open 1omp and make it look like my hemoglobin figure", [], True, True, ["view"]),
    ("09_compare",   None, ["close", "open 1omp", "open 1anf", LOOK, "view"], False, False, []),
    ("09_compare_b", "compare these two models and tell me what changed", [], True, True,
                     ["view", "zoom 1.7", "key delete", "2dlabels delete", "key #bdbdbd:0 gold:1 orange:3 #b2182b:6+ pos 0.36,0.05 size 0.12,0.03 fontSize 20",
                      '2dlabels text "C\u03b1 shift after fit (\u00c5)" xpos 0.36 ypos 0.095 size 20 color black']),   # key re-placed inside the 4:3 crop of the wide window
    ("09b_open",     None, ["close", "open 1ake", "open 4ake", LOOK, "view"], False, False, []),   # adenylate kinase closed/open pair, textbook contact-comparison case
    ("09b_contacts", "which contacts are lost when it opens, and which salt bridges break?", [], True, True, ["view"]),
    ("10_close",     "close everything", [], False, None, []),     # confirmation card
]

ONLY = os.environ.get("PELLAEON_TUT_STEPS", "")   # e.g. "01,02,03,04,05" to re-shoot a prefix of the walkthrough
if ONLY:
    STEPS = [st for st in STEPS if st[0][:2] in ONLY.split(",")]

def go(i):
    if i >= len(STEPS):
        log("TUT done"); later(1, lambda: run(session, "exit")); return
    tag, req, pre, do_render, do_panel, post = STEPS[i]
    for c in pre:
        try: run(session, c, log=False)
        except Exception as e: log("TUT pre failed %s: %s" % (c, e))
    if do_panel == "figure":
        import shutil; shutil.rmtree("/tmp/pellaeon_figs", ignore_errors=True)
        inst._act_figure_form({"name": "heme_pocket"}, None)
        def saved():
            inst._act_figure_save({"name": "heme_pocket", "folder": "/tmp/pellaeon_figs", "width": "2400", "height": "1800", "closeup": "#1/A:87 :<6", "session": "1"}, None)
            t0 = time.time()
            def poll():
                if not inst._busy and time.time() - t0 > 3: later(1.5, lambda: panel(tag, then=lambda: go(i + 1)))
                elif time.time() - t0 > 120: go(i + 1)
                else: later(0.5, poll)
            later(1.0, poll)
        later(1.5, saved); return
    if do_panel == "settings":
        inst.push({"type": "show_page", "page": "settings"})
        def after_settings():
            inst.push({"type": "show_page", "page": "chat"}); later(1.0, lambda: go(i + 1))
        later(1.5, lambda: panel(tag, then=after_settings)); return
    if tag == "10_close":
        inst._tut_hold = True
        inst.submit(req)
        t0 = time.time()
        def poll():
            if inst._confirms:
                def approve():
                    setattr(inst, "_tut_hold", False)
                    if inst._confirms: inst._act_confirm({"confirm_id": list(inst._confirms)[0], "decision": "run"}, None)
                    wait_idle(lambda: go(i + 1))
                later(1.0, lambda: panel(tag, then=approve, ready_js=HAS_CONFIRM_JS))
            elif time.time() - t0 > 90 or not inst._busy: log("TUT no confirm"); go(i + 1)
            else: later(0.4, poll)
        later(0.5, poll); return
    def after():
        def cont():
            for c in post:
                try: run(session, c, log=False)
                except Exception as e: log("TUT post failed %s: %s" % (c, e))
            if do_render: later(0.8, lambda: (render(tag), go(i + 1)))
            else: go(i + 1)
        if do_panel: panel(tag, then=cont)
        else: cont()
    if req:
        inst.submit(req); wait_idle(after)
    else:
        later(2.0, after)

def start():
    if not inst._page_ready: return later(0.5, start)
    later(2.0, lambda: go(0))
later(3, start)
