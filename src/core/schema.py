"""Provider-neutral message schema.

A conversation is a list of ``Message`` objects. Each message holds ``parts``:
plain text, tool calls made by the assistant, tool results supplied back, or
opaque provider-specific blocks (for example Anthropic thinking blocks) that
must be echoed back verbatim to the provider that produced them.

Everything here is a plain dataclass so it can be serialised to JSON for
conversation persistence and used in tests without ChimeraX or any SDK.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Union


@dataclass
class TextPart:
    text: str
    kind: str = "text"


@dataclass
class ToolCall:
    id: str
    name: str
    args: Dict[str, Any] = field(default_factory=dict)
    kind: str = "tool_call"


@dataclass
class ToolResult:
    call_id: str
    name: str
    content: str
    is_error: bool = False
    image_png_b64: Optional[str] = None
    kind: str = "tool_result"


@dataclass
class OpaquePart:
    """Provider-specific block echoed back only to the same provider."""
    provider: str
    data: Dict[str, Any]
    kind: str = "opaque"


Part = Union[TextPart, ToolCall, ToolResult, OpaquePart]


@dataclass
class Message:
    role: str  # "user" | "assistant" | "tool"
    parts: List[Part] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)  # e.g. {"context": "..."}
    ts: float = field(default_factory=time.time)

    # ----- convenience -----
    @classmethod
    def user(cls, text: str, context: Optional[str] = None) -> "Message":
        m = cls("user", [TextPart(text)])
        if context:
            m.meta["context"] = context
        return m

    @classmethod
    def assistant(cls, text: str) -> "Message":
        return cls("assistant", [TextPart(text)])

    @classmethod
    def tool_results(cls, results: List[ToolResult]) -> "Message":
        return cls("tool", list(results))

    def text(self) -> str:
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    def tool_calls(self) -> List[ToolCall]:
        return [p for p in self.parts if isinstance(p, ToolCall)]

    def tool_results_list(self) -> List[ToolResult]:
        return [p for p in self.parts if isinstance(p, ToolResult)]

    def has_tool_calls(self) -> bool:
        return any(isinstance(p, ToolCall) for p in self.parts)

    # ----- serialisation -----
    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "parts": [asdict(p) for p in self.parts],
            "meta": self.meta,
            "ts": self.ts,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Message":
        parts: List[Part] = []
        for p in d.get("parts", []):
            kind = p.get("kind")
            q = {k: v for k, v in p.items() if k != "kind"}
            if kind == "text":
                parts.append(TextPart(**q))
            elif kind == "tool_call":
                parts.append(ToolCall(**q))
            elif kind == "tool_result":
                parts.append(ToolResult(**q))
            elif kind == "opaque":
                parts.append(OpaquePart(**q))
        return cls(d["role"], parts, dict(d.get("meta", {})), d.get("ts", time.time()))


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON schema (object)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens


def new_id(prefix: str = "call") -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:12])


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (about 4 characters per token)."""
    return max(1, len(text) // 4)


def conversation_to_json(messages: List[Message]) -> str:
    return json.dumps([m.to_dict() for m in messages], ensure_ascii=False, indent=1)


def conversation_from_json(s: str) -> List[Message]:
    return [Message.from_dict(d) for d in json.loads(s)]
