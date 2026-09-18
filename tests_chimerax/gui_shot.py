"""GUI check: open the panel, submit a request through the real UI path, screenshot.

    chimerax --script tests_chimerax/gui_shot.py
"""
import os
import sys
import time

from Qt.QtCore import QTimer
from chimerax.core.commands import run

args = [a for a in sys.argv[1:] if not a.endswith(".py")]
request = args[0] if args else "open 4hhb and color it by chain"
wait_s = float(args[1]) if len(args) > 1 else 60
out = "/tmp/pellaeon_gui"
os.makedirs(out, exist_ok=True)

run(session, "ui tool show Pellaeon")  # noqa: F821
from chimerax.pellaeon.tool import _INSTANCES  # noqa: E402
inst = _INSTANCES.get(id(session))  # noqa: F821
print("panel instance:", inst)


def grab(tag):
    try:
        session.ui.main_window.grab().save(os.path.join(out, "main_%s.png" % tag))  # noqa: F821
        inst.html_view.grab().save(os.path.join(out, "panel_%s.png" % tag))
        from Qt.QtGui import QGuiApplication
        scr = QGuiApplication.primaryScreen()
        w = session.ui.main_window  # noqa: F821
        scr.grabWindow(w.winId()).save(os.path.join(out, "screen_%s.png" % tag))
        print("saved screenshots", tag)
    except Exception as e:
        print("screenshot failed:", e)


def step_submit():
    if not inst.settings.configured:
        inst.settings.preset = "ollama"
        inst.settings.provider = "ollama"
        inst.settings.model = "qwen3:8b"
        inst.settings.configured = True
        inst.settings.save()
        inst.push({"type": "config", "config": inst._config_summary()})
    grab("before")
    print("submitting:", request)
    inst.submit(request)


def step_done():
    grab("after")
    print("busy:", inst._busy, "conversation:", len(inst.agent.conversation) if inst.agent else None)
    if inst.agent:
        for m in inst.agent.conversation:
            print("  ", m.role, (m.text()[:120] or [c.name for c in m.tool_calls()]))
    run(session, "exit")  # noqa: F821


QTimer.singleShot(5000, step_submit)
QTimer.singleShot(int(5000 + wait_s * 1000), step_done)
