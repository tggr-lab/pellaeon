"""The Pellaeon panel inside ChimeraX: an HtmlToolInstance around PanelBase.

Python -> JS: ``Pellaeon.push({...})`` via runJavaScript (main thread only).
JS -> Python: navigations to ``pellaeon:<action>?<params>`` (intercepted by
ChimeraX's HtmlView and delivered to ``handle_scheme`` on the main thread).
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
import time
from typing import Any, Dict, List, Optional

from chimerax.ui import HtmlToolInstance

from .bridge import ChimeraXExecutor, data_path, pellaeon_dir
from .panel_base import PanelBase
from .settings import PellaeonSettings, SecretStore

_INSTANCES: Dict[int, "PellaeonTool"] = {}


def show_panel(session, page: Optional[str] = None) -> "PellaeonTool":
    inst = _INSTANCES.get(id(session))
    if inst is None or getattr(inst, "_deleted", False):
        inst = PellaeonTool(session, "Pellaeon")
    else:
        inst.tool_window.shown = True
    if page:
        inst.push({"type": "show_page", "page": page})
    return inst


class PellaeonTool(HtmlToolInstance, PanelBase):
    SESSION_ENDURING = True
    SESSION_SAVE = False
    CUSTOM_SCHEME = "pellaeon"
    PLACEMENT = "right"
    help = "https://github.com/tggr-lab/pellaeon#readme"
    edition = "chimerax"

    def call_soon(self, fn, *args):
        self.session.ui.thread_safe(fn, *args)

    def log(self, msg):
        self.session.ui.thread_safe(self.session.logger.info, msg)

    def __init__(self, session, tool_name):
        super().__init__(session, tool_name, size_hint=(440, 720), log_errors=True)
        self.display_name = "Pellaeon"
        _INSTANCES[id(session)] = self
        self._init_panel_state()
        self.dirs = {"data": pellaeon_dir("data"), "config": pellaeon_dir("config"), "cache": pellaeon_dir("cache")}
        self.data_dir = data_path()
        self.settings = PellaeonSettings(session, "Pellaeon")
        self.secrets = SecretStore(pellaeon_dir("config"))
        self.executor = ChimeraXExecutor(session, log=self._log_info)
        self._sel_handler = None
        try:
            self._sel_handler = session.triggers.add_handler("selection changed", self._selection_changed)
        except Exception:
            pass
        try:
            from .analysis import AskMouseMode
            mode = AskMouseMode.make(session, lambda info: self.session.ui.thread_safe(self._picked, info))
            session.ui.mouse_modes.add_mode(mode)
        except Exception as e:  # noqa: BLE001
            session.logger.info("Pellaeon: click-to-ask mouse mode unavailable: %s" % e)
        html = pathlib.Path(os.path.dirname(os.path.abspath(__file__)), "ui", "panel.html")
        from Qt.QtCore import QUrl
        self.html_view.setUrl(QUrl.fromLocalFile(str(html)))

    def _log_info(self, msg: str) -> None:
        self.session.ui.thread_safe(self.session.logger.info, msg)

    def push(self, obj: Dict[str, Any]) -> None:
        """Send a message to the page. Main thread only."""
        if self._deleted:
            return
        if not self._page_ready:
            self._queued.append(obj)
            return
        js = "window.Pellaeon && Pellaeon.push(%s);" % json.dumps(obj, ensure_ascii=False)
        try:
            self.html_view.runJavaScript(js)
        except Exception as e:  # noqa: BLE001
            self.session.logger.warning("Pellaeon UI push failed: %s" % e)

    def push_ts(self, obj: Dict[str, Any]) -> None:
        """Send a message to the page from any thread."""
        self.session.ui.thread_safe(self.push, obj)

    def _selection_changed(self, trigger_name, data):
        try:
            from chimerax.atomic import selected_residues
            from .bridge import _residues_spec
            res = selected_residues(self.session)
            if 0 < len(res) <= 3:
                r = res[0]
                self.push({"type": "selection", "spec": _residues_spec(res), "n": len(res),
                           "name": r.name, "number": int(r.number), "chain": r.chain_id, "model": "#" + r.structure.id_string})
            else:
                self.push({"type": "selection", "spec": "", "n": len(res)})
        except Exception:
            pass

    def _picked(self, info):
        self.tool_window.shown = True
        self.push({"type": "picked", "pick": info})

    def _act_bind_click(self, params, payload):
        from chimerax.core.commands import run
        try:
            run(self.session, 'ui mousemode alt leftMode "pellaeon ask"', log=True)
            self.push({"type": "toast", "kind": "ok", "text": "Alt+click any atom to ask about it"})
        except Exception as e:  # noqa: BLE001
            self.push({"type": "toast", "kind": "error", "text": "Could not bind mouse mode: %s" % e})

    def delete(self):
        self._deleted = True
        if self._sel_handler is not None:
            try:
                self.session.triggers.remove_handler(self._sel_handler)
            except Exception:
                pass
        self._cancel.set()
        for c in self._confirms.values():
            c["event"].set()
        _INSTANCES.pop(id(self.session), None)
        super().delete()

    def _act_copy(self, params, payload):
        from Qt.QtWidgets import QApplication
        QApplication.clipboard().setText(params.get("text", ""))
        self.push({"type": "toast", "kind": "ok", "text": "Copied"})

    def _act_open_url(self, params, payload):
        url = params.get("url", "")
        if url.startswith("help:"):
            from chimerax.core.commands import run
            run(self.session, "help %s" % url, log=False)
        elif url.startswith(("http://", "https://")):
            from chimerax.help_viewer import show_url
            show_url(self.session, url, new_tab=True)

    def _pick_table_file(self):
        from Qt.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self.session.ui.main_window, "Import a per-residue table", "",
                                              "Tables (*.csv *.tsv *.txt);;All files (*)")
        return path or None

    def _act_export_cxc(self, params, payload):
        """Save everything that actually ran in this chat (any path) as a .cxc script."""
        from .panel_base import export_lines
        if not self.agent or not self.agent.journal:
            self.push({"type": "toast", "kind": "warn", "text": "No commands were run in this chat yet."})
            return
        lines = export_lines(self.agent)
        from Qt.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self.session.ui.main_window, "Export commands", "pellaeon_session.cxc",
                                              "ChimeraX command script (*.cxc)")
        if not path:
            return
        if not path.lower().endswith(".cxc"):
            path += ".cxc"
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        n = sum(1 for l in lines if l and not l.startswith("#"))
        self.session.logger.info("Pellaeon: exported %d commands to %s (open it with: open %s)" % (n, path, path))
        self.push({"type": "toast", "kind": "ok", "text": "Saved " + os.path.basename(path)})

    def update_models(self, trigger_name, trigger_data):
        # called on model add/remove; throttle a little
        now = time.time()
        if now - self._last_state_push < 0.5:
            return
        self._last_state_push = now
        self.push({"type": "state", "state": self._safe_state()})
