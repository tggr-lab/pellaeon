"""Pellaeon's analysis tools as plain ChimeraX commands, no model involved:

    pellaeon tool contacts #1 #2 [chain A] [restrict :HEM] [cutoff 4.0]
    pellaeon tool compare #1 #2 [chain A]
    pellaeon tool annotate #1 P07550 variant [color orange] [label true]
    pellaeon tool fetch alphamissense P07550 [model #1] [chain A]
    pellaeon tool table /path/scores.csv [column hydropathy] [model #1] [chain A] [palette viridis]
    pellaeon tool tidy [keep sel]
    pellaeon tool explain #1/A:87
    pellaeon tool identity #1-4 [chain A]
    pellaeon tool closewindows
    pellaeon tool undo
    pellaeon tool membrane #1 [pdb 3vw7] [slabs true]
    pellaeon tool gpcr P25116 [open inactive,active]
    pellaeon tool list

The same code the chat panel calls, driven from the command line, a .cxc script, or an external agent
through ChimeraX's own `mcp` bridge (its run_command tool). Results go to the Log; when the panel is
open its agent is used, so tables loaded there are visible here and vice versa.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from chimerax.core.commands import (BoolArg, CmdDesc, FloatArg, OpenFileNameArg, StringArg,
                                    register)
from chimerax.core.errors import UserError

TOOLS = [
    ("contacts", "compare_contacts", "lost, gained and kept contacts between two conformations, drawn red and green"),
    ("compare", "compare_structures", "superpose two models and color the reference by residue displacement"),
    ("annotate", "annotate", "color a model by UniProt features or ClinVar variants for an accession"),
    ("fetch", "fetch_annotation", "color by AlphaMissense pathogenicity (UniProt accession) or ConSurf conservation (PDB id)"),
    ("table", "table_overlay", "color a model by a column of a residue table (CSV/TSV)"),
    ("tidy", "tidy_labels", "move or drop overlapping residue labels so every label is readable"),
    ("explain", "explain_residue", "why a residue has its current color: the command, table, annotation or comparison"),
    ("identity", "sequence_identity", "percent sequence identity between every pair of chains, as a table, no windows"),
    ("closewindows", "close_windows", "close sequence viewers and their alignments"),
    ("undo", "undo_last_request", "undo the last request: close the model(s) it opened, or restore the whole session"),
    ("membrane", "membrane_view", "orient a membrane protein from OPM: extracellular up, membrane planes drawn"),
    ("axis", "view_axis", "look down a principal axis: symmetry axis of a ring or capsid, rod end-on or broadside"),
    ("gpcr", "gpcr_states", "GPCRdb structures of a receptor, and its inactive/active AlphaFold-Multistate models"),
]


def _agent(session):
    """The panel's agent when the panel is open (shared tables and journal), else a headless one kept on the session."""
    try:
        from .tool import _INSTANCES
        inst = _INSTANCES.get(id(session))
        if inst is not None and not getattr(inst, "_deleted", False) and getattr(inst, "agent", None) is not None:
            return inst.agent
    except Exception:
        pass
    ag = getattr(session, "_pellaeon_cli_agent", None)
    if ag is None:
        from .bridge import ChimeraXExecutor
        from .core.agent import Agent, AgentConfig, Callbacks
        from .core.safety import AUTONOMY_AUTO
        # no provider: only _dispatch is used, never run_turn; nothing here needs a confirmation card
        ag = Agent(None, ChimeraXExecutor(session), config=AgentConfig(autonomy=AUTONOMY_AUTO), callbacks=Callbacks())
        session._pellaeon_cli_agent = ag
    return ag


def _run(session, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    from .core.schema import ToolCall, new_id
    ag = _agent(session)
    ag._contacts_runs = {}   # each command is its own request
    ag._turn_text = ""       # the user chose this tool, so no source guard
    ag._coloring_sources = []
    _, payload = ag._dispatch(ToolCall(new_id(), tool, args))
    if not isinstance(payload, dict):
        payload = {"result": payload}
    if payload.get("error"):
        raise UserError("pellaeon tool %s: %s" % (tool, payload["error"]))
    shown = {k: v for k, v in payload.items() if k not in ("commands", "png_b64")}
    text = payload.get("report") or payload.get("summary") or payload.get("text")
    if isinstance(text, str) and text.strip():
        session.logger.info("Pellaeon: " + text.strip())
    session.logger.info("pellaeon tool %s result:\n%s" % (tool, json.dumps(shown, indent=1, ensure_ascii=False)[:6000]))
    return payload


def _spec(a) -> str:
    return str(a).strip() if a is not None else ""


def tool_list(session):
    lines = ["pellaeon tool <name> ...  (Pellaeon's analysis tools without the chat; usage <name> for arguments)"]
    for name, _, what in TOOLS:
        lines.append("  %-9s %s" % (name, what))
    session.logger.info("\n".join(lines))


def tool_contacts(session, reference, other, chain=None, restrict=None, cutoff=4.0):
    return _run(session, "compare_contacts", {"reference": _spec(reference), "other": _spec(other),
                                               "chain": chain or None, "restrict": _spec(restrict) or None,
                                               "cutoff": float(cutoff)})


def tool_compare(session, reference, other, chain=None):
    return _run(session, "compare_structures", {"reference": _spec(reference), "other": _spec(other), "chain": chain or None})


def tool_annotate(session, model, accession, kind="variant", color="orange", label=True):
    return _run(session, "annotate", {"model": _spec(model), "accession": accession, "kind": kind, "color": color,
                                      "label": bool(label)})


def tool_fetch(session, source, protein, model=None, chain=None):
    source = source.lower()
    if source not in ("alphamissense", "conservation", "consurf"):
        raise UserError("source must be alphamissense or conservation")
    if source == "consurf":
        source = "conservation"
    return _run(session, "fetch_annotation", {"source": source, "protein": protein, "model": _spec(model) or "#1",
                                              "chain": chain or ""})


def tool_table(session, path, column=None, model=None, chain=None, palette=None, accession=None, label=False):
    from .core.tables import guess_columns, parse_table
    ag = _agent(session)
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise UserError("no such file: %s" % path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        t = parse_table(f.read(), path)
    if t.get("error"):
        raise UserError(t["error"])
    g = guess_columns(t["columns"], t["rows"])
    if g["position"] is None:
        raise UserError("No residue-position column found (a column of integers such as 'position' or 'resnum').")
    name = os.path.splitext(os.path.basename(path))[0]
    ag.tables[name] = {"id": name, "name": name, "path": path, "columns": t["columns"], "rows": t["rows"], "guess": g,
                       "delimiter": t["delimiter"]}
    return _run(session, "table_overlay", {"dataset": name, "column": column or "", "model": _spec(model) or "#1",
                                           "chain": chain or None, "palette": palette or "", "accession": accession or None,
                                           "label": bool(label)})


def tool_tidy(session, keep=None):
    return _run(session, "tidy_labels", {"keep": _spec(keep)})


def tool_explain(session, residue):
    return _run(session, "explain_residue", {"residue": _spec(residue)})


def tool_identity(session, models=None, chain=None):
    return _run(session, "sequence_identity", {"models": _spec(models), "chain": chain or None})


def tool_closewindows(session):
    return _run(session, "close_windows", {"which": "sequence"})


def tool_undo(session):
    """Typing this command is itself the user's OK, so it calls the executor directly instead of
    going through the agent's tool dispatch (which asks the panel to confirm)."""
    ag = _agent(session)
    fn = getattr(ag.executor, "undo_last_request", None)
    if fn is None:
        raise UserError("pellaeon tool undo: not available in this edition.")
    payload = fn()
    if payload.get("error"):
        raise UserError("pellaeon tool undo: %s" % payload["error"])
    session.logger.info("Pellaeon: " + payload.get("summary", "Undid the last request."))
    session.logger.info("pellaeon tool undo result:\n%s" % json.dumps(payload, indent=1, ensure_ascii=False)[:6000])
    return payload


def tool_membrane(session, model, pdb=None, slabs=True):
    return _run(session, "membrane_view", {"model": _spec(model), "pdb": pdb or None, "slabs": bool(slabs)})


def tool_axis(session, model=None, axis="short"):
    return _run(session, "view_axis", {"model": _spec(model) if model else "", "axis": axis})


def tool_gpcr(session, protein, open=None):  # noqa: A002  (ChimeraX keyword name)
    states = [x.strip().lower() for x in (open or "").split(",") if x.strip()]
    return _run(session, "gpcr_states", {"protein": protein, "open_states": states})


def register_tools(base: str, logger):
    """Register `<base> tool <name>` commands; `base` is the bundle's command name ("pellaeon")."""
    register(base + " tool list", CmdDesc(synopsis="List Pellaeon's analysis tools"), tool_list, logger=logger)
    register(base + " tool contacts",
             CmdDesc(required=[("reference", StringArg), ("other", StringArg)],
                     keyword=[("chain", StringArg), ("restrict", StringArg), ("cutoff", FloatArg)],
                     synopsis="Contacts lost and gained between two conformations, matched through the alignment"),
             tool_contacts, logger=logger)
    register(base + " tool compare",
             CmdDesc(required=[("reference", StringArg), ("other", StringArg)], keyword=[("chain", StringArg)],
                     synopsis="Superpose two models and color by residue displacement"),
             tool_compare, logger=logger)
    register(base + " tool annotate",
             CmdDesc(required=[("model", StringArg), ("accession", StringArg)],
                     optional=[("kind", StringArg)],
                     keyword=[("color", StringArg), ("label", BoolArg)],
                     synopsis="Color by UniProt features (domain, transmembrane, binding, ...) or ClinVar variants"),
             tool_annotate, logger=logger)
    register(base + " tool fetch",
             CmdDesc(required=[("source", StringArg), ("protein", StringArg)],
                     keyword=[("model", StringArg), ("chain", StringArg)],
                     synopsis="Color by AlphaMissense (UniProt accession) or ConSurf conservation (PDB id)"),
             tool_fetch, logger=logger)
    register(base + " tool table",
             CmdDesc(required=[("path", OpenFileNameArg)],
                     keyword=[("column", StringArg), ("model", StringArg), ("chain", StringArg), ("palette", StringArg),
                              ("accession", StringArg), ("label", BoolArg)],
                     synopsis="Color a model by a column of a residue table"),
             tool_table, logger=logger)
    register(base + " tool tidy", CmdDesc(keyword=[("keep", StringArg)], synopsis="Rearrange overlapping labels"),
             tool_tidy, logger=logger)
    register(base + " tool identity", CmdDesc(optional=[("models", StringArg)], keyword=[("chain", StringArg)],
                                              synopsis="Pairwise sequence identity between chains, no windows"),
             tool_identity, logger=logger)
    register(base + " tool closewindows", CmdDesc(synopsis="Close sequence viewers and their alignments"),
             tool_closewindows, logger=logger)
    register(base + " tool undo", CmdDesc(synopsis="Undo the last request: close the model(s) it opened, or restore the whole session"),
             tool_undo, logger=logger)
    register(base + " tool membrane", CmdDesc(required=[("model", StringArg)], keyword=[("pdb", StringArg), ("slabs", BoolArg)],
                                              synopsis="Orient a membrane protein from OPM, extracellular up"),
             tool_membrane, logger=logger)
    register(base + " tool axis", CmdDesc(optional=[("model", StringArg)], keyword=[("axis", StringArg)],
                                          synopsis="Look down a principal axis: short (symmetry axis), long (rod end-on), side"),
             tool_axis, logger=logger)
    register(base + " tool gpcr", CmdDesc(required=[("protein", StringArg)], keyword=[("open", StringArg)],
                                          synopsis="GPCRdb structures and inactive/active models of a receptor"),
             tool_gpcr, logger=logger)
    register(base + " tool explain", CmdDesc(required=[("residue", StringArg)], synopsis="Why a residue has its color"),
             tool_explain, logger=logger)
