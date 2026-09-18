"""Common interface for LLM providers."""
from __future__ import annotations

import json
import threading
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
