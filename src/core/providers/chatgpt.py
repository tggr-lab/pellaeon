"""ChatGPT subscription adapter: the Codex backend's Responses endpoint with a Sign-in-with-ChatGPT token.

Same wire format as the Codex CLI (openai/codex, codex-rs/codex-api): POST
{base}/responses with store=false, stream=true, the whole conversation in
`input`, function tools, and encrypted reasoning items echoed back verbatim.
The api_key slot carries the token JSON from chatgpt_auth; options may carry
"token_store" (a SecretStore) so refreshed tokens are kept.
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
import uuid
from typing import Any, Dict, List, Optional, Tuple

from ..http import request_json, stream_lines, iter_sse, HttpError
from ..schema import Message, OpaquePart, TextPart, ToolCall, ToolSpec, Usage, new_id
from .base import Provider, ProviderError, OnDelta
from . import chatgpt_auth as auth

DEFAULT_MODEL = "gpt-6-luna"
# The model catalog is filtered by client version: with 0.0.0 (or 0.153) the gpt-6 Sol and Luna models are not
# listed at all. Keep this at a current Codex CLI release (github.com/openai/codex/releases).
CLIENT_VERSION = "0.157.1"
USER_AGENT = "codex_cli_rs/%s (Pellaeon; ChimeraX)" % CLIENT_VERSION     # the backend accepts first-party originators only
LITE_HEADER = "x-openai-internal-codex-responses-lite"
# models the catalog marks use_responses_lite; used only when the catalog cannot be read
_LITE_PREFIXES = ("gpt-6", "gpt-5.6")


class ChatGPTProvider(Provider):
    name = "chatgpt"
    label = "ChatGPT (subscription)"
    supports_vision = True
    needs_key = False

    def __init__(self, model: str, api_key: str = "", base_url: str = "", timeout: float = 180.0,
                 options: Optional[Dict[str, Any]] = None):
        super().__init__(model or DEFAULT_MODEL, api_key, base_url, timeout, options)
        self.store = self.options.get("token_store")
        self.tokens: Dict[str, Any] = {}
        if self.store is not None:
            self.tokens = auth.load_tokens(self.store)
        if not self.tokens and api_key:
            try:
                d = json.loads(api_key)
                self.tokens = d if isinstance(d, dict) and d.get("access_token") else {}
            except Exception:
                self.tokens = {}
        self.session_id = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._catalog: Optional[Dict[str, Dict[str, Any]]] = None   # slug -> the catalog entry
        self._raw_calls: Dict[str, Dict[str, Any]] = {}            # call_id -> function_call item as received

    @classmethod
    def default_base_url(cls) -> str:
        return "https://chatgpt.com/backend-api/codex"

    # ---- auth ----
    def _ensure_token(self, force_refresh: bool = False) -> None:
        with self._lock:
            if not self.tokens:
                raise ProviderError("Not signed in to ChatGPT. Open Settings and press Sign in with ChatGPT.")
            if force_refresh or auth.needs_refresh(self.tokens):
                try:
                    fresh = auth.refresh_tokens(self.tokens, http=request_json)
                except auth.AuthError as e:
                    raise ProviderError(str(e))
                if self.store is not None and not auth.load_tokens(self.store):
                    # Sign out was pressed while the refresh was in flight: the store is empty and stays so
                    self.tokens = {}
                    raise ProviderError("Signed out of ChatGPT. Open Settings and press Sign in with ChatGPT.")
                self.tokens = fresh
                try:
                    auth.save_tokens(self.store, self.tokens)
                except Exception:   # the refreshed token still works for this session
                    pass

    def _headers(self) -> Dict[str, str]:
        h = {
            "Authorization": "Bearer " + self.tokens.get("access_token", ""),
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "originator": auth.ORIGINATOR,
            "User-Agent": USER_AGENT,
            "OpenAI-Beta": "responses=experimental",
            "session_id": self.session_id,
        }
        if self.tokens.get("account_id"):
            h["ChatGPT-Account-ID"] = self.tokens["account_id"]
        return h

    # ---- the model catalog ----
    def _fetch_catalog(self) -> Dict[str, Dict[str, Any]]:
        headers = self._headers()
        headers.pop("Accept", None)
        data = request_json("GET", self.base_url + "/models?client_version=" + CLIENT_VERSION, headers=headers, timeout=20)
        models = data.get("models") if isinstance(data, dict) else None
        self._catalog = {m["slug"]: m for m in (models or []) if isinstance(m, dict) and m.get("slug")}
        return self._catalog

    def _model_info(self) -> Dict[str, Any]:
        if self._catalog is None:
            try:
                self._fetch_catalog()
            except Exception:   # noqa: BLE001  (the request itself will report a real problem)
                self._catalog = {}
        return (self._catalog or {}).get(self.model) or {}

    def _is_lite(self) -> bool:
        """Responses Lite (Codex's request form for the gpt-6 and gpt-5.6 models): instructions and tools travel
        as the first input items instead of top-level fields."""
        info = self._model_info()
        if "use_responses_lite" in info:
            return bool(info["use_responses_lite"])
        return self.model.startswith(_LITE_PREFIXES)

    # ---- request body ----
    def _to_input(self, messages: List[Message], lite: bool = False) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        last_user = max((i for i, m in enumerate(messages) if m.role == "user"), default=-1)
        for i, m in enumerate(messages):
            if m.role == "user":
                text = self.user_text(m) if i == last_user else m.text()
                out.append({"type": "message", "role": "user", "content": [{"type": "input_text", "text": text or "(empty)"}]})
            elif m.role == "assistant":
                for p in m.parts:
                    if isinstance(p, OpaquePart) and p.provider == self.name:
                        out.append(p.data)
                    elif isinstance(p, TextPart) and p.text.strip():
                        out.append({"type": "message", "role": "assistant",
                                    "content": [{"type": "output_text", "text": p.text}]})
                    elif isinstance(p, ToolCall):
                        raw = self._raw_calls.get(p.id)
                        if raw:          # echo the call as it came (namespace, encrypted arguments), without its id
                            out.append(raw)
                        else:
                            item = {"type": "function_call", "call_id": p.id, "name": p.name, "arguments": json.dumps(p.args)}
                            if lite:
                                item["namespace"] = "functions"
                            out.append(item)
            elif m.role == "tool":
                images = []
                for r in m.tool_results_list():
                    out.append({"type": "function_call_output", "call_id": r.call_id, "output": self.result_text(r)})
                    if r.image_png_b64:
                        images.append((r.name, r.image_png_b64))
                for name, b64 in images:
                    out.append({"type": "message", "role": "user", "content": [
                        {"type": "input_text", "text": "Screenshot from tool %s:" % name},
                        {"type": "input_image", "image_url": "data:image/png;base64," + b64}]})
        return out

    def _body(self, system: str, messages: List[Message], tools: List[ToolSpec], lite: Optional[bool] = None) -> Dict[str, Any]:
        lite = self._is_lite() if lite is None else lite
        info = self._model_info() if self._catalog is not None else {}
        functions = [{"type": "function", "name": t.name, "description": t.description,
                      "parameters": t.parameters, "strict": False} for t in tools or []]
        body: Dict[str, Any] = {
            "model": self.model,
            "input": self._to_input(messages, lite),
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "reasoning": {"effort": self.options.get("effort") or info.get("default_reasoning_level") or "medium"},
            "store": False,
            "stream": True,
            "include": ["reasoning.encrypted_content"],
            "text": {"verbosity": info.get("default_verbosity") or "medium"},
            "prompt_cache_key": self.session_id,
        }
        if lite:
            # as codex-rs/core/src/client.rs build_responses_request: the tools (one "functions" namespace) and the
            # system prompt are the first input items, with ids stable within the session; no instructions/tools fields
            ns = uuid.uuid5(uuid.NAMESPACE_OID, self.session_id)
            tools_json = [{"type": "namespace", "name": "functions", "description": "", "tools": functions}] if functions else []
            prefix: List[Dict[str, Any]] = [{"type": "additional_tools", "role": "developer", "tools": tools_json,
                                             "id": "at_%s" % uuid.uuid5(ns, json.dumps(tools_json, sort_keys=True))}]
            if system:
                prefix.append({"type": "message", "role": "developer", "id": "msg_%s" % uuid.uuid5(ns, system),
                               "content": [{"type": "input_text", "text": system}]})
            body["input"] = prefix + body["input"]
            body["reasoning"]["context"] = "all_turns"
        else:
            body["instructions"] = system
            body["reasoning"]["summary"] = "auto"
            if functions:
                body["tools"] = functions
        return body

    # ---- streaming ----
    def stream(self, system, messages, tools, on_delta: Optional[OnDelta] = None,
               cancel: Optional[threading.Event] = None) -> Tuple[Message, Usage]:
        self._ensure_token()
        lite = self._is_lite()
        body = self._body(system, messages, tools, lite)

        def once():
            seen = {"text": False}

            def tap(piece):
                seen["text"] = True
                if on_delta:
                    on_delta(piece)

            try:
                return self._stream_once(body, tap, cancel, lite)
            except HttpError as e:
                if e.status == 401 and not seen["text"]:
                    self._ensure_token(force_refresh=True)          # expired mid-way: refresh once, retry
                    try:
                        return self._stream_once(body, tap, cancel, lite)
                    except HttpError as e2:
                        raise ProviderError(self._explain(e2))
                raise ProviderError(self._explain(e))
            except ProviderError as e:
                e.streamed = seen["text"]
                raise

        return self._retrying(once, cancel)

    def _stream_once(self, body, on_delta, cancel, lite: bool = False) -> Tuple[Message, Usage]:
        parts: List[Any] = []
        deltas: List[str] = []
        usage = Usage()
        completed = False
        try:
            headers = self._headers()
            if lite:
                headers[LITE_HEADER] = "true"
            lines = stream_lines("POST", self.base_url + "/responses", headers=headers,
                                 body=body, timeout=self.timeout, cancel=cancel)
            for _event, data in iter_sse(lines):
                try:
                    ev = json.loads(data)
                except json.JSONDecodeError:
                    continue
                et = ev.get("type", "")
                if et == "response.output_text.delta":
                    piece = ev.get("delta") or ""
                    if piece:
                        deltas.append(piece)
                        on_delta(piece)
                elif et == "response.output_item.done":
                    part = self._item_to_part(ev.get("item") or {})
                    if part is not None:
                        parts.append(part)
                elif et == "response.completed":
                    completed = True
                    u = (ev.get("response") or {}).get("usage") or {}
                    usage = Usage(int(u.get("input_tokens", 0) or 0), int(u.get("output_tokens", 0) or 0),
                                  int(((u.get("input_tokens_details") or {}).get("cached_tokens", 0)) or 0))
                elif et == "response.failed":
                    err = (ev.get("response") or {}).get("error") or {}
                    raise ProviderError(self._explain_error(err))
                elif et == "response.incomplete":
                    det = (ev.get("response") or {}).get("incomplete_details") or {}
                    parts.append(TextPart("\n\n(Response was cut off: %s.)" % (det.get("reason") or "unknown")))
                elif et == "error":
                    err = ev.get("error") or ev
                    raise ProviderError(self._explain_error(err if isinstance(err, dict) else {"message": str(err)}))
        except ConnectionError as e:
            raise ProviderError("Could not reach %s: %s" % (self.base_url, e))
        if not any(isinstance(p, TextPart) for p in parts) and deltas:
            parts.insert(0, TextPart("".join(deltas)))          # the stream ended before the message item closed
        if not completed and not parts:
            raise ProviderError("ChatGPT closed the stream without a reply.")
        if not completed and any(isinstance(p, ToolCall) for p in parts):
            # the reply was cut off after a function call: the rest of the plan is unknown, so nothing runs
            raise ProviderError("The connection to ChatGPT dropped before the reply was complete; nothing was run. "
                                "Send the request again.")
        return Message("assistant", parts), usage

    def _item_to_part(self, item: Dict[str, Any]):
        t = item.get("type")
        if t == "message":
            text = "".join(c.get("text", "") for c in item.get("content") or [] if c.get("type") == "output_text")
            return TextPart(text) if text else None
        if t == "function_call":
            call = ToolCall(item.get("call_id") or new_id(), item.get("name", ""),
                            self.safe_json_loads(item.get("arguments", "")))
            raw = {k: v for k, v in item.items() if k not in ("id", "status")}
            raw["call_id"] = call.id
            self._raw_calls[call.id] = raw
            return call
        if t == "reasoning":
            data = {"type": "reasoning", "summary": item.get("summary") or []}
            if item.get("encrypted_content"):
                data["encrypted_content"] = item["encrypted_content"]
            elif not data["summary"]:
                return None
            return OpaquePart(self.name, data)       # stateless backend: echoed back on the next call, without its id
        return None

    # ---- errors ----
    _USAGE_LIMIT = ("usage_limit_reached", "usage_not_included", "insufficient_quota")

    @staticmethod
    def _reset_text(resets_at: Any) -> str:
        try:
            when = _dt.datetime.fromtimestamp(float(resets_at)).astimezone()
        except (TypeError, ValueError, OverflowError, OSError):
            return ""
        return " It resets at %s." % when.strftime("%H:%M on %d %b")

    def _explain_error(self, err: Dict[str, Any]) -> str:
        code = str(err.get("code") or err.get("type") or "")
        msg = str(err.get("message") or "")
        if code == "usage_limit_reached":
            plan = auth.plan_label(str(err.get("plan_type") or self.tokens.get("plan", "")))
            return ("Your ChatGPT%s usage limit is reached, so ChatGPT will not answer until it resets.%s "
                    "Pick another provider in Settings meanwhile." % ((" " + plan) if plan else "", self._reset_text(err.get("resets_at"))))
        if code == "usage_not_included":
            return "This model is not included in your ChatGPT plan. Pick another model (Refresh list in Settings)."
        if code in ("insufficient_quota", "credit_balance_exhausted"):
            return "ChatGPT reports no remaining quota for this account. %s" % msg
        if code == "context_length_exceeded":
            return "The conversation no longer fits the model's context window. Start a new chat."
        if code in ("rate_limit_exceeded", "slow_down", "server_is_overloaded"):
            return "Rate limited: ChatGPT is asking us to slow down. %s" % msg
        return "ChatGPT: %s" % (msg or code or "request failed")

    def _explain(self, e: HttpError) -> str:
        try:
            err = json.loads(e.body).get("error") or {}
        except Exception:
            err = {}
        if not isinstance(err, dict):
            err = {"message": str(err)}
        if e.status == 401:
            return "ChatGPT rejected the sign-in (401). Sign in again in Settings."
        if e.status == 403:
            return "ChatGPT refused the request (403): %s" % (err.get("message") or e.body[:200])
        if e.status == 404 and not err.get("type") in self._USAGE_LIMIT:
            return "Model or endpoint not found (404): %s" % (err.get("message") or e.body[:200])
        if e.status == 429 or err.get("type") in self._USAGE_LIMIT or err.get("code") in self._USAGE_LIMIT:
            if err:
                return self._explain_error(err)
            return "Rate limited: ChatGPT is asking us to slow down. %s" % e.body[:200]
        if e.status == 400:
            return "ChatGPT request error: %s" % (err.get("message") or e.body[:300])
        return "ChatGPT HTTP %d: %s" % (e.status, (err.get("message") or e.body)[:300])

    @staticmethod
    def is_daily_limit(message: str) -> bool:
        """A plan's usage window is hours or days: waiting a minute is pointless."""
        return ("usage limit is reached" in (message or "") or "not included in your ChatGPT plan" in (message or "")
                or Provider.is_daily_limit(message))

    # ---- models ----
    def list_models(self) -> List[str]:
        self._ensure_token()
        headers = self._headers()
        headers.pop("Accept", None)
        try:
            catalog = self._fetch_catalog()
        except HttpError as e:
            raise ProviderError(self._explain(e))
        except ConnectionError as e:
            raise ProviderError("Could not reach %s: %s" % (self.base_url, e))
        listed = list(catalog.values())
        shown = [m for m in listed if str(m.get("visibility", "list")).lower() == "list"] or listed
        # priority 1 is the catalog's top model (Codex lists them in ascending order)
        shown.sort(key=lambda m: (int(m.get("priority", 999) or 999), m["slug"]))
        return [m["slug"] for m in shown]

    def test(self) -> str:
        models = self.list_models()
        plan = auth.plan_label(self.tokens.get("plan", ""))
        head = "Signed in to ChatGPT%s." % ((" (%s plan)" % plan) if plan else "")
        return head + (" %d models available." % len(models) if models else "")
