"""GPCRdb: experimental structures per receptor (with state and ligand) and the AlphaFold-Multistate
models (inactive / active), for "show me PAR1 active and inactive" and state-change animations.

GPCRdb names receptors by the UniProt entry name in lower case (PAR1_HUMAN -> par1_human). The
structure list is a public JSON service; the multistate models are zip files behind the website's
download links (no JSON service), one PDB per zip.
"""
from __future__ import annotations

import io
import os
import re
import zipfile
from typing import Any, Dict, List, Optional

from .http import request_json, request_bytes

GPCRDB = "https://gpcrdb.org"
STATES = ("inactive", "active")


def entry_name(uniprot_entry_name: str) -> str:
    """'PAR1_HUMAN' -> 'par1_human' (what GPCRdb calls the receptor)."""
    return (uniprot_entry_name or "").strip().lower()


def entry_for_accession(accession: str, timeout: float = 30) -> str:
    """UniProt accession -> GPCRdb entry name (P25116 -> par1_human), via the UniProt entry name."""
    acc = (accession or "").strip().upper()
    if re.match(r"^[A-Z0-9]+_[A-Z]+$", acc):      # already an entry name
        return acc.lower()
    d = request_json("GET", "https://rest.uniprot.org/uniprotkb/%s.json?fields=id" % acc, timeout=timeout)
    return entry_name(str(d.get("uniProtkbId", "")))


def structures(entry: str, timeout: float = 30) -> Dict[str, Any]:
    """Experimental structures of a receptor: PDB code, state, method, resolution, ligands."""
    try:
        rows = request_json("GET", "%s/services/structure/protein/%s/" % (GPCRDB, entry), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"error": "GPCRdb request failed for %s: %s" % (entry, e)}
    if not isinstance(rows, list):
        return {"error": "GPCRdb has no structures listed for %s." % entry}
    out = []
    for r in rows:
        ligs = ["%s (%s)" % (l.get("name"), l["function"].lower()) if l.get("function") else l.get("name")
                for l in (r.get("ligands") or []) if l.get("name")]
        out.append({"pdb": r.get("pdb_code"), "state": r.get("state"), "method": r.get("type"),
                    "resolution": r.get("resolution"), "ligands": ligs[:4], "date": r.get("publication_date"),
                    "preferred_chain": r.get("preferred_chain"), "species": r.get("species")})
    out.sort(key=lambda x: (str(x.get("state")), -(float(x.get("resolution") or 99))), reverse=False)
    return {"entry": entry, "structures": out, "count": len(out),
            "caveat": "'state' is GPCRdb's annotation; an antagonist-bound structure is an inactive conformation whatever "
                      "the field says (GPCRdb lists 3VW7, PAR1 with the antagonist vorapaxar, as Active). Judge by the ligand's function."}


def model_url(entry: str, state: str) -> str:
    return "%s/structure/homology_models/%s_%s_full/download_pdb" % (GPCRDB, entry, state)


def fetch_model(entry: str, state: str, cache_dir: Optional[str], timeout: float = 90) -> Dict[str, Any]:
    """Download the AlphaFold-Multistate model for one state; returns the local PDB path."""
    state = state.lower().strip()
    if state not in STATES:
        return {"error": "state must be one of %s" % ", ".join(STATES)}
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cached = os.path.join(cache_dir, "%s_%s_afms.pdb" % (entry, state))
        if os.path.isfile(cached) and os.path.getsize(cached) > 100:
            return {"path": cached, "entry": entry, "state": state, "cached": True}
    try:
        raw = request_bytes("GET", model_url(entry, state), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"error": "GPCRdb has no %s multistate model for %s (%s)." % (state, entry, e)}
    text = None
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".pdb")]
            if names:
                text = z.read(names[0]).decode("utf-8", "replace")
    elif b"ATOM" in raw[:5000]:
        text = raw.decode("utf-8", "replace")
    if not text or "ATOM" not in text:
        return {"error": "GPCRdb returned no PDB for the %s model of %s." % (state, entry)}
    path = os.path.join(cache_dir or ".", "%s_%s_afms.pdb" % (entry, state))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    m = re.search(r"AFMS_(\d{4}-\d{2}-\d{2})", text[:2000]) or re.search(r"(\d{4}-\d{2}-\d{2})", raw[:400].decode("latin-1"))
    return {"path": path, "entry": entry, "state": state, "cached": False, "version": m.group(1) if m else ""}
