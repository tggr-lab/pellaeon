"""Classify ChimeraX commands as safe to auto-run or needing confirmation.

The gate is deliberately simple and conservative: anything that closes or
deletes data, writes files, runs arbitrary code, changes the install, or
loops forever asks first. Everything else (display, color, select, measure,
open from databases, view changes) runs immediately in "auto" mode.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

AUTONOMY_ASK = "ask"          # confirm every batch
AUTONOMY_AUTO = "auto"        # confirm only risky commands (default)
AUTONOMY_ALL = "all"          # never confirm (this session only)
AUTONOMY_MODES = (AUTONOMY_ASK, AUTONOMY_AUTO, AUTONOMY_ALL)

# first word (after stripping a leading "~") -> reason
_CONFIRM_FIRST_WORD = {
    "close": "closes models or the whole session",
    "delete": "deletes atoms, bonds or residues",
    "save": "writes a file to disk",
    "exit": "quits ChimeraX",
    "quit": "quits ChimeraX",
    "runscript": "runs a script file",
    "pip": "changes installed Python packages",
    "toolshed": "installs or removes ChimeraX bundles",
    "devel": "builds or installs bundles",
    "remotecontrol": "opens a network control port",
    "mcp": "opens a network control port",
    "perframe": "starts a per-frame loop",
    "alias": "redefines commands",
    "cd": "changes the working directory",
    "rename": "renames models or chains",
    "changechains": "changes chain identifiers",
    "renumber": "renumbers residues",
    "combine": "creates a combined copy of models",
    "split": "splits models",
    "build": "modifies atoms",
    "swapaa": "mutates residues",
    "swapna": "mutates nucleotides",
    "addh": "adds hydrogens (modifies the model)",
    "addcharge": "modifies the model",
    "dockprep": "modifies the model",
    "movie": "records or writes movie files",
    "snapshot": "writes an image file",
    "meeting": "opens a network connection to other computers",
    "system": "runs a system command",
    "copy": "writes an image or file",
    "write": "writes a structure file",
    "export": "writes a scene file",
}
_CONFIRM_SUBCOMMANDS = {("log", "save"): "writes a file to disk"}
_SAFE_URL_SUFFIXES = (".pdb", ".cif", ".mmcif", ".ent", ".mol2", ".sdf", ".mol", ".xyz", ".mrc", ".map", ".ccp4",
                      ".mtz", ".fasta", ".fa", ".aln", ".msf", ".pir", ".sto", ".gz", ".png", ".jpg", ".html", ".htm", ".pae")

# "open" of a database identifier is safe; opening local files is not.
_DB_PREFIXES = ("pdb:", "alphafold:", "esmfold:", "emdb:", "uniprot:", "modelcif:",
                "cellpack:", "pubchem:", "ccd:", "eds:", "edsdiff:", "emdbfit:", "alphafolddb:")
_URL_RE = re.compile(r"^https?://", re.I)
_PDB_ID_RE = re.compile(r"^[0-9][A-Za-z0-9]{3}$")
_EMDB_RE = re.compile(r"^emd[-_]?\d+$", re.I)


@dataclass
class Classification:
    command: str
    confirm: bool
    reason: str = ""


def first_word(cmd: str) -> str:
    cmd = cmd.strip()
    if cmd.startswith("~"):
        cmd = cmd[1:]
    m = re.match(r"[A-Za-z][A-Za-z0-9_]*", cmd)
    return m.group(0).lower() if m else ""


def _open_targets(cmd: str) -> List[str]:
    rest = cmd.strip().split(None, 1)
    if len(rest) < 2:
        return []
    args = rest[1]
    # stop at first keyword-like token (e.g. "format", "fromDatabase")
    toks = []
    for tok in args.split():
        if re.match(r"^[a-z][A-Za-z]+$", tok) and tok.lower() in (
                "format", "fromdatabase", "ignorecache", "name", "maxmodels", "coordsets",
                "autostyle", "structurefactors", "combine", "atomic"):
            break
        toks.append(tok.strip(",;"))
    return toks


def _is_database_open(target: str) -> bool:
    t = target.strip().strip('"').strip("'")
    if _URL_RE.match(t):
        # ChimeraX runs remote .py/.cxc files; only plain data files are safe
        path = t.split("?")[0].split("#")[0].lower()
        return path.endswith(_SAFE_URL_SUFFIXES)
    low = t.lower()
    if any(low.startswith(p) for p in _DB_PREFIXES):
        return True
    if _PDB_ID_RE.match(t) or _EMDB_RE.match(t):
        return True
    return False


def _expand_abbreviation(word: str) -> str:
    """ChimeraX accepts unique prefixes (clo -> close); map risky prefixes to the full word."""
    if word in _CONFIRM_FIRST_WORD or len(word) < 2:
        return word
    matches = [w for w in _CONFIRM_FIRST_WORD if w.startswith(word)]
    return matches[0] if matches else word


def classify(cmd: str) -> Classification:
    word = _expand_abbreviation(first_word(cmd))
    if not word:
        return Classification(cmd, False)
    if word in _CONFIRM_FIRST_WORD:
        return Classification(cmd, True, _CONFIRM_FIRST_WORD[word])
    toks = cmd.strip().lstrip("~").split()
    if len(toks) >= 2 and (word, toks[1].lower()) in _CONFIRM_SUBCOMMANDS:
        return Classification(cmd, True, _CONFIRM_SUBCOMMANDS[(word, toks[1].lower())])
    if word == "open":
        for target in _open_targets(cmd):
            if not _is_database_open(target):
                return Classification(cmd, True, "opens a local file: %s" % target)
        return Classification(cmd, False)
    return Classification(cmd, False)


def needs_confirmation(commands: List[str], mode: str) -> List[Classification]:
    """Return the classifications that require the user's OK under ``mode``."""
    if mode == AUTONOMY_ALL:
        return []
    cls = [classify(c) for c in commands]
    if mode == AUTONOMY_ASK:
        return cls
    return [c for c in cls if c.confirm]


def split_commands(text: str) -> List[str]:
    """Split a ';'-joined command string into individual commands."""
    out = []
    for piece in text.split(";"):
        piece = piece.strip()
        if piece and not piece.startswith("#"):
            out.append(piece)
    return out
