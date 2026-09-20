"""Scripted GUI scenario: open+color, then a risky request that must show a
confirmation card, approve it, verify the model is gone. Screenshots in /tmp/pellaeon_gui.

    chimerax --script tests_chimerax/gui_flow.py
"""
import os
import time

from Qt.QtCore import QTimer
from chimerax.core.commands import run

out = "/tmp/pellaeon_gui"
os.makedirs(out, exist_ok=True)
run(session, "ui tool show Pellaeon")  # noqa: F821
from chimerax.pellaeon.tool import _INSTANCES  # noqa: E402
inst = _INSTANCES.get(id(session))  # noqa: F821
_logfile = open(os.path.join(out, "gui_flow.log"), "w")


def log(msg):   # a GUI ChimeraX prints nothing to stdout, so tee every line to a file
    session.logger.info(msg)  # noqa: F821
    _logfile.write(msg + "\n")
    _logfile.flush()

phase = {"n": 0, "t0": time.time(), "deadline": time.time() + 240}
saved = {}


def grab(tag):
    try:
        inst.html_view.grab().save(os.path.join(out, "flow_%s.png" % tag))
        log("PELLAEON-TEST screenshot %s" % tag)
    except Exception as e:
        log("PELLAEON-TEST screenshot failed: %s" % e)


def finish(ok):
    if saved:      # put the user's own provider back
        for k, v in saved.items():
            setattr(inst.settings, k, v)
        inst.settings.save()
        log("PELLAEON-TEST restored settings: %s / %s" % (saved.get("preset"), saved.get("model")))
    log("PELLAEON-TEST RESULT: %s" % ("PASS" if ok else "FAIL"))
    QTimer.singleShot(500, lambda: run(session, "exit"))  # noqa: F821


def tick():
    if time.time() > phase["deadline"]:
        grab("timeout")
        return finish(False)
    n = phase["n"]
    if n == 0:
        if not inst._page_ready:
            return QTimer.singleShot(500, tick)
        override = os.environ.get("PELLAEON_GUI_PRESET", "")   # e.g. "gemini:gemini-flash-lite-latest"
        if override:   # ChimeraX Settings are AUTO_SAVE: assigning persists, so remember what to put back
            saved.update({k: getattr(inst.settings, k) for k in ("preset", "provider", "model", "base_url", "configured")})
        if override or not inst.settings.configured:
            pid, _, mname = override.partition(":")
            from chimerax.pellaeon.core.providers.presets import preset_by_id
            preset = preset_by_id(pid or "ollama") or {}
            inst.settings.preset = pid or "ollama"
            inst.settings.provider = preset.get("provider", "ollama")
            inst.settings.model = mname or preset.get("model", "qwen3:8b")
            inst.settings.base_url = preset.get("base_url", "")
            inst.settings.configured = True
            inst.settings.save()
            inst.push({"type": "config", "config": inst._config_summary()})
        inst.submit("open 4hhb and color it by chain")
        phase["n"] = 1
    elif n == 1:
        if inst._busy:
            return QTimer.singleShot(1000, tick)
        models = [m.id_string for m in session.models.list()]  # noqa: F821
        log("PELLAEON-TEST after open: models=%s" % models)
        if not models:
            grab("open_failed")
            return finish(False)
        inst.submit("close everything")
        phase["n"] = 2
    elif n == 2:
        if not inst._confirms:
            if not inst._busy:
                log("PELLAEON-TEST no confirmation card appeared (turn ended)")
                grab("noconfirm")
                return finish(False)
            return QTimer.singleShot(1000, tick)
        cid = list(inst._confirms)[0]
        log("PELLAEON-TEST confirm card %s commands=%s" % (cid, inst._confirms[cid]["commands"]))
        grab("confirm")
        inst._act_confirm({"confirm_id": cid, "decision": "run"}, None)
        phase["n"] = 3
    elif n == 3:
        if inst._busy:
            return QTimer.singleShot(1000, tick)
        models = [m.id_string for m in session.models.list()]  # noqa: F821
        log("PELLAEON-TEST after close: models=%s" % models)
        grab("closed")
        for m in inst.agent.conversation:
            log("PELLAEON-TEST  %s | %s" % (m.role, (m.text()[:100] or [c.name for c in m.tool_calls()])))
        return finish(len(models) == 0)
    QTimer.singleShot(500, tick)


QTimer.singleShot(3000, tick)
