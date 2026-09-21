"""Bring-your-own-data tables: parse a CSV/TSV of per-residue values, guess its columns, plan an overlay
(colors by value or by category) onto a structure, and report the mapping honestly (mapped, missing, mismatches).
ChimeraX-free; the executor applies the plan."""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional, Tuple

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
        else:
            kinds.append("text")
    names = [_norm(c) for c in columns]
    pick = lambda cands, allowed: next((i for i, nm in enumerate(names) if nm in cands and kinds[i] in allowed), None)
    pos = pick(_POS_NAMES, ("int",))
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
    """Normalized rows: position (int), value (float or str), chain (str or None), ref (1-letter or None)."""
    out = []
    pos_i, ref_i = guess["position"], guess.get("reference")
    for r in table["rows"]:
        try:
            pos = int(float(r[pos_i]))
        except (ValueError, TypeError, IndexError):
            continue
        raw = r[value_col] if value_col < len(r) else ""
        if raw == "":
            continue
        val: Any = float(raw) if _is_number(raw) else raw
        ref = None
        if ref_i is not None and ref_i < len(r) and r[ref_i]:
            v = r[ref_i].upper()
            ref = AA1.get(v, v if len(v) == 1 else None)
        chain = r[chain_col].strip() if chain_col is not None and chain_col < len(r) and r[chain_col] else None
        out.append({"position": pos, "value": val, "chain": chain, "ref": ref})
    return out


def plan_overlay(rows: List[Dict[str, Any]], residues: Dict[Tuple[str, int], str], model: str, attr: str,
                 chains: Optional[List[str]] = None, palette: str = "blue-white-red",
                 numbering_map: Optional[Dict[int, Dict[str, Any]]] = None, edition: str = "chimerax") -> Dict[str, Any]:
    """rows -> per-residue assignments and the commands that color them.
    residues: {(chain_id, number): resname} of the target model (what exists).
    numbering_map: optional {table_position: {"chain","number","resname"}} from a UniProt mapping; when absent the
    table positions are structure residue numbers applied to `chains` (or the row's own chain column, or every chain)."""
    all_chains = sorted({c for c, _ in residues})
    assign: Dict[Tuple[str, int], Any] = {}
    missing: List[int] = []
    mismatches: List[str] = []
    for row in rows:
        targets: List[Tuple[str, int]] = []
        if numbering_map is not None:
            m = numbering_map.get(row["position"])
            if m and (m["chain"], int(m["number"])) in residues:
                targets = [(m["chain"], int(m["number"]))]
        else:
            cs = [row["chain"]] if row.get("chain") else (chains or all_chains)
            targets = [(c, row["position"]) for c in cs if (c, row["position"]) in residues]
        if not targets:
            missing.append(row["position"])
            continue
        for key in targets:
            resname = residues.get(key, "")
            if row.get("ref") and AA1.get(resname.upper()) and AA1[resname.upper()] != row["ref"]:
                mismatches.append("%s:%d is %s, table says %s" % (key[0], key[1], AA1[resname.upper()], row["ref"]))
                continue
            assign[key] = row["value"]
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
        if edition == "chimerax":
            cmds.append("color byattribute r:%s %s palette %s range %g,%g target ac novalue gray" % (attr, model, pal, lo, hi))
            stops = pal.split(":")
            if len(stops) >= 2 and hi > lo:
                labels = ["%g" % (lo + (hi - lo) * i / (len(stops) - 1)) for i in range(len(stops))]
                cmds.append("key %s pos 0.30,0.075 size 0.40,0.04 fontSize 20" % " ".join("%s:%s" % (c, l) for c, l in zip(stops, labels)))
                cmds.append('2dlabels create pellaeon_title text "%s (gray = no value)" xpos 0.30 ypos 0.165 size 20 color black' % _title_words(attr))
                cmds.append('view %s' % model)   # fit what was colored, then leave the legend its own band
                cmds.append('zoom 0.85')
        legend = "%s from %g (low) to %g (high), palette %s; residues without a value gray" % (attr, lo, hi, palette)
    else:
        cats = sorted({str(v) for v in assign.values()})
        colors = {c: CATEGORY_COLORS[i % len(CATEGORY_COLORS)] for i, c in enumerate(cats)}
        cmds.append("color %s lightgray target ac" % model)
        for c in cats:
            by_chain: Dict[str, List[int]] = {}
            for (cid, num), v in assign.items():
                if str(v) == c:
                    by_chain.setdefault(cid, []).append(num)
            spec = " ".join("%s/%s:%s" % (model, cid, ",".join(str(n) for n in sorted(nums))) for cid, nums in sorted(by_chain.items()))
            cmds.append("color %s %s target ac" % (spec, colors[c]))
        legend = "; ".join("%s = %s" % (c, colors[c]) for c in cats)
    return {"assignments": {"%s:%d" % k: v for k, v in assign.items()}, "numeric": numeric, "commands": cmds, "legend": legend,
            "mapped": len(assign), "missing": missing[:200], "n_missing": len(missing),
            "mismatches": mismatches[:50], "n_mismatches": len(mismatches),
            "chains": sorted({k[0] for k in assign}), "attr": attr}


def attr_name(dataset: str, column: str) -> str:
    return "pellaeon_" + re.sub(r"[^a-z0-9]+", "_", ("%s_%s" % (dataset, column)).lower()).strip("_")[:40]
