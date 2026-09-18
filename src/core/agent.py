"""The agent loop: user text -> model -> tool calls -> executor -> ... -> reply.

Runs in a worker thread. Everything that must happen on ChimeraX's main
thread is behind the ``Executor`` and the UI callbacks; the agent itself
never imports ChimeraX.
"""
from __future__ import annotations

import json
import re
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import prompt as prompt_mod
from .fixups import suggest
from .safety import AUTONOMY_AUTO, first_word, needs_confirmation, split_commands
from .schema import (Message, TextPart, ToolCall, ToolResult, Usage, estimate_tokens)
from .tools import tool_specs
from .providers.base import Provider, ProviderError

MAX_TOOL_ROUNDS = 12
MAX_CONSECUTIVE_ERRORS = 3
COMPACT_AFTER_MESSAGES = 80
COMPACT_AFTER_TOKENS = 60000


class TurnCancelled(Exception):
    pass


@dataclass
class AgentConfig:
    autonomy: str = AUTONOMY_AUTO
    allow_python: bool = False
    vision: bool = False
    docs_per_turn: int = 6
    max_tool_rounds: int = MAX_TOOL_ROUNDS
    compact_after_messages: int = COMPACT_AFTER_MESSAGES
    compact_after_tokens: int = COMPACT_AFTER_TOKENS
    nudge_on_no_action: bool = True   # re-prompt when an action request got words but no tool call


NUDGE = ("(system) You did not call any tool, so NOTHING changed in ChimeraX. If the user asked you to change "
         "something, do it now with run_commands and only then describe the result. Do not claim it was already done. "
         "If it was just a question or a remark, answer it briefly.")

NUDGE_AFTER_ERROR = ("(system) The last command failed and you stopped without running a corrected one, so the user's "
                     "request is NOT done. Run the corrected commands now with run_commands (use the suggestion/usage in "
                     "the error result). Do not describe commands in text without running them. Only if ChimeraX truly "
                     "cannot do it, say so in one sentence.")

NUDGE_ONLY = ("(system) The user said ONLY, but nothing was hidden. Hide everything else first, then show the part: "
              "e.g. run_commands([\"hide #1 target acs\", \"cartoon #1/B\", \"show #1/B atoms\"]) for 'only chain B', "
              "or [\"cartoon #1\", \"hide #1 atoms\"] for 'only the cartoon'. Do it now.")
_ONLY_RE = re.compile(r"\bonly\b|\bjust (the |chain )", re.I)
_HIDE_RE = re.compile(r"^\s*(hide|~|cartoon hide|surface hide|close|delete)", re.I)

_COMPLAINT_RE = re.compile(
    r"\b(did ?n[o']?t|does ?n[o']?t|not work(ing)?|nothing (happened|changed)|no(t)? (you|it) (did|does)|you did not|"
    r"that'?s (wrong|not it)|wrong|not what i|still the same|no change|nope|it'?s not|isn'?t|aren'?t|are they though|"
    r"are they thou|try (again|something else|a different)|didn'?t work)\b")


def looks_like_complaint(text: str) -> bool:
    return bool(_COMPLAINT_RE.search((text or "").strip().lower()))

_QUESTION_STARTS = ("what", "why", "how", "which", "where", "when", "who", "is", "are", "do", "does", "did", "can",
                    "could", "should", "would", "explain", "tell", "describe", "list", "summarize", "summarise")
_SMALL_TALK = {"thanks", "thank", "thx", "ok", "okay", "cool", "nice", "great", "hi", "hello", "hey", "bye", "good", "perfect"}


def looks_like_action_request(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t or t.startswith("(system)"):
        return False
    words = re.findall(r"[a-z']+", t)
    if not words:
        return False
    if len(words) <= 2 and all(w in _SMALL_TALK for w in words):
        return False
    if t.endswith("?") or words[0] in _QUESTION_STARTS:
        return False
    return True


@dataclass
class Callbacks:
    """UI hooks. All are optional; all are called from the worker thread."""
    on_text_delta: Optional[Callable[[str], None]] = None
    on_tool_start: Optional[Callable[[ToolCall], None]] = None
    on_tool_result: Optional[Callable[[ToolCall, ToolResult, Any], None]] = None
    # confirm(commands, reasons) -> approved commands list, or None if user skipped
    on_confirm: Optional[Callable[[List[str], List[str]], Optional[List[str]]]] = None
    on_ask_user: Optional[Callable[[str, List[str]], None]] = None
    on_status: Optional[Callable[[str], None]] = None


@dataclass
class TurnResult:
    reply: str
    messages_added: int
    usage: Usage = field(default_factory=Usage)
    asked_user: bool = False
    error: Optional[str] = None


class Agent:
    def __init__(self, provider: Provider, executor, knowledge=None,
                 config: Optional[AgentConfig] = None, callbacks: Optional[Callbacks] = None,
                 system_prompt: Optional[str] = None,
                 directory=None, gotchas: Optional[str] = None, recipes=None):
        self.provider = provider
        self.executor = executor
        self.knowledge = knowledge
        self.config = config or AgentConfig()
        self.cb = callbacks or Callbacks()
        self.conversation: List[Message] = []
        self.archived: List[Message] = []   # messages folded into `summary` (kept for the transcript on disk)
        self.summary: str = ""
        self.total_usage = Usage()
        self._system = system_prompt or prompt_mod.build_system_prompt(
            directory=directory, gotchas=gotchas, recipes=recipes,
            allow_python=self.config.allow_python,
            vision=self.config.vision and getattr(provider, "supports_vision", False))

    # ------------------------------------------------------------ public
    @property
    def system_prompt(self) -> str:
        if self.summary:
            return self._system + "\n\nEarlier in this conversation (summary):\n" + self.summary
        return self._system

    def reset(self) -> None:
        self.conversation = []
        self.archived = []
        self.summary = ""

    def run_turn(self, user_text: str, cancel: Optional[threading.Event] = None) -> TurnResult:
        cancel = cancel or threading.Event()
        start_len = len(self.conversation)
        usage = Usage()
        try:
            self._status("Thinking…")
            context = self._build_context(user_text)
            self.conversation.append(Message.user(user_text, context))
            tools = tool_specs(self.config.allow_python,
                               self.config.vision and getattr(self.provider, "supports_vision", False))
            outcome = self._loop(tools, cancel, usage)
            if outcome.get("asked"):
                self.total_usage.add(usage)
                return TurnResult(self._last_assistant_text(), len(self.conversation) - start_len, usage, asked_user=True)
            nudge = None
            if self.config.nudge_on_no_action and looks_like_action_request(user_text):
                if not outcome.get("called_tool"):
                    nudge = NUDGE          # words but no action
                elif outcome.get("ended_after_error"):
                    nudge = NUDGE_AFTER_ERROR   # gave up after a failed command
                elif _ONLY_RE.search(user_text) and not any(_HIDE_RE.match(c) for c in self._commands_since(start_len)):
                    nudge = NUDGE_ONLY     # "only X" without hiding the rest
            if nudge:
                self.conversation.append(Message.user(nudge))
                outcome = self._loop(tools, cancel, usage)
                if outcome.get("asked"):
                    self.total_usage.add(usage)
                    return TurnResult(self._last_assistant_text(), len(self.conversation) - start_len, usage, asked_user=True)
            self.total_usage.add(usage)
            self._maybe_compact()
            final = self._last_assistant_text()
            return TurnResult(final, len(self.conversation) - start_len, usage)
        except TurnCancelled:
            self.conversation.append(Message.assistant("(stopped)"))
            return TurnResult("(stopped)", len(self.conversation) - start_len, usage, error="cancelled")
        except ProviderError as e:
            self.conversation.append(Message.assistant("Error: %s" % e))
            return TurnResult(str(e), len(self.conversation) - start_len, usage, error=str(e))
        except Exception as e:  # never let the worker die silently
            tb = traceback.format_exc()
            self.conversation.append(Message.assistant("Unexpected error: %s" % e))
            return TurnResult("Unexpected error: %s" % e, len(self.conversation) - start_len, usage, error=tb)
        finally:
            self._status("")

    def _loop(self, tools, cancel: threading.Event, usage: Usage) -> Dict[str, bool]:
        """Model -> tools -> model ... until a reply without tool calls. Returns flags."""
        consecutive_errors = 0
        called_tool = False
        last_round_failed = False
        for _round in range(self.config.max_tool_rounds + 1):
            if cancel.is_set():
                raise TurnCancelled()
            reply, u = self.provider.stream(self.system_prompt, self.conversation, tools,
                                            on_delta=self.cb.on_text_delta, cancel=cancel)
            usage.add(u)
            self.conversation.append(reply)
            calls = reply.tool_calls()
            if not calls:
                return {"called_tool": called_tool, "asked": False, "ended_after_error": last_round_failed}
            results: List[ToolResult] = []
            asked = False
            any_error = False
            for call in calls:
                if cancel.is_set():
                    raise TurnCancelled()
                if call.name == "ask_user":
                    q = str(call.args.get("question", "")).strip()
                    opts = [str(o) for o in (call.args.get("options") or [])]
                    if self.cb.on_ask_user:
                        self.cb.on_ask_user(q, opts)
                    results.append(ToolResult(call.id, call.name, "Question shown to the user; wait for their answer."))
                    asked = True
                    continue
                called_tool = True
                res, payload = self._dispatch(call)
                any_error = any_error or res.is_error
                results.append(res)
            self.conversation.append(Message.tool_results(results))
            last_round_failed = any_error
            if asked:
                return {"called_tool": called_tool, "asked": True}
            if any_error:
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    self.conversation.append(Message.user(
                        "(system) Several attempts failed. Stop trying; explain briefly what failed and ask the user how to proceed."))
                    reply, u = self.provider.stream(self.system_prompt, self.conversation, [],
                                                    on_delta=self.cb.on_text_delta, cancel=cancel)
                    usage.add(u)
                    self.conversation.append(reply)
                    if not reply.text().strip():
                        last = self.executor.get_state().get("last_error", "") if hasattr(self.executor, "get_state") else ""
                        self.conversation.append(Message.assistant(
                            "I could not complete this request after several attempts. Last error: %s" % (last or "unknown")))
                    return {"called_tool": True, "asked": False}
            else:
                consecutive_errors = 0
        self.conversation.append(Message.assistant("I stopped after too many steps. Tell me how you'd like to continue."))
        return {"called_tool": called_tool, "asked": False}

    def answer_question(self, answer: str, cancel: Optional[threading.Event] = None) -> TurnResult:
        """Continue after an ask_user: the answer is just the next user turn."""
        return self.run_turn(answer, cancel)

    # ------------------------------------------------------------ internals
    def _status(self, msg: str) -> None:
        if self.cb.on_status:
            self.cb.on_status(msg)

    def _commands_since(self, index: int) -> List[str]:
        out: List[str] = []
        for m in self.conversation[index:]:
            if m.role == "assistant":
                for c in m.tool_calls():
                    if c.name == "run_commands":
                        raw = c.args.get("commands") or []
                        out.extend(str(x) for x in (raw if isinstance(raw, list) else [raw]))
        return out

    def _last_commands(self) -> List[str]:
        for m in reversed(self.conversation):
            if m.role == "assistant":
                for c in m.tool_calls():
                    if c.name == "run_commands":
                        raw = c.args.get("commands") or []
                        return [str(x) for x in (raw if isinstance(raw, list) else [raw])]
        return []

    def _build_context(self, user_text: str) -> str:
        state: Dict[str, Any] = {}
        try:
            state = self.executor.get_state()
        except Exception as e:
            state = {"error": str(e)}
        docs: List[Dict[str, Any]] = []
        if self.config.docs_per_turn > 0:
            try:
                docs = self.executor.search_docs(user_text, self.config.docs_per_turn)
            except Exception:
                docs = []
        note = ""
        if looks_like_complaint(user_text):
            last = self._last_commands()
            note = ("The user says the previous action did NOT do what they asked."
                    + (" The previous commands were: %s." % "; ".join(last) if last else "")
                    + " Do not repeat them and do not describe them as successful. Find a different, correct approach "
                      "(call search_docs and/or command_usage first), run different commands, or state plainly that "
                      "ChimeraX cannot do it and why.")
        return prompt_mod.build_context(state, docs, note=note)

    def _dispatch(self, call: ToolCall):
        if self.cb.on_tool_start:
            self.cb.on_tool_start(call)
        name = call.name
        args = call.args or {}
        payload: Any = None
        try:
            if name == "run_commands":
                res = self._run_commands(call)
                payload = res
                # a user's "skip" is a decision, not a failure to retry
                result = ToolResult(call.id, name, json.dumps(res, ensure_ascii=False),
                                    is_error=not res.get("ok", False) and not res.get("skipped"))
            elif name == "get_state":
                payload = self.executor.get_state()
                result = ToolResult(call.id, name, prompt_mod.format_state(payload))
            elif name == "command_usage":
                text = self.executor.command_usage(str(args.get("name", "")))
                result = ToolResult(call.id, name, text or "No usage found for that command.")
            elif name == "search_docs":
                k = int(args.get("k", 5) or 5)
                hits = self.executor.search_docs(str(args.get("query", "")), k)
                payload = hits
                txt = "\n\n".join("[%s | %s]\n%s" % (h.get("title"), h.get("section"), h.get("text")) for h in hits)
                result = ToolResult(call.id, name, txt or "No matching documentation.")
            elif name == "resolve_protein":
                payload = self.executor.resolve_protein(str(args.get("query", "")), str(args.get("organism", "human") or "human"))
                result = ToolResult(call.id, name, json.dumps(payload, ensure_ascii=False), is_error="error" in payload)
            elif name == "protein_features":
                payload = self.executor.protein_features(str(args.get("accession", "")), args.get("kinds"))
                result = ToolResult(call.id, name, json.dumps(payload, ensure_ascii=False), is_error="error" in payload)
            elif name == "run_python":
                code = str(args.get("code", ""))
                approved = self._confirm([code], ["runs Python code inside ChimeraX"])
                if approved is None:
                    result = ToolResult(call.id, name, "The user declined to run this code.", is_error=True)
                else:
                    payload = self.executor.run_python(approved[0] if approved else code)
                    result = ToolResult(call.id, name, json.dumps(payload, ensure_ascii=False), is_error=not payload.get("ok", False))
            elif name == "look_at_view":
                payload = self.executor.look_at_view()
                result = ToolResult(call.id, name, payload.get("text", "Screenshot attached."),
                                    image_png_b64=payload.get("png_b64"))
            else:
                result = ToolResult(call.id, name, "Unknown tool: %s" % name, is_error=True)
        except Exception as e:
            result = ToolResult(call.id, name, "Tool failed: %s" % e, is_error=True)
        if self.cb.on_tool_result:
            self.cb.on_tool_result(call, result, payload)
        return result, payload

    def _confirm(self, commands: List[str], reasons: List[str]) -> Optional[List[str]]:
        if self.cb.on_confirm is None:
            return commands
        return self.cb.on_confirm(commands, reasons)

    def _run_commands(self, call: ToolCall) -> Dict[str, Any]:
        raw = call.args.get("commands") or []
        if isinstance(raw, str):
            raw = [raw]
        commands: List[str] = []
        for c in raw:
            commands.extend(split_commands(str(c)))
        if not commands:
            return {"ok": False, "results": [], "error": "No commands given."}
        pending = needs_confirmation(commands, self.config.autonomy)
        if pending:
            reasons = []
            for cmd in commands:
                match = [p for p in pending if p.command == cmd]
                reasons.append(match[0].reason if match else "")
            approved = self._confirm(commands, reasons)
            if approved is None:
                return {"ok": False, "results": [], "error": "The user chose not to run these commands.", "skipped": True}
            commands = approved
            if not commands:
                return {"ok": False, "results": [], "error": "The user removed all commands.", "skipped": True}
        results = self.executor.run_commands(commands)
        ok = all(r.get("ok") for r in results) and len(results) == len(commands)
        out: Dict[str, Any] = {"ok": ok, "results": results}
        if not ok:
            failed = [r for r in results if not r.get("ok")]
            if failed:
                out["error"] = failed[0].get("error", "command failed")
                out["failed_command"] = failed[0].get("command")
                # attach the real syntax so the model can fix it without guessing
                word = first_word(failed[0].get("command", ""))
                if word:
                    try:
                        usage = self.executor.command_usage(word)
                    except Exception:
                        usage = ""
                    if usage:
                        out["usage_of_%s" % word] = usage[:1500]
                tip = suggest(failed[0].get("command", ""), failed[0].get("error", ""))
                if tip:
                    out["suggestion"] = tip
                out["hint"] = ("Run a corrected command now (follow the suggestion if there is one, else the usage). "
                               "Do not explain the fix in words without running it. If the option you wanted does not "
                               "exist, call search_docs for the task and use a different approach; do not invent options.")
            remaining = commands[len(results):]
            if remaining:
                out["not_run"] = remaining
        return out

    def _last_assistant_text(self) -> str:
        for m in reversed(self.conversation):
            if m.role == "assistant":
                t = m.text().strip()
                if t:
                    return t
        return ""

    # ------------------------------------------------------------ memory
    def _estimated_tokens(self) -> int:
        n = 0
        for m in self.conversation:
            for p in m.parts:
                if isinstance(p, TextPart):
                    n += estimate_tokens(p.text)
                elif isinstance(p, ToolResult):
                    n += estimate_tokens(p.content)
                elif isinstance(p, ToolCall):
                    n += estimate_tokens(json.dumps(p.args))
            n += estimate_tokens(m.meta.get("context", ""))
        return n

    def _maybe_compact(self) -> None:
        if (len(self.conversation) < self.config.compact_after_messages
                and self._estimated_tokens() < self.config.compact_after_tokens):
            return
        # keep the last 8 messages verbatim; summarise the rest
        keep = 8
        old, recent = self.conversation[:-keep], self.conversation[-keep:]
        # make sure we do not cut between an assistant tool call and its results
        while recent and recent[0].role == "tool":
            old.append(recent.pop(0))
        transcript = []
        for m in old:
            if m.role == "user":
                transcript.append("User: " + m.text())
            elif m.role == "assistant":
                t = m.text()
                calls = ["%s(%s)" % (c.name, json.dumps(c.args)[:200]) for c in m.tool_calls()]
                transcript.append("Assistant: " + t + (" [called: " + "; ".join(calls) + "]" if calls else ""))
            elif m.role == "tool":
                transcript.append("Results: " + "; ".join(
                    ("ERROR " if r.is_error else "") + r.content[:200] for r in m.tool_results_list()))
        try:
            msg = Message.user("Summarize the following conversation in under 200 words, keeping model numbers, "
                               "accessions, residue numbers and the current state of the scene:\n\n" + "\n".join(transcript))
            reply, u = self.provider.stream(self._system, [msg], [], on_delta=None, cancel=None)
            self.total_usage.add(u)
            new_summary = reply.text().strip()
        except Exception:
            new_summary = "\n".join(transcript)[-3000:]
        self.summary = (self.summary + "\n" + new_summary).strip() if self.summary else new_summary
        for m in old:
            m.meta.pop("context", None)
        self.archived.extend(old)
        # strip context blocks from retained old messages to save tokens
        for m in recent:
            if m.role == "user" and m is not recent[-1]:
                m.meta.pop("context", None)
        self.conversation = recent
