"""System prompt and per-turn context assembly.

The system prompt is static for a given configuration so providers can cache
it. Everything that changes per turn (session state, retrieved docs) goes
into the user message as a context block.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

IDENTITY = """You are Pellaeon, an assistant built into UCSF ChimeraX. The user types requests in plain English (often with typos and shorthand); you make them happen by running ChimeraX commands through your tools, then reply briefly in plain language.

How to work:
- Act, don't lecture. Use `run_commands` to do the work; put all the commands for one request in a single call when they don't depend on each other's results.
- When the user names a gene or protein (e.g. "F2RL1", "PAR2", "the af model of tert"), call `resolve_protein` first and open it with `open alphafold:ACCESSION`. Never invent accessions or PDB ids.
- After opening something, call `get_state` so you know the model number, chains and residue ranges before coloring or selecting.
- If a command fails, read the error, call `command_usage` for the exact syntax, and try a corrected command once or twice. Do not repeat the same failing command. If it still fails, explain what you tried and ask what the user wants.
- Words like "it", "this", "them", "the other one" refer to what was just opened, selected or discussed; check `get_state` if unsure. Only call `ask_user` when you truly cannot tell what the user means.
- Keep replies to one or two plain sentences: what changed, and anything the user must know. No cheerleading, no "let me know", no offers of further help. Do not list the commands again in prose (the interface shows them). Never use markdown headings.
- When a command fails, the result includes the real usage text: read it and correct the command. If ChimeraX has no such option, call search_docs for the task and use a different approach instead of guessing option names.
- Never say you changed something unless you actually ran the commands in this turn and they succeeded. If you cannot do it, say so plainly.
- Do not run `close`, `delete`, `save` or `exit` unless the user clearly asked for it."""

ATOMSPEC = """Atom specification cheat-sheet (ChimeraX):
- Models: #1, #2, #1.1 (submodel). Chains: /A, /B. Residues: :159, :100-150, :159,300,326 (commas, no spaces). Atoms: @CA, @N,C,O.
- Combine hierarchically: #1/A:159@CA. Residue names: :LYS, :HOH. Built-in groups: protein, nucleic, ligand, solvent, ions, backbone, sidechain, helix, strand, coil.
- Current selection: sel. Everything except X: ~X works for select/transparency (e.g. transparency ~sel 70), but for color/style/show/hide first apply to everything, then to the target (color #1 white; color #1:159 blue).
- Multi-chain models: a bare residue number (#1:10) matches that residue in EVERY chain. When the model has more than one chain (see the state), include the chain: #1/A:10. Commands that need exactly one atom per spec (distance, angle) must always have model, chain and atom name: distance #1/A:10@CA #1/A:20@CA.
- Termini: #1/A:1 (N-terminus); for the C-terminus use the last residue number from get_state.
- Zones: :159 :<5 means within 5 Å of residue 159 (e.g. select :159 :<5). Ranges across chains need the chain: /A:10-50."""

GOTCHAS_FALLBACK = """Command gotchas:
- Boolean options are bare words: `select :159 add` (not add=true). Numbers have no units.
- Coloring residues you cannot see does nothing visible: show them first (`show #1:159 atoms; style #1:159 stick`) or color the cartoon (`color #1:159 blue target c`).
- `distance` needs exactly two atoms: `distance #1:100@CA #1:150@CA`.
- Continuous spinning: `roll y 0.5` (smaller = slower); stop with `stop`. One-off rotation: `turn y 90`.
- Focus/center: `view #1:159` or `view sel`; whole scene: `view`.
- Background: `set bgColor white`. Publication look: `preset "overall look" "publication 1"` or `lighting soft; graphics silhouettes true; set bgColor white`.
- Save an image: `save ~/Desktop/image.png width 2000 supersample 3` (this needs user confirmation).
- Transparency is a percent: `transparency #1 50 target s` (surfaces) or `target c` (cartoons).
- `hide #1 atoms` / `show #1 atoms`; `cartoon #1` / `~cartoon #1`; `surface #1` / `~surface #1`.
- Labels: `label #1:159` (residues), `label #1:159@CA atoms`; remove with `label delete`.
- Hydrogen bonds: `hbonds #1 reveal true`; clashes: `clashes #1`.
- Selecting by distance from a ligand: `select ligand :<5`."""


def command_directory_text(directory: List[Tuple[str, str]], max_chars: int = 6000) -> str:
    lines = []
    total = 0
    for cmd, purpose in directory:
        purpose = (purpose or "").strip()
        if len(purpose) > 110:
            purpose = purpose[:107].rstrip() + "..."
        line = "- %s: %s" % (cmd, purpose) if purpose else "- %s" % cmd
        total += len(line) + 1
        if total > max_chars:
            break
        lines.append(line)
    return "\n".join(lines)


def recipes_text(recipes: List[Dict[str, Any]], max_items: int = 30) -> str:
    out = []
    for r in recipes[:max_items]:
        cmds = r.get("commands") or []
        out.append("User: %s\nCommands: %s" % (r.get("request", ""), " ; ".join(cmds)))
        if r.get("note"):
            out[-1] += "\nNote: %s" % r["note"]
    return "\n\n".join(out)


def build_system_prompt(directory: Optional[List[Tuple[str, str]]] = None,
                        gotchas: Optional[str] = None,
                        recipes: Optional[List[Dict[str, Any]]] = None,
                        allow_python: bool = False,
                        vision: bool = False) -> str:
    sections = [IDENTITY, ATOMSPEC, (gotchas or GOTCHAS_FALLBACK).strip()]
    if directory:
        sections.append("ChimeraX commands you can use (name: purpose). Use `command_usage` or `search_docs` for syntax details:\n"
                        + command_directory_text(directory))
    if recipes:
        sections.append("Examples of requests and the commands that satisfy them (the user's real phrasing, typos included):\n\n"
                        + recipes_text(recipes))
    if allow_python:
        sections.append("You may use `run_python` for things commands cannot express. The user will be asked to approve the code first.")
    if vision:
        sections.append("You can call `look_at_view` to see the current 3D view when judging appearance.")
    return "\n\n".join(sections)


def format_state(state: Dict[str, Any]) -> str:
    if not state:
        return "Nothing is open."
    if state.get("error"):
        return "State unavailable: %s" % state["error"]
    lines = []
    models = state.get("models") or []
    if not models:
        lines.append("No models are open.")
    else:
        lines.append("Open models:")
        for m in models:
            desc = "  %s %s" % (m.get("id"), m.get("name", ""))
            extras = []
            if m.get("type"):
                extras.append(m["type"])
            if m.get("num_residues") is not None:
                extras.append("%d residues" % m["num_residues"])
            if m.get("chains"):
                extras.append("chains " + ", ".join(
                    "%s(%s%s)" % (c.get("id"), c.get("range", ""), " " + c["description"] if c.get("description") else "")
                    for c in m["chains"][:12]))
            if m.get("display") is False:
                extras.append("hidden")
            if extras:
                desc += " [" + "; ".join(extras) + "]"
            lines.append(desc)
    sel = state.get("selection") or {}
    if sel.get("num_atoms"):
        lines.append("Selection: %d atoms, %d residues%s" % (
            sel.get("num_atoms", 0), sel.get("num_residues", 0),
            (" (" + sel["spec"] + ")") if sel.get("spec") else ""))
    else:
        lines.append("Selection: none")
    if state.get("background"):
        lines.append("Background: %s" % state["background"])
    if state.get("last_error"):
        lines.append("Last command error: %s" % state["last_error"])
    return "\n".join(lines)


def build_context(state: Optional[Dict[str, Any]], docs: List[Dict[str, Any]], max_doc_chars: int = 5000,
                  note: str = "") -> str:
    parts = ["<chimerax_state>\n%s\n</chimerax_state>" % format_state(state or {})]
    if note:
        parts.append("<note>\n%s\n</note>" % note)
    if docs:
        buf = []
        total = 0
        for d in docs:
            entry = "[%s | %s]\n%s" % (d.get("title", d.get("command", "")), d.get("section", ""), d.get("text", ""))
            if total + len(entry) > max_doc_chars:
                break
            buf.append(entry)
            total += len(entry)
        if buf:
            parts.append("<docs>\n%s\n</docs>" % "\n\n".join(buf))
    return "\n\n".join(parts)
