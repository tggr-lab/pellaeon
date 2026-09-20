"""Per-residue annotation sources: AlphaMissense pathogenicity and ConSurf-DB conservation.

Both fetchers return a dataset in exactly the shape `tables.parse_table` produces
({"columns", "rows", "delimiter"}), so `guess_columns` / `table_rows` / `plan_overlay` place and
colour them with no change at all -- a fetched annotation is just a table the user did not have to
download by hand. Stdlib only, disk cache and polite pacing as in `clinvar.py`.

Numbering matters and the two sources differ, so every dataset says which it uses:
  * AlphaMissense is per UniProt position, and the dataset carries `accession`, which is the key
    `agent._table_overlay` reads to route positions through `analysis.map_positions`.
  * ConSurf-DB grades are keyed by the *structure's* author residue numbers, so the dataset carries
    no accession and the positions are applied to the model directly.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .http import _build_request, _open, request_json

_AFDB = "https://alphafold.ebi.ac.uk/api/prediction/%s"
_CONSURF = "https://consurfdb.tau.ac.il"

# DeepMind's published AlphaMissense cut-offs (Cheng et al., Science 2023): <0.34 likely benign,
# >0.564 likely pathogenic, ambiguous in between. Used here to classify the per-position mean.
AM_BENIGN_MAX = 0.34
AM_PATHOGENIC_MIN = 0.564
AM_CLASSES = ("likely benign", "ambiguous", "likely pathogenic")

# ConSurf's own 1-9 scale, in its published colours (9 = most conserved, maroon; 1 = variable, cyan).
CONSURF_COLORS = {1: "#10C8D1", 2: "#8CFFFF", 3: "#D7FFFF", 4: "#EAFFFF", 5: "#FFFFFF",
                  6: "#FCEDF4", 7: "#FAC9DE", 8: "#F07DAB", 9: "#A02560"}
_AA3_TO_1 = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E", "GLY": "G",
             "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P", "SER": "S",
             "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V"}
_VARIANT_RE = re.compile(r"^([A-Z])(\d+)([A-Z])$")
# POS SEQ ATOM SCORE COLOR ... ; ATOM is "MET:1:A" (or "MET:1:A " padded), COLOR may carry a trailing *
_GRADE_RE = re.compile(r"^\s*(\d+)\s+(\w)\s+([A-Z]{3}):(-?\d+[A-Za-z]?):(\w)\s+(-?\d+\.\d+)\s+(\d)\s*\*?")


def am_class(score: float) -> str:
    """AlphaMissense class of a pathogenicity score, by the published cut-offs."""
    if score < AM_BENIGN_MAX:
        return AM_CLASSES[0]
    if score > AM_PATHOGENIC_MIN:
        return AM_CLASSES[2]
    return AM_CLASSES[1]


def _cache_path(cache_dir: Optional[str], key: str) -> Optional[str]:
    if not cache_dir:
        return None
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "%s.json" % re.sub(r"[^A-Za-z0-9_-]", "_", key))


def _cached(path: Optional[str], max_age: float) -> Optional[Dict[str, Any]]:
    if path and os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt cache file must never break the fetch
            return None
    return None


def _store(path: Optional[str], data: Dict[str, Any]) -> None:
    if not path:
        return
    try:
        json.dump(data, open(path, "w", encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass


def request_text(url: str, timeout: float = 60) -> str:
    """GET a text body. The annotation sources serve CSV and plain text, which `request_json` cannot."""
    with _open(_build_request("GET", url, None, None), timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def alphamissense(accession: str, cache_dir: Optional[str] = None, timeout: float = 60,
                  max_age_days: int = 30) -> Dict[str, Any]:
    """Per-residue AlphaMissense summary for a human UniProt accession.

    AlphaFold DB publishes the full 19-substitution matrix as a CSV per entry; its URL is the
    `amAnnotationsUrl` field of the prediction record (there is no per-residue endpoint, so the mean,
    the maximum and the class of the mean are computed here).
    """
    acc = (accession or "").strip().upper()
    if not acc:
        return {"error": "No UniProt accession given for AlphaMissense."}
    path = _cache_path(cache_dir, "alphamissense_%s" % acc)
    hit = _cached(path, max_age_days * 86400)
    if hit:
        return hit
    try:
        records = request_json("GET", _AFDB % acc, timeout=timeout)
        if not isinstance(records, list) or not records:
            return {"error": "AlphaFold DB has no entry for %s, so there is no AlphaMissense data for it." % acc}
        rec = records[0]
        csv_url = rec.get("amAnnotationsUrl")
        if not csv_url:
            return {"error": "AlphaFold DB has no AlphaMissense annotations for %s (%s). AlphaMissense "
                             "covers the human proteome only." % (acc, rec.get("organismScientificName") or "unknown organism")}
        time.sleep(0.2)  # two requests back to back against one EBI host; stay polite
        text = request_text(csv_url, timeout=timeout)
        rows, n_variants = _aggregate_am(text, rec.get("uniprotSequence") or rec.get("sequence") or "")
    except Exception as e:  # noqa: BLE001
        return {"error": "AlphaMissense request failed: %s" % e}
    if not rows:
        return {"error": "The AlphaMissense file for %s had no usable substitutions." % acc}
    means = [float(r[2]) for r in rows]
    out = {"columns": ["position", "wt", "am_mean", "am_max", "am_class"], "rows": rows, "delimiter": "comma",
           "name": "alphamissense_%s" % acc, "accession": acc,
           "position_numbering": "uniprot", "position_column": "position",
           "source": "AlphaMissense (Cheng et al. 2023) via AlphaFold DB",
           "source_url": csv_url, "palette": "blue-white-red",
           "gene": rec.get("gene") or "", "protein": rec.get("uniprotDescription") or "",
           "n_positions": len(rows), "n_variants": n_variants,
           "value_range": [min(means), max(means)],
           "cutoffs": {"likely benign below": AM_BENIGN_MAX, "likely pathogenic above": AM_PATHOGENIC_MIN}}
    _store(path, out)
    return out


def _aggregate_am(text: str, sequence: str = "") -> Tuple[List[List[str]], int]:
    """CSV of protein_variant,am_pathogenicity,am_class -> one row per position: mean, max, class of mean.

    A few entries carry substitutions for two different wild-type residues at one position (sequence
    variants kept in the file); when the canonical sequence is known, only its wild type is summarised,
    otherwise the wild type with the most substitutions wins.
    """
    per_pos: Dict[int, Dict[str, List[float]]] = {}
    n = 0
    for row in csv.DictReader(io.StringIO(text)):
        m = _VARIANT_RE.match((row.get("protein_variant") or "").strip().upper())
        if not m:
            continue
        try:
            score = float(row.get("am_pathogenicity") or "")
        except ValueError:
            continue
        wt, pos = m.group(1), int(m.group(2))
        per_pos.setdefault(pos, {}).setdefault(wt, []).append(score)
        n += 1
    rows: List[List[str]] = []
    for pos in sorted(per_pos):
        by_wt = per_pos[pos]
        wt = ""
        if sequence and 1 <= pos <= len(sequence) and sequence[pos - 1] in by_wt:
            wt = sequence[pos - 1]
        else:
            wt = max(by_wt, key=lambda k: len(by_wt[k]))
        scores = by_wt[wt]
        mean = sum(scores) / len(scores)
        rows.append([str(pos), wt, "%.4f" % mean, "%.4f" % max(scores), am_class(mean)])
    return rows, n


def conservation(accession_or_pdb: str, chain: str = "", cache_dir: Optional[str] = None,
                 timeout: float = 60, max_age_days: int = 30) -> Dict[str, Any]:
    """Per-residue evolutionary conservation for a PDB chain, from ConSurf-DB's precomputed grades.

    ConSurf-DB is indexed by PDB entry and chain only: it has no UniProt or AlphaFold-model route
    (find_chains rejects an accession), so an AlphaFold model has to be given a PDB entry of the same
    protein instead. Grades run 1 (variable) to 9 (conserved); the normalised rate is also returned,
    negative meaning conserved.
    """
    pdb = (accession_or_pdb or "").strip().upper()
    chain = (chain or "").strip()
    if not re.fullmatch(r"[0-9][A-Za-z0-9]{3}", pdb):
        return {"error": "ConSurf-DB is indexed by PDB entry, not by UniProt accession. Give a 4-character "
                         "PDB ID (and a chain) for a structure of this protein; '%s' is not one." % (accession_or_pdb or "")}
    path = _cache_path(cache_dir, "consurf_%s_%s" % (pdb, chain or "auto"))
    hit = _cached(path, max_age_days * 86400)
    if hit:
        return hit
    try:
        chains = request_json("GET", "%s/find_chains/?pdb_ID=%s" % (_CONSURF, pdb), timeout=timeout)
        if not isinstance(chains, list) or not chains or chains[0] == "error":
            return {"error": "ConSurf-DB has no precomputed conservation for PDB %s." % pdb}
        if not chain:
            chain = str(chains[0])
        elif chain not in chains:
            return {"error": "ConSurf-DB has no chain %s for PDB %s; it has %s." % (chain, pdb, ", ".join(map(str, chains)))}
        time.sleep(0.34)  # three chained requests against one academic host
        ident = request_json("GET", "%s/get_identical_chain/?unique_pdb=%s&unique_chain=%s" % (_CONSURF, pdb, chain),
                             timeout=timeout)
        rep_pdb, rep_chain = ident.get("identical_pdb") or pdb, ident.get("identical_chain") or chain
        time.sleep(0.34)
        final = request_json("GET", "%s/get_final_data/?unique_pdb=%s&identical_pdb=%s&identical_chain=%s"
                             % (_CONSURF, pdb, rep_pdb, rep_chain), timeout=timeout)
        score_url = final.get("Score_File")
        if not score_url:
            return {"error": "ConSurf-DB returned no grades file for %s chain %s." % (pdb, chain)}
        time.sleep(0.34)
        rows = _parse_consurf_grades(request_text(score_url, timeout=timeout), chain)
    except Exception as e:  # noqa: BLE001
        return {"error": "ConSurf-DB request failed: %s" % e}
    if not rows:
        return {"error": "The ConSurf-DB grades file for %s chain %s had no per-residue rows." % (pdb, chain)}
    grades = [int(r[3]) for r in rows]
    note = ""
    if rep_pdb.upper() != pdb:
        # ConSurf computes once per sequence cluster; the grades carry the representative's residue
        # numbers, which usually but not always equal the queried entry's.
        note = ("Grades come from the identical chain %s/%s, so residue numbers are that entry's; they match %s "
                "only if the two entries number the sequence the same way." % (rep_pdb, rep_chain, pdb))
    out = {"columns": ["position", "chain", "wt", "consurf_grade", "consurf_score"], "rows": rows, "delimiter": "comma",
           "name": "consurf_%s_%s" % (pdb, chain), "position_numbering": "pdb", "position_column": "position",
           "source": "ConSurf-DB (Ben Chorin et al.) grades for %s chain %s" % (rep_pdb, rep_chain),
           "source_url": score_url, "palette": "consurf", "pdb": pdb, "chain": chain,
           "representative": "%s/%s" % (rep_pdb, rep_chain), "note": note,
           "n_positions": len(rows), "value_range": [min(grades), max(grades)],
           "colors": {str(k): v for k, v in CONSURF_COLORS.items()}}
    _store(path, out)
    return out


def _parse_consurf_grades(text: str, chain: str) -> List[List[str]]:
    """ConSurf grades table -> rows in the structure's own numbering.

    The ATOM column ("MET:1:A") holds the author residue number; the leading POS column is the
    alignment index and is not what the structure uses. Positions with no ATOM record are skipped:
    they cannot be coloured.
    """
    rows: List[List[str]] = []
    seen = set()
    for line in text.splitlines():
        m = _GRADE_RE.match(line)
        if not m:
            continue
        num = re.match(r"-?\d+", m.group(4))
        if not num:
            continue
        pos = int(num.group(0))
        if pos in seen:
            continue
        seen.add(pos)
        rows.append([str(pos), chain, _AA3_TO_1.get(m.group(3), ""), m.group(7), m.group(6)])
    return rows
