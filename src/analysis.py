"""ChimeraX-side analysis helpers used by the compare and annotate tools (main thread only)."""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional




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
        return {"error": _no_structure(session, model)}
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
        return {"error": _no_structure(session, model), "accessions": []}
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
        return {"error": _no_structure(session, model)}
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
        return {"error": _no_structure(session, model)}
    warning = None
    try:
        Residue.register_attr(session, attr, "Pellaeon", attr_type=float)
    except Exception as e:  # an earlier registration with another type: values still color, tests like ::attr>3 will not
        warning = "attribute %s could not be registered as a number (%s); 'select ::%s>value' will not work, coloring will" % (attr, e, attr)
        session.logger.warning("Pellaeon: " + warning)
    by_key = {"%s:%d" % (r.chain_id, int(r.number)): r for r in m.residues}
    n = 0
    for k, v in values.items():
        r = by_key.get(k)
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
        return {"error": "Need at least two chains to compare; found %d%s." % (
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


def _open_url_or_path(session, what: str):
    """Open a file or URL and return the new top-level models (URLs are cached locally first)."""
    from chimerax.core.commands import run
    if re.match(r"^https?://", what.strip(), re.I):
        what = fetch_to_cache(what.strip())
    before = set(id(m) for m in session.models.list())
    run(session, "open %s" % _quote_path(what), log=False)
    return [m for m in session.models.list() if id(m) not in before and m.parent is session.models.scene_root_model]


def membrane_orient(session, model: str, pdb: Optional[str] = None, slabs: bool = True) -> Dict[str, Any]:
    """Put a membrane protein in the membrane frame from OPM (Orientations of Proteins in Membranes):
    fetch OPM's oriented coordinates for a PDB entry, superpose the model onto them, look from the side
    with the extracellular face up, and optionally draw the two membrane boundaries."""
    from chimerax.core.commands import run
    from chimerax.atomic import AtomicStructure
    import numpy as np
    st = _find_structure(session, model)
    if st is None:
        return {"error": _no_structure(session, model)}
    ident = (pdb or "").strip().lower() or (re.match(r"^([0-9][a-z0-9]{3})\b", (st.name or "").lower()) or [None, None])[1]
    candidates = [ident] if ident else []
    via = ""
    if not candidates:
        # an AlphaFold/UniProt model: for a GPCR, GPCRdb knows its experimental structures; try those on OPM
        acc = re.search(r"\b([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})\b", st.name or "")
        if acc:
            try:
                from .core import gpcrdb
                entry = gpcrdb.entry_for_accession(acc.group(1))
                rows = gpcrdb.structures(entry).get("structures") or []
                candidates = [str(r["pdb"]).lower() for r in rows if r.get("pdb")][:6]
                via = "GPCRdb (%s)" % entry
            except Exception:  # noqa: BLE001
                candidates = []
    if not candidates:
        return {"error": "No PDB id for %s (%s). Give the PDB entry of this protein or a close homologue (pdb='3vw7'); "
                         "OPM stores orientations per PDB entry." % (model, st.name)}
    ref, tried = None, []
    for ident in candidates:
        try:
            ref = _open_url_or_path(session, "https://opm-assets.storage.googleapis.com/pdb/%s.pdb" % ident)
            break
        except Exception as e:  # noqa: BLE001
            tried.append("%s (%s)" % (ident.upper(), str(e)[:60]))
            ref = None
    if ref is None:
        return {"error": "OPM has no entry for %s. Try the PDB entry of a close homologue." % "; ".join(tried)}
    refs = [m for m in ref if isinstance(m, AtomicStructure)]
    if not refs:
        for m in ref:
            m.delete()
        return {"error": "OPM returned no structure for %s." % ident.upper()}
    opm = refs[0]
    # membrane half-thickness: OPM's dummy atoms (DUM) mark the two boundary planes
    dum = opm.atoms.filter(opm.atoms.residues.names == "DUM")
    half = float(np.abs(dum.coords[:, 2]).mean()) if len(dum) else 15.0
    run(session, "matchmaker #%s to #%s" % (st.id_string, opm.id_string), log=False)
    xyz = st.atoms.scene_coords
    cx, cy = float(xyz[:, 0].mean()), float(xyz[:, 1].mean())
    zmax = float(xyz[:, 2].max())
    extent = float(max(xyz[:, 0].max() - xyz[:, 0].min(), xyz[:, 1].max() - xyz[:, 1].min()))
    for m in ref:
        m.delete()
    # OPM's frame: z is the membrane normal, +z the extracellular side (checked on 3vw7: ECL residues at z ~ +19)
    run(session, "view orient; turn x -90", log=False)      # look along the membrane plane, +z up on screen
    cmds = []
    if slabs:
        w = int(extent + 30)
        for z, name in ((half, "pellaeon_membrane_out"), (-half, "pellaeon_membrane_in")):
            # a thin box, not a one-sided sheet: the sheet's back face is unlit and shows black from below
            cmds.append("shape rectangle width %d height %d center %.1f,%.1f,%.1f slab 1.5 color #90a4ae80 name %s" % (w, w, cx, cy, z, name))
    cmds.append("view #%s" % st.id_string)
    low = None
    try:
        b = st.residues.filter(st.residues.numbers <= 60).atoms
        if len(b) and st.name.lower().startswith(("alphafold", "af-")) :
            low = float((b.bfactors < 50).mean()) if len(b) else None
    except Exception:  # noqa: BLE001
        low = None
    for c in cmds:
        run(session, c, log=False)
    return {"model": "#" + st.id_string, "opm_entry": ident.upper(), "membrane_half_thickness": round(half, 1),
            "extracellular": "up (+z of the OPM frame); cytoplasm down", "slabs": bool(slabs),
            "reference_via": via or "the model's own PDB id",
            "note": "The camera now looks along the membrane with the extracellular side up. Turning with `turn y N` keeps that; "
                    "`view` re-fits. Other structures superposed on %s share the frame." % ("#" + st.id_string)
                    + (" The N-terminal region has low pLDDT: its placement (across the membrane plane or elsewhere) is not modelled; "
                       "hide or fade it if asked, never move the model." if low and low > 0.5 else "")}


def gpcr_states(session, protein: str, open_states: Optional[List[str]] = None, cache_dir: Optional[str] = None) -> Dict[str, Any]:
    """GPCRdb for a receptor: its experimental structures (state, ligand, resolution) and, on request, its
    AlphaFold-Multistate inactive/active models opened as new models (superposed on each other)."""
    from chimerax.core.commands import run
    from .core import gpcrdb
    try:
        entry = gpcrdb.entry_for_accession(protein)
    except Exception as e:  # noqa: BLE001
        return {"error": "Could not turn %r into a GPCRdb entry name: %s (give a UniProt accession or entry name such as PAR1_HUMAN)." % (protein, e)}
    if not entry:
        return {"error": "No GPCRdb entry name for %r." % protein}
    out = gpcrdb.structures(entry)
    if out.get("error"):
        return out
    opened = []
    for state in (open_states or []):
        got = gpcrdb.fetch_model(entry, state, cache_dir)
        if got.get("error"):
            out.setdefault("model_errors", []).append(got["error"])
            continue
        try:
            new = _open_url_or_path(session, got["path"])
        except Exception as e:  # noqa: BLE001
            out.setdefault("model_errors", []).append("Could not open the %s model: %s" % (state, e))
            continue
        if new:
            m = new[0]
            m.name = "GPCRdb %s %s (AlphaFold-Multistate%s)" % (entry, state, (" " + got["version"]) if got.get("version") else "")
            opened.append({"model": "#" + m.id_string, "state": state, "name": m.name, "path": got["path"]})
    if len(opened) >= 2:
        run(session, "matchmaker %s to %s" % (opened[1]["model"], opened[0]["model"]), log=False)
        # no recording recipe here: given one, the model records a movie to the desktop unasked
        out["animation"] = ("For a state-change animation: morph %s,%s frames 30 same true  (makes a new model; play it with "
                            "coordset #N 1,30). Record or save only if the user asks."
                            % (opened[0]["model"].lstrip("#"), opened[1]["model"].lstrip("#")))
    if not open_states:
        out["multistate_models"] = ("GPCRdb also has AlphaFold-Multistate models (inactive and active, full length) for this receptor; "
                                    "call gpcr_states again with open_states=['inactive','active'] to open them.")
    out["opened"] = opened
    out["note"] = ("Experimental structures are listed with their state and ligand; open one with `open <pdb>`. The multistate "
                   "models are full-length AlphaFold predictions from GPCRdb, one per state, superposed on each other.")
    return out


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
# rebuilt) at the start of every user request: which models were open, and, unless the session
# is very large, a saved .cxs snapshot. undo_last_request restores the checkpoint before the
# LAST request: closing the model(s) it opened when that is all it did, or the whole session
# when it also colored, styled, moved the view or deleted anything.
# --------------------------------------------------------------------------------------

CHECKPOINT_ATOM_CAP = 300_000     # a .cxs of more than this takes seconds; above it only model ids are kept
CHECKPOINT_SLOW_SECONDS = 3.0     # one slow save switches the session to ids-only checkpoints
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


def _atom_count(session) -> int:
    try:
        from chimerax.atomic import all_atomic_structures
        return int(sum(m.num_atoms for m in all_atomic_structures(session)))
    except Exception:  # noqa: BLE001
        return 0


def checkpoint_request(session, request_text: str, cache_dir: str) -> Dict[str, Any]:
    """Record a restore point before a user request runs: the ids of every open model and,
    unless the session is very large (over CHECKPOINT_ATOM_CAP atoms), a saved .cxs snapshot.
    Keeps the last MAX_CHECKPOINTS; older snapshot files are removed. Called once at the start
    of every request; bookkeeping only, must never raise into the turn that calls it."""
    cps = _checkpoints(session)
    cp: Dict[str, Any] = {"request": request_text, "ts": time.time(), "models_before": _open_model_ids(session),
                          "cxs_path": None, "capped": False, "save_error": "",
                          "new_model_ids": [], "only_opens": True, "any_activity": False}
    atoms = _atom_count(session)
    if os.environ.get("PELLAEON_CHECKPOINTS", "").lower() in ("off", "0", "no"):
        cp["capped"] = True                      # evaluation runs: no per-request session files
    elif atoms > CHECKPOINT_ATOM_CAP or getattr(session, "_pellaeon_checkpoint_slow", False):
        cp["capped"] = True
    elif not cp["models_before"]:
        pass                                     # empty session: the model ids say it all, nothing to snapshot
    else:
        try:
            from chimerax.core.commands import run
            os.makedirs(cache_dir, exist_ok=True)
            path = os.path.join(cache_dir, "checkpoint_%d.cxs" % int(cp["ts"] * 1000))
            t0 = time.time()
            run(session, "save %s" % _quote_path(path), log=False)
            if time.time() - t0 > CHECKPOINT_SLOW_SECONDS:
                session._pellaeon_checkpoint_slow = True   # big session: do not pay this on every request
            cp["cxs_path"] = path if os.path.exists(path) else None
            if cp["cxs_path"] is None:
                cp["save_error"] = "the session file was not written"
        except Exception as e:  # noqa: BLE001
            cp["save_error"] = str(e)
    cps.append(cp)
    while len(cps) > MAX_CHECKPOINTS:
        old = cps.pop(0)
        p = old.get("cxs_path")
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    return cp


def note_checkpoint_activity(session, commands: List[str], results: List[Dict[str, Any]]) -> None:
    """After a batch of commands ran: update the current checkpoint with what it did, so
    undo_last_request can tell a request that only opened models (cheap to undo: just close
    them) from one that changed anything else (needs the whole session restored)."""
    cps = getattr(session, "_pellaeon_checkpoints", None)
    if not cps:
        return
    cp = cps[-1]
    for cmd, r in zip(commands, results):
        if not r.get("ok"):
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
    cps = getattr(session, "_pellaeon_checkpoints", None)
    if cps:
        cps[-1]["only_opens"] = False
        cps[-1]["any_activity"] = True


def _undo_target(session):
    """The checkpoint to restore to (the one recorded before the last real request), or None
    when there is nothing earlier than the checkpoint just recorded for THIS request."""
    cps = _checkpoints(session)
    if len(cps) < 2:
        return None, cps
    return cps[-2], cps


def pending_undo(session) -> Dict[str, Any]:
    """What undo_last_request would do, without doing it: used to word the confirmation card."""
    target, _cps = _undo_target(session)
    if target is None:
        return {"error": "There is no earlier request to undo yet."}
    if target["only_opens"] and target["new_model_ids"]:
        return {"request": target["request"], "kind": "models"}
    if target.get("capped"):
        return {"request": target["request"], "kind": "models" if target["new_model_ids"] else "none"}
    if not target.get("cxs_path"):
        return {"request": target["request"], "kind": "none"}
    return {"request": target["request"], "kind": "session"}


def undo_last_request(session) -> Dict[str, Any]:
    """Restore ChimeraX to how it was immediately before the last request ran. `run` is imported lazily
    at each use (not at the top) so the no-op/error branches below stay callable without ChimeraX, for
    plain-Python testing of the fast-path-vs-full-restore decision."""
    target, cps = _undo_target(session)
    if target is None:
        return {"error": "There is no earlier request to undo yet."}
    models_before = _open_model_ids(session)
    open_now = {m.id_string for m in session.models.list() if m.id}

    if target["only_opens"] and target["new_model_ids"]:
        ids = sorted(set(target["new_model_ids"]) & open_now)
        cps.pop()
        if not ids:
            return {"error": "The model(s) that request opened are already closed; nothing to undo.",
                    "request": target["request"]}
        from chimerax.core.commands import run
        run(session, "close " + " ".join("#" + i for i in ids), log=False)
        models_after = _open_model_ids(session)
        return {"restored": "models", "request": target["request"], "models_before": models_before,
                "models_after": models_after,
                "summary": "Undid the last request (\"%s\"): closed the model(s) it opened (%s). Models now open: %s."
                           % (target["request"], ", ".join("#" + i for i in ids), ", ".join("#" + i for i in models_after) or "none")}

    if target.get("capped"):
        ids = sorted(set(target["new_model_ids"]) & open_now)
        cps.pop()
        if ids:
            from chimerax.core.commands import run
            run(session, "close " + " ".join("#" + i for i in ids), log=False)
            models_after = _open_model_ids(session)
            return {"restored": "models only", "request": target["request"], "models_before": models_before,
                    "models_after": models_after,
                    "summary": "The session was too large to snapshot (over %d atoms) before that request ran, so "
                               "only the model(s) it opened could be closed (%s); any color, style or view change "
                               "it made is still there."
                               % (CHECKPOINT_ATOM_CAP, ", ".join("#" + i for i in ids))}
        return {"error": "The session was too large to snapshot (over %d atoms) before that request ran, and it "
                         "did more than open a model, so it cannot be undone." % CHECKPOINT_ATOM_CAP,
                "request": target["request"]}

    path = target.get("cxs_path")
    if not path or not os.path.exists(path):
        cps.pop()
        return {"error": "The saved checkpoint for that request is missing (%s), so it cannot be restored."
                         % (target.get("save_error") or "file not found"), "request": target["request"]}
    from chimerax.core.commands import run
    run(session, "close", log=False)
    run(session, "open %s" % _quote_path(path), log=False)
    cps.pop()
    models_after = _open_model_ids(session)
    return {"restored": "session", "request": target["request"], "models_before": models_before,
            "models_after": models_after,
            "summary": "Undid the last request (\"%s\"): restored the session to how it was before it ran. "
                       "Models now open: %s." % (target["request"], ", ".join("#" + i for i in models_after) or "none")}
