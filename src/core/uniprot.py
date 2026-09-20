"""UniProt lookups: gene/protein name -> accession, and sequence features.

Stdlib only; results are cached on disk (JSON) so repeated requests are free
and work offline afterwards.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from .http import request_json

_BASE = "https://rest.uniprot.org/uniprotkb"
_TAXON = {
    "human": 9606, "homo sapiens": 9606, "mouse": 10090, "mus musculus": 10090,
    "rat": 10116, "zebrafish": 7955, "fly": 7227, "drosophila": 7227, "yeast": 559292,
    "e. coli": 83333, "ecoli": 83333, "escherichia coli": 83333, "arabidopsis": 3702,
    "c. elegans": 6239, "worm": 6239, "chicken": 9031, "cow": 9913, "pig": 9823, "dog": 9615,
}

FEATURE_KINDS_DEFAULT = ["Domain", "Transmembrane", "Topological domain", "Binding site",
                         "Active site", "Site", "Disulfide bond", "Glycosylation", "Signal",
                         "Region", "Motif", "Helix", "Beta strand", "Turn", "Natural variant"]


class UniProtClient:
    def __init__(self, cache_dir: Optional[str] = None, timeout: float = 20, max_age_days: int = 30):
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.max_age = max_age_days * 86400
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    # ---- cache ----
    def _cache_path(self, key: str) -> Optional[str]:
        if not self.cache_dir:
            return None
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:120]
        return os.path.join(self.cache_dir, safe + ".json")

    def _cached(self, key: str) -> Optional[Any]:
        p = self._cache_path(key)
        if p and os.path.exists(p) and time.time() - os.path.getmtime(p) < self.max_age:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def _store(self, key: str, value: Any) -> None:
        p = self._cache_path(key)
        if p:
            try:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(value, f)
            except Exception:
                pass

    # ---- lookups ----
    @staticmethod
    def taxon_id(organism: Optional[str]) -> Optional[int]:
        if not organism:
            return 9606
        o = organism.strip().lower()
        if o.isdigit():
            return int(o)
        return _TAXON.get(o)

    def resolve(self, query: str, organism: str = "human") -> Dict[str, Any]:
        query = query.strip()
        if not query:
            return {"error": "empty query"}
        key = "resolve_%s_%s" % (query.lower(), (organism or "").lower())
        hit = self._cached(key)
        if hit is not None:
            return hit
        taxon = self.taxon_id(organism)
        org_clause = " AND (organism_id:%d)" % taxon if taxon else ""
        if re.match(r"^[OPQ][0-9][A-Z0-9]{3}[0-9]$|^[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}$", query.upper()):
            queries = ["(accession:%s)" % query.upper()]
        else:
            q = query.replace('"', "")
            queries = [
                "(gene_exact:%s)%s AND (reviewed:true)" % (q, org_clause),
                "(gene:%s)%s AND (reviewed:true)" % (q, org_clause),
                "(protein_name:\"%s\")%s AND (reviewed:true)" % (q, org_clause),
                "%s%s AND (reviewed:true)" % (q, org_clause),
                "%s%s" % (q, org_clause),
            ]
        fields = "accession,gene_primary,gene_names,protein_name,organism_name,length,reviewed"
        last_err = None
        for uq in queries:
            url = "%s/search?%s" % (_BASE, urllib.parse.urlencode(
                {"query": uq, "fields": fields, "format": "json", "size": 5}))
            try:
                data = request_json("GET", url, timeout=self.timeout)
            except Exception as e:  # network error: report and stop
                last_err = str(e)
                break
            results = data.get("results") or []
            if results:
                out = {"query": query, "organism": organism, "candidates": []}
                for r in results:
                    genes = r.get("genes") or []
                    primary = ""
                    synonyms = []
                    if genes:
                        primary = (genes[0].get("geneName") or {}).get("value", "")
                        synonyms = [s.get("value", "") for s in genes[0].get("synonyms", [])]
                    pname = ((r.get("proteinDescription") or {}).get("recommendedName") or {}).get("fullName", {}).get("value", "")
                    if not pname:
                        subs = (r.get("proteinDescription") or {}).get("submissionNames") or []
                        if subs:
                            pname = subs[0].get("fullName", {}).get("value", "")
                    out["candidates"].append({
                        "accession": r.get("primaryAccession"),
                        "gene": primary,
                        "synonyms": synonyms[:6],
                        "protein_name": pname,
                        "organism": (r.get("organism") or {}).get("scientificName", ""),
                        "length": (r.get("sequence") or {}).get("length"),
                        "reviewed": r.get("entryType", "").startswith("UniProtKB reviewed"),
                    })
                best = out["candidates"][0]
                out.update({"accession": best["accession"], "gene": best["gene"],
                            "protein_name": best["protein_name"], "length": best["length"],
                            "open_command": "open alphafold:%s" % best["accession"]})
                # "the PAR receptor" matches F2R, F2RL1, F2RL2 and F2RL3 equally well, and picking the
                # first one silently opens a different protein from the one the user meant. Say so.
                q = query.strip().lower()
                named = any(q == (c.get("gene") or "").lower() or q in [s.lower() for s in (c.get("synonyms") or [])]
                            or q == (c.get("accession") or "").lower()
                            for c in out["candidates"])
                rival = [c for c in out["candidates"]
                         if c.get("reviewed") and (c.get("gene") or "").upper() != (best.get("gene") or "").upper()]
                if not named and rival:
                    out["ambiguous"] = [{"accession": c["accession"], "gene": c["gene"],
                                         "protein_name": c["protein_name"]}
                                        for c in ([best] + rival)[:5]]
                    out["note"] = ("'%s' matches several human proteins. If the request does not name one of "
                                   "these, ask the user which one with ask_user before opening anything." % query)
                self._store(key, out)
                return out
        if last_err:
            return {"error": "UniProt request failed: %s" % last_err}
        return {"error": "No UniProt entry found for '%s' (%s). Try another name or organism." % (query, organism)}

    def features(self, accession: str, kinds: Optional[List[str]] = None) -> Dict[str, Any]:
        acc = accession.strip().upper()
        key = "features_%s" % acc
        entry = self._cached(key)
        if entry is None:
            url = "%s/%s.json" % (_BASE, acc)
            try:
                entry = request_json("GET", url, timeout=self.timeout)
            except Exception as e:
                return {"error": "UniProt request failed: %s" % e}
            self._store(key, entry)
        wanted = set(k.lower() for k in (kinds or FEATURE_KINDS_DEFAULT))
        feats = []
        for f in entry.get("features", []):
            ftype = f.get("type", "")
            if ftype.lower() not in wanted:
                continue
            loc = f.get("location", {})
            start = (loc.get("start") or {}).get("value")
            end = (loc.get("end") or {}).get("value")
            if start is None or end is None:
                continue
            if ftype == "Disulfide bond" and start != end:
                spec = ":%d,%d" % (start, end)          # the two cysteines, not the residues between them
            else:
                spec = ":%d-%d" % (start, end) if start != end else ":%d" % start
            feats.append({
                "type": ftype,
                "start": start,
                "end": end,
                "description": f.get("description", ""),
                "spec": spec,
            })
        seq = entry.get("sequence", {})
        return {
            "accession": acc,
            "gene": ((entry.get("genes") or [{}])[0].get("geneName") or {}).get("value", ""),
            "length": seq.get("length"),
            "features": feats,
            "count": len(feats),
        }
