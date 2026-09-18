"""OpenAI-compatible chat completions adapter.

Covers OpenAI itself and every server that speaks the same protocol:
OpenRouter, Groq, Mistral, LM Studio, vLLM, llama.cpp, Cerebras, Together...
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from ..http import request_json, stream_lines, iter_sse, HttpError
from ..schema import Message, TextPart, ToolCall, ToolSpec, Usage, new_id
from .base import Provider, ProviderError, OnDelta


class OpenAICompatProvider(Provider):
    name = "openai"
    label = "OpenAI / compatible"
    supports_vision = True

    @classmethod
    def default_base_url(cls) -> str:
        return "https://api.openai.com/v1"

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = "Bearer " + self.api_key
        if "openrouter" in self.base_url:
            h["HTTP-Referer"] = "https://github.com/pellaeon-chimerax/pellaeon"
            h["X-Title"] = "Pellaeon for ChimeraX"
        return h

    def _to_messages(self, system: str, messages: List[Message]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        last_user = max((i for i, m in enumerate(messages) if m.role == "user"), default=-1)
        for i, m in enumerate(messages):
            if m.role == "user":
                out.append({"role": "user", "content": self.user_text(m) if i == last_user else m.text()})
            elif m.role == "assistant":
                calls = m.tool_calls()
                d: Dict[str, Any] = {"role": "assistant", "content": m.text() if (m.text() or not calls) else None}
                if calls:
                    d["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                                       for c in calls]
                out.append(d)
            elif m.role == "tool":
                images = []
                for r in m.tool_results_list():
                    out.append({"role": "tool", "tool_call_id": r.call_id, "content": self.result_text(r)})
                    if r.image_png_b64:
                        images.append((r.name, r.image_png_b64))
                for name, b64 in images:  # images go after ALL tool messages (tool_calls -> tool adjacency)
                    out.append({"role": "user", "content": [
                        {"type": "text", "text": "Screenshot from tool %s:" % name},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]})
        return out

    def stream(self, system, messages, tools, on_delta: Optional[OnDelta] = None,
               cancel: Optional[threading.Event] = None) -> Tuple[Message, Usage]:
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": self._to_messages(system, messages),
            "stream": True,
        }
        if not re.match(r"^(gpt-5|o\d)", self.model):  # reasoning models reject temperature
            body["temperature"] = float(self.options.get("temperature", 0.2))
        if self.options.get("include_usage", True):
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = [{"type": "function", "function": t.to_dict()} for t in tools]
            body["tool_choice"] = "auto"
        try:
            return self._stream_once(body, on_delta, cancel)
        except HttpError as e:
            if e.status == 400 and "stream_options" in e.body and "stream_options" in body:
                del body["stream_options"]
                return self._stream_once(body, on_delta, cancel)
            raise ProviderError(self._explain(e))

    def _stream_once(self, body, on_delta, cancel) -> Tuple[Message, Usage]:
        text_parts: List[str] = []
        pending: Dict[int, Dict[str, Any]] = {}
        usage = Usage()
        try:
            lines = stream_lines("POST", self.base_url + "/chat/completions", headers=self._headers(),
                                 body=body, timeout=self.timeout, cancel=cancel)
            for _event, data in iter_sse(lines):
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if "error" in chunk and chunk["error"]:
                    err = chunk["error"]
                    raise ProviderError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
                if chunk.get("usage"):
                    u = chunk["usage"]
                    usage = Usage(int(u.get("prompt_tokens", 0) or 0), int(u.get("completion_tokens", 0) or 0),
                                  int(((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)) or 0))
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        text_parts.append(piece)
                        if on_delta:
                            on_delta(piece)
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = pending.setdefault(idx, {"id": "", "name": "", "args": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["args"] += fn["arguments"]
        except ConnectionError as e:
            raise ProviderError("Could not reach %s: %s" % (self.base_url, e))
        parts: List[Any] = []
        text = "".join(text_parts)
        if text:
            parts.append(TextPart(text))
        for idx in sorted(pending):
            slot = pending[idx]
            parts.append(ToolCall(slot["id"] or new_id(), slot["name"], self.safe_json_loads(slot["args"])))
        return Message("assistant", parts), usage

    def _explain(self, e: HttpError) -> str:
        if e.status == 401:
            return "The API key was rejected (401). Check the key in Settings."
        if e.status == 404:
            return "Model or endpoint not found (404): %s" % e.body[:200]
        if e.status == 429:
            return "Rate limited (429). Wait a moment or switch model/provider. %s" % e.body[:200]
        return "HTTP %d: %s" % (e.status, e.body[:300])

    def list_models(self) -> List[str]:
        try:
            data = request_json("GET", self.base_url + "/models", headers=self._headers(), timeout=20)
        except HttpError as e:
            raise ProviderError(self._explain(e))
        except ConnectionError as e:
            raise ProviderError("Could not reach %s: %s" % (self.base_url, e))
        return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))
