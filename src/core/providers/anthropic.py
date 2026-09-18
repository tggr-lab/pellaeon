"""Anthropic Claude adapter over the raw Messages API (SSE streaming).

Defaults follow current API guidance: claude-opus-5, adaptive thinking,
tool_choice auto, no prefill, prompt caching on the static system prompt,
all tool results returned in one user message. Thinking blocks are kept as
opaque parts and echoed back verbatim.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional, Tuple

from ..http import request_json, stream_lines, iter_sse, HttpError
from ..schema import Message, OpaquePart, TextPart, ToolCall, ToolSpec, Usage, new_id
from .base import Provider, ProviderError, OnDelta

API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-opus-5"
_NO_ADAPTIVE = ("claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4-5", "claude-3", "claude-opus-4-1", "claude-sonnet-4-2")


class AnthropicProvider(Provider):
    name = "anthropic"
    label = "Anthropic Claude"
    supports_vision = True

    @classmethod
    def default_base_url(cls) -> str:
        return "https://api.anthropic.com"

    def _headers(self) -> Dict[str, str]:
        h = {"x-api-key": self.api_key, "anthropic-version": API_VERSION, "Content-Type": "application/json"}
        betas = []
        if self.options.get("fallbacks", True) and self.model.startswith(("claude-opus-5", "claude-fable")):
            betas.append("server-side-fallback-2026-07-01")
        if betas:
            h["anthropic-beta"] = ",".join(betas)
        return h

    def _to_messages(self, messages: List[Message]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        last_user = max((i for i, m in enumerate(messages) if m.role == "user"), default=-1)
        for i, m in enumerate(messages):
            if m.role == "user":
                text = self.user_text(m) if i == last_user else m.text()
                out.append({"role": "user", "content": [{"type": "text", "text": text or "(empty)"}]})
            elif m.role == "assistant":
                blocks: List[Dict[str, Any]] = []
                for p in m.parts:
                    if isinstance(p, OpaquePart) and p.provider == self.name:
                        blocks.append(p.data)
                    elif isinstance(p, TextPart) and p.text.strip():
                        blocks.append({"type": "text", "text": p.text})
                    elif isinstance(p, ToolCall):
                        blocks.append({"type": "tool_use", "id": p.id, "name": p.name, "input": p.args})
                if blocks:
                    out.append({"role": "assistant", "content": blocks})
            elif m.role == "tool":
                blocks = []
                for r in m.tool_results_list():
                    content: Any = [{"type": "text", "text": self.result_text(r)}]
                    if r.image_png_b64:
                        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                    "data": r.image_png_b64}})
                    blocks.append({"type": "tool_result", "tool_use_id": r.call_id,
                                   "content": content, "is_error": bool(r.is_error)})
                out.append({"role": "user", "content": blocks})
        return out

    def _body(self, system: str, messages: List[Message], tools: List[ToolSpec]) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(self.options.get("max_tokens", 16000)),
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": self._to_messages(messages),
            "stream": True,
        }
        if not self.model.startswith(_NO_ADAPTIVE):
            body["thinking"] = {"type": "adaptive"}
            effort = self.options.get("effort")
            if effort:
                body["output_config"] = {"effort": effort}
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters}
                             for t in tools]
            body["tools"][-1]["cache_control"] = {"type": "ephemeral"}
            body["tool_choice"] = {"type": "auto"}
        if "server-side-fallback-2026-07-01" in self._headers().get("anthropic-beta", ""):
            body["fallbacks"] = "default"
        return body

    def stream(self, system, messages, tools, on_delta: Optional[OnDelta] = None,
               cancel: Optional[threading.Event] = None) -> Tuple[Message, Usage]:
        if not self.api_key:
            raise ProviderError("No Anthropic API key. Add one in Settings (or set ANTHROPIC_API_KEY).")
        body = self._body(system, messages, tools)
        try:
            return self._stream_once(body, on_delta, cancel)
        except HttpError as e:
            if e.status == 400 and "fallbacks" in e.body and "fallbacks" in body:
                body.pop("fallbacks", None)
                self.options["fallbacks"] = False
                return self._stream_once(body, on_delta, cancel)
            raise ProviderError(self._explain(e))
        except ConnectionError as e:
            raise ProviderError("Could not reach %s: %s" % (self.base_url, e))

    def _stream_once(self, body, on_delta, cancel) -> Tuple[Message, Usage]:
        parts: List[Any] = []
        blocks: Dict[int, Dict[str, Any]] = {}
        usage = Usage()
        stop_reason = None
        lines = stream_lines("POST", self.base_url + "/v1/messages", headers=self._headers(),
                             body=body, timeout=self.timeout, cancel=cancel)
        for event, data in iter_sse(lines):
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            et = ev.get("type", event)
            if et == "message_start":
                u = (ev.get("message") or {}).get("usage") or {}
                usage.input_tokens = int(u.get("input_tokens", 0) or 0)
                usage.cache_read_tokens = int(u.get("cache_read_input_tokens", 0) or 0)
            elif et == "content_block_start":
                idx = ev["index"]
                cb = dict(ev.get("content_block") or {})
                blocks[idx] = {"block": cb, "text": "", "json": "", "thinking": "", "signature": ""}
                if cb.get("type") == "text" and cb.get("text"):
                    blocks[idx]["text"] = cb["text"]
                    if on_delta:
                        on_delta(cb["text"])
            elif et == "content_block_delta":
                idx = ev["index"]
                slot = blocks.setdefault(idx, {"block": {"type": "text"}, "text": "", "json": "", "thinking": "", "signature": ""})
                d = ev.get("delta") or {}
                dt = d.get("type")
                if dt == "text_delta":
                    slot["text"] += d.get("text", "")
                    if on_delta:
                        on_delta(d.get("text", ""))
                elif dt == "input_json_delta":
                    slot["json"] += d.get("partial_json", "")
                elif dt == "thinking_delta":
                    slot["thinking"] += d.get("thinking", "")
                elif dt == "signature_delta":
                    slot["signature"] += d.get("signature", "")
            elif et == "message_delta":
                d = ev.get("delta") or {}
                stop_reason = d.get("stop_reason")
                u = ev.get("usage") or {}
                usage.output_tokens = int(u.get("output_tokens", 0) or 0)
                if stop_reason == "refusal":
                    det = ev.get("stop_details") or d.get("stop_details") or {}
                    raise ProviderError("Claude declined this request (%s)." % (det.get("category") or "refusal"))
            elif et == "error":
                err = ev.get("error") or {}
                raise ProviderError("Anthropic: %s" % err.get("message", str(err)))
        for idx in sorted(blocks):
            slot = blocks[idx]
            btype = slot["block"].get("type")
            if btype == "text":
                if slot["text"]:
                    parts.append(TextPart(slot["text"]))
            elif btype == "tool_use":
                args = self.safe_json_loads(slot["json"]) if slot["json"] else dict(slot["block"].get("input") or {})
                parts.append(ToolCall(slot["block"].get("id") or new_id(), slot["block"].get("name", ""), args))
            elif btype == "thinking":
                data = {"type": "thinking", "thinking": slot["thinking"]}
                if slot["signature"]:
                    data["signature"] = slot["signature"]
                parts.append(OpaquePart(self.name, data))
            elif btype == "redacted_thinking":
                parts.append(OpaquePart(self.name, slot["block"]))
        if stop_reason == "max_tokens":
            parts.append(TextPart("\n\n(Response was cut off by the output limit.)"))
        return Message("assistant", parts), usage

    @staticmethod
    def _explain(e: HttpError) -> str:
        try:
            msg = json.loads(e.body).get("error", {}).get("message", e.body)
        except Exception:
            msg = e.body
        if e.status == 401:
            return "Anthropic rejected the API key (401). Check it in Settings."
        if e.status == 404:
            return "Anthropic: model not found. %s" % msg
        if e.status == 429:
            return "Anthropic rate limit (429). %s" % msg
        if e.status == 400:
            return "Anthropic request error: %s" % msg
        return "Anthropic HTTP %d: %s" % (e.status, str(msg)[:300])

    def list_models(self) -> List[str]:
        if not self.api_key:
            raise ProviderError("No Anthropic API key.")
        try:
            data = request_json("GET", self.base_url + "/v1/models?limit=100", headers=self._headers(), timeout=20)
        except HttpError as e:
            raise ProviderError(self._explain(e))
        except ConnectionError as e:
            raise ProviderError("Could not reach %s: %s" % (self.base_url, e))
        return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))
