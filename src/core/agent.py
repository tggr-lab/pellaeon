"""The agent loop: user text -> model -> tool calls -> executor -> ... -> reply.

Runs in a worker thread. Everything that must happen on ChimeraX's main
thread is behind the ``Executor`` and the UI callbacks; the agent itself
never imports ChimeraX.
"""
from __future__ import annotations

import json
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import prompt as prompt_mod
from .fixups import suggest
from .safety import AUTONOMY_AUTO, first_word, needs_confirmation, split_commands
from .schema import (Message, TextPart, ToolCall, ToolResult, Usage, estimate_tokens, new_id)
from .tools import tool_specs
from .http import Cancelled
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
    edition: str = "chimerax"          # "chimerax" or "chimera" (classic)


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
NUDGE_ONLY_CHIMERA = ("(system) The user said ONLY, but nothing was hidden. In classic Chimera hide everything first, then show "
                      "the part: run_commands([\"~display #0\", \"~ribbon #0\", \"ribbon :.B\", \"display :.B\"]) for 'only chain B' "
                      "(chain goes after the residue with a dot), or [\"~display #0\", \"ribbon #0\"] for 'only the ribbon'. Do it now.")
_ONLY_RE = re.compile(r"\bonly\b|\bjust (the |chain )", re.I)
_HIDE_RE_CHIMERA = re.compile(r"^\s*(~|show\b|close|delete)", re.I)   # classic Chimera's `show` = display ONLY these
_HIDE_RE = re.compile(r"^\s*(hide|~|cartoon hide|surface hide|close|delete|undisplay)", re.I)

NUDGE_AA = ("(system) The user asked to color by amino-acid/residue TYPE. ChimeraX has no built-in scheme for that "
            "(byelement/bychain are wrong). Run: color #1:ala,val,ile,leu,met,phe,trp,pro,gly white ; "
            "color #1:ser,thr,asn,gln,cys,tyr green ; color #1:lys,arg,his blue ; color #1:asp,glu red")
_AA_TYPE_RE = re.compile(r"\b(by|per)\s+(the\s+)?(type\s+of\s+)?(aa|amino\s*acids?|residues?)(\s+type)?\b|\bresidue[- ]type\b|hydrophobic", re.I)
_AA_CLASS_RE = re.compile(r":(ala|asp|lys|ser|glu|arg|leu|val)\b|byattr", re.I)
_ANNOUNCE_RE = re.compile(r"\b(i'?ll|i will|let me|i am going to|i'm going to|going to)\b", re.I)

NUDGE_ANNOTATE = ("(system) The user asked for variants/annotations. Do NOT type 'annotate' as a command: CALL THE TOOL named "
                  "annotate with arguments model (e.g. \"#1\"), accession (the UniProt accession, or a gene symbol for kind "
                  "\"clinvar\") and kind (\"clinvar\" for ClinVar disease variants, \"disease\"/\"variant\" for UniProt variants, "
                  "\"domain\", \"transmembrane\", \"binding\"...). Call it now.")
_ANNOT_RE = re.compile(r"\b(clinvar|variants?|mutations?|domains?|transmembrane|binding sites?|active sites?|glycosylation|disulfides?)\b", re.I)

_TOOL_NAMES = {"annotate", "compare_structures", "resolve_protein", "protein_features", "search_docs", "get_state",
               "command_usage", "run_python", "look_at_view", "ask_user", "run_commands"}

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
    # confirm(commands, reasons, python=False) -> approved list, or None if the user skipped
    on_confirm: Optional[Callable[..., Optional[List[str]]]] = None
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
        self.journal: List[Dict[str, Any]] = []   # every command that actually ran, from any path
        self._verified_accessions: set = set()     # accessions returned by resolve_protein in this conversation
        self._user_text_upper: str = ""
        self._failed_this_turn: set = set()
        self.archived: List[Message] = []   # messages folded into `summary` (kept for the transcript on disk)
        self.summary: str = ""
        self.total_usage = Usage()
        self._system = system_prompt or prompt_mod.build_system_prompt(
            directory=directory, gotchas=gotchas, recipes=recipes,
            allow_python=self.config.allow_python,
            vision=self.config.vision and getattr(provider, "supports_vision", False),
            edition=self.config.edition)

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
        self._verified_accessions = set()
        self._user_text_upper = ""

    def run_turn(self, user_text: str, cancel: Optional[threading.Event] = None) -> TurnResult:
        cancel = cancel or threading.Event()
        start_len = len(self.conversation)
        usage = Usage()
        try:
            self._status("Thinking…")
            self._failed_this_turn = set()
            self._user_text_upper += " " + user_text.upper()
            context = self._build_context(user_text)
            self.conversation.append(Message.user(user_text, context))
            tools = tool_specs(self.config.allow_python,
                               self.config.vision and getattr(self.provider, "supports_vision", False))
            outcome = self._loop(tools, cancel, usage)
            if outcome.get("asked"):
                self.total_usage.add(usage)
                return TurnResult(self._last_assistant_text(), len(self.conversation) - start_len, usage, asked_user=True)
            for attempt in range(2):
                nudge = None
                last_ctx = context
                if self.config.nudge_on_no_action and looks_like_action_request(user_text):
                    ran = self._commands_since(start_len)
                    if not outcome.get("called_tool"):
                        nudge = NUDGE          # words but no action
                    elif outcome.get("ended_after_error"):
                        nudge = NUDGE_AFTER_ERROR   # gave up after a failed command
                    elif _ONLY_RE.search(user_text) and not any(
                            (_HIDE_RE_CHIMERA if self.config.edition == "chimera" else _HIDE_RE).match(c) for c in ran):
                        nudge = NUDGE_ONLY_CHIMERA if self.config.edition == "chimera" else NUDGE_ONLY
                    elif _AA_TYPE_RE.search(user_text) and ran and not any(_AA_CLASS_RE.search(c) for c in ran):
                        nudge = NUDGE_AA       # residue-type coloring done with a wrong built-in scheme
                    elif _ANNOT_RE.search(user_text) and not self._tool_called_since(start_len, "annotate"):
                        nudge = NUDGE_ANNOTATE  # variants/domains asked for, annotate tool never called
                if not nudge:
                    break
                if attempt == 1 and not _ANNOUNCE_RE.search(self._last_assistant_text()):
                    break  # second try only when the model keeps announcing instead of acting
                self.conversation.append(Message.user(nudge, last_ctx))
                outcome = self._loop(tools, cancel, usage)
                if outcome.get("asked"):
                    self.total_usage.add(usage)
                    return TurnResult(self._last_assistant_text(), len(self.conversation) - start_len, usage, asked_user=True)
                if outcome.get("asked"):
                    self.total_usage.add(usage)
                    return TurnResult(self._last_assistant_text(), len(self.conversation) - start_len, usage, asked_user=True)
            self._canned_fallbacks(user_text, start_len)
            self.total_usage.add(usage)
            self._maybe_compact()
            final = self._last_assistant_text()
            return TurnResult(final, len(self.conversation) - start_len, usage)
        except (TurnCancelled, Cancelled):
            self._close_dangling_tool_calls("Cancelled by the user before this tool ran.")
            self.conversation.append(Message.assistant("(stopped)"))
            return TurnResult("(stopped)", len(self.conversation) - start_len, usage, error="cancelled")
        except ProviderError as e:
            self.conversation.append(Message.assistant("Error: %s" % e))
            return TurnResult(str(e), len(self.conversation) - start_len, usage, error=str(e))
        except Exception as e:  # never let the worker die silently
            tb = traceback.format_exc()
            self._close_dangling_tool_calls("Tool did not run: %s" % e)
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
            if not reply.text().strip() and not reply.tool_calls():
                # empty answer (small local models do this occasionally): ask once more,
                # with thinking switched on for Ollama models, which reliably fixes it
                self._status("Empty answer, retrying…")
                opts = getattr(self.provider, "options", None)
                restore = None
                if getattr(self.provider, "name", "") == "ollama" and isinstance(opts, dict):
                    restore = opts.get("think", None)
                    opts["think"] = True
                try:
                    reply, u = self.provider.stream(self.system_prompt, self.conversation, tools,
                                                    on_delta=self.cb.on_text_delta, cancel=cancel)
                finally:
                    if isinstance(opts, dict) and getattr(self.provider, "name", "") == "ollama":
                        if restore is None:
                            opts.pop("think", None)
                        else:
                            opts["think"] = restore
                usage.add(u)
                if not reply.text().strip() and not reply.tool_calls():
                    reply = Message.assistant("The model returned an empty answer twice. Please try again or rephrase.")
            self.conversation.append(reply)
            calls = reply.tool_calls()
            if not calls:
                return {"called_tool": called_tool, "asked": False, "ended_after_error": last_round_failed}
            results: List[ToolResult] = []
            asked = False
            any_error = False
            try:
                for call in calls:
                    if asked:
                        results.append(ToolResult(call.id, call.name, "Not run: waiting for the user's answer first.", is_error=True))
                        continue
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
            except (TurnCancelled, Cancelled):
                done = {r.call_id for r in results}
                results += [ToolResult(c.id, c.name, "Cancelled by the user before this tool ran.", is_error=True)
                            for c in calls if c.id not in done]
                self.conversation.append(Message.tool_results(results))
                raise
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

    def _canned_fallbacks(self, user_text: str, start_len: int) -> None:
        """Last resort for requests the model keeps getting wrong: run a known-good recipe ourselves."""
        ran = self._commands_since(start_len)
        if _AA_TYPE_RE.search(user_text) and looks_like_action_request(user_text) \
                and not any(_AA_CLASS_RE.search(c) for c in ran):
            if self.config.edition == "chimera":
                cmds = ["color white,a,r :ala,val,ile,leu,met,phe,trp,pro,gly", "color green,a,r :ser,thr,asn,gln,cys,tyr",
                        "color blue,a,r :lys,arg,his", "color red,a,r :asp,glu"]
            else:
                cmds = ["color #1:ala,val,ile,leu,met,phe,trp,pro,gly white", "color #1:ser,thr,asn,gln,cys,tyr green",
                        "color #1:lys,arg,his blue", "color #1:asp,glu red"]
            call = ToolCall(new_id(), "run_commands", {"commands": cmds})
            self.conversation.append(Message("assistant", [call]))
            res, _payload = self._dispatch(call)
            self.conversation.append(Message.tool_results([res]))
            if res.is_error:
                self.conversation.append(Message.assistant("I tried the residue-class coloring but it failed: %s" % res.content[:200]))
            else:
                self.conversation.append(Message.assistant(
                    "Colored by residue class: hydrophobic white, polar green, positive (Lys/Arg/His) blue, negative (Asp/Glu) red."))

    def _close_dangling_tool_calls(self, note: str) -> None:
        """Every tool call needs a result before the next request, even after Stop."""
        if not self.conversation or self.conversation[-1].role != "assistant":
            return
        calls = self.conversation[-1].tool_calls()
        if calls:
            self.conversation.append(Message.tool_results(
                [ToolResult(c.id, c.name, note, is_error=True) for c in calls]))

    def _tool_called_since(self, index: int, name: str) -> bool:
        return any(c.name == name for m in self.conversation[index:] if m.role == "assistant" for c in m.tool_calls())

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
                q = str(args.get("query", "")).strip()
                if re.match(r"^[0-9][A-Za-z0-9]{3}$", q):
                    payload = {"hint": "'%s' is a PDB id, not a gene. Just run: open %s" % (q, q.lower())}
                else:
                    payload = self.executor.resolve_protein(q, str(args.get("organism", "human") or "human"))
                    for cand in ([payload] if payload.get("accession") else []) + list(payload.get("candidates") or []):
                        if cand.get("accession"):
                            self._verified_accessions.add(str(cand["accession"]).upper())
                result = ToolResult(call.id, name, json.dumps(payload, ensure_ascii=False), is_error="error" in payload)
            elif name == "protein_features":
                payload = self.executor.protein_features(str(args.get("accession", "")), args.get("kinds"))
                result = ToolResult(call.id, name, json.dumps(payload, ensure_ascii=False), is_error="error" in payload)
            elif name == "compare_structures":
                if not args.get("reference") or not args.get("other"):
                    raise RuntimeError("compare_structures needs both 'reference' and 'other' model specs.")
                payload = self._compare(str(args["reference"]), str(args["other"]), args.get("chain") or None)
                result = ToolResult(call.id, name, json.dumps({k: v for k, v in payload.items() if k != "commands"}, ensure_ascii=False),
                                    is_error="error" in payload)
            elif name == "annotate":
                payload = self._annotate(str(args.get("model", "#1")), str(args.get("accession", "")),
                                         str(args.get("kind", "variant")), str(args.get("color", "orange") or "orange"),
                                         bool(args.get("label", True)))
                result = ToolResult(call.id, name, json.dumps({k: v for k, v in payload.items() if k != "commands"}, ensure_ascii=False),
                                    is_error="error" in payload)
            elif name == "run_python":
                if not self.config.allow_python:
                    raise RuntimeError("Python execution is disabled in Settings.")
                code = str(args.get("code", ""))
                approved = self._confirm([code], ["runs Python code inside ChimeraX"], python=True)
                if approved is None:
                    result = ToolResult(call.id, name, "The user declined to run this code.", is_error=True)
                else:
                    payload = self.executor.run_python(approved[0] if approved else code)
                    result = ToolResult(call.id, name, json.dumps(payload, ensure_ascii=False), is_error=not payload.get("ok", False))
            elif name == "look_at_view":
                if not (self.config.vision and getattr(self.provider, "supports_vision", False)):
                    raise RuntimeError("Screenshots are disabled in Settings.")
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

    def _confirm(self, commands: List[str], reasons: List[str], python: bool = False) -> Optional[List[str]]:
        if self.cb.on_confirm is None:
            return None  # no way to ask the user: fail closed
        try:
            return self.cb.on_confirm(commands, reasons, python)
        except TypeError:
            return self.cb.on_confirm(commands, reasons)

    def _run_commands(self, call: ToolCall) -> Dict[str, Any]:
        raw = call.args.get("commands") or []
        if isinstance(raw, str):
            raw = [raw]
        commands: List[str] = []
        for c in raw:
            commands.extend(split_commands(str(c)))
        return self.execute_commands(commands, origin="model")

    def execute_commands(self, commands: List[str], origin: str = "model") -> Dict[str, Any]:
        """The single path for running commands: policy, repeat guard, execution, journal."""
        commands = [c.strip() for c in commands if c and c.strip()]
        if not commands:
            return {"ok": False, "results": [], "error": "No commands given."}
        for c in commands:
            for acc in re.findall(r"alphafold:([A-Za-z0-9]+)", c, re.I):
                if acc.upper() not in self._verified_accessions and acc.upper() not in self._user_text_upper:
                    return {"ok": False, "results": [], "error": "Accession %s was NOT obtained from resolve_protein, so it may be the "
                            "wrong protein. Call resolve_protein with the gene/protein name first and use the open_command it returns."
                            % acc, "unverified_accession": acc}
            w = first_word(c)
            if w in _TOOL_NAMES:
                return {"ok": False, "results": [], "error": "'%s' is one of YOUR TOOLS, not a ChimeraX command. Call the tool "
                        "named %s with its arguments instead of running it as text." % (w, w), "tool_misuse": w}
        repeats = [c for c in commands if c in self._failed_this_turn]
        if repeats:
            return {"ok": False, "results": [], "error": "You already ran exactly this command in this turn and it failed: %s. "
                    "Do not repeat it. Change it according to the usage/suggestion, or use a different approach." % repeats[0],
                    "repeated": True}
        pending = needs_confirmation(commands, self.config.autonomy)
        if pending:
            reasons = []
            for cmd in commands:
                match = [p for p in pending if p.command == cmd]
                reasons.append(match[0].reason if match else "")
            approved = self._confirm(commands, reasons)
            if approved is None:
                return {"ok": False, "results": [], "error": "The user chose not to run these commands.", "skipped": True}
            commands = [c.strip() for c in approved if c and c.strip()]
            if not commands:
                return {"ok": False, "results": [], "error": "The user removed all commands.", "skipped": True}
        results = self.executor.run_commands(commands)
        now = time.time()
        for r in results:
            self.journal.append({"ts": now, "origin": origin, "command": r.get("command", ""), "ok": bool(r.get("ok")),
                                 "error": r.get("error", "")})
        ok = all(r.get("ok") for r in results) and len(results) == len(commands)
        out: Dict[str, Any] = {"ok": ok, "results": results}
        if not ok:
            failed = [r for r in results if not r.get("ok")]
            if failed:
                self._failed_this_turn.add(str(failed[0].get("command", "")).strip())
                out["error"] = failed[0].get("error", "command failed")
                out["failed_command"] = failed[0].get("command")
                word = first_word(failed[0].get("command", ""))
                if word:
                    try:
                        usage = self.executor.command_usage(word)
                    except Exception:
                        usage = ""
                    if usage:
                        out["usage_of_%s" % word] = usage[:1500]
                tip = suggest(failed[0].get("command", ""), failed[0].get("error", "")) if self.config.edition == "chimerax" else None
                if tip:
                    out["suggestion"] = tip
                out["hint"] = ("Run a corrected command now (follow the suggestion if there is one, else the usage). "
                               "Do not explain the fix in words without running it. If the option you wanted does not "
                               "exist, call search_docs for the task and use a different approach; do not invent options.")
            remaining = commands[len(results):]
            if remaining:
                out["not_run"] = remaining
        return out

    # ------------------------------------------------------------ compare / annotate (orchestrated here so that
    # every scene change passes through execute_commands and network work stays off the UI thread)
    def _compare(self, reference: str, other: str, chain: Optional[str]) -> Dict[str, Any]:
        prep = self.executor.prepare_compare(reference, other, chain)
        if prep.get("error"):
            return prep
        cmd = "matchmaker %s %s" % (prep["other_spec"], prep["ref_spec"]) if self.config.edition == "chimera" \
            else "matchmaker %s to %s" % (prep["other_spec"], prep["ref_spec"])
        mm = self.execute_commands([cmd], origin="compare")
        if not mm.get("ok"):
            return {"error": "matchmaker did not run: %s" % mm.get("error", ""), "details": mm}
        info = mm["results"][0].get("info", []) if mm["results"] else []
        rmsd_line = next((i for i in info if "RMSD" in i), "")
        disp = self.executor.compute_displacement(prep)
        out: Dict[str, Any] = {"reference": prep["ref_spec"], "compared": prep["other_spec"], "chain": prep.get("chain") or "all",
                               "rmsd": rmsd_line or "see log", "commands": mm["results"]}
        if disp.get("unsupported"):
            out["note"] = disp.get("note", "Per-residue displacement is not available in this edition.")
            return out
        if disp.get("error"):
            out["error"] = disp["error"]
            return out
        out.update({k: v for k, v in disp.items() if k != "color_commands"})
        if disp.get("color_commands"):
            col = self.execute_commands(disp["color_commands"], origin="compare")
            out["colored"] = bool(col.get("ok"))
            out["commands"] = out["commands"] + col.get("results", [])
            if col.get("skipped"):
                out["note"] = "The user declined the coloring commands."
        return out

    def _annotate(self, model: str, accession: str, kind: str, color: str, label: bool) -> Dict[str, Any]:
        from .annotate import normalize_kind, uniprot_items, clinvar_items, build_annotation, is_accession
        if not re.match(r"^(#[0-9a-fA-F]{6}|[A-Za-z][A-Za-z ]{1,30})$", color or ""):
            color = "orange"
        kinds, only_disease, use_clinvar = normalize_kind(kind)
        acc = (accession or "").strip()
        gene = acc
        if is_accession(acc):
            info = self.executor.protein_features(acc, ["Domain"])
            gene = info.get("gene") or acc
        elif acc:
            r = self.executor.resolve_protein(acc, "human")   # gene symbol -> accession (needed to map onto PDB chains)
            if not r.get("accession"):
                return {"error": "Could not find a UniProt accession for '%s'." % accession}
            acc = r["accession"]
            gene = r.get("gene") or gene
        if use_clinvar:
            clinvar = getattr(self.executor, "clinvar", None)
            if clinvar is None:
                from .clinvar import ClinVarClient
                clinvar = ClinVarClient(None)
            data = clinvar.missense_variants(gene)
            if "error" in data:
                return data
            items = clinvar_items(data)
            source = "ClinVar missense variants for %s (%d records, %d positions%s)" % (
                gene, data.get("searched", 0), data.get("count", 0),
                "; more may exist" if data.get("searched", 0) >= 2000 else "")
        else:
            if not is_accession(acc):
                return {"error": "annotate needs a UniProt accession (use resolve_protein) or a gene name for 'clinvar'."}
            feats = self.executor.protein_features(acc, kinds)
            if "error" in feats:
                return feats
            items = uniprot_items(feats, only_disease)
            source = "UniProt %s features: %s" % (acc, ", ".join(kinds or []))
        if not items:
            return {"accession": acc, "kind": kind, "count": 0, "message": "No matching annotations (%s)." % source}
        positions = sorted({p for it in items for p in it["positions"]})
        mapping = self.executor.map_positions(model, acc, positions)
        if mapping.get("error"):
            return mapping
        cmds, summary = build_annotation(items, mapping, color, label, edition=self.config.edition)
        if not cmds:
            return {"accession": acc, "kind": kind, "count": len(items), "source": source, **summary,
                    "message": "None of the annotated positions could be mapped onto the model."}
        res = self.execute_commands(cmds, origin="annotate")
        out = {"accession": acc, "kind": kind, "count": len(items), "source": source, **summary,
               "ok": bool(res.get("ok")), "commands_failed": sum(1 for r in res.get("results", []) if not r.get("ok")),
               "commands": res.get("results", [])}
        if res.get("skipped"):
            out["note"] = "The user declined the commands."
        if use_clinvar:
            out["legend"] = "red = pathogenic, orange = likely pathogenic, yellow = uncertain, cyan/blue = (likely) benign"
            out["pathogenic"] = [it["label"] for it in items if it.get("pathogenic")][:40]
            out["by_significance"] = {}
            for it in items:
                out["by_significance"][it["type"]] = out["by_significance"].get(it["type"], 0) + 1
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
