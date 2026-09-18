"""Ollama adapter (local models) using the native /api/chat endpoint."""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple

from ..http import request_json, stream_lines, iter_ndjson, HttpError, Cancelled
from ..schema import Message, TextPart, ToolCall, ToolResult, ToolSpec, Usage, new_id
from .base import Provider, ProviderError, OnDelta


class OllamaProvider(Provider):
    name = "ollama"
    label = "Ollama (local)"
    needs_key = False

    @classmethod
    def default_base_url(cls) -> str:
        return "http://localhost:11434"

    # ---- conversion ----
    def _to_messages(self, system: str, messages: List[Message]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        last_user = max((i for i, m in enumerate(messages) if m.role == "user"), default=-1)
        for i, m in enumerate(messages):
            if m.role == "user":
                content = self.user_text(m) if i == last_user else m.text()
                out.append({"role": "user", "content": content})
            elif m.role == "assistant":
                d: Dict[str, Any] = {"role": "assistant", "content": m.text()}
                calls = m.tool_calls()
                if calls:
                    d["tool_calls"] = [{"function": {"name": c.name, "arguments": c.args}} for c in calls]
                out.append(d)
            elif m.role == "tool":
                for r in m.tool_results_list():
                    out.append({"role": "tool", "content": self.result_text(r), "tool_name": r.name})
        return out

    @staticmethod
    def _tools(tools: List[ToolSpec]) -> List[Dict[str, Any]]:
        return [{"type": "function", "function": t.to_dict()} for t in tools]

    # ---- API ----
    def stream(self, system, messages, tools, on_delta: Optional[OnDelta] = None,
               cancel: Optional[threading.Event] = None) -> Tuple[Message, Usage]:
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": self._to_messages(system, messages),
            "stream": True,
            "options": {
                "temperature": float(self.options.get("temperature", 0.2)),
                "num_ctx": int(self.options.get("num_ctx", 16384)),
            },
            "keep_alive": self.options.get("keep_alive", "10m"),
        }
        if tools:
            body["tools"] = self._tools(tools)
        if "think" in self.options:
            body["think"] = bool(self.options["think"])
        text_parts: List[str] = []
        calls: List[ToolCall] = []
        usage = Usage()
        try:
            for chunk in iter_ndjson(stream_lines("POST", self.base_url + "/api/chat", body=body,
                                                  timeout=self.timeout, cancel=cancel)):
                if "error" in chunk:
                    raise ProviderError("Ollama: %s" % chunk["error"])
                msg = chunk.get("message") or {}
                piece = msg.get("content") or ""
                if piece:
                    text_parts.append(piece)
                    if on_delta:
                        on_delta(piece)
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        args = self.safe_json_loads(args)
                    calls.append(ToolCall(tc.get("id") or new_id(), fn.get("name", ""), args or {}))
                if chunk.get("done"):
                    usage = Usage(int(chunk.get("prompt_eval_count", 0) or 0),
                                  int(chunk.get("eval_count", 0) or 0))
        except HttpError as e:
            if e.status == 404:
                raise ProviderError("Ollama: model '%s' not found. Pull it first (Settings > Pull model)." % self.model)
            raise ProviderError("Ollama: %s" % e.body[:300])
        except ConnectionError as e:
            raise ProviderError("Ollama is not running at %s (%s). Start Ollama and try again." % (self.base_url, e))
        parts: List[Any] = []
        text = "".join(text_parts)
        if text:
            parts.append(TextPart(text))
        parts.extend(calls)
        return Message("assistant", parts), usage

    def list_models(self) -> List[str]:
        try:
            data = request_json("GET", self.base_url + "/api/tags", timeout=10)
        except ConnectionError as e:
            raise ProviderError("Ollama is not running at %s" % self.base_url)
        return sorted(m.get("name", "") for m in data.get("models", []) if m.get("name"))

    def test(self) -> str:
        models = self.list_models()
        if not models:
            return "Ollama is running but has no models. Pull one (e.g. qwen3:8b)."
        if self.model and self.model not in models and self.model + ":latest" not in models:
            return "Ollama is running (%d models) but '%s' is not pulled yet." % (len(models), self.model)
        return "Ollama is running. %d models available." % len(models)

    def pull(self, model: str, progress=None, cancel: Optional[threading.Event] = None) -> None:
        """Pull a model, reporting (status, completed, total) via ``progress``."""
        try:
            for chunk in iter_ndjson(stream_lines("POST", self.base_url + "/api/pull",
                                                  body={"model": model, "stream": True},
                                                  timeout=3600, cancel=cancel)):
                if "error" in chunk:
                    raise ProviderError("Ollama pull failed: %s" % chunk["error"])
                if progress:
                    progress(chunk.get("status", ""), chunk.get("completed", 0), chunk.get("total", 0))
        except Cancelled:
            raise
        except ConnectionError as e:
            raise ProviderError("Ollama is not running at %s" % self.base_url)
