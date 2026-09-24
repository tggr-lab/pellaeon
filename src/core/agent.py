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
from typing import Tuple, Any, Callable, Dict, List, Optional

from . import prompt as prompt_mod
from .fixups import suggest, rewrite as rewrite_command
from .safety import AUTONOMY_AUTO, AUTONOMY_ALL, Classification, first_word, needs_confirmation, split_commands
from .schema import (Message, TextPart, ToolCall, ToolResult, Usage, estimate_tokens, new_id)
from .recovery import split_joined, _looks_like_prose
from .tools import tool_specs
from .annotate import CLINVAR_KINDS
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
    initiative: str = "minimal"        # "minimal": exactly what was asked; "initiative": small extras allowed
    guards: bool = True                # the 2026-09-23 deterministic guards (ablation: harness PELLAEON_GUARDS=off)


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
# "hydrophobic" alone used to count: "color its surface by hydrophobicity" then got the residue-class
# recolor on top of a correct `mlp`, which wiped the lipophilicity gradient. Only a class contrast counts now.
_AA_TYPE_RE = re.compile(r"\b(by|per)\s+(the\s+)?(type\s+of\s+)?(aa|amino\s*acids?|residues?)(\s+type)?\b|\bresidue[- ]type\b|"
                         r"\bhydrophobic\s+(residues?\s+)?(and|,|vs\.?|versus|or|from)\s+(the\s+)?(polar|hydrophilic|charged)", re.I)
_MLP_RAN_RE = re.compile(r"^\s*(mlp|color\s+byattr\w*\s+(r:)?mlp|coulombic)\b", re.I)
_AA_CLASS_RE = re.compile(r":(ala|asp|lys|ser|glu|arg|leu|val)\b|byattr", re.I)
_ANNOUNCE_RE = re.compile(r"\b(i'?ll|i will|let me|i am going to|i'm going to|going to)\b", re.I)

NUDGE_SAVE_CLAIM = ("(system) Your reply says the file was saved, but no save command ran, so NOTHING was saved. If the user "
                    "asked for a file, run the save now (e.g. save ~/Desktop/image.png supersample 3; the interface asks them "
                    "to confirm). Otherwise correct your reply.")
_SAVE_DENIED_RE = re.compile(r"\b(not|nothing|never|no file|wasn't|isn't|hasn't|cannot|can't|couldn't)\b(\s+\w+){0,2}\s+(saved|exported)", re.I)
_SAVE_CLAIM_RE = re.compile(r"\b(saved|exported|written to|wrote (it|the))\b", re.I)
NUDGE_SEQID = ("(system) The user asked about sequence identity/similarity. Do NOT type `sequence identity` or `sequence "
               "align` as commands: CALL THE TOOL named sequence_identity (models: e.g. '#1-4'). It returns the table.")
NUDGE_CLOSEWIN = ("(system) The user wants the sequence/alignment windows closed. ChimeraX has no command for it: CALL THE "
                  "TOOL named close_windows (which='opened' for the windows your commands opened; 'sequence' for all of them).")
_CLOSE_WIN_RE = re.compile(r"\b(close|get rid of|remove|dismiss)\b.*\b(sequence|alignment|seq)\w*\s*(windows?|viewers?|panels?|tabs?)\b", re.I)
_PAE_ASK_RE = re.compile(r"\bpae\b|predicted (aligned error|domains?)|rigid(-| )?(body )?domains?", re.I)
NUDGE_UNDO = ("(system) The user wants the WHOLE last request undone (not just one command). CALL THE TOOL named "
             "undo_last_request (no arguments); do not try to reconstruct the earlier state by hand with commands.")
# Deliberately narrow: plain "undo that"/"undo it" after a single reversible change (e.g. a color) is still the
# ChimeraX `undo` command, which handles it. This tool and nudge are for undoing the REQUEST as a whole: several
# commands, an open, a close, or anything plain `undo` cannot reach.
_UNDO_RE = re.compile(r"\bundo (the |my )?(last|previous|whole|entire) request\b|\bundo (all|everything)\b|"
                      r"\brevert (the |my )?(last|previous|whole|entire) request\b|"
                      r"\b(go|put (it|that|everything|things|this)) back to (how it was |the way it was )?before\b|"
                      r"\bput (it|that|everything|things) back\b(?!\s+(to|in|on|into)\b)|"
                      r"\bundo everything (you|i) (just )?(did|asked|changed)\b|"
                      r"\bwhole (thing|request) was wrong\b", re.I)
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
_IMPOSSIBLE_RE = re.compile(r"\b(e-?mail|fax|whatsapp|text (me|it|this|them|him|her|us)|message me|call me|"
                            r"print (it|this|them)( out)?|printer|send (it|this|them|me|the \w+)\s+(to|over|the)|"
                            r"upload (it|this)|post (it|this) (to|on)|share (it|this) (with|on)|tweet|"
                            r"order (a|an|the|some|more|new)?\s*\w*\s*(reagent|kit|plasmid|antibod\w*|primer\w*|from)|order (me|us)\b|"
                            r"from an? (vendor|supplier|company)|"
                            r"buy|purchase|ship (it|this|them))\b", re.I)
# does the request also ask for something ChimeraX can do? "color it red and email it" keeps its color
_ACTION_RE = re.compile(r"\b(colou?r|show|hide|display|open|load|fetch|select|label|rotate|spin|turn|roll|rock|zoom|"
                        r"view|focus|center|centre|measure|distance|style|stick|sphere|ball|cartoon|ribbon|surface|"
                        r"transparen\w*|save|export|render|picture|image|figure|movie|close|delete|remove|compare|"
                        r"superpose|align|annotate|highlight|paint|rainbow|background|light\w*|silhouette\w*|"
                        r"undo|reset|bookmark|clip|slab|contact\w*|h-?bond\w*|clash\w*|mutat\w*|swap)\b", re.I)

_SAVE_WANTED_RE = re.compile(r"\b(save|export|write|download|png|jpe?g|tiff|figure file|to my (desktop|folder|computer))\b", re.I)

_REMOTE_SCRIPT_RE = re.compile(r"\bhttps?://\S+\.(?:cxc|py|pyc|cxs)(?:\.(?:gz|bz2|xz|zip))?(?:\s|$|[?#])", re.I)

# RBVI's own recipes repository defines commands ChimeraX lacks (convexhull, color smooth, ...);
# 21 entries of our recipe library need it, and the safety gate still asks before it runs.
_RBVI_RECIPE_RE = re.compile(r"https?://raw\.githubusercontent\.com/rbvi/chimerax-recipes/", re.I)

_DISTANCE_RE = re.compile(r"^distance\s+(?!style\b|delete\b|save\b|format\b)\S", re.I)

# Commands that open a tool window. Asked for the sequence identity of four structures, a model opened one
# alignment viewer per pair until ChimeraX was taller than the screen, and had no command to close them.
_WINDOW_CMD_RE = re.compile(r"^\s*(sequence\s+(align|chain|viewer)|seq\s+(align|chain)|ui\s+tool\s+show|tool\s+show|"
                            r"toolshed\s+show|blastprotein|alphafold\s+pae|interfaces\b)", re.I)
_SEQ_WINDOW_RE = re.compile(r"^\s*(sequence|seq)\s+(align|chain|viewer|identity)\b", re.I)
_IDENTITY_ASK_RE = re.compile(r"\bidentit|\b(sequence|seq)s?\s+(similarity|similar)|\bhow similar\b.*\bsequences?\b", re.I)
WINDOW_CAP = 3
_WANTS_VIEWER_RE = re.compile(r"\balign(ment|ed)?\b|\bviewer\b|\b(see|show|display|open)\b.{0,20}\bsequences?\b", re.I)
_HARMLESS_TOOLS = {"Distances", "Log", "Help Viewer", "Command Line Interface", "Pellaeon"}

# Opening what is already open. Asked to "open 4xt3 and color its surface by hydrophobicity" with 4xt3
# loaded, or to split "this AlphaFold model" into PAE domains, models opened a second copy and then
# worked on the wrong one (5 of 152 real-world requests).
_OPEN_TARGET_RE = re.compile(r"^\s*(?:open\s+(?:pdb:)?([0-9][a-z0-9]{3})\s*$|open\s+alphafold:([a-z0-9_-]+)\b|"
                             r"alphafold\s+fetch\s+([a-z0-9_-]+)\b)", re.I)
_OPEN_AGAIN_RE = re.compile(r"\b(again|another|second|copy|copies|twice|re-?open|re-?load|duplicate|fresh)\b", re.I)

# Presets restyle everything at once. "make the background white for the figure" became the publication
# preset and "switch everything to sticks" became `preset sticks` (real-world set, ministral-8b).
_PRESET_ASK_RE = re.compile(r"\bpresets?\b|publication|paper[- ]?ready|journal|pretty|fancy|beautiful|professional|"
                            r"look (nice|good|better|great|clean)|make (it|this|them) look|nice (figure|image|picture)|"
                            r"figure[- ]ready|cartoon look|initial look", re.I)

# A narrow command followed by a whole-model one with the same verb is undone by it: "imatinib as
# spheres and everything else as sticks" ran `style ligand sphere` then `style #1 stick` (7 real requests).
_ORDER_VERBS = ("style", "color", "colour", "show", "transparency", "size")
_NARROW_SPEC_RE = re.compile(r"[:/@~]|\b(ligands?|protein|nucleic|solvent|ions|sel|helix|strand|coil|backbone|sidechain|"
                             r"main|water|het\w*|by(het\w*|element))\b|\bzone\b|&", re.I)
# ChimeraX has no :end/:first/:last; `select #1/A:end` succeeds and selects nothing, so no error hint fires.
_TERMINUS_RE = re.compile(r"(?<=[:,])(end|last|first|c-?term\w*|n-?term\w*)\b(?!\s*-)", re.I)   # bare :end or :1,end; :70-end is valid
_CHAIN_SPEC_RE = re.compile(r"(?:^|[\s#\d.,])/[A-Za-z0-9]{1,4}(?=[:@\s,&]|$)")   # a chain spec, not a file path
_NO_CHAIN_CHECK = ("save", "open", "movie", "2dlabels", "cd", "log", "close")
_USER_CHAINS_RE = re.compile(r"\bchains?\b|/[A-Za-z]", re.I)
_PRONOUN_RE = re.compile(r"\b(it|this|that|this one|that one)\b", re.I)
_WHICH_NAMED_RE = re.compile(r"#\d|\bmodels?\s*#?\d|\b(all|both|them|those|these|every\w*|each|first|second|third|"
                             r"last|other|older|newer|reference|apo|holo|bound|unbound)\b", re.I)
_ACTION_VERBS = ("color", "colour", "style", "show", "hide", "cartoon", "surface", "transparency", "label", "rainbow")

# After annotate / fetch_annotation / table_overlay the coloring, key and title are already drawn. Asked to
# "color ADRB2 by AlphaMissense", ministral then recolored by hand with another palette, deleted the key,
# ran `key pellaeon_alphamissense_...` (read by ChimeraX as a palette name: a web lookup that fails) and
# told the user the key could not be added. 4 of 4 runs.
_REDO_OVERLAY_RE = re.compile(r"^\s*(colou?r\s+byattr\w*|cartoon\s+byattr\w*|key\s+(?!delete\b)|2dlabels\s+(create|change)|"
                              r"mutationscores\b|open\b.*\b(alpha_?missense|amiss)\b)", re.I)   # incl. ChimeraX's own AlphaMissense route

# Per-residue data sources a request can name. A small model asked for ClinVar reached for AlphaMissense
# as well (or instead) in 3 of 8 phrasings, and once described the AlphaMissense coloring as ClinVar.
_SOURCE_NAMED_RE = {
    "clinvar": re.compile(r"\bclin\s?var\b|\b(known|reported|clinical|disease)\s+(missense\s+)?(variants?|mutations?)\b", re.I),
    "alphamissense": re.compile(r"alpha\s?missense|\bpredicted\s+(to be\s+)?pathogenic\w*|\bpathogenicity\s+(score|prediction)|\bcolou?r\w*\s+(it\s+|this\s+)?by\s+pathogenicity", re.I),
    "conservation": re.compile(r"\bconserv|\bconsurf\b", re.I),
    "uniprot variants": re.compile(r"\buniprot\b.*\bvariant|\bnatural variants?\b", re.I),
}
_SOURCE_HOW = {"clinvar": "annotate with kind 'clinvar'", "alphamissense": "fetch_annotation with source 'alphamissense'",
               "conservation": "fetch_annotation with source 'conservation'", "uniprot variants": "annotate with kind 'variant'"}
_SOURCE_LABEL = {"clinvar": "ClinVar variants", "alphamissense": "AlphaMissense pathogenicity",
                 "conservation": "ConSurf conservation", "uniprot variants": "UniProt variants"}


_TIDY_RE = re.compile(r"\blabels?\b.*\b(overlap|unreadable|readable|too (small|many|big)|tidy|clean|declutter|mess)|\b(tidy|clean up|declutter)\b.*\blabels?\b", re.I)
NUDGE_TIDY = ("(system) The user is complaining about the LABELS (overlap, readability, clutter). Do not re-run label commands "
              "with guesses: CALL THE TOOL named tidy_labels (optionally with keep=<spec>). It measures the overlaps on screen and fixes them.")
_ANNOT_RE = re.compile(r"\b(clinvar|variants?|mutations?|domains?|transmembrane|binding sites?|active sites?|glycosylation|disulfides?)\b", re.I)

_TOOL_NAMES = {"annotate", "compare_structures", "table_overlay", "tidy_labels", "explain_residue", "save_figure", "resolve_protein", "protein_features", "search_docs", "get_state",
               "command_usage", "run_python", "look_at_view", "ask_user", "run_commands", "map_numbering", "apply_figure_style", "compare_contacts", "fetch_annotation",
               "sequence_identity", "close_windows", "membrane_view", "gpcr_states", "view_axis", "undo_last_request"}

_COMPLAINT_RE = re.compile(
    r"\b(did ?n[o']?t|does ?n[o']?t|not work(ing)?|nothing (happened|changed)|no(t)? (you|it) (did|does)|you did not|"
    r"that'?s (wrong|not it)|wrong|not what i|still the same|no change|nope|it'?s not|isn'?t|aren'?t|are they though|"
    r"are they thou|try (again|something else|a different)|didn'?t work)\b")


# Information questions (which residues form a strand, whether a prediction can be trusted, whether an edit is acceptable): the answer
# is words, and small models answered them by recoloring, restyling or hiding dust. Reading and measuring commands
# stay allowed; "how do I ...", "can I ...", "is there a way ..." are requests to do it and are not covered.
_INFO_Q_RE = re.compile(r"^(what|what's|whats|which|where|wheres|why|who|whose|when|is|are|was|were|does|did|am|"
                        r"how\s+(many|much|big|large|far|close|long|good|well|confident|reliable|accurate|similar|"
                        r"different|old|deep|wide|thick|flexible|stable|buried|exposed)|tell me|explain|describe)\b", re.I)
_REQUEST_Q_RE = re.compile(r"^(how (do|can|could|would|should|to) (i|we|you)|how to|can|could|would|will|please|"
                           r"is there|are there|is it possible|do you|what (command|commands)|what'?s the command)\b", re.I)
_ASKS_ACTION_RE = re.compile(r"\b(can|could|would|will) you\b|\bshow me\b|\bplease\b|\b(and|then|also)\s+"
                             r"(colou?r|show|hide|label|make|display|zoom|highlight|select|open|mark)\b", re.I)
_SCENE_CMD_RE = re.compile(r"^\s*~?(color|colour|rainbow|surface|hide|show|display|style|cartoon|ribbon|delete|close|save|"
                           r"set|lighting|light|preset|label|2dlab\w*|transparency|swapaa|addh|coulombic|mlp|matchmaker|mm|"
                           r"align|morph|turn|move|roll|rock|volume|key|combine|sym|graphics|camera|clip|size|build|material|"
                           r"nucleotides|bumps|movie|split|rename|changechains|renumber)\b", re.I)


def question_only(text: str) -> bool:
    """True when every sentence of the request asks for information and none asks for a change."""
    t = (text or "").strip()
    if not t or _ASKS_ACTION_RE.search(t):
        return False
    parts = [p.strip() for p in re.split(r"[?.!;\n]+", t) if p and p.strip()]
    return bool(parts) and all(_INFO_Q_RE.match(p) and not _REQUEST_Q_RE.match(p) for p in parts)


# The user wants the command in words and says not to execute it: a small model ran the nearest thing anyway.
_DONT_RUN_RE = re.compile(r"\b(don'?t|do not|dont|without)\s+(actually\s+)?(run|running|execute|executing|apply|applying)\b|"
                          r"\bjust (tell|give|show) me the (command|syntax)\b|\btell me the command\b", re.I)

# Commands that open a tool window. The window cap above stops a pile of them; these are refused outright
# unless the user asked to see a window: `coordset slider` after a morph, `sequence chain` to read a
# sequence the model could have looked up, each counted against a user who asked for neither.
_UNASKED_WINDOW_RE = re.compile(r"^\s*(coordset\s+slider|mseries\s+slider|sequence\s+(chain|align|viewer)|seq\s+(chain|align)|"
                                r"ui\s+tool\s+show|tool\s+show)\b", re.I)
_ASKS_WINDOW_RE = re.compile(r"\balign(ment|ed)?\b|\bviewer\b|\bwindow\b|\bpanel\b|\bslider\b|\bplot\b|\bdialog\b|"
                             r"\b(see|show|display|open|read)\b.{0,20}\bsequences?\b", re.I)

# After `open 9zzz` fails, one model opened an unrelated entry (6n2y) and described it as the result.
_OPEN_ID_RE = re.compile(r"^\s*open\s+(?:pdb:|alphafold:|emdb:)?([0-9][a-z0-9]{3}|[a-z][0-9][a-z0-9]{3,8}|emd[-_]?\d+|\d{4,5})\b", re.I)
_OPEN_FAILED_RE = re.compile(r"404|not found|no such|failed|could not|couldn't|does not exist|unknown|invalid", re.I)

# Community recipes are retrieved by word overlap; a model ran the z-mirroring `flip` recipe when a rotation was meant
# and the same recipe for a GROMACS topology request. A recipe must share a content word with the request.
_RECIPE_DIR_RE = re.compile(r"chimerax-recipes/(?:master|main)/([^/\s]+)/", re.I)
_RECIPE_STOP = set("""this that with from have what when your into them they their there where which while about
show make color colour chimerax model models structure structures protein proteins residue residues chain chains open
using used user want would could should like just please into onto also then than more most some each every other
command commands script recipe file files view image""".split())

# A model writes our tool names as ChimeraX commands: `membrane_view #1`, `gpcr_states protein=P25116`.
# These are dispatched as the tool they name (a `#1` goes to the tool's model argument).
_MODEL_ARGS = ("model", "models", "reference", "other", "residue", "keep")
_NOT_DISPATCHED = {"run_commands", "ask_user", "run_python"}


def _coerce(value: str, schema: Dict[str, Any]):
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    typ = (schema or {}).get("type")
    if v.startswith("[") and v.endswith("]"):
        items = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip().strip("\"'")]
        return items if typ in ("array", None) else (items[0] if items else "")
    if typ == "boolean":
        return v.lower() in ("true", "t", "1", "yes", "on")
    if typ == "integer":
        try:
            return int(float(v))
        except ValueError:
            return v
    if typ == "number":
        try:
            return float(v)
        except ValueError:
            return v
    if typ == "array":
        return [x.strip() for x in v.split(",") if x.strip()]
    return v


def parse_tool_line(line: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """`gpcr_states protein=P25116 open_states=[inactive,active]` -> ('gpcr_states', {...}). None when the line does
    not start with one of our tools, or the arguments the tool requires cannot be read from it."""
    from . import tools as tools_mod
    from .schema import ToolSpec
    m = re.match(r"^\s*([a-z_]+)\b(.*)$", line or "", re.S)
    if not m:
        return None
    name, rest = m.group(1).lower(), m.group(2)
    # every ToolSpec the tools module defines, listed in ALL_TOOLS or not (a new tool must not need two edits)
    spec = next((t for t in vars(tools_mod).values() if isinstance(t, ToolSpec) and t.name == name), None)
    if spec is None or name in _NOT_DISPATCHED:
        return None
    props = (spec.parameters or {}).get("properties") or {}
    required = list((spec.parameters or {}).get("required") or [])
    args: Dict[str, Any] = {}
    # key=value anywhere; key: value only for a real argument name not inside a spec (#1/A:87 is a residue)
    kv = re.compile(r"(?<![\w/#:@.])([A-Za-z_]+)\s*(=|:)\s*(\[[^\]]*\]|\"[^\"]*\"|'[^']*'|[^\s,]+)")

    def take(mo):
        k, sep, v = mo.group(1), mo.group(2), mo.group(3)
        if k in props:
            args[k] = _coerce(v, props[k])
            return " "
        return " " if sep == "=" else mo.group(0)
    loose = kv.sub(take, rest)
    tokens = re.findall(r"\"[^\"]*\"|'[^']*'|\[[^\]]*\]|\S+", loose)
    specs = [x for t in tokens if t.startswith(("#", "/", ":")) for x in re.split(r",(?=[#/])", t) if x]
    words = [t for t in tokens if not t.startswith(("#", "/", ":")) and t.strip(",")]
    model_slots = [p for p in _MODEL_ARGS if p in props and p not in args]
    if "models" in model_slots and specs:
        args["models"] = " ".join(specs)
        specs = []
    for p in model_slots:
        if not specs:
            break
        if p != "models":
            args[p] = specs.pop(0)
    free = [p for p in required + list(props) if p not in args and p not in _MODEL_ARGS
            and (props.get(p) or {}).get("type") in ("string", "array", None)]
    seen = set()
    free = [p for p in free if not (p in seen or seen.add(p))]
    i = 0
    while i < len(words):
        w = words[i].strip(",")
        if w.lower() in props and i + 1 < len(words):     # `kind clinvar`: an argument name followed by its value
            if w.lower() not in args:
                args[w.lower()] = _coerce(words[i + 1].strip(","), props[w.lower()])
                if w.lower() in free:
                    free.remove(w.lower())
                i += 2
            else:                                           # `#1 model P68871`: the name repeats a value already read
                i += 1
            continue
        if free:
            p = free.pop(0)
            args[p] = _coerce(w, props.get(p) or {})
        i += 1
    if any(r not in args for r in required):
        return None
    return name, args


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
        self._impossible_only: bool = False
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
            edition=self.config.edition, compact=self.compact, initiative=self.config.initiative)

    def go_compact(self) -> bool:
        """Switch to the short prompt and shrink what was already built. False if already compact."""
        if self.compact:
            return False
        self.compact = True
        if not self._fixed_system:
            self._system = self._build_system()
        for m in reversed(self.conversation):    # adapters send the context of the last user message only
            if m.role == "user":
                ctx = m.meta.get("context") if m.meta else None
                if ctx:
                    m.meta["context"] = prompt_mod.trim_context(ctx)
                break
        return True

    @staticmethod
    def _request_too_large(error_text: str) -> bool:
        t = (error_text or "").lower()
        return (bool(re.search(r"\b(?:http\s*)?413\b", t)) or "too large" in t or "too long" in t
                or "reduce your message size" in t or "context length" in t or "maximum context" in t)

    def _stream(self, tools, cancel, on_delta=_UNSET):
        """Every model call goes through here, so one 'request too large' can retry compactly."""
        delta = self.cb.on_text_delta if on_delta is _UNSET else on_delta
        try:
            return self.provider.stream(self.system_prompt, self.conversation, tools, on_delta=delta, cancel=cancel)
        except ProviderError as e:
            if self._request_too_large(str(e)) and self.go_compact():
                self._status("The prompt was too large for this model; retrying with a shorter one…")
                if tools:
                    tools = self._tools()
                return self.provider.stream(self.system_prompt, self.conversation, tools, on_delta=delta, cancel=cancel)
            raise

    def _tools(self):
        return tool_specs(self.config.allow_python,
                          self.config.vision and getattr(self.provider, "supports_vision", False),
                          tables=bool(self.tables), compact=self.compact, edition=self.config.edition)

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
            self._contacts_runs = {}
            self._turn_text = user_text
            self._coloring_sources = []
            self._windows_this_turn = []
            self._overlay_done = ""
            # "it" is only ambiguous when nothing was opened or named in the last two requests
            prior = list(getattr(self, "_prior_texts", []))[-2:]
            since = (getattr(self, "_turn_starts", []) or [0.0])[-2:][0]
            opened_recently = any(first_word(str(j.get("command", ""))) == "open" and j.get("ok") and float(j.get("ts", 0)) >= since
                                  for j in self.journal[-60:])
            self._no_recent_focus = not opened_recently and not any(_WHICH_NAMED_RE.search(t) or "#" in t for t in prior)
            self._prior_texts = (list(getattr(self, "_prior_texts", [])) + [user_text])[-4:]
            self._turn_starts = (list(getattr(self, "_turn_starts", [])) + [time.time()])[-4:]
            self._impossible_ask = bool(_IMPOSSIBLE_RE.search(user_text)) and not _SAVE_WANTED_RE.search(user_text)
            # the whole request is the impossible thing: then nothing the model runs can be right
            self._impossible_only = self._impossible_ask and not _ACTION_RE.search(user_text)
            self._question_only = question_only(user_text)
            self._dont_run = bool(_DONT_RUN_RE.search(user_text))
            self._failed_opens = set()
            self._ambiguous_fired = False
            self._dispatched_this_turn = set()
            self._turn_start_index = start_len
            self._user_text_upper += " " + user_text.upper()
            if self.config.edition == "chimerax" and hasattr(self.executor, "checkpoint_request"):
                try:   # a restore point for undo_last_request; bookkeeping must never break a turn
                    self.executor.checkpoint_request(user_text)
                except Exception:  # noqa: BLE001
                    pass
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
            tools = self._tools()
            outcome = self._loop(tools, cancel, usage)
            if outcome.get("asked"):
                self.total_usage.add(usage)
                return TurnResult(self._last_assistant_text(), len(self.conversation) - start_len, usage, asked_user=True)
            asked_in_text = self._text_since(start_len)
            if getattr(self, "_ambiguous_fired", False) and asked_in_text.rstrip().endswith("?") and not self._commands_ran_since(start_len):
                # the ambiguity guard told the model to ask; it asked in plain text instead of calling ask_user.
                # Deliver it as the question it is, so the panel shows it as one and waits for the answer.
                if self.cb.on_ask_user:
                    self.cb.on_ask_user(asked_in_text, [])
                self.total_usage.add(usage)
                return TurnResult(asked_in_text, len(self.conversation) - start_len, usage, asked_user=True)
            for attempt in range(2):
                nudge = None
                last_ctx = context
                # not for a request ChimeraX cannot fulfil: pushing the model to "do it now" there
                # makes it invent an unrelated action (a view reset, a publication preset)
                if self.config.nudge_on_no_action and looks_like_action_request(user_text) and not self._impossible_ask:
                    ran = self._commands_since(start_len)
                    if not outcome.get("called_tool") and _WHICH_MODEL_RE.search(self._last_assistant_text()) and self._one_model_open():
                        nudge = NUDGE_ONE_MODEL   # asked which model although only one is open
                    elif not outcome.get("called_tool") and not self._tool_called_since(start_len, "tidy_labels"):
                        nudge = NUDGE          # words but no action
                    elif outcome.get("ended_after_error"):
                        nudge = NUDGE_AFTER_ERROR   # gave up after a failed command
                    elif (_SAVE_CLAIM_RE.search(self._last_assistant_text() or "")
                          and not _SAVE_DENIED_RE.search(self._last_assistant_text() or "")
                          and not any(first_word(c) in ("save", "movie") for c in ran)
                          and not self._tool_called_since(start_len, "save_figure")):
                        # "opne 2gbp, hid the ions, and safe a pic": it replied "saved the picture to your desktop"
                        nudge = NUDGE_SAVE_CLAIM
                    elif _ONLY_RE.search(user_text) and not any(
                            (_HIDE_RE_CHIMERA if self.config.edition == "chimera" else _HIDE_RE).match(c) for c in ran):
                        nudge = NUDGE_ONLY_CHIMERA if self.config.edition == "chimera" else NUDGE_ONLY
                    elif _AA_TYPE_RE.search(user_text) and ran and not any(_AA_CLASS_RE.search(c) for c in ran) \
                            and not any(_MLP_RAN_RE.match(c) for c in ran) and not self._mentions_table(user_text):
                        nudge = NUDGE_AA       # residue-type coloring done with a wrong built-in scheme
                    elif _TIDY_RE.search(user_text) and not self._tool_called_since(start_len, "tidy_labels") and self.config.edition == "chimerax":
                        nudge = NUDGE_TIDY
                    elif _LOOK_RE.search(user_text) and self.config.vision and getattr(self.provider, "supports_vision", False) \
                            and not self._tool_called_since(start_len, "look_at_view"):
                        nudge = NUDGE_LOOK
                    elif _WHY_RE.search(user_text) and not self._tool_called_since(start_len, "explain_residue") and self.config.edition == "chimerax":
                        nudge = NUDGE_WHY
                    elif (self.config.edition == "chimerax" and _IDENTITY_ASK_RE.search(user_text)
                          and not self._tool_called_since(start_len, "sequence_identity")):
                        nudge = NUDGE_SEQID
                    elif (self.config.edition == "chimerax" and _CLOSE_WIN_RE.search(user_text)
                          and not self._tool_called_since(start_len, "close_windows")):
                        nudge = NUDGE_CLOSEWIN
                    elif (self.config.edition == "chimerax" and _UNDO_RE.search(user_text)
                          and not self._tool_called_since(start_len, "undo_last_request")):
                        nudge = NUDGE_UNDO
                    elif _ANNOT_RE.search(user_text) and not _PAE_ASK_RE.search(user_text) \
                            and not self._tool_called_since(start_len, "annotate") \
                            and not (self._tool_called_since(start_len, "protein_features") and ran):
                        # variants/domains asked for and neither route taken. Fetching the features and
                        # coloring them by hand is the other correct route: asked to color the transmembrane
                        # helices, a model that had done exactly that was nudged here and went on to annotate
                        # "disease" and "variant" nobody asked for, scattering nine labels over the figure.
                        nudge = NUDGE_ANNOTATE
                    elif _NAMED_GROUP_RE.search(user_text) and ran and any(_NUMERIC_SPEC_RE.search(c) for c in ran) \
                            and not any(_NAMED_SPEC_RE.search(c) for c in ran) and not re.search(r"\b\d{2,}\b", user_text):
                        nudge = NUDGE_NAMED     # heme/ATP/water addressed by invented residue numbers
                if not nudge:
                    break
                if attempt == 1 and not _ANNOUNCE_RE.search(self._last_assistant_text()):
                    break  # second try only when the model keeps announcing instead of acting
                self.conversation.append(Message.user(nudge, self._refreshed_context(last_ctx)))
                outcome = self._loop(tools, cancel, usage)
                if outcome.get("asked"):
                    self.total_usage.add(usage)
                    return TurnResult(self._last_assistant_text(), len(self.conversation) - start_len, usage, asked_user=True)
                if outcome.get("asked"):
                    self.total_usage.add(usage)
                    return TurnResult(self._last_assistant_text(), len(self.conversation) - start_len, usage, asked_user=True)
            if self.config.nudge_on_no_action and looks_like_action_request(user_text) and not outcome.get("asked") \
                    and not self._commands_since(start_len) and not any(c.name != "run_commands" for c in self._tool_calls_since(start_len)) \
                    and not self._impossible_ask and not getattr(self, "_dont_run", False) \
                    and self._fallback_wanted(self._text_since(start_len)):
                # only for a model that meant to act and could not make a tool call. It used to fire on any
                # words-only turn and replaced correct answers ("ChimeraX cannot send email") with "Nothing ran."
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
                and not any(_AA_CLASS_RE.search(c) for c in ran) and not any(_MLP_RAN_RE.match(c) for c in ran):
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
        # a tool line typed inside run_commands and run as the tool counts as a call: otherwise the "call the tool"
        # nudge fired after the tool had answered, with the turn's first state block ("no models are open")
        if name in getattr(self, "_dispatched_this_turn", set()) and index >= getattr(self, "_turn_start_index", 0):
            return True
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
        # The static prompt is byte-identical every call, so providers cache it and trimming it saves
        # little of what is actually billed. The retrieved-docs block changes every turn and can never
        # cache: it is most of the real cost. Dropping it in compact mode measured 56/62 against a
        # baseline that itself ranged 55-57, and cut billed tokens per request by about two thirds.
        # The model can still pull documentation when it needs it, by calling search_docs.
        want = 0 if self.compact else self.config.docs_per_turn
        if want > 0:
            try:
                docs = self.executor.search_docs(user_text, want)
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
        found = self._locate_sequences(user_text)
        if found:
            note = (note + "\n" if note else "") + found
        return prompt_mod.build_context(state, docs, note=note)

    _PEPTIDE_RE = re.compile(r"(?<![A-Za-z0-9])[ACDEFGHIKLMNPQRSTVWY]{6,}(?![A-Za-z0-9])")

    def _locate_sequences(self, text: str) -> str:
        """A typed amino-acid sequence ("select LEVEPSDT...") located in the open chains, so the model gets residue
        numbers instead of guessing them or opening a sequence window to read them."""
        if not hasattr(self.executor, "find_sequence"):
            return ""
        lines = []
        for seq in dict.fromkeys(self._PEPTIDE_RE.findall(text or "")):
            try:
                hits = self.executor.find_sequence(seq)
            except Exception:  # noqa: BLE001
                continue
            if hits:
                lines.append("The sequence %s is residues %s. Use these residue numbers." % (seq, ", ".join(hits)))
        return "\n".join(lines)

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
            elif name == "compare_contacts":
                if not args.get("reference") or not args.get("other"):
                    raise RuntimeError("compare_contacts needs both 'reference' and 'other' model specs.")
                restrict = str(self._scalar(args.get("restrict"), "") or "") or None
                cutoff = float(args.get("cutoff") or 4.0)
                key = (str(args["reference"]), str(args["other"]), args.get("chain") or None, restrict)
                runs = getattr(self, "_contacts_runs", None)
                if runs is None:
                    runs = self._contacts_runs = {}
                if key in runs and abs(runs[key] - cutoff) > 1e-6:
                    # a second pass at another cutoff produces a second, different answer that the model
                    # then merges with the first; the standard cutoff's result stands unless the user asks
                    payload = {"error": "compare_contacts already ran for %s vs %s at %.1f A in this request. "
                                        "Report that result; do not repeat the comparison at another cutoff "
                                        "unless the user asks for a specific distance." % (key[0], key[1], runs[key])}
                else:
                    runs[key] = cutoff
                    payload = self._compare_contacts(key[0], key[1], key[2], restrict, cutoff)
                result = ToolResult(call.id, name, json.dumps({k: v for k, v in payload.items() if k != "commands"}, ensure_ascii=False),
                                    is_error="error" in payload)
            elif name == "explain_residue":
                payload = self.executor.residue_provenance(str(self._scalar(args.get("residue"), "") or ""), list(self.journal))
                result = ToolResult(call.id, name, json.dumps(payload, ensure_ascii=False), is_error="error" in payload)
            elif name == "save_figure":
                if self._impossible_ask:
                    # a figure bundle is still a file: it is not a step towards emailing or printing,
                    # and this path runs its saves with origin="figure", which the guard below does not see
                    payload = {"error": "The user asked for something ChimeraX cannot do (emailing, printing, messaging "
                                        "or uploading). Saving a figure is not a step towards it. Say in one sentence that "
                                        "ChimeraX cannot do it and run nothing else."}
                    return ToolResult(call.id, name, json.dumps(payload), is_error=True), payload
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
                self._overlay_pending = "table_overlay"
                payload = self._table_overlay(str(args.get("dataset", "") or ""), str(args.get("column", "") or ""),
                                              args.get("chain") or None, str(args.get("palette", "") or ""),
                                              str(args.get("model", "") or ""), args.get("accession") or None, bool(args.get("label", False)))
                result = ToolResult(call.id, name, json.dumps({k: v for k, v in payload.items() if k != "commands"}, ensure_ascii=False),
                                    is_error="error" in payload)
            elif name == "annotate":
                kind = str(self._scalar(args.get("kind"), "variant") or "variant").lower()
                self._overlay_pending = "annotate"
                src = "clinvar" if kind in CLINVAR_KINDS else ("uniprot variants" if kind in ("variant", "disease") else None)
                refusal = self._source_guard(src) if src else None
                payload = {"error": refusal} if refusal else self._annotate(str(self._scalar(args.get("model"), "#1") or "#1"), str(self._scalar(args.get("accession"), "")),
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
            elif name == "fetch_annotation":
                src = str(self._scalar(args.get("source"), "alphamissense") or "alphamissense").lower()
                self._overlay_pending = "fetch_annotation"
                refusal = self._source_guard("conservation" if src.startswith(("conserv", "consurf")) else "alphamissense")
                payload = {"error": refusal} if refusal else self._fetch_annotation(str(self._scalar(args.get("source"), "alphamissense") or "alphamissense"),
                                                 str(self._scalar(args.get("protein"), "") or ""),
                                                 str(self._scalar(args.get("model"), "#1") or "#1"),
                                                 str(self._scalar(args.get("chain"), "") or ""))
                result = ToolResult(call.id, name, json.dumps({k: v for k, v in payload.items() if k != "commands"},
                                                              ensure_ascii=False), is_error="error" in payload)
            elif name in ("sequence_identity", "close_windows"):
                fn = getattr(self.executor, name, None)
                if fn is None:
                    payload = {"error": "%s is not available in this edition." % name}
                elif name == "sequence_identity":
                    models = args.get("models")
                    models = " ".join(str(x) for x in models) if isinstance(models, list) else str(models or "")
                    payload = fn(models, str(self._scalar(args.get("chain"), "") or "") or None)
                else:
                    payload = fn(str(self._scalar(args.get("which"), "sequence") or "sequence"))
                result = ToolResult(call.id, name, json.dumps(payload, ensure_ascii=False), is_error="error" in payload)
            elif name == "undo_last_request":
                fn = getattr(self.executor, "undo_last_request", None)
                if fn is None:
                    payload = {"error": "Undo is not available in this edition."}
                else:
                    preview_fn = getattr(self.executor, "pending_undo", None)
                    preview = preview_fn() if preview_fn else {}
                    if preview.get("error"):
                        payload = preview
                    elif self.config.autonomy == AUTONOMY_ALL:
                        payload = fn()
                    else:
                        classification = Classification(
                            "undo_last_request", True,
                            "restores ChimeraX to before the last request (\"%s\")%s; anything done since is lost"
                            % (preview.get("request", "the last request"),
                               " by closing the model(s) it opened" if preview.get("kind") == "models" else ""))
                        approved = self._confirm([classification.command], [classification.reason])
                        payload = {"error": "The user chose not to undo.", "skipped": True} if approved is None else fn()
                result = ToolResult(call.id, name, json.dumps(payload, ensure_ascii=False),
                                    is_error=bool(payload.get("error")) and not payload.get("skipped"))
            elif name in ("membrane_view", "gpcr_states"):
                fn = getattr(self.executor, "membrane_orient" if name == "membrane_view" else "gpcr_states", None)
                if fn is None:
                    payload = {"error": "%s is not available in this edition." % name}
                elif name == "membrane_view":
                    # minimal scope: the membrane planes are drawn only when the request speaks of a membrane
                    if "slabs" in args:
                        slabs = bool(args.get("slabs"))
                    else:
                        slabs = bool(re.search(r"membrane|bilayer|lipid|slab|plane", getattr(self, "_turn_text", "") or "", re.I))
                    payload = fn(str(self._scalar(args.get("model"), "#1") or "#1"), str(self._scalar(args.get("pdb"), "") or "") or None, slabs)
                else:
                    states = args.get("open_states") or []
                    if isinstance(states, str):
                        states = [x.strip() for x in re.split(r"[,\s]+", states) if x.strip()]
                    payload = fn(str(self._scalar(args.get("protein"), "") or ""), [str(x).lower() for x in states])
                result = ToolResult(call.id, name, json.dumps(payload, ensure_ascii=False), is_error="error" in payload)
            elif name == "view_axis":
                fn = getattr(self.executor, "view_axis", None)
                if fn is None:
                    payload = {"error": "view_axis is not available in this edition."}
                else:
                    payload = fn(str(self._scalar(args.get("model"), "") or ""), str(self._scalar(args.get("axis"), "short") or "short").lower())
                result = ToolResult(call.id, name, json.dumps(payload, ensure_ascii=False), is_error="error" in payload)
            elif name == "map_numbering":
                positions = args.get("positions") or []
                if not isinstance(positions, list):
                    positions = [positions]
                payload = self._map_numbering(str(self._scalar(args.get("model"), "#1") or "#1"),
                                              str(self._scalar(args.get("protein"), "") or ""),
                                              [int(p) for p in positions if str(p).strip().lstrip("-").isdigit()])
                result = ToolResult(call.id, name, json.dumps(payload, ensure_ascii=False), is_error="error" in payload)
            elif name == "apply_figure_style":
                payload = self._apply_figure_style(str(self._scalar(args.get("figure"), "") or ""),
                                                   str(self._scalar(args.get("model"), "") or ""))
                result = ToolResult(call.id, name, json.dumps({k: v for k, v in payload.items() if k != "commands"},
                                                              ensure_ascii=False), is_error="error" in payload)
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
        if getattr(self, "_overlay_pending", "") and isinstance(payload, dict) and not payload.get("error") and payload.get("colored", True):
            self._overlay_done = self._overlay_pending
            if isinstance(payload, dict):
                payload.setdefault("note", "The coloring, color key and title are drawn. Nothing else to run: report this result.")
                result = ToolResult(call.id, name, json.dumps({k: v for k, v in payload.items() if k not in ("commands", "png_b64")}, ensure_ascii=False),
                                    is_error=False)
        self._overlay_pending = ""
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
        """The single path for running commands: rewrites, policy, repeat guard, execution, journal."""
        commands = split_joined([c.strip() for c in commands if c and c.strip()])
        notes: List[str] = []
        tags: List[str] = []
        if origin == "model" and self.config.guards and self.config.edition == "chimerax" and commands:
            commands, notes, tags = self._rewrite_batch(commands)
        out = self._execute_commands(commands, origin, preapproved)
        if tags:
            out["fixups"] = list(out.get("fixups") or []) + tags
        if notes:
            text = "Pellaeon corrected: " + "; ".join(notes) + "."
            out["note"] = (text + " " + out["note"]) if out.get("note") else text
        return out

    # the rewrite notes of fixups.rewrite -> short names for the run report
    _REWRITE_TAGS = (("attribute test", "attr_test"), ("several specs", "spec_list"), ("`info", "info_residues"),
                     ("the alignment window", "mm_show"), ("`hide` alone", "hide_rest"), ("bychain takes", "bychain_palette"),
                     ("submodel range", "submodel_range"), ("a class after", "class_and"), ("DNA/RNA", "class_chain"))

    def _rewrite_batch(self, commands: List[str]) -> Tuple[List[str], List[str], List[str]]:
        """Deterministic corrections of the model's batch before anything runs. Each returns the new list and
        says what it changed; the model sees the notes, the run report the tags."""
        text = getattr(self, "_turn_text", "") or ""
        notes: List[str] = []
        tags: List[str] = []
        out = []
        for c in commands:
            if re.match(r"^\s*run_commands\s+\S", c):       # the tool's name typed in front of its command
                c = c.split(None, 1)[1]
                tags.append("run_commands_prefix")
            new, why = rewrite_command(c, text)
            if why:
                notes.append("`%s` -> `%s` (%s)" % (c, new, "; ".join(why)))
                tags.extend(next((t for k, t in self._REWRITE_TAGS if w.startswith(k)), "rewrite") for w in why)
            out.append(new)
        for step in (self._select_union, self._key_for_coloring, self._show_before_style, self._ligand_with_ions,
                     self._compact_submodels, self._unasked_atoms, self._label_cap, self._structure_targets):
            try:
                out, n, tag = step(out, text)
            except Exception:  # noqa: BLE001  (a correction must never break the batch)
                continue
            if n:
                notes.extend(n)
                tags.extend([tag] * len(n))
        return out, notes, tags

    def _select_union(self, commands: List[str], text: str):
        """`select A` then `select B` in one batch: the second replaces the first, which nobody writes on purpose
        (asked for the residues of chain A near B and of B near A, a model selected one side only). -> select add B."""
        out, notes, last_plain = list(commands), [], None
        subs = ("add", "subtract", "intersect", "clear", "up", "down", "zone", "~sel", "sel", "all")
        for i, c in enumerate(out):
            if first_word(c) == "select" and not c.lstrip().startswith("~"):
                rest = c.split(None, 1)[1].strip() if len(c.split(None, 1)) > 1 else ""
                if rest and rest.split()[0].lower() not in subs:
                    if last_plain is not None and rest != last_plain[1] and not any(
                            re.search(r"\bsel\b", out[k]) for k in range(last_plain[0] + 1, i)):
                        out[i] = "select add " + rest
                        notes.append("`%s` -> `%s` (a second select replaces the first; both sets were meant)" % (c, out[i]))
                    last_plain = (i, rest)
                    continue
            if re.search(r"\bsel\b", c):
                last_plain = None
        return out, notes, "select_add"

    _KEY_TRIGGER = ("true", "on", "yes", "show", "add", "bfactor", "byattribute", "attribute", "palette", "legend")
    _ATTR_COLOR_RE = re.compile(r"^\s*(colou?r\s+(bfactor|byattr\w*)|mlp|coulombic)\b", re.I)

    def _key_for_coloring(self, commands: List[str], text: str):
        """`key true` / `key bfactor ...` read their first word as a palette name (a web lookup that fails). What was
        meant is a key for the attribute coloring just made: repeat that coloring with `key true`."""
        out, notes = list(commands), []
        for i, c in enumerate(out):
            if first_word(c) != "key":
                continue
            args = c.split()[1:]
            if not args or ":" in args[0] or not (args[0].lower() in self._KEY_TRIGGER or args[0].startswith("#")):
                continue
            prior = [x for x in out[:i] if self._ATTR_COLOR_RE.match(x)]
            if not prior:
                prior = [str(j.get("command", "")) for j in self.journal if j.get("ok") and self._ATTR_COLOR_RE.match(str(j.get("command", "")))]
            if not prior:
                continue
            base = re.sub(r"\s+key\s+\S+", "", prior[-1].strip())
            out[i] = base + " key true"
            notes.append("`%s` -> `%s` (a key for an attribute coloring comes from the coloring command)" % (c, out[i]))
        return out, notes, "key_from_coloring"

    _SHOW_ASK_RE = re.compile(r"\b(show\w*|display\w*|see|visible|draw)\b", re.I)
    _STYLE_SPEC_RE = re.compile(r"^\s*style\s+(\S.*?)\s+(stick|ball|sphere)\s*$", re.I)

    def _show_before_style(self, commands: List[str], text: str):
        """Asked to display a selection in stick style, `style sel stick` alone changes how DISPLAYED atoms are drawn and shows nothing.
        When the user asked to see them and the batch shows or hides nothing, show the styled atoms first."""
        if not self._SHOW_ASK_RE.search(text or "") or not hasattr(self.executor, "spec_atoms"):
            return commands, [], "show_before_style"
        if any(first_word(c) in ("show", "hide", "display") or c.lstrip().startswith("~") for c in commands):
            return commands, [], "show_before_style"
        out, notes = [], []
        for c in commands:
            m = self._STYLE_SPEC_RE.match(c)
            if m:
                chk = self.executor.spec_atoms(m.group(1)) or {}
                n, shown = chk.get("atoms"), chk.get("shown")
                if chk.get("used") and n and shown is not None and shown < 0.5 * n:
                    add = "show %s atoms" % chk["used"]
                    out.append(add)
                    notes.append("added `%s` before `%s` (style changes only atoms that are displayed; %d of %d were hidden)"
                                 % (add, c.strip(), n - shown, n))
            out.append(c)
        return out, notes, "show_before_style"

    _SUBMODEL_CMD_RE = re.compile(r"^(\w+)\s+#(\d+)\.(\d+)\b(.*)$")
    _SUBMODEL_WORDS = ("surface", "color", "colour", "show", "hide", "style", "cartoon", "transparency", "rainbow", "size")

    def _compact_submodels(self, commands: List[str], text: str):
        """`surface #2.1 ...` repeated for #2.2 ... #2.60 (an assembly's copies) is one command on the parent: 60 separate
        surface calculations take minutes and fill the log; `surface #2 ...` does the same thing."""
        out, notes, i = [], [], 0
        while i < len(commands):
            m = self._SUBMODEL_CMD_RE.match(commands[i].strip())
            if m and m.group(1).lower() in self._SUBMODEL_WORDS:
                j = i
                while j < len(commands):
                    mj = self._SUBMODEL_CMD_RE.match(commands[j].strip())
                    if not (mj and mj.group(1) == m.group(1) and mj.group(2) == m.group(2) and mj.group(4) == m.group(4)):
                        break
                    j += 1
                if j - i >= 4:
                    merged = "%s #%s%s" % (m.group(1), m.group(2), m.group(4))
                    out.append(merged)
                    notes.append("%d commands on submodels #%s.* merged into `%s`" % (j - i, m.group(2), merged))
                    i = j
                    continue
            out.append(commands[i])
            i += 1
        return out, notes, "compact_submodels"

    _ATOM_WORDS_RE = re.compile(r"\b(atoms?|sticks?|side ?chains?|spheres?|balls?|ball[- ]and[- ]stick|stick style|residues? as|all atoms)\b", re.I)
    _CARTOON_ONLY_RE = re.compile(r"\b(cartoons?|ribbons?)\b|\b(keep|show|display|leave)\s+only\b|\bonly\s+(the\s+)?(receptor|protein|chain)\b", re.I)
    _SHOW_ATOMS_RE = re.compile(r"^show\s+(\S+(?:\s*&\s*\S+)?)\s+atoms\s*$", re.I)

    def _unasked_atoms(self, commands: List[str], text: str):
        """"Keep only chain A" or "as a cartoon" is a display of what was asked, not an invitation to show every
        atom: drop a `show ... atoms` the request never asked for (the cartoon is what the user will see)."""
        if not text or not self._CARTOON_ONLY_RE.search(text) or self._ATOM_WORDS_RE.search(text):
            return commands, [], "unasked_atoms"
        out, notes = [], []
        for c in commands:
            if self._SHOW_ATOMS_RE.match(c.strip()):
                notes.append("dropped `%s`: atoms were not asked for (cartoon / keep-only request)" % c.strip())
                continue
            out.append(c)
        return out, notes, "unasked_atoms"

    _LABEL_SPEC_RE = re.compile(r"^label\s+(?!delete\b|height\b|size\b|offset\b|color\b|bgColor\b|onTop\b)(\S+(?:\s*&\s*\S+)?)", re.I)
    _LABEL_ALL_RE = re.compile(r"\b(all|every|each)\b.{0,25}\b(residues?|labels?)\b|\blabel\s+(all|every|everything)\b", re.I)
    LABEL_CAP = 60

    def _label_cap(self, commands: List[str], text: str):
        """`label sel` on 600 residues (a whole domain, a 4 A zone) paints the view white with labels: refuse a label
        on more than LABEL_CAP residues unless the user asked for all of them, and say how to narrow it."""
        if not hasattr(self.executor, "spec_atoms") or (text and self._LABEL_ALL_RE.search(text)):
            return commands, [], "label_cap"
        out, notes = [], []
        for c in commands:
            m = self._LABEL_SPEC_RE.match(c.strip())
            if m and not c.strip().startswith("~"):
                try:
                    chk = self.executor.spec_atoms(m.group(1)) or {}
                except Exception:  # noqa: BLE001
                    chk = {}
                n = chk.get("residues") or 0
                if chk.get("used") and n > self.LABEL_CAP:
                    notes.append("refused `%s`: it would label %d residues. Label the few that matter (name them, or the ones "
                                 "within 4 A of the ligand), or say 'label all of them' if that is really wanted." % (c.strip(), n))
                    continue
            out.append(c)
        return out, notes, "label_cap"

    _MM_TO_RE = re.compile(r"^(matchmaker|mm|align)\s+(#\d+)(\S*)\s+(to|toAtoms)\s+(#\d+)(\S*)(.*)$", re.I)

    def _structure_targets(self, commands: List[str], text: str):
        """`matchmaker #3/A to #2/A` where #2 is the distances (or a key, a slab, a label) model: the model counted
        model numbers instead of reading the state. When exactly one other atomic structure is open, use it."""
        if not any(first_word(c) in ("matchmaker", "mm", "align") for c in commands) or not hasattr(self.executor, "get_state"):
            return commands, [], "structure_targets"
        try:
            models = (self.executor.get_state() or {}).get("models") or []
        except Exception:  # noqa: BLE001
            return commands, [], "structure_targets"
        structures = [m["id"] for m in models if m.get("id") and not m.get("note", "").startswith("not a structure") and m.get("num_atoms")]
        non = {m["id"] for m in models if m.get("id") and m.get("note", "").startswith("not a structure")}
        out, notes = [], []
        for c in commands:
            m = self._MM_TO_RE.match(c.strip())
            if m and m.group(5) in non:
                others = [sid for sid in structures if sid != m.group(2)]
                if len(others) == 1:
                    fixed = "%s %s%s %s %s%s%s" % (m.group(1), m.group(2), m.group(3), m.group(4), others[0], m.group(6), m.group(7))
                    notes.append("`%s` -> `%s` (%s is not a structure; the other open structure is %s)" % (c.strip(), fixed, m.group(5), others[0]))
                    c = fixed
                else:
                    notes.append("%s is not a structure (a key, distances or label model); name the structure to match to" % m.group(5))
            out.append(c)
        return out, notes, "structure_targets"

    def _ligand_with_ions(self, commands: List[str], text: str):
        """A heme's iron (a cofactor's metal) is classified as an ion, so `show ligand` leaves it hidden: show the
        whole residue of a ligand that contains an ion atom as well."""
        if not any(first_word(c) == "show" and re.search(r"\bligands?\b", c) for c in commands):
            return commands, [], "ligand_ions"
        try:
            models = (self.executor.get_state() or {}).get("models") or []
        except Exception:  # noqa: BLE001
            return commands, [], "ligand_ions"
        both = sorted({n for m in models for n in (set((m.get("hets") or {}).get("ligand") or [])
                                                   & set((m.get("hets") or {}).get("ions") or []))})
        if not both:
            return commands, [], "ligand_ions"
        out, notes = [], []
        for c in commands:
            out.append(c)
            if first_word(c) == "show" and re.search(r"\bligands?\b", c):
                mid = re.search(r"#\d+(?:\.\d+)*", c)
                for name in both:
                    extra = "show %s:%s atoms" % (mid.group(0) if mid else "", name)
                    if not any((":" + name).lower() in x.lower() for x in commands):
                        out.append(extra)
                        notes.append("added `%s` (its metal atom is classified as an ion, which `ligand` leaves out)" % extra)
        return out, notes, "ligand_ions"

    def _recipe_mismatch(self, command: str) -> str:
        """Why an RBVI community recipe does not fit this request ('' when it does or cannot be checked)."""
        m = _RECIPE_DIR_RE.search(command)
        lib = getattr(getattr(self.executor, "knowledge", None), "recipe_library", None) if m else None
        if not m or not lib:
            return ""
        entry = next((r for r in lib if str(r.get("dir", "")).lower() == m.group(1).lower()), None)
        if not entry:
            return ""
        text = (getattr(self, "_turn_text", "") or "").lower()
        what = str(entry.get("what", ""))[:160]
        if entry.get("only_if") and not re.search(entry["only_if"], text, re.I):
            return ("The community recipe '%s' does something else: %s Do not run it; use plain ChimeraX commands "
                    "for what the user asked, or say ChimeraX cannot do it." % (entry.get("name", m.group(1)), what))

        def words(t):
            return {w[:5] for w in re.findall(r"[a-z][a-z0-9-]{3,}", t.lower()) if w not in _RECIPE_STOP}
        mine = words(" ".join([str(entry.get("name", "")), str(entry.get("what", ""))] + [str(x) for x in entry.get("requests") or []]))
        if not (words(text) & mine):
            return ("The community recipe '%s' is unrelated to this request (%s). Do not run it; use plain ChimeraX "
                    "commands, or say plainly that ChimeraX cannot do it." % (entry.get("name", m.group(1)), what))
        return ""

    def _run_tool_line(self, commands: List[str], at: int, name: str, args: Dict[str, Any], origin: str,
                       preapproved: bool) -> Dict[str, Any]:
        """A line naming one of our tools, run as that tool; the commands around it run as usual."""
        out: Dict[str, Any] = {"ok": True, "results": []}
        if at > 0:
            out = self.execute_commands(commands[:at], origin, preapproved)
            if not out.get("ok"):
                out["not_run"] = commands[at:]
                return out
        res, _payload = self._dispatch(ToolCall(new_id(), name, dict(args)))
        self._dispatched_this_turn = set(getattr(self, "_dispatched_this_turn", set())) | {name}
        out["results"] = list(out.get("results") or [])
        out["tool"] = name
        out["tool_args"] = args
        out["tool_result"] = res.content[:4000]
        out["fixups"] = list(out.get("fixups") or []) + ["tool_as_command:" + name]
        note = ("`%s` is a Pellaeon tool, not a ChimeraX command: it was run as the tool %s(%s); its result is in "
                "tool_result. Do not run it again." % (commands[at].strip()[:80], name, json.dumps(args)[:200]))
        out["note"] = (out["note"] + " " + note) if out.get("note") else note
        if res.is_error:
            out["ok"] = False
            out["error"] = "The tool %s reported an error (see tool_result)." % name
            if commands[at + 1:]:
                out["not_run"] = commands[at + 1:]
            return out
        if commands[at + 1:]:
            more = self.execute_commands(commands[at + 1:], origin, preapproved)
            out["results"] += more.get("results") or []
            for k, v in more.items():
                if k in ("results", "note"):
                    continue
                out[k] = v if k != "fixups" else out["fixups"] + list(v)
            if more.get("note"):
                out["note"] += " " + more["note"]
        return out

    def _execute_commands(self, commands: List[str], origin: str = "model", preapproved: bool = False) -> Dict[str, Any]:
        if not commands:
            return {"ok": False, "results": [], "error": "No commands given."}
        blocked = None
        dispatch = None
        turn_text = getattr(self, "_turn_text", "") or ""
        for idx, c in enumerate(commands):
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
                    blocked = (idx, {"error": "Accession %s was NOT obtained from resolve_protein%s, so it may be the "
                                     "wrong protein. Call resolve_protein with the gene/protein name first and use the open_command it returns."
                                     % (acc, (" (UniProt says it is %s, which the user did not mention)" % gene) if gene else ""),
                                     "unverified_accession": acc})
                    break
            if blocked is not None:      # the inner break only left the accession loop
                break
            w = first_word(c)
            if origin == "model" and getattr(self, "_impossible_only", False):
                # "dock this and text me", "order the reagent": models answered with a surface, a recipe
                # script, a publication preset. There is no command that helps; say so.
                blocked = (idx, {"error": "The user asked only for something ChimeraX cannot do (emailing, printing, "
                                 "messaging, ordering, uploading). No command helps with that. Say in one sentence that "
                                 "ChimeraX cannot do it. Do not run any other command in its place.", "impossible": True})
                break
            if origin == "model" and w == "open" and _REMOTE_SCRIPT_RE.search(c) and not _RBVI_RECIPE_RE.search(c):
                # "color by residue type" once became `open https://raw.githubusercontent.com/.../amino-coloring.cxc`:
                # a script fetched from the internet, run on the user's say-so. The model knows the commands.
                blocked = (idx, {"error": "Do not fetch and run a script from the internet (%s). Run the ChimeraX "
                                 "commands yourself instead; if you do not know them, call search_docs or command_usage."
                                 % c.split()[1][:80], "remote_script": True})
                break
            if (origin == "model" and self.config.guards and _SEQ_WINDOW_RE.match(c) and _IDENTITY_ASK_RE.search(getattr(self, "_turn_text", "") or "")
                    and not _WANTS_VIEWER_RE.search(getattr(self, "_turn_text", "") or "")):
                if self.config.edition == "chimerax" and hasattr(self.executor, "sequence_identity"):
                    # what was meant is the table: run the sequence_identity tool on the models it names
                    specs = [t for t in re.split(r"[\s,]+(?=[#/])|\s+", " ".join(c.split()[2:])) if t.startswith(("#", "/"))]
                    dispatch = (idx, "sequence_identity", {"models": " ".join(specs)})
                    break
                blocked = (idx, {"error": "For sequence identity call the sequence_identity tool: it aligns every pair and "
                                 "returns a table without opening any window. `%s` opens a viewer window." % c.strip()[:60],
                                 "window": True})
                break
            if origin == "model" and self.config.guards and _WINDOW_CMD_RE.match(c) and len(getattr(self, "_windows_this_turn", [])) >= WINDOW_CAP:
                blocked = (idx, {"error": "This request already opened %d tool windows (%s). Do not open more: report what "
                                 "you have, or use close_windows if the user wants them gone."
                                 % (len(self._windows_this_turn), ", ".join(sorted(set(self._windows_this_turn)))),
                                 "window": True})
                break
            if origin == "model" and self.config.guards and getattr(self, "_overlay_done", "") and _REDO_OVERLAY_RE.match(c):
                blocked = (idx, {"error": "%s already colored the model and drew the key and title. Do not redo or restyle "
                                 "that: report its result (the legend text it returned) to the user now." % self._overlay_done,
                                 "scope": True})
                break
            if origin == "model" and self.config.guards and _TERMINUS_RE.search(c):
                blocked = (idx, {"error": "A bare :end/:first/:last matches nothing (silently; ranges like :70-end do work). "
                                 "Use the residue numbers: %s" % self._chain_ranges_text(), "terminus": True})
                break
            if (origin == "model" and self.config.guards and w not in _NO_CHAIN_CHECK and _CHAIN_SPEC_RE.search(c) and any(first_word(x) == "open" for x in commands[:idx])
                    and not _USER_CHAINS_RE.search(getattr(self, "_turn_text", "") or "")):
                blocked = (idx, {"error": "You just opened a structure and guessed its chain letters. Call get_state to see "
                                 "its chains and what each one is, then run the chain commands.", "chains_unchecked": True})
                break
            if (origin == "model" and self.config.guards and w == "preset" and self.config.initiative != "initiative"
                    and not _PRESET_ASK_RE.search(getattr(self, "_turn_text", "") or "")):
                blocked = (idx, {"error": "Do not use a preset: it changes lighting, colors and styles the user did not ask "
                                 "about. Run only the command for what was asked (e.g. `set bgColor white`, "
                                 "`style #1 stick`, `lighting soft`).", "scope": True})
                break
            if origin == "model" and _looks_like_prose(c):
                # the model put its own reasoning into the commands list ("Wait, no. Let me re-read...")
                blocked = (idx, {"error": "That is a sentence, not a ChimeraX command: %r. Put only commands in the "
                                 "list. If the request cannot be done, answer in words instead." % c[:80], "prose": True})
                break
            if self._impossible_ask and origin == "model" and (
                    w in ("save", "export", "write", "movie", "snapshot", "copy") or c.lower().startswith("view name")
                    or c.lower().startswith("log save")):
                # anything that stores or produces something is a "half step" towards the impossible ask:
                # with bookmarks available, "email the view" became `view name hemoglobin_view`
                blocked = (idx, {"error":
                           "The user asked for something ChimeraX cannot do (emailing, printing, messaging or uploading). "
                           "Saving a file is NOT a step towards it. Answer in words now: say in one sentence that ChimeraX "
                           "cannot do it, and that they can save an image themselves and send it from their own email or file "
                           "manager. Do not run any other command in its place.", "impossible": True})
                break
            if origin == "model" and self.config.guards and getattr(self, "_dont_run", False):
                blocked = (idx, {"error": "The user asked to be told the command, not to have it run. Run nothing: give the "
                                 "command(s) in your reply.", "dont_run": True})
                break
            if origin == "model" and self.config.guards and getattr(self, "_question_only", False) and _SCENE_CMD_RE.match(c) \
                    and w not in _TOOL_NAMES:
                blocked = (idx, {"error": "The user asked a question and did not ask to change anything. Answer in words. Commands "
                                 "that only read or measure are fine (info residues, measure, log metadata, distance, angle, "
                                 "select); `%s` changes the display." % c.strip()[:60], "question": True})
                break
            if origin == "model" and self.config.guards and _UNASKED_WINDOW_RE.match(c) and not _ASKS_WINDOW_RE.search(turn_text):
                blocked = (idx, {"error": "`%s` opens a tool window the user did not ask for. Do the task without it (a morph "
                                 "plays by itself, replay it with `coordset #N`; residue names and numbers come from "
                                 "`info residues`)." % c.strip()[:60], "window": True})
                break
            if origin == "model" and self.config.guards and w == "open" and getattr(self, "_failed_opens", None):
                mo = _OPEN_ID_RE.match(c)
                if mo and mo.group(1).lower() not in self._failed_opens and not re.search(r"\b%s\b" % re.escape(mo.group(1)), turn_text, re.I):
                    blocked = (idx, {"error": "%s could not be opened. Do not open a different entry in its place: tell the user "
                                     "it could not be fetched and to check the ID." % ", ".join(sorted(self._failed_opens)),
                                     "substitute": True})
                    break
            if origin == "model" and self.config.guards and w == "open" and _RBVI_RECIPE_RE.search(c):
                why = self._recipe_mismatch(c)
                if why:
                    blocked = (idx, {"error": why, "recipe": True})
                    break
            if w in _TOOL_NAMES:
                parsed = parse_tool_line(c) if (origin == "model" and self.config.guards) else None
                if parsed:
                    dispatch = (idx, parsed[0], parsed[1])
                    break
                blocked = (idx, {"error": "'%s' is one of YOUR TOOLS, not a ChimeraX command. Call the tool "
                           "named %s with its arguments instead of running it as text." % (w, w), "tool_misuse": w})
                break
        if dispatch is not None:
            return self._run_tool_line(commands, dispatch[0], dispatch[1], dispatch[2], origin, preapproved)
        if blocked is not None:
            # The batch used to be discarded whole: asked to "open the AlphaFold model of ADRB2 and show
            # its variants", a model that wrote `annotate ...` as text lost the `open` in front of it too,
            # and then reported the model as open. Run what came first, then report the offender.
            stop_at, err = blocked
            if stop_at == 0:
                return dict(err, ok=False, results=[])
            out = self.execute_commands(commands[:stop_at], origin, preapproved)
            out.update({k: v for k, v in err.items() if k != "error"})
            out["ok"] = False
            if out.get("skipped"):       # the user declined the prefix: that is the message that matters
                out["error"] = "%s Also: %s" % (out.get("error") or "The user declined.", err["error"])
            else:
                out["error"] = err["error"]
            return out

        if origin == "model" and self.config.guards:
            ask = self._ambiguous_it(commands)
            if ask:
                self._ambiguous_fired = True
                return {"ok": False, "results": [], "error": ask, "ambiguous": True}
            commands, order_note = self._broad_first(commands)
        else:
            order_note = ""
        open_notes = []
        if origin == "model" and self.config.edition == "chimerax" and self.config.guards:
            commands, open_notes = self._drop_duplicate_opens(commands)
            if not commands:
                return {"ok": True, "results": [], "note": " ".join(open_notes)}
        if self.config.edition == "chimerax":
            commands = self._one_legend(commands)
        if self.config.readable_labels and self.config.edition == "chimerax" and any(_DISTANCE_RE.match(c) for c in commands):
            # ChimeraX draws distances and their labels yellow, unreadable on the white background of
            # every publication view; restyle them to contrast with the current background
            try:
                bg = str((self.executor.get_state() or {}).get("background") or "")
            except Exception:  # noqa: BLE001
                bg = ""
            nums = [int(x) for x in re.findall(r"\d+", bg)][:3]
            light = bool(nums) and sum(nums) / len(nums) > 140
            styled = []
            for c in commands:
                styled.append(c)
                if _DISTANCE_RE.match(c) and not re.search(r"\bcolor\b", c, re.I):
                    styled.append("distance style color %s" % ("#1f3a5f" if light else "gold"))
            commands = styled

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
        self._windows_this_turn = list(getattr(self, "_windows_this_turn", [])) + [
            t for t in (getattr(self.executor, "last_new_tools", None) or []) if t not in _HARMLESS_TOOLS]
        bad = next((r for r in results if not r.get("ok") and str(r.get("command", "")).startswith("2dlabels change pellaeon_title ")), None)
        if bad is not None:      # the title Pellaeon remembered was deleted by hand: make it afresh
            commands = [("2dlabels create pellaeon_title " + c[len("2dlabels change pellaeon_title "):]) if c == bad["command"] else c
                        for c in commands]
            results = self.executor.run_commands(commands)
        self._legend_title = any(str(r.get("command", "")).startswith("2dlabels ") and "pellaeon_title" in str(r.get("command", "")) and r.get("ok")
                                 for r in results) or getattr(self, "_legend_title", False)
        now = time.time()
        for r in results:
            self.journal.append({"ts": now, "origin": origin, "command": r.get("command", ""), "ok": bool(r.get("ok")), "noop": bool(r.get("noop")),
                                 "error": r.get("error", "")})
        for r in results:
            mo = _OPEN_ID_RE.match(str(r.get("command", "")))
            if mo and not r.get("ok") and _OPEN_FAILED_RE.search(str(r.get("error", ""))):
                self._failed_opens = set(getattr(self, "_failed_opens", set())) | {mo.group(1).lower()}
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
                bad_attr = re.search(r"::?([A-Za-z_][A-Za-z0-9_]*)\s*[<>=]", failed[0].get("command", ""))
                if self.layers and bad_attr and bad_attr.group(1) not in {l.get("attr") for l in self.layers}:
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
        if open_notes or order_note:
            out["note"] = " ".join(open_notes + ([order_note] if order_note else []))
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


    def _compare_contacts(self, reference: str, other: str, chain: Optional[str],
                          restrict: Optional[str], cutoff: float) -> Dict[str, Any]:
        """Superpose, collect contacts on both models, compare them THROUGH the alignment and draw
        the change. Everything that changes the scene goes through execute_commands.

        `restrict` (a ligand, a pocket, an interface residue) is written for the reference and is
        translated to the other model through the pairing, not by swapping the model id. It is
        independent of `chain`: `chain` limits the partner side of every contact, while the
        restricted atoms are kept whatever their chain, so `chain='A', restrict='/B:87'` asks
        what B87 touches in chain A.
        """
        from .contacts import (atom_part, compare_contacts, contact_commands, model_restriction,
                               translate_restriction)
        if not hasattr(self.executor, "residue_contacts"):
            return {"error": "Contact comparison needs the ChimeraX edition."}
        prep = self.executor.prepare_compare(reference, other, chain)
        if prep.get("error"):
            return prep
        cmd = "matchmaker %s %s" % (prep["other_spec"], prep["ref_spec"]) if self.config.edition == "chimera" \
            else "matchmaker %s to %s" % (prep["other_spec"], prep["ref_spec"])
        mm = self.execute_commands([cmd], origin="compare")
        if not mm.get("ok"):
            return {"error": "matchmaker did not run: %s" % mm.get("error", ""), "details": mm}
        pairing = self.executor.residue_pairing(prep)
        if pairing.get("error"):
            return pairing
        # A restrict spec is written for the reference. The other model's restriction is NOT the
        # same string with the model id swapped: it is built from the residues the reference
        # restriction actually selected, carried across by the alignment, because the same pocket
        # is routinely numbered differently and residue 87 of one entry is not residue 87 of the
        # other. A selector word ('ligand') becomes an intersection, never '#2/ligand'.
        ref_restrict = oth_restrict = None
        if restrict:
            ref_restrict = model_restriction(restrict, prep["ref_id"])
        ref = self.executor.residue_contacts(prep["ref_spec"], cutoff, ref_restrict)
        if ref_restrict and not ref.get("error"):
            # "restrict to #1/A" or "protein" selects the whole chain, which excluded every contact and
            # answered "nothing changed" with confidence; treat a restriction covering most of the chain
            # as no restriction at all
            selected = len(ref.get("restrict_residues") or [])
            if selected and selected >= 0.8 * max(1, int(pairing.get("paired_residues") or selected)):
                ref_restrict = oth_restrict = None
                restrict = None
                ref = self.executor.residue_contacts(prep["ref_spec"], cutoff, None)
        if ref.get("error"):
            return ref
        if ref.get("empty_selection"):
            return {"error": "%s Nothing to compare." % ref.get("reason", "The restriction matched nothing.")}
        unpaired_restrict = ""
        if restrict:
            oth_restrict = translate_restriction(ref.get("restrict_residues") or [], pairing["pairing"],
                                                 prep["other_id"], atom_part(restrict))
            if not oth_restrict:
                unpaired_restrict = ("None of the residues '%s' selects in %s has a counterpart in %s: "
                                     "they are outside the alignment (a ligand, a tag, an unmodelled region)."
                                     % (restrict, prep["ref_spec"], prep["other_spec"]))
        if unpaired_restrict:
            oth = {"contacts": [], "empty_selection": True, "reason": unpaired_restrict}
        else:
            oth = self.executor.residue_contacts(prep["other_spec"], cutoff, oth_restrict)
        if oth.get("error"):
            # never compare against an empty list because the other side failed: that would report
            # every reference contact as lost when in truth nothing was measured
            return {"error": "Contacts could not be collected in %s: %s" % (prep["other_spec"], oth["error"]),
                    "reference_contacts": ref.get("count", 0)}
        missing = oth.get("reason", "") if oth.get("empty_selection") else ""
        res = compare_contacts(ref["contacts"], oth.get("contacts") or [], pairing["pairing"])
        out: Dict[str, Any] = {"reference": prep["ref_spec"], "compared": prep["other_spec"],
                               "chain": prep.get("chain") or "all", "cutoff": cutoff,
                               "restrict": ref_restrict or "", "pairing": pairing.get("basis"),
                               "summary": res["summary"], "counts": res["counts"], "by_kind": res["by_kind"],
                               "lost": res["lost"][:25], "gained": res["gained"][:25],
                               "kind_changed": res["kind_changed"][:25],
                               "commands": mm["results"]}
        if oth_restrict:
            out["compared_restrict"] = oth_restrict
        if res.get("note"):
            out["note"] = res["note"]
        if missing:
            # the restriction names something the other form does not have (a ligand in the apo
            # form): say so, and answer the wider question as well, because "what is lost when it
            # opens" is about the whole chain, not only the ligand pocket
            ref_all = self.executor.residue_contacts(prep["ref_spec"], cutoff, None)
            oth_all = self.executor.residue_contacts(prep["other_spec"], cutoff, None)
            if not ref_all.get("error") and not oth_all.get("error"):
                overall = compare_contacts(ref_all["contacts"], oth_all.get("contacts") or [], pairing["pairing"])
                out["overall"] = {"summary": overall["summary"], "counts": overall["counts"],
                                  "lost": overall["lost"][:15], "gained": overall["gained"][:15]}
                out["report_verbatim"] = ("Report the restricted result first (the restriction matched nothing in %s), then the "
                                          "'overall' summary sentence for the whole chain, verbatim." % prep["other_spec"])
            out["compared_side"] = ("%s Contacts whose residues are both in the alignment are judged as usual; "
                                    "the rest could not be judged and are counted as unmapped, not as lost." % missing)
        if str(pairing.get("basis", "")).startswith("chain id"):
            out["pairing_fallback"] = True
            out["pairing_note"] = ("Residues were paired by chain ID and residue number because no alignment was "
                                   "available; the lost/gained lists are only meaningful if both structures use the same numbering.")
        out["report_verbatim"] = ("Report the 'summary' sentence as it is, then name the specific contacts that broke "
                                  "or formed. Contacts in 'lost'/'gained' are residue pairs, not distances the user measured; "
                                  "'kind_changed' pairs still touch but interact differently, and contacts counted as "
                                  "unmapped were not judged at all - never describe those as lost.")
        cmds = contact_commands(res, prep["ref_spec"], prep["other_spec"])
        out["legend"] = ("In the view: %s is light gray, %s dark gray (cartoons made transparent); red dashes and red side chains "
                         "are contacts present in %s but lost in %s, green ones are contacts gained in %s. Say this in one "
                         "sentence so the user can read the picture." % (prep["ref_spec"], prep["other_spec"], prep["ref_spec"],
                                                                       prep["other_spec"], prep["other_spec"]))
        if cmds:
            col = self.execute_commands(cmds, origin="compare")
            out["drawn"] = bool(col.get("ok"))
            out["commands"] = out["commands"] + col.get("results", [])
            if col.get("skipped"):
                out["note"] = "The user declined the display commands."
            else:
                self.figure_notes.append("Contact comparison: lost contacts red on %s, gained green on %s."
                                         % (prep["ref_spec"], prep["other_spec"]))
        return out


    # ------------------------------------------------------------ published annotations
    def _any_structure_open(self) -> bool:
        try:
            return any(m.get("chains") for m in ((self.executor.get_state() or {}).get("models") or []))
        except Exception:  # noqa: BLE001
            return False

    def _chain_ranges_text(self) -> str:
        try:
            models = (self.executor.get_state() or {}).get("models") or []
        except Exception:  # noqa: BLE001
            models = []
        parts = ["%s/%s %s" % (m.get("id"), c.get("id"), c.get("range")) for m in models for c in (m.get("chains") or []) if c.get("range")]
        return ("residue ranges: " + ", ".join(parts[:12]) + " (first = the lower number, last = the higher)") if parts \
            else "call get_state for the residue range of each chain (first = its lower number, last = its higher)."

    def _broad_first(self, commands: List[str]) -> Tuple[List[str], str]:
        """Run a whole-model command before narrower ones with the same verb, so it does not undo them."""
        out = list(commands)
        moved = []
        for j in range(len(out)):
            c = out[j]
            v = first_word(c)
            if v not in _ORDER_VERBS or _NARROW_SPEC_RE.search(c.split(None, 1)[1] if " " in c.strip() else ""):
                continue
            i = next((k for k in range(j) if first_word(out[k]) == v and _NARROW_SPEC_RE.search(out[k])), None)
            if i is not None:
                out.insert(i, out.pop(j))
                moved.append(c.strip())
        if not moved:
            return commands, ""
        return out, "Ran %s before the narrower %s commands, so it does not undo them." % (
            ", ".join("`%s`" % m for m in moved), first_word(moved[0]))

    def _ambiguous_it(self, commands: List[str]) -> Optional[str]:
        """Several structures open, nothing opened or named in the last two requests, the user says "it": ask which."""
        text = getattr(self, "_turn_text", "") or ""
        if (not getattr(self, "_no_recent_focus", False) or not _PRONOUN_RE.search(text) or _WHICH_NAMED_RE.search(text)
                or len(re.findall(r"[A-Za-z']+", text)) > 10
                or not any(first_word(c) in _ACTION_VERBS for c in commands) or any(first_word(c) == "open" for c in commands)
                or self._tool_called_since(max(0, len(self.conversation) - 6), "ask_user")):
            return None
        try:
            st = self.executor.get_state() or {}
        except Exception:  # noqa: BLE001
            return None
        structs = [m for m in (st.get("models") or []) if m.get("type") in ("AtomicStructure", None) and m.get("chains")]
        if len(structs) < 2 or (st.get("selection") or {}).get("num_atoms"):
            return None
        names = [m.get("name", "") for m in structs]
        if any(n and n.lower() in text.lower() for n in names):
            return None
        opts = ", ".join("%s %s" % (m.get("id"), m.get("name", "")) for m in structs[:4])
        return ("%d structures are open (%s) and the user said \"it\" without saying which. Call ask_user now with "
                "those as options plus 'all of them'; do not guess." % (len(structs), opts))

    def _drop_duplicate_opens(self, commands: List[str]) -> Tuple[List[str], List[str]]:
        """Drop `open <id>` / `open alphafold:<acc>` / `alphafold fetch <acc>` for something already open
        (unless the user asked for another copy) and say which model to use instead."""
        if not any(_OPEN_TARGET_RE.match(c) for c in commands) or _OPEN_AGAIN_RE.search(getattr(self, "_turn_text", "") or ""):
            return commands, []
        try:
            models = (self.executor.get_state() or {}).get("models") or []
        except Exception:  # noqa: BLE001
            return commands, []
        names = [(str(m.get("id", "")), str(m.get("name", "")).lower()) for m in models]
        kept, notes, seen = [], [], set()
        for c in commands:
            m = _OPEN_TARGET_RE.match(c)
            if not m:
                kept.append(c)
                continue
            pdb, acc = (m.group(1) or "").lower(), (m.group(2) or m.group(3) or "").lower()
            key = pdb or acc
            hit = next((mid for mid, n in names if (pdb and (n == pdb or n.startswith(pdb + " ")))
                        or (acc and ("alphafold " + acc) in n)), None)
            if key in seen:
                notes.append("Skipped `%s`: it is already being opened in this batch." % c.strip())
                continue
            seen.add(key)
            if hit is None or re.search(r"\b%s\b" % re.escape(key), getattr(self, "_turn_text", "") or "", re.I):
                kept.append(c)      # not open, or the user asked for it by name: their call
                continue
            if m.group(3) and re.search(r"\bpae\b", c, re.I):
                notes.append("Skipped `%s`: %s is already open as %s. For its PAE use `alphafold pae %s` (add "
                             "colorDomains true for domains)." % (c.strip(), key.upper(), hit, hit))
            else:
                notes.append("Skipped `%s`: %s is already open as %s. Work on %s; do not open another copy."
                             % (c.strip(), key.upper(), hit, hit))
        return kept, notes

    def _source_guard(self, source: str) -> Optional[str]:
        """One per-residue data source per request unless the user named more: refuse a source the user did not
        ask for when they named another one, or a second coloring on top of the first. Returns an error or None."""
        text = getattr(self, "_turn_text", "") or ""
        if not text or not self.config.guards:   # `pellaeon tool ...` commands: the user picked the tool themselves
            return None
        named = [k for k, rx in _SOURCE_NAMED_RE.items() if rx.search(text)]
        if named and source not in named:
            return ("The user asked for %s, not %s. Use %s instead, and nothing else."
                    % (" and ".join(_SOURCE_LABEL[n] for n in named), _SOURCE_LABEL[source], _SOURCE_HOW[named[0]]))
        done = [d for d in getattr(self, "_coloring_sources", []) if d != source]
        if done and source not in named:
            return ("This request already used %s. The user did not ask for %s as well: report that result now. You may "
                    "mention in one sentence that %s is also available, without running it."
                    % (_SOURCE_LABEL[done[0]], _SOURCE_LABEL[source], _SOURCE_LABEL[source]))
        if source not in getattr(self, "_coloring_sources", []):
            self._coloring_sources = list(getattr(self, "_coloring_sources", [])) + [source]
        return None

    def _fetch_annotation(self, source: str, protein: str, model: str, chain: str) -> Dict[str, Any]:
        """AlphaMissense or ConSurf as a table overlay: the dataset lands in self.tables like a
        user-loaded CSV, so placement, the key, the layers list and the figure legend come for free."""
        from .annot_sources import alphamissense, conservation
        from .tables import guess_columns
        if not protein.strip():
            return {"error": "fetch_annotation needs the protein: a UniProt accession for AlphaMissense, a PDB ID for conservation."}
        if self._model_missing(model):
            return {"error": "No model matches '%s'. Open the structure first." % model}
        cache = None
        clinvar = getattr(self.executor, "clinvar", None)
        if clinvar is not None and getattr(clinvar, "cache_dir", None):
            cache = os.path.join(os.path.dirname(clinvar.cache_dir.rstrip(os.sep)), "annotations")
        src = source.strip().lower()
        if src.startswith("alpha"):
            acc, err = self._accession_for(protein)
            if err:
                return {"error": err}
            ds = alphamissense(acc, cache)
        else:
            ds = conservation(protein, chain, cache)
        if ds.get("error"):
            return {"error": ds["error"], "source": src}
        name = ds["name"]
        self.tables[name] = {"id": name, "name": name, "path": ds.get("source_url", ""), "columns": ds["columns"],
                             "rows": ds["rows"], "guess": guess_columns(ds["columns"], ds["rows"]),
                             "delimiter": ds.get("delimiter", ","), "accession": ds.get("accession", "")}
        column = "am_mean" if src.startswith("alpha") else "consurf_grade"
        payload = self._table_overlay(name, column, chain or None, ds.get("palette") or "blue-white-red", model,
                                      ds.get("accession") or None, False)
        payload = dict(payload, source=ds["source"], dataset=name, n_positions=ds.get("n_positions"),
                       value_range=ds.get("value_range"))
        if ds.get("note"):
            payload["note"] = ds["note"]
        if "error" not in payload:
            self.figure_notes.append("%s: %s." % (ds["source"], payload.get("summary") or column))
        return payload



    def _background_is_dark(self) -> bool:
        try:
            bg = str((self.executor.get_state() or {}).get("background") or "")
        except Exception:  # noqa: BLE001
            return False
        nums = [int(x) for x in re.findall(r"\d+", bg)][:3]
        return bool(nums) and sum(nums) / len(nums) <= 140

    # ------------------------------------------------------------ one legend at a time
    def _one_legend(self, commands: List[str]) -> List[str]:
        """Keys and titles replace the previous ones instead of piling up.

        A second `key` already replaces the first, but every overlay also wrote a plain 2D label
        as its title, and those accumulated at the same spot: after a comparison followed by a
        contact comparison the two titles sat on top of each other. Pellaeon's title is a named
        label now, changed in place once it exists, and a stale key is deleted before a new one.
        """
        out: List[str] = []
        for c in commands:
            low = c.strip().lower()
            if low in ("close", "close #all", "close all", "close session") or low.startswith("close session"):
                self._legend_title = False
            if low.startswith("key ") and not low.startswith("key delete"):
                if not any(o.strip().lower().startswith("key delete") for o in out):
                    out.append("key delete")
            if low.startswith("2dlabels create pellaeon_title "):
                if self._background_is_dark():
                    c = re.sub(r"\bcolor black\b", "color white", c)
                if getattr(self, "_legend_title", False):
                    c = "2dlabels change pellaeon_title " + c.strip()[len("2dlabels create pellaeon_title "):]
                self._legend_title = True
            out.append(c)
        return out

    # ------------------------------------------------------------ numbering
    def _accession_for(self, protein: str) -> Tuple[str, str]:
        """(accession, error) for a UniProt accession or a gene/protein name."""
        from .annotate import is_accession
        p = (protein or "").strip()
        if not p:
            return "", ""
        if is_accession(p):
            return p.upper(), ""
        r = self.executor.resolve_protein(p, "human")
        if not r.get("accession"):
            return "", "Could not find a UniProt entry for '%s'." % protein
        return str(r["accession"]), ""

    def _map_numbering(self, model: str, protein: str, positions: List[int]) -> Dict[str, Any]:
        """UniProt positions -> this structure's residues, through the chain's own UniProt alignment.

        Structure numbering routinely differs from the sequence database: a missing initiator Met,
        a purification tag, a construct that starts at residue 20. Coloring "residue 159" from a paper
        without this step colors the wrong residue in silence, which is the kind of mistake nobody
        notices until a figure is in print.
        """
        if not positions:
            return {"error": "map_numbering needs at least one UniProt position."}
        if self._model_missing(model):
            return {"error": "No model matches '%s'. Open the structure first." % model}
        acc, err = self._accession_for(protein)
        if err:
            return {"error": err}
        if not acc:
            # no protein named: take the chain's own UniProt entry from the structure's metadata
            info = self.executor.chain_uniprot(model) if hasattr(self.executor, "chain_uniprot") else {}
            ids = info.get("accessions") or []
            if len(ids) == 1:
                acc = ids[0]
            elif len(ids) > 1:
                return {"error": "Model %s has chains from several UniProt entries (%s): say which protein." % (model, ", ".join(ids))}
            else:
                return {"error": "The structure file does not say which UniProt entry it is; give the accession or gene name."}
        mapping = self.executor.map_positions(model, acc, sorted(set(positions)))
        if mapping.get("error"):
            return mapping
        got = mapping.get("map") or {}
        rows = []
        for p in sorted(set(positions)):
            m = got.get(p) or got.get(str(p))
            if m:
                rows.append({"uniprot": p, "chain": m.get("chain"), "residue": m.get("number"), "name": m.get("resname"),
                             "spec": "%s/%s:%s" % (mapping.get("model", model), m.get("chain"), m.get("number"))})
            else:
                rows.append({"uniprot": p, "missing": True})
        found = [r for r in rows if not r.get("missing")]
        offsets = sorted({r["residue"] - r["uniprot"] for r in found if isinstance(r.get("residue"), int)})
        out = {"model": mapping.get("model", model), "accession": mapping.get("accession", acc), "positions": rows,
               "found": len(found), "missing": [r["uniprot"] for r in rows if r.get("missing")],
               "note": mapping.get("note", "")}
        if len(offsets) == 1:
            out["offset"] = offsets[0]
            out["summary"] = ("Structure numbering = UniProt %s %d for these positions." %
                              ("+" if offsets[0] >= 0 else "-", abs(offsets[0])) if offsets[0] else
                              "Structure numbering matches UniProt numbering for these positions.")
        elif offsets:
            out["summary"] = "The offset varies along the chain (%s): use the per-position specs, not a single shift." % (
                ", ".join(str(o) for o in offsets[:6]))
        return out

    # ------------------------------------------------------------ figure style reuse
    _STYLE_WORDS = ("color", "colour", "style", "cartoon", "surface", "show", "hide", "lighting", "graphics", "set",
                    "material", "preset", "transparency", "size", "nucleotides", "rainbow", "label", "2dlabels", "key",
                    "camera", "clip", "windowsize", "cofr", "view")

    def _apply_figure_style(self, figure: str, model: str = "") -> Dict[str, Any]:
        """Replay a figure bundle's styling on the open models.

        A bundle's .cxc is the record of everything that produced the figure, including the opens,
        the saves and the closes. Only the styling is wanted back: anything that would fetch, write or
        remove something is left out, and what remains still goes through execute_commands, so the
        usual confirmation applies to anything risky.
        """
        if not figure.strip():
            return {"error": "Which figure? Give its name (the folder in the figures directory) or a path."}
        base = self.figures_dir or os.path.join(os.path.expanduser("~"), "Pellaeon figures")
        cand = [figure, os.path.join(base, figure), os.path.join(base, figure, figure + ".cxc")]
        path = ""
        for c in cand:
            c = os.path.expanduser(c)
            if os.path.isfile(c) and c.endswith(".cxc"):
                path = c
                break
            if os.path.isdir(c):
                scripts = [f for f in os.listdir(c) if f.endswith(".cxc")]
                if scripts:
                    path = os.path.join(c, sorted(scripts)[0])
                    break
        if not path:
            try:
                have = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
            except OSError:
                have = []
            return {"error": "No figure bundle named '%s'.%s" % (figure, (" Saved figures: " + ", ".join(have[:12])) if have else "")}
        cmds = []
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                w = first_word(line)
                if w not in self._STYLE_WORDS or line.startswith("view name") or line.startswith("view initial"):
                    continue
                if w == "view" and len(line.split()) > 1 and not line.split()[1].startswith(("#", "sel", "matrix")):
                    continue   # restoring a saved view of another structure makes no sense here
                if model:
                    line = re.sub(r"#\d+(?:\.\d+)*", model, line)
                cmds.append(line)
        if not cmds:
            return {"error": "That bundle's script has no styling commands to reuse (%s)." % os.path.basename(path)}
        run = self.execute_commands(cmds, origin="style")
        n_ok = sum(1 for r in run.get("results") or [] if r.get("ok"))
        out = {"figure": os.path.basename(os.path.dirname(path)) or path, "script": path, "commands": cmds,
               "applied": n_ok, "of": len(cmds), "ok": run.get("ok", False),
               "summary": "Applied %d of %d styling commands from %s." % (n_ok, len(cmds), os.path.basename(path))}
        if run.get("error"):
            out["error"] = run["error"]
        return out


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

    def _refreshed_context(self, ctx: str) -> str:
        """The turn's context with its state block brought up to date. A nudge used to carry the state from before
        the turn ('No models are open' after the model had opened two) and the model believed it."""
        try:
            state = self.executor.get_state()
            if self.tables and isinstance(state, dict):
                state["tables"] = self._tables_state()
            fresh = "<chimerax_state>\n%s\n</chimerax_state>" % prompt_mod.format_state(state or {})
        except Exception:  # noqa: BLE001
            return ctx
        if ctx and "<chimerax_state>" in ctx:
            return re.sub(r"<chimerax_state>\n.*?\n</chimerax_state>", lambda _m: fresh, ctx, count=1, flags=re.S)
        return fresh

    def _text_since(self, start_len: int) -> str:
        """The last non-empty assistant text of this turn ('' if the model said nothing in words)."""
        for m in reversed(self.conversation[start_len:]):
            if m.role == "assistant":
                t = m.text().strip()
                if t:
                    return t
        return ""

    def _commands_ran_since(self, start_len: int) -> bool:
        since = (getattr(self, "_turn_starts", []) or [0.0])[-1]
        return any(j.get("ok") and not j.get("noop") and float(j.get("ts", 0)) >= since for j in self.journal[-40:])

    _CLAIM_RE = re.compile(r"\b(i'?ve|i have|has been|have been|is now|are now|now (shown|colou?red|displayed|hidden|visible))\b", re.I)
    _REFUSAL_RE = re.compile(r"\b(cannot|can'?t|can not|unable|not (possible|able|supported|available)|no (such|built-?in|way|command)|"
                             r"does ?n[o']?t (support|have|exist|offer|provide)|isn'?t (possible|supported|available))\b", re.I)

    def _fallback_wanted(self, text: str) -> bool:
        """The commands-only fallback is for a model that meant to act: it said nothing, announced or claimed an action,
        or wrote the commands as text. A reply that explains why it cannot be done is an answer, not a failed call."""
        t = (text or "").strip()
        if not t:
            return True
        if self._REFUSAL_RE.search(t):
            return False
        if _ANNOUNCE_RE.search(t) or self._CLAIM_RE.search(t):
            return True
        known = self._known_commands()
        return any(first_word(x) in known for x in re.findall(r"`([^`\n]+)`", t)) if known else False

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

    def _model_missing(self, spec: str) -> bool:
        """True when `spec` matches nothing in the session (and we could actually check)."""
        try:
            got = self.executor.spec_atoms(spec or "")
        except Exception:  # noqa: BLE001
            return False
        return bool(got.get("used")) and got.get("atoms", 0) == 0

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
        # Hemoglobin is two gene products (alpha on A/C, beta on B/D). Asked for "the disease
        # variants on this structure", models picked one in silence. If the structure carries several
        # UniProt entries and the user named none of them, that is a question, not a guess.
        if hasattr(self.executor, "chain_uniprot"):
            try:
                entries = [e for e in (self.executor.chain_uniprot(model).get("accessions") or []) if e]
            except Exception:  # noqa: BLE001
                entries = []
            if len(entries) > 1:
                said = self._user_text_upper
                if acc.upper() not in said and (gene or "").upper() not in said and not any(e in said for e in entries):
                    return {"error": "Model %s has chains from several proteins (UniProt %s) and the user did not say which. "
                                     "Ask with ask_user which protein or chain they mean before annotating." % (model, ", ".join(entries)),
                            "ambiguous": entries}
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
            # Check the target exists before reporting "nothing to annotate": with an empty session this
            # returned quietly and the model announced the structure as open. map_positions would have
            # caught it, but it is only reached when there are items to place.
            if self._model_missing(model):
                return {"error": "No model matches '%s'. Open the structure first, then annotate it." % model}
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
            # which disease each pathogenic variant is recorded for, so "which of these cause X" is answered from data
            out["conditions"] = dict(list({it["label"]: it["conditions"] for it in items
                                           if it.get("pathogenic") and it.get("conditions")}.items())[:40])
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
