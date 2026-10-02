"""What the user did in ChimeraX by hand between two requests.

One watcher per session listens to ChimeraX triggers (commands typed on the command line or run from
menus, selection changes from mouse picks) and compares the session against a snapshot taken when
Pellaeon's previous turn ended: models opened, closed, shown, hidden or moved, the selection, the
camera. At the start of the next turn the agent gets that difference as a few lines of context, so
"this", "that one" and "the one I just opened" resolve to what the user actually did.

Everything that happens while a turn is running is Pellaeon's own work and is not reported.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

import re

MAX_TYPED = 8          # typed commands kept between two requests
# commands whose printed output is not an answer (the diff already says what they changed)
_QUIET = {"open", "close", "select", "show", "hide", "color", "colour", "cartoon", "style", "view", "turn", "roll", "move",
          "zoom", "label", "set", "lighting", "graphics", "transparency", "surface", "rainbow", "size", "clip", "camera",
          "~select", "~show", "~cartoon", "~surface", "~label", "2dlabels", "key", "windowsize", "stop", "wait"}
_LINK = re.compile(r"\[([^\]]*)\]\((?:cxcmd|help|https?):[^)]*\)")
_SKIP_TYPED = ("pellaeon", "ui ", "toolshed", "log ", "help", "usage", "version")


def _output_log_class():
    from chimerax.core.logger import PlainTextLog

    class _TypedOutput(PlainTextLog):
        """Collects what one hand-typed command printed; never consumes the message."""
        excludes_other_logs = False

        def __init__(self, entry):
            super().__init__()
            self.entry = entry

        def log(self, level, msg):
            text = " ".join(str(msg or "").split())
            if not text or text.startswith("Executing:") or text == self.entry["command"]:
                return False
            text = _LINK.sub(r"\1", text).replace("**", "")
            # the GUI command line reports a UserError as a (red) info message after the "command failed" trigger
            if level >= self.LEVEL_ERROR or not self.entry.get("ok", True):
                self.entry["ok"] = False
                self.entry.setdefault("error", text[:200])
            elif level >= self.LEVEL_INFO and not self.entry.get("quiet") and len(self.entry.setdefault("output", [])) < 2:
                self.entry["output"].append(_LINK.sub(r"\1", text)[:160])
            return False
    return _TypedOutput


def watcher(session) -> "SessionWatcher":
    w = getattr(session, "_pellaeon_watcher", None)
    if w is None:
        w = SessionWatcher(session)
        session._pellaeon_watcher = w
    return w


class SessionWatcher:
    def __init__(self, session):
        self.session = session
        self.in_turn = False     # between begin_turn and end_turn
        self.depth = 0           # inside an executor call (bridge._run_on_main_thread)
        self.typed: List[Dict[str, Any]] = []
        self.selection_events = 0
        self.baseline: Optional[Dict[str, Any]] = None
        self._handlers = []
        t = session.triggers
        self._cap = None
        for name, cb in (("command started", self._cmd_started), ("command failed", self._cmd_failed),
                         ("command finished", self._cmd_finished), ("selection changed", self._sel_changed)):
            try:
                self._handlers.append(t.add_handler(name, cb))
            except Exception:  # noqa: BLE001  (an older ChimeraX without the trigger: fewer details, no failure)
                pass
        self.reset()

    @property
    def busy(self) -> bool:
        return self.in_turn or self.depth > 0

    # ------------------------------------------------------------ triggers
    def _detach(self, *_):
        if self._cap is not None:
            try:
                self.session.logger.remove_log(self._cap)
            except Exception:  # noqa: BLE001
                pass
            self._cap = None

    def _cmd_started(self, _name, text):
        self._detach()
        if self.busy:
            return
        text = str(text or "").strip()
        if not text or text.lower().startswith(_SKIP_TYPED):
            return
        entry = {"ts": time.time(), "command": text[:200], "ok": True,
                 "quiet": text.split()[0].lower() in _QUIET}
        self.typed.append(entry)
        del self.typed[:-MAX_TYPED * 3]
        try:   # what it prints (a distance, a count, an error) is part of what the user saw
            self._cap = _output_log_class()(entry)
            self.session.logger.add_log(self._cap)
        except Exception:  # noqa: BLE001
            self._cap = None

    def _cmd_finished(self, *_):
        self._detach()

    def _cmd_failed(self, _name, text):
        if self.busy:
            return
        text = str(text or "").strip()[:200]
        for t in reversed(self.typed):
            if t["command"] == text:
                t["ok"] = False
                break
        # the command line logs the error only after this trigger: keep listening until the next frame
        if self._cap is not None and getattr(self.session.ui, "is_gui", False):
            def once(*_):
                self._detach()
                from chimerax.core.triggerset import DEREGISTER
                return DEREGISTER
            self.session.triggers.add_handler("new frame", once)

    def _sel_changed(self, *_):
        if not self.busy:
            self.selection_events += 1

    # ------------------------------------------------------------ turn boundaries
    def reset(self) -> None:
        """Forget everything before now (a new conversation, or a new executor)."""
        self.typed = []
        self.selection_events = 0
        self.baseline = self._snapshot()

    def _typed_but_unparsed(self) -> None:
        """A command that failed to parse (a typo in the command word, a bad option) never fires "command started".
        The GUI command line still remembers the last text typed into it: record it as failed if it is new."""
        try:
            from chimerax.cmd_line.tool import CommandLine
            cl = self.session.tools.find_by_class(CommandLine)
            text = str(getattr(cl[0], "_just_typed_command", "") or "").strip() if cl else ""
        except Exception:  # noqa: BLE001
            return
        if not text or text == getattr(self, "_last_seen_typed", None) or text.lower().startswith(_SKIP_TYPED):
            return
        self._last_seen_typed = text
        if not any(t["command"] == text[:200] for t in self.typed):
            self.typed.append({"ts": time.time(), "command": text[:200], "ok": False, "quiet": False,
                               "error": "ChimeraX could not parse it"})

    def begin_turn(self) -> Dict[str, Any]:
        """The changes since the previous turn ended; from here on everything is Pellaeon's own work."""
        self._detach()
        self._typed_but_unparsed()
        try:
            changes = self.changes()
        except Exception as e:  # noqa: BLE001
            changes = {"error": str(e)}
        self.in_turn = True
        return changes

    def end_turn(self) -> None:
        self.in_turn = False
        try:   # what the command line holds now was typed before this turn and is already accounted for
            from chimerax.cmd_line.tool import CommandLine
            cl = self.session.tools.find_by_class(CommandLine)
            if cl:
                self._last_seen_typed = str(getattr(cl[0], "_just_typed_command", "") or "").strip()
        except Exception:  # noqa: BLE001
            pass
        self.typed = []
        self.selection_events = 0
        try:
            self.baseline = self._snapshot()
        except Exception:  # noqa: BLE001
            self.baseline = None

    def absorb(self) -> None:
        """Pellaeon just changed the session outside a request (a panel button, a re-run): not the user's doing."""
        if not self.in_turn and self.baseline is not None:
            try:   # models only: a selection or rotation the user made before pressing the button stays reported
                self.baseline["models"] = self._snapshot()["models"]
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ snapshot and diff
    def _snapshot(self) -> Dict[str, Any]:
        s = self.session
        models = {}
        for m in s.models.list():
            if getattr(m, "id", None) is None or len(m.id) != 1:
                continue    # top-level models only: submodels (surfaces, pseudobond groups) are details
            if m.__class__.__name__ == "PseudobondGroup":
                continue    # the "distances" group a `distance` command makes: the typed command already says it
            try:
                pos = tuple(float(x) for x in m.position.matrix.flat)
            except Exception:  # noqa: BLE001
                pos = ()
            models[m.id_string] = {"name": m.name, "display": bool(getattr(m, "display", True)), "pos": pos}
        cam = s.main_view.camera
        try:
            cp = cam.position
            camera = {"axes": [tuple(float(v) for v in a) for a in cp.axes()], "origin": tuple(float(v) for v in cp.origin()),
                      "width": float(getattr(cam, "field_width", 0.0) or 0.0)}
        except Exception:  # noqa: BLE001
            camera = {}
        return {"ts": time.time(), "models": models, "selection": describe_selection(s), "camera": camera}

    def changes(self) -> Dict[str, Any]:
        base = self.baseline
        now = self._snapshot()
        out: Dict[str, Any] = {}
        typed = [dict(t, output=list(t.get("output") or [])) for t in self.typed]
        if typed:
            out["typed"] = typed[-MAX_TYPED:]
        if base is None:
            return out
        bm, nm = base["models"], now["models"]
        opened = [("#" + i, nm[i]["name"]) for i in nm if i not in bm or bm[i]["name"] != nm[i]["name"]]
        # a model closed and another opened in its place keeps the id: that is a close AND an open
        closed = [("#" + i, bm[i]["name"]) for i in bm if i not in nm or bm[i]["name"] != nm[i]["name"]]
        shown = ["#" + i for i in nm if i in bm and bm[i]["name"] == nm[i]["name"] and nm[i]["display"] and not bm[i]["display"]]
        hidden = ["#" + i for i in nm if i in bm and bm[i]["name"] == nm[i]["name"] and not nm[i]["display"] and bm[i]["display"]]
        moved = ["#" + i for i in nm if i in bm and bm[i]["name"] == nm[i]["name"] and bm[i]["pos"] and nm[i]["pos"]
                 and max(abs(a - b) for a, b in zip(bm[i]["pos"], nm[i]["pos"])) > 0.5]
        for key, val in (("opened", opened), ("closed", closed), ("shown", shown), ("hidden", hidden), ("moved_models", moved)):
            if val:
                out[key] = val
        bs, ns = base.get("selection") or {}, now.get("selection") or {}
        if bs.get("text") != ns.get("text"):
            out["selection"] = {"before": bs.get("text") or "none", "now": ns.get("text") or "none",
                                "spec": ns.get("spec", "")}
        cam = _camera_change(base.get("camera") or {}, now.get("camera") or {})
        if cam:
            # only that the view moved: which residue the user "looks at" after a free rotation is a guess
            out["view"] = cam
        return out


def _camera_change(a: Dict[str, Any], b: Dict[str, Any]) -> str:
    """Rotating in ChimeraX turns the camera about the centre of rotation, so the camera also moves;
    only a move without rotation reads as zoom or pan."""
    if not a or not b:
        return ""
    try:
        cosines = [sum(x * y for x, y in zip(u, v)) for u, v in zip(a["axes"], b["axes"])]
        angle = max(math.degrees(math.acos(max(-1.0, min(1.0, c)))) for c in cosines)
        if angle > 5:
            return "rotated"
        zoom = a.get("width") and b.get("width") and abs(a["width"] - b["width"]) / a["width"] > 0.1
        if zoom or math.dist(a["origin"], b["origin"]) > 2.0:
            return "zoomed or panned"
    except Exception:  # noqa: BLE001
        pass
    return ""


# ---------------------------------------------------------------- descriptions
def _res_label(r) -> str:
    try:
        return "#%s/%s:%d%s %s" % (r.structure.id_string, r.chain_id, r.number, r.insertion_code or "", r.name)
    except Exception:  # noqa: BLE001
        return str(r)


def describe_selection(session, max_residues: int = 6) -> Dict[str, Any]:
    """The selection in words: single atoms and a few residues by name, larger selections as counts and a spec."""
    try:
        from chimerax.atomic import selected_atoms
    except Exception:  # noqa: BLE001
        return {}
    atoms = selected_atoms(session)
    if not len(atoms):
        return {"text": ""}
    res = atoms.unique_residues
    if len(atoms) <= 3 and len(res) == 1:
        r = res[0]
        text = "%d atom%s of %s (%s)" % (len(atoms), "" if len(atoms) == 1 else "s", _res_label(r), ", ".join(atoms.names))
        spec = "#%s/%s:%d%s@%s" % (r.structure.id_string, r.chain_id, r.number, r.insertion_code or "", ",".join(atoms.names))
        return {"text": text, "spec": spec}
    if len(res) <= max_residues:
        labels = [_res_label(r) for r in res]
        try:
            from .bridge import _residues_spec
            spec = _residues_spec(res)
        except Exception:  # noqa: BLE001
            spec = " ".join(l.split()[0] for l in labels)
        return {"text": "%d residue%s: %s" % (len(res), "" if len(res) == 1 else "s", ", ".join(labels)), "spec": spec}
    try:
        from .bridge import _residues_spec
        spec = _residues_spec(res)
    except Exception:  # noqa: BLE001
        spec = ""
    models = sorted({"#" + a.structure.id_string for a in atoms[:2000]})
    return {"text": "%d residues (%d atoms) in %s" % (len(res), len(atoms), ", ".join(models)), "spec": spec}
