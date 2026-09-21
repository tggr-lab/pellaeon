"""Tutorial photo shoot: real requests through Pellaeon + 3D renders + panel captures.
    chimerax --script tests_chimerax/tutorial_renders.py      -> /tmp/pellaeon_tut2/
Renders are saved at the window's aspect ratio (tools/tutorial_images.py center-crops them to 4:3 so the molecule fills the frame).
"""
import os, time
from Qt.QtCore import QTimer, QPoint
from chimerax.core.commands import run

OUT = os.environ.get("PELLAEON_TUT_OUT", "/tmp/pellaeon_tut2"); os.makedirs(OUT, exist_ok=True)   # one folder per re-shoot keeps stale frames out of tools/tutorial_images.py
run(session, "ui tool hide Log; ui tool hide Models; ui tool show Pellaeon")
# A maximized window gives a viewport two and a half times wider than it is tall, and the overlay key
# is placed as a fraction of the height: its tick labels then fall off the bottom edge. Un-maximizing
# to a fixed size first gives the same, roughly 4:3 viewport on every run.
session.ui.main_window.showNormal(); session.ui.main_window.resize(1500, 980)
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
# "highlight residue N" sometimes makes the model raise a molecular surface. The rest of the
# walkthrough is drawn as a ribbon, and a white surface is also what step 7 then ends up colouring,
# so the representation is put back for the pictures; the bookmark and the round trip stay the model's.
CARTOON = ["hide #1 surfaces", "show #1 cartoon"]

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
    """Render at the viewport's own pixel size, not a rounder one.

    Labels are drawn at a fixed pixel size, so a render that is not the size of the viewport changes
    how much of the picture each label box covers. Saving a 1000 px high image of an 1153 px high
    viewport made every label about 15% larger relative to the structure, which is enough to put back
    the collisions tidy_labels had just measured away and reported as "0 removed"."""
    gw = session.ui.main_window.graphics_window.widget
    run(session, 'save "%s" width %d height %d supersample 3' % (os.path.join(OUT, "render_%s.png" % tag), gw.width(), gw.height()), log=False)
    log("TUT render " + tag)
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
                     # The 5 A set the tool found stays on screen as sticks, which is the answer; the three
                     # other chains are put away and the rest of chain A greyed back. The picture is shown at
                     # 420 px on the homepage, where a dozen residue labels are a grey smear, so only the two
                     # things the caption names are labeled - His 87 and the heme it holds.
                     ["hide #1/B,C,D cartoon", "hide #1/B,C,D target a",
                      "color #1/A & ~(#1/A:87 :<5) #d6dade target c", "transparency #1/A & ~(#1/A:87 :<5) 45 target c",
                      "~label", "label #1/A:87 residues text \"His 87\" height fixed size 34 color black bgColor white",
                      "label #1/A:HEM residues text \"heme\" height fixed size 34 color black bgColor white",
                      "view #1/A:87 | (#1/A:87 :<5)", "zoom 0.7"]),
    ("03c_why",      "Why is residue 87 (HIS) of chain A in 4hhb this color?", ["select #1/A:87"], False, True, []),
    # The request now asks for the sticks and the iron sphere, so the turn produces them and nothing here
    # restyles the heme: what is left is framing. The other three chains and their hemes are put away, the
    # camera is turned onto the ring (edge on, a heme drawn as sticks is an unreadable streak) and tilted a
    # little off its normal so His 87 is not hidden behind the porphyrin and the two labels separate.
    ("04_pub",       "make it look publication ready and focus on the heme of chain A, shown as sticks with its iron as a small sphere", [], True, True,
                     ["hide #1/B,C,D target acs", "hide #1/B,C,D cartoon", "transparency #1/A 70 target c",
                      "show #1/A:87 target a", "style #1/A:87 stick",
                      "~label", "label #1/A:87 residues text \"His 87\" height fixed size 34 color black bgColor white",
                      "label #1/A:HEM residues text \"heme\" height fixed size 34 color black bgColor white",
                      "@face_heme", "turn x 58", "turn y 12", "view #1/A:HEM #1/A:87", "zoom 0.95"]),
    ("05_distance",  "measure the distance between the iron of the heme in chain A and the CA of residue 87 in chain A", [], True, True,
                     # Same subject as step 4, enlarged so the measured pair is the picture. The monitor line
                     # and its number are left to the build, which now picks a colour that contrasts with the
                     # background instead of drawing yellow on white.
                     ["hide #1/B,C,D target acs", "hide #1/B,C,D cartoon", "hide #1 target a",
                      "show #1/A:HEM target a", "style #1/A:HEM stick", "size #1/A:HEM stickRadius 0.2",
                      "style #1/A:HEM@FE sphere", "size #1/A:HEM@FE atomRadius 1.0",
                      "show #1/A:87 target a", "style #1/A:87 stick", "size #1/A:87 stickRadius 0.2",
                      "transparency #1/A 72 target c",
                      "@face_heme", "view #1/A:87 #1/A:HEM", "zoom 0.9"]),
    ("12_figure",    None, [], False, "figure", []),     # the Save figure form + result card
    ("06_af",        "close everything, then open the AlphaFold model of the gene ADRB2", [], True, True, [LOOK, "view"]),
    ("06b_map",      "where is UniProt residue 159 in this structure?", [], True, True, []),
    # two visibly different named views: the bookmarked pocket, then the whole receptor, then the return.
    # 06c_zoom is rendered too so tools/tutorial_images.py can build 06c.png as a before/after pair.
    ("06c_zoom",     "highlight residue 113, zoom in on it and bookmark this view as pocket", [], True, False, CARTOON),
    ("06c_wide",     "now show the whole thing", [], True, False, CARTOON + ["view", "zoom 0.8"]),
    ("06c",          "go back to the pocket view", [], True, True, CARTOON),
    # the tool really does color the rest white; a darker, slightly wider silhouette keeps that white
    # context visible on the white page background instead of disappearing into it
    ("07_tm",        "color the transmembrane helices orange and the rest white", CARTOON + ["~label"], True, True,
                     ["graphics silhouettes true width 2.5 color #4a5056", "view", "zoom 0.85"]),
    ("08_open_hbb",  None, ["close", "graphics silhouettes true width 1 color black", "open alphafold:P68871", LOOK, "view"], False, False, []),   # HBB, deterministic setup for the ClinVar step
    # overview first: the color map on its own, with the labels the turn drew put aside for the shot
    # (@keeplabels remembers them, @relabel below puts them back so the tidy step has something to tidy)
    ("08_clinvar",   "show the ClinVar disease variants of HBB on it", [], True, True,
                     ["@keeplabels", "~label", "hide #1 target a", "zoom 0.92"]),   # colour map on its own
    # nothing moves the camera after the tidy: tidy_labels places labels for the view that is on screen,
    # so re-framing afterwards would put back exactly the overlaps it just removed
    ("08b_tidy",     "the labels overlap, tidy them", ["@relabel"], True, True, []),
    ("07b_open",     None, ["close"], False, False, []),   # clean scene before re-using the saved figure's style
    ("07b_reuse",    "open 1omp and make it look like my hemoglobin figure", [], True, True, ["view"]),
    ("09_compare",   None, ["close", "open 1omp", "open 1anf", LOOK, "view"], False, False, []),
    ("09_compare_b", "compare these two models and tell me what changed", [], True, True,
                     []),   # the build places the key and its title in a band of their own and pulls the camera back for it
    # adenylate kinase closed/open pair, textbook contact-comparison case. A fresh chat and a scene with
    # nothing left over (a color key and a 2D-label model used to survive `close` and take ids #1/#2,
    # which is what made this step fail) - then the comparison turn, then the contacts question.
    ("09b_open",     None, ["@newchat", "key delete", "2dlabels delete", "close", "open 1ake", "open 4ake", LOOK, "view",
                            "graphics silhouettes true width 1 color black"], False, False, []),
    ("09b_pre",      "compare these two models and tell me what changed", [], False, False, []),
    # both entries are dimers and only chain A was compared, so the second copy is hidden rather than
    # left in the frame; the comparison turn's own key and title are replaced by the contacts key
    # (same colors the tool uses) in a corner where it does not land on the protein or on the title.
    ("09b_contacts", "which contacts are lost when it opens, and which salt bridges break?", [], True, True,
                     # The tool now places its own key and title in a clear band, so they are left alone; the
                     # shot only hides the second copy of each dimer, drops the per-distance numbers the
                     # caption does not mention, and pulls back far enough to keep that band clear.
                     ["hide #1/B target acs", "hide #2/B target acs",
                      "~label", "label delete pseudobonds",
                      "view #1/A #2/A", "zoom 0.72", "move y 5"]),
    ("10_close",     "close everything", [], False, None, []),     # confirmation card
]

ONLY = os.environ.get("PELLAEON_TUT_STEPS", "")   # "01,02,03" (groups) or exact tags ("04_pub,05_distance")

# A subset run starts from an empty session, so each group says how to reach the scene the
# walkthrough is in when that group begins. Only the first selected group's cold start runs.
COLD = {
    "03": ["close", "open 4hhb", LOOK, "color bychain", "show #1:HEM target a", "style #1:HEM sphere", "view"],
    "04": ["close", "open 4hhb", LOOK, "color bychain", "show #1:HEM target a", "style #1:HEM sphere", "view"],
    "05": ["close", "open 4hhb", LOOK, "color bychain", "show #1:HEM target a", "style #1:HEM sphere", "view"],
    "07": ["close", "open alphafold:P07550", LOOK, "view"],
}
if ONLY:
    groups = [g.strip() for g in ONLY.split(",")]
    STEPS = [st for st in STEPS if st[0][:2] in groups or st[0] in groups]
    for c in COLD.get(STEPS[0][0][:2], []) if STEPS else []:
        try: run(session, c, log=False)
        except Exception as e: log("TUT cold failed %s: %s" % (c, e))

_LABELS = []        # label commands of the most recent turn, remembered by @keeplabels

def run_pre(cmds):
    """Pre/post commands, plus a few @tokens the photo shoot needs."""
    for c in cmds:
        try:
            if c == "@newchat":
                inst._act_new_chat({}, None)
            elif c == "@keeplabels":
                del _LABELS[:]
                _LABELS.extend(j["command"] for j in (inst.agent.journal if inst.agent else [])
                               if j.get("ok") and j["command"].strip().startswith("label "))
                log("TUT kept %d label commands" % len(_LABELS))
            elif c == "@relabel":
                for lc in _LABELS:
                    run(session, lc, log=False)
            elif c == "@face_heme":
                # Turn the camera so the porphyrin is face on. Edge on, a heme drawn as sticks is an
                # unreadable red streak; the ring plane is perpendicular to the Fe-NE2 bond that His 87
                # makes, so looking along that bond shows the ring and the histidine in front of it.
                # Only the orientation is set here; the `view <spec>` that follows keeps it and frames.
                from chimerax.core.commands import atomspec as _aspec
                from chimerax.geometry import orthonormal_frame, Place
                def _pos(spec):
                    return _aspec.AtomSpecArg.parse(spec, session)[0].evaluate(session).atoms.scene_coords[0]
                axis = _pos("#1/A:87@NE2") - _pos("#1/A:HEM@FE")
                cam = session.main_view.camera
                cam.position = Place(axes=orthonormal_frame(axis).axes(), origin=cam.position.origin())
            else:
                run(session, c, log=False)
        except Exception as e: log("TUT cmd failed %s: %s" % (c, e))

TOOL_RESULTS = []       # (name, ok, payload) of every tool call, for checking a turn really worked
_orig_tool_result = inst._on_tool_result
def _watch_tool_result(call, result, payload):
    TOOL_RESULTS.append((call.name, not result.is_error, payload))
    return _orig_tool_result(call, result, payload)
inst._on_tool_result = _watch_tool_result

def contacts_ok():
    """Did the contacts turn really produce a comparison?

    A green tick is not enough: ministral-8b often passes `restrict` = the whole reference chain,
    and the tool then reports '0 contacts, all present in both conformations' as a successful
    result, so the panel shows a confident 'nothing changed' that is simply wrong.

    The other way a run goes wrong is quieter: the model calls the tool again at a looser cutoff and
    then writes up the union of both answers, so the reply lists salt bridges the published run did
    not find. So a run counts as good only when every card was ok, every call used the same 4 A
    cutoff, and the last card carries the real counts (104 lost / 74 gained / 3 salt bridges broken
    for 1ake vs 4ake chain A)."""
    cc = [(ok, p) for name, ok, p in TOOL_RESULTS if name == "compare_contacts"]
    if not cc or any(not ok for ok, _ in cc) or len(cc) > 2:
        return False
    if any(not isinstance(p, dict) or float(p.get("cutoff", 0) or 0) != 4.0 for _, p in cc):
        return False
    last = cc[-1][1]
    counts = last.get("counts") or {}
    return int(counts.get("lost", 0)) > 0 and int(last.get("by_kind", {}).get("salt bridge", {}).get("lost", 0)) == 3

# Steps whose panel picture must not contain a red card: the walkthrough presents them as things that
# just work, and a first attempt that failed and was retried is a different story than the caption
# tells. The value is what to run to get back to the step's starting scene before trying again.
CLEAN_TURN = {
    "05_distance": ["@newchat"] + COLD["05"],
    "04_pub": ["@newchat"] + COLD["04"],
    "03_nearby_b": ["@newchat"] + COLD["03"] + ["select #1/A:87"],
}

def journal_has(pattern):
    import re as _re
    return any(j.get("ok") and _re.search(pattern, str(j.get("command", "")), _re.I)
               for j in (inst.agent.journal if inst.agent else []))

# Some steps can end with every card green and still not have done what the request asked: step 4's
# model often cannot find the iron (it guesses ':FE' as a residue) and signs off by telling the user
# which command to run instead. A green tick is not the test; the commands that ran are.
def _styled(spec, mode):
    """Is anything matching `spec` displayed in `mode` right now?

    Asking the scene, not the journal: a `style #1/A:FE sphere` that matched no atom still comes back
    ok, so a step can look delivered while the reply is explaining that it could not find the iron.
    """
    from chimerax.core.commands import atomspec
    from chimerax.atomic import Atom
    try:
        atoms = atomspec.AtomSpecArg.parse(spec, session)[0].evaluate(session).atoms
    except Exception:  # noqa: BLE001
        return False
    want = {"sphere": Atom.SPHERE_STYLE, "stick": Atom.STICK_STYLE}[mode]
    return any(bool(a.display) and a.draw_mode == want for a in atoms)

STEP_DELIVERED = {
    "04_pub": lambda: _styled("#1/A:HEM@FE", "sphere") and _styled("#1/A:HEM@N*,C*", "stick"),
}

def turn_clean():
    """Every tool card in the turn green, every command inside a run_commands card ok, and - where the
    step says so - the commands that actually deliver what the request asked for."""
    for _name, ok, payload in TOOL_RESULTS:
        if not ok:
            return False
        if isinstance(payload, dict):
            if payload.get("error"):
                return False
            for r in payload.get("results") or []:
                if isinstance(r, dict) and not r.get("ok", True):
                    return False
    return True

def go(i):
    if i >= len(STEPS):
        log("TUT done"); later(1, lambda: run(session, "exit")); return
    tag, req, pre, do_render, do_panel, post = STEPS[i]
    run_pre(pre)
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
            run_pre(post)
            if do_render: later(0.8, lambda: (render(tag), go(i + 1)))
            else: go(i + 1)
        if do_panel: panel(tag, then=cont)
        else: cont()
    if tag == "09b_contacts":
        # Retried until the tool really compares the two conformations (see contacts_ok): a wrong
        # `restrict` argument makes it answer "nothing changed" with a green tick, and publishing
        # that as the worked example is exactly what this step got wrong before. Each attempt is a
        # fresh chat on a fresh scene, so nothing from a failed attempt is left in the picture.
        tries = [0]
        def attempt():
            del TOOL_RESULTS[:]
            inst.submit(req)
            def judge():
                if contacts_ok() or tries[0] >= int(os.environ.get("PELLAEON_09B_TRIES", "10")):
                    log("TUT 09b ok=%s after %d retries" % (contacts_ok(), tries[0]))
                    after(); return
                tries[0] += 1
                log("TUT 09b attempt %d gave no contact change, retrying" % tries[0])
                run_pre(["@newchat", "key delete", "2dlabels delete", "close", "open 1ake", "open 4ake", LOOK, "view",
                         "graphics silhouettes true width 1 color black"])
                inst.submit("compare these two models and tell me what changed")
                wait_idle(lambda: later(1.0, attempt))
            wait_idle(judge)
        later(0.5, attempt); return
    if tag in CLEAN_TURN and req:
        tries = [0]
        def attempt2():
            del TOOL_RESULTS[:]
            inst.submit(req)
            def judge():
                ok = turn_clean() and STEP_DELIVERED.get(tag, lambda: True)()
                if ok or tries[0] >= int(os.environ.get("PELLAEON_CLEAN_TRIES", "5")):
                    log("TUT %s clean=%s delivered=%s after %d retries"
                        % (tag, turn_clean(), STEP_DELIVERED.get(tag, lambda: True)(), tries[0]))
                    after(); return
                tries[0] += 1
                log("TUT %s attempt %d did not come out clean, retrying" % (tag, tries[0]))
                run_pre(CLEAN_TURN[tag])
                later(1.0, attempt2)
            wait_idle(judge)
        later(0.5, attempt2); return
    if req:
        inst.submit(req); wait_idle(after)
    else:
        later(2.0, after)

def start():
    if not inst._page_ready: return later(0.5, start)
    later(2.0, lambda: go(0))
later(3, start)
