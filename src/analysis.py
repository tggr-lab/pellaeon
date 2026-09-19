"""ChimeraX-side analysis helpers used by the compare and annotate tools (main thread only)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_DISEASE_RE = re.compile(r"\bin ([A-Z][A-Z0-9]{2,}[^;.]*)")


def _find_structure(session, spec: str):
    """Accept '#1', '1', '#1.1', a model name, or (if only one structure is open) anything."""
    from chimerax.atomic import AtomicStructure
    structs = [m for m in session.models.list() if isinstance(m, AtomicStructure)]
    ident = (spec or "").strip()
    m = re.search(r"#?(\d+(?:\.\d+)*)", ident)
    if m:
        for st in structs:
            if st.id_string == m.group(1):
                return st
    if m:
        return None  # an explicit id that does not exist must not silently become another model
    low = ident.lower().lstrip("#")
    for st in structs:
        if low and (low == st.name.lower() or low in st.name.lower()):
            return st
    if len(structs) == 1 and not low:
        return structs[0]
    return None


def compare_structures(session, run_commands, ref: str = "#1", other: str = "#2", chain: Optional[str] = None,
                       color: bool = True) -> Dict[str, Any]:
    """Superpose `other` onto `ref` and compute per-residue CA displacement."""
    import numpy as np
    from chimerax.atomic import Residue
    a = _find_structure(session, ref)
    b = _find_structure(session, other)
    if a is None or b is None:
        return {"error": "Need two open atomic models, e.g. #1 and #2 (got %s, %s)." % (ref, other)}
    if a is b:
        return {"error": "Reference and compared model are the same (%s)." % ref}
    if not chain and len(a.chains) > 1:
        # matchmaker superposes one chain pair; compare that chain only so other chains do not look "moved"
        chain = a.chains[0].chain_id
    spec_a = "#%s%s" % (a.id_string, "/" + chain if chain else "")
    spec_b = "#%s%s" % (b.id_string, "/" + chain if chain else "")
    res = run_commands(["matchmaker %s to %s" % (spec_b, spec_a)])
    if not res or not res[0].get("ok"):
        return {"error": "matchmaker failed: %s" % (res[0].get("error") if res else "no result")}
    rmsd_line = next((i for i in res[0].get("info", []) if "RMSD" in i), "")
    # pair residues by chain id + number
    ca_a = {}
    for r in a.residues:
        if chain and r.chain_id != chain:
            continue
        at = r.find_atom("CA")
        if at is not None:
            ca_a[(r.chain_id, r.number)] = at
    try:
        Residue.register_attr(session, "pellaeon_disp", "Pellaeon", attr_type=float)
    except Exception:
        pass
    pairs = []
    for r in b.residues:
        if chain and r.chain_id != chain:
            continue
        at = r.find_atom("CA")
        ref_at = ca_a.get((r.chain_id, r.number))
        if at is None or ref_at is None:
            continue
        d = float(np.linalg.norm(at.scene_coord - ref_at.scene_coord))
        r.pellaeon_disp = d
        pairs.append((r.chain_id, r.number, r.name, d))
    if not pairs:
        return {"error": "No residues with matching chain ids and numbers between %s and %s." % (ref, other),
                "rmsd": rmsd_line}
    disps = np.array([p[3] for p in pairs])
    top = sorted(pairs, key=lambda p: -p[3])[:12]
    # contiguous regions moving more than 2 A
    regions = []
    cur = None
    for cid, num, name, d in sorted(pairs):
        if d > 2.0:
            if cur and cur[0] == cid and num == cur[2] + 1:
                cur[2] = num
                cur[3] = max(cur[3], d)
            else:
                if cur:
                    regions.append(cur)
                cur = [cid, num, num, d]
        else:
            if cur:
                regions.append(cur)
                cur = None
    if cur:
        regions.append(cur)
    out: Dict[str, Any] = {
        "reference": spec_a, "compared": spec_b, "chain": chain or "all", "rmsd": rmsd_line or "see log",
        "paired_residues": len(pairs), "mean_displacement": round(float(disps.mean()), 2),
        "max_displacement": round(float(disps.max()), 2),
        "residues_over_2A": int((disps > 2.0).sum()),
        "top_shifted": [{"chain": c, "residue": n, "name": nm, "displacement": round(d, 2)} for c, n, nm, d in top],
        "moving_regions": [{"chain": r[0], "range": "%d-%d" % (r[1], r[2]), "max": round(r[3], 2)} for r in regions[:15]],
    }
    if color:
        cmds = ["color byattribute r:pellaeon_disp %s palette 0,#d9d9d9:1,#ffd27f:3,orange:6,red range 0,6 target ac novalue gray" % spec_b,
                "color %s #9ecae1 target ac" % spec_a]
        cres = run_commands(cmds)
        out["colored"] = all(x.get("ok") for x in cres)
        out["coloring"] = "%s colored by displacement (gray = unchanged, red = 6 A or more); %s light blue" % (spec_b, spec_a)
    return out


def annotate_clinvar(session, run_commands, clinvar, m, gene: str, label: bool = True, max_labels: int = 60) -> Dict[str, Any]:
    """Color residues by ClinVar missense classification (red pathogenic ... blue benign, yellow VUS)."""
    from .core.clinvar import color_for
    data = clinvar.missense_variants(gene)
    if "error" in data:
        return data
    variants = data.get("variants", [])
    if not variants:
        return {"gene": gene, "kind": "clinvar", "count": 0, "message": "ClinVar lists no missense variants for %s." % gene}
    spec = "#" + m.id_string
    cmds: List[str] = []
    by_color: Dict[str, List[int]] = {}
    for v in variants:
        by_color.setdefault(color_for(v["significance"]), []).append(v["position"])
    for col, positions in by_color.items():
        for i in range(0, len(positions), 150):
            cmds.append("color %s:%s %s target ac" % (spec, ",".join(str(p) for p in positions[i:i + 150]), col))
    labeled = 0
    if label:
        for v in sorted(variants, key=lambda x: (x["significance"] != "Pathogenic", x["position"])):
            if not v["significance"].lower().startswith(("pathogenic", "likely pathogenic")) or labeled >= max_labels:
                continue
            cmds.append('label %s:%d text "%s%d%s" height 1.2 color %s' % (spec, v["position"], v["ref"], v["position"], v["alt"],
                                                                          color_for(v["significance"])))
            labeled += 1
    res = run_commands(cmds)
    counts: Dict[str, int] = {}
    for v in variants:
        counts[v["significance"]] = counts.get(v["significance"], 0) + 1
    return {"gene": gene, "kind": "clinvar", "count": len(variants), "labeled": labeled, "by_significance": counts,
            "legend": "red = pathogenic, orange = likely pathogenic, yellow = uncertain, cyan/blue = (likely) benign",
            "commands_failed": sum(1 for r in res if not r.get("ok")),
            "pathogenic": [{"residue": v["position"], "change": "%s%d%s" % (v["ref"], v["position"], v["alt"])}
                           for v in variants if v["significance"].lower().startswith("pathogenic")][:40],
            "source": data.get("source")}


def annotate(session, run_commands, uniprot, model: str, accession: str, kind: str = "variant",
             color: str = "orange", label: bool = True, only_disease: bool = False, max_labels: int = 40,
             clinvar=None) -> Dict[str, Any]:
    """Color/label UniProt (or ClinVar) annotations on a model (numbering must match UniProt, as in AlphaFold models)."""
    m = _find_structure(session, model)
    if m is None:
        return {"error": "No atomic model %s is open." % model}
    if not re.match(r"^(#[0-9a-fA-F]{6}|[A-Za-z][A-Za-z ]{1,30})$", color or ""):
        color = "orange"
    kind_l = kind.lower().strip()
    if kind_l in ("clinvar", "pathogenic", "clinical") and clinvar is not None:
        gene = accession
        if re.match(r"^[OPQ][0-9][A-Z0-9]{3}[0-9]$|^[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}$", (accession or "").upper()):
            info = uniprot.features(accession, ["Domain"])
            gene = info.get("gene") or accession
        return annotate_clinvar(session, run_commands, clinvar, m, gene, label)
    kinds_map = {
        "variant": ["Natural variant"], "variants": ["Natural variant"], "disease": ["Natural variant"],
        "domain": ["Domain"], "domains": ["Domain"], "transmembrane": ["Transmembrane"], "tm": ["Transmembrane"],
        "topology": ["Topological domain", "Transmembrane"], "binding": ["Binding site"], "binding site": ["Binding site"],
        "active": ["Active site"], "active site": ["Active site"], "site": ["Site", "Active site", "Binding site"],
        "glycosylation": ["Glycosylation"], "disulfide": ["Disulfide bond"], "modified": ["Modified residue"],
        "ptm": ["Modified residue", "Glycosylation", "Lipidation"], "region": ["Region", "Motif"], "motif": ["Motif"],
        "secondary": ["Helix", "Beta strand", "Turn"],
    }
    kinds = kinds_map.get(kind_l, [kind])
    if kind_l == "disease":
        only_disease = True
    # accept a gene/protein name, or take the accession from an AlphaFold model name
    acc = (accession or "").strip()
    if not re.match(r"^[OPQ][0-9][A-Z0-9]{3}[0-9]$|^[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}$", acc.upper()):
        mname = re.search(r"\b([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})\b", m.name.upper())
        if mname:
            acc = mname.group(1)
        elif acc:
            r = uniprot.resolve(acc, "human")
            if r.get("accession"):
                acc = r["accession"]
            else:
                return {"error": "Could not find a UniProt accession for '%s'." % accession}
        else:
            return {"error": "No UniProt accession given and none found in the model name."}
    accession = acc
    feats = uniprot.features(accession, kinds)
    if "error" in feats:
        return feats
    items = feats.get("features", [])
    if only_disease:
        # UniProt writes disease-linked variants as "in <DISEASE>; ..." and neutral ones as "in dbSNP:..."
        items = [f for f in items if re.match(r"\s*in (?!dbSNP)", f.get("description", ""))]
    if not items:
        return {"accession": accession, "kind": kind, "count": 0, "message": "No matching annotations in UniProt %s." % accession}
    spec = "#" + m.id_string
    numbered_ok = any(r.number == items[0]["start"] for r in m.residues) or True
    cmds: List[str] = []
    labeled = 0
    listing = []
    for f in items:
        rng = f["spec"]
        cmds.append("color %s%s %s target ac" % (spec, rng, color))
        if f["start"] == f["end"]:
            cmds.append("show %s%s atoms" % (spec, rng))
            cmds.append("style %s%s stick" % (spec, rng))
        desc = f.get("description", "")
        if label and labeled < max_labels:
            short = re.sub(r"\s*\(.*?\)|dbSNP:\S+|;.*", "", desc).strip()[:40] or f["type"]
            cmds.append('label %s%s text "%s" height 1.2 color %s' % (spec, rng, short.replace('"', ""), color))
            labeled += 1
        listing.append({"type": f["type"], "residues": rng.lstrip(":"), "description": desc[:120]})
    res = run_commands(cmds)
    failed = [r for r in res if not r.get("ok")]
    warn = ""
    if not ("alphafold" in m.name.lower() or accession.upper() in m.name.upper()):
        warn = "Model %s may not use UniProt numbering (it is not an AlphaFold model); positions can be shifted." % spec
    return {"accession": accession, "kind": kind, "count": len(items), "labeled": labeled, "colored": len(items),
            "commands_failed": len(failed), "annotations": listing[:40], "warning": warn}


class AskMouseMode:
    """Factory for the 'pellaeon ask' mouse mode: click an atom to ask about its residue."""

    @staticmethod
    def make(session, on_pick):
        from chimerax.mouse_modes import MouseMode

        class _Mode(MouseMode):
            name = "pellaeon ask"
            icon_file = None

            def mouse_down(self, event):
                MouseMode.mouse_down(self, event)
                x, y = event.position()
                pick = self.view.picked_object(x, y)
                res = getattr(pick, "residue", None)
                atom = getattr(pick, "atom", None)
                if res is None and atom is not None:
                    res = atom.residue
                if res is None:
                    return
                on_pick({"spec": "#%s/%s:%d" % (res.structure.id_string, res.chain_id, res.number),
                         "name": res.name, "number": int(res.number), "chain": res.chain_id,
                         "model": res.structure.name, "atom": atom.name if atom is not None else ""})
        return _Mode(session)
