"""Recover tool calls that weak models write as text, and read plain command lines.
Small local models often emit `run_commands "color #1 red"` or a JSON blob as ordinary text instead of a structured
tool call. Executing what they clearly meant, through the same safety gate, turns a failed turn into a working one."""
from __future__ import annotations

import json
import re
from typing import Iterable, List, Optional, Set

from .safety import first_word, split_commands

# ChimeraX command words a reply may legitimately contain (the executor's directory is merged in at runtime)
KNOWN_COMMANDS: Set[str] = set("""
2dlabels alias align angle bond build bumps cartoon cd changechains clashes close cofr color colour colordef contacts coordset
coulombic crossfade crystalcontacts define delete device distance dssp echo fitmap fly graphics hbonds help hide hkcage info
interfaces key label lighting log marker matchmaker material measure meeting mlp morph movie mseries name nucleotides open
palette perframe preset rainbow rename resfit rmsd rock roll runscript save select sequence set setattr shape show size
split stop style surface swapaa sym tile toolshed torsion transparency turn ui undo view volume vseries wait windowsize zoom
""".split())
_TOOL_NAMES = ("run_commands",)


def _valid(cmd: str, known: Set[str]) -> bool:
    w = first_word(cmd)
    return bool(w) and (w in known or w.rstrip("s") in known) and len(cmd) < 400


def _clean_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", line)          # bullets / numbering
    line = line.strip("`").strip()
    line = re.sub(r"^(?:run|type|enter|use|execute)\s*:\s*", "", line, flags=re.I)
    return line.strip().strip("`").strip()


def _from_json_like(text: str) -> List[str]:
    """{"name": "run_commands", "arguments": {"commands": [...]}} and run_commands{commands:[...]} shapes."""
    out: List[str] = []
    for m in re.finditer(r"\{[^{}]*\"?commands\"?\s*:\s*\[(.*?)\][^{}]*\}", text, re.S):
        inner = m.group(1)
        for q in re.findall(r"\"((?:[^\"\\\\]|\\\\.)*)\"|'((?:[^'\\\\]|\\\\.)*)'|<\|\"\|>(.*?)<\|\"\|>", inner, re.S):
            piece = next((p for p in q if p), "")
            out.extend(split_commands(piece))
        if not out:
            for piece in re.split(r",", inner):
                piece = piece.strip().strip("\"'")
                if piece:
                    out.extend(split_commands(piece))
    if out:
        return out
    try:
        obj = json.loads(text.strip())
        args = obj.get("arguments") or obj.get("parameters") or obj.get("args") or obj
        cmds = args.get("commands") if isinstance(args, dict) else None
        if isinstance(cmds, str):
            cmds = [cmds]
        if isinstance(cmds, list):
            for c in cmds:
                out.extend(split_commands(str(c)))
    except Exception:  # noqa: BLE001
        pass
    return out


def parse_textual_tool_call(text: str, known: Optional[Iterable[str]] = None) -> List[str]:
    """Commands a model clearly meant to run but wrote as text. Empty when the reply is ordinary prose."""
    if not text or "run_commands" not in text and "<tool_call>" not in text and "```" not in text:
        return []
    kn = set(KNOWN_COMMANDS) | set(known or [])
    cands: List[str] = []
    # <tool_call>{...}</tool_call> or bare JSON
    for m in re.finditer(r"<tool_call>(.*?)</tool_call>", text, re.S | re.I):
        cands.extend(_from_json_like(m.group(1)))
    if not cands and "run_commands" in text:
        cands.extend(_from_json_like(text))
    if not cands:
        # run_commands "a; b"   /   run_commands ["a", "b"]   /   run_commands hide #1 atoms
        for m in re.finditer(r"run_commands\s*[:(]?\s*(.+)", text):
            rest = m.group(1).strip().rstrip(")").strip()
            if rest.startswith("["):
                for q in re.findall(r"\"((?:[^\"\\\\]|\\\\.)*)\"|'((?:[^'\\\\]|\\\\.)*)'", rest):
                    cands.extend(split_commands(next((p for p in q if p), "")))
            elif rest.startswith(("\"", "'")):
                cands.extend(split_commands(rest.strip("\"'")))
            else:
                cands.extend(split_commands(rest.splitlines()[0]))
    if not cands and "```" in text:
        for block in re.findall(r"```[a-zA-Z]*\n(.*?)```", text, re.S):
            lines = [_clean_line(l) for l in block.splitlines() if l.strip()]
            if lines and all(_valid(l, kn) for l in lines):
                for l in lines:
                    cands.extend(split_commands(l))
    cmds = [c.strip() for c in cands if c and c.strip()]
    cmds = [c for c in cmds if _valid(c, kn)]
    return cmds[:20]


def parse_command_lines(text: str, known: Optional[Iterable[str]] = None) -> List[str]:
    """Every line of a commands-only reply that is a ChimeraX command (bullets, backticks and 'Run:' prefixes stripped)."""
    kn = set(KNOWN_COMMANDS) | set(known or [])
    out: List[str] = []
    for raw in (text or "").splitlines():
        line = _clean_line(raw)
        if not line or line.startswith(("#", "//")):
            continue
        for piece in split_commands(line):
            if _valid(piece, kn):
                out.append(piece.strip())
    return out[:20]
