"""ChimeraX-side analysis helpers used by the compare and annotate tools (main thread only)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_DISEASE_RE = re.compile(r"\bin ([A-Z][A-Z0-9]{2,}[^;.]*)")


def _find_structure(session, spec: str):
    from chimerax.atomic import AtomicStructure
    ident = spec.strip().lstrip("#")
    for m in session.models.list():
        if isinstance(m, AtomicStructure) and m.id_string == ident:
            return m
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


def annotate(session, run_commands, uniprot, model: str, accession: str, kind: str = "variant",
             color: str = "orange", label: bool = True, only_disease: bool = False, max_labels: int = 40) -> Dict[str, Any]:
    """Color/label UniProt annotations on a model (numbering must match UniProt, as in AlphaFold models)."""
    m = _find_structure(session, model)
    if m is None:
        return {"error": "No atomic model %s is open." % model}
    kind_l = kind.lower().strip()
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
