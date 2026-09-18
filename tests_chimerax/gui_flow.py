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
log = session.logger.info  # noqa: F821
phase = {"n": 0, "t0": time.time(), "deadline": time.time() + 240}


def grab(tag):
    try:
        inst.html_view.grab().save(os.path.join(out, "flow_%s.png" % tag))
        log("PELLAEON-TEST screenshot %s" % tag)
    except Exception as e:
        log("PELLAEON-TEST screenshot failed: %s" % e)


def finish(ok):
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
        if not inst.settings.configured:
            inst.settings.preset = "ollama"; inst.settings.provider = "ollama"; inst.settings.model = "qwen3:8b"
            inst.settings.configured = True; inst.settings.save()
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
