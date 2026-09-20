"""Common interface for LLM providers."""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..schema import Message, ToolResult, ToolSpec, Usage

OnDelta = Callable[[str], None]


class ProviderError(Exception):
    """A provider failed in a way the user should see (bad key, model missing...)."""


class Provider:
    name = "base"
    label = "Base"
    supports_tools = True
    supports_vision = False
    needs_key = True

    def __init__(self, model: str, api_key: str = "", base_url: str = "",
                 timeout: float = 180.0, options: Optional[Dict[str, Any]] = None):
        self.model = model
        self.api_key = api_key or ""
        self.base_url = (base_url or self.default_base_url()).rstrip("/")
        self.timeout = timeout
        self.options = options or {}

    @classmethod
    def default_base_url(cls) -> str:
        return ""

    # ---- to implement ----
    def stream(self, system: str, messages: List[Message], tools: List[ToolSpec],
               on_delta: Optional[OnDelta] = None,
               cancel: Optional[threading.Event] = None) -> Tuple[Message, Usage]:
        raise NotImplementedError

    def list_models(self) -> List[str]:
        return []

    def test(self) -> str:
        """Cheap connectivity check; returns a human-readable status line."""
        models = self.list_models()
        if models:
            return "Connected. %d models available." % len(models)
        return "Connected."

    # ---- rate limits (free tiers hit them constantly) ----
    max_retry_wait = 90.0      # total seconds a single request may spend waiting out limits
    retry_attempts = 4

    _RETRY_RE = re.compile(
        r"(?:try again|retry(?:Delay)?|retry[- ]after)\D{0,24}?"
        r"(\d+(?:\.\d+)?)\s*(ms|m(?:in[a-z]*)?|s(?:ec[a-z]*)?)?", re.I)
    _SECONDS_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*s", re.I)

    @classmethod
    def parse_retry_delay(cls, message: str) -> float:
        """Seconds a provider asked us to wait ('try again in 1m2.3s', '"retryDelay": "12s"'); 0 if it said nothing."""
        m = cls._RETRY_RE.search(message or "")
        if not m:
            return 0.0
        value, unit = float(m.group(1)), (m.group(2) or "s").lower()
        if unit == "ms":
            return value / 1000.0
        if unit.startswith("m"):
            tail = cls._SECONDS_RE.match(message[m.end():])   # Groq writes "1m2.3s"
            return value * 60.0 + (float(tail.group(1)) if tail else 0.0)
        return value

    @staticmethod
    def is_rate_limited(message: str) -> bool:
        text = message or ""
        return ("429" in text or "503" in text or "529" in text
                or "rate limit" in text.lower() or "overloaded" in text.lower())

    def _retrying(self, call: Callable[[], Any], cancel: Optional[threading.Event] = None,
                  on_error: Optional[Callable[[str, int], bool]] = None) -> Any:
        """Run `call`, waiting out rate limits (and anything `on_error` says to retry)."""
        waited = 0.0
        last = None
        for attempt in range(self.retry_attempts):
            try:
                return call()
            except ProviderError as e:
                last = e
                text = str(e)
                if on_error is not None and on_error(text, attempt):
                    continue
                if attempt + 1 >= self.retry_attempts or waited >= self.max_retry_wait or not self.is_rate_limited(text):
                    raise
                delay = min((self.parse_retry_delay(text) or 15.0 * (attempt + 1)) + 1.0, 60.0)
                if cancel is not None:
                    if cancel.wait(delay):
                        raise
                else:
                    time.sleep(delay)
                waited += delay
        raise last if last is not None else ProviderError("The provider kept refusing the request.")

    # ---- helpers shared by adapters ----
    @staticmethod
    def user_text(m: Message) -> str:
        """User text plus the per-turn context block, if any."""
        ctx = m.meta.get("context")
        text = m.text()
        if ctx:
            return "%s\n\n%s" % (ctx, text)
        return text

    @staticmethod
    def result_text(r: ToolResult) -> str:
        return r.content if r.content else ("(error)" if r.is_error else "(no output)")

    @staticmethod
    def safe_json_loads(s: str) -> Dict[str, Any]:
        s = (s or "").strip()
        if not s:
            return {}
        try:
            v = json.loads(s)
            return v if isinstance(v, dict) else {"value": v}
        except json.JSONDecodeError:
            # try to salvage a truncated object
            depth = s.count("{") - s.count("}")
            try:
                v = json.loads(s + "}" * max(0, depth))
                return v if isinstance(v, dict) else {"value": v}
            except json.JSONDecodeError:
                return {"_raw": s}
