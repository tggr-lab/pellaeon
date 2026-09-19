"""Google Gemini adapter (generateContent REST API, SSE streaming)."""
from __future__ import annotations

import json
import re
import time
import threading
from typing import Any, Dict, List, Optional, Tuple

from ..http import request_json, stream_lines, iter_sse, HttpError
from ..schema import Message, TextPart, ToolCall, ToolSpec, Usage, new_id
from .base import Provider, ProviderError, OnDelta

DEFAULT_MODEL = "gemini-2.5-flash"


def _clean_schema(schema: Any) -> Any:
    """Gemini rejects some JSON-schema keywords; strip them recursively."""
    if isinstance(schema, dict):
        return {k: _clean_schema(v) for k, v in schema.items()
                if k not in ("default", "additionalProperties", "$schema", "examples")}
    if isinstance(schema, list):
        return [_clean_schema(v) for v in schema]
    return schema


class GeminiProvider(Provider):
    name = "gemini"
    label = "Google Gemini"
    supports_vision = True

    @classmethod
    def default_base_url(cls) -> str:
        return "https://generativelanguage.googleapis.com"

    def _headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json", "x-goog-api-key": self.api_key}

    def _to_contents(self, messages: List[Message]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        last_user = max((i for i, m in enumerate(messages) if m.role == "user"), default=-1)
        for i, m in enumerate(messages):
            if m.role == "user":
                text = self.user_text(m) if i == last_user else m.text()
                out.append({"role": "user", "parts": [{"text": text or "(empty)"}]})
            elif m.role == "assistant":
                parts: List[Dict[str, Any]] = []
                for p in m.parts:
                    if isinstance(p, TextPart) and p.text.strip():
                        parts.append({"text": p.text})
                    elif isinstance(p, ToolCall):
                        part: Dict[str, Any] = {"functionCall": {"name": p.name, "args": p.args}}
                        # Gemini 3 validates a thought signature on every function call; calls Pellaeon injected itself
                        # (tidy labels, explain residue, canned fallbacks) carry Google's documented skip marker instead
                        part["thoughtSignature"] = p.meta.get("thoughtSignature") or "skip_thought_signature_validator"
                        parts.append(part)
                if parts:
                    out.append({"role": "model", "parts": parts})
            elif m.role == "tool":
                parts = []
                for r in m.tool_results_list():
                    parts.append({"functionResponse": {"name": r.name,
                                                       "response": {"result": self.result_text(r),
                                                                    "error": bool(r.is_error)}}})
                    if r.image_png_b64:
                        parts.append({"inlineData": {"mimeType": "image/png", "data": r.image_png_b64}})
                out.append({"role": "user", "parts": parts})
        return out

    switched_to: str = ""     # set when Google retired the configured model and named its replacement

    @staticmethod
    def suggested_model(message: str) -> str:
        """Google's 'no longer available ... use models/X' error names the replacement; extract X."""
        m = re.search(r"use\s+models/([A-Za-z0-9._-]+)", message or "")
        return m.group(1) if m else ""

    @staticmethod
    def retry_delay(message: str) -> float:
        """Seconds to wait before retrying a 429, from Google's 'retry in 12.3s' / retryDelay hint (0 = none given)."""
        m = re.search(r"retry(?:Delay)?[\"']?\s*[:=]?\s*[\"']?(?:in\s+)?(\d+(?:\.\d+)?)\s*s", message or "", re.I)
        return float(m.group(1)) if m else 0.0

    def stream(self, system, messages, tools, on_delta: Optional[OnDelta] = None,
               cancel: Optional[threading.Event] = None) -> Tuple[Message, Usage]:
        waited = 0.0
        for attempt in range(4):
            try:
                return self._stream(system, messages, tools, on_delta, cancel)
            except ProviderError as e:
                text = str(e)
                new = self.suggested_model(text)
                if new and new != self.model:
                    self.model = new
                    self.switched_to = new
                    continue
                if ("429" in text or "503" in text) and attempt < 3 and waited < 90:
                    delay = self.retry_delay(text) or (15.0 * (attempt + 1))
                    delay = min(delay + 1.0, 60.0)
                    if cancel is not None and cancel.wait(delay):
                        raise
                    elif cancel is None:
                        time.sleep(delay)
                    waited += delay
                    continue
                raise
        raise ProviderError("Gemini kept refusing the request.")

    def _stream(self, system, messages, tools, on_delta: Optional[OnDelta] = None,
                cancel: Optional[threading.Event] = None) -> Tuple[Message, Usage]:
        if not self.api_key:
            raise ProviderError("No Gemini API key. Get a free one at aistudio.google.com and add it in Settings.")
        body: Dict[str, Any] = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": self._to_contents(messages),
            "generationConfig": {"temperature": float(self.options.get("temperature", 0.2))},
        }
        if tools:
            body["tools"] = [{"function_declarations": [
                {"name": t.name, "description": t.description, "parameters": _clean_schema(t.parameters)}
                for t in tools]}]
        url = "%s/v1beta/models/%s:streamGenerateContent?alt=sse" % (self.base_url, self.model)
        text_parts: List[str] = []
        calls: List[ToolCall] = []
        usage = Usage()
        try:
            lines = stream_lines("POST", url, headers=self._headers(), body=body,
                                 timeout=self.timeout, cancel=cancel)
            for _event, data in iter_sse(lines):
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if "error" in chunk:
                    raise ProviderError("Gemini: %s" % chunk["error"].get("message", chunk["error"]))
                um = chunk.get("usageMetadata") or {}
                if um:
                    usage = Usage(int(um.get("promptTokenCount", 0) or 0),
                                  int(um.get("candidatesTokenCount", 0) or 0) + int(um.get("thoughtsTokenCount", 0) or 0),
                                  int(um.get("cachedContentTokenCount", 0) or 0))
                for cand in chunk.get("candidates") or []:
                    fr = cand.get("finishReason")
                    if fr in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT"):
                        raise ProviderError("Gemini blocked the response (%s)." % fr)
                    if fr in ("MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL"):
                        raise ProviderError("Gemini produced an invalid tool call (%s); try again or switch model." % fr)
                    if fr == "MAX_TOKENS":
                        text_parts.append("\n\n(Response was cut off by the output limit.)")
                    for part in (cand.get("content") or {}).get("parts") or []:
                        if part.get("thought"):
                            continue
                        if "text" in part and part["text"]:
                            text_parts.append(part["text"])
                            if on_delta:
                                on_delta(part["text"])
                        fc = part.get("functionCall")
                        if fc:
                            meta = {"thoughtSignature": part["thoughtSignature"]} if part.get("thoughtSignature") else {}
                            calls.append(ToolCall(new_id(), fc.get("name", ""), dict(fc.get("args") or {}), meta))
        except HttpError as e:
            raise ProviderError(self._explain(e))
        except ConnectionError as e:
            raise ProviderError("Could not reach %s: %s" % (self.base_url, e))
        parts: List[Any] = []
        text = "".join(text_parts)
        if text:
            parts.append(TextPart(text))
        parts.extend(calls)
        return Message("assistant", parts), usage

    @staticmethod
    def _explain(e: HttpError) -> str:
        try:
            msg = json.loads(e.body).get("error", {}).get("message", e.body)
        except Exception:
            msg = e.body
        if e.status in (401, 403):
            return "Gemini rejected the API key (%d). Check it in Settings." % e.status
        if e.status == 404:
            return "Gemini: model not found. %s" % msg
        if e.status == 429:
            return "Gemini rate limit (429). The free tier allows a limited number of requests per minute. %s" % msg
        return "Gemini HTTP %d: %s" % (e.status, str(msg)[:300])

    def list_models(self) -> List[str]:
        if not self.api_key:
            raise ProviderError("No Gemini API key.")
        try:
            data = request_json("GET", self.base_url + "/v1beta/models?pageSize=200", headers=self._headers(), timeout=20)
        except HttpError as e:
            raise ProviderError(self._explain(e))
        except ConnectionError as e:
            raise ProviderError("Could not reach %s: %s" % (self.base_url, e))
        names = []
        for m in data.get("models", []):
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                names.append(m.get("name", "").replace("models/", ""))
        return sorted(names)
