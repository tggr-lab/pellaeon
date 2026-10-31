"""ChimeraX-side analysis helpers used by the compare and annotate tools (main thread only)."""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple




def _no_structure(session, spec: str) -> str:
    """The error for a spec that is not an atomic model, naming the ones that are.

    In a tutorial run the model asked to compare '#1' and '#2' after a color key and a
    2D-label model had taken those ids; 'No atomic model #1 is open' gave it nothing to
    recover with and it reported that the tool could not find protein residues.
    """
    from chimerax.atomic import AtomicStructure
    have = ["#%s %s" % (m.id_string, m.name) for m in session.models.list() if isinstance(m, AtomicStructure)]
    what = ""
    for m in session.models.list():
        if "#" + m.id_string == (spec or "").strip() or m.id_string == (spec or "").strip().lstrip("#"):
            what = " (%s is a %s, not a structure)" % (spec, m.__class__.__name__)
            break
    return "No atomic model %s is open%s. Atomic models open: %s." % (spec, what, ", ".join(have) or "none")


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
        return {"error": "Need two open atomic models. %s %s" % (_no_structure(session, reference) if a is None else "",
                                                                  _no_structure(session, other) if b is None else "")}
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
    for r in b.residues:                       # stale values from an earlier comparison must not survive
        try:
            r.pellaeon_disp = None
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
        rows.append((oth_r, d, ref_r))
    if not rows:
        return {"error": "Paired residues have no CA atoms."}
    arr = np.array(disps)
    top = sorted(rows, key=lambda r: -r[1])[:12]
    # moving segments run along the chain's own residue order, so 82, 82A, 82B, 83 are consecutive
    order = {}
    for r, _d, _ref in rows:
        if r.chain is not None and r.chain not in order:
            order[r.chain] = {res: i for i, res in enumerate(r.chain.residues) if res is not None}
    def seq_pos(r):
        return order.get(r.chain, {}).get(r, int(r.number))
    regions = []
    cur = None
    for r, d, _ref in sorted(rows, key=lambda x: (x[0].chain_id, seq_pos(x[0]))):
        if d > 2.0:
            if cur and cur[0] == r.chain_id and seq_pos(r) == cur[4] + 1:
                cur[2] = r
                cur[3] = max(cur[3], d)
                cur[4] = seq_pos(r)
            else:
                if cur:
                    regions.append(cur)
                cur = [r.chain_id, r, r, d, seq_pos(r)]
        else:
            if cur:
                regions.append(cur)
                cur = None
    if cur:
        regions.append(cur)

    def num(r):
        return int(r.number) if not r.insertion_code else _res_key(r, False).split(":", 1)[1]

    def span(a, b):
        return _res_key(a, False).split(":", 1)[1] + ("" if a is b else "-" + _res_key(b, False).split(":", 1)[1])
    total_other = sum(1 for r in b.residues if (not prep.get("chain") or r.chain_id == prep["chain"]) and r.find_atom("CA") is not None)
    color_cmds = ["color byattribute r:pellaeon_disp %s palette 0,#bdbdbd:1,gold:3,orange:6,#b2182b range 0,6 target ac novalue #d9c6f0" % prep["other_spec"],
                  "color %s #b8c4d6 target ac" % prep["ref_spec"], "transparency %s 60 target c" % prep["ref_spec"],
                  "hide %s models" % prep["ref_spec"],
                  # a chain-restricted comparison leaves the other chains of both models as untouched copies
                  # floating next to the result; hide them so only what was analysed is on screen
                  *(["hide #%s & ~/%s target acs" % (prep["ref_id"], prep["chain"]),
                     "hide #%s & ~/%s target acs" % (prep["other_id"], prep["chain"])] if prep.get("chain") else []),
                  "key #bdbdbd:0 gold:1 orange:3 #b2182b:6+ pos 0.30,0.075 size 0.40,0.04 fontSize 20",
                  '2dlabels create pellaeon_title text "C\u03b1 shift after fit (\u00c5); lavender = not compared" xpos 0.30 ypos 0.165 size 20 color black',
                  'view %s' % prep['other_spec'], 'zoom 0.85']
    return {
        "pairing": basis, "paired_residues": len(rows), "coverage": "%d of %d residues of %s paired" % (len(rows), total_other, prep["other_spec"]),
        "mean_displacement": round(float(arr.mean()), 2), "max_displacement": round(float(arr.max()), 2),
        "rms_displacement": round(float((arr ** 2).mean() ** 0.5), 2), "not_compared": int(total_other - len(rows)),
        "ref_spec": prep["ref_spec"], "other_spec": prep["other_spec"],
        "residues_over_2A": int((arr > 2.0).sum()),
        "top_shifted": [{"chain": r.chain_id, "residue": num(r), "name": r.name, "displacement": round(d, 2),
                         "spec": _res_key(r, True), "reference_residue": _res_key(ref, False)}
                        for r, d, ref in top],
        "moving_regions": [{"chain": g[0], "range": span(g[1], g[2]), "max": round(g[3], 2),
                            "spec": "#%s/%s:%s" % (prep["other_id"], g[0], span(g[1], g[2]))} for g in regions[:15]],
        "coloring": "%s shown alone, colored by C-alpha displacement after the fit (gray < 1 A, gold, orange, dark red 6 A or more; lavender = not compared); reference %s hidden, can be shown as a pale ghost" % (prep["other_spec"], prep["ref_spec"]),
        "color_commands": color_cmds,
    }


def _find_structures(session, spec: str):
    """The structures a model spec names and the chains it names: '#1' (with an ensemble's members when #1
    groups them), '#1.2', '#1/A', '#1/A,C', a model name. -> (structures, chain ids or None)."""
    from chimerax.atomic import AtomicStructure
    text = (spec or "").strip()
    chains = None
    m = re.match(r"^(.*?)\s*/\s*([A-Za-z0-9]+(?:\s*,\s*[A-Za-z0-9]+)*)\s*$", text)
    if m and not re.search(r"[:@]", text):
        text, chains = m.group(1).strip(), [c.strip() for c in m.group(2).split(",")]
    st = _find_structure(session, text)
    if st is not None:
        return [st], chains
    ident = re.match(r"^#?(\d+(?:\.\d+)*)$", text)
    if ident:
        members = [x for x in session.models.list() if isinstance(x, AtomicStructure)
                   and x.id_string.startswith(ident.group(1) + ".")]
        return sorted(members, key=lambda x: x.id), chains
    return [], chains


class _Seq:
    """The two methods NeedlemanWunsch.nw asks of a sequence, for a plain string."""

    def __init__(self, characters: str):
        self.characters = characters

    def __len__(self):
        return len(self.characters)

    def ss_type(self, i):
        return None


def _align_chain(session, chain, sequence: str):
    """Chain sequence (SEQRES where the file has it) aligned to `sequence` with Needleman-Wunsch and
    BLOSUM-62, as matchmaker does. -> ([(chain index, sequence index)], identity over aligned pairs)."""
    from chimerax.alignment_algs import NeedlemanWunsch
    from chimerax import sim_matrices
    matrix = sim_matrices.matrix("BLOSUM-62", session.logger)
    score, pairs = NeedlemanWunsch.nw(chain, _Seq(sequence), score_gap=-1, score_gap_open=-10,
                                      similarity_matrix=matrix, ss_fraction=None)
    chars = chain.characters
    same = sum(1 for i, j in pairs if chars[i] == sequence[j])
    return sorted(pairs), (same / float(len(pairs)) if pairs else 0.0)


# A file's UniProt reference whose offset puts fewer matching residues than this on the current UniProt
# sequence is out of date (the entry's sequence was renumbered), and the chain is aligned instead.
REFERENCE_AGREEMENT = 0.8


def _one_letter(name: str) -> str:
    from chimerax.atomic import Sequence
    try:
        c = Sequence.rname3to1(name)
    except Exception:  # noqa: BLE001
        return ""
    return c if c and c != "X" else ""




def map_positions(session, model: str, accession: str, positions: List[int], chain: Optional[str] = None,
                  sequence: Optional[str] = None) -> Dict[str, Any]:
    """Read-only: map UniProt positions of `accession` onto residues of `model`, every chain that is that protein.

    Each position maps to a list of targets {model, chain, number, icode, resname, status, basis}. The status says
    how: 'exact' or 'offset' from the file's UniProt reference (struct_ref) when its numbering is a constant
    offset, 'alignment' when the chain was aligned to `sequence` (the UniProt sequence): the reference spans a
    numbering with gaps or insertion codes, or the file has no reference for this accession. Without `sequence`
    those chains are left out and the result says needs_sequence. Chains the file assigns to another UniProt
    entry are not targets unless named (`chain`, or a chain in the model spec)."""
    from chimerax.atomic import Residue
    from .core.resid import ALIGN_IDENTITY, ALIGN_IDENTITY_CHOSEN
    structs, spec_chains = _find_structures(session, model)
    if not structs:
        return {"error": _no_structure(session, model)}
    named = [chain] if chain else spec_chains
    acc = (accession or "").upper()
    want = sorted({int(p) for p in positions})
    out_map: Dict[int, List[Dict[str, Any]]] = {}
    chain_status: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    needs_sequence = False
    others: List[str] = []
    ins_numbers: Dict[str, List[int]] = {}
    seen_align: Dict[Tuple[str, str], Any] = {}

    def add(pos, st, r, status, basis):
        out_map.setdefault(pos, []).append({"model": "#" + st.id_string, "chain": r.chain_id, "number": int(r.number),
                                            "icode": (r.insertion_code or "").upper(), "resname": r.name,
                                            "status": status, "basis": basis})

    for st in structs:
        try:
            from chimerax.atomic import uniprot_ids
            entries = uniprot_ids(st)
        except Exception:  # noqa: BLE001
            entries = []
        af_named = "alphafold" in (st.name or "").lower() and acc and acc in (st.name or "").upper()
        for c in st.chains:
            if named and c.chain_id not in named:
                continue
            if not named and c.polymer_type != Residue.PT_AMINO:
                continue
            existing = [r for r in c.residues if r is not None]
            icodes = sorted({int(r.number) for r in existing if r.insertion_code})
            if icodes:
                ins_numbers["#%s/%s" % (st.id_string, c.chain_id)] = icodes
            segs = [e for e in entries if e.chain_id == c.chain_id and (getattr(e, "uniprot_id", "") or "").upper() == acc
                    and e.database_sequence_range and e.chain_sequence_range]
            other = sorted({(getattr(e, "uniprot_id", "") or "?") for e in entries if e.chain_id == c.chain_id
                            and (getattr(e, "uniprot_id", "") or "").upper() != acc})
            by_num = {(int(r.number), (r.insertion_code or "")): r for r in existing}
            label = "#%s/%s" % (st.id_string, c.chain_id)
            constant = []
            agree = total = 0
            for e in segs:
                (ds, de), (cs, ce) = e.database_sequence_range, e.chain_sequence_range
                gapless = (ce - cs) == (de - ds) and not any(cs <= int(r.number) <= ce and r.insertion_code for r in existing)
                constant.append(gapless)
                if gapless and sequence:   # does the file's reference still match the UniProt sequence?
                    for r in existing:
                        p = int(r.number) - (cs - ds)
                        if cs <= int(r.number) <= ce and 1 <= p <= len(sequence) and _one_letter(r.name):
                            total += 1
                            agree += _one_letter(r.name) == sequence[p - 1]
            stale = bool(total) and agree < REFERENCE_AGREEMENT * total
            if segs and all(constant) and not stale:
                offs = []
                for e in segs:
                    (ds, de), (cs, ce) = e.database_sequence_range, e.chain_sequence_range
                    off = cs - ds
                    offs.append(off)
                    basis = "UniProt %d-%d = %s %d-%d in the file" % (ds, de, c.chain_id, cs, ce)
                    for pos in want:
                        if ds <= pos <= de:
                            r = by_num.get((pos + off, ""))
                            if r is not None:
                                add(pos, st, r, "exact" if off == 0 else "offset", basis)
                chain_status.append({"chain": label, "status": "exact" if set(offs) == {0} else "offset",
                                     "offset": offs[0] if len(set(offs)) == 1 else None})
                continue
            if not segs and af_named and not other:
                for pos in want:
                    r = by_num.get((pos, ""))
                    if r is not None:
                        add(pos, st, r, "exact", "AlphaFold model of %s (UniProt numbering)" % acc)
                chain_status.append({"chain": label, "status": "exact"})
                continue
            if not segs and other and not named:
                others.extend(o for o in other if o not in others)
                continue   # the file says this chain is another protein
            if not sequence:
                needs_sequence = True
                chain_status.append({"chain": label, "status": "unresolved", "reason": "needs_sequence"})
                continue
            key = (c.characters, sequence)
            if key not in seen_align:
                seen_align[key] = _align_chain(session, c, sequence)
            pairs, ident = seen_align[key]
            limit = ALIGN_IDENTITY_CHOSEN if named else ALIGN_IDENTITY
            if not pairs or ident < limit:
                candidates.append({"chain": label, "identity": round(100 * ident, 1), "aligned": len(pairs)})
                continue
            basis = "chain aligned to UniProt %s, %d%% identity over %d residues" % (acc, round(100 * ident), len(pairs))
            if segs and stale:
                basis += " (the file's UniProt reference does not match the current UniProt sequence)"
            elif segs:
                basis += " (the file's reference spans a numbering with gaps or insertion codes)"
            wanted = set(want)
            for i, j in pairs:
                r = c.residues[i]
                if r is not None and (j + 1) in wanted:
                    add(j + 1, st, r, "alignment", basis)
            chain_status.append({"chain": label, "status": "alignment", "identity": round(100 * ident, 1)})
    mapped_chains = sorted({t["chain"] for ts in out_map.values() for t in ts})
    offsets = {}
    for cs in chain_status:
        if cs.get("offset") is not None:
            offsets[cs["chain"].split("/")[-1]] = cs["offset"]
    spec_model = "#" + structs[0].id_string if len(structs) == 1 else "#" + structs[0].id_string.rsplit(".", 1)[0]
    out = {"model": spec_model, "accession": acc, "map": out_map, "chains": mapped_chains, "offsets": offsets,
           "unmapped": [p for p in want if p not in out_map], "note": "", "chain_status": chain_status,
           "insertion_numbers": ins_numbers}
    if needs_sequence:
        out["needs_sequence"] = True
    if candidates:
        out["candidates"] = candidates
    matched = any(x.get("status") in ("exact", "offset", "alignment") for x in chain_status)
    if not matched and not needs_sequence:
        have = sorted({"#%s/%s" % (st.id_string, c.chain_id) for st in structs for c in st.chains})
        if candidates:
            why = "no chain of %s is UniProt %s: %s" % (model, acc, "; ".join(
                "%s %s%% identical" % (x["chain"], x["identity"]) for x in candidates))
        elif others:
            why = "%s has UniProt references for %s but not for %s" % (model, ", ".join(others), acc)
        else:
            why = "no chain of %s could be matched to UniProt %s" % (model, acc)
        out.update(error="Model %s: %s." % (model, why), unresolved=True, chains_available=have)
    return out


def chain_uniprot(session, model: str) -> Dict[str, Any]:
    """Read-only: the UniProt accessions the structure file associates with `model`'s chains."""
    found, _chains = _find_structures(session, model)
    if not found:
        return {"error": _no_structure(session, model), "accessions": []}
    m = found[0]
    accs: List[str] = []
    try:
        from chimerax.atomic import uniprot_ids
        for e in uniprot_ids(m):
            uid = (getattr(e, "uniprot_id", "") or "").upper()
            if uid and uid not in accs:
                accs.append(uid)
    except Exception:  # noqa: BLE001
        pass
    if not accs and "alphafold" in (m.name or "").lower():
        import re as _re
        hit = _re.search(r"[A-NR-Z][0-9][A-Z0-9]{3}[0-9]|[OPQ][0-9][A-Z0-9]{3}[0-9]", m.name.upper())
        if hit:
            accs.append(hit.group(0))
    return {"model": "#" + m.id_string, "accessions": accs}


def label_layout(session) -> Dict[str, Any]:
    """Read-only: every 3D label with its screen box (pixels, lower-left origin) and the scene size of one pixel at its depth."""
    import math
    import numpy as np
    from chimerax.label.label3d import ObjectLabels
    view = session.main_view
    cam = view.camera
    w, h = view.window_size
    inv = cam.position.inverse()
    persp = hasattr(cam, "field_of_view")
    if persp:
        half = math.tan(math.radians(cam.field_of_view) / 2.0)
    try:
        from Qt.QtGui import QGuiApplication
        px_per_pt = QGuiApplication.primaryScreen().logicalDotsPerInch() / 72.0   # label sizes are in points
    except Exception:  # noqa: BLE001
        px_per_pt = 96.0 / 72.0
    items = []
    for lm in session.models.list(type=ObjectLabels):
        parent = lm.parent
        for lab in lm.labels():
            obj = getattr(lab, "object", None)
            if obj is None or getattr(obj, "deleted", False) or not lab.visible():
                continue
            loc = lab.location()
            if loc is None:
                continue
            xyz = parent.scene_position * np.array(loc, dtype=float) if parent is not None else np.array(loc, dtype=float)
            p = inv * xyz
            if persp:
                z = -p[2]
                if z <= 0:
                    continue
                per_px = 2.0 * z * half / w          # scene units per pixel at this depth
            else:
                per_px = cam.field_width / w
            off = [float(v) for v in lab.offset]
            x = w / 2.0 + (p[0] + off[0]) / per_px
            y = h / 2.0 + (p[1] + off[1]) / per_px
            text = lab.text
            size = float(getattr(lab, "size", 16) or 16)
            spec = None
            from chimerax.atomic import Residue, Atom
            if isinstance(obj, Residue):
                spec = "#%s/%s:%d%s" % (obj.structure.id_string, obj.chain_id, obj.number, obj.insertion_code)
                level = "residues"
            elif isinstance(obj, Atom):
                r = obj.residue
                spec = "#%s/%s:%d%s@%s" % (r.structure.id_string, r.chain_id, r.number, r.insertion_code, obj.name)
                level = "atoms"
            else:
                continue
            px = size * px_per_pt
            items.append({"spec": spec, "level": level, "text": text, "x": x, "y": y, "w": 0.62 * px * max(1, len(text)),
                          "h": 1.3 * px, "per_px": per_px, "offset": off})
    return {"labels": items, "window": [w, h]}


def spec_atoms(session, text: str) -> Dict[str, Any]:
    """Read-only: parse the leading atom spec of `text` (the part of a command after its name) and count what it matches.
    Returns {'used': spec text, 'atoms': n} or {'used': ''} when the text does not start with a spec (= everything)."""
    from chimerax.core.commands import atomspec
    try:
        spec, used, rest = atomspec.AtomSpecArg.parse(text, session)
    except Exception:  # noqa: BLE001
        return {"used": ""}
    if not used or not used.strip():
        return {"used": ""}
    try:
        results = spec.evaluate(session)
        return {"used": used.strip(), "atoms": len(results.atoms), "residues": len(results.atoms.unique_residues),
                "models": len(results.models), "shown": int(results.atoms.displays.sum()) if len(results.atoms) else 0}
    except Exception as e:  # noqa: BLE001
        return {"used": used.strip(), "error": str(e)}


_COLOR_WORDS = ("color", "colour", "rainbow", "coulombic", "mlp")


def residue_provenance(session, res_spec: str, journal: List[Dict[str, Any]], with_environment: bool = False) -> Dict[str, Any]:
    """Read-only: what a residue looks like now and which recorded commands / overlays gave it that look."""
    import numpy as np
    from chimerax.core.commands import atomspec
    try:
        spec, used, rest = atomspec.AtomSpecArg.parse(res_spec, session)
        res = spec.evaluate(session).atoms.unique_residues
    except Exception as e:  # noqa: BLE001
        return {"error": "Could not read '%s': %s" % (res_spec, e)}
    if len(res) == 0:
        return {"error": "No residue matches '%s'." % res_spec}
    r = res[0]
    m = r.structure
    out: Dict[str, Any] = {"residue": "%s %s" % (r.name, _res_key(r, False).split(":", 1)[1]), "chain": r.chain_id,
                           "model": "#" + m.id_string, "model_name": m.name, "spec": _res_key(r, True)}
    def hexc(c):
        return "#%02x%02x%02x" % tuple(int(v) for v in c[:3])
    out["ribbon_color"] = hexc(r.ribbon_color) if r.ribbon_display else None
    shown = [a for a in r.atoms if a.display]
    if shown:
        cols = {}
        for a in shown:
            cols[hexc(a.color)] = cols.get(hexc(a.color), 0) + 1
        out["atom_colors"] = sorted(cols.items(), key=lambda kv: -kv[1])[:3]
        out["atoms_shown"] = len(shown)
    else:
        out["atoms_shown"] = 0
    out["selected"] = bool(r.atoms.selecteds.any())
    # Pellaeon attributes (table overlays, displacement)
    attrs = {}
    for name in dir(r):
        if name.startswith("pellaeon_"):
            try:
                v = getattr(r, name)
            except Exception:  # noqa: BLE001
                continue
            if v is not None:
                attrs[name] = float(v) if isinstance(v, (int, float)) else str(v)
    out["attributes"] = attrs
    # labels
    try:
        from chimerax.label.label3d import ObjectLabels
        labels = []
        for lm in session.models.list(type=ObjectLabels):
            for lab in lm.labels():
                obj = getattr(lab, "object", None)
                if obj is r or getattr(obj, "residue", None) is r:
                    labels.append(lab.text)
        out["labels"] = labels
    except Exception:  # noqa: BLE001
        out["labels"] = []
    # recorded commands that touched this residue, newest first
    hits = []
    n_total = len(journal)
    for idx in range(n_total - 1, -1, -1):
        j = journal[idx]
        if not j.get("ok"):
            continue
        cmd = str(j.get("command", ""))
        word = cmd.split()[0].lower() if cmd.split() else ""
        if word not in _COLOR_WORDS + ("label", "style", "show", "hide", "cartoon", "transparency", "surface", "select", "delete"):
            continue
        rest_text = cmd[len(word):].strip()
        try:
            sp, used, _ = atomspec.AtomSpecArg.parse(rest_text, session)
            touched = used.strip() != ""
        except Exception:  # noqa: BLE001
            sp, used, touched = None, "", False
        if touched:
            try:
                if r not in sp.evaluate(session).atoms.unique_residues:
                    continue
            except Exception:  # noqa: BLE001
                continue
        elif word == "select":
            continue    # `select` without a spec is not about this residue
        # a spec-less command applies to everything (e.g. `color red`, `color bychain`)
        hits.append({"command": cmd, "origin": j.get("origin", "model"), "kind": "color" if word in _COLOR_WORDS else word,
                     "commands_ago": n_total - idx, "everything": not touched})
        if len(hits) >= 8:
            break
    out["history"] = hits
    out["last_color_command"] = next((h for h in hits if h["kind"] == "color"), None)
    if with_environment:
        try:   # what surrounds it, measured (residue_env.py)
            from .residue_env import environment
            out["environment"] = environment(session, r)
        except Exception as e:  # noqa: BLE001
            out["environment"] = {"error": str(e)[:120]}
    return out


def figure_info(session) -> Dict[str, Any]:
    """Read-only: what a figure bundle needs to record: models and where they came from, the chains shown (with
    atoms, a ribbon or a displayed molecular surface), camera, background, labels."""
    import re as _re
    from chimerax.atomic import AtomicStructure, MolecularSurface
    models = []
    for m in session.models.list(type=AtomicStructure):
        name = m.name or ""
        src = ""
        if _re.fullmatch(r"[0-9][A-Za-z0-9]{3}", name):
            src = "PDB %s" % name.upper()
        elif "alphafold" in name.lower() or name.upper().startswith("AF-"):
            acc = _re.search(r"(?:AF-)?([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9][A-Z][A-Z0-9]{2}[0-9])", name.upper())
            src = "AlphaFold DB %s" % (acc.group(1) if acc else name)
        fn = getattr(m, "filename", None)
        if fn and not src:                      # a local file; fetched entries keep their database id only
            src = "file " + str(fn)
        surfaced = set()        # chains with a displayed molecular surface: shown, though every atom and ribbon may be hidden
        try:
            for sm in m.child_models():
                if isinstance(sm, MolecularSurface) and sm.display and sm.atoms is not None:
                    surfaced.update(str(x) for x in sm.atoms.residues.unique_chain_ids)
        except Exception:  # noqa: BLE001
            pass
        shown_chains = []
        for c in m.chains:
            try:
                res = c.existing_residues
                if res.ribbon_displays.any() or res.atoms.displays.any() or c.chain_id in surfaced:
                    shown_chains.append(c.chain_id)
            except Exception:  # noqa: BLE001
                shown_chains.append(c.chain_id)
        models.append({"id": "#" + m.id_string, "name": name, "source": src or name, "chains": [c.chain_id for c in m.chains],
                       "chains_shown": shown_chains, "residues": int(m.num_residues), "atoms": int(m.num_atoms), "shown": bool(m.display)})
    v = session.main_view
    cam = v.camera
    bg = v.background_color
    n_labels = 0
    try:
        from chimerax.label.label3d import ObjectLabels
        n_labels = sum(len(lm.labels()) for lm in session.models.list(type=ObjectLabels))
    except Exception:  # noqa: BLE001
        pass
    try:
        from chimerax.core import version as cxversion
    except Exception:  # noqa: BLE001
        cxversion = ""
    return {"models": models, "background": "#%02x%02x%02x" % tuple(int(255 * c) for c in bg[:3]),
            "camera": {"type": cam.name, "position": [list(map(float, row)) for row in cam.position.matrix],
                       "field_of_view": float(getattr(cam, "field_of_view", 0) or 0)},
            "window": list(v.window_size), "labels": n_labels, "chimerax_version": str(cxversion)}


def residue_colors(session) -> List[List[Any]]:
    """Read-only: per-residue ribbon color (and majority atom color of displayed atoms) for every open structure."""
    from chimerax.atomic import AtomicStructure
    rows = []
    for m in session.models.list(type=AtomicStructure):
        for r in m.residues:
            rib = "#%02x%02x%02x" % tuple(int(c) for c in r.ribbon_color[:3]) if r.ribbon_display else ""
            shown = [a for a in r.atoms if a.display]
            atom = ""
            if shown:
                counts: Dict[str, int] = {}
                for a in shown:
                    h = "#%02x%02x%02x" % tuple(int(c) for c in a.color[:3]); counts[h] = counts.get(h, 0) + 1
                atom = max(counts.items(), key=lambda kv: kv[1])[0]
            num = int(r.number) if not r.insertion_code else _res_key(r, False).split(":", 1)[1]   # 82, 82A
            rows.append(["#" + m.id_string, r.chain_id, num, r.name, rib, atom])
    return rows


def count_residues(session, spec: str) -> int:
    """Read-only: how many residues an atom spec matches (0 when it does not parse)."""
    from chimerax.core.commands import atomspec
    try:
        results = atomspec.AtomSpecArg.parse(spec, session)[0].evaluate(session)
        return len(results.atoms.unique_residues)
    except Exception:  # noqa: BLE001
        return 0


def _res_key(r, with_model: bool) -> str:
    """'A:82A', or '#1.2/A:82A' when several structures are involved (core/resid.py spells it the same way)."""
    num = "%d%s" % (int(r.number), (r.insertion_code or "").upper())
    return ("#%s/%s:%s" % (r.structure.id_string, r.chain_id, num)) if with_model else ("%s:%s" % (r.chain_id, num))


def list_residues(session, model: str) -> Dict[str, Any]:
    """Read-only: {'model': '#1', 'residues': {'A:10': 'ALA', 'B:82A': 'SER', ...}} for polymer residues with a CA
    atom; an ensemble ('#1' grouping #1.1-#1.10) keys them '#1.1/A:10'. chain_groups: chain id -> what molecule it
    is (its UniProt entry from the file, else its sequence), so chains of one protein can be told from others."""
    structs, _chains = _find_structures(session, model)
    if not structs:
        return {"error": _no_structure(session, model)}
    several = len(structs) > 1
    out = {}
    groups: Dict[str, str] = {}
    for m in structs:
        for r in m.residues:
            if r.polymer_type != 0 or r.find_atom("CA") is not None:
                out[_res_key(r, several)] = r.name
    try:
        from chimerax.atomic import uniprot_ids
        refs: Dict[str, List[str]] = {}
        for e in uniprot_ids(structs[0]):
            refs.setdefault(e.chain_id, []).append((getattr(e, "uniprot_id", "") or "").upper())
    except Exception:  # noqa: BLE001
        refs = {}
    for c in structs[0].chains:
        groups[c.chain_id] = ("uniprot:" + ",".join(sorted(set(refs[c.chain_id])))) if refs.get(c.chain_id) else ("sequence:" + c.characters)
    spec_model = "#" + structs[0].id_string if not several else "#" + structs[0].id_string.rsplit(".", 1)[0]
    res = {"model": spec_model, "residues": out, "chain_groups": groups}
    if several:
        res["models"] = ["#" + m.id_string for m in structs]
    return res


def set_residue_attr(session, model: str, attr: str, values: Dict[str, Any]) -> Dict[str, Any]:
    """Store per-residue numbers as a registered residue attribute (so `color byattribute r:<attr>` and `key` can use it).
    values: {'A:82A': 1.5} for one structure, {'#1.2/A:10': 0.3} for the members of an ensemble."""
    import re as _re
    from chimerax.atomic import Residue
    if not _re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,60}$", attr or ""):
        return {"error": "Bad attribute name %r" % attr}
    structs, _chains = _find_structures(session, model)
    if not structs:
        return {"error": _no_structure(session, model)}
    warning = None
    try:
        Residue.register_attr(session, attr, "Pellaeon", attr_type=float)
    except Exception as e:  # an earlier registration with another type: values still color, tests like ::attr>3 will not
        warning = "attribute %s could not be registered as a number (%s); 'select ::%s>value' will not work, coloring will" % (attr, e, attr)
        session.logger.warning("Pellaeon: " + warning)
    from .core.resid import ResId
    by_key = {("#" + m.id_string, r.chain_id, int(r.number), (r.insertion_code or "").upper()): r
              for m in structs for r in m.residues}
    only = "#" + structs[0].id_string if len(structs) == 1 else ""
    n = 0
    for k, v in values.items():
        rid = ResId.parse(k, only)
        r = by_key.get(tuple(rid)) if rid is not None else None
        if r is not None:
            try:
                setattr(r, attr, float(v)); n += 1
            except (TypeError, ValueError):
                pass
    out = {"set": n, "attr": attr}
    if warning:
        out["warning"] = warning
    return out


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
                on_pick({"spec": "#%s/%s" % (res.structure.id_string, _res_key(res, False)),
                         "name": res.name, "number": int(res.number), "insertion_code": (res.insertion_code or "").upper(),
                         "chain": res.chain_id,
                         "model": res.structure.name, "atom": atom.name if atom is not None else ""})
        return _Mode(session)


# --------------------------------------------------------------------------------------
# Contact comparison (added by the contact-comparison module; append-only).
# Collector half of src/core/contacts.py: everything below reads the session and returns
# plain data, so the comparison itself stays ChimeraX-free and unit-testable.
# --------------------------------------------------------------------------------------

_SOLVENT_NAMES = {"HOH", "WAT", "DOD", "H2O"}


def _res_spec(r) -> str:
    """'/A:45', or '/A:45B' when the residue carries an insertion code.

    The pairing, the collected contacts and the drawing commands all name residues with this
    one function: an insertion code dropped on one side and kept on the other turns a paired
    residue into an unpaired one, and two insertion variants into a single overwritten entry.
    """
    return "/%s:%d%s" % (r.chain_id, int(r.number), (r.insertion_code or "").strip())


def residue_pairing(session, prep: Dict[str, Any], matchmaker_returns=None) -> Dict[str, Any]:
    """Reference residue spec -> compared model's residue spec, from matchmaker's own residue
    correspondence (the same pairs ``compute_displacement`` uses), falling back to chain+number.

    Contacts must be compared through this map, never by residue number: the two entries may be
    numbered differently, and then a number-based comparison compares unrelated residues.
    """
    a = _find_structure(session, "#" + prep["ref_id"])
    b = _find_structure(session, "#" + prep["other_id"])
    if a is None or b is None:
        return {"error": "Models changed during the comparison."}
    pairing: Dict[str, str] = {}
    basis = "matchmaker alignment"
    for rv in (matchmaker_returns or []):
        if not isinstance(rv, dict):
            continue
        ra, ma = rv.get("full ref atoms"), rv.get("full match atoms")
        if ra is None or ma is None:
            continue
        for x, y in zip(ra, ma):
            if x.structure is a and y.structure is b:
                pairing[_res_spec(x.residue)] = _res_spec(y.residue)
    if not pairing:
        basis = "chain id + residue number (no alignment available)"
        chain = prep.get("chain")
        have = {_res_spec(r) for r in b.residues}
        for r in a.residues:
            if chain and r.chain_id != chain:
                continue
            spec = _res_spec(r)
            if spec in have:
                pairing[spec] = spec
    if not pairing:
        return {"error": "No residues could be paired between %s and %s." % (prep["ref_spec"], prep["other_spec"])}
    return {"pairing": pairing, "basis": basis, "paired_residues": len(pairing)}


def _empty_selection(model, model_spec: str, cutoff: float, restrict: str) -> Dict[str, Any]:
    """A valid restriction that matches nothing here is an *answer* (an apo form has no ligand),
    not a failure: it gets its own status so the caller never mistakes it for a collector error
    and never compares real contacts against a silently empty list."""
    return {"model": "#" + model.id_string, "cutoff": float(cutoff), "contacts": [],
            "restrict": restrict or "", "restrict_residues": [], "count": 0,
            "empty_selection": True,
            "reason": "The restrict spec '%s' matches nothing in %s." % (restrict, model_spec)}


def residue_contacts(session, model_spec: str, cutoff: float = 4.0,
                     restrict: Optional[str] = None) -> Dict[str, Any]:
    """Read-only: every residue-residue contact inside one model, as plain dicts.

    A contact is a pair of residues with at least one pair of heavy atoms within `cutoff` A.
    The reported ``min_dist`` and atom names are that closest pair, which is what a picture of
    the interaction should be drawn between, while ``kind`` is the strongest interaction any
    atom pair of the two residues supports - the closest pair is usually carbon, so classifying
    it alone would miss the salt bridge two atoms further along. Hydrogens are ignored (most
    crystal structures have none, so including them would make two otherwise identical
    structures differ), solvent is ignored, and intra-residue and sequential-backbone pairs are
    dropped because they are present in every structure and say nothing about a conformational
    change.

    A chain in `model_spec` ('#1/A') limits the collection to that chain, which is what the
    comparison wants: matchmaker superposes one chain pair, so contacts of the other chains
    could never be paired anyway. Atoms matched by `restrict` are kept whatever their chain,
    because a ligand usually sits in a chain of its own.

    `restrict` is an atom spec (e.g. a ligand, '#1:AP5'): only contacts with at least one
    *selected atom* within the cutoff are returned - measured from the selected atoms, so
    '#1/A:87@CA' really means CA, not "anything in residue 87". A pair whose two residues are
    both selected is reported once; the restricted side is reported as ``a`` whenever only one
    side is selected.

    Returns {'model': '#1', 'contacts': [{a, a_name, a_atom, b, b_name, b_atom, min_dist, kind}, ...],
    'restrict_residues': ['/A:215', ...]} - the residues the restriction selected, which is what
    the caller translates through the alignment to address the same site in the other model.
    A valid restriction matching nothing returns 'empty_selection': True instead of 'error'.
    """
    import numpy as np
    from chimerax.geometry import find_close_points
    from .core.contacts import CONTACT, HBOND_ELEMENTS, classify_contact, strongest_kind

    m = _find_structure(session, model_spec)
    if m is None:
        return {"error": _no_structure(session, model_spec)}
    atoms = m.atoms

    wanted = set()
    if restrict:
        from chimerax.core.commands import atomspec
        try:
            sel = atomspec.AtomSpecArg.parse(restrict, session)[0].evaluate(session).atoms
        except Exception as e:  # noqa: BLE001
            return {"error": "Could not read restrict spec '%s': %s" % (restrict, e)}
        wanted = set(int(p) for p in sel.pointers)   # C++ pointers identify atoms across collections
        if not wanted:
            return _empty_selection(m, model_spec, cutoff, restrict)

    chain_m = re.search(r"/([A-Za-z0-9]+)", model_spec or "")
    chain = chain_m.group(1) if chain_m else None
    ptrs = [int(p) for p in atoms.pointers]
    keep = [i for i, a in enumerate(atoms)
            if a.element.name != "H" and a.residue.name.upper() not in _SOLVENT_NAMES
            and (chain is None or a.residue.chain_id == chain or ptrs[i] in wanted)]
    if not keep:
        return {"error": "Model %s has no non-solvent heavy atoms." % model_spec}
    atoms = atoms[np.array(keep, dtype=np.int32)]   # an Atoms collection only indexes with an array
    coords = atoms.scene_coords
    residues = [a.residue for a in atoms]
    names = [a.name for a in atoms]
    elements = [a.element.name for a in atoms]
    # Only N/O/S can carry a hydrogen bond or a formal charge, so a pair of carbons is a plain
    # contact whatever its distance; skipping those keeps the all-atom-pairs pass cheap.
    polar = [e.upper() in HBOND_ELEMENTS for e in elements]

    restrict_set = None
    if restrict:
        restrict_set = {i for i, p in enumerate(atoms.pointers) if int(p) in wanted}
        if not restrict_set:
            return _empty_selection(m, model_spec, cutoff, restrict)

    by_res: Dict[Any, List[int]] = {}
    for i, r in enumerate(residues):
        by_res.setdefault(r, []).append(i)
    restricted_res = set()
    if restrict_set is not None:
        for i in restrict_set:
            restricted_res.add(residues[i])
    seq_index = _sequence_index(by_res.keys())

    # Which residue pairs are candidates. With a restriction only selected atoms seed the
    # search, so "at least one selected atom within the cutoff" is measured where it is claimed.
    pairs = []
    seen = set()
    for r, idxs in by_res.items():
        seeds = idxs if restrict_set is None else [i for i in idxs if i in restrict_set]
        if not seeds:
            continue
        near = find_close_points(coords[np.array(seeds, dtype=np.int32)], coords, float(cutoff))[1]
        for j in near:
            other = residues[int(j)]
            if other is r or _sequential(r, other, seq_index):
                continue
            if restrict_set is not None and other not in restricted_res:
                key = (r, other)            # only one side is selected: it is reported as `a`
            else:
                key = _ordered(r, other)    # both selected (or no restriction): score it once
            if key in seen:
                continue
            seen.add(key)
            pairs.append(key)

    out: List[Dict[str, Any]] = []
    for ra, rb in pairs:
        ia, ib = by_res[ra], by_res[rb]
        ca = coords[np.array(ia, dtype=np.int32)]
        cb = coords[np.array(ib, dtype=np.int32)]
        d = np.linalg.norm(ca[:, None, :] - cb[None, :, :], axis=2)
        close = d <= float(cutoff)
        if restrict_set is not None:
            sel_a = np.array([i in restrict_set for i in ia], dtype=bool)
            sel_b = np.array([i in restrict_set for i in ib], dtype=bool)
            close = close & (sel_a[:, None] | sel_b[None, :])
        if not close.any():
            continue
        kinds = {CONTACT}
        for p, q in zip(*np.nonzero(close)):
            ai, bi = ia[int(p)], ib[int(q)]
            if not (polar[ai] and polar[bi]):
                continue
            kinds.add(classify_contact(ra.name, names[ai], rb.name, names[bi], float(d[p, q]),
                                       a_element=elements[ai], b_element=elements[bi],
                                       a_standard=ra.polymer_type != 0, b_standard=rb.polymer_type != 0))
        # the closest qualifying atom pair is what a picture is drawn between
        p, q = np.unravel_index(int(np.where(close, d, np.inf).argmin()), d.shape)
        ai, bi = ia[int(p)], ib[int(q)]
        out.append({"a": _res_spec(ra), "a_name": ra.name, "a_atom": names[ai],
                    "b": _res_spec(rb), "b_name": rb.name, "b_atom": names[bi],
                    "min_dist": round(float(d[p, q]), 2), "kind": strongest_kind(sorted(kinds))})
    out.sort(key=lambda c: (c["a"].split(":")[0], int(re.sub(r"\D", "", c["a"].split(":")[1]) or 0),
                            c["b"].split(":")[0], int(re.sub(r"\D", "", c["b"].split(":")[1]) or 0)))
    return {"model": "#" + m.id_string, "cutoff": float(cutoff), "contacts": out,
            "restrict": restrict or "", "count": len(out),
            "restrict_residues": sorted({_res_spec(r) for r in restricted_res})}


def _sequence_index(residues) -> Dict[Any, Any]:
    """Residue -> (chain id, position in that chain's sequence), for the polymer residues.

    Adjacency has to come from the polymer itself, not from residue numbers: two entries of the
    same protein are routinely numbered differently, and then "45 and 46 are neighbours" is true
    in one structure and false in the other, so the same backbone pair is collected on one side
    only and reported as a lost contact.
    """
    index: Dict[Any, Any] = {}
    seen = set()
    for r in residues:
        ch = getattr(r, "chain", None)
        if ch is None or ch.chain_id in seen:
            continue
        seen.add(ch.chain_id)
        for pos, res in enumerate(ch.residues):
            if res is not None:
                index[res] = (ch.chain_id, pos)
    return index


def _sequential(r1, r2, seq_index: Dict[Any, Any]) -> bool:
    """True for two polymer residues adjacent in their chain's sequence: their backbone touches
    in every structure, so the pair carries no information about a conformational change."""
    a = seq_index.get(r1)
    b = seq_index.get(r2)
    return a is not None and b is not None and a[0] == b[0] and abs(a[1] - b[1]) == 1


def _ordered(r1, r2):
    """Stable order for an undirected residue pair, so each contact is stored once."""
    k1 = (r1.chain_id, int(r1.number), r1.insertion_code or "")
    k2 = (r2.chain_id, int(r2.number), r2.insertion_code or "")
    return (r1, r2) if k1 <= k2 else (r2, r1)


def _identity_chains(session, spec: str, chain: Optional[str]):
    """The chains to compare: those a spec with a chain part names, else one per structure
    (the chain id asked for, or the longest protein chain)."""
    from chimerax.atomic import AtomicStructure
    spec = (spec or "").strip()
    if spec.startswith("["):   # "['#1', '#2']" from a small model
        spec = " ".join(re.findall(r"#[\w./:,-]+", spec))
    if spec.lower() in ("all", "*", "everything"):
        spec = ""
    spec = re.sub(r",\s*(?=[#/])", " ", spec)   # "#1,#2" parses as #1 and a leftover: one chain, "nothing to compare"
    if spec:
        from chimerax.core.commands import atomspec
        try:
            parsed, used, rest = atomspec.AtomSpecArg.parse(spec, session)
            res = parsed.evaluate(session)
        except Exception as e:  # noqa: BLE001
            return None, "Could not read %r as models: %s" % (spec, e)
        structs = [m for m in res.models if isinstance(m, AtomicStructure)]
        touched = {}
        for r in res.atoms.unique_residues:
            if r.chain is not None:
                touched.setdefault(r.structure, [])
                if r.chain not in touched[r.structure]:
                    touched[r.structure].append(r.chain)
    else:
        structs = [m for m in session.models.list() if isinstance(m, AtomicStructure)]
        touched = {}
    out = []
    for st in structs:
        chains = [c for c in st.chains if c.polymer_type == 1 or len(c) > 0]
        if chain:
            pick = [c for c in chains if c.chain_id == chain]
        elif "/" in spec and touched.get(st):
            pick = touched[st]
        else:
            protein = [c for c in chains if c.polymer_type == 1] or chains
            pick = sorted(protein, key=lambda c: -len(c))[:1]
        out.extend(pick)
    return out, None


def sequence_identity(session, spec: str = "", chain: Optional[str] = None) -> Dict[str, Any]:
    """Pairwise percent identity between chains, aligned with ChimeraX's own Needleman-Wunsch and
    BLOSUM-62 (as matchmaker does), without creating alignments or viewer windows."""
    from chimerax.alignment_algs import NeedlemanWunsch
    from chimerax import sim_matrices
    chains, err = _identity_chains(session, spec, chain)
    if err:
        return {"error": err}
    chains = [c for c in chains if len(c) >= 5]
    if len(chains) < 2:
        return {"error": "Need at least two chains to compare; found %d%s. Chains are written with a slash, one per spec: "
                         "'#1/A' and '#1/C' (not '#1:A,C' or '#1/A,C' in one argument)." % (
            len(chains), (" with chain id " + chain) if chain else "")}
    if len(chains) > 12:
        return {"error": "That is %d chains (%d pairs); name up to 12, e.g. '#1-4'." % (len(chains), len(chains) * (len(chains) - 1) // 2)}
    matrix = sim_matrices.matrix("BLOSUM-62", session.logger)
    label = lambda c: "#%s/%s" % (c.structure.id_string, c.chain_id)
    entries = [{"chain": label(c), "name": c.structure.name, "length": len(c)} for c in chains]
    pairs = []
    for i in range(len(chains)):
        for j in range(i + 1, len(chains)):
            a, b = chains[i], chains[j]
            score, matches = NeedlemanWunsch.nw(a, b, score_gap=-1, score_gap_open=-10,
                                                similarity_matrix=matrix, ss_fraction=None)
            sa, sb = a.characters, b.characters
            same = sum(1 for x, y in matches if sa[x] == sb[y])
            shorter = min(len(sa), len(sb))
            pairs.append({"a": label(a), "b": label(b), "identity_percent": round(100.0 * same / shorter, 1),
                          "identical": same, "aligned": len(matches), "shorter_length": shorter})
    ids = [e["chain"] for e in entries]
    cell = {(p["a"], p["b"]): p["identity_percent"] for p in pairs}
    rows = ["| | " + " | ".join(ids) + " |", "|---" * (len(ids) + 1) + "|"]
    for x in ids:
        vals = []
        for y in ids:
            v = 100.0 if x == y else cell.get((x, y), cell.get((y, x)))
            vals.append("%.1f" % v if v is not None else "")
        rows.append("| **%s** | %s |" % (x, " | ".join(vals)))
    table = "\n".join(rows)
    session.logger.info("Pellaeon: sequence identity (%%, identical residues / length of the shorter chain)\n%s\n%s" % (
        "\n".join("  %s  %s (%d residues)" % (e["chain"], e["name"], e["length"]) for e in entries),
        "\n".join("  %s vs %s: %.1f%% (%d of %d)" % (p["a"], p["b"], p["identity_percent"], p["identical"], p["shorter_length"])
                  for p in pairs)))
    return {"chains": entries, "pairs": pairs, "table": table,
            "note": "Identity = identical aligned residues / length of the shorter chain, full sequences (SEQRES where the "
                    "file has it). Show the user the table; no windows were opened."}


def close_windows(session, which: str = "sequence", opened=None) -> Dict[str, Any]:
    closed = []
    if which != "opened":
        mgr = getattr(session, "alignments", None)
        for aln in list(getattr(mgr, "alignments", []) or []):
            try:
                mgr.destroy_alignment(aln)
                closed.append("alignment " + str(getattr(aln, "ident", "") or ""))
            except Exception:  # noqa: BLE001
                pass
        for t in list(session.tools.list()):
            if t.tool_name == "Sequence Viewer":
                try:
                    t.delete()
                    closed.append("Sequence Viewer")
                except Exception:  # noqa: BLE001
                    pass
    else:
        for t in list(opened or []):
            if t in session.tools.list():
                try:
                    name = t.tool_name
                    t.delete()
                    closed.append(name)
                except Exception:  # noqa: BLE001
                    pass
    viewers = sum(1 for c in closed if c == "Sequence Viewer")
    return {"closed": len(closed), "sequence_viewers": viewers,
            "summary": ("Closed %d window(s)." % len(closed)) if closed else "No such windows were open."}


def fetch_to_cache(url: str, subdir: str = "downloads") -> str:
    """Download a URL into Pellaeon's cache and return the local path. `open <url>` in ChimeraX saves a copy of
    every file into ~/Downloads (with (1), (2)... suffixes on repeats); this keeps them out of the user's folders."""
    import hashlib
    from .bridge import pellaeon_dir
    from .core.http import request_bytes
    d = os.path.join(pellaeon_dir("cache"), subdir)
    os.makedirs(d, exist_ok=True)
    name = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "file"
    path = os.path.join(d, "%s_%s" % (hashlib.sha1(url.encode()).hexdigest()[:8], name))
    if not (os.path.isfile(path) and os.path.getsize(path) > 0):
        with open(path, "wb") as f:
            f.write(request_bytes("GET", url, timeout=90))
    return path


# The membrane view (OPM, Orientations of Proteins in Membranes) is put together in core/agent.py: the lookups
# run on the agent's thread and the commands go through execute_commands. These are its main-thread parts.
_ACCESSION_RE = re.compile(r"\b([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})\b")
MEMBRANE_PLANES = ("pellaeon_membrane_out", "pellaeon_membrane_in")


def membrane_target(session, model: str, pdb: Optional[str] = None) -> Dict[str, Any]:
    """The structure to orient, with what names it in the databases: the PDB id given or in its name, and the
    UniProt accession in its name (an AlphaFold model). `planes` are the membrane planes already drawn."""
    st = _find_structure(session, model)
    if st is None:
        return {"error": _no_structure(session, model)}
    ident = (pdb or "").strip().lower() or (re.match(r"^([0-9][a-z0-9]{3})\b", (st.name or "").lower()) or [None, None])[1]
    acc = _ACCESSION_RE.search(st.name or "")
    return {"model": "#" + st.id_string, "name": st.name, "pdb": ident or "", "accession": acc.group(1) if acc else "",
            "planes": ["#" + m.id_string for m in session.models.list() if m.name in MEMBRANE_PLANES]}


def membrane_frame(session, ref: str) -> Dict[str, Any]:
    """The membrane half-thickness from OPM's reference entry: its dummy atoms (DUM) mark the two boundary planes."""
    import numpy as np
    opm = _find_structure(session, ref)
    if opm is None:
        return {"error": "OPM returned no structure (%s)." % ref}
    dum = opm.atoms.filter(opm.atoms.residues.names == "DUM")
    return {"half": float(np.abs(dum.coords[:, 2]).mean()) if len(dum) else 15.0}


def membrane_geometry(session, model: str) -> Dict[str, Any]:
    """Where the model sits in OPM's frame once superposed: its centre and extent in the membrane plane, and
    whether the N-terminal region of an AlphaFold model has low confidence (then its placement means nothing)."""
    st = _find_structure(session, model)
    if st is None:
        return {"error": _no_structure(session, model)}
    xyz = st.atoms.scene_coords
    low = None
    try:
        b = st.residues.filter(st.residues.numbers <= 60).atoms
        if len(b) and st.name.lower().startswith(("alphafold", "af-")):
            low = float((b.bfactors < 50).mean())
    except Exception:  # noqa: BLE001
        low = None
    return {"cx": float(xyz[:, 0].mean()), "cy": float(xyz[:, 1].mean()),
            "extent": float(max(xyz[:, 0].max() - xyz[:, 0].min(), xyz[:, 1].max() - xyz[:, 1].min())),
            "low_plddt": bool(low and low > 0.5)}


_OUTSIDE = ("extracellular", "lumenal", "periplasmic", "exoplasmic", "vesicular", "mitochondrial intermembrane")


def membrane_from_topology(session, model: str, accession: str, slabs: bool, why: str, feats: Dict[str, Any]) -> Dict[str, Any]:
    """Membrane frame from UniProt topology for a UniProt-numbered model (AlphaFold): the membrane normal points
    from the cytoplasmic ends of the transmembrane helices to their extracellular ends. The model is moved into
    that frame (normal = +z, centred on the helices); the view and the planes come back as `commands` for the
    agent to run. `feats` are the UniProt Transmembrane and Topological domain features, fetched by the caller."""
    from chimerax.geometry import Place
    import numpy as np
    st = _find_structure(session, model)
    if st is None:
        return {"error": _no_structure(session, model)}
    if feats.get("error"):
        return {"error": "%s, and UniProt could not be read: %s" % (why, feats["error"])}
    tms = sorted((f["start"], f["end"]) for f in feats.get("features", []) if f["type"] == "Transmembrane")
    doms = [(f["start"], f["end"], (f.get("description") or "").lower()) for f in feats.get("features", []) if f["type"] == "Topological domain"]
    if len(tms) < 1 or not doms:
        return {"error": "%s, and UniProt %s has no transmembrane topology to orient it by." % (why, accession)}

    def side(resnum):
        for a, b, d in doms:
            if a <= resnum <= b:
                if any(o in d for o in _OUTSIDE):
                    return "out"
                if "cytoplasm" in d:
                    return "in"
        return None

    ca = {int(r.number): r.find_atom("CA") for r in st.residues if r.chain_id == st.residues[0].chain_id}
    name = (st.name or "").lower()
    if not ("af-" in name or "alphafold" in name):
        # a PDB entry named after its UniProt accession is numbered by the PDB author: the UniProt positions of
        # the helices only apply when the chain starts at 1 and every helix is present under those numbers
        covered = all(sum(ca.get(n) is not None for n in range(a, b + 1)) >= 0.8 * (b - a + 1) for a, b in tms)
        if min(ca) > 3 or not covered:
            return {"error": "%s, and %s is not numbered like UniProt %s (the transmembrane helices are not found at "
                             "their UniProt positions), so the topology cannot orient it. Give the PDB entry of a "
                             "homologue that OPM has (pdb='2rh1')." % (why, st.name, accession)}
    def ends(a, b):
        pts = [ca[n].scene_coord for n in range(a, b + 1) if ca.get(n) is not None]
        return np.mean(pts, axis=0) if pts else None
    out_pts, in_pts, tm_pts = [], [], []
    for a, b in tms:
        head, tail = ends(a, min(a + 2, b)), ends(max(b - 2, a), b)
        before, after = side(a - 1), side(b + 1)
        for pt, where in ((head, before), (tail, after)):
            if pt is None or where is None:
                continue
            (out_pts if where == "out" else in_pts).append(pt)
        tm_pts += [ca[n].scene_coord for n in range(a, b + 1) if ca.get(n) is not None]
    if not out_pts or not in_pts:
        return {"error": "%s, and the model does not cover the transmembrane helices of UniProt %s." % (why, accession)}
    normal = np.mean(out_pts, axis=0) - np.mean(in_pts, axis=0)
    thickness = float(np.linalg.norm(normal))
    normal = normal / thickness
    center = np.mean(tm_pts, axis=0)
    # rotation taking the normal to +z (Rodrigues), then the helix centre to the origin
    z = np.array([0.0, 0.0, 1.0])
    v, c = np.cross(normal, z), float(np.dot(normal, z))
    if np.linalg.norm(v) < 1e-8:
        rot = np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    else:
        vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
        rot = np.eye(3) + vx + vx @ vx * (1.0 / (1.0 + c))
    m = np.zeros((3, 4))
    m[:, :3] = rot
    m[:, 3] = -rot @ center
    st.position = Place(m) * st.position
    half = 15.0          # the hydrophobic core of a typical plasma membrane is about 30 A thick
    xyz = st.atoms.scene_coords
    extent = float(max(xyz[:, 0].max() - xyz[:, 0].min(), xyz[:, 1].max() - xyz[:, 1].min()))
    cmds = ["view orient", "turn x -90"]
    if slabs:
        w = int(min(extent, 80) + 30)
        for zz, name in ((half, "pellaeon_membrane_out"), (-half, "pellaeon_membrane_in")):
            cmds.append("shape rectangle width %d height %d center 0,0,%.1f slab 1.5 color #90a4ae80 name %s" % (w, w, zz, name))
    cmds.append("view #%s" % st.id_string)
    return {"model": "#" + st.id_string, "orientation_from": "UniProt %s topology (%d transmembrane helices)" % (accession, len(tms)),
            "why_not_opm": why, "extracellular": "up; cytoplasm down", "slabs": bool(slabs),
            "membrane_half_thickness": half, "commands": cmds,
            "note": "Oriented from the UniProt topology, not from OPM: the membrane normal runs from the cytoplasmic to the "
                    "extracellular ends of the transmembrane helices, and the boundaries are drawn 15 A either side of their "
                    "centre, an approximation. Say so in one sentence. `turn y N` keeps extracellular up."}


_AA3 = {"ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
        "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
        "MSE": "M", "SEC": "U"}


def find_sequence(session, seq: str) -> List[str]:
    """Read-only: where a typed one-letter peptide sequence occurs in the open chains, as residue specs
    ('#1/A:18-38'). Searches the residues that exist, so gaps in the model do not shift the numbers."""
    from chimerax.atomic import AtomicStructure
    seq = (seq or "").upper()
    hits: List[str] = []
    if len(seq) < 5:
        return hits
    for m in session.models.list(type=AtomicStructure):
        for c in m.chains:
            res = [r for r in c.existing_residues]
            letters = "".join(_AA3.get(r.name, "X") for r in res)
            start = letters.find(seq)
            while start >= 0 and len(hits) < 6:
                a, b = res[start], res[start + len(seq) - 1]
                hits.append("#%s/%s:%d-%d" % (m.id_string, c.chain_id, a.number, b.number))
                start = letters.find(seq, start + 1)
    return hits


def view_axis(session, spec: str = "", axis: str = "short") -> Dict[str, Any]:
    """Look straight down a principal axis of an assembly: 'short' = the axis of least extent (the
    symmetry axis of rings, discs, capsid faces, the membrane normal of a flat complex), 'long' = the
    axis of greatest extent (a rod or filament seen end-on). 'side' turns a rod broadside on."""
    from chimerax.core.commands import run, atomspec
    from chimerax.atomic import AtomicStructure, all_atomic_structures
    from chimerax.geometry import Place, orthonormal_frame
    import numpy as np
    spec = (spec or "").strip()
    if spec:
        try:
            sp, used, rest = atomspec.AtomSpecArg.parse(spec, session)
            atoms = sp.evaluate(session).atoms
        except Exception as e:  # noqa: BLE001
            return {"error": "Cannot read '%s' as a model or atom specifier: %s" % (spec, e)}
    else:
        atoms = None
        structs = all_atomic_structures(session)
        if structs:
            from chimerax.atomic import concatenate
            atoms = concatenate([s.atoms for s in structs if s.display])
    if atoms is None or len(atoms) < 3:
        return {"error": "Nothing to orient: %s" % (_no_structure(session, spec) if spec else "no atomic structures are open.")}
    xyz = atoms.scene_coords
    center = xyz.mean(axis=0)
    cov = np.cov((xyz - center).T)
    vals, vecs = np.linalg.eigh(cov)          # ascending: vecs[:, 0] = least extent, vecs[:, 2] = greatest
    which = {"short": 0, "long": 2, "side": 2}.get((axis or "short").lower())
    if which is None:
        return {"error": "axis must be 'short' (look down the thin direction), 'long' (rod end-on) or 'side' (rod broadside)."}
    look = vecs[:, which]
    cam = session.main_view.camera
    if (axis or "").lower() == "side":
        # camera looks along the middle axis with the long axis horizontal on screen
        view_dir, screen_up = vecs[:, 1], vecs[:, 0]
        frame = orthonormal_frame(-view_dir, ydir=screen_up).axes()
    else:
        # keep the current camera's sense so the view does not flip on repeat; nothing else to keep
        if np.dot(cam.view_direction(), look) < 0:
            look = -look
        frame = orthonormal_frame(-look).axes()
    dist = max(1.0, float(np.sqrt(vals[2]) * 6))
    cam.position = Place(axes=frame, origin=center - frame[:, 2] * dist)   # camera looks down its -z axis
    run(session, "view %s" % (spec or ""), log=False)
    ext = np.sqrt(vals) * 2
    return {"axis": axis, "atoms": len(atoms), "extents": [round(float(e), 1) for e in ext],
            "hint": "spread of the atoms along the three principal axes (least to greatest); 'short' is the symmetry axis of a ring or disc, 'long' a rod"}


# --------------------------------------------------------------------------------------
# Request-level undo. A checkpoint is recorded on the session (not the Executor, which can be
# rebuilt) before the first command or tool of a user request that changes anything (a question
# answered with `info` records none): which models were open, and, unless the session is very
# large, a saved .cxs snapshot. undo_last_request restores the checkpoint of the last request
# that changed something: closing the model(s) it opened when that is all it did, or the whole
# session when it also colored, styled, moved the view or deleted anything.
# --------------------------------------------------------------------------------------

CHECKPOINT_ATOM_CAP = 300_000     # a .cxs of more than this takes seconds; above it only model ids are kept
CHECKPOINT_SLOW_SECONDS = 3.0     # two slow saves in a row switch the session to ids-only checkpoints
MAX_CHECKPOINTS = 3


def _quote_path(path: str) -> str:
    return '"%s"' % path.replace('"', '\\"')


def _checkpoints(session) -> List[Dict[str, Any]]:
    cps = getattr(session, "_pellaeon_checkpoints", None)
    if cps is None:
        cps = session._pellaeon_checkpoints = []
    return cps


def _open_model_ids(session) -> List[str]:
    return sorted(m.id_string for m in session.models.list() if m.id)


def key_palette_stops(session, colors: List[str]) -> Dict[str, Any]:
    """The ChimeraX palette whose colours are exactly `colors`, in that order, when it has numeric stops of its own
    (alphafold: 0 50 70 90 100). The 0-1 palettes of the colour-brewer family have no stops worth printing."""
    try:
        import numpy as np
        from chimerax.core.colors import BuiltinColormaps, BuiltinColors
    except Exception:  # noqa: BLE001
        return {}
    want = []
    for name in colors:
        name = name.strip().strip("\"'").lower()
        if name.startswith("#") and len(name) in (7, 9):
            try:
                want.append([int(name[i:i + 2], 16) / 255.0 for i in (1, 3, 5)])
                continue
            except ValueError:
                return {}
        col = BuiltinColors.get(name)
        if col is None:
            return {}
        want.append([float(x) for x in col.rgba[:3]])
    for pname, cmap in BuiltinColormaps.items():
        vals = getattr(cmap, "data_values", None)
        if vals is None or len(vals) != len(want) or float(np.max(vals)) <= 1.0:
            continue
        have = np.asarray(cmap.colors, dtype=float)[:, :3]
        if np.allclose(have, np.asarray(want, dtype=float), atol=0.01):
            return {"palette": pname, "values": [float(v) for v in vals]}
    return {}


def _top_level(ids: List[str]) -> List[str]:
    """Model ids as a user counts them: #1, not its submodels #1.1 and #1.2."""
    return [i for i in ids if "." not in i]


def _atom_count(session) -> int:
    try:
        from chimerax.atomic import all_atomic_structures
        return int(sum(m.num_atoms for m in all_atomic_structures(session)))
    except Exception:  # noqa: BLE001
        return 0


CAP_REASONS = {
    "atoms": "the session has more than %d atoms, too large to save a copy of first" % CHECKPOINT_ATOM_CAP,
    "slow": "saving a copy of the session took more than %d s twice in a row" % CHECKPOINT_SLOW_SECONDS,
    "off": "session copies are switched off (PELLAEON_CHECKPOINTS)",
}


def _save_session(session, path: str) -> None:
    from chimerax.core.commands import run
    run(session, "save %s" % _quote_path(path), log=False)


def checkpoint_request(session, request_text: str, cache_dir: str) -> Dict[str, Any]:
    """Record a restore point before a user request changes anything: the ids of every open model and,
    unless the session is very large (over CHECKPOINT_ATOM_CAP atoms) or saving it has been slow twice
    in a row, a saved .cxs snapshot. Keeps the last MAX_CHECKPOINTS; older snapshot files are removed.
    Called once per request, by the agent, before its first command or tool that changes something;
    bookkeeping only, must never raise into the turn that calls it. A save that fails or is skipped is
    recorded in `save_error` / `cap_reason`, so the agent can say before the change what undo will cover.
    With PELLAEON_UNDO=snapshot the restore point is an in-memory snapshot instead (see undo.py)."""
    if snapshot_undo():
        return _snap_begin(session, request_text, cache_dir)
    cps = _checkpoints(session)
    cp: Dict[str, Any] = {"request": request_text, "ts": time.time(), "models_before": _open_model_ids(session),
                          "cxs_path": None, "capped": False, "cap_reason": "", "save_error": "",
                          "new_model_ids": [], "only_opens": True, "any_activity": False}
    atoms = _atom_count(session)
    slow_saves = int(getattr(session, "_pellaeon_checkpoint_slow", 0) or 0)
    if os.environ.get("PELLAEON_CHECKPOINTS", "").lower() in ("off", "0", "no"):
        cp["capped"], cp["cap_reason"] = True, CAP_REASONS["off"]       # evaluation runs: no per-request session files
        cp["quiet"] = True                                               # and nothing to tell the user about
    elif atoms > CHECKPOINT_ATOM_CAP:
        cp["capped"], cp["cap_reason"] = True, CAP_REASONS["atoms"]
    elif slow_saves >= 2 and atoms >= getattr(session, "_pellaeon_checkpoint_slow_size", 0):
        # two slow saves in a row at this size: a session this big does not pay for a file on every request.
        # One slow save is not evidence (the first save of a session pays for imports); a smaller session tries again.
        cp["capped"], cp["cap_reason"] = True, CAP_REASONS["slow"]
    elif not cp["models_before"]:
        pass                                     # empty session: the model ids say it all, nothing to snapshot
    else:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            path = os.path.join(cache_dir, "checkpoint_%d.cxs" % int(cp["ts"] * 1000))
            t0 = time.time()
            _save_session(session, path)
            if time.time() - t0 > CHECKPOINT_SLOW_SECONDS:
                session._pellaeon_checkpoint_slow = slow_saves + 1
                session._pellaeon_checkpoint_slow_size = atoms
            else:
                session._pellaeon_checkpoint_slow = 0
            cp["cxs_path"] = path if os.path.exists(path) else None
            if cp["cxs_path"] is None:
                cp["save_error"] = "the session file was not written"
        except Exception as e:  # noqa: BLE001
            cp["save_error"] = str(e)
    cps.append(cp)
    session._pellaeon_checkpoint_current = cp     # the request in flight: its batches report to it, undo skips it
    while len(cps) > MAX_CHECKPOINTS:
        old = cps.pop(0)
        p = old.get("cxs_path")
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    return cp


def _current_checkpoint(session) -> Optional[Dict[str, Any]]:
    """The checkpoint of the request in flight, while it is still on the stack."""
    cp = getattr(session, "_pellaeon_checkpoint_current", None)
    return cp if cp is not None and any(c is cp for c in _checkpoints(session)) else None


def note_checkpoint_activity(session, commands: List[str], results: List[Dict[str, Any]]) -> None:
    """After a batch of commands ran: update the checkpoint of the request in flight with what it did, so
    undo_last_request can tell a request that only opened models (cheap to undo: just close them) from one
    that changed anything else (needs the whole session restored). Commands that only read count for nothing."""
    try:
        from .core.safety import reads_only
    except ImportError:      # the plain-Python tests import analysis as a top-level module
        from core.safety import reads_only
    cp = _current_checkpoint(session)
    if cp is None:
        return
    for cmd, r in zip(commands, results):
        if not r.get("ok") or reads_only(str(cmd)):
            continue
        cp["any_activity"] = True
        toks = str(cmd).strip().lstrip("~").split()
        word = toks[0].lower() if toks else ""
        if word == "open":
            for nm in (r.get("new_models") or []):
                mid = nm[1:].split()[0] if nm.startswith("#") else ""
                if mid:
                    cp["new_model_ids"].append(mid)
        else:
            cp["only_opens"] = False


def mark_checkpoint_dirty(session) -> None:
    """Something changed the session outside run_commands (run_python can do anything): the
    models-only fast path no longer applies to the current request."""
    cp = _current_checkpoint(session)
    if cp is not None:
        cp["only_opens"] = False
        cp["any_activity"] = True
        cp["dirty"] = True


def _closing_restores(cp: Dict[str, Any]) -> bool:
    """Closing the models the request opened puts everything back: it did nothing else, or nothing was open
    before it (an empty session saves no file) and its commands touched only what it opened. Python code
    can change anything, so a request that ran some is not covered."""
    if not cp["new_model_ids"]:
        return False
    return bool(cp["only_opens"]) or (not cp["models_before"] and not cp.get("cxs_path") and not cp.get("dirty"))


def _undo_target(session, direct: bool = False):
    """The checkpoint to restore to: the last one, skipping the request in flight (an undo asked in words is
    itself a request; it has a checkpoint of its own only if it changed something before asking). The Undo
    button (`direct`) makes no request. None when there is nothing to restore."""
    cps = _checkpoints(session)
    current = None if direct else _current_checkpoint(session)
    target = next((c for c in reversed(cps) if c is not current), None)
    return target, cps


UNDO_DETAIL = {
    "models": "closes the model(s) it opened",
    "session": "reopens the session saved before it; anything done since is lost",
    "none": "it cannot be undone",
    "snapshot": "puts back what it changed; changes made by hand since are kept",
}


def pending_undo(session, direct: bool = False) -> Dict[str, Any]:
    """What undo_last_request would do, without doing it: used to word the confirmation card and the
    Undo button. `detail` says what undoing does, `warning` what would be lost with it."""
    if snapshot_undo():
        return _snap_pending(session)
    target, _cps = _undo_target(session, direct)
    if target is None:
        return {"error": "There is no earlier request to undo yet."}
    if _closing_restores(target):
        kind = "models"
    elif target.get("capped"):
        kind = "models" if target["new_model_ids"] else "none"
    elif not target.get("cxs_path"):
        kind = "none"
    else:
        kind = "session"
    return {"request": target["request"], "kind": kind, "detail": UNDO_DETAIL[kind]}


def undo_last_request(session, direct: bool = False) -> Dict[str, Any]:
    """Restore ChimeraX to how it was immediately before the last request ran. `run` is imported lazily
    at each use (not at the top) so the no-op/error branches below stay callable without ChimeraX, for
    plain-Python testing of the fast-path-vs-full-restore decision."""
    if snapshot_undo():
        return _snap_undo(session)
    target, cps = _undo_target(session, direct)
    if target is None:
        return {"error": "There is no earlier request to undo yet."}
    models_before = _open_model_ids(session)
    open_now = {m.id_string for m in session.models.list() if m.id}

    if _closing_restores(target):
        ids = sorted(set(target["new_model_ids"]) & open_now)
        cps.remove(target)  # spent, restored or not: the next undo reaches the request before it
        if not ids:
            return {"error": "The model(s) that request opened are already closed; nothing to undo.",
                    "request": target["request"]}
        from chimerax.core.commands import run
        run(session, "close " + " ".join("#" + i for i in ids), log=False)
        models_after = _open_model_ids(session)
        return {"restored": "models", "request": target["request"], "models_before": models_before,
                "models_after": models_after,
                "summary": "Undid the last request (\"%s\"): closed the model(s) it opened (%s). Models now open: %s."
                           % (target["request"], ", ".join("#" + i for i in ids), ", ".join("#" + i for i in _top_level(models_after)) or "none")}

    if target.get("capped"):
        ids = sorted(set(target["new_model_ids"]) & open_now)
        cps.remove(target)
        why = target.get("cap_reason") or CAP_REASONS["atoms"]
        if ids:
            from chimerax.core.commands import run
            run(session, "close " + " ".join("#" + i for i in ids), log=False)
            models_after = _open_model_ids(session)
            return {"restored": "models only", "request": target["request"], "models_before": models_before,
                    "models_after": models_after,
                    "summary": "No copy of the session was saved before that request ran (%s), so only the model(s) "
                               "it opened could be closed (%s); any color, style or view change it made is still there."
                               % (why, ", ".join("#" + i for i in ids))}
        return {"error": "No copy of the session was saved before that request ran (%s), and it did more than open "
                         "a model, so it cannot be undone." % why, "request": target["request"]}

    path = target.get("cxs_path")
    if not path or not os.path.exists(path):
        cps.remove(target)
        return {"error": "The saved checkpoint for that request is missing (%s), so it cannot be restored."
                         % (target.get("save_error") or "file not found"), "request": target["request"]}
    from chimerax.core.commands import run
    run(session, "close", log=False)
    run(session, "open %s" % _quote_path(path), log=False)
    cps.remove(target)
    models_after = _open_model_ids(session)
    return {"restored": "session", "request": target["request"], "models_before": models_before,
            "models_after": models_after,
            "summary": "Undid the last request (\"%s\"): restored the session to how it was before it ran. "
                       "Models now open: %s." % (target["request"], ", ".join("#" + i for i in _top_level(models_after)) or "none")}


# --------------------------------------------------------------------------------------
# Request-level undo from in-memory snapshots (PELLAEON_UNDO=snapshot; see undo.py). Each request that
# changed something leaves one entry on a stack, pushed when the request ends, so the Undo button and the
# undo_last_request tool both undo the top entry; pressing again undoes the request before it. A session
# file is saved only before a batch that deletes or replaces atoms, models or pseudobonds (undo.needs_session).
# --------------------------------------------------------------------------------------

def snapshot_undo() -> bool:
    return os.environ.get("PELLAEON_UNDO", "").lower() == "snapshot"


def _undo_stack(session) -> List[Dict[str, Any]]:
    st = getattr(session, "_pellaeon_undo_stack", None)
    if st is None:
        st = session._pellaeon_undo_stack = []
    return st


def _drop_entry(e: Dict[str, Any]) -> None:
    p = e.get("session_path")
    if p and os.path.exists(p):
        try:
            os.remove(p)
        except OSError:
            pass


def _snap_begin(session, request_text: str, cache_dir: str) -> Dict[str, Any]:
    from . import undo
    finish_request(session)        # a request that never reached its end is closed first
    p = {"request": request_text, "before": undo.capture(session), "cache_dir": cache_dir,
         "session_path": None, "mid": None, "reason": "", "reason_command": "", "save_error": ""}
    session._pellaeon_undo_pending = p
    return {"request": request_text, "seconds": p["before"]["seconds"]}


def before_batch(session, commands: List[str], reason: Optional[str] = None) -> None:
    """Called before a batch of commands (or Python code, with a reason) runs inside a request: saves a session
    file first when the batch can delete or replace what the snapshot cannot bring back. Once per request."""
    if not snapshot_undo():
        return
    p = getattr(session, "_pellaeon_undo_pending", None)
    if p is None or p["reason"]:
        return
    from . import undo
    if reason:
        why, what = reason, "the Python code"
    else:
        why, what = next(((w, c) for c, w in ((c, undo.needs_session(session, c)) for c in commands) if w), (None, ""))
    if not why:
        return
    p["reason"], p["reason_command"] = why, what if reason else '"%s"' % what.strip()[:80]
    if os.environ.get("PELLAEON_CHECKPOINTS", "").lower() in ("off", "0", "no"):
        p["save_error"] = "session files are switched off"
        return
    if _atom_count(session) > CHECKPOINT_ATOM_CAP:
        p["save_error"] = "the session has more than %d atoms, too large to save a copy first" % CHECKPOINT_ATOM_CAP
        return
    try:
        from chimerax.core.commands import run
        os.makedirs(p["cache_dir"], exist_ok=True)
        path = os.path.join(p["cache_dir"], "checkpoint_%d.cxs" % int(time.time() * 1000))
        run(session, "save %s" % _quote_path(path), log=False)
        if not os.path.exists(path):
            p["save_error"] = "the session file was not written"
            return
        p["session_path"] = path
        p["mid"] = undo.capture(session)
    except Exception as e:  # noqa: BLE001
        p["save_error"] = "the session could not be saved (%s)" % e


def finish_request(session) -> Optional[Dict[str, Any]]:
    """Called when a request ends: keeps what it changed on the undo stack, or nothing when it changed nothing.
    Without snapshot undo there is only the checkpoint of the request in flight to let go of."""
    session._pellaeon_checkpoint_current = None
    p = getattr(session, "_pellaeon_undo_pending", None)
    if p is None:
        return None
    session._pellaeon_undo_pending = None
    from . import undo
    entry = undo.diff(p["before"], undo.capture(session))
    entry.update(request=p["request"], session_path=p["session_path"], reason=p["reason"],
                 reason_command=p["reason_command"], save_error=p["save_error"])
    if p["session_path"]:
        if entry["empty"]:
            _drop_entry(entry)
            return None
        entry["mid"] = undo.diff(p["before"], p["mid"])     # what ran before the file was saved
        entry["lost"] = []                                  # the file brings these back
        try:
            entry["screen"] = visible_snapshot(session)     # to tell what is done by hand afterwards
        except Exception:  # noqa: BLE001
            entry["screen"] = None
    elif entry["empty"]:
        return None
    st = _undo_stack(session)
    st.append(entry)
    total = sum(e.get("bytes", 0) for e in st)
    while len(st) > undo.MAX_ENTRIES or (len(st) > 1 and total > undo.MAX_BYTES):
        old = st.pop(0)
        total -= old.get("bytes", 0)
        _drop_entry(old)
    return entry


def _snap_pending(session) -> Dict[str, Any]:
    st = _undo_stack(session)
    if not st:
        return {"error": "There is no earlier request to undo yet."}
    e = st[-1]
    info: Dict[str, Any] = {"request": e["request"], "depth": len(st)}
    if e.get("session_path"):
        info["kind"] = "session"
        info["detail"] = "reopens the session saved before %s ran, since that %s" % (e["reason_command"], e["reason"])
        if e.get("screen") is not None:
            try:
                from .core.effects import describe
                vis, hid = describe(e["screen"], visible_snapshot(session))
                if vis or hid:
                    info["warning"] = "what was changed since is undone too: " + "; ".join((vis + hid)[:4])
            except Exception:  # noqa: BLE001
                pass
    elif e["lost"]:
        info["kind"] = "partial"
        info["detail"] = "puts back what it changed but not this: %s (%s)" % (
            "; ".join(e["lost"][:3]), e.get("save_error") or "no session file was saved")
    else:
        info["kind"] = "snapshot"
        info["detail"] = UNDO_DETAIL["snapshot"]
    return info


def _snap_undo(session) -> Dict[str, Any]:
    from . import undo
    st = _undo_stack(session)
    if not st:
        return {"error": "There is no earlier request to undo yet."}
    e = st.pop()
    models_before = _open_model_ids(session)
    if e.get("session_path"):
        path = e["session_path"]
        if not os.path.exists(path):
            return {"error": "The session file saved before that request is missing, so it cannot be restored.",
                    "request": e["request"]}
        from chimerax.core.commands import run
        run(session, "close", log=False)
        run(session, "open %s" % _quote_path(path), log=False)
        rep = undo.restore(session, e["mid"], by_id=True, force=True)
        _drop_entry(e)
        for older in st:              # the models were made again from the file: find them by number from now on
            older["by_id"] = True
        how = "reopened the session saved before it"
    else:
        rep = undo.restore(session, e, by_id=bool(e.get("by_id")))
        how = "put back what it changed"
        if rep["closed"]:
            how += " and closed %s" % ", ".join(rep["closed"])
    p = getattr(session, "_pellaeon_undo_pending", None)
    if p is not None:                 # asked for inside a request: the undo is not that request's own change
        p["before"] = undo.capture(session)
    models_after = _open_model_ids(session)
    summary = "Undid the last request (\"%s\"): %s." % (e["request"], how)
    if rep["kept"]:
        summary += " Kept what was changed by hand since: %s." % "; ".join(rep["kept"][:4])
    not_back = list(e.get("lost") or []) + rep["failed"]
    if not_back:
        summary += " Not restored: %s." % "; ".join(not_back[:4])
    summary += " Models now open: %s." % (", ".join("#" + i for i in models_after) or "none")
    if st:
        summary += " %d earlier request%s can be undone." % (len(st), "" if len(st) == 1 else "s")
    return {"restored": "session" if e.get("session_path") else "snapshot", "request": e["request"],
            "models_before": models_before, "models_after": models_after, "kept": rep["kept"],
            "not_restored": not_back, "summary": summary}


# ---------------------------------------------------------------- state helpers for the guards (2026-10-01)
def selection_snapshot(session) -> Dict[str, Any]:
    """The selected atoms, kept as the collection itself so they can be re-selected exactly."""
    from chimerax.atomic import selected_atoms
    atoms = selected_atoms(session)
    return {"atoms": atoms, "num_atoms": len(atoms)}


def selection_restore(session, snap: Dict[str, Any]) -> int:
    """Put back a snapshot's selection (after a select that matched nothing cleared it)."""
    atoms = snap.get("atoms")
    session.selection.clear()
    if atoms is None or len(atoms) == 0:
        return 0
    atoms.selected = True
    try:
        atoms.intra_bonds.selected = True
    except Exception:  # noqa: BLE001
        pass
    return len(atoms)


def selection_count(session) -> int:
    from chimerax.atomic import selected_atoms
    return len(selected_atoms(session))


def has_surface(session, spec: str) -> Optional[bool]:
    """Does a molecular surface exist for atoms of `spec`? None when the spec cannot be read."""
    from chimerax.core.commands import atomspec
    from chimerax.atomic import MolecularSurface
    surfaces = [m for m in session.models.list(type=MolecularSurface)]
    if not surfaces:
        return False
    try:
        parsed, used, rest = atomspec.AtomSpecArg.parse(spec or "", session)
        atoms = parsed.evaluate(session).atoms if used and used.strip() else None
    except Exception:  # noqa: BLE001
        return None
    if atoms is None:
        return True
    for s in surfaces:
        try:
            if len(s.atoms.intersect(atoms)):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def residues_named(session, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """For residues named in a request ("Leu8", "Gly184A", "Thr-3"): what sits at that number (with insertion code)
    in the open structures, restricted to the chains the request names when it names any. Each item:
    {"name": "LEU", "number": 8, "insertion": "", "chains": ["B"] or []}. A hyphen before the number may be a
    minus sign or a separator, so both readings are tried and the one whose residue name matches wins."""
    from chimerax.atomic import all_atomic_structures
    out = []
    for it in items:
        name, num, ins, chains = it["name"].upper(), int(it["number"]), (it.get("insertion") or "").upper(), [c.upper() for c in it.get("chains") or []]
        hits = []
        for m in all_atomic_structures(session):
            for r in m.residues:
                if r.polymer_type == 0 or (chains and r.chain_id.upper() not in chains):
                    continue
                if int(r.number) == num and (ins == "*" or (r.insertion_code or "").upper() == ins):
                    hits.append({"spec": "#%s/%s:%d%s" % (m.id_string, r.chain_id, num, (r.insertion_code or "").upper()), "name": r.name, "chain": r.chain_id})
        out.append({"asked": it.get("asked") or "%s%d%s" % (name.capitalize(), num, ins), "name": name, "number": num,
                    "insertion": ins, "chains": chains, "found": hits[:8]})
    return out


def surface_models_for(session, spec: str) -> List[str]:
    """Ids of the molecular surfaces whose atoms are in `spec` (a chain-only spec selects no surface model by
    itself in `surface style`, so the surface's own id is what to pass)."""
    from chimerax.core.commands import atomspec
    from chimerax.atomic import MolecularSurface
    surfaces = session.models.list(type=MolecularSurface)
    if not surfaces:
        return []
    try:
        parsed, used, rest = atomspec.AtomSpecArg.parse(spec or "", session)
        atoms = parsed.evaluate(session).atoms if used and used.strip() else None
    except Exception:  # noqa: BLE001
        return []
    out = []
    for sf in surfaces:
        try:
            if atoms is None or len(sf.atoms.intersect(atoms)):
                out.append("#" + sf.id_string)
        except Exception:  # noqa: BLE001
            continue
    return out


def spec_residue_keys(session, spec: str) -> Optional[List[Tuple[str, str, int, str]]]:
    """The residues a spec matches, as (model, chain, number, insertion) keys; None when it cannot be read."""
    from chimerax.core.commands import atomspec
    try:
        parsed, used, rest = atomspec.AtomSpecArg.parse(spec or "", session)
        if not used or not used.strip():
            return None
        res = parsed.evaluate(session).atoms.unique_residues
    except Exception:  # noqa: BLE001
        return None
    return sorted({(r.structure.id_string, r.chain_id, int(r.number), r.insertion_code or "") for r in res})


def same_targets(session, specs: List[str], spec: str) -> Optional[bool]:
    """Do the specs together match exactly what `spec` matches: the same atoms and the same models (a grouping
    model without content of its own aside)? None when a spec cannot be read."""
    import numpy as np
    from chimerax.core.commands import atomspec

    def objects(text):
        parsed, used, rest = atomspec.AtomSpecArg.parse(text, session)
        if not used or not used.strip() or (rest or "").strip():
            raise ValueError(text)
        return parsed.evaluate(session)
    try:
        parts = [objects(x) for x in specs]
        whole = objects(spec)
    except Exception:  # noqa: BLE001
        return None

    def content(models):
        return {m for m in models if type(m).__name__ != "Model"}
    atoms_a = np.unique(np.concatenate([np.asarray(p.atoms.pointers) for p in parts])) if parts else np.array([])
    atoms_b = np.unique(np.asarray(whole.atoms.pointers))
    models_a = set().union(*[content(p.models) for p in parts]) if parts else set()
    return bool(np.array_equal(atoms_a, atoms_b)) and models_a == content(whole.models)


def visible_snapshot(session) -> Dict[str, Any]:
    """What is on screen, compact enough to compare before and after a batch (core.effects.describe does the
    comparison). Every model carries its own display flag and its effective visibility (itself and every parent
    displayed). Per structure: which atoms and cartoon residues are displayed (count and identity), digests of
    their colours (displayed and all), styles and radii, position, coordinates, and its residue labels (text,
    colour, size). Per surface: visibility, style, colours, transparency. 2D labels: text, colour, size,
    position. Pseudobonds: which are displayed, colours, radii. Volumes: levels, colours, style. Other models:
    colour, geometry size, mask and position. Also which atoms are selected and the camera (position and field
    of view)."""
    import hashlib
    import numpy as np
    from chimerax.atomic import Structure, PseudobondGroup

    def dg(arr) -> str:
        try:
            a = np.ascontiguousarray(arr)
            return hashlib.blake2b(a.tobytes(), digest_size=8).hexdigest() + ":" + str(a.shape[0] if a.ndim else 0)
        except Exception:  # noqa: BLE001
            return str(arr)

    def dgs(items) -> str:
        return hashlib.blake2b(repr(items).encode(), digest_size=8).hexdigest() + ":" + str(len(items))

    models: Dict[str, Any] = {}
    sel_parts = []
    nsel = 0
    for m in session.models.list():
        name = m.__class__.__name__
        parent = getattr(m, "parent", None)
        if name == "ObjectLabels" and isinstance(parent, Structure):
            continue                         # reported with its structure
        e: Dict[str, Any] = {"display": bool(m.display), "visible": bool(getattr(m, "visible", m.display))}
        try:
            if isinstance(m, Structure):
                atoms, res = m.atoms, m.residues
                shown = atoms.visibles
                rib = res.ribbon_displays
                sel = atoms.selected
                if sel.any():
                    nsel += int(sel.sum())
                    sel_parts.append(m.id_string + ":" + dg(sel))
                e.update(kind="structure", atoms_shown=int(shown.sum()), atoms_shown_id=dg(shown), atom_colors=dg(atoms.colors),
                         atom_colors_shown=dg(atoms.colors[shown]), atom_styles=dg(atoms.draw_modes[shown]),
                         atom_radii_shown=dg(np.round(atoms.radii[shown], 2)),
                         ribbons_shown=int(rib.sum()), ribbons_shown_id=dg(rib), ribbon_colors=dg(res.ribbon_colors),
                         ribbon_colors_shown=dg(res.ribbon_colors[rib]),
                         position=dg(np.round(m.position.matrix, 3)), coords=dg(np.round(atoms.coords, 3)))
                labels = []
                subs = m.all_models()
                for c in subs:
                    if c.__class__.__name__ == "ObjectLabels" and c.display:
                        for lab in c.labels():
                            if getattr(lab, "visible", True):
                                labels.append((getattr(lab.object, "cpp_pointer", 0), lab.text, tuple(lab.color), lab.size, lab.height))
                e["labels"] = len(labels)
                e["labels_id"] = dgs(sorted(labels))
                e["surface_shown"] = any(c.__class__.__name__ == "MolecularSurface" and c.display and
                                         (getattr(c, "triangle_mask", None) is None or int(c.triangle_mask.sum()) > 0) for c in subs)
            elif name == "MolecularSurface":
                mask = getattr(m, "triangle_mask", None)
                vis = bool(m.display) and (mask is None or int(mask.sum()) > 0)
                vc = getattr(m, "vertex_colors", None)
                e.update(kind="surface", visible=vis and e["visible"], style=str(getattr(m, "display_style", "")),
                         colors=dg(vc) if vc is not None else dg(np.array(m.color)),
                         transparency=int(m.color[3]) if vc is None else int(np.asarray(vc)[:, 3].min()),
                         mask=dg(mask) if mask is not None else "")
            elif name == "LabelModel" and hasattr(m, "label"):
                lab = m.label
                e.update(kind="2dlabel", digest=dgs([getattr(lab, k, None) for k in
                                                     ("text", "color", "background", "size", "xpos", "ypos", "bold", "italic", "font")]))
            elif isinstance(m, PseudobondGroup):
                pb = m.pseudobonds
                shown = pb.displays
                e.update(kind="pseudobonds", shown=int(shown.sum()), shown_id=dg(shown), colors=dg(pb.colors[shown]),
                         radii=dg(np.round(pb.radii[shown], 2)), dashes=int(getattr(m, "dashes", 0)))
            elif hasattr(m, "surfaces") and hasattr(m, "matrix_value_statistics"):      # a volume
                e.update(kind="volume", digest=dgs([(round(float(s.level), 4), tuple(int(x) for x in s.color), bool(s.display),
                                                     str(getattr(s, "style", ""))) for s in m.surfaces]
                                                   + [str(getattr(m, "image_shown", "")), str(m.region)]))
            else:
                vc = getattr(m, "vertex_colors", None)
                va = getattr(m, "vertices", None)
                ta = getattr(m, "triangles", None)
                mask = getattr(m, "triangle_mask", None)
                e.update(kind=name, digest=dgs([list(np.asarray(getattr(m, "color", (0, 0, 0, 0))).tolist()),
                                                dg(vc) if vc is not None else "", 0 if va is None else len(va),
                                                0 if ta is None else len(ta), dg(mask) if mask is not None else "",
                                                str(getattr(m, "display_style", "")), dg(np.round(m.position.matrix, 3))]))
        except Exception:  # noqa: BLE001
            e["kind"] = name
            e["unmeasured"] = True
        models["#" + m.id_string] = e
    try:
        cam = session.main_view.camera
        cam_d = dg(np.round(cam.position.matrix, 2)) + "/" + str(round(float(getattr(cam, "field_of_view", 0) or 0), 2)) + \
            "/" + str(round(float(getattr(cam, "field_width", 0) or 0), 2))
    except Exception:  # noqa: BLE001
        cam_d = ""
    return {"v": 2, "models": models, "selection": nsel, "selection_id": dgs(sel_parts), "camera": cam_d}


def key_status(session) -> Dict[str, Any]:
    """The colour key on screen and which of its colours are still on screen: per entry its colour, label and
    whether a displayed atom, cartoon residue or surface of a displayed model shows that colour (within 24 of 255
    per channel). {} when no key is shown."""
    import numpy as np
    try:
        from chimerax.color_key.model import get_model
        key = get_model(session, create=False)
    except Exception:  # noqa: BLE001
        return {}
    if key is None or key.deleted or not key.display:
        return {}
    from chimerax.atomic import Structure
    shown = []
    for m in session.models.list():
        if not getattr(m, "visible", m.display):
            continue
        if isinstance(m, Structure):
            atoms, res = m.atoms, m.residues
            vis, rib = atoms.visibles, res.ribbon_displays
            if vis.any():
                shown.append(atoms.colors[vis][:, :3])
            if rib.any():
                shown.append(res.ribbon_colors[rib][:, :3])
        elif m.__class__.__name__ == "MolecularSurface":
            vc = getattr(m, "vertex_colors", None)
            shown.append(np.asarray(vc)[:, :3] if vc is not None else np.asarray([m.color[:3]]))
    if shown:
        # distinct colours, packed into one integer each (a row-wise unique is far slower on large surfaces)
        packed = np.unique(np.concatenate([(a[:, 0].astype(np.int32) << 16) | (a[:, 1].astype(np.int32) << 8) | a[:, 2]
                                           for a in shown]))
        cols = np.stack([(packed >> 16) & 255, (packed >> 8) & 255, packed & 255], axis=1).astype(np.int16)
    else:
        cols = np.zeros((0, 3), np.int16)
    entries = []
    for rgba, label in key.rgbas_and_labels:
        c = np.array([int(round(float(x) * 255)) for x in rgba[:3]], np.int16)
        present = bool(len(cols)) and bool((np.abs(cols - c).max(axis=1) <= 24).any())
        entries.append({"color": "#%02x%02x%02x" % tuple(int(x) for x in c), "label": str(label or ""), "present": present})
    title = ""                     # Pellaeon's legend title, which goes with the key: its model spec
    try:
        from chimerax.label.label2d import session_labels
        lm = session_labels(session, create=False)
        lbl = lm.named_label("pellaeon_title") if lm is not None else None
        d = getattr(lbl, "drawing", None)
        if d is not None and not d.deleted:
            title = "#" + d.id_string
    except Exception:  # noqa: BLE001
        pass
    return {"id": "#" + key.id_string, "entries": entries, "title": title}


def key_text_metrics(session, labels: List[str], font_size: int, font: str = "Arial", bold: bool = False,
                     italic: bool = False) -> Dict[str, Any]:
    """Label widths and height in pixels as a colour key draws them (the font at font_size times the pixel scale,
    as color_key does), and the window size in the same pixels. {} without a Qt application: nothing is drawn then,
    and Qt fonts cannot be used."""
    try:
        from Qt.QtGui import QFont, QFontMetrics, QGuiApplication
        if QGuiApplication.instance() is None:
            return {}
        v = session.main_view
        try:
            scale = float(v.render.pixel_scale()) if getattr(v, "render", None) is not None else 1.0
        except Exception:  # noqa: BLE001
            scale = 1.0
        w, h = v.window_size
        fm = QFontMetrics(QFont(font, int(font_size * scale), QFont.Bold if bold else QFont.Normal, italic))
        return {"widths": [fm.boundingRect(t).width() + 6 if t else 0 for t in labels], "height": fm.ascent(),
                "window": [w * scale, h * scale]}
    except Exception:  # noqa: BLE001
        return {}
