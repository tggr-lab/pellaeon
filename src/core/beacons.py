"""Which structures exist for a protein: the 3D-Beacons summary for a UniProt accession (PDBe entries with
method, resolution, coverage, bound ligands and partner proteins; AlphaFold DB and SWISS-MODEL models).
One free EBI call, no key. Stdlib only; cached on disk like the UniProt client.

    https://www.ebi.ac.uk/pdbe/pdbe-kb/3dbeacons/api/uniprot/summary/<ACCESSION>.json
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from .http import request_json

BEACONS_URL = "https://www.ebi.ac.uk/pdbe/pdbe-kb/3dbeacons/api/uniprot/summary/%s.json"
# solvent, buffer and common ions are not what a user means by "bound to"
_BORING_LIGANDS = {"HOH", "DOD", "SO4", "PO4", "CL", "NA", "K", "MG", "CA", "ZN", "GOL", "EDO", "PEG", "PGE", "PG4",
                   "ACT", "ACE", "NH2", "DMS", "MPD", "TRS", "EPE", "MES", "BME", "IMD", "FMT", "NO3", "NI", "CD",
                   "OLC", "OLA", "OLB", "PLM", "CLR", "LDA", "BNG", "LMT", "UNX", "UNL", "1PE", "P6G", "MLI", "CIT",
                   "BU1", "12P", "ACM", "PE4", "PE5", "PE8", "2PE", "BOG", "HTG", "DDM", "LMU", "Y01", "OCT", "MYR",
                   "NAG", "NDG", "BMA", "MAN", "FUC", "GAL", "GLC", "NONE", ""}
_METHOD_WORDS = {"xray": "X-RAY DIFFRACTION", "x-ray": "X-RAY DIFFRACTION", "crystal": "X-RAY DIFFRACTION",
                 "em": "ELECTRON MICROSCOPY", "cryo-em": "ELECTRON MICROSCOPY", "cryoem": "ELECTRON MICROSCOPY",
                 "nmr": "SOLUTION NMR"}


class BeaconsClient:
    def __init__(self, cache_dir: Optional[str] = None, timeout: float = 25, max_age_days: int = 7):
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.max_age = max_age_days * 86400
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def _cache_path(self, key: str) -> Optional[str]:
        if not self.cache_dir:
            return None
        return os.path.join(self.cache_dir, re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:120] + ".json")

    def fetch(self, accession: str) -> Dict[str, Any]:
        acc = (accession or "").strip().upper()
        if not re.match(r"^[A-Z][A-Z0-9]{5,9}(-\d+)?$", acc):
            return {"error": "not a UniProt accession: %r" % accession}
        p = self._cache_path("beacons_" + acc)
        if p and os.path.exists(p) and time.time() - os.path.getmtime(p) < self.max_age:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:  # noqa: BLE001
                pass
        try:
            data = request_json("GET", BEACONS_URL % acc, headers={"Accept": "application/json"}, timeout=self.timeout)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "404" in msg:
                return {"error": "3D-Beacons has no entry for %s" % acc}
            return {"error": "3D-Beacons lookup failed: %s" % msg[:200]}
        if p:
            try:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(data, f)
            except Exception:  # noqa: BLE001
                pass
        return data

    def structures(self, accession: str, method: str = "", ligand: str = "", limit: int = 10) -> Dict[str, Any]:
        data = self.fetch(accession)
        if "error" in data:
            return data
        return summarize(data, method=method, ligand=ligand, limit=limit)


def _method_filter(method: str) -> Optional[str]:
    m = (method or "").strip().lower()
    if not m:
        return None
    for k, v in _METHOD_WORDS.items():
        if k in m:
            return v
    return m.upper()


def summarize(data: Dict[str, Any], method: str = "", ligand: str = "", limit: int = 10) -> Dict[str, Any]:
    """Aggregate the per-segment records into one entry per PDB id, plus the predicted models."""
    entry = data.get("uniprot_entry") or {}
    acc = entry.get("ac", "")
    length = entry.get("sequence_length")
    per_pdb: Dict[str, Dict[str, Any]] = {}
    predicted: List[Dict[str, Any]] = []
    for s in data.get("structures") or []:
        summ = s.get("summary") or s
        cat = summ.get("model_category", "")
        if cat == "EXPERIMENTALLY DETERMINED" and summ.get("provider") == "PDBe":
            pid = str(summ.get("model_identifier", "")).lower()
            if not pid:
                continue
            e = per_pdb.setdefault(pid, {"id": pid, "method": summ.get("experimental_method"), "resolution": summ.get("resolution"),
                                         "coverage": 0.0, "from": None, "to": None, "chains": set(), "ligands": {}, "partners": {},
                                         "released": summ.get("created"), "assembly": summ.get("oligomeric_state")})
            e["coverage"] = max(e["coverage"], float(summ.get("coverage") or 0))
            st, en = summ.get("uniprot_start"), summ.get("uniprot_end")
            if st is not None:
                e["from"] = st if e["from"] is None else min(e["from"], st)
            if en is not None:
                e["to"] = en if e["to"] is None else max(e["to"], en)
            for ent in summ.get("entities") or []:
                ident = str(ent.get("identifier", ""))
                desc = str(ent.get("description", "")).strip()
                if ent.get("entity_type") == "POLYMER":
                    if ident.upper() == acc.upper():
                        e["chains"].update(ent.get("chain_ids") or [])
                    elif ident and ent.get("identifier_category") == "UNIPROT":
                        e["partners"][ident] = desc[:60]
                elif ent.get("entity_type") in ("NON-POLYMER", "BRANCHED") and ident and ident.upper() not in _BORING_LIGANDS:
                    e["ligands"][ident.upper()] = desc[:50].title() if desc.isupper() else desc[:50]
        elif cat in ("TEMPLATE-BASED", "AB-INITIO", "DEEP-LEARNING") or summ.get("provider") in ("AlphaFold DB", "SWISS-MODEL"):
            predicted.append({"provider": summ.get("provider"), "id": summ.get("model_identifier"),
                              "coverage": summ.get("coverage"), "confidence": summ.get("confidence_avg_local_score"),
                              "url": summ.get("model_page_url") or summ.get("model_url")})
    entries = list(per_pdb.values())
    want_method = _method_filter(method)
    if want_method:
        entries = [e for e in entries if (e.get("method") or "").upper().startswith(want_method[:5])]
    ligand_note = ""
    if ligand:
        lig = ligand.strip().lower()
        hit = [e for e in entries if any(lig in k.lower() or lig in v.lower() for k, v in e["ligands"].items())]
        if hit:
            entries = hit
        else:
            ligand_note = ("No entry lists a ligand matching %r by CCD id or chemical name (drug names often differ from the "
                           "chemical name in the PDB); showing all entries with their ligands instead. " % ligand)
    total = len(entries)
    # coverage in bands of 0.2 so a slightly longer construct does not outrank a much better resolution
    entries.sort(key=lambda e: (-int(e["coverage"] * 5), e["resolution"] if e["resolution"] is not None else 99.0, e["id"]))
    out = []
    for e in entries[:max(1, limit)]:
        out.append({"id": e["id"], "method": _short_method(e["method"]), "resolution": e["resolution"],
                    "residues": ("%s-%s" % (e["from"], e["to"])) if e["from"] is not None else None,
                    "coverage": round(e["coverage"], 2), "chains": sorted(e["chains"]),
                    "ligands": dict(list(e["ligands"].items())[:6]), "partners": dict(list(e["partners"].items())[:4]),
                    "released": (e.get("released") or "")[:4], "assembly": e.get("assembly")})
    af = [p for p in predicted if p.get("provider") == "AlphaFold DB"]
    sm = [p for p in predicted if p.get("provider") == "SWISS-MODEL"]
    return {"accession": acc, "name": entry.get("id"), "length": length, "experimental_total": total,
            "experimental": out,
            "alphafold": ({"id": af[0]["id"], "confidence": af[0].get("confidence"), "open": "open alphafold:%s" % acc} if af else None),
            "swiss_model_count": len(sm),
            "note": ligand_note + ("%d of %d experimental entries shown (say the total when asked how many). Open an entry with "
                                   "`open <id>`; the AlphaFold model with `open alphafold:%s`. Entries are sorted by coverage of "
                                   "this protein, then resolution; ligands are the non-solvent hetero groups (CCD id: chemical name)."
                                   % (len(out), total, acc))}


def _short_method(m: Optional[str]) -> Optional[str]:
    if not m:
        return None
    m = m.upper()
    return {"X-RAY DIFFRACTION": "X-ray", "ELECTRON MICROSCOPY": "cryo-EM", "SOLUTION NMR": "NMR"}.get(m, m.title())


def mentions_structures(text: str) -> bool:
    """The requests this tool is for: which structures exist, the best one, the one bound to a ligand."""
    return bool(_STRUCT_RE.search(text or ""))


_STRUCT_RE = re.compile(
    r"(which|what|any|are there|is there|list|how many|find|show me)\b.{0,40}\b(structures?|pdb (entries|ids?|structures?)|"
    r"crystal structures?|cryo-?em structures?|experimental (structures?|models?))|"
    r"\b(best|highest|good|high)[- ]resolution\b|\bstructures? (of|for) .{0,40}\b(bound|in complex|with a? ?ligand|available|exist)|"
    r"\b(bound to|in complex with)\b.{0,40}\b(structure|pdb)|\bpdb (entry|entries|structures?) (of|for|with)\b", re.I)
