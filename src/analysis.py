"""ChimeraX-side analysis helpers used by the compare and annotate tools (main thread only)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional



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


def prepare_compare(session, reference: str, other: str, chain: Optional[str] = None) -> Dict[str, Any]:
    """Read-only: resolve both models and choose the chain to compare. No commands are run here."""
    a = _find_structure(session, reference)
    b = _find_structure(session, other)
    if a is None or b is None:
        return {"error": "Need two open atomic models, e.g. #1 and #2 (got %s, %s)." % (reference, other)}
    if a is b:
        return {"error": "Reference and compared model are the same (%s)." % reference}
    if not chain and len(a.chains) > 1:
        chain = a.chains[0].chain_id   # matchmaker superposes one chain pair; keep the stats on that chain
    return {"ref_id": a.id_string, "other_id": b.id_string, "chain": chain,
            "ref_spec": "#%s%s" % (a.id_string, "/" + chain if chain else ""),
            "other_spec": "#%s%s" % (b.id_string, "/" + chain if chain else "")}


def compute_displacement(session, prep: Dict[str, Any], matchmaker_returns=None) -> Dict[str, Any]:
    """Read-only: per-residue CA displacement using matchmaker's own residue correspondence
    (the full pre-pruning atom pairs it returns); falls back to chain+number pairing."""
    import numpy as np
    from chimerax.atomic import Residue
    a = _find_structure(session, "#" + prep["ref_id"])
    b = _find_structure(session, "#" + prep["other_id"])
    if a is None or b is None:
        return {"error": "Models changed during the comparison."}
    pairs = []          # (ref_residue, other_residue)
    basis = "matchmaker alignment"
    for rv in (matchmaker_returns or []):
        if not isinstance(rv, dict):
            continue
        ra, ma = rv.get("full ref atoms"), rv.get("full match atoms")
        if ra is None or ma is None:
            continue
        for x, y in zip(ra, ma):
            if x.structure is a and y.structure is b:
                pairs.append((x.residue, y.residue))
    if not pairs:
        basis = "chain id + residue number (no alignment available)"
        chain = prep.get("chain")
        ca = {}
        for r in a.residues:
            if (not chain or r.chain_id == chain) and r.find_atom("CA") is not None:
                ca[(r.chain_id, r.number, r.insertion_code)] = r
        for r in b.residues:
            if (not chain or r.chain_id == chain) and r.find_atom("CA") is not None:
                ref = ca.get((r.chain_id, r.number, r.insertion_code))
                if ref is not None:
                    pairs.append((ref, r))
    if not pairs:
        return {"error": "No residues could be paired between %s and %s." % (prep["ref_spec"], prep["other_spec"])}
    try:
        Residue.register_attr(session, "pellaeon_disp", "Pellaeon", attr_type=float)
    except Exception:
        pass
    disps = []
    rows = []
    for ref_r, oth_r in pairs:
        x, y = ref_r.find_atom("CA"), oth_r.find_atom("CA")
        if x is None or y is None:
            continue
        d = float(np.linalg.norm(x.scene_coord - y.scene_coord))
        oth_r.pellaeon_disp = d
        disps.append(d)
        rows.append((oth_r.chain_id, int(oth_r.number), oth_r.name, d, ref_r.chain_id, int(ref_r.number)))
    if not rows:
        return {"error": "Paired residues have no CA atoms."}
    arr = np.array(disps)
    top = sorted(rows, key=lambda r: -r[3])[:12]
    regions = []
    cur = None
    for cid, num, name, d, rcid, rnum in sorted(rows, key=lambda r: (r[0], r[1])):
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
    total_other = sum(1 for r in b.residues if (not prep.get("chain") or r.chain_id == prep["chain"]) and r.find_atom("CA") is not None)
    color_cmds = ["color byattribute r:pellaeon_disp %s palette 0,#d9d9d9:1,#ffd27f:3,orange:6,red range 0,6 target ac novalue gray" % prep["other_spec"],
                  "color %s #9ecae1 target ac" % prep["ref_spec"]]
    return {
        "pairing": basis, "paired_residues": len(rows), "coverage": "%d of %d residues of %s paired" % (len(rows), total_other, prep["other_spec"]),
        "mean_displacement": round(float(arr.mean()), 2), "max_displacement": round(float(arr.max()), 2),
        "residues_over_2A": int((arr > 2.0).sum()),
        "top_shifted": [{"chain": c, "residue": n, "name": nm, "displacement": round(d, 2), "reference_residue": "%s:%d" % (rc, rn)}
                        for c, n, nm, d, rc, rn in top],
        "moving_regions": [{"chain": r[0], "range": "%d-%d" % (r[1], r[2]), "max": round(r[3], 2),
                            "spec": "#%s/%s:%d-%d" % (prep["other_id"], r[0], r[1], r[2])} for r in regions[:15]],
        "coloring": "%s colored by displacement (gray unchanged, red 6 A or more); %s light blue" % (prep["other_spec"], prep["ref_spec"]),
        "color_commands": color_cmds,
    }


def map_positions(session, model: str, accession: str, positions: List[int]) -> Dict[str, Any]:
    """Read-only: map UniProt positions of `accession` onto residues of `model`, chain by chain,
    using the mmCIF/PDB struct_ref mapping (numbering offsets) when the file has it."""
    m = _find_structure(session, model)
    if m is None:
        return {"error": "No atomic model %s is open." % model}
    acc = (accession or "").upper()
    targets: List[Tuple[str, int, Tuple[int, int]]] = []   # (chain_id, offset, (db_start, db_end))
    note = ""
    try:
        from chimerax.atomic import uniprot_ids
        entries = uniprot_ids(m)
    except Exception:
        entries = []
    for e in entries:
        if (getattr(e, "uniprot_id", "") or "").upper() == acc and e.database_sequence_range and e.chain_sequence_range:
            targets.append((e.chain_id, e.chain_sequence_range[0] - e.database_sequence_range[0], tuple(e.database_sequence_range)))
    if not targets:
        if "alphafold" in m.name.lower() or acc in m.name.upper() or len(m.chains) == 1:
            targets = [(c.chain_id, 0, (1, 10 ** 6)) for c in m.chains]
            note = "" if ("alphafold" in m.name.lower() or acc in m.name.upper()) else \
                "The structure file does not say which UniProt entry its chain is; positions were applied 1:1 to the single chain."
        elif entries:
            return {"error": "Model %s has UniProt mappings for %s but not for %s." % (
                model, ", ".join(sorted({getattr(e, "uniprot_id", "?") for e in entries})), acc)}
        else:
            return {"error": "Model %s has several chains and no UniProt mapping; say which chain corresponds to %s." % (model, acc)}
    by_chain = {}
    for c in m.chains:
        by_chain[c.chain_id] = {int(r.number): r for r in c.existing_residues}
    out_map: Dict[int, Dict[str, Any]] = {}
    for pos in positions:
        for cid, offset, (dstart, dend) in targets:
            if not (dstart <= pos <= dend):
                continue
            r = by_chain.get(cid, {}).get(pos + offset)
            if r is not None and pos not in out_map:
                out_map[pos] = {"chain": cid, "number": int(r.number), "resname": r.name}
    return {"model": "#" + m.id_string, "map": out_map, "chains": sorted({t[0] for t in targets}),
            "offsets": {t[0]: t[1] for t in targets}, "unmapped": [p for p in positions if p not in out_map], "note": note}


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
