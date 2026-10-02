"""Panel controller shared by the ChimeraX bundle and the classic (UCSF Chimera) edition.

Subclasses provide: push(obj) (deliver a message to the page), call_soon(fn, *args)
(run on the UI thread), log(msg), self.executor, self.settings (attributes + save()),
self.secrets, self.dirs {"data","config","cache"}, and the ChimeraX/Chimera-specific actions.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional

try:  # inside the ChimeraX bundle (package chimerax.pellaeon)
    from .core.agent import Agent, AgentConfig, Callbacks
    from .core.knowledge import load_json
    from .core.learned import LearnedFixes
    from .core.markdown import render as render_markdown
    from .core.providers.base import ProviderError
    from .core.providers.presets import PRESETS, make_provider, preset_by_id
    from .core.providers import chatgpt_auth
    from .core.safety import AUTONOMY_MODES
    from .core.prompt import INITIATIVE_MODES
    from .core.schema import conversation_to_json, conversation_from_json
    from .core.secrets import masked
    from . import __version__
except ImportError:  # classic edition: src/ is on sys.path
    from core.agent import Agent, AgentConfig, Callbacks
    from core.learned import LearnedFixes
    from core.knowledge import load_json
    from core.markdown import render as render_markdown
    from core.providers.base import ProviderError
    from core.providers.presets import PRESETS, make_provider, preset_by_id
    from core.providers import chatgpt_auth
    from core.safety import AUTONOMY_MODES
    from core.prompt import INITIATIVE_MODES
    from core.schema import conversation_to_json, conversation_from_json
    from core.secrets import masked
    __version__ = "0.1.0"


class PanelBase:
    """Everything the chat panel does that does not depend on the host application."""

    executor = None
    settings = None
    secrets = None
    dirs: Dict[str, str] = {}
    data_dir: str = ""          # bundled data files (cheatsheet.json, recipes.json, gotchas.md, ...)
    edition: str = "chimerax"

    def _init_panel_state(self):
        self._deleted = False
        self.agent: Optional[Agent] = None
        self._worker: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self._busy = False
        self.tables: Dict[str, Dict[str, Any]] = {}
        self._page_ready = False
        self._queued: List[Dict[str, Any]] = []
        self._turn_id = 0
        self._delta_lock = threading.Lock()
        self._delta_buf: List[str] = []
        self._delta_scheduled = False
        self._confirms: Dict[str, Dict[str, Any]] = {}
        self._conv_id = ""
        self._last_state_push = 0.0

    def data_path(self, *parts: str) -> str:
        return os.path.join(self.data_dir, *parts)

    # ---- host hooks (override) ----
    def push(self, obj: Dict[str, Any]) -> None:
        raise NotImplementedError

    def push_ts(self, obj: Dict[str, Any]) -> None:
        self.call_soon(self.push, obj)

    def call_soon(self, fn, *args) -> None:
        fn(*args)

    def log(self, msg: str) -> None:
        print(msg)

    def _act_copy(self, params, payload):
        pass

    def _act_open_url(self, params, payload):
        pass

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
                self.log("Pellaeon: unknown UI action %r" % action)
                return
            handler(params, payload)
        except Exception as e:  # noqa: BLE001
            import traceback
            self.log("Pellaeon UI action %s failed: %s\n%s" % (action, e, traceback.format_exc()))
            self.push({"type": "toast", "kind": "error", "text": "Pellaeon: %s" % e})

    def _act_ready(self, params, payload):
        first = not self._page_ready
        self._page_ready = True
        self.push(self._init_message())
        for obj in self._queued:
            self.push(obj)
        self._queued = []
        self.push({"type": "state", "state": self._safe_state()})
        if not first or (self.agent and self.agent.conversation):
            # a reloaded page: replay the transcript, busy state and any pending confirmation
            if self.agent and (self.agent.conversation or self.agent.archived):
                self.push({"type": "conversation", "id": self._conv_id, "title": "", "messages": self._render_messages()})
            if self._busy:
                self.push({"type": "assistant_start", "id": "t%d" % self._turn_id})
                self.push({"type": "busy", "busy": True})
            for cid, c in list(self._confirms.items()):
                self.push({"type": "confirm", "id": "t%d" % self._turn_id, "confirm_id": cid, "commands": c["commands"],
                           "reasons": [""] * len(c["commands"]), "python": bool(c.get("python"))})
        # warm the docs index in the background so the first request is fast
        threading.Thread(target=self._warm_index, daemon=True).start()

    # ---- tables (bring your own data) ----
    def _pick_table_file(self) -> Optional[str]:
        """Host hook: ask the user for a CSV/TSV path (None when unsupported or cancelled)."""
        self.push({"type": "toast", "kind": "warn", "text": "Table import is not available in this edition yet."})
        return None

    def _act_table_import(self, params, payload):
        path = (params.get("path") or "").strip() or self._pick_table_file()
        if not path:
            return
        try:
            from .core.tables import parse_table, guess_columns
        except ImportError:
            from core.tables import parse_table, guess_columns
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError as e:
            self.push({"type": "toast", "kind": "error", "text": "Could not read %s: %s" % (path, e)})
            return
        t = parse_table(text, path)
        if t.get("error"):
            self.push({"type": "toast", "kind": "error", "text": t["error"]})
            return
        g = guess_columns(t["columns"], t["rows"])
        if g["position"] is None:
            self.push({"type": "toast", "kind": "error", "text": "No residue-position column found (a column of integers such as 'position' or 'resnum')."})
            return
        name = os.path.splitext(os.path.basename(path))[0]
        self.tables[name] = {"id": name, "name": name, "path": path, "columns": t["columns"], "rows": t["rows"], "guess": g, "delimiter": t["delimiter"]}
        if self.agent is not None:
            self.agent.tables = self.tables
        st = self._safe_state()
        self.push({"type": "table_preview", "dataset": name, "path": path, "columns": t["columns"], "kinds": g["kinds"], "rows": len(t["rows"]),
                   "sample": t["rows"][:5], "position": g["position"], "value": g["default_value"], "values": g["values"],
                   "categories": g["categories"], "chain_col": g["chain"], "reference": g["reference"], "accession_col": g["accession"],
                   "models": [m.get("id") for m in (st.get("models") or []) if m.get("id")], "delimiter": t["delimiter"]})

    def _act_table_apply(self, params, payload):
        if self._busy_guard():
            return
        if self.agent is None:
            if not self.settings.configured:
                self.push({"type": "toast", "kind": "warn", "text": "Choose an AI provider first (the overlay itself does not use the AI, but the panel needs a configured session)."})
                return
            try:
                self.agent = self._build_agent()
            except Exception as e:  # noqa: BLE001
                self.push({"type": "toast", "kind": "error", "text": "Could not start: %s" % e})
                return
        self.agent.tables = self.tables
        args = {k: (params.get(k) or "") for k in ("dataset", "column", "chain", "palette", "model", "accession")}
        label = str(params.get("label", "")).lower() in ("1", "true", "yes")
        self._busy = True
        self._cancel = threading.Event()
        self._turn_id += 1
        tid = "t%d" % self._turn_id
        self.push({"type": "busy", "busy": True})
        self.push({"type": "status", "text": "Placing the table on the structure…"})

        def work():
            try:
                out = self.agent._table_overlay(args["dataset"], args["column"], args["chain"] or None, args["palette"],
                                                args["model"], args["accession"] or None, label)
            except Exception as e:  # noqa: BLE001
                out = {"error": "Overlay failed: %s" % e}
            self.push_ts({"type": "table_result", "id": tid, "card": out, "results": out.get("commands", [])})
            self.push_ts({"type": "layers", "layers": list(self.agent.layers)})
            self._busy = False
            self.push_ts({"type": "busy", "busy": False})
        threading.Thread(target=work, daemon=True).start()

    def _act_table_remove(self, params, payload):
        name = params.get("dataset", "")
        self.tables.pop(name, None)
        if self.agent is not None:
            self.agent.tables = self.tables
            self.agent.layers = [l for l in self.agent.layers if l.get("dataset") != name]
            self.push({"type": "layers", "layers": list(self.agent.layers)})
        self.push({"type": "toast", "kind": "ok", "text": "Removed table %s (colors stay until you recolor)." % name})

    # ---- figure bundles ----
    def _pick_folder(self) -> Optional[str]:
        """Host hook: choose a folder for figure bundles (None when unsupported or cancelled)."""
        self.push({"type": "toast", "kind": "warn", "text": "Figure bundles are not available in this edition yet."})
        return None

    def _act_figure_form(self, params, payload):
        st = self._safe_state()
        names = [m.get("name", "") for m in (st.get("models") or []) if m.get("name")]
        default = (params.get("name") or "").strip() or "_".join(n for n in names[:2]) or "figure"
        folder = getattr(self.settings, "figures_dir", "") or os.path.join(os.path.expanduser("~"), "Pellaeon figures")
        self.push({"type": "figure_form", "name": re.sub(r"[^A-Za-z0-9_.-]+", "_", default), "folder": folder,
                   "models": [m.get("id") for m in (st.get("models") or []) if m.get("id")]})

    def _act_figure_pick_folder(self, params, payload):
        path = self._pick_folder()
        if path:
            try:
                self.settings.figures_dir = path
                self.settings.save()
            except Exception:  # noqa: BLE001
                pass
            self.push({"type": "figure_folder", "path": path})

    def _act_figure_save(self, params, payload):
        if self._busy_guard():
            return
        if self.agent is None:
            if not self.settings.configured:
                self.push({"type": "toast", "kind": "warn", "text": "Choose an AI provider first."})
                return
            self.agent = self._build_agent()
        folder = (params.get("folder") or "").strip() or os.path.join(os.path.expanduser("~"), "Pellaeon figures")
        name = (params.get("name") or "").strip()
        try:
            width = int(params.get("width") or 2400); height = int(params.get("height") or 1800); ss = int(params.get("supersample") or 3)
        except ValueError:
            width, height, ss = 2400, 1800, 3
        transparent = str(params.get("transparent", "")).lower() in ("1", "true", "yes")
        closeup = (params.get("closeup") or "").strip()
        include_session = str(params.get("session", "1")).lower() in ("1", "true", "yes")
        self.agent.figures_dir = folder
        self._busy = True
        self._turn_id += 1
        tid = "t%d" % self._turn_id
        self.push({"type": "busy", "busy": True}); self.push({"type": "status", "text": "Saving the figure bundle…"})

        def work():
            try:
                out = self.agent._figure_bundle(folder, name, width, height, ss, transparent, closeup, include_session, preapproved=True)
            except Exception as e:  # noqa: BLE001
                out = {"error": "Could not save the figure: %s" % e}
            self.push_ts({"type": "figure_result", "id": tid, "card": out, "results": out.get("commands", [])})
            self._busy = False
            self.push_ts({"type": "busy", "busy": False})
        threading.Thread(target=work, daemon=True).start()

    def _act_review_view(self, params, payload):
        """The model looks at the 3D view, reviews it as a figure and adjusts; needs a vision model and the setting."""
        try:
            preset = preset_by_id(self.settings.preset) or {}
        except Exception:  # noqa: BLE001
            preset = {}
        vision_provider = preset.get("provider") in ("gemini", "anthropic", "openai_compat", "openai", "chatgpt", "ollama")
        if vision_provider and not self.settings.vision:
            # pressing "Review the view" is the request to send a screenshot: switch it on instead of a detour to Settings
            self.settings.vision = True
            if self.agent is not None:
                self.agent.config.vision = True
            self.push({"type": "toast", "kind": "info", "text": "Screenshots switched on for this provider (Settings ▸ Advanced ▸ "
                       "'Let the model look at screenshots')."})
        if not self.settings.vision or not vision_provider:
            self.push({"type": "toast", "kind": "warn", "text": "Reviewing the view needs a model that can see images (ChatGPT, Gemini, Claude, OpenAI, or a vision-capable "
                       "Ollama model such as gemma4) and the setting 'Let the model look at screenshots' in Settings ▸ Advanced." if vision_provider else
                       "Reviewing the view needs a model that can see images: ChatGPT, Gemini, Claude, OpenAI or a vision-capable Ollama model."})
            if vision_provider:
                self.push({"type": "show_page", "page": "settings"})
            return
        self.submit("Look at the current view and review it as a figure: framing and size of the subject, anything clipped, label overlap or clutter, "
                    "colors that are hard to tell apart, background. Fix what you can with commands, look again to confirm, and tell me what you changed.")

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
            c["result"] = [str(x) for x in cmds if str(x).strip()] if not c.get("python") else [str(cmds[0]) if cmds else ""]
        else:
            c["result"] = None
        c["event"].set()
        self.push({"type": "confirm_done", "confirm_id": cid, "decision": decision})

    def _act_rerun(self, params, payload):
        cmds = payload if isinstance(payload, list) else [params.get("command", "")]
        cmds = [c for c in cmds if c and c.strip()]
        if not cmds or self._busy_guard():
            return
        if self.agent is None:
            try:
                self.agent = self._build_agent()
            except Exception as e:  # noqa: BLE001
                return self.push({"type": "toast", "kind": "error", "text": "Could not start: %s" % e})

        def work():
            out = self.agent.execute_commands(cmds, origin="rerun")   # same policy, confirmation and journal
            self.push_ts({"type": "rerun_result", "results": out.get("results", []), "skipped": bool(out.get("skipped")),
                          "error": out.get("error", "")})
            self.call_soon(lambda: self.push({"type": "state", "state": self._safe_state()}))
        threading.Thread(target=work, daemon=True).start()

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
            if mode != "all":            # "never ask" is for this session only: never persisted
                self.settings.autonomy = mode
            self._runtime_autonomy = mode
            if self.agent:
                self.agent.config.autonomy = mode

    def _act_settings_get(self, params, payload):
        self.push(self._settings_message())

    # ---- Sign in with ChatGPT (tokens live in the SecretStore under "chatgpt"; the e-mail is never kept) ----
    def _act_chatgpt_login(self, params, payload):
        flow = getattr(self, "_chatgpt_flow", None)
        if flow is not None and not flow.finished:
            flow.cancel()

        def done(tokens, error):
            if error is not None:
                self.push_ts({"type": "chatgpt_login", "ok": False, "text": str(error)})
                return
            try:
                chatgpt_auth.save_tokens(self.secrets, tokens)
            except Exception as e:  # noqa: BLE001
                self.push_ts({"type": "chatgpt_login", "ok": False, "text": "Signed in, but the tokens could not be stored: %s" % e})
                return
            self._set_chatgpt_tokens(tokens)
            self.push_ts({"type": "chatgpt_login", "ok": True, "text": "Signed in.",
                          "account": chatgpt_auth.status(self.secrets)})

        try:
            flow = chatgpt_auth.LoginFlow(done)
            url = flow.start()
        except chatgpt_auth.AuthError as e:
            self.push({"type": "chatgpt_login", "ok": False, "text": str(e)})
            return
        self._chatgpt_flow = flow
        self.push({"type": "chatgpt_login", "pending": True, "url": url,
                   "text": "Waiting for the browser. Sign in there, then come back here."})

    def _act_chatgpt_logout(self, params, payload):
        flow = getattr(self, "_chatgpt_flow", None)
        if flow is not None and not flow.finished:
            flow.cancel()
        chatgpt_auth.clear_tokens(self.secrets)
        self._set_chatgpt_tokens({})
        self.push({"type": "chatgpt_login", "ok": True, "text": "Signed out.", "account": chatgpt_auth.status(self.secrets)})

    def _set_chatgpt_tokens(self, tokens):
        """A running ChatGPT agent picks up the new sign-in without losing its conversation."""
        agent = self.agent
        if agent is not None and getattr(agent.provider, "name", "") == "chatgpt":
            agent.provider.tokens = dict(tokens or {})

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
        s.readable_labels = bool(payload.get("readable_labels", True))
        s.learn_fixes = bool(payload.get("learn_fixes", True))
        if hasattr(self, "_learned_store"):
            self._learned_store.enabled = s.learn_fixes
        s.vision = bool(payload.get("vision", False))
        s.think = bool(payload.get("think", False))
        s.effort = str(payload.get("effort", "") or "")
        if payload.get("initiative") in INITIATIVE_MODES:
            s.initiative = payload["initiative"]
        if payload.get("allow_docking") is not None and hasattr(self, "set_docking"):
            try:
                self.set_docking(bool(payload["allow_docking"]))
            except Exception as e:  # noqa: BLE001
                self.log("Pellaeon: could not change docking: %s" % e)
        try:
            s.temperature = float(payload.get("temperature", s.temperature))
        except (TypeError, ValueError):
            pass
        if preset.get("signin"):
            pass                                  # tokens are managed by the sign-in buttons, not the key field
        elif payload.get("clear_key"):
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
        if preset.get("signin") and not chatgpt_auth.status(self.secrets)["signed_in"]:
            self.push({"type": "toast", "kind": "warn", "text": "Settings saved. Sign in with ChatGPT before sending a request."})
        else:
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

    @staticmethod
    def _valid_conv_id(cid: str) -> bool:
        return bool(re.match(r"^[0-9]{8}-[0-9]{6}$", cid or ""))

    def _act_load_chat(self, params, payload):
        if self._busy_guard():
            return
        cid = params.get("id", "")
        if not self._valid_conv_id(cid):
            return
        self._load_conversation(cid)

    def _act_delete_chat(self, params, payload):
        cid = params.get("id", "")
        if not self._valid_conv_id(cid):
            return
        path = os.path.join(self.dirs["data"], "conversations", cid + ".json")
        if cid and os.path.exists(path):
            os.remove(path)
        self.push({"type": "history", "conversations": self._list_conversations()})

    def _act_log(self, params, payload):
        self.log("Pellaeon UI: %s" % params.get("text", ""))

    def _provider_from_payload(self, payload: Dict[str, Any]):
        preset = preset_by_id(payload.get("preset", "")) or preset_by_id("ollama")
        key = payload.get("api_key")
        if not key or key == "__unchanged__" or preset.get("signin"):
            key = self.secrets.get(preset["id"])
        options = {"token_store": self.secrets} if preset["provider"] == "chatgpt" else None
        return make_provider(preset["provider"], (payload.get("model") or preset["model"] or ""),
                             api_key=key or "", base_url=(payload.get("base_url") or preset["base_url"] or ""),
                             timeout=30, options=options)

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
        try:
            from .core.providers.ollama import OllamaProvider
        except ImportError:
            from core.providers.ollama import OllamaProvider
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

    def _init_message(self) -> Dict[str, Any]:
        return {"type": "init", "version": __version__, "presets": PRESETS,
                "settings": self._settings_message()["settings"],
                "key_masked": self._settings_message()["key_masked"],
                "key_source": self._settings_message()["key_source"],
                "chatgpt_account": self._settings_message()["chatgpt_account"],
                "configured": bool(self.settings.configured),
                "config": self._config_summary(),
                "conversations": self._list_conversations()[:20]}

    def _settings_message(self) -> Dict[str, Any]:
        s = self.settings
        key = self.secrets.get(s.preset)
        return {"type": "settings",
                "settings": {"preset": s.preset, "provider": s.provider, "model": s.model, "base_url": s.base_url,
                             "autonomy": s.autonomy, "allow_python": bool(s.allow_python), "vision": bool(s.vision), "readable_labels": bool(getattr(s, "readable_labels", True)),
                             "learn_fixes": bool(getattr(s, "learn_fixes", True)), "learned_count": len(self.learned.entries),
                             "think": bool(s.think), "temperature": s.temperature, "effort": s.effort,
                             "initiative": getattr(s, "initiative", "minimal"),
                             "allow_docking": self.docking_allowed() if hasattr(self, "docking_allowed") else None,
                             "configured": bool(s.configured)},
                "key_masked": masked(key), "key_source": self.secrets.source(s.preset),
                "chatgpt_account": chatgpt_auth.status(self.secrets)}

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

    def _build_agent(self) -> Agent:
        s = self.settings
        preset = preset_by_id(s.preset) or preset_by_id("ollama")
        options: Dict[str, Any] = {"temperature": float(s.temperature)}
        if preset["provider"] == "ollama":
            options["think"] = bool(s.think)
        if preset["provider"] in ("anthropic", "chatgpt") and s.effort:
            options["effort"] = s.effort
        if preset["provider"] == "chatgpt":
            options["token_store"] = self.secrets
        provider = make_provider(preset["provider"], s.model or preset["model"],
                                 api_key=self.secrets.get(preset["id"]),
                                 base_url=s.base_url or preset["base_url"], options=options)
        gotchas = None
        recipes = None
        suffix = "_chimera" if self.edition == "chimera" else ""
        try:
            with open(self.data_path("gotchas%s.md" % suffix), "r", encoding="utf-8") as f:
                gotchas = f.read()
        except Exception:
            pass
        try:
            recipes = load_json(self.data_path("recipes%s.json" % suffix))
        except Exception:
            pass
        directory = self.executor.knowledge.command_directory()
        # Groq and OpenRouter meter input tokens per minute, and every failure we measured on their
        # free tiers was a rate limit rather than a mistake: start compact there, which costs no accuracy.
        compact = bool((preset_by_id(getattr(s, "preset", "")) or {}).get("compact_prompt"))
        cfg = AgentConfig(autonomy=getattr(self, "_runtime_autonomy", None) or s.autonomy, allow_python=bool(s.allow_python), vision=bool(s.vision),
                          docs_per_turn=int(s.docs_per_turn), edition=self.edition, readable_labels=bool(getattr(s, "readable_labels", True)),
                          compact_prompt=compact, initiative=getattr(s, "initiative", "minimal") or "minimal")
        if preset["provider"] == "ollama":
            # keep the conversation well inside the local context window (32k by default)
            cfg.compact_after_tokens = max(8000, int(options.get("num_ctx", 32768) * 0.55))
        cb = Callbacks(on_text_delta=self._on_delta, on_tool_start=self._on_tool_start,
                       on_tool_result=self._on_tool_result, on_confirm=self._on_confirm,
                       on_ask_user=self._on_ask_user, on_status=self._on_status)
        cfg.learn_fixes = bool(getattr(s, "learn_fixes", True))
        return Agent(provider, self.executor, config=cfg, callbacks=cb,
                     directory=directory, gotchas=gotchas, recipes=recipes, learned=self.learned)

    # ---- learned fixes: refused -> accepted command pairs kept across sessions (core.learned)
    @property
    def learned(self):
        if not hasattr(self, "_learned_store"):
            data_dir = (getattr(self, "dirs", None) or {}).get("data")
            self._learned_store = LearnedFixes(os.path.join(data_dir, "learned.json") if data_dir else None,
                                               enabled=bool(getattr(self.settings, "learn_fixes", True)))
        return self._learned_store

    def _learned_message(self) -> Dict[str, Any]:
        return {"type": "learned", "entries": [{"word": e["word"], "refused": e["refused"], "accepted": e["accepted"],
                                                "error": e.get("error", ""), "count": int(e.get("count", 1))} for e in self.learned.entries],
                "enabled": self.learned.enabled}

    def _act_learned(self, params, payload):
        self.push(self._learned_message())

    def _act_learned_forget(self, params, payload):
        try:
            self.learned.forget(int(params.get("index", -1)))
        except ValueError:
            pass
        self.push(self._learned_message())

    def _act_learned_clear(self, params, payload):
        self.learned.clear()
        self.push(self._learned_message())

    SHARE_URL = "https://github.com/tggr-lab/pellaeon/discussions/new"
    SHARE_CATEGORY = "learned-fixes"

    def _act_learned_share(self, params, payload):
        """Opt-in only, from the button on the Learned page: open a prefilled GitHub discussion in the user's
        browser with the stored pairs (commands and error text, paths and URLs already replaced). Nothing is
        sent by Pellaeon itself; the user reads the whole text on GitHub and posts it, or closes the tab."""
        import urllib.parse
        import webbrowser
        data = self.learned.export()
        if not data["entries"]:
            self.push({"type": "toast", "kind": "warn", "text": "Nothing learned yet."})
            return
        body = ("Fixes Pellaeon learned on my machine (a command ChimeraX refused, and the one that ran instead). "
                "Shared so the next version can avoid the mistake up front.\n\n```json\n%s\n```\n" % json.dumps(data, indent=1))
        q = {"category": self.SHARE_CATEGORY, "title": "Learned fixes (%d)" % len(data["entries"]), "body": body}
        url = self.SHARE_URL + "?" + urllib.parse.urlencode(q)
        if len(url) > 7000:   # too long for a URL: the text goes to the clipboard, the page opens empty
            self._act_copy({"text": body}, None)
            url = self.SHARE_URL + "?" + urllib.parse.urlencode({"category": q["category"], "title": q["title"],
                                                                 "body": "(paste the copied text here)"})
        webbrowser.open(url)

    ISSUES_URL = "https://github.com/tggr-lab/pellaeon/issues/new"
    DOCS_URL = "https://tggr-lab.github.io/pellaeon/"

    def _environment(self) -> Dict[str, Any]:
        """What a bug report needs to know about this installation; the ChimeraX edition adds its own version."""
        import platform
        preset = preset_by_id(self.settings.preset) or {}
        return {"Pellaeon": __version__, "edition": getattr(self, "edition", "chimerax"),
                "provider": "%s (%s)" % (preset.get("label", self.settings.preset), self.settings.provider),
                "model": self.settings.model, "autonomy": self.settings.autonomy,
                "OS": "%s %s" % (platform.system(), platform.release()), "Python": platform.python_version()}

    def _act_copy_chat(self, params, payload):
        """The visible conversation as Markdown, to the clipboard."""
        if not self.agent or not (self.agent.conversation or self.agent.archived):
            self.push({"type": "toast", "kind": "warn", "text": "No chat yet."})
            return
        self._act_copy({"text": chat_markdown(self.agent)}, None)

    def _report_text(self) -> str:
        return problem_report(self.agent, self._environment())

    def _act_copy_report(self, params, payload):
        self._act_copy({"text": self._report_text()}, None)

    def _act_report_problem(self, params, payload):
        """Open a GitHub issue in the browser, prefilled with the report; nothing is sent until the user posts it there.
        A report too long for a URL goes to the clipboard and the issue opens with a note to paste it."""
        import webbrowser
        body = self._report_text()
        last = ""
        if self.agent and self.agent.conversation:
            last = next((m.text().strip() for m in reversed(self.agent.conversation)
                         if m.role == "user" and not m.text().startswith("(system)")), "")
        try:
            from .core.learned import _scrub
        except ImportError:
            from core.learned import _scrub
        title = ("Problem: " + _scrub(last)[:70]) if last else "Problem report"
        url = self.ISSUES_URL + "?" + urllib.parse.urlencode({"title": title, "body": body})
        if len(url) > 7000:
            self._act_copy({"text": body}, None)
            url = self.ISSUES_URL + "?" + urllib.parse.urlencode({"title": title, "body": "(paste the copied report here)"})
        webbrowser.open(url)

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
            switched = getattr(agent.provider, "switched_to", "")
            if switched and switched != self.settings.model:
                # the provider retired the configured model and named a replacement: keep it
                self.settings.model = switched
                try:
                    self.settings.save()
                except Exception:  # noqa: BLE001
                    pass
                self.push_ts({"type": "toast", "kind": "warn", "text": "The provider retired the configured model; switched to %s (saved in Settings)." % switched})
                self.push_ts({"type": "config", "config": self._config_summary()})
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
        self.call_soon(self._turn_finished)

    def _turn_finished(self):
        self._busy = False
        self.push({"type": "busy", "busy": False})
        self.push({"type": "state", "state": self._safe_state()})
        self._save_conversation()

    def _on_delta(self, text: str):
        with self._delta_lock:
            self._delta_buf.append(text)
            if self._delta_scheduled:
                return
            self._delta_scheduled = True
        self.call_soon(self._flush_deltas)

    def _flush_deltas(self):
        with self._delta_lock:
            text = "".join(self._delta_buf)
            self._delta_buf = []
            self._delta_scheduled = False
        if text:
            self.push({"type": "assistant_delta", "id": "t%d" % self._turn_id, "text": text})

    def _flush_deltas_ts(self):
        self.call_soon(self._flush_deltas)

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
            if call.name in ("compare_structures", "annotate", "table_overlay", "tidy_labels", "save_figure") and isinstance(payload, dict):
                msg["card"] = {k: v for k, v in payload.items() if k != "commands"}
                msg["results"] = payload.get("commands", [])
        self.push_ts(msg)

    def _on_confirm(self, commands: List[str], reasons: List[str], python: bool = False) -> Optional[List[str]]:
        cid = "c%d_%d" % (self._turn_id, int(time.time() * 1000))
        entry = {"event": threading.Event(), "result": None, "commands": commands, "python": python}
        self._confirms[cid] = entry
        self._flush_deltas_ts()
        self.push_ts({"type": "confirm", "id": "t%d" % self._turn_id, "confirm_id": cid,
                      "commands": commands, "reasons": reasons, "python": python})
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

    def _conv_dir(self) -> str:
        d = os.path.join(self.dirs["data"], "conversations")
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
               "summary": self.agent.summary, "journal": self.agent.journal[-2000:],
               "archived": json.loads(conversation_to_json(self.agent.archived)),
               "messages": json.loads(conversation_to_json(self.agent.conversation))}
        try:
            with open(os.path.join(self._conv_dir(), self._conv_id + ".json"), "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            self.log("Pellaeon: could not save conversation: %s" % e)

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
        self.agent.journal = list(doc.get("journal", []))
        self._conv_id = cid
        self.push({"type": "conversation", "id": cid, "title": doc.get("title", ""), "messages": self._render_messages()})

    def _render_messages(self) -> List[Dict[str, Any]]:
        """Transcript for the page: each tool call joined to its real result (status per command)."""
        msgs = self.agent.archived + self.agent.conversation
        results_by_id: Dict[str, Any] = {}
        for m in msgs:
            if m.role == "tool":
                for r in m.tool_results_list():
                    results_by_id[r.call_id] = r
        rendered: List[Dict[str, Any]] = []
        for m in msgs:
            if m.role == "user" and not m.text().startswith("(system)"):
                rendered.append({"role": "user", "text": m.text()})
            elif m.role == "assistant":
                calls = []
                for c in m.tool_calls():
                    entry: Dict[str, Any] = {"name": c.name, "args": c.args, "call_id": c.id}
                    r = results_by_id.get(c.id)
                    if r is not None:
                        entry["ok"] = not r.is_error
                        if c.name == "run_commands":
                            try:
                                res = json.loads(r.content)
                                entry["results"] = res.get("results", [])
                                entry["skipped"] = bool(res.get("skipped"))
                                entry["not_run"] = res.get("not_run", [])
                            except Exception:
                                pass
                        else:
                            card = None
                            if c.name in ("compare_structures", "annotate", "table_overlay", "tidy_labels", "save_figure"):
                                try:
                                    card = json.loads(r.content)
                                    entry["card"] = card
                                except Exception:
                                    card = None
                            entry["summary"] = _summarize_tool(c, r, card)
                    calls.append(entry)
                if m.text().strip() or calls:
                    rendered.append({"role": "assistant", "html": render_markdown(m.text()), "calls": calls})
        return rendered


def _ran_in(result) -> List[Dict[str, Any]]:
    """The commands a run_commands result says ran (after Pellaeon's rewrites), with their errors."""
    if result is None:
        return []
    try:
        payload = json.loads(result.content)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(payload, dict):
        return []
    out = [{"command": str(r.get("command", "")), "ok": bool(r.get("ok")), "error": str(r.get("error") or "")}
           for r in (payload.get("results") or []) if isinstance(r, dict)]
    if payload.get("error") and not out:
        out.append({"command": "", "ok": False, "error": str(payload["error"])})
    return out


def chat_markdown(agent, scrub: bool = False) -> str:
    """The conversation as Markdown: each request, the commands that ran for it (with errors), the reply.
    Tool calls other than run_commands are one italic line. `scrub` replaces paths and URLs."""
    try:
        from .core.learned import _scrub
    except ImportError:
        from core.learned import _scrub
    clean = _scrub if scrub else (lambda t: t)
    msgs = list(getattr(agent, "archived", []) or []) + list(agent.conversation)
    lines = ["# Pellaeon chat", ""]
    if getattr(agent, "summary", ""):
        lines += ["_Earlier part, summarized by the model:_ " + clean(agent.summary.strip()), ""]
    for i, m in enumerate(msgs):
        if m.role == "user":
            t = m.text().strip()
            if t and not t.startswith("(system)"):
                lines += ["**You:** " + clean(t), ""]
        elif m.role == "assistant":
            calls = m.tool_calls()
            if calls:
                nxt = msgs[i + 1] if i + 1 < len(msgs) and msgs[i + 1].role == "tool" else None
                by_id = {r.call_id: r for r in (nxt.tool_results_list() if nxt else [])}
                for c in calls:
                    if c.name == "run_commands":
                        ran = _ran_in(by_id.get(c.id))
                        if not ran:
                            asked = c.args.get("commands") or []
                            ran = [{"command": str(x), "ok": True, "error": ""} for x in (asked if isinstance(asked, list) else [asked])]
                        lines.append("```")
                        for r in ran:
                            cmd = clean(r["command"])
                            err = clean(r["error"]).replace("\n", " ")[:120]
                            lines.append(cmd if r["ok"] else ("%s    # failed: %s" % (cmd, err) if cmd else "# " + err))
                        lines += ["```", ""]
                    elif c.name == "ask_user":
                        lines += ["**Pellaeon asks:** " + clean(str(c.args.get("question", ""))), ""]
                    else:
                        args = ", ".join("%s=%s" % (k, v) for k, v in (c.args or {}).items())
                        lines += ["_%s(%s)_" % (c.name, clean(args)[:160]), ""]
            t = m.text().strip()
            if t:
                lines += ["**Pellaeon:** " + clean(t), ""]
    return "\n".join(lines).rstrip() + "\n"


def problem_report(agent, environment: Dict[str, Any], last_turns: int = 2) -> str:
    """A bug report a maintainer can act on: versions, provider and model, and the last turns of the chat
    (requests, commands with errors, replies), paths and URLs replaced. No keys, no file contents."""
    try:
        from .core.learned import _scrub
    except ImportError:
        from core.learned import _scrub
    env_lines = ["- %s: %s" % (k, v) for k, v in environment.items() if v not in (None, "")]
    lines = ["**What I asked for, and what happened instead**", "", "(describe it here)", "",
             "**Environment**", ""] + env_lines + [""]
    if agent is not None and agent.conversation:
        # the last N user turns and everything after them
        idx = [i for i, m in enumerate(agent.conversation) if m.role == "user" and not m.text().startswith("(system)")]
        start = idx[-last_turns] if len(idx) >= last_turns else (idx[0] if idx else 0)
        class _View:  # the tail of the conversation, through the same renderer
            archived = []
            summary = ""
            conversation = agent.conversation[start:]
        tail = chat_markdown(_View(), scrub=True)
        tail = tail.split("\n", 2)[2] if tail.startswith("# Pellaeon chat") else tail
        lines += ["**Last turns of the chat** (paths and URLs replaced; edit out anything you prefer not to share)", "", tail.rstrip(), ""]
        last_err = [j for j in (agent.journal or [])[-40:] if not j.get("ok")]
        if last_err:
            lines += ["**Commands ChimeraX refused in this chat**", "", "```"]
            lines += ["%s    # %s" % (_scrub(j.get("command", "")), _scrub(str(j.get("error") or "")).replace("\n", " ")[:160]) for j in last_err[-8:]]
            lines += ["```", ""]
    else:
        lines += ["(no chat yet in this session)", ""]
    return "\n".join(lines)


def export_lines(agent, edition: str = "chimerax") -> List[str]:
    """Replayable script from the execution journal (everything that actually ran)."""
    head = "# ChimeraX command script exported by Pellaeon" if edition != "chimera" else \
        "# Chimera command script exported by Pellaeon Classic (open it with: open script.cmd)"
    lines = [head, ""]
    for j in agent.journal:
        if j.get("ok"):
            lines.append(j["command"])
        else:
            lines.append("# failed: %s   (%s)" % (j.get("command", ""), (j.get("error") or "")[:80].replace("\n", " ")))
    return lines


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
    if name == "compare_structures":
        if isinstance(payload, dict) and not payload.get("error"):
            if payload.get("paired_residues") is None:
                return "Superposed %s onto %s (%s)" % (payload.get("compared"), payload.get("reference"), payload.get("rmsd", ""))
            return "Compared %s to %s: %d residues paired, mean shift %s A, %d moved over 2 A" % (
                payload.get("compared"), payload.get("reference"), payload.get("paired_residues", 0),
                payload.get("mean_displacement"), payload.get("residues_over_2A", 0))
        return "Comparison failed" if result is None or result.is_error else "Compared structures"
    if name == "save_figure":
        if isinstance(payload, dict) and not payload.get("error"):
            return "Saved figure bundle %s (%d files)" % (os.path.basename(payload.get("folder", "")), len(payload.get("files", [])))
        return "Figure not saved"
    if name == "explain_residue":
        if isinstance(payload, dict) and not payload.get("error"):
            return "Explained %s (chain %s)" % (payload.get("residue", ""), payload.get("chain", ""))
        return "Could not explain the residue"
    if name == "tidy_labels":
        if isinstance(payload, dict) and not payload.get("error"):
            return "Tidied labels: %d kept, %d moved, %d removed" % (payload.get("kept", 0), payload.get("moved", 0), payload.get("removed", 0))
        return "Could not tidy labels"
    if name == "table_overlay":
        if isinstance(payload, dict) and not payload.get("error"):
            return "Colored %s by '%s': %d placed, %d not found%s" % (payload.get("model", ""), payload.get("column", ""), payload.get("mapped", 0),
                                                                   payload.get("n_missing", 0), (", %d mismatches" % payload["n_mismatches"]) if payload.get("n_mismatches") else "")
        return "Table overlay failed"
    if name == "annotate":
        if isinstance(payload, dict) and not payload.get("error"):
            return "Annotated %s: %d mapped, %d unmapped%s" % (
                a.get("kind", ""), payload.get("mapped", 0), payload.get("unmapped", 0),
                (", %d reference mismatches" % payload["reference_mismatch"]) if payload.get("reference_mismatch") else "")
        return "Annotation failed" if result is None or result.is_error else "Annotated %s" % a.get("kind", "")
    if name == "run_python":
        return "Ran Python code" + ("" if not result.is_error else " (failed)")
    if name == "look_at_view":
        return "Looked at the 3D view"
    return name
