"""Bring-your-own-data tables: parse a CSV/TSV of per-residue values, guess its columns, plan an overlay
(colors by value or by category) onto a structure, and report the mapping honestly (mapped, missing, mismatches).
ChimeraX-free; the executor applies the plan."""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional, Tuple

from .resid import (ResId, STATUS_STRUCTURE, STATUS_UNRESOLVED, entry_targets, insertion_numbers, parse_number,
                    specs_by_chain, target_id)

AA1 = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
       "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V"}
AA3 = {v: k for k, v in AA1.items()}
_POS_NAMES = ("position", "pos", "resnum", "resi", "residue_number", "res_num", "resid", "site", "aa_pos", "residue", "index", "number", "seqpos")
_CHAIN_NAMES = ("chain", "chain_id", "chainid", "auth_asym_id", "asym")
_ACC_NAMES = ("accession", "uniprot", "uniprot_id", "acc", "entry")
_REF_NAMES = ("wt", "ref", "wild_type", "wildtype", "ref_aa", "aa", "amino_acid", "resname", "res_name", "residue_name", "wt_aa", "from")
def _title_words(attr: str) -> str:
    """'pellaeon_ubiquitin_hydropathy_hydropathy' -> 'ubiquitin hydropathy' (the column repeats the table name)."""
    out = []
    for w in attr.replace("pellaeon_", "", 1).split("_"):
        if w and (not out or w.lower() != out[-1].lower()):
            out.append(w)
    return " ".join(out)


PALETTES = {"blue-white-red": "blue:white:red", "white-red": "white:red", "blue-white": "white:blue", "viridis": "#440154:#31688e:#35b779:#fde725",
            "rainbow": "blue:cyan:green:yellow:red", "gray-orange-red": "#bdbdbd:gold:orange:#b2182b", "green-white-magenta": "green:white:magenta",
            # AlphaMissense benign -> pathogenic (ColorBrewer RdBu ends); ConSurf's published grade colours 1 -> 9
            "alphamissense": "#2166ac:#f7f7f7:#b2182b",
            "consurf": "#10C8D1:#8CFFFF:#D7FFFF:#EAFFFF:#FFFFFF:#FCEDF4:#FAC9DE:#F07DAB:#A02560"}
CATEGORY_COLORS = ["cornflowerblue", "orange", "mediumseagreen", "orchid", "gold", "tomato", "steelblue", "sienna", "hotpink", "slategray", "olive", "teal"]


def parse_table(text: str, filename: str = "") -> Dict[str, Any]:
    """CSV/TSV/whitespace table -> {'columns': [...], 'rows': [[...]], 'delimiter': str}. Comments (#) and blank lines skipped."""
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if not lines:
        return {"error": "The file is empty."}
    sample = "\n".join(lines[:20])
    if filename.lower().endswith(".tsv") or ("\t" in sample and sample.count("\t") >= sample.count(",")):
        delim = "\t"
    elif "," in sample:
        delim = ","
    elif ";" in sample:
        delim = ";"
    else:
        delim = None   # whitespace
    if delim:
        rows = [r for r in csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)]
    else:
        rows = [l.split() for l in lines]
    rows = [[c.strip() for c in r] for r in rows if any(c.strip() for c in r)]
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header = rows[0]
    has_header = not all(_is_number(c) for c in header if c) and any(re.search(r"[A-Za-z]", c) for c in header)
    if has_header:
        columns = [c or "col%d" % (i + 1) for i, c in enumerate(header)]
        data = rows[1:]
    else:
        columns = ["col%d" % (i + 1) for i in range(width)]
        data = rows
    if not data:
        return {"error": "The file has a header but no data rows."}
    return {"columns": columns, "rows": data, "delimiter": {"\t": "tab", ",": "comma", ";": "semicolon", None: "whitespace"}[delim]}


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def guess_columns(columns: List[str], rows: List[List[str]]) -> Dict[str, Any]:
    """Which column is the residue position, which are values, which (if any) are chain / accession / reference residue."""
    n = len(columns)
    kinds: List[str] = []
    for i in range(n):
        vals = [r[i] for r in rows if i < len(r) and r[i] != ""]
        if not vals:
            kinds.append("empty")
        elif all(_is_number(v) for v in vals):
            kinds.append("int" if all(re.fullmatch(r"[-+]?\d+", v) for v in vals) else "float")
        elif all(re.fullmatch(r"-?\d+[A-Za-z]?", v) for v in vals):
            kinds.append("resnum")   # residue numbers, some with an insertion code (82, 82A, 82B)
        else:
            kinds.append("text")
    names = [_norm(c) for c in columns]
    pick = lambda cands, allowed: next((i for i, nm in enumerate(names) if nm in cands and kinds[i] in allowed), None)
    pos = pick(_POS_NAMES, ("int", "resnum"))
    if pos is None:
        pos = next((i for i, k in enumerate(kinds) if k == "resnum"), None)
    if pos is None:
        pos = next((i for i, k in enumerate(kinds) if k == "int"), None)   # first integer column
    chain = pick(_CHAIN_NAMES, ("text", "int"))
    acc = pick(_ACC_NAMES, ("text",))
    ref = pick(_REF_NAMES, ("text",))
    if ref is not None:
        vals = {r[ref].upper() for r in rows if ref < len(r) and r[ref]}
        if not all(len(v) == 1 or v in AA1 for v in vals):
            ref = None
    values = [i for i, k in enumerate(kinds) if k in ("int", "float") and i != pos and i != chain]
    categories = [i for i, k in enumerate(kinds) if k == "text" and i not in (chain, acc, ref)
                  and 1 < len({r[i] for r in rows if i < len(r)}) <= 12]
    return {"kinds": kinds, "position": pos, "chain": chain, "accession": acc, "reference": ref,
            "values": values, "categories": categories,
            "default_value": (values[0] if values else (categories[0] if categories else None))}


def table_rows(table: Dict[str, Any], guess: Dict[str, Any], value_col: int, chain_col: Optional[int] = None) -> List[Dict[str, Any]]:
    """Normalized rows: position (int), icode (insertion code or ''), value (float or str), chain (str or None),
    ref (1-letter or None)."""
    out = []
    pos_i, ref_i = guess["position"], guess.get("reference")
    for r in table["rows"]:
        num = parse_number(r[pos_i]) if pos_i is not None and pos_i < len(r) else None
        if num is None:
            continue
        pos, icode = num
        raw = r[value_col] if value_col < len(r) else ""
        if raw == "":
            continue
        val: Any = float(raw) if _is_number(raw) else raw
        ref = None
        if ref_i is not None and ref_i < len(r) and r[ref_i]:
            v = r[ref_i].upper()
            ref = AA1.get(v, v if len(v) == 1 else None)
        chain = r[chain_col].strip() if chain_col is not None and chain_col < len(r) and r[chain_col] else None
        out.append({"position": pos, "icode": icode, "value": val, "chain": chain, "ref": ref})
    return out


# More reference residues differing than this, among the placed rows that name one, means the numbering
# does not correspond (an off-by-one or another chain): nothing is placed. Engineered mutations in a
# structure stay well below it; a shifted or wrong chain is far above it.
MISMATCH_LIMIT = 0.2
MISMATCH_MIN_CHECKED = 5


def _residue_index(residues: Dict[Any, str], model: str) -> Dict[ResId, str]:
    """{ResId: resname} from list_residues keys ('A:82A', '#1.2/A:10') or older (chain, number) tuples."""
    out: Dict[ResId, str] = {}
    for k, v in residues.items():
        rid = ResId.parse(k, model)
        if rid is not None:
            out[rid if rid.model else rid._replace(model=model)] = v
    return out


def _unresolved(model: str, reason: str, text: str, **extra: Any) -> Dict[str, Any]:
    return dict({"error": text, "mapping": STATUS_UNRESOLVED, "reason": reason, "model": model}, **extra)


def plan_overlay(rows: List[Dict[str, Any]], residues: Dict[Any, str], model: str, attr: str,
                 chains: Optional[List[str]] = None, palette: str = "blue-white-red",
                 numbering_map: Optional[Dict[int, Any]] = None, edition: str = "chimerax",
                 chain_groups: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """rows -> per-residue assignments and the commands that color them.

    residues: what exists, keyed 'A:82A' or '#1.2/A:10' (older (chain, number) tuples are read too).
    numbering_map: {table_position: [target, ...]} from a UniProt mapping, each target with chain, number, icode,
    status and basis (one dict per position in older mappings). Without it the positions are the structure's own
    residue numbers, placed on `chains` when given, else on the chain the row names, else on every chain when the
    chains are one molecule (chain_groups: chain -> molecule key).

    Nothing is placed, and the result says why (mapping 'unresolved'), when a chain asked for or named by the rows
    is not in the model, when rows without a chain meet chains of several molecules, or when the reference residues
    of the placed rows disagree with the structure too often for the numbering to correspond.
    """
    index = _residue_index(residues, model)
    models = sorted({r.model for r in index})
    all_chains = sorted({r.chain for r in index})
    by_number: Dict[Tuple[str, int, str], List[ResId]] = {}
    for r in index:
        by_number.setdefault((r.chain, r.number, r.icode), []).append(r)
    asked = [c for c in (chains or []) if c]
    if asked and not any(c in all_chains for c in asked):
        return _unresolved(model, "chain_missing", "Chain %s is not in %s (chains: %s). Nothing was placed." % (
            ", ".join(asked), model, ", ".join(all_chains)), missing_chains=asked, chains_available=all_chains)
    groups = chain_groups or {}
    if numbering_map is None and not asked and any(not row.get("chain") for row in rows):
        kinds = sorted({groups.get(c, c) for c in all_chains}) if groups else []
        if len(kinds) > 1:
            return _unresolved(model, "several_proteins",
                               "The table names no chain and %s has chains of %d different molecules (%s). "
                               "Nothing was placed." % (model, len(kinds), "; ".join(
                                   ",".join(c for c in all_chains if groups.get(c, c) == k) for k in kinds)),
                               chains_available=all_chains)
    assign: Dict[ResId, Any] = {}
    status: Dict[ResId, str] = {}
    missing: List[str] = []
    mismatches: List[str] = []
    absent_chains: List[str] = []
    rows_unresolved = 0
    checked = 0
    for row in rows:
        pos, icode = row["position"], (row.get("icode") or "")
        label: Any = pos if not icode else "%d%s" % (pos, icode)
        targets: List[Tuple[ResId, str]] = []
        if numbering_map is not None:
            entry = numbering_map.get(pos, numbering_map.get(str(pos)))
            for t in entry_targets(entry, model):
                rid = target_id(t, model)
                if t.get("status") == STATUS_UNRESOLVED or (asked and rid.chain not in asked):
                    continue
                if rid in index:
                    targets.append((rid, t.get("status") or "offset"))
        else:
            if asked:
                cs = [c for c in asked if c in all_chains]
            elif row.get("chain"):
                if row["chain"] not in all_chains:
                    rows_unresolved += 1
                    if row["chain"] not in absent_chains:
                        absent_chains.append(row["chain"])
                    continue
                cs = [row["chain"]]
            else:
                cs = all_chains
            for c in cs:
                for rid in by_number.get((c, pos, icode), []):
                    targets.append((rid, STATUS_STRUCTURE))
        if not targets:
            missing.append(label)
            continue
        for rid, st in targets:
            resname = index.get(rid, "")
            one = AA1.get(resname.upper())
            if row.get("ref") and one:
                checked += 1
                if one != row["ref"]:
                    mismatches.append("%s is %s, table says %s" % (rid.short(), one, row["ref"]))
                    continue
            assign[rid] = row["value"]
            status[rid] = st
    single = len(models) <= 1
    keyname = (lambda r: r.short()) if single else (lambda r: r.spec())
    if absent_chains and not assign:
        return _unresolved(model, "chain_missing", "The table's chain %s is not in %s (chains: %s). Nothing was placed." % (
            ", ".join(absent_chains), model, ", ".join(all_chains)), missing_chains=absent_chains, chains_available=all_chains)
    if checked >= MISMATCH_MIN_CHECKED and len(mismatches) > MISMATCH_LIMIT * checked:
        return _unresolved(model, "reference_mismatch",
                           "%d of %d reference residues in the table differ from %s, so the table's numbering does not "
                           "correspond to this structure. Nothing was placed." % (len(mismatches), checked, model),
                           mismatches=mismatches[:20], n_mismatches=len(mismatches), chains_available=all_chains)
    numeric = all(isinstance(v, float) for v in assign.values()) and bool(assign)
    cmds: List[str] = []
    legend = ""
    if not assign:
        return {"error": "No table row could be placed on %s (%d positions not found%s)." % (
            model, len(missing), ", %d reference-residue mismatches" % len(mismatches) if mismatches else ""),
                "missing": missing[:50], "mismatches": mismatches[:20]}
    if numeric:
        vals = list(assign.values())
        lo, hi = min(vals), max(vals)
        pal = PALETTES.get(palette, palette or "blue:white:red")
        if edition == "chimerax" and palette == "consurf":
            # ConSurf grades are nine bins with fixed colors, not a gradient: one color per grade, and a
            # key with distinct blocks labelled 1 (variable) to 9 (conserved), as ConSurf itself draws it
            stops = pal.split(":")
            cmds.append("color %s & protein gray target ac" % model)      # ligands, ions and waters keep their colors
            for grade, colour in enumerate(stops, start=1):
                cmds.append("color %s::%s=%d %s target ac" % (model, attr, grade, colour))
            # ConSurf's yellow marks positions with too few sequences; the bin appears only when the model has one
            starred = any(v == 0 for v in assign.values())
            if starred:
                cmds.append("color %s::%s=0 #FFFF96 target ac" % (model, attr))
            labels = {1: "variable", len(stops): "conserved"}
            bins = " ".join("%s:%s" % (c, labels.get(g, g)) for g, c in enumerate(stops, start=1))
            cmds.append("key %s%s colorTreatment distinct pos 0.30,0.075 size 0.40,0.04 fontSize 20"
                        % (bins, " #FFFF96:?" if starred else ""))
            cmds.append('2dlabels create pellaeon_title text "ConSurf conservation: variable (1) to conserved (9)%s; gray = no data" xpos 0.30 ypos 0.165 size 20 color black'
                        % ("; ? = too few sequences" if starred else ""))
            cmds.append('view %s' % model)
            cmds.append('zoom 0.85')
        elif edition == "chimerax":
            cmds.append("color byattribute r:%s %s palette %s range %g,%g target ac novalue gray" % (attr, model, pal, lo, hi))
            stops = pal.split(":")
            if len(stops) >= 2 and hi > lo:
                labels = ["%g" % (lo + (hi - lo) * i / (len(stops) - 1)) for i in range(len(stops))]
                cmds.append("key %s pos 0.30,0.075 size 0.40,0.04 fontSize 20" % " ".join("%s:%s" % (c, l) for c, l in zip(stops, labels)))
                cmds.append('2dlabels create pellaeon_title text "%s (gray = no value)" xpos 0.30 ypos 0.165 size 20 color black' % _title_words(attr))
                cmds.append('view %s' % model)   # fit what was colored, then leave the legend its own band
                cmds.append('zoom 0.85')
        if palette == "consurf":
            legend = ("ConSurf grades in ConSurf's own nine colors: 1 = variable (teal) through 5 = average (white) to 9 = conserved "
                      "(maroon); residues without data gray. Say 'teal to maroon', not 'blue to red'.")
        else:
            legend = "%s from %g (low) to %g (high), palette %s; residues without a value gray" % (attr, lo, hi, palette)
    else:
        cats = sorted({str(v) for v in assign.values()})
        colors = {c: CATEGORY_COLORS[i % len(CATEGORY_COLORS)] for i, c in enumerate(cats)}
        cmds.append("color %s lightgray target ac" % model)
        ins = insertion_numbers(index)
        for c in cats:
            spec = " ".join(specs_by_chain([r for r, v in assign.items() if str(v) == c], ins, model))
            cmds.append("color %s %s target ac" % (spec, colors[c]))
        legend = "; ".join("%s = %s" % (c, colors[c]) for c in cats)
    counts: Dict[str, int] = {}
    for st in status.values():
        counts[st] = counts.get(st, 0) + 1
    out = {"assignments": {keyname(k): v for k, v in assign.items()}, "numeric": numeric, "commands": cmds, "legend": legend,
           "mapped": len(assign), "missing": missing[:200], "n_missing": len(missing),
           "mismatches": mismatches[:50], "n_mismatches": len(mismatches),
           "chains": sorted({k.chain for k in assign}), "attr": attr, "status": counts}
    if absent_chains:
        out["missing_chains"] = absent_chains
        out["rows_unresolved"] = rows_unresolved
    if not single:
        out["models"] = sorted({k.model for k in assign})
    return out


def attr_name(dataset: str, column: str) -> str:
    return "pellaeon_" + re.sub(r"[^a-z0-9]+", "_", ("%s_%s" % (dataset, column)).lower()).strip("_")[:40]
