"""Request-level undo from in-memory snapshots (ChimeraX only).

Before a request runs, everything its commands can change without creating or deleting atoms is copied
into memory: per structure the atom, bond and residue arrays that ChimeraX scenes keep (colours, display,
draw mode, radii, coordinates, ribbons, rings, selection) plus secondary structure; for every model its
display, positions and name; pseudobonds, molecular surfaces (colours, masks, style, parameters), 3D labels,
2D labels, the colour key and volumes (their scene state); and the view: camera, lighting, material,
background, silhouettes, clip planes and centre of rotation.

When the request ends the same is taken again and only what differs is kept with the request, together
with the models it created. Undo puts back the earlier values only where the current value is still what
the request left, so a change made by hand after the request stays (and is reported), and closes the
models the request created. Several requests can be undone in turn, last first.

Deleting or replacing atoms, models or pseudobond groups cannot be put back from these arrays. Before a
batch with such a command runs, a session file is saved instead (see needs_session); undoing that request
restores the file and then the snapshot.
"""
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ATOM_EXTRA: Tuple[str, ...] = ()
RESIDUE_EXTRA = ("ss_types", "ss_ids")
STRUCTURE_EXTRA = ("bond_radius", "pseudobond_radius")
VOLSURF_ATTRS = ("level", "rgba", "show_mesh")
PBOND_ATTRS = ("colors", "displays", "halfbonds", "radii", "selected")
SURFACE_PARAMS = ("probe_radius", "grid_spacing", "resolution", "level", "sharp_boundaries", "visible_patches")
SURFACE_DRAWING = ("vertex_colors", "triangle_mask", "display_style", "color")
SURFACE_REFS = ("auto_remask_triangles", "auto_recolor_vertices", "_atom_patch_colors", "_atom_patch_color_mask")
LABEL_ATTRS = ("text", "offset", "color", "background", "size", "height", "font")
MAX_ENTRIES = 10
MAX_BYTES = 400 * 1024 * 1024      # the arrays kept for all entries together

# Commands that delete or replace atoms, models or pseudobond groups, or run arbitrary code: a session file
# is saved before a batch that holds one. Everything else either changes values the snapshot covers or
# creates models that undo closes. Labels, 2D labels and the colour key are restored even when deleted.
_SESSION_WORDS = {"delete", "del", "close", "swapaa", "swapna", "addh", "delh", "split", "changechains", "renumber",
                  "dockprep", "minimize", "torsion", "build", "altlocs", "bond", "runscript", "pyscript",
                  "addcharge", "addcharges", "remotecontrol"}
_REPLACING = {"hbonds": "hydrogen bonds", "contacts": "contacts", "clashes": "clashes"}


def needs_session(session, command: str) -> Optional[str]:
    """Why a command needs a session file to be undone, or None when the snapshot covers it."""
    toks = command.strip().split()
    if not toks:
        return None
    word = toks[0].lower()
    sub = toks[1].lower() if len(toks) > 1 else ""
    if word.startswith("~"):
        base = word[1:]
        if base in ("hbonds", "contacts", "clashes", "distance", "struts", "pbond", "interfaces"):
            return "removes pseudobonds"
        if base == "bond":
            return "deletes bonds"
        return None
    if word in _SESSION_WORDS:
        return "deletes or changes atoms or models"
    if word == "open" and re.search(r"\.(cxs|py|cxc|pyc)\b", command, re.I):
        return "opens a session or script"
    if word == "volume" and sub == "delete":
        return "deletes a volume"
    if word == "surface" and sub in ("close", "delete"):
        return "closes a surface"
    if word == "distance" and sub == "delete":
        return "removes pseudobonds"
    if word == "marker" and sub in ("delete", "change"):
        return "deletes or changes markers"
    if word in _REPLACING and not re.search(r"\bname\s+\S", command, re.I):
        try:   # a second `hbonds` replaces the group the first one made
            from chimerax.atomic import PseudobondGroup
            name = _REPLACING[word]
            if any(isinstance(m, PseudobondGroup) and m.name == name for m in session.models.list()):
                return "replaces the existing %s" % name
        except Exception:  # noqa: BLE001
            return "may replace pseudobonds"
    return None


# ---------------------------------------------------------------------------------------- capture

def _attr_lists():
    from chimerax.atomic import Structure
    return (tuple(Structure.ATOM_SCENE_ATTRS) + ATOM_EXTRA, tuple(Structure.BOND_SCENE_ATTRS),
            tuple(Structure.RESIDUE_SCENE_ATTRS) + RESIDUE_EXTRA, tuple(Structure.STRUCTURE_SCENE_ATTRS) + STRUCTURE_EXTRA)


def _copy(v):
    if isinstance(v, np.ndarray):
        return v.copy()
    if isinstance(v, dict):
        return {k: _copy(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_copy(x) for x in v]
    return v


def _same(a, b) -> bool:
    try:
        if a is b:
            return True
        if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
            a2, b2 = np.asarray(a), np.asarray(b)
            return a2.shape == b2.shape and bool(np.array_equal(a2, b2))
        if isinstance(a, dict) and isinstance(b, dict):
            return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
        if hasattr(a, "matrix") and hasattr(b, "matrix"):      # Place
            return bool(np.array_equal(a.matrix, b.matrix))
        if hasattr(a, "rgba") and hasattr(b, "rgba"):          # Color
            return bool(np.array_equal(a.rgba, b.rgba))
        r = a == b
        return bool(r.all()) if isinstance(r, np.ndarray) else bool(r)
    except Exception:  # noqa: BLE001
        return False


def _nbytes(v) -> int:
    if isinstance(v, np.ndarray):
        return int(v.nbytes)
    if isinstance(v, dict):
        return sum(_nbytes(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return sum(_nbytes(x) for x in v)
    return 64


def _label_state(lm) -> Dict[Any, Dict[str, Any]]:
    out = {}
    for lab in lm.labels():
        st = {a: _copy(getattr(lab, a)) for a in LABEL_ATTRS if hasattr(lab, a)}
        try:   # to find the atom or residue again after its structure was re-made from a session file
            st["_spec"] = lab.object.string(style="command")
        except Exception:  # noqa: BLE001
            st["_spec"] = None
        out[lab.object] = st
    return out


def _resolve(session, spec: Optional[str]):
    """The atom or residue a label spec names, in the models open now; None when it is not there."""
    if not spec:
        return None
    try:
        from chimerax.core.commands import atomspec
        objs = atomspec.AtomSpecArg.parse(spec, session)[0].evaluate(session)
        atoms = objs.atoms
        if not len(atoms):
            return None
        return atoms[0] if "@" in spec else atoms.unique_residues[0]
    except Exception:  # noqa: BLE001
        return None


def _pb_ends(pb):
    """Each pseudobond as the coordinates of its two atoms, smaller end first: finds it again after its
    structure was re-made from a session file, which does not keep the order of pseudobonds."""
    try:
        a1, a2 = pb.atoms
        k1, k2 = np.round(a1.coords, 3), np.round(a2.coords, 3)
        x, y = k1.T, k2.T
        swap = (x[0] > y[0]) | ((x[0] == y[0]) & ((x[1] > y[1]) | ((x[1] == y[1]) & (x[2] > y[2]))))
        lo = np.where(swap[:, None], k2, k1)
        hi = np.where(swap[:, None], k1, k2)
        return np.ascontiguousarray(np.hstack([lo, hi]))
    except Exception:  # noqa: BLE001
        return None


def _row_index(keys_from, keys_to):
    """For each key of keys_from, its row in keys_to (or -1). Keys are ints or rows of numbers."""
    def as_keys(k):
        k = np.ascontiguousarray(k)
        return [r.tobytes() for r in k] if k.ndim > 1 else list(k.tolist())
    where = {k: i for i, k in enumerate(as_keys(keys_to))}
    return np.array([where.get(k, -1) for k in as_keys(keys_from)], int)


def _model_kind(m) -> str:
    from chimerax.atomic import Structure, PseudobondGroup, MolecularSurface
    name = m.__class__.__name__
    if isinstance(m, Structure):
        return "structure"
    if name == "ObjectLabels":
        return "labels3d"
    if isinstance(m, PseudobondGroup):
        return "pbonds"
    if isinstance(m, MolecularSurface):
        return "molsurf"
    if name == "ColorKeyModel":
        return "key"
    if name == "LabelModel":
        return "label2d"
    if name in ("Labels",):
        return "label2d_parent"
    if hasattr(m, "matrix_value_statistics") and hasattr(m, "surfaces"):
        return "volume"
    if name == "VolumeSurface":
        return "volsurf"
    if name == "VolumeImage":
        return "volume_part"         # drawn from its volume's state
    return "model"


def _scene_state(session, m):
    from chimerax.core.state import State
    from chimerax.scenes.scene import md_scene_implementation
    cls = md_scene_implementation(m)
    if cls is None:
        return None
    try:
        return _copy(cls.take_snapshot(m, session, State.SCENE))
    except Exception:  # noqa: BLE001
        return None


def capture(session) -> Dict[str, Any]:
    """Everything a request can change without creating or deleting atoms, as copies in memory."""
    from chimerax.core.state import State
    from chimerax.graphics.gsession import ViewState
    atom_attrs, bond_attrs, res_attrs, st_attrs = _attr_lists()
    t0 = time.time()
    models: Dict[int, Dict[str, Any]] = {}
    for m in session.models.list():
        kind = _model_kind(m)
        e: Dict[str, Any] = {"model": m, "id": m.id_string, "kind": kind, "class": m.__class__.__name__,
                             "display": bool(m.display), "positions": m.positions.array().copy(), "name": m.name}
        try:
            if kind == "structure":
                atoms, bonds, res = m.atoms, m.bonds, m.residues
                e["counts"] = (len(atoms), len(bonds), len(res))
                e["ptrs"] = {"atoms": atoms.pointers.copy(), "bonds": bonds.pointers.copy(), "residues": res.pointers.copy()}
                e["arrays"] = {}
                for group, coll, attrs in (("atoms", atoms, atom_attrs), ("bonds", bonds, bond_attrs),
                                           ("residues", res, res_attrs)):
                    for a in attrs:
                        e["arrays"][(group, a)] = np.asarray(getattr(coll, a)).copy()
                e["structure"] = {a: _copy(getattr(m, a)) for a in st_attrs if hasattr(m, a)}
                e["ribbon"] = _copy(m.ribbon_xs_mgr.take_snapshot(session, State.SCENE))
            elif kind == "pbonds":
                pb = m.pseudobonds
                e["pb_ptrs"] = pb.pointers.copy()
                e["pb_ends"] = _pb_ends(pb)
                e["arrays"] = {("pbonds", a): np.asarray(getattr(pb, a)).copy() for a in PBOND_ATTRS}
                e["dashes"] = m.dashes
                e["selected"] = None
            elif kind == "labels3d":
                e["labels"] = _label_state(m)
                e["parent"] = m.parent
            elif kind == "molsurf":
                e["params"] = {a: _copy(getattr(m, a)) for a in SURFACE_PARAMS}
                e["show_atoms"] = m.show_atoms
                e["geometry"] = (m.vertices, m.normals, m.triangles)      # replaced, not changed in place, on recompute
                e["drawing"] = {a: _copy(getattr(m, a)) for a in SURFACE_DRAWING}
                e["refs"] = {a: getattr(m, a, None) for a in SURFACE_REFS}
            elif kind == "volsurf":
                e["attrs"] = {a: _copy(getattr(m, a)) for a in VOLSURF_ATTRS}
                e["drawing"] = {a: _copy(getattr(m, a, None)) for a in ("triangle_mask",)}
                e["refs"] = {a: getattr(m, a, None) for a in ("auto_remask_triangles",)}
            elif kind == "key":
                e["attrs"] = {a: _copy(getattr(m, a)) for a in m.session_attrs}
            elif kind == "label2d":
                e["scene"] = _scene_state(session, m)
                e["snapshot"] = _copy(m.take_snapshot(session, State.SESSION).get("label state"))
            elif kind in ("volume", "model", "label2d_parent"):
                e["scene"] = _scene_state(session, m)
                if kind == "model" and hasattr(m, "vertex_colors"):
                    e["drawing"] = {a: _copy(getattr(m, a, None)) for a in SURFACE_DRAWING}
                    e["refs"] = {a: getattr(m, a, None) for a in SURFACE_REFS}
        except Exception as ex:  # noqa: BLE001
            e["error"] = "%s: %s" % (ex.__class__.__name__, ex)
        models[id(m)] = e
    view = session.main_view
    vs = ViewState.take_snapshot(view, session, State.SCENE)
    vs.pop("window_size", None)
    snap = {"models": models, "view": _copy(vs), "camera_class": view.camera.name, "ts": time.time()}
    snap["seconds"] = time.time() - t0
    return snap


# ---------------------------------------------------------------------------------------- diff

def diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """What a request changed: only the parts that differ, as (before, after) pairs, the models it created
    and what it destroyed that the arrays cannot bring back."""
    entry: Dict[str, Any] = {"changes": [], "new_models": [], "lost": [], "bytes": 0}
    bm, am = before["models"], after["models"]
    for key, a in am.items():
        if key in bm or a["kind"] == "volume_part":
            continue
        if a["kind"] == "labels3d":            # first labels on a structure: removed label by label
            entry["changes"].append({"kind": "labels3d", "id": a["id"], "model": a["model"], "parent": a["parent"],
                                     "parts": {"labels": ({}, a["labels"])}})
        else:
            entry["new_models"].append((a["model"], a["id"], a["class"]))
    for key, b in bm.items():
        a = am.get(key)
        kind = b["kind"]
        if a is None:
            if kind in ("labels3d", "key", "label2d"):
                entry["changes"].append({"kind": kind, "id": b["id"], "before": b, "after": None})
            elif kind not in ("volume_part", "label2d_parent", "volsurf"):
                entry["lost"].append("%s #%s was closed" % (b["class"], b["id"]))
            continue
        ch: Dict[str, Any] = {"kind": kind, "id": b["id"], "model": b["model"], "parts": {}}
        for k in ("display", "positions", "name"):
            if not _same(b[k], a[k]):
                ch["parts"][k] = (b[k], a[k])
        if kind == "structure":
            same = {g: _same(b["ptrs"][g], a["ptrs"][g]) for g in b["ptrs"]}
            if not same["atoms"]:
                added = not np.isin(a["ptrs"]["atoms"], b["ptrs"]["atoms"]).all()
                entry["lost"].append("atoms of #%s were %s" % (b["id"], "added or replaced" if added else "deleted"))
            for k, v in b["arrays"].items():
                g = k[0]
                if same[g]:
                    if not _same(v, a["arrays"][k]):
                        ch["parts"][k] = (v, a["arrays"][k])
                elif np.isin(a["ptrs"][g], b["ptrs"][g]).all():
                    # only deletions: the objects that are left keep their order, so the earlier values of
                    # those can still be put back (found by pointer when restoring)
                    keep = np.isin(b["ptrs"][g], a["ptrs"][g])
                    if not _same(v[keep], a["arrays"][k]):
                        ch["parts"][k] = (v[keep], a["arrays"][k], a["ptrs"][g])
            for k, v in b["structure"].items():
                if not _same(v, a["structure"].get(k)):
                    ch["parts"][("structure", k)] = (v, a["structure"].get(k))
            if not _same(b["ribbon"], a["ribbon"]):
                ch["parts"]["ribbon"] = (b["ribbon"], a["ribbon"])
        elif kind == "pbonds":
            bp, ap = b["pb_ptrs"], a["pb_ptrs"]
            if not np.isin(bp, ap).all():
                entry["lost"].append("pseudobonds of #%s were deleted" % b["id"])
            else:
                ch["pb_ptrs"] = (bp, ap)
                ch["pb_ends"] = (b["pb_ends"], a["pb_ends"])
                for k, v in b["arrays"].items():
                    av = a["arrays"][k]
                    if len(bp) == len(ap) and _same(v, av):
                        continue
                    ch["parts"][k] = (v, av)
                if len(ap) > len(bp):
                    ch["parts"]["added"] = (None, ap[~np.isin(ap, bp)])
            if b["dashes"] != a["dashes"]:
                ch["parts"]["dashes"] = (b["dashes"], a["dashes"])
        elif kind == "labels3d":
            if not _same(b["labels"], a["labels"]):
                ch["parts"]["labels"] = (b["labels"], a["labels"])
                ch["parent"] = b["parent"]
        elif kind == "molsurf":
            for grp in ("params", "drawing"):
                for k, v in b[grp].items():
                    if not _same(v, a[grp][k]):
                        ch["parts"][(grp, k)] = (v, a[grp][k])
            if any(x is not y for x, y in zip(b["geometry"], a["geometry"])):
                ch["parts"]["geometry"] = (b["geometry"], a["geometry"])
                ch["parts"]["show_atoms"] = (b["show_atoms"], a["show_atoms"])
            for k, v in b["refs"].items():
                if v is not a["refs"][k]:
                    ch["parts"][("refs", k)] = (v, a["refs"][k])
        elif kind in ("key", "volsurf"):
            for k, v in b["attrs"].items():
                if not _same(v, a["attrs"].get(k)):
                    ch["parts"][("attrs", k)] = (v, a["attrs"].get(k))
            for grp in ("drawing", "refs"):
                for k, v in (b.get(grp) or {}).items():
                    av = (a.get(grp) or {}).get(k)
                    if (grp == "refs" and v is not av) or (grp == "drawing" and not _same(v, av)):
                        ch["parts"][(grp, k)] = (v, av)
        elif kind in ("label2d", "volume", "model", "label2d_parent"):
            if b.get("scene") is not None and not _same(b["scene"], a.get("scene")):
                ch["parts"]["scene"] = (b["scene"], a.get("scene"))
            for grp in ("drawing", "refs"):
                for k, v in (b.get(grp) or {}).items():
                    av = (a.get(grp) or {}).get(k)
                    if (grp == "refs" and v is not av) or (grp == "drawing" and not _same(v, av)):
                        ch["parts"][(grp, k)] = (v, av)
        if b.get("error") or a.get("error"):
            entry["lost"].append("#%s could not be read (%s)" % (b["id"], b.get("error") or a.get("error")))
        if ch["parts"]:
            entry["changes"].append(ch)
    vb, va = before["view"], after["view"]
    vparts = {}
    for k in set(vb) | set(va):
        if k == "version":
            continue
        if not _same(vb.get(k), va.get(k)):
            vparts[k] = (vb.get(k), va.get(k))
    if "center_of_rotation" in vparts:          # the point means something only with the method that made it
        vparts["center_of_rotation_method"] = (vb.get("center_of_rotation_method"), va.get("center_of_rotation_method"))
    if before.get("camera_class") != after.get("camera_class"):
        vparts["camera_class"] = (before.get("camera_class"), after.get("camera_class"))
    if vparts:
        entry["changes"].append({"kind": "view", "id": "", "parts": vparts})
    entry["bytes"] = sum(_nbytes(p) for c in entry["changes"] for p in c.get("parts", {}).values()
                         if c["kind"] != "labels3d")
    entry["empty"] = not (entry["changes"] or entry["new_models"] or entry["lost"])
    return entry


# ---------------------------------------------------------------------------------------- restore

_WORDS = {"colors": "colours", "displays": "display", "draw_modes": "style", "radii": "size", "selected": "selection",
          "coords": "coordinates", "ribbon_colors": "cartoon colours", "ribbon_displays": "cartoon display",
          "ring_colors": "ring colours", "ring_displays": "ring display", "halfbonds": "bond colouring",
          "ss_types": "secondary structure", "ss_ids": "secondary structure"}


class _Report:
    def __init__(self):
        self.restored: List[str] = []
        self.kept: List[str] = []          # changed by hand after the request: left as it is
        self.failed: List[str] = []
        self.closed: List[str] = []

    def as_dict(self):
        return {"restored": self.restored, "kept": self.kept, "failed": self.failed, "closed": self.closed}


def _merge_rows(cur, before, after):
    """Three-way merge of per-object arrays: rows still as the request left them get the earlier value.
    Returns (new array or None when nothing to change, number of rows kept because they changed since)."""
    cur = np.asarray(cur)
    if cur.shape != after.shape or cur.shape != before.shape:
        return None, -1
    if cur.ndim == 1:
        same_after = cur == after
        differs = before != after
    else:
        same_after = (cur == after).reshape(len(cur), -1).all(axis=1)
        differs = ~(before == after).reshape(len(cur), -1).all(axis=1)
    put = same_after & differs
    kept = int((~same_after & differs).sum())
    if not put.any():
        return None, kept
    new = cur.copy()
    new[put] = before[put]
    return new, kept


def _live(m) -> bool:
    return m is not None and not getattr(m, "deleted", False) and getattr(m, "id", None) is not None


def _find(session, ch, by_id: bool):
    m = ch.get("model")
    if not by_id:
        # the object itself or nothing: a model closed since the request is gone, and one reopened under the
        # same id is another model, not a place to merge the old values into
        return m if _live(m) else None
    for x in session.models.list():
        if x.id_string == ch["id"] and x.__class__.__name__ == (m.__class__.__name__ if m is not None else ch.get("class")):
            return x
    return None


def _restore_structure(session, m, ch, rep, force):
    label = "#%s" % ch["id"]
    parts = ch["parts"]
    for key, val in parts.items():             # structure-level first: the active coordinate set before coordinates
        if isinstance(key, tuple) and key[0] == "structure":
            b, a = val
            cur = getattr(m, key[1])
            if force or _same(cur, a):
                setattr(m, key[1], b)
            elif not _same(cur, b):
                rep.kept.append("%s %s" % (label, key[1].replace("_", " ")))
    order = sorted((k for k in parts if isinstance(k, tuple) and k[0] in ("atoms", "bonds", "residues")),
                   key=lambda k: (k[0] != "residues", k[1] != "ss_ids"))     # ss ids before ss types
    colls = {"atoms": m.atoms, "bonds": m.bonds, "residues": m.residues}
    for key in order:
        b, a = parts[key][:2]
        coll = colls[key[0]]
        if len(parts[key]) == 3:               # some objects were deleted: the ones left, by pointer
            ptrs = parts[key][2]
            cur_ptrs = coll.pointers
            if len(cur_ptrs) != len(ptrs) or not np.array_equal(cur_ptrs, ptrs):
                rep.failed.append("%s %s (changed again since)" % (label, key[0]))
                continue
        cur = np.asarray(getattr(coll, key[1]))
        if force:
            new, kept = (b if cur.shape == b.shape else None), 0
            if new is None:
                rep.failed.append("%s %s (sizes differ)" % (label, key[0]))
                continue
        else:
            new, kept = _merge_rows(cur, b, a)
            if kept < 0:
                rep.failed.append("%s %s (sizes differ)" % (label, key[0]))
                continue
        if kept > 0:
            rep.kept.append("%s %s of %d %s" % (label, _WORDS.get(key[1], key[1].replace("_", " ")), kept,
                                                key[0] if kept > 1 else key[0][:-1]))
        if new is not None:
            setattr(coll, key[1], new)
    if "ribbon" in parts:
        b, a = parts["ribbon"]
        try:
            m.ribbon_xs_mgr.set_state_from_snapshot(session, _copy(b))
        except Exception as e:  # noqa: BLE001
            rep.failed.append("%s cartoon style (%s)" % (label, e))


def _restore_model_attrs(m, ch, rep, force):
    from chimerax.geometry import Places
    for k in ("display", "positions", "name"):
        if k not in ch["parts"]:
            continue
        b, a = ch["parts"][k]
        cur = m.positions.array() if k == "positions" else getattr(m, k)
        if force or _same(cur, a):
            if k == "positions":
                m.positions = Places(place_array=b.copy())
            else:
                setattr(m, k, b)
        elif not _same(cur, b):
            rep.kept.append("#%s %s" % (ch["id"], "position" if k == "positions" else k))


def _restore_pbonds(m, ch, rep, force, by_id=False):
    """Pseudobonds are matched by pointer, or by their end atoms after a session file was reopened."""
    parts = ch["parts"]
    (bp, ap), (be, ae) = ch["pb_ptrs"], ch.get("pb_ends") or (None, None)
    if by_id and (be is None or ae is None):
        rep.failed.append("#%s pseudobonds" % ch["id"])
        return
    pb = m.pseudobonds
    cur_keys = _pb_ends(pb) if by_id else pb.pointers
    if "added" in parts:                  # pseudobonds the request made in a group that existed
        added = ae[~np.isin(ap, bp)] if by_id else parts["added"][1]
        rows = _row_index(added, cur_keys)
        if (rows >= 0).any():
            pb.filter(rows[rows >= 0]).delete()
            pb = m.pseudobonds
            cur_keys = _pb_ends(pb) if by_id else pb.pointers
    if not (any(isinstance(k, tuple) for k in parts) and len(bp)):
        pass
    else:
        cur_rows = _row_index(be if by_id else bp, cur_keys)
        if (cur_rows < 0).any():
            rep.failed.append("#%s pseudobonds (deleted since)" % ch["id"])
        else:
            sub = pb.filter(cur_rows)                    # current pseudobonds in snapshot order
            after_rows = _row_index(bp, ap)              # the request only added: every earlier one is still there
            for key, (b, a) in parts.items():
                if not (isinstance(key, tuple) and key[0] == "pbonds"):
                    continue
                a_sub = np.asarray(a)[after_rows]
                curv = np.asarray(getattr(sub, key[1]))
                new, kept = (b, 0) if force else _merge_rows(curv, b, a_sub)
                if kept > 0:
                    rep.kept.append("#%s %s of %d pseudobonds" % (ch["id"], _WORDS.get(key[1], key[1]), kept))
                if new is not None and kept >= 0:
                    setattr(sub, key[1], new)
    if "dashes" in parts:
        b, a = parts["dashes"]
        if force or m.dashes == a:
            m.dashes = b


def _restore_labels3d(session, ch, rep, force, by_id):
    """Labels are put back object by object: removed when the request added them, re-made when it deleted
    them, reset when it changed them, unless they changed again since."""
    from chimerax.label.label3d import labels_model, label_class
    if "parts" in ch:
        b, a = ch["parts"]["labels"]
        parent = ch.get("parent")
    else:                                  # the whole label model was closed by the request
        b, a = ch["before"]["labels"], {}
        parent = ch["before"]["parent"]
    if by_id:                              # the structure was re-made from a session file: find it and its atoms again
        parent = _find(session, {"id": ch["id"].rsplit(".", 1)[0], "model": parent}, True)
        b = {x: st for x, st in ((_resolve(session, st.get("_spec")), st) for st in b.values()) if x is not None}
        a = {x: st for x, st in ((_resolve(session, st.get("_spec")), st) for st in a.values()) if x is not None}
    if parent is None or getattr(parent, "deleted", False):
        rep.failed.append("labels of #%s" % ch["id"].rsplit(".", 1)[0])
        return
    lm = labels_model(parent, create=False)
    cur = _label_state(lm) if lm is not None else {}
    view = session.main_view
    remove, put = [], []
    for obj in set(b) | set(a):
        if getattr(obj, "deleted", False):
            continue
        c = cur.get(obj)
        if not force and not _same(c, a.get(obj)):
            if not _same(c, b.get(obj)):
                rep.kept.append("label on %s" % obj)
            continue
        if obj not in b:
            if c is not None:
                remove.append(obj)
        else:
            put.append(obj)
    if remove and lm is not None:
        lm.delete_labels(remove)
    if put:
        lm = labels_model(parent, create=True)
        for obj in put:
            lm.add_labels([obj], label_class(obj), view, settings={k: v for k, v in b[obj].items() if not k.startswith("_")})


def _restore_key(session, ch, rep, force):
    from chimerax.color_key.model import get_model
    if "parts" not in ch:                  # deleted by the request
        key = get_model(session, create=False)
        if key is not None and not force:
            rep.kept.append("colour key (made again since)")
            return
        if key is None:                    # made again under its old model number when that is free
            key = get_model(session, add_created=False)
            old = tuple(int(x) for x in ch["id"].split("."))
            if not any(m.id == old for m in session.models.list()):
                key.id = old
            session.models.add([key], root_model=True)
        for k, v in ch["before"]["attrs"].items():
            setattr(key, k, _copy(v))
        key.display = ch["before"]["display"]
    else:
        key = get_model(session, create=False)
        if key is None:
            rep.failed.append("colour key")
            return
        for (grp, k), (b, a) in ((k, v) for k, v in ch["parts"].items() if isinstance(k, tuple)):
            if force or _same(getattr(key, k), a):
                setattr(key, k, _copy(b))
            elif not _same(getattr(key, k), b):
                rep.kept.append("colour key %s" % k.strip("_"))
    key.needs_update = True
    key.redraw_needed()


def _restore_attrs(m, ch, rep, force):
    for key, (b, a) in ch["parts"].items():
        if isinstance(key, tuple) and key[0] == "attrs":
            cur = getattr(m, key[1])
            if force or _same(cur, a):
                setattr(m, key[1], _copy(b))
            elif not _same(cur, b):
                rep.kept.append("#%s %s" % (ch["id"], key[1].strip("_").replace("_", " ")))


def _restore_label2d(session, ch, rep, force, m):
    from chimerax.label.label2d import label_create
    if "parts" not in ch:                  # deleted by the request: made again with the same settings
        s = dict(ch["before"]["snapshot"] or {})
        params = {k: s[k] for k in ("text", "color", "size", "font", "bold", "italic", "xpos", "ypos", "visibility",
                                     "margin", "outline") if k in s}
        if s.get("background") is not None:
            params["bg_color"] = s["background"]
        try:
            from chimerax.core.colors import Color
            if params.get("color") is not None:
                params["color"] = Color(params["color"])
            if params.get("bg_color") is not None:
                params["bg_color"] = Color(params["bg_color"])
            from chimerax.label.label2d import Labels, session_labels
            if session_labels(session) is None:     # the label model under its old number when that is free
                parent = Labels(session)
                old = tuple(int(x) for x in ch["id"].split(".")[:-1])
                if old and not any(x.id == old for x in session.models.list()):
                    parent.id = old
                session.models.add([parent], root_model=True)
            label_create(session, s.get("name") or ch["before"]["name"], **params)
        except Exception as e:  # noqa: BLE001
            rep.failed.append("2D label %s (%s)" % (ch["before"]["name"], e))
        return
    cur_name = m.name
    _restore_scene_model(session, m, ch, rep, force)
    if "name" in ch["parts"] and (force or _same(cur_name, ch["parts"]["name"][1])):
        m.name = ch["parts"]["name"][0]          # label_change renamed the model after its text
    ch["parts"] = {k: v for k, v in ch["parts"].items() if k != "name"}


def _restore_scene_model(session, m, ch, rep, force):
    from chimerax.scenes.scene import md_scene_implementation
    parts = ch["parts"]
    if "scene" in parts:
        b, a = parts["scene"]
        cur = _scene_state(session, m)
        if force or _same(cur, a):
            cls = md_scene_implementation(m)
            try:
                cls.restore_scene(m, _copy(b))
            except Exception as e:  # noqa: BLE001
                rep.failed.append("#%s (%s)" % (ch["id"], e))
        elif not _same(cur, b):
            rep.kept.append("#%s" % ch["id"])
    _restore_drawing(m, ch, rep, force)


def _restore_drawing(m, ch, rep, force):
    parts = ch["parts"]
    if "geometry" in parts:
        (vb, nb, tb), _a = parts["geometry"]
        try:
            if "show_atoms" in parts:
                m.show_atoms = parts["show_atoms"][0]
            for (grp, k), (b, a) in [(k, v) for k, v in parts.items() if isinstance(k, tuple) and k[0] == "params"]:
                setattr(m, k, b)
            m.set_geometry(vb, nb, tb)
        except Exception as e:  # noqa: BLE001
            rep.failed.append("#%s surface shape (%s)" % (ch["id"], e))
    for key, (b, a) in parts.items():
        if not isinstance(key, tuple) or key[0] not in ("drawing", "refs", "params"):
            continue
        grp, k = key
        if grp == "params" and "geometry" in parts:
            continue
        cur = getattr(m, k, None)
        unchanged = (cur is a) if grp == "refs" else _same(cur, a)
        if force or unchanged or "geometry" in parts:
            try:
                setattr(m, k, _copy(b) if grp != "refs" else b)
            except Exception as e:  # noqa: BLE001
                rep.failed.append("#%s %s (%s)" % (ch["id"], k, e))
        elif not ((cur is b) if grp == "refs" else _same(cur, b)):
            rep.kept.append("#%s %s" % (ch["id"], k.strip("_").replace("_", " ")))


def _restore_view(session, ch, rep, force):
    from chimerax.graphics.gsession import CameraState, LightingState, MaterialState, ViewState
    from chimerax.core.state import State
    view = session.main_view
    cur = ViewState.take_snapshot(view, session, State.SCENE)
    parts = ch["parts"]
    cam_class = parts.get("camera_class")
    # setting the centre of rotation makes its method "fixed": the method goes after it
    for k in sorted(parts, key=lambda k: k == "center_of_rotation_method"):
        b, a = parts[k]
        if k == "camera_class":
            continue
        if k == "center_of_rotation" and parts.get("center_of_rotation_method", ("fixed",))[0] != "fixed":
            continue                       # computed from the view, not set: the method alone puts it back
        if not force and not _same(cur.get(k), a):
            if not _same(cur.get(k), b):
                rep.kept.append("view %s" % k.replace("_", " "))
            continue
        try:
            if k == "camera":
                bb = _copy(b)
                if cam_class and view.camera.name != cam_class[0]:
                    cam = CameraState.restore_snapshot(session, {"name": cam_class[0]})
                    view.camera = cam
                CameraState.restore_scene(view.camera, session, bb)
                view.camera.redraw_needed = True
            elif k == "lighting":
                LightingState.restore_scene(view.lighting, session, _copy(b))
                view.update_lighting = True
            elif k == "material":
                MaterialState.restore_scene(view.material, session, _copy(b))
                view.update_lighting = True
            elif k == "clip_planes":
                from chimerax.graphics.gsession import CameraClipPlaneState, SceneClipPlaneState
                planes = [(CameraClipPlaneState if t == "camera" else SceneClipPlaneState).restore_scene(None, session, _copy(d))
                          for t, d in b]
                view.clip_planes.replace_planes(planes)
            elif k == "silhouettes":
                for attr, v in b.items():
                    setattr(view.silhouette, attr, v)
            elif k in ("clipping_surface_caps", "clipping_cap_offset"):
                from chimerax.surface.settings import settings as surf_settings
                setattr(surf_settings, k, b)
            else:
                setattr(view, k, _copy(b))
        except Exception as e:  # noqa: BLE001
            rep.failed.append("view %s (%s)" % (k, e))
    if cam_class and "camera" not in parts and view.camera.name != cam_class[0] and \
            (force or view.camera.name == cam_class[1]):
        pos = view.camera.position
        cam = CameraState.restore_snapshot(session, {"name": cam_class[0]})
        cam.position = pos
        view.camera = cam


def restore(session, entry: Dict[str, Any], by_id: bool = False, force: bool = False) -> Dict[str, Any]:
    """Put back what a request changed (see the module notes). by_id: the models were re-made from a session
    file since the entry was taken, so they are found by model id; force: overwrite without the three-way check."""
    rep = _Report()
    closing = [m for m, mid, cls in entry["new_models"] if _live(m)] if not by_id else \
        [x for x in session.models.list() if (x.id_string, x.__class__.__name__) in
         {(mid, cls) for _m, mid, cls in entry["new_models"]}]
    closing = [m for m in closing if not any(p in closing for p in _parents(m))]
    if closing:
        rep.closed = ["#" + m.id_string for m in closing]
        session.models.close(closing)
    for ch in entry["changes"]:
        kind = ch["kind"]
        try:
            if kind == "view":
                _restore_view(session, ch, rep, force)
                continue
            if kind == "labels3d":
                _restore_labels3d(session, ch, rep, force, by_id)
                continue
            if kind == "key":
                _restore_key(session, ch, rep, force)
                continue
            if kind == "label2d" and "parts" not in ch:
                _restore_label2d(session, ch, rep, force, None)
                continue
            m = _find(session, ch, by_id)
            if m is None:
                rep.failed.append("#%s (no longer open)" % ch["id"])
                continue
            if kind == "structure":
                _restore_structure(session, m, ch, rep, force)
            elif kind == "pbonds":
                _restore_pbonds(m, ch, rep, force, by_id)
            elif kind == "molsurf":
                _restore_drawing(m, ch, rep, force)
            elif kind == "label2d":
                _restore_label2d(session, ch, rep, force, m)
            elif kind in ("volume", "model", "label2d_parent"):
                _restore_scene_model(session, m, ch, rep, force)
            elif kind == "volsurf":
                _restore_attrs(m, ch, rep, force)
                _restore_drawing(m, ch, rep, force)
            _restore_model_attrs(m, ch, rep, force)   # last: a 2D label's text also renames its model
        except Exception as e:  # noqa: BLE001
            rep.failed.append("#%s (%s: %s)" % (ch.get("id"), e.__class__.__name__, e))
    return rep.as_dict()


def _parents(m):
    out = []
    p = getattr(m, "parent", None)
    while p is not None:
        out.append(p)
        p = getattr(p, "parent", None)
    return out
