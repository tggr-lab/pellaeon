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

    _ORGANISM_WORDS = re.compile(r"^\s*(human|mouse|murine|rat|bovine|cow|yeast|chicken|zebrafish|fly|e\.? ?coli)\s+|"
                                 r"\s+(human|mouse|murine|rat|bovine|cow|yeast|chicken|zebrafish|fly)\s*$", re.I)
    _NOISE_WORDS = {"the", "a", "an", "protein", "of", "model", "structure", "alphafold", "af"}

    @staticmethod
    def _name_score(query: str, cand: Dict[str, Any]) -> Tuple[float, int]:
        """How well a candidate matches a name: its gene symbol or the whole recommended name (3); the name with
        one short qualifier ("Lysozyme C", "Ubiquitin D") or the poly-form ("Polyubiquitin-B") (2.5); the query
        as a word or phrase of a longer name (2); part of a compound word (1); nothing (0). Then shorter names
        first, so "Lysozyme C" outranks "Lysozyme-like protein 6" and both outrank an enzyme acting on it."""
        q = query.strip().lower()
        gene = (cand.get("gene") or "").lower()
        syns = [x.lower() for x in (cand.get("synonyms") or [])]
        name = (cand.get("protein_name") or "").lower()
        if q and (q == gene or q in syns):
            return (3, 0)
        if q and q == name:
            return (3, len(name))
        toks = [t for t in re.split(r"[^a-z0-9]+", name) if t]
        qtoks = [t for t in re.split(r"[^a-z0-9]+", q) if t]
        if qtoks and any("poly" + qtoks[0] == t for t in toks) and len(toks) <= len(qtoks) + 1:
            return (2.5, len(name))
        if (q in toks or q in name) and len(toks) <= len(qtoks) + 1:
            return (2.5, len(name))
        if q in toks or (len(qtoks) > 1 and q in name):
            return (2, len(name))
        if qtoks and any(qtoks[0] in t and len(t) <= len(qtoks[0]) + 4 for t in toks):
            return (1, len(name))
        return (0, len(name))

    def resolve(self, query: str, organism: str = "human") -> Dict[str, Any]:
        query = query.strip()
        if not query:
            return {"error": "empty query"}
        # "human ubiquitin": the organism is a filter, not part of the name
        m = self._ORGANISM_WORDS.search(query)
        if m and len(query.split()) > 1:
            word = (m.group(1) or m.group(2) or "").lower()
            if not organism or organism.lower() in ("human", word):
                organism = word
            query = (query[:m.start()] + " " + query[m.end():]).strip()
        words = [w for w in query.split() if w.lower() not in self._NOISE_WORDS]
        query = " ".join(words) if words else query
        key = "resolve3_%s_%s" % (query.lower(), (organism or "").lower())
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

        def fetch(uq: str) -> Optional[List[Dict[str, Any]]]:
            url = "%s/search?%s" % (_BASE, urllib.parse.urlencode({"query": uq, "fields": fields, "format": "json", "size": 100}))
            try:
                return (request_json("GET", url, timeout=self.timeout) or {}).get("results") or []
            except Exception as e:  # noqa: BLE001  network error: report and stop
                nonlocal_err[0] = str(e)
                return None

        nonlocal_err = [None]
        merged: List[Dict[str, Any]] = []
        seen: set = set()

        def add(results: List[Dict[str, Any]]) -> None:
            for r in results:
                acc = r.get("primaryAccession")
                if acc and acc not in seen:
                    seen.add(acc)
                    merged.append(r)

        exact = None
        for n, uq in enumerate(queries):
            results = fetch(uq)
            if results is None:
                break
            if n == 0 and results and not queries[0].startswith("(accession:"):
                exact = results   # an exact gene symbol: no ranking needed
                add(results)
                break
            add(results)
            if queries[0].startswith("(accession:") and results:
                break
            if n >= 2 and merged:   # gene + protein-name searches gathered: enough to rank
                break
        last_err = nonlocal_err[0]
        if merged:
            results = merged
            if True:
                out = {"query": query, "organism": organism, "candidates": []}
                for r in results:
                    genes = r.get("genes") or []
                    primary = ""
                    synonyms = []
                    if genes:
                        primary = (genes[0].get("geneName") or {}).get("value", "")
                        synonyms = [x.get("value", "") for x in genes[0].get("synonyms", [])]
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
                # UniProt's own order is relevance over all fields; what the user means by a bare name is the
                # protein called that, not an enzyme acting on it. Rank by name match, then keep five.
                if exact is None:
                    out["candidates"].sort(key=lambda c: (-self._name_score(query, c)[0], not c.get("reviewed"), self._name_score(query, c)[1]))
                out["candidates"] = out["candidates"][:5]
                best = out["candidates"][0]
                out.update({"accession": best["accession"], "gene": best["gene"],
                            "protein_name": best["protein_name"], "length": best["length"],
                            "open_command": "open alphafold:%s" % best["accession"]})
                # "the adrenergic receptor" matches ADRA1A, ADRA2A, ADRB1 and ADRB2 equally well, and picking the
                # first one silently opens a different protein from the one the user meant. Say so.
                toks = {w for w in re.split(r"[^a-z0-9]+", query.strip().lower()) if w}
                named = any((c.get("gene") or "").lower() in toks or (c.get("accession") or "").lower() in toks
                            or any(x.lower() in toks for x in (c.get("synonyms") or []))
                            for c in out["candidates"])
                best_q = self._name_score(query, best)[0]
                if exact is None and best_q == 0 and not named:
                    # nothing is called that in this organism (GFP is a jellyfish protein, "spike" a viral one):
                    # a list of unrelated proteins must not become a silent resolution
                    out["note"] = ("No %s protein is named '%s'. It may be a protein of another organism (then say which, "
                                   "e.g. organism 'jellyfish' or a virus) or a PDB entry; the candidates are only text matches."
                                   % (organism or "human", query))
                    for k in ("accession", "gene", "protein_name", "length", "open_command"):
                        out.pop(k, None)
                    out["unresolved"] = True
                    self._store(key, out)
                    return out
                rival = [c for c in out["candidates"]
                         if c.get("reviewed") and (c.get("gene") or "").upper() != (best.get("gene") or "").upper()
                         and self._name_score(query, c)[0] >= max(1, best_q)]
                if not named and rival:
                    out["ambiguous"] = [{"accession": c["accession"], "gene": c["gene"],
                                         "protein_name": c["protein_name"]}
                                        for c in ([best] + rival)[:5]]
                    out["note"] = ("'%s' matches several %s proteins equally well. None is resolved: ask the user which one "
                                   "with ask_user before opening or naming any of them." % (query, organism or "human"))
                    for k in ("accession", "gene", "protein_name", "length", "open_command"):
                        out.pop(k, None)
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
