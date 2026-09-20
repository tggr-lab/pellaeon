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
    color_cmds = ["color byattribute r:pellaeon_disp %s palette 0,#bdbdbd:1,gold:3,orange:6,#b2182b range 0,6 target ac novalue #d9c6f0" % prep["other_spec"],
                  "color %s #b8c4d6 target ac" % prep["ref_spec"], "transparency %s 60 target c" % prep["ref_spec"],
                  "hide %s models" % prep["ref_spec"],
                  "key #bdbdbd:0 gold:1 orange:3 #b2182b:6+ pos 0.70,0.05 size 0.27,0.035 fontSize 16",
                  '2dlabels text "C\u03b1 shift after fit (\u00c5); lavender = not compared" xpos 0.70 ypos 0.10 size 15 color black']
    return {
        "pairing": basis, "paired_residues": len(rows), "coverage": "%d of %d residues of %s paired" % (len(rows), total_other, prep["other_spec"]),
        "mean_displacement": round(float(arr.mean()), 2), "max_displacement": round(float(arr.max()), 2),
        "rms_displacement": round(float((arr ** 2).mean() ** 0.5), 2), "not_compared": int(total_other - len(rows)),
        "ref_spec": prep["ref_spec"], "other_spec": prep["other_spec"],
        "residues_over_2A": int((arr > 2.0).sum()),
        "top_shifted": [{"chain": c, "residue": n, "name": nm, "displacement": round(d, 2), "reference_residue": "%s:%d" % (rc, rn)}
                        for c, n, nm, d, rc, rn in top],
        "moving_regions": [{"chain": r[0], "range": "%d-%d" % (r[1], r[2]), "max": round(r[3], 2),
                            "spec": "#%s/%s:%d-%d" % (prep["other_id"], r[0], r[1], r[2])} for r in regions[:15]],
        "coloring": "%s shown alone, colored by C-alpha displacement after the fit (gray < 1 A, gold, orange, dark red 6 A or more; lavender = not compared); reference %s hidden, can be shown as a pale ghost" % (prep["other_spec"], prep["ref_spec"]),
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


def chain_uniprot(session, model: str) -> Dict[str, Any]:
    """Read-only: the UniProt accessions the structure file associates with `model`'s chains."""
    m = _find_structure(session, model)
    if m is None:
        return {"error": "No atomic model %s is open." % model, "accessions": []}
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
                "models": len(results.models)}
    except Exception as e:  # noqa: BLE001
        return {"used": used.strip(), "error": str(e)}


_COLOR_WORDS = ("color", "colour", "rainbow", "coulombic", "mlp")


def residue_provenance(session, res_spec: str, journal: List[Dict[str, Any]]) -> Dict[str, Any]:
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
    out: Dict[str, Any] = {"residue": "%s %d" % (r.name, int(r.number)), "chain": r.chain_id, "model": "#" + m.id_string,
                           "model_name": m.name, "spec": "#%s/%s:%d" % (m.id_string, r.chain_id, int(r.number))}
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
    return out


def figure_info(session) -> Dict[str, Any]:
    """Read-only: what a figure bundle needs to record: models and where they came from, camera, background, labels."""
    import re as _re
    from chimerax.atomic import AtomicStructure
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
        models.append({"id": "#" + m.id_string, "name": name, "source": src or name, "chains": [c.chain_id for c in m.chains],
                       "residues": int(m.num_residues), "atoms": int(m.num_atoms), "shown": bool(m.display)})
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
            rows.append(["#" + m.id_string, r.chain_id, int(r.number), r.name, rib, atom])
    return rows


def count_residues(session, spec: str) -> int:
    """Read-only: how many residues an atom spec matches (0 when it does not parse)."""
    from chimerax.core.commands import atomspec
    try:
        results = atomspec.AtomSpecArg.parse(spec, session)[0].evaluate(session)
        return len(results.atoms.unique_residues)
    except Exception:  # noqa: BLE001
        return 0


def list_residues(session, model: str) -> Dict[str, Any]:
    """Read-only: {'model': '#1', 'residues': {'A:10': 'ALA', ...}} for polymer residues with a CA atom."""
    m = _find_structure(session, model)
    if m is None:
        return {"error": "No atomic model %s is open." % model}
    out = {}
    for r in m.residues:
        if r.polymer_type != 0 or r.find_atom("CA") is not None:
            out["%s:%d" % (r.chain_id, int(r.number))] = r.name
    return {"model": "#" + m.id_string, "residues": out}


def set_residue_attr(session, model: str, attr: str, values: Dict[str, Any]) -> Dict[str, Any]:
    """Store per-residue numbers as a registered residue attribute (so `color byattribute r:<attr>` and `key` can use it)."""
    import re as _re
    from chimerax.atomic import Residue
    if not _re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,60}$", attr or ""):
        return {"error": "Bad attribute name %r" % attr}
    m = _find_structure(session, model)
    if m is None:
        return {"error": "No atomic model %s is open." % model}
    try:
        Residue.register_attr(session, attr, "Pellaeon", attr_type=float)
    except Exception:
        pass
    by_key = {"%s:%d" % (r.chain_id, int(r.number)): r for r in m.residues}
    n = 0
    for k, v in values.items():
        r = by_key.get(k)
        if r is not None:
            try:
                setattr(r, attr, float(v)); n += 1
            except (TypeError, ValueError):
                pass
    return {"set": n, "attr": attr}


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


# --------------------------------------------------------------------------------------
# Contact comparison (added by the contact-comparison module; append-only).
# Collector half of src/core/contacts.py: everything below reads the session and returns
# plain data, so the comparison itself stays ChimeraX-free and unit-testable.
# --------------------------------------------------------------------------------------

_SOLVENT_NAMES = {"HOH", "WAT", "DOD", "H2O"}


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
                pairing["/%s:%d" % (x.residue.chain_id, int(x.residue.number))] = \
                    "/%s:%d" % (y.residue.chain_id, int(y.residue.number))
    if not pairing:
        basis = "chain id + residue number (no alignment available)"
        chain = prep.get("chain")
        have = {(r.chain_id, int(r.number)) for r in b.residues}
        for r in a.residues:
            if chain and r.chain_id != chain:
                continue
            if (r.chain_id, int(r.number)) in have:
                pairing["/%s:%d" % (r.chain_id, int(r.number))] = "/%s:%d" % (r.chain_id, int(r.number))
    if not pairing:
        return {"error": "No residues could be paired between %s and %s." % (prep["ref_spec"], prep["other_spec"])}
    return {"pairing": pairing, "basis": basis, "paired_residues": len(pairing)}


def residue_contacts(session, model_spec: str, cutoff: float = 4.0,
                     restrict: Optional[str] = None) -> Dict[str, Any]:
    """Read-only: every residue-residue contact inside one model, as plain dicts.

    A contact is a pair of residues with at least one pair of heavy atoms within `cutoff` A;
    the reported ``min_dist`` and atom names are that closest pair, which is what a picture of
    the interaction should be drawn between. Hydrogens are ignored (most crystal structures have
    none, so including them would make two otherwise identical structures differ), solvent is
    ignored, and intra-residue and sequential-backbone pairs are dropped because they are present
    in every structure and say nothing about a conformational change.

    `restrict` is an atom spec (e.g. a ligand, '#1:AP5'): only contacts with at least one atom on
    that side are returned, and that side is always reported as ``a``.

    Returns {'model': '#1', 'contacts': [{a, a_name, a_atom, b, b_name, b_atom, min_dist, kind}, ...]}.
    """
    import numpy as np
    from chimerax.geometry import find_close_points
    from .core.contacts import classify_contact, strongest_kind

    m = _find_structure(session, model_spec)
    if m is None:
        return {"error": "No atomic model %s is open." % model_spec}
    atoms = m.atoms
    keep = [i for i, a in enumerate(atoms)
            if a.element.name != "H" and a.residue.name.upper() not in _SOLVENT_NAMES]
    if not keep:
        return {"error": "Model %s has no non-solvent heavy atoms." % model_spec}
    atoms = atoms[keep]
    coords = atoms.scene_coords
    residues = [a.residue for a in atoms]
    names = [a.name for a in atoms]
    elements = [a.element.name for a in atoms]

    restrict_set = None
    if restrict:
        from chimerax.core.commands import atomspec
        try:
            sel = atomspec.AtomSpecArg.parse(restrict, session)[0].evaluate(session).atoms
        except Exception as e:  # noqa: BLE001
            return {"error": "Could not read restrict spec '%s': %s" % (restrict, e)}
        wanted = set(sel.pointers if hasattr(sel, "pointers") else [])
        restrict_set = {i for i, a in enumerate(atoms) if a in sel}
        if not restrict_set:
            return {"error": "The restrict spec '%s' matches nothing in %s." % (restrict, model_spec)}

    by_res: Dict[Any, List[int]] = {}
    for i, r in enumerate(residues):
        by_res.setdefault(r, []).append(i)

    def spec_of(r) -> str:
        return "/%s:%d%s" % (r.chain_id, int(r.number), r.insertion_code or "")

    # (residue_a, residue_b) -> (min_dist, atom_a, atom_b, [kinds])
    best: Dict[Any, Any] = {}
    for r, idxs in by_res.items():
        if restrict_set is not None and not any(i in restrict_set for i in idxs):
            continue        # with a restrict spec only that side seeds the search
        rc = coords[idxs]
        near = find_close_points(rc, coords, float(cutoff))[1]
        for j in near:
            other = residues[j]
            if other is r or (restrict_set is not None and j in restrict_set):
                continue    # ligand-internal contacts are not what "what does it touch" means
            if _sequential(r, other):
                continue
            key = (r, other) if restrict_set is not None else _ordered(r, other)
            d = np.linalg.norm(rc - coords[j], axis=1)
            k = int(d.argmin())
            dist = float(d[k])
            if dist > cutoff:
                continue
            ai = idxs[k]
            if key[0] is r:
                pair_atoms = (names[ai], names[j], elements[ai], elements[j])
            else:
                pair_atoms = (names[j], names[ai], elements[j], elements[ai])
            kind = classify_contact(key[0].name, pair_atoms[0], key[1].name, pair_atoms[1], dist,
                                    a_element=pair_atoms[2], b_element=pair_atoms[3],
                                    a_standard=key[0].polymer_type != 0, b_standard=key[1].polymer_type != 0)
            prev = best.get(key)
            if prev is None:
                best[key] = [dist, pair_atoms[0], pair_atoms[1], {kind}]
            else:
                prev[3].add(kind)
                if dist < prev[0]:
                    prev[0], prev[1], prev[2] = dist, pair_atoms[0], pair_atoms[1]

    out: List[Dict[str, Any]] = []
    for (ra, rb), (dist, an, bn, kinds) in best.items():
        out.append({"a": spec_of(ra), "a_name": ra.name, "a_atom": an,
                    "b": spec_of(rb), "b_name": rb.name, "b_atom": bn,
                    "min_dist": round(dist, 2), "kind": strongest_kind(sorted(kinds))})
    out.sort(key=lambda c: (c["a"].split(":")[0], int(re.sub(r"\D", "", c["a"].split(":")[1]) or 0),
                            c["b"].split(":")[0], int(re.sub(r"\D", "", c["b"].split(":")[1]) or 0)))
    return {"model": "#" + m.id_string, "cutoff": float(cutoff), "contacts": out,
            "restrict": restrict or "", "count": len(out)}


def _sequential(r1, r2) -> bool:
    """True for two polymer residues adjacent in sequence: their backbone touches in every
    structure, so the pair carries no information about a conformational change."""
    return (r1.chain_id == r2.chain_id and r1.polymer_type != 0 and r2.polymer_type != 0
            and abs(int(r1.number) - int(r2.number)) <= 1)


def _ordered(r1, r2):
    """Stable order for an undirected residue pair, so each contact is stored once."""
    k1 = (r1.chain_id, int(r1.number), r1.insertion_code or "")
    k2 = (r2.chain_id, int(r2.number), r2.insertion_code or "")
    return (r1, r2) if k1 <= k2 else (r2, r1)
