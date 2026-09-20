"""The agent loop: user text -> model -> tool calls -> executor -> ... -> reply.

Runs in a worker thread. Everything that must happen on ChimeraX's main
thread is behind the ``Executor`` and the UI callbacks; the agent itself
never imports ChimeraX.
"""
from __future__ import annotations

import json
import os
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
from .recovery import split_joined
from .tools import tool_specs
from .http import Cancelled
from .providers.base import Provider, ProviderError

MAX_TOOL_ROUNDS = 12
MAX_CONSECUTIVE_ERRORS = 3
COMPACT_AFTER_MESSAGES = 80
COMPACT_AFTER_TOKENS = 60000


class TurnCancelled(Exception):
    pass


_UNSET = object()      # "use the default callback" (None is a meaningful value: stream silently)


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
    readable_labels: bool = True       # add fixed size / white background / on-top to plain label commands
    compact_prompt: bool = False       # short prompt for providers that meter input tokens per minute


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
_NAMED_GROUP_RE = re.compile(r"\b(haems?|hemes?|heme\s+groups?|atp|adp|amp|gtp|gdp|nadh?|nadph?|fadh?|fmn|nag|man|bma|retinal|chlorophyll|cofactors?|"
                             r"ligands?|glycans?|sugars?|waters?|ions?|zinc|magnesium|calcium|sodium)\b", re.I)
_NAMED_SPEC_RE = re.compile(r":[A-Za-z][A-Za-z0-9]{1,3}\b|\b(ligand|solvent|ions|water)\b|&", re.I)
_NUMERIC_SPEC_RE = re.compile(r":\d+")
NUDGE_NAMED = ("(system) The user referred to a named ligand/cofactor/group (heme, ATP, NAG, water, ions ...), but you used "
               "residue NUMBERS, which are guesses and matched nothing. Use the residue NAME spec (heme = :HEM, ATP = :ATP, "
               "water = :HOH) or a built-in selector (`ligand`, `solvent`, `ions`), e.g. `show :HEM atoms; style :HEM sphere; "
               "color :HEM red`. Run the corrected commands now.")
_COLORS = r"colou?r(ed)?|red|blue|green|yellow|orange|white|gray|grey|magenta|cyan|pink|purple"
_WHY_RE = re.compile(r"\bwhy\b.*\b(%s|label(ed|s)?|this look|that look|look like)\b"
                     r"|\b(where|what|which)\s+(did|does|made|gave|command)\b.*\b(%s|label|look)|\b(what|which)\s+colou?red\b|\blabel\b.*\bcome[s]? from\b" % (_COLORS, _COLORS), re.I)
_RES_SPEC_RE = re.compile(r"#\d+(?:\.\d+)*/[A-Za-z0-9]+:-?\d+[A-Za-z]?")
NUDGE_WHY = ("(system) The user asks WHY a residue looks like it does. Do not guess: CALL THE TOOL named explain_residue with the "
             "residue spec (the selection in the state, or the residue named in the request) and answer from its history.")
_CHECKED_WORDS = ("color", "colour", "select", "style", "show", "hide", "cartoon", "transparency", "surface", "label", "size", "rainbow")
_WHICH_MODEL_RE = re.compile(r"\b(which|what|specify( the)?|need to know which)\s+(model|structure|selection|one)\b|\bspecific model\b", re.I)
NUDGE_ONE_MODEL = ("(system) Exactly ONE model is open (see the state: it is #1). It is the target. Do not ask which model; "
                   "run the commands on #1 now with run_commands.")
COMMANDS_ONLY = ("(system) You did not call the tool. Reply with ONLY the ChimeraX command lines that do what the user asked, "
                 "one command per line, no prose, no numbering, no backticks. Example:\ncolor #1 bychain\nshow ligand atoms")
_LOOK_RE = re.compile(r"\b(look at|take a look|have a look|check|review|inspect|how does it look|does it look|what do you see|see the view|the screen|screenshot)\b", re.I)
NUDGE_LOOK = ("(system) The user wants you to LOOK at the view. Call the tool look_at_view first (it returns a screenshot), "
              "describe what you see, then fix problems with commands and look again.")
# "email this to my boss", "print it out": ChimeraX cannot, and every model tested so far
# tries to save a file as a first step anyway. Saving is still fine if the user also asked for it.
_IMPOSSIBLE_RE = re.compile(r"\b(e-?mail|fax|whatsapp|text (it|this|them)|print (it|this|them)( out)?|printer|"
                            r"send (it|this|them|the \w+)\s+(to|over)|upload (it|this)|post (it|this) (to|on)|"
                            r"share (it|this) (with|on))\b", re.I)
_SAVE_WANTED_RE = re.compile(r"\b(save|export|write|download|png|jpe?g|tiff|figure file|to my (desktop|folder|computer))\b", re.I)

_TIDY_RE = re.compile(r"\blabels?\b.*\b(overlap|unreadable|readable|too (small|many|big)|tidy|clean|declutter|mess)|\b(tidy|clean up|declutter)\b.*\blabels?\b", re.I)
NUDGE_TIDY = ("(system) The user is complaining about the LABELS (overlap, readability, clutter). Do not re-run label commands "
              "with guesses: CALL THE TOOL named tidy_labels (optionally with keep=<spec>). It measures the overlaps on screen and fixes them.")
_ANNOT_RE = re.compile(r"\b(clinvar|variants?|mutations?|domains?|transmembrane|binding sites?|active sites?|glycosylation|disulfides?)\b", re.I)

_TOOL_NAMES = {"annotate", "compare_structures", "table_overlay", "tidy_labels", "explain_residue", "save_figure", "resolve_protein", "protein_features", "search_docs", "get_state",
               "command_usage", "run_python", "look_at_view", "ask_user", "run_commands"}

_COMPLAINT_RE = re.compile(
    r"\b(did ?n[o']?t|does ?n[o']?t|not work(ing)?|nothing (happened|changed)|no(t)? (you|it) (did|does)|you did not|"
    r"that'?s (wrong|not it)|wrong|not what i|still the same|no change|nope|it'?s not|isn'?t|aren'?t|are they though|"
    r"are they thou|try (again|something else|a different)|didn'?t work)\b")


_RMSD_RE = re.compile(r"RMSD between (\d+) pruned atom pairs is ([\d.]+)(?: angstroms)?(?:; \(across all (\d+) pairs: ([\d.]+)\))?", re.I)


def parse_rmsd_line(line: str) -> Dict[str, Any]:
    """MatchMaker's log line -> {'fit_pairs', 'fit_rmsd', 'all_pairs', 'all_rmsd'} (whatever is present)."""
    m = _RMSD_RE.search(line or "")
    if not m:
        return {}
    out: Dict[str, Any] = {"fit_pairs": int(m.group(1)), "fit_rmsd": float(m.group(2))}
    if m.group(3):
        out["all_pairs"] = int(m.group(3)); out["all_rmsd"] = float(m.group(4))
    return out


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
        self._impossible_ask: bool = False
        self.archived: List[Message] = []   # messages folded into `summary` (kept for the transcript on disk)
        self.summary: str = ""
        self.total_usage = Usage()
        self.tables: Dict[str, Dict[str, Any]] = {}     # user-loaded tables (name -> dataset), shared with the panel
        self.layers: List[Dict[str, Any]] = []          # applied table overlays
        self.figure_notes: List[str] = []               # legend fragments from annotate / compare / table overlays
        self.recovered_calls: int = 0                   # tool calls that arrived as text and were run anyway
        self.commands_only_turns: int = 0               # turns rescued by the commands-only fallback
        self.figures_dir: str = ""                      # where model-initiated figure bundles go (set by the host)
        self._fixed_system = system_prompt          # set by the caller: never rebuilt
        self._prompt_parts = {"directory": directory, "gotchas": gotchas, "recipes": recipes}
        self.compact = bool(self.config.compact_prompt)
        self._system = system_prompt or self._build_system()

    def _build_system(self) -> str:
        return prompt_mod.build_system_prompt(
            directory=self._prompt_parts["directory"], gotchas=self._prompt_parts["gotchas"],
            recipes=self._prompt_parts["recipes"],
            allow_python=self.config.allow_python,
            vision=self.config.vision and getattr(self.provider, "supports_vision", False),
            edition=self.config.edition, compact=self.compact)

    def go_compact(self) -> bool:
        """Switch to the short prompt and shrink what was already built. False if already compact."""
        if self.compact:
            return False
        self.compact = True
        if not self._fixed_system:
            self._system = self._build_system()
        for m in self.conversation:
            ctx = m.meta.get("context") if m.meta else None
            if ctx:
                m.meta["context"] = prompt_mod.trim_context(ctx)
        return True

    @staticmethod
    def _request_too_large(error_text: str) -> bool:
        t = (error_text or "").lower()
        return ("413" in t or "too large" in t or "too long" in t
                or "reduce your message size" in t or "context length" in t or "maximum context" in t)

    def _stream(self, tools, cancel, on_delta=_UNSET):
        """Every model call goes through here, so one 'request too large' can retry compactly."""
        delta = self.cb.on_text_delta if on_delta is _UNSET else on_delta
        try:
            return self.provider.stream(self.system_prompt, self.conversation, tools, on_delta=delta, cancel=cancel)
        except ProviderError as e:
            if self._request_too_large(str(e)) and self.go_compact():
                self._status("The prompt was too large for this model; retrying with a shorter one…")
                return self.provider.stream(self.system_prompt, self.conversation, tools, on_delta=delta, cancel=cancel)
            raise

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
            self._impossible_ask = bool(_IMPOSSIBLE_RE.search(user_text)) and not _SAVE_WANTED_RE.search(user_text)
            self._user_text_upper += " " + user_text.upper()
            context = self._build_context(user_text)
            self.conversation.append(Message.user(user_text, context))
            if self.config.edition == "chimerax" and _WHY_RE.search(user_text) and hasattr(self.executor, "residue_provenance"):
                # "why is this red?": measured from the journal and the session, answered directly when the residue is unambiguous
                target = self._why_target(user_text)
                if target:
                    call = ToolCall(new_id(), "explain_residue", {"residue": target})
                    self.conversation.append(Message("assistant", [call]))
                    res, payload = self._dispatch(call)
                    self.conversation.append(Message.tool_results([res]))
                    if isinstance(payload, dict) and not payload.get("error"):
                        text = self._explain_text(payload)
                        self.conversation.append(Message.assistant(text))
                        return TurnResult(text, len(self.conversation) - start_len, Usage(0, 0))
            if self.config.edition == "chimerax" and _TIDY_RE.search(user_text) and hasattr(self.executor, "label_layout"):
                # a complaint about labels is measured and fixed directly; the model then reports what happened
                call = ToolCall(new_id(), "tidy_labels", {})
                self.conversation.append(Message("assistant", [call]))
                res, payload = self._dispatch(call)
                self.conversation.append(Message.tool_results([res]))
                only_labels = not re.search(r"\b(and|then|also|color|show|hide|open|select)\b", user_text, re.I)
                if only_labels and isinstance(payload, dict):
                    if payload.get("error"):
                        text = "I could not tidy the labels: %s" % payload["error"]
                    else:
                        text = "Tidied %d labels: %d moved to a free spot, %d removed because they could not fit%s." % (
                            payload.get("labels", 0), payload.get("moved", 0), payload.get("removed", 0),
                            (" (" + ", ".join(payload.get("removed_labels", [])[:12]) + ")") if payload.get("removed") else "")
                        if payload.get("removed"):
                            text += " Ask for specific residues to label them again."
                    self.conversation.append(Message.assistant(text))
                    self._save_hook() if hasattr(self, "_save_hook") else None
                    return TurnResult(text, len(self.conversation) - start_len, Usage(0, 0))
            tools = tool_specs(self.config.allow_python,
                               self.config.vision and getattr(self.provider, "supports_vision", False),
                               tables=bool(self.tables), compact=self.compact)
            outcome = self._loop(tools, cancel, usage)
            if outcome.get("asked"):
                self.total_usage.add(usage)
                return TurnResult(self._last_assistant_text(), len(self.conversation) - start_len, usage, asked_user=True)
            for attempt in range(2):
                nudge = None
                last_ctx = context
                if self.config.nudge_on_no_action and looks_like_action_request(user_text):
                    ran = self._commands_since(start_len)
                    if not outcome.get("called_tool") and _WHICH_MODEL_RE.search(self._last_assistant_text()) and self._one_model_open():
                        nudge = NUDGE_ONE_MODEL   # asked which model although only one is open
                    elif not outcome.get("called_tool") and not self._tool_called_since(start_len, "tidy_labels"):
                        nudge = NUDGE          # words but no action
                    elif outcome.get("ended_after_error"):
                        nudge = NUDGE_AFTER_ERROR   # gave up after a failed command
                    elif _ONLY_RE.search(user_text) and not any(
                            (_HIDE_RE_CHIMERA if self.config.edition == "chimera" else _HIDE_RE).match(c) for c in ran):
                        nudge = NUDGE_ONLY_CHIMERA if self.config.edition == "chimera" else NUDGE_ONLY
                    elif _AA_TYPE_RE.search(user_text) and ran and not any(_AA_CLASS_RE.search(c) for c in ran) \
                            and not self._mentions_table(user_text):
                        nudge = NUDGE_AA       # residue-type coloring done with a wrong built-in scheme
                    elif _TIDY_RE.search(user_text) and not self._tool_called_since(start_len, "tidy_labels") and self.config.edition == "chimerax":
                        nudge = NUDGE_TIDY
                    elif _LOOK_RE.search(user_text) and self.config.vision and getattr(self.provider, "supports_vision", False) \
                            and not self._tool_called_since(start_len, "look_at_view"):
                        nudge = NUDGE_LOOK
                    elif _WHY_RE.search(user_text) and not self._tool_called_since(start_len, "explain_residue") and self.config.edition == "chimerax":
                        nudge = NUDGE_WHY
                    elif _ANNOT_RE.search(user_text) and not self._tool_called_since(start_len, "annotate"):
                        nudge = NUDGE_ANNOTATE  # variants/domains asked for, annotate tool never called
                    elif _NAMED_GROUP_RE.search(user_text) and ran and any(_NUMERIC_SPEC_RE.search(c) for c in ran) \
                            and not any(_NAMED_SPEC_RE.search(c) for c in ran) and not re.search(r"\b\d{2,}\b", user_text):
                        nudge = NUDGE_NAMED     # heme/ATP/water addressed by invented residue numbers
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
            if self.config.nudge_on_no_action and looks_like_action_request(user_text) and not outcome.get("asked") \
                    and not self._commands_since(start_len) and not any(c.name != "run_commands" for c in self._tool_calls_since(start_len)):
                self._commands_only_fallback(user_text, context, cancel, usage)
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
            reply, u = self._stream(tools, cancel)
            usage.add(u)
            if not reply.text().strip() and not reply.tool_calls():
                # empty answer (small local models do this occasionally): ask once more,
                # with thinking switched on for Ollama models, which reliably fixes it
                self._status("Empty answer, retrying…")
                opts = getattr(self.provider, "options", None)
                restore = None
                can_think = True
                try:
                    caps = self.provider.capabilities() if hasattr(self.provider, "capabilities") else None
                    can_think = caps is None or "thinking" in caps
                except Exception:  # noqa: BLE001
                    pass
                if getattr(self.provider, "name", "") == "ollama" and isinstance(opts, dict) and can_think:
                    restore = opts.get("think", None)
                    opts["think"] = True
                try:
                    reply, u = self._stream(tools, cancel)
                finally:
                    if isinstance(opts, dict) and getattr(self.provider, "name", "") == "ollama":
                        if restore is None:
                            opts.pop("think", None)
                        else:
                            opts["think"] = restore
                usage.add(u)
                if not reply.text().strip() and not reply.tool_calls():
                    reply = Message.assistant("The model returned an empty answer twice. Please try again or rephrase.")
            calls = reply.tool_calls()
            if not calls:
                # a tool call written as text (small models do this): run what was clearly meant, through the same gate
                from .recovery import parse_textual_tool_call
                cmds = parse_textual_tool_call(reply.text(), self._known_commands())
                if cmds:
                    call = ToolCall(new_id(), "run_commands", {"commands": cmds})
                    reply = Message("assistant", [TextPart(reply.text()), call])
                    calls = [call]
                    self.recovered_calls += 1
            self.conversation.append(reply)
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
                    reply, u = self._stream([], cancel)
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
        if _AA_TYPE_RE.search(user_text) and looks_like_action_request(user_text) and not self._mentions_table(user_text) \
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
            if self.tables and isinstance(state, dict):
                state["tables"] = self._tables_state()
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
            elif name == "explain_residue":
                payload = self.executor.residue_provenance(str(self._scalar(args.get("residue"), "") or ""), list(self.journal))
                result = ToolResult(call.id, name, json.dumps(payload, ensure_ascii=False), is_error="error" in payload)
            elif name == "save_figure":
                payload = self._figure_bundle(self.figures_dir or os.path.join(os.path.expanduser("~"), "Pellaeon figures"),
                                              str(self._scalar(args.get("name"), "") or ""), int(args.get("width") or 2400),
                                              int(args.get("height") or 1800), 3, bool(args.get("transparent", False)),
                                              str(self._scalar(args.get("closeup"), "") or ""), True, preapproved=False)
                result = ToolResult(call.id, name, json.dumps({k: v for k, v in payload.items() if k not in ("commands", "legend")}, ensure_ascii=False),
                                    is_error="error" in payload)
            elif name == "tidy_labels":
                payload = self._tidy_labels(str(self._scalar(args.get("keep"), "") or ""))
                result = ToolResult(call.id, name, json.dumps({k: v for k, v in payload.items() if k != "commands"}, ensure_ascii=False),
                                    is_error="error" in payload)
            elif name == "table_overlay":
                payload = self._table_overlay(str(args.get("dataset", "") or ""), str(args.get("column", "") or ""),
                                              args.get("chain") or None, str(args.get("palette", "") or ""),
                                              str(args.get("model", "") or ""), args.get("accession") or None, bool(args.get("label", False)))
                result = ToolResult(call.id, name, json.dumps({k: v for k, v in payload.items() if k != "commands"}, ensure_ascii=False),
                                    is_error="error" in payload)
            elif name == "annotate":
                payload = self._annotate(str(self._scalar(args.get("model"), "#1") or "#1"), str(self._scalar(args.get("accession"), "")),
                                         str(self._scalar(args.get("kind"), "variant") or "variant"), str(self._scalar(args.get("color"), "orange") or "orange"),
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

    def execute_commands(self, commands: List[str], origin: str = "model", preapproved: bool = False) -> Dict[str, Any]:
        """The single path for running commands: policy, repeat guard, execution, journal."""
        commands = split_joined([c.strip() for c in commands if c and c.strip()])
        if not commands:
            return {"ok": False, "results": [], "error": "No commands given."}
        for c in commands:
            for acc in re.findall(r"alphafold:([A-Za-z0-9]+)", c, re.I):
                if acc.upper() not in self._verified_accessions and acc.upper() not in self._user_text_upper:
                    # verify instead of refusing: does this accession belong to a gene/protein the user named?
                    gene = ""
                    try:
                        info = self.executor.protein_features(acc, ["Domain"]) or {}
                        gene = str(info.get("gene") or "")
                    except Exception:  # noqa: BLE001
                        info = {}
                    names = [gene] + [str(x) for x in (info.get("names") or [])]
                    if gene and any(n and n.upper() in self._user_text_upper for n in names):
                        self._verified_accessions.add(acc.upper())
                        continue
                    return {"ok": False, "results": [], "error": "Accession %s was NOT obtained from resolve_protein%s, so it may be the "
                            "wrong protein. Call resolve_protein with the gene/protein name first and use the open_command it returns."
                            % (acc, (" (UniProt says it is %s, which the user did not mention)" % gene) if gene else ""), "unverified_accession": acc}
            w = first_word(c)
            if self._impossible_ask and w in ("save", "export") and origin == "model":
                return {"ok": False, "results": [], "error":
                        "The user asked for something ChimeraX cannot do (emailing, printing, messaging or uploading). "
                        "Saving a file is NOT a step towards it. Run no commands: say in one sentence that ChimeraX cannot "
                        "do it, and if it helps, that they can save an image themselves and send it from their own email or "
                        "file manager.", "impossible": True}
            if w in _TOOL_NAMES:
                return {"ok": False, "results": [], "error": "'%s' is one of YOUR TOOLS, not a ChimeraX command. Call the tool "
                        "named %s with its arguments instead of running it as text." % (w, w), "tool_misuse": w}
        label_notes = []
        if self.config.readable_labels and self.config.edition == "chimerax":
            from .labels import style_label_command, label_spec
            styled = []
            for c in commands:
                n = None
                spec = label_spec(c)
                if spec and hasattr(self.executor, "count_residues"):
                    try:
                        n = self.executor.count_residues(spec)
                    except Exception:  # noqa: BLE001
                        n = None
                c2, note = style_label_command(c, n)
                if note:
                    label_notes.append(note)
                styled.append(c2)
            commands = styled
        repeats = [c for c in commands if c in self._failed_this_turn]
        if repeats:
            return {"ok": False, "results": [], "error": "You already ran exactly this command in this turn and it failed: %s. "
                    "Do not repeat it. Change it according to the usage/suggestion, or use a different approach." % repeats[0],
                    "repeated": True}
        pending = [] if preapproved else needs_confirmation(commands, self.config.autonomy)
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
            self.journal.append({"ts": now, "origin": origin, "command": r.get("command", ""), "ok": bool(r.get("ok")), "noop": bool(r.get("noop")),
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
                if self.layers and re.search(r"(?<!:):[A-Za-z_][A-Za-z0-9_]*\s*[<>=]", failed[0].get("command", "")):
                    tip = ((tip + " ") if tip else "") + "Table values are residue ATTRIBUTES, selected with two colons and the attribute name: " + \
                          ", ".join("`%s::%s>3`" % (l["model"], l["attr"]) for l in self.layers[-3:] if l.get("attr")) + \
                          ". E.g. `label %s::%s>3 residues; show %s::%s>3 atoms; style %s::%s>3 stick`." % ((self.layers[-1]["model"], self.layers[-1]["attr"]) * 3)
                if tip:
                    out["suggestion"] = tip
                out["hint"] = ("Run a corrected command now (follow the suggestion if there is one, else the usage). "
                               "Do not explain the fix in words without running it. If the option you wanted does not "
                               "exist, call search_docs for the task and use a different approach; do not invent options.")
            remaining = commands[len(results):]
            if remaining:
                out["not_run"] = remaining
        if label_notes:
            out["label_note"] = " ".join(label_notes)
        # outcome check: a command whose spec matches nothing changed nothing, even though ChimeraX did not complain
        if self.config.edition == "chimerax" and hasattr(self.executor, "spec_atoms"):
            noops = []
            for r in out.get("results", []):
                if not r.get("ok"):
                    continue
                cmd = r.get("command", ""); word = first_word(cmd)
                if word not in _CHECKED_WORDS:
                    continue
                try:
                    chk = self.executor.spec_atoms(cmd[len(word):].strip())
                except Exception:  # noqa: BLE001
                    continue
                if chk.get("used") and chk.get("atoms") == 0:
                    r["noop"] = True
                    r["warning"] = "matched nothing: '%s' selects 0 atoms" % chk["used"]
                    noops.append(cmd)
                elif chk.get("used") and chk.get("atoms") is not None:
                    r["matched"] = {"atoms": chk["atoms"], "residues": chk.get("residues", 0)}
            if noops:
                for j in self.journal[-len(out.get("results", [])):]:
                    if j.get("command") in noops:
                        j["noop"] = True
                out["no_effect"] = noops
                out["hint"] = ((out.get("hint") + " ") if out.get("hint") else "") + \
                    "These commands matched NOTHING and changed nothing: %s. The spec is wrong (wrong residue numbers, chain, model or name). " \
                    "Check with get_state or use residue names / built-in selectors, then run corrected commands." % "; ".join(noops)
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
        out.update(parse_rmsd_line(rmsd_line))   # fit subset vs all aligned pairs, stated separately
        if disp.get("unsupported"):
            out["note"] = disp.get("note", "Per-residue displacement is not available in this edition.")
            return out
        if disp.get("error"):
            out["error"] = disp["error"]
            return out
        out.update({k: v for k, v in disp.items() if k != "color_commands"})
        n_over = disp.get("residues_over_2A", 0); n_pairs = disp.get("paired_residues", 0)
        out["summary"] = ("%s superposed on %s: %d of %d compared residues moved more than 2 \u00c5 (mean C\u03b1 shift %.2f \u00c5, max %.1f \u00c5). "
                          "The fit used %s retained C\u03b1 pairs (fit RMSD %s \u00c5); over all %d compared residues the RMS shift is %.2f \u00c5." % (
                              prep["other_spec"], prep["ref_spec"], n_over, n_pairs, disp.get("mean_displacement", 0), disp.get("max_displacement", 0),
                              out.get("fit_pairs", "the"), out.get("fit_rmsd", "?"), n_pairs, disp.get("rms_displacement", 0)))
        out["report_verbatim"] = ("Report the 'summary' sentence as it is; the fit RMSD is NOT 'the RMSD between the structures'. "
                                  "Then name the segments with the largest shifts.")
        if disp.get("coloring"):
            self.figure_notes.append("Comparison: %s" % disp["coloring"])
        if str(disp.get("pairing", "")).startswith("chain id"):
            out["pairing_fallback"] = True
            out["pairing_note"] = ("Residues were paired by chain ID and residue number because no alignment was available. "
                                   "This is only meaningful if both structures use the same numbering; treat the displacement figures as approximate.")
        if disp.get("color_commands"):
            col = self.execute_commands(disp["color_commands"], origin="compare")
            out["colored"] = bool(col.get("ok"))
            out["commands"] = out["commands"] + col.get("results", [])
            if col.get("skipped"):
                out["note"] = "The user declined the coloring commands."
        return out

    def _mentions_table(self, text: str) -> bool:
        """True when the request names a loaded table or one of its columns (then 'hydrophobic' etc. refer to the data, not residue types)."""
        low = (text or "").lower()
        for n, t in self.tables.items():
            for term in [n] + list(t.get("columns") or []):
                term = str(term).lower()
                if len(term) >= 4 and term in low:
                    return True
        return False

    @staticmethod
    def _scalar(v, default=""):
        """Small models sometimes pass ['clinvar'] or "['clinvar']" for a string argument: take the first scalar."""
        if isinstance(v, (list, tuple)):
            v = v[0] if v else default
        if isinstance(v, str):
            m = re.match(r"^\s*\[\s*['\"]?([^'\"\],]+)['\"]?\s*(?:,.*)?\]\s*$", v)
            if m:
                v = m.group(1).strip()
        return v if v is not None else default

    def _figure_bundle(self, folder: str, name: str, width: int, height: int, supersample: int, transparent: bool,
                       closeup: str, include_session: bool, preapproved: bool) -> Dict[str, Any]:
        """Image(s) + session + script + colors + sources + draft legend, in one folder. Image/session saves go through
        execute_commands (so a model-initiated save still asks); the text files are written here."""
        import csv
        import datetime
        if not hasattr(self.executor, "figure_info"):
            return {"error": "Figure bundles need the ChimeraX edition."}
        info = self.executor.figure_info()
        if info.get("error"):
            return info
        if not info.get("models"):
            return {"error": "Nothing is open to save."}
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip()) or ("figure_" + "_".join(m["name"] for m in info["models"][:2]))
        outdir = os.path.join(folder, name)
        try:
            os.makedirs(outdir, exist_ok=True)
        except OSError as e:
            return {"error": "Cannot create %s: %s" % (outdir, e)}
        width, height = max(200, int(width)), max(200, int(height))
        q = lambda p: '"%s"' % p.replace('"', "")
        cmds = ['save %s width %d height %d supersample %d%s' % (q(os.path.join(outdir, name + ".png")), width, height, max(1, int(supersample)),
                                                                  " transparentBackground true" if transparent else "")]
        if closeup.strip():
            cmds += ["view %s" % closeup.strip(),
                     'save %s width %d height %d supersample %d%s' % (q(os.path.join(outdir, name + "_closeup.png")), width, height, max(1, int(supersample)),
                                                                    " transparentBackground true" if transparent else ""),
                     "view"]
        if include_session:
            cmds.append("save %s" % q(os.path.join(outdir, name + ".cxs")))
        run = self.execute_commands(cmds, origin="figure", preapproved=preapproved)
        if run.get("skipped"):
            return {"error": "The user did not approve saving the files.", "skipped": True}
        files = [os.path.basename(c.split('"')[1]) for c in cmds if c.startswith("save ") and '"' in c]
        # text files: script, colors, sources + metadata, draft legend
        from .agent import export_lines_for  # noqa: F401  (self-import guard for classic packaging)
        script = export_lines_for(self)
        script.insert(2, "# Figure bundle '%s', %s" % (name, datetime.date.today().isoformat()))
        for m in info["models"]:
            script.insert(3, "# %s %s: %s" % (m["id"], m["name"], m["source"]))
        with open(os.path.join(outdir, name + ".cxc"), "w", encoding="utf-8") as f:
            f.write("\n".join(script) + "\n")
        files.append(name + ".cxc")
        try:
            rows = self.executor.residue_colors() if hasattr(self.executor, "residue_colors") else []
        except Exception:  # noqa: BLE001
            rows = []
        if rows:
            with open(os.path.join(outdir, name + "_colors.csv"), "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f); w.writerow(["model", "chain", "number", "residue", "ribbon_color", "atom_color"]); w.writerows(rows)
            files.append(name + "_colors.csv")
        legend = self._legend_text(name, info)
        with open(os.path.join(outdir, name + "_legend.md"), "w", encoding="utf-8") as f:
            f.write(legend + "\n")
        files.append(name + "_legend.md")
        meta = {"name": name, "date": datetime.datetime.now().isoformat(timespec="seconds"), "chimerax": info.get("chimerax_version", ""),
                "pellaeon": getattr(self, "version", ""), "image": {"width": width, "height": height, "supersample": supersample, "transparent": transparent},
                "closeup": closeup.strip() or None, "models": info["models"], "background": info.get("background"), "camera": info.get("camera"),
                "labels": info.get("labels", 0), "tables": [{"dataset": l["dataset"], "column": l["column"], "legend": l.get("legend")} for l in self.layers],
                "notes": list(self.figure_notes), "commands_recorded": len(self.journal), "files": files}
        with open(os.path.join(outdir, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        files.append(name + ".json")
        out = {"folder": outdir, "files": files, "legend": legend, "commands": run.get("results", []), "ok": bool(run.get("ok")),
               "models": [m["source"] for m in info["models"]], "width": width, "height": height}
        if not run.get("ok"):
            out["note"] = "Some save commands failed; see the commands."
        return out

    def _legend_text(self, name: str, info: Dict[str, Any]) -> str:
        """Draft legend from recorded actions only."""
        who = {"model": "", "rerun": "", "annotate": " (annotation)", "table": " (table overlay)", "compare": " (comparison)", "tidy": ""}
        parts = ["# %s" % name, ""]
        srcs = ["%s (%s)" % (m["name"], m["source"]) if m["source"] != m["name"] else m["name"] for m in info["models"]]
        parts.append("Structure%s: %s." % ("s" if len(srcs) > 1 else "", "; ".join(srcs)))
        colors = [j for j in self.journal if j.get("ok") and not j.get("noop") and first_word(j.get("command", "")) in ("color", "rainbow", "coloring")]
        if colors:
            last = colors[-1]
            parts.append("Coloring: `%s`%s." % (last["command"], who.get(last.get("origin", ""), "")))
        for n in self.figure_notes[-4:]:
            parts.append(n.rstrip(".") + ".")
        for l in self.layers:
            parts.append("Table overlay %s / %s: %s." % (l["dataset"], l["column"], l.get("legend", "")))
        styles = [j["command"] for j in self.journal if j.get("ok") and first_word(j.get("command", "")) in ("cartoon", "style", "show", "hide", "surface", "transparency")][-3:]
        if styles:
            parts.append("Representation commands: " + "; ".join("`%s`" % c for c in styles) + ".")
        if info.get("labels"):
            parts.append("%d label%s shown." % (info["labels"], "" if info["labels"] == 1 else "s"))
        parts.append("Background %s. Rendered with ChimeraX %s; every command that produced this view is in %s.cxc." % (
            info.get("background", ""), info.get("chimerax_version", ""), name))
        parts.append("")
        parts.append("_Draft written from the recorded commands; edit before use._")
        return "\n".join(parts)

    def _tool_calls_since(self, start_len: int) -> List[ToolCall]:
        out: List[ToolCall] = []
        for m in self.conversation[start_len:]:
            if m.role == "assistant":
                out.extend(m.tool_calls())
        return out

    def _known_commands(self):
        kn = getattr(self, "_known_cache", None)
        if kn is None:
            kn = set()
            try:
                kn = {c for c, _ in (self.executor.knowledge.command_directory() or [])}
            except Exception:  # noqa: BLE001
                pass
            self._known_cache = kn
        return kn

    def _one_model_open(self) -> bool:
        try:
            return len((self.executor.get_state() or {}).get("models") or []) == 1
        except Exception:  # noqa: BLE001
            return False

    def _commands_only_fallback(self, user_text: str, context: str, cancel, usage: Usage) -> bool:
        """Last resort for models that cannot produce a structured tool call: ask for bare command lines and run them."""
        from .recovery import parse_command_lines
        self.conversation.append(Message.user(COMMANDS_ONLY, context))
        reply, u = self._stream([], cancel, on_delta=None)
        usage.add(u)
        cmds = parse_command_lines(reply.text(), self._known_commands())
        if not cmds:
            self.conversation.append(reply)
            return False
        call = ToolCall(new_id(), "run_commands", {"commands": cmds})
        self.conversation.append(Message("assistant", [TextPart(reply.text()), call]))
        res, payload = self._dispatch(call)
        self.conversation.append(Message.tool_results([res]))
        self.commands_only_turns += 1
        results = payload.get("results", []) if isinstance(payload, dict) else []
        ok = [r["command"] for r in results if r.get("ok") and not r.get("noop")]
        bad = [r for r in results if not r.get("ok")]
        text = "Ran: " + "; ".join("`%s`" % c for c in ok) + "." if ok else "Nothing ran."
        if bad:
            text += " Failed: " + "; ".join("`%s` (%s)" % (r["command"], (r.get("error") or "")[:80]) for r in bad)
        if isinstance(payload, dict) and payload.get("skipped"):
            text = "The commands were shown for your approval and skipped."
        self.conversation.append(Message.assistant(text))
        return True

    def _why_target(self, user_text: str) -> str:
        """The residue a 'why is this ...' question is about: a spec in the text, else the single selected residue."""
        m = _RES_SPEC_RE.search(user_text)
        if m:
            return m.group(0)
        try:
            sel = (self.executor.get_state() or {}).get("selection") or {}
        except Exception:  # noqa: BLE001
            sel = {}
        spec = sel.get("spec") or ""
        if sel.get("num_residues") == 1 and spec:
            return spec
        m2 = re.search(r"\bresidue\s+(\d+)\b", user_text, re.I)
        if m2 and sel.get("num_residues") != 1:
            return ":" + m2.group(1)
        return ""

    @staticmethod
    def _explain_text(p: Dict[str, Any]) -> str:
        who = {"model": "from your request", "rerun": "re-run by you", "annotate": "by the annotation", "table": "by the table overlay",
               "compare": "by the comparison", "tidy": "by tidy labels"}
        parts = ["**%s** (chain %s, %s)" % (p.get("residue"), p.get("chain"), p.get("model"))]
        look = []
        if p.get("ribbon_color"):
            look.append("ribbon %s" % p["ribbon_color"])
        if p.get("atoms_shown"):
            look.append("%d atoms shown, mostly %s" % (p["atoms_shown"], p["atom_colors"][0][0]))
        if p.get("labels"):
            look.append("label \"%s\"" % "\", \"".join(p["labels"]))
        if p.get("selected"):
            look.append("selected")
        parts.append("Now: " + (", ".join(look) if look else "not displayed") + ".")
        lc = p.get("last_color_command")
        if lc:
            parts.append("Color set by `%s` (%s, %d commands ago%s)." % (lc["command"], who.get(lc["origin"], lc["origin"]), lc["commands_ago"],
                                                                     "; that command applied to everything" if lc.get("everything") else ""))
        else:
            parts.append("No recorded Pellaeon command colored it: this is the color it had when opened (ChimeraX's default, e.g. by chain) or a change made outside Pellaeon.")
        attrs = p.get("attributes") or {}
        for k, v in attrs.items():
            if k == "pellaeon_disp":
                parts.append("Displacement from the last comparison: %.2f Å." % float(v))
            else:
                parts.append("Table value %s = %s." % (k.replace("pellaeon_", "", 1), v))
        others = [h for h in (p.get("history") or []) if h is not lc][:4]
        if others:
            parts.append("Other commands that touched it: " + "; ".join("`%s` (%s)" % (h["command"], who.get(h["origin"], h["origin"])) for h in others) + ".")
        return "\n\n".join(parts)

    def _tidy_labels(self, keep: str = "") -> Dict[str, Any]:
        """Declutter the 3D labels: project to screen, pack, then move/remove through execute_commands."""
        from .labels import pack_labels, SIZE
        if not hasattr(self.executor, "label_layout"):
            return {"error": "Tidying labels needs the ChimeraX edition."}
        lay = self.executor.label_layout()
        if lay.get("error"):
            return lay
        labels = lay.get("labels") or []
        if not labels:
            return {"error": "There are no labels to tidy. Label something first (e.g. `label sel`)."}
        keep_specs = set()
        if keep and hasattr(self.executor, "list_residues"):
            pass   # kept labels are simply packed first (see below)
        boxes = [(i, l["x"], l["y"], l["w"], l["h"]) for i, l in enumerate(labels)]
        if keep:
            k = keep.replace(" ", "")
            boxes.sort(key=lambda b: 0 if k and k in labels[b[0]]["spec"].replace(" ", "") else 1)
        packed = pack_labels(boxes)
        cmds: List[str] = []
        moved = []
        for i, (dx, dy) in packed["moved"].items():
            l = labels[i]
            ox, oy, oz = l["offset"]
            off = (ox + dx * l["per_px"], oy + dy * l["per_px"], oz)
            cmds.append("label %s %s offset %.2f,%.2f,%.2f" % (l["spec"], l["level"], off[0], off[1], off[2]))
            moved.append(l["text"])
        dropped = [labels[i] for i in packed["dropped"]]
        if dropped:
            by_level: Dict[str, List[str]] = {}
            for l in dropped:
                by_level.setdefault(l["level"], []).append(l["spec"])
            for level, specs in by_level.items():
                cmds.append("label delete %s %s" % (" ".join(specs), level))
        kept_specs = " ".join(labels[i]["spec"] for i in packed["kept"])
        if kept_specs:
            cmds.append("label %s size %d height fixed onTop true color black bgColor #ffffffd9" % (kept_specs, SIZE))
        run = self.execute_commands(cmds, origin="tidy") if cmds else {"ok": True, "results": []}
        return {"labels": len(labels), "kept": len(packed["kept"]), "moved": len(moved), "removed": len(dropped),
                "removed_labels": [l["text"] for l in dropped][:40], "commands": run.get("results", []), "ok": bool(run.get("ok")),
                "note": ("%d overlapping labels were removed; ask for specific residues to label them again." % len(dropped)) if dropped else ""}

    def _tables_state(self) -> List[Dict[str, Any]]:
        return [{"name": n, "rows": len(t.get("rows") or []), "columns": t.get("columns") or [],
                 "attributes": ["%s::%s" % (l["model"], l["attr"]) for l in self.layers if l.get("dataset") == n and l.get("attr")],
                 "position_column": (t.get("columns") or [""])[t["guess"]["position"]] if t.get("guess") and t["guess"].get("position") is not None else ""}
                for n, t in self.tables.items()]

    def _table_overlay(self, dataset: str, column: str, chain: Optional[str], palette: str, model: str,
                       accession: Optional[str], label: bool) -> Dict[str, Any]:
        """Color a model by a column of a user-loaded table; every scene change goes through execute_commands."""
        from .tables import table_rows, plan_overlay, attr_name, _norm
        if not self.tables:
            return {"error": "No table is loaded. The user can load a CSV/TSV with the 'Import table' button in the panel header."}
        ds = self.tables.get(dataset)
        if ds is None and (not dataset or len(self.tables) == 1):
            ds = next(iter(self.tables.values()))
        if ds is None:
            ds = next((t for t in self.tables.values() if dataset.lower() in t["name"].lower()), None)
        if ds is None:
            return {"error": "No table named '%s'. Loaded tables: %s" % (dataset, ", ".join(self.tables))}
        cols, g = ds["columns"], ds["guess"]
        ci = None
        if column:
            ci = next((i for i, c in enumerate(cols) if c == column), None)
            if ci is None:
                ci = next((i for i, c in enumerate(cols) if _norm(c) == _norm(column)), None)
        elif g.get("default_value") is not None:
            ci = g["default_value"]
        if ci is None or ci == g.get("position"):
            return {"error": "Table '%s' has no value column '%s'. Columns: %s" % (ds["name"], column, ", ".join(cols))}
        model = model or "#1"
        res = self.executor.list_residues(model)
        if res.get("error"):
            return res
        resmap = {(k.split(":")[0], int(k.split(":")[1])): v for k, v in res["residues"].items()}
        chain_col = None if chain else g.get("chain")
        rows = table_rows(ds, g, ci, chain_col)
        if not rows:
            return {"error": "Column '%s' has no usable values." % cols[ci]}
        numbering = "structure residue numbers"
        numbering_map = None
        acc = (accession or ds.get("accession") or "").strip()
        if not acc and g.get("accession") is not None:
            acc = next((r[g["accession"]] for r in ds["rows"] if r[g["accession"]]), "")
        if acc:
            mp = self.executor.map_positions(res["model"], acc, sorted({r["position"] for r in rows}))
            if mp.get("error"):
                numbering += " (UniProt %s could not be mapped: %s)" % (acc, mp["error"])
            else:
                numbering_map = {int(k): v for k, v in (mp.get("map") or {}).items()}
                offs = mp.get("offsets") or {}
                numbering = "UniProt %s numbering, offsets %s" % (acc, ", ".join("%s:%+d" % (c, o) for c, o in sorted(offs.items())) or "0")
        attr = attr_name(ds["name"], cols[ci])
        plan = plan_overlay(rows, resmap, res["model"], attr, chains=[chain] if chain else None,
                            palette=palette or "blue-white-red", numbering_map=numbering_map, edition=self.config.edition)
        base = {"dataset": ds["name"], "column": cols[ci], "model": res["model"], "numbering": numbering}
        if plan.get("error"):
            return dict(base, **plan)
        if plan["numeric"]:
            r = self.executor.set_residue_attr(res["model"], attr, plan["assignments"])
            if r.get("error"):
                return dict(base, error=r["error"])
        cmds = list(plan["commands"])
        if label:
            by_chain: Dict[str, List[int]] = {}
            for k in list(plan["assignments"])[:60]:
                c, n = k.split(":")
                by_chain.setdefault(c, []).append(int(n))
            cmds += ["label %s/%s:%s" % (res["model"], c, ",".join(str(n) for n in sorted(ns))) for c, ns in sorted(by_chain.items())]
        run = self.execute_commands(cmds, origin="table")
        out = dict(base, attribute=attr, select_syntax="%s::%s>0.5  (residue attribute selector: TWO colons + the attribute name; works with <, >, =)" % (res["model"], attr),
                   mapped=plan["mapped"], chains=plan["chains"], n_missing=plan["n_missing"], missing=plan["missing"][:30],
                   n_mismatches=plan["n_mismatches"], mismatches=plan["mismatches"][:10], legend=plan["legend"],
                   numeric=plan["numeric"], commands=run.get("results", []), colored=bool(run.get("ok")))
        if run.get("skipped"):
            out["note"] = "The user declined the coloring commands."
        elif run.get("ok"):
            layer = {"dataset": ds["name"], "column": cols[ci], "model": res["model"], "chain": chain or "", "palette": palette or "blue-white-red", "attr": attr,
                     "accession": acc, "legend": plan["legend"], "mapped": plan["mapped"]}
            self.layers = [l for l in self.layers if not (l["dataset"] == layer["dataset"] and l["column"] == layer["column"])] + [layer]
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
            self.figure_notes.append("ClinVar variants of %s: %s" % (gene, out["legend"]))
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


def export_lines_for(agent, edition: str = "chimerax") -> List[str]:
    """Replayable script from the execution journal (same format as the panel's export)."""
    head = "# ChimeraX command script exported by Pellaeon" if edition != "chimera" else "# Chimera command script exported by Pellaeon Classic"
    lines = [head, ""]
    for j in agent.journal:
        if j.get("ok") and not j.get("noop"):
            lines.append(j["command"])
        elif j.get("ok"):
            lines.append("# matched nothing: %s" % j["command"])
        else:
            lines.append("# failed: %s   (%s)" % (j.get("command", ""), (j.get("error") or "")[:80].replace("\n", " ")))
    return lines
