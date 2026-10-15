"""Pure logic for annotation overlays: turn UniProt/ClinVar records plus a residue mapping into commands."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .resid import ResId, STATUS_UNRESOLVED, entry_targets, number_text, specs_by_chain, target_id

KINDS = {
    "variant": ["Natural variant"], "variants": ["Natural variant"], "disease": ["Natural variant"],
    "domain": ["Domain"], "domains": ["Domain"], "transmembrane": ["Transmembrane"], "tm": ["Transmembrane"],
    "topology": ["Topological domain", "Transmembrane"], "binding": ["Binding site"], "binding site": ["Binding site"],
    "active": ["Active site"], "active site": ["Active site"], "site": ["Site", "Active site", "Binding site"],
    "glycosylation": ["Glycosylation"], "disulfide": ["Disulfide bond"], "modified": ["Modified residue"],
    "ptm": ["Modified residue", "Glycosylation", "Lipidation"], "region": ["Region", "Motif"], "motif": ["Motif"],
    "secondary": ["Helix", "Beta strand", "Turn"],
}
CLINVAR_KINDS = ("clinvar", "pathogenic", "clinical")
AA3 = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H",
       "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
       "TYR": "Y", "VAL": "V", "MSE": "M"}
_ACC_RE = re.compile(r"^[OPQ][0-9][A-Z0-9]{3}[0-9]$|^[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}$")


def is_accession(s: str) -> bool:
    return bool(_ACC_RE.match((s or "").strip().upper()))


def normalize_kind(kind: str) -> Tuple[Optional[List[str]], bool, bool]:
    """-> (uniprot feature types or None, only_disease, is_clinvar)"""
    k = (kind or "variant").lower().strip()
    if k in CLINVAR_KINDS:
        return None, False, True
    return KINDS.get(k, [kind]), k == "disease", False


def uniprot_items(feats: Dict[str, Any], only_disease: bool) -> List[Dict[str, Any]]:
    items = []
    for f in feats.get("features", []):
        desc = f.get("description", "")
        if only_disease and not re.match(r"\s*in (?!dbSNP)", desc):
            continue
        endpoints = f.get("type") == "Disulfide bond" and f["start"] != f["end"]
        positions = [f["start"], f["end"]] if endpoints else list(range(f["start"], f["end"] + 1))
        short = re.sub(r"\s*\(.*?\)|dbSNP:\S+|;.*", "", desc).strip()[:40] or f.get("type", "")
        items.append({"type": f.get("type", ""), "positions": positions, "label": short, "description": desc,
                      "single": f["start"] == f["end"], "ref": None, "color": None})
    return items


def clinvar_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    from .clinvar import color_for
    return [{"type": v["significance"], "positions": [v["position"]], "label": "%s%d%s" % (v["ref"], v["position"], v["alt"]),
             "description": v["significance"] + (" (%s)" % "; ".join(v["conditions"]) if v.get("conditions") else ""),
             "conditions": v.get("conditions") or [], "single": True, "ref": v["ref"], "color": color_for(v["significance"]),
             "pathogenic": v["significance"].lower().startswith(("pathogenic", "likely pathogenic"))}
            for v in data.get("variants", [])]


def build_annotation(items: List[Dict[str, Any]], mapping: Dict[str, Any], color: str = "orange",
                     label: bool = True, max_labels: int = 40, edition: str = "chimerax") -> Tuple[List[str], Dict[str, Any]]:
    """mapping: {"map": {pos: [{"model": "#1", "chain": "A", "number": 87, "icode": "", "resname": "LYS",
    "status": "offset", "basis": ...}, ...]}, "model": "#1", "insertion_numbers": {"#1/A": [82]}, ...}.
    Older mappings with one {"chain", "number", "resname"} dict per position are read too."""
    model = mapping.get("model", "#1")
    pmap = {int(k): entry_targets(v, model) for k, v in (mapping.get("map") or {}).items()}
    ins: Dict[Tuple[str, str], List[int]] = {}
    for k, nums in (mapping.get("insertion_numbers") or {}).items():
        m, _, c = str(k).rpartition("/")
        ins[(m or model, c)] = list(nums)
    cmds: List[str] = []
    mapped = unmapped = mismatched = labeled = 0
    listing = []
    color_groups: Dict[str, List[ResId]] = {}   # color -> residues
    singles: List[ResId] = []
    statuses: Dict[str, int] = {}
    for it in items:
        col = it.get("color") or color
        hits = [(target_id(t, model), t.get("resname", ""), t.get("status") or "") for p in it["positions"] for t in pmap.get(p, [])
                if t.get("status") != STATUS_UNRESOLVED]
        if not hits:
            unmapped += 1
            continue
        if it.get("ref") and it["single"]:
            resname = hits[0][1]
            if resname and AA3.get(resname.upper()) and AA3[resname.upper()] != it["ref"]:
                mismatched += 1
                continue  # the model residue is not the reference amino acid: numbering does not correspond
        mapped += 1
        for rid, _rn, st in hits:
            color_groups.setdefault(col, []).append(rid)
            if st:
                statuses[st] = statuses.get(st, 0) + 1
        if it["single"]:
            singles.extend(rid for rid, _rn, _st in hits)
        if label and labeled < max_labels and (it["single"] or it.get("type") in ("Domain", "Transmembrane", "Topological domain", "Region", "Motif")) \
                and (it.get("pathogenic", True)):
            rid = hits[0][0]
            text = it["label"].replace('"', "")
            if edition == "chimera":
                cmds.append('rlabel %s:%s.%s text "%s"' % (rid.model, number_text(rid.number, rid.icode), rid.chain, text))
            else:
                cmds.append('label %s text "%s"' % (rid.spec(), text))
            labeled += 1
        listing.append({"type": it["type"], "residues": ",".join(r.short() for r, _, _ in hits[:6]) + ("…" if len(hits) > 6 else ""),
                        "description": it["description"][:120]})
    # coloring first (labels were appended above; keep colors before labels)
    color_cmds = []
    for col, rids in color_groups.items():
        for spec in specs_by_chain(rids, ins, model):
            if edition == "chimera":
                m, _, rest = spec.partition("/")
                ch, _, nums = rest.partition(":")
                color_cmds.append("color %s,a,r %s:%s.%s" % (col, m, nums, ch))
            else:
                color_cmds.append("color %s %s target ac" % (spec, col))
    for spec in specs_by_chain(singles, ins, model):
        if edition == "chimera":
            m, _, rest = spec.partition("/")
            ch, _, nums = rest.partition(":")
            color_cmds.append("display %s:%s.%s" % (m, nums, ch))
            color_cmds.append("represent stick %s:%s.%s" % (m, nums, ch))
        else:
            color_cmds.append("show %s atoms" % spec)
            color_cmds.append("style %s stick" % spec)
    cmds = color_cmds + cmds
    summary = {"mapped": mapped, "unmapped": unmapped, "reference_mismatch": mismatched, "labeled": labeled,
               "chains": sorted({r.chain for rids in color_groups.values() for r in rids}), "model": model,
               "mapping_note": mapping.get("note", ""), "annotations": listing[:40]}
    if statuses:
        summary["status"] = statuses
    return cmds, summary
