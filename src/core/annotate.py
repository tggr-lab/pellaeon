"""Pure logic for annotation overlays: turn UniProt/ClinVar records plus a residue mapping into commands."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

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
             "description": v["significance"], "single": True, "ref": v["ref"], "color": color_for(v["significance"]),
             "pathogenic": v["significance"].lower().startswith(("pathogenic", "likely pathogenic"))}
            for v in data.get("variants", [])]


def _ranges(nums: List[int]) -> str:
    nums = sorted(set(nums))
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append("%d-%d" % (start, prev) if start != prev else str(start))
        start = prev = n
    out.append("%d-%d" % (start, prev) if start != prev else str(start))
    return ",".join(out)


def build_annotation(items: List[Dict[str, Any]], mapping: Dict[str, Any], color: str = "orange",
                     label: bool = True, max_labels: int = 40, edition: str = "chimerax") -> Tuple[List[str], Dict[str, Any]]:
    """mapping: {"map": {pos: {"chain": "A", "number": 159, "resname": "LYS"}}, "model": "#1", ...}"""
    pmap = {int(k): v for k, v in (mapping.get("map") or {}).items()}
    model = mapping.get("model", "#1")
    cmds: List[str] = []
    mapped = unmapped = mismatched = labeled = 0
    listing = []
    color_groups: Dict[Tuple[str, str], List[Tuple[str, int]]] = {}   # (color, chain) -> numbers
    singles: List[Tuple[str, int]] = []
    for it in items:
        col = it.get("color") or color
        hits = [(pmap[p]["chain"], pmap[p]["number"], pmap[p].get("resname", "")) for p in it["positions"] if p in pmap]
        if not hits:
            unmapped += 1
            continue
        if it.get("ref") and it["single"]:
            resname = hits[0][2]
            if resname and AA3.get(resname.upper()) and AA3[resname.upper()] != it["ref"]:
                mismatched += 1
                continue  # the model residue is not the reference amino acid: numbering does not correspond
        mapped += 1
        for ch, num, _rn in hits:
            color_groups.setdefault((col, ch), []).append(num)
        if it["single"]:
            singles.append((hits[0][0], hits[0][1]))
        if label and labeled < max_labels and (it["single"] or it.get("type") in ("Domain", "Transmembrane", "Topological domain", "Region", "Motif")) \
                and (it.get("pathogenic", True)):
            ch, num, _rn = hits[0]
            text = it["label"].replace('"', "")
            if edition == "chimera":
                cmds.append('rlabel %s:%d.%s text "%s"' % (model, num, ch, text))
            else:
                cmds.append('label %s/%s:%d text "%s"' % (model, ch, num, text))
            labeled += 1
        listing.append({"type": it["type"], "residues": ",".join("%s:%d" % (c, n) for c, n, _ in hits[:6]) + ("…" if len(hits) > 6 else ""),
                        "description": it["description"][:120]})
    # coloring first (labels were appended above; keep colors before labels)
    color_cmds = []
    for (col, ch), nums in color_groups.items():
        spec = _ranges(nums)
        if edition == "chimera":
            color_cmds.append("color %s,a,r %s:%s.%s" % (col, model, spec, ch))
        else:
            color_cmds.append("color %s/%s:%s %s target ac" % (model, ch, spec, col))
    if singles:
        by_chain: Dict[str, List[int]] = {}
        for ch, num in singles:
            by_chain.setdefault(ch, []).append(num)
        for ch, nums in by_chain.items():
            if edition == "chimera":
                color_cmds.append("display %s:%s.%s" % (model, _ranges(nums), ch))
                color_cmds.append("represent stick %s:%s.%s" % (model, _ranges(nums), ch))
            else:
                color_cmds.append("show %s/%s:%s atoms" % (model, ch, _ranges(nums)))
                color_cmds.append("style %s/%s:%s stick" % (model, ch, _ranges(nums)))
    cmds = color_cmds + cmds
    summary = {"mapped": mapped, "unmapped": unmapped, "reference_mismatch": mismatched, "labeled": labeled,
               "chains": sorted({ch for (_c, ch) in color_groups}), "model": model,
               "mapping_note": mapping.get("note", ""), "annotations": listing[:40]}
    return cmds, summary
