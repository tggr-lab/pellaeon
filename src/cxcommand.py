"""ChimeraX command-line entry points:

    pellaeon "open the alphafold model of F2RL1 and color it white"
    pellaeon settings            (open the panel on the settings page)
    pellaeon index rebuild       (rebuild the docs index)
"""
from __future__ import annotations

from chimerax.core.commands import CmdDesc, RestOfLine, StringArg, register


def pellaeon_cmd(session, request):
    request = (request or "").strip()
    if not request:
        from .tool import show_panel
        show_panel(session)
        return
    if request.lower() in ("settings", "setup"):
        from .tool import show_panel
        show_panel(session, page="settings")
        return
    if request.lower().startswith("index"):
        from .bridge import ChimeraXExecutor
        ex = ChimeraXExecutor(session)
        ex.rebuild_index()
        session.logger.info("Pellaeon: docs index rebuilt.")
        return
    from .tool import show_panel
    panel = show_panel(session)
    panel.submit(request)


def register(name, logger):
    from chimerax.core.commands import register as _register
    desc = CmdDesc(optional=[("request", RestOfLine)],
                   synopsis="Ask Pellaeon to do something in plain English")
    _register(name, desc, pellaeon_cmd, logger=logger)
