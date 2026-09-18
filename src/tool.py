"""The Pellaeon panel: an HTML chat UI docked inside ChimeraX.

Python -> JS: ``Pellaeon.push({...})`` via runJavaScript (main thread only).
JS -> Python: navigations to ``pellaeon:<action>?<params>`` (intercepted by
ChimeraX's HtmlView and delivered to ``handle_scheme`` on the main thread).
The agent runs in a worker thread; everything it needs from ChimeraX goes
through ``bridge.ChimeraXExecutor``.
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from chimerax.ui import HtmlToolInstance

from . import __version__
from .bridge import ChimeraXExecutor, data_path, pellaeon_dir
from .core.agent import Agent, AgentConfig, Callbacks
from .core.knowledge import load_json
from .core.markdown import render as render_markdown
from .core.providers.base import ProviderError
from .core.providers.presets import PRESETS, make_provider, preset_by_id
from .core.safety import AUTONOMY_MODES
from .core.schema import conversation_to_json, conversation_from_json
from .settings import PellaeonSettings, SecretStore, masked

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


class PellaeonTool(HtmlToolInstance):
    SESSION_ENDURING = True
    SESSION_SAVE = False
    CUSTOM_SCHEME = "pellaeon"
    PLACEMENT = "right"
    help = "https://github.com/pellaeon-chimerax/pellaeon#readme"

    def __init__(self, session, tool_name):
        super().__init__(session, tool_name, size_hint=(440, 720), log_errors=True)
        self.display_name = "Pellaeon"
        _INSTANCES[id(session)] = self
        self._deleted = False
        self.settings = PellaeonSettings(session, "Pellaeon")
        self.secrets = SecretStore(pellaeon_dir("config"))
        self.executor = ChimeraXExecutor(session, log=self._log_info)
        self.agent: Optional[Agent] = None
        self._worker: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self._busy = False
        self._page_ready = False
        self._queued: List[Dict[str, Any]] = []
        self._turn_id = 0
        self._delta_lock = threading.Lock()
        self._delta_buf: List[str] = []
        self._delta_scheduled = False
        self._confirms: Dict[str, Dict[str, Any]] = {}
        self._conv_id = ""
        self._last_state_push = 0.0
        html = pathlib.Path(os.path.dirname(os.path.abspath(__file__)), "ui", "panel.html")
        from Qt.QtCore import QUrl
        self.html_view.setUrl(QUrl.fromLocalFile(str(html)))

    # ------------------------------------------------------------ plumbing
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

    def delete(self):
        self._deleted = True
        self._cancel.set()
        for c in self._confirms.values():
            c["event"].set()
        _INSTANCES.pop(id(self.session), None)
        super().delete()

    # ------------------------------------------------------------ JS -> Python
    def handle_scheme(self, url):
        action = url.path() or url.host()
        action = action.strip("/")
        params = dict(urllib.parse.parse_qsl(url.query(), keep_blank_values=True))
        payload: Any = None
        if "payload" in params:
            try:
                payload = json.loads(params["payload"])
            except Exception:
                payload = None
        try:
            handler = getattr(self, "_act_" + action, None)
            if handler is None:
                self.session.logger.warning("Pellaeon: unknown UI action %r" % action)
                return
            handler(params, payload)
        except Exception as e:  # noqa: BLE001
            import traceback
            self.session.logger.warning("Pellaeon UI action %s failed: %s\n%s" % (action, e, traceback.format_exc()))
            self.push({"type": "toast", "kind": "error", "text": "Pellaeon: %s" % e})

    def _act_ready(self, params, payload):
        self._page_ready = True
        self.push(self._init_message())
        for obj in self._queued:
            self.push(obj)
        self._queued = []
        self.push({"type": "state", "state": self._safe_state()})
        # warm the docs index in the background so the first request is fast
        threading.Thread(target=self._warm_index, daemon=True).start()

    def _act_send(self, params, payload):
        text = (params.get("text") or "").strip()
        if text:
            self.submit(text)

    def _act_stop(self, params, payload):
        self._cancel.set()
        if not self._busy and getattr(self, "_pull_cancel", None) is not None:
            self._pull_cancel.set()
        for c in self._confirms.values():
            c["result"] = None
            c["event"].set()
        self.push({"type": "status", "text": "Stopping…"})

    def _act_confirm(self, params, payload):
        cid = params.get("confirm_id", "")
        c = self._confirms.get(cid)
        if not c:
            return
        decision = params.get("decision", "skip")
        if decision == "run":
            cmds = payload if isinstance(payload, list) else c["commands"]
            c["result"] = [str(x) for x in cmds if str(x).strip()]
        else:
            c["result"] = None
        c["event"].set()
        self.push({"type": "confirm_done", "confirm_id": cid, "decision": decision})

    def _act_rerun(self, params, payload):
        cmds = payload if isinstance(payload, list) else [params.get("command", "")]
        cmds = [c for c in cmds if c and c.strip()]
        if not cmds:
            return
        results = self.executor.run_commands(cmds)
        self.push({"type": "rerun_result", "results": results})
        self.push({"type": "state", "state": self._safe_state()})

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

    def _busy_guard(self) -> bool:
        if self._busy:
            self.push({"type": "toast", "kind": "warn", "text": "Still working. Press Stop first."})
            return True
        return False

    def _act_new_chat(self, params, payload):
        if self._busy_guard():
            return
        self._save_conversation()
        self._conv_id = ""
        if self.agent:
            self.agent.reset()
        self.push({"type": "clear"})

    def _act_set_autonomy(self, params, payload):
        mode = params.get("mode", "auto")
        if mode in AUTONOMY_MODES:
            self.settings.autonomy = mode
            if self.agent:
                self.agent.config.autonomy = mode

    def _act_settings_get(self, params, payload):
        self.push(self._settings_message())

    def _act_settings_save(self, params, payload):
        if not isinstance(payload, dict) or self._busy_guard():
            return
        s = self.settings
        preset = preset_by_id(payload.get("preset", s.preset)) or preset_by_id("ollama")
        s.preset = preset["id"]
        s.provider = preset["provider"]
        s.model = (payload.get("model") or preset["model"] or "").strip()
        s.base_url = (payload.get("base_url") or "").strip()
        s.autonomy = payload.get("autonomy", s.autonomy) if payload.get("autonomy") in AUTONOMY_MODES else s.autonomy
        s.allow_python = bool(payload.get("allow_python", False))
        s.vision = bool(payload.get("vision", False))
        s.think = bool(payload.get("think", False))
        s.effort = str(payload.get("effort", "") or "")
        try:
            s.temperature = float(payload.get("temperature", s.temperature))
        except (TypeError, ValueError):
            pass
        if payload.get("clear_key"):
            self.secrets.set(s.preset, "")
        elif payload.get("api_key"):
            self.secrets.set(s.preset, payload["api_key"])
        s.configured = True
        s.save()
        # rebuild the agent with the new provider but keep the conversation
        old = self.agent
        self.agent = None
        if old is not None and (old.conversation or old.archived):
            try:
                self.agent = self._build_agent()
                self.agent.conversation, self.agent.archived, self.agent.summary = old.conversation, old.archived, old.summary
            except Exception as e:  # noqa: BLE001
                self.push({"type": "toast", "kind": "error", "text": "Could not switch provider: %s" % e})
        self.push(self._settings_message())
        self.push({"type": "toast", "kind": "ok", "text": "Settings saved"})
        self.push({"type": "config", "config": self._config_summary()})

    def _act_settings_test(self, params, payload):
        if not isinstance(payload, dict):
            return
        threading.Thread(target=self._test_connection, args=(payload,), daemon=True).start()

    def _act_list_models(self, params, payload):
        if not isinstance(payload, dict):
            return
        threading.Thread(target=self._list_models, args=(payload,), daemon=True).start()

    def _act_pull_model(self, params, payload):
        model = (params.get("model") or "").strip()
        base_url = (params.get("base_url") or "").strip()
        if model:
            self._pull_cancel = threading.Event()
            threading.Thread(target=self._pull_model, args=(model, base_url), daemon=True).start()

    def _act_rebuild_index(self, params, payload):
        threading.Thread(target=self._rebuild_index, daemon=True).start()

    def _act_history(self, params, payload):
        self.push({"type": "history", "conversations": self._list_conversations()})

    def _act_load_chat(self, params, payload):
        if self._busy_guard():
            return
        cid = params.get("id", "")
        self._load_conversation(cid)

    def _act_delete_chat(self, params, payload):
        cid = params.get("id", "")
        path = os.path.join(pellaeon_dir("data"), "conversations", cid + ".json")
        if cid and os.path.exists(path):
            os.remove(path)
        self.push({"type": "history", "conversations": self._list_conversations()})

    def _act_export_cxc(self, params, payload):
        """Save every successfully executed command of this chat as a .cxc script."""
        if not self.agent or not (self.agent.archived or self.agent.conversation):
            self.push({"type": "toast", "kind": "warn", "text": "Nothing to export yet."})
            return
        lines = ["# ChimeraX command script exported by Pellaeon", ""]
        for m in self.agent.archived + self.agent.conversation:
            if m.role == "user" and not m.text().startswith("(system)"):
                lines.append("# " + m.text().replace("\n", " ")[:200])
            elif m.role == "tool":
                for r in m.tool_results_list():
                    if r.name != "run_commands":
                        continue
                    try:
                        res = json.loads(r.content)
                    except Exception:
                        continue
                    for x in res.get("results", []):
                        if x.get("ok"):
                            lines.append(x["command"])
        if len(lines) <= 2:
            self.push({"type": "toast", "kind": "warn", "text": "No commands were run in this chat."})
            return
        from Qt.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self.session.ui.main_window, "Export commands", "pellaeon_session.cxc",
                                              "ChimeraX command script (*.cxc)")
        if not path:
            return
        if not path.lower().endswith(".cxc"):
            path += ".cxc"
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self.session.logger.info("Pellaeon: exported %d commands to %s (open it with: open %s)" % (
            sum(1 for l in lines if l and not l.startswith("#")), path, path))
        self.push({"type": "toast", "kind": "ok", "text": "Saved " + os.path.basename(path)})

    def _act_log(self, params, payload):
        self.session.logger.info("Pellaeon UI: %s" % params.get("text", ""))

    # ------------------------------------------------------------ background helpers
    def _provider_from_payload(self, payload: Dict[str, Any]):
        preset = preset_by_id(payload.get("preset", "")) or preset_by_id("ollama")
        key = payload.get("api_key")
        if not key or key == "__unchanged__":
            key = self.secrets.get(preset["id"])
        return make_provider(preset["provider"], (payload.get("model") or preset["model"] or ""),
                             api_key=key or "", base_url=(payload.get("base_url") or preset["base_url"] or ""),
                             timeout=30)

    def _test_connection(self, payload):
        try:
            prov = self._provider_from_payload(payload)
            text = prov.test()
            models = []
            try:
                models = prov.list_models()
            except Exception:
                pass
            self.push_ts({"type": "test_result", "ok": True, "text": text, "models": models[:300]})
        except ProviderError as e:
            self.push_ts({"type": "test_result", "ok": False, "text": str(e)})
        except Exception as e:  # noqa: BLE001
            self.push_ts({"type": "test_result", "ok": False, "text": "Failed: %s" % e})

    def _list_models(self, payload):
        try:
            prov = self._provider_from_payload(payload)
            models = prov.list_models()
            self.push_ts({"type": "models_list", "models": models[:300]})
        except Exception as e:  # noqa: BLE001
            self.push_ts({"type": "models_list", "models": [], "error": str(e)})

    def _pull_model(self, model: str, base_url: str):
        from .core.providers.ollama import OllamaProvider
        prov = OllamaProvider(model, base_url=base_url)
        last = [0.0]

        def progress(status, completed, total):
            now = time.time()
            if now - last[0] > 0.3 or (total and completed == total):
                last[0] = now
                self.push_ts({"type": "pull_progress", "model": model, "status": status,
                              "completed": completed, "total": total, "done": False})
        try:
            prov.pull(model, progress=progress, cancel=getattr(self, "_pull_cancel", None))
            self.push_ts({"type": "pull_progress", "model": model, "status": "done", "completed": 1, "total": 1, "done": True})
        except Exception as e:  # noqa: BLE001
            self.push_ts({"type": "pull_progress", "model": model, "status": "error: %s" % e, "done": True, "error": True})

    def _warm_index(self):
        try:
            self.executor.ensure_index()
            self.push_ts({"type": "index_progress", "finished": True,
                          "count": len(self.executor.knowledge.index.chunks) if self.executor.knowledge.index else 0})
        except Exception as e:  # noqa: BLE001
            self.push_ts({"type": "toast", "kind": "error", "text": "Docs index failed: %s" % e})

    def _rebuild_index(self):
        def progress(done, total):
            self.push_ts({"type": "index_progress", "done": done, "total": total, "finished": False})
        try:
            idx = self.executor.rebuild_index(progress)
            self.push_ts({"type": "index_progress", "finished": True, "count": len(idx.chunks)})
        except Exception as e:  # noqa: BLE001
            self.push_ts({"type": "toast", "kind": "error", "text": "Docs index failed: %s" % e})

    # ------------------------------------------------------------ messages to the page
    def _init_message(self) -> Dict[str, Any]:
        return {"type": "init", "version": __version__, "presets": PRESETS,
                "settings": self._settings_message()["settings"],
                "key_masked": self._settings_message()["key_masked"],
                "key_source": self._settings_message()["key_source"],
                "configured": bool(self.settings.configured),
                "config": self._config_summary(),
                "conversations": self._list_conversations()[:20]}

    def _settings_message(self) -> Dict[str, Any]:
        s = self.settings
        key = self.secrets.get(s.preset)
        return {"type": "settings",
                "settings": {"preset": s.preset, "provider": s.provider, "model": s.model, "base_url": s.base_url,
                             "autonomy": s.autonomy, "allow_python": bool(s.allow_python), "vision": bool(s.vision),
                             "think": bool(s.think), "temperature": s.temperature, "effort": s.effort,
                             "configured": bool(s.configured)},
                "key_masked": masked(key), "key_source": self.secrets.source(s.preset)}

    def _config_summary(self) -> Dict[str, Any]:
        preset = preset_by_id(self.settings.preset) or {}
        return {"preset": self.settings.preset, "label": preset.get("label", self.settings.preset),
                "model": self.settings.model, "autonomy": self.settings.autonomy,
                "configured": bool(self.settings.configured)}

    def _safe_state(self) -> Dict[str, Any]:
        try:
            return self.executor.get_state()
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    def update_models(self, trigger_name, trigger_data):
        # called on model add/remove; throttle a little
        now = time.time()
        if now - self._last_state_push < 0.5:
            return
        self._last_state_push = now
        self.push({"type": "state", "state": self._safe_state()})

    # ------------------------------------------------------------ the agent
    def _build_agent(self) -> Agent:
        s = self.settings
        preset = preset_by_id(s.preset) or preset_by_id("ollama")
        options: Dict[str, Any] = {"temperature": float(s.temperature)}
        if preset["provider"] == "ollama":
            options["think"] = bool(s.think)
        if preset["provider"] == "anthropic" and s.effort:
            options["effort"] = s.effort
        provider = make_provider(preset["provider"], s.model or preset["model"],
                                 api_key=self.secrets.get(preset["id"]),
                                 base_url=s.base_url or preset["base_url"], options=options)
        gotchas = None
        recipes = None
        try:
            with open(data_path("gotchas.md"), "r", encoding="utf-8") as f:
                gotchas = f.read()
        except Exception:
            pass
        try:
            recipes = load_json(data_path("recipes.json"))
        except Exception:
            pass
        directory = self.executor.knowledge.command_directory()
        cfg = AgentConfig(autonomy=s.autonomy, allow_python=bool(s.allow_python), vision=bool(s.vision),
                          docs_per_turn=int(s.docs_per_turn))
        if preset["provider"] == "ollama":
            # keep the conversation well inside the local context window (32k by default)
            cfg.compact_after_tokens = max(8000, int(options.get("num_ctx", 32768) * 0.55))
        cb = Callbacks(on_text_delta=self._on_delta, on_tool_start=self._on_tool_start,
                       on_tool_result=self._on_tool_result, on_confirm=self._on_confirm,
                       on_ask_user=self._on_ask_user, on_status=self._on_status)
        return Agent(provider, self.executor, config=cfg, callbacks=cb,
                     directory=directory, gotchas=gotchas, recipes=recipes)

    def submit(self, text: str) -> None:
        """Start a turn (main thread)."""
        if self._busy:
            self.push({"type": "toast", "kind": "warn", "text": "Still working on the previous request. Press Stop to interrupt."})
            return
        if not self.settings.configured:
            self.push({"type": "show_page", "page": "settings"})
            self.push({"type": "toast", "kind": "warn", "text": "Choose an AI provider first."})
            return
        if self.agent is None:
            try:
                self.agent = self._build_agent()
            except Exception as e:  # noqa: BLE001
                self.push({"type": "toast", "kind": "error", "text": "Could not start: %s" % e})
                return
        self._busy = True
        self._cancel = threading.Event()
        self._turn_id += 1
        tid = "t%d" % self._turn_id
        self.push({"type": "user_message", "id": tid, "text": text})
        self.push({"type": "assistant_start", "id": tid})
        self.push({"type": "busy", "busy": True})
        self._worker = threading.Thread(target=self._run_turn, args=(tid, text), daemon=True)
        self._worker.start()

    def _run_turn(self, tid: str, text: str):
        agent = self.agent
        cancel = self._cancel
        try:
            result = agent.run_turn(text, cancel)
        except Exception as e:  # noqa: BLE001
            result = None
            self.push_ts({"type": "toast", "kind": "error", "text": "Pellaeon crashed: %s" % e})
        self._flush_deltas_ts()
        if result is not None:
            html = render_markdown(result.reply) if result.reply else ""
            self.push_ts({"type": "assistant_done", "id": tid, "html": html, "error": result.error,
                          "asked_user": result.asked_user,
                          "usage": {"input": result.usage.input_tokens, "output": result.usage.output_tokens,
                                    "cached": result.usage.cache_read_tokens,
                                    "total_input": agent.total_usage.input_tokens,
                                    "total_output": agent.total_usage.output_tokens}})
        self.session.ui.thread_safe(self._turn_finished)

    def _turn_finished(self):
        self._busy = False
        self.push({"type": "busy", "busy": False})
        self.push({"type": "state", "state": self._safe_state()})
        self._save_conversation()

    # ---- callbacks from the worker thread ----
    def _on_delta(self, text: str):
        with self._delta_lock:
            self._delta_buf.append(text)
            if self._delta_scheduled:
                return
            self._delta_scheduled = True
        self.session.ui.thread_safe(self._flush_deltas)

    def _flush_deltas(self):
        with self._delta_lock:
            text = "".join(self._delta_buf)
            self._delta_buf = []
            self._delta_scheduled = False
        if text:
            self.push({"type": "assistant_delta", "id": "t%d" % self._turn_id, "text": text})

    def _flush_deltas_ts(self):
        self.session.ui.thread_safe(self._flush_deltas)

    def _on_tool_start(self, call):
        self._flush_deltas_ts()
        self.push_ts({"type": "tool_start", "id": "t%d" % self._turn_id, "call_id": call.id,
                      "name": call.name, "args": call.args})

    def _on_tool_result(self, call, result, payload):
        msg: Dict[str, Any] = {"type": "tool_result", "id": "t%d" % self._turn_id, "call_id": call.id,
                               "name": call.name, "ok": not result.is_error}
        if call.name == "run_commands" and isinstance(payload, dict):
            msg["results"] = payload.get("results", [])
            msg["error"] = payload.get("error", "")
            msg["skipped"] = bool(payload.get("skipped"))
            msg["not_run"] = payload.get("not_run", [])
        else:
            msg["summary"] = _summarize_tool(call, result, payload)
        self.push_ts(msg)

    def _on_confirm(self, commands: List[str], reasons: List[str]) -> Optional[List[str]]:
        cid = "c%d_%d" % (self._turn_id, int(time.time() * 1000))
        entry = {"event": threading.Event(), "result": None, "commands": commands}
        self._confirms[cid] = entry
        self._flush_deltas_ts()
        self.push_ts({"type": "confirm", "id": "t%d" % self._turn_id, "confirm_id": cid,
                      "commands": commands, "reasons": reasons})
        while not entry["event"].is_set():
            if self._cancel.is_set():
                break
            entry["event"].wait(0.2)
        self._confirms.pop(cid, None)
        return entry["result"]

    def _on_ask_user(self, question: str, options: List[str]):
        self._flush_deltas_ts()
        self.push_ts({"type": "question", "id": "t%d" % self._turn_id, "question": question, "options": options})

    def _on_status(self, text: str):
        self.push_ts({"type": "status", "text": text})

    # ------------------------------------------------------------ conversation persistence
    def _conv_dir(self) -> str:
        d = os.path.join(pellaeon_dir("data"), "conversations")
        os.makedirs(d, exist_ok=True)
        return d

    def _save_conversation(self):
        if not self.agent or not self.agent.conversation:
            return
        if not self._conv_id:
            self._conv_id = time.strftime("%Y%m%d-%H%M%S")
        first_user = next((m.text() for m in self.agent.conversation if m.role == "user"), "Conversation")
        first_user = next((m.text() for m in (self.agent.archived + self.agent.conversation)
                           if m.role == "user" and not m.text().startswith("(system)")), first_user)
        doc = {"id": self._conv_id, "title": first_user[:80], "updated": time.time(),
               "summary": self.agent.summary,
               "archived": json.loads(conversation_to_json(self.agent.archived)),
               "messages": json.loads(conversation_to_json(self.agent.conversation))}
        try:
            with open(os.path.join(self._conv_dir(), self._conv_id + ".json"), "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            self.session.logger.warning("Pellaeon: could not save conversation: %s" % e)

    def _list_conversations(self) -> List[Dict[str, Any]]:
        out = []
        try:
            for name in os.listdir(self._conv_dir()):
                if not name.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(self._conv_dir(), name), "r", encoding="utf-8") as f:
                        doc = json.load(f)
                    out.append({"id": doc.get("id", name[:-5]), "title": doc.get("title", ""),
                                "updated": doc.get("updated", 0), "n": len(doc.get("messages", []))})
                except Exception:
                    continue
        except Exception:
            pass
        out.sort(key=lambda d: -d["updated"])
        return out

    def _load_conversation(self, cid: str):
        path = os.path.join(self._conv_dir(), cid + ".json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception as e:  # noqa: BLE001
            self.push({"type": "toast", "kind": "error", "text": "Could not load: %s" % e})
            return
        if self.agent is None:
            try:
                self.agent = self._build_agent()
            except Exception as e:  # noqa: BLE001
                self.push({"type": "toast", "kind": "error", "text": "Could not start: %s" % e})
                return
        self._save_conversation()
        self.agent.conversation = conversation_from_json(json.dumps(doc.get("messages", [])))
        self.agent.archived = conversation_from_json(json.dumps(doc.get("archived", [])))
        self.agent.summary = doc.get("summary", "")
        self._conv_id = cid
        rendered = []
        for m in self.agent.archived + self.agent.conversation:
            if m.role == "user" and not m.text().startswith("(system)"):
                rendered.append({"role": "user", "text": m.text()})
            elif m.role == "assistant":
                calls = [{"name": c.name, "args": c.args} for c in m.tool_calls()]
                if m.text().strip() or calls:
                    rendered.append({"role": "assistant", "html": render_markdown(m.text()), "calls": calls})
        self.push({"type": "conversation", "id": cid, "title": doc.get("title", ""), "messages": rendered})


def _summarize_tool(call, result, payload) -> str:
    name = call.name
    a = call.args or {}
    if name == "resolve_protein":
        if isinstance(payload, dict) and payload.get("accession"):
            return "UniProt: %s → %s (%s, %s aa)" % (a.get("query", ""), payload["accession"],
                                                   payload.get("protein_name", "")[:60], payload.get("length"))
        return "UniProt lookup for %s failed" % a.get("query", "")
    if name == "get_state":
        n = len(payload.get("models", [])) if isinstance(payload, dict) else 0
        return "Checked what is open (%d models)" % n
    if name == "command_usage":
        return "Looked up syntax of '%s'" % a.get("name", "")
    if name == "search_docs":
        return "Searched the docs for '%s'" % a.get("query", "")
    if name == "protein_features":
        n = payload.get("count", 0) if isinstance(payload, dict) else 0
        return "Fetched %d UniProt features for %s" % (n, a.get("accession", ""))
    if name == "run_python":
        return "Ran Python code" + ("" if not result.is_error else " (failed)")
    if name == "look_at_view":
        return "Looked at the 3D view"
    return name
