"""ClinVar missense variants for a gene via NCBI E-utilities (stdlib only, disk-cached).

Ported from the user's earlier ClinVARing script: esearch for "<gene>[gene] AND missense[consequence]",
esummary in batches, parse the p.HGVS protein change, keep the most severe classification per position.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from .http import request_json

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
SEVERITY = ["Pathogenic", "Pathogenic/Likely pathogenic", "Likely pathogenic", "Conflicting classifications of pathogenicity",
            "Uncertain significance", "Likely benign", "Benign/Likely benign", "Benign"]
COLORS = {"Pathogenic": "red", "Pathogenic/Likely pathogenic": "orangered", "Likely pathogenic": "orange",
          "Conflicting classifications of pathogenicity": "magenta", "Uncertain significance": "yellow",
          "Likely benign": "cyan", "Benign/Likely benign": "lightblue", "Benign": "blue"}
_AA3 = {"Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C", "Gln": "Q", "Glu": "E", "Gly": "G", "His": "H",
        "Ile": "I", "Leu": "L", "Lys": "K", "Met": "M", "Phe": "F", "Pro": "P", "Ser": "S", "Thr": "T", "Trp": "W",
        "Tyr": "Y", "Val": "V", "Ter": "*"}
_HGVS_RE = re.compile(r"p\.\(?([A-Z][a-z]{2})(\d+)([A-Z][a-z]{2}|=|\*)\)?")


def parse_hgvs_p(s: str):
    m = _HGVS_RE.search(s or "")
    if not m:
        return None
    return int(m.group(2)), _AA3.get(m.group(1), m.group(1)), _AA3.get(m.group(3), m.group(3))


def _rank(sig: str) -> int:
    best = None
    for i, s in enumerate(SEVERITY):  # longest matching prefix wins ("Pathogenic/Likely pathogenic" != "Pathogenic")
        if sig.lower().startswith(s.lower()) and (best is None or len(s) > len(SEVERITY[best])):
            best = i
    return len(SEVERITY) if best is None else best


class ClinVarClient:
    def __init__(self, cache_dir: Optional[str] = None, timeout: float = 30, max_age_days: int = 30):
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.max_age = max_age_days * 86400
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def _cache(self, gene: str) -> Optional[str]:
        return os.path.join(self.cache_dir, "clinvar_%s.json" % re.sub(r"[^A-Za-z0-9_-]", "_", gene)) if self.cache_dir else None

    def missense_variants(self, gene: str, max_results: int = 2000) -> Dict[str, Any]:
        gene = (gene or "").strip().upper()
        if not gene:
            return {"error": "no gene symbol"}
        p = self._cache(gene)
        if p and os.path.exists(p) and time.time() - os.path.getmtime(p) < self.max_age:
            try:
                return json.load(open(p, encoding="utf-8"))
            except Exception:
                pass
        try:
            q = urllib.parse.urlencode({"db": "clinvar", "term": "%s[gene] AND missense[consequence]" % gene,
                                        "retmode": "json", "retmax": max_results})
            data = request_json("GET", "%s/esearch.fcgi?%s" % (_EUTILS, q), timeout=self.timeout)
            ids = (data.get("esearchresult") or {}).get("idlist") or []
            per_pos: Dict[int, Dict[str, Any]] = {}
            for i in range(0, len(ids), 200):
                batch = ids[i:i + 200]
                q = urllib.parse.urlencode({"db": "clinvar", "id": ",".join(batch), "retmode": "json"})
                res = request_json("GET", "%s/esummary.fcgi?%s" % (_EUTILS, q), timeout=self.timeout)
                results = res.get("result") or {}
                for uid in results.get("uids") or []:
                    v = results.get(uid) or {}
                    sig = ((v.get("germline_classification") or v.get("clinical_significance") or {}).get("description")
                           or "Unknown")
                    pc = v.get("protein_change") or ""
                    hg = None
                    for cand in (pc if isinstance(pc, list) else [pc]) + [v.get("title", "")]:
                        parsed = parse_hgvs_p(str(cand))
                        if parsed:
                            hg = parsed
                            break
                    if not hg:
                        continue
                    pos, ref, alt = hg
                    if alt in ("*", "="):
                        continue
                    entry = {"position": pos, "ref": ref, "alt": alt, "significance": sig, "id": uid}
                    cur = per_pos.get(pos)
                    if cur is None or _rank(sig) < _rank(cur["significance"]):
                        per_pos[pos] = entry
                time.sleep(0.34)  # NCBI: max 3 requests/second without an API key
            variants = [per_pos[k] for k in sorted(per_pos)]
            out = {"gene": gene, "count": len(variants), "searched": len(ids), "variants": variants,
                   "colors": COLORS, "source": "ClinVar (NCBI E-utilities)"}
        except Exception as e:  # noqa: BLE001
            return {"error": "ClinVar request failed: %s" % e}
        if p:
            try:
                json.dump(out, open(p, "w", encoding="utf-8"))
            except Exception:
                pass
        return out


def color_for(sig: str) -> str:
    best = ""
    for k in COLORS:
        if sig.lower().startswith(k.lower()) and len(k) > len(best):
            best = k
    return COLORS.get(best, "gray")
