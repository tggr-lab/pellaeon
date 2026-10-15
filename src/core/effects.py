"""What a batch of commands changed on screen, from a snapshot taken before and after it.

The snapshot (built in the ChimeraX edition by analysis.visible_snapshot) is a plain dict, so the comparison
here runs without ChimeraX and is unit-tested on plain Python. ChimeraX accepts many commands that change
nothing visible (colouring hidden atoms, a spec that matches nothing, a style on a surface that is not
there); this tells the model so, in the tool result, instead of letting it report success."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_TARGET_ATOMS_RE = re.compile(r"\btarget\s+(?=[abfmpr]*$)[abfmpr]*a[abfmpr]*\b", re.I)   # target a / ab, not ac, acs

# the key a snapshot entry carries for each digest, and the plain words for a change in it
_ATOMIC_KEYS = ("atoms_shown", "atom_colors_shown", "atom_colors", "atom_styles", "ribbons_shown", "ribbon_colors_shown",
                "ribbon_colors", "position", "coords", "labels")


def _kind(e: Dict[str, Any]) -> str:
    return str(e.get("kind") or "")


def describe(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """(visible changes, invisible-only changes), each a list of short phrases. Empty lists = nothing changed."""
    if not before or not after:
        return [], []
    v2 = before.get("v") == 2 and after.get("v") == 2
    b_models = before.get("models") or {}
    a_models = after.get("models") or {}
    visible: List[str] = []
    hidden: List[str] = []
    for mid in a_models:
        if mid not in b_models:
            visible.append("%s opened (%s)" % (mid, _kind(a_models[mid]) or "model"))
    for mid in b_models:
        if mid not in a_models:
            visible.append("%s closed" % mid)
    for mid, a in a_models.items():
        b = b_models.get(mid)
        if b is None:
            continue
        kind = _kind(a)
        if v2 and kind != "surface":       # a surface's own visibility includes its mask (see _surface)
            vb, va = bool(b.get("visible", b.get("display"))), bool(a.get("visible", a.get("display")))
            if va != vb:
                visible.append("%s %s" % (mid, "shown" if va else "hidden"))
                continue
            if bool(a.get("display")) != bool(b.get("display")):
                hidden.append("%s %s, but a parent model is hidden" % (mid, "shown" if a.get("display") else "hidden"))
                continue
            if not va:
                # a model that is not drawn: whatever changed in it is not on screen
                if {k: v for k, v in a.items() if k not in ("display", "visible")} != {k: v for k, v in b.items() if k not in ("display", "visible")}:
                    hidden.append("%s changed, but the model is hidden" % mid)
                continue
        elif bool(a.get("display")) != bool(b.get("display")):
            visible.append("%s %s" % (mid, "shown" if a.get("display") else "hidden"))
            continue
        if kind == "structure":
            _structure(mid, b, a, visible, hidden)
        elif kind == "surface":
            _surface(mid, b, a, visible, hidden)
        elif kind == "pseudobonds":
            if a.get("shown") != b.get("shown"):
                visible.append("%s pseudobonds shown: %s -> %s" % (mid, b.get("shown"), a.get("shown")))
            elif any(a.get(k) != b.get(k) for k in ("shown_id", "colors", "radii", "dashes")):
                visible.append("%s pseudobonds changed" % mid)
        elif kind == "2dlabel":
            if a.get("digest") != b.get("digest"):
                visible.append("2D label %s changed" % mid)
        else:
            if a.get("digest") != b.get("digest"):
                visible.append("%s changed" % mid)
    sel_b, sel_a = before.get("selection") or 0, after.get("selection") or 0
    if sel_a != sel_b:
        visible.append("selection: %d -> %d atoms" % (sel_b, sel_a))
    elif v2 and after.get("selection_id") != before.get("selection_id"):
        visible.append("selection changed: %d atoms, different ones" % sel_a)
    if after.get("camera") != before.get("camera"):
        visible.append("view moved")
    return visible, hidden


def _same_mask(b: Dict[str, Any], a: Dict[str, Any], key: str) -> bool:
    """Are the shown masks the same? A mask over a different number of atoms or residues (added, deleted,
    swapped) is not compared: the coordinates line reports that change."""
    mb, ma = str(b.get(key) or ""), str(a.get(key) or "")
    if mb.rsplit(":", 1)[-1] != ma.rsplit(":", 1)[-1]:
        return True
    return mb == ma


def _structure(mid: str, b: Dict[str, Any], a: Dict[str, Any], visible: List[str], hidden: List[str]) -> None:
    shown_b, shown_a = int(b.get("atoms_shown") or 0), int(a.get("atoms_shown") or 0)
    rib_b, rib_a = int(b.get("ribbons_shown") or 0), int(a.get("ribbons_shown") or 0)
    same_atoms = shown_a == shown_b and _same_mask(b, a, "atoms_shown_id")
    if shown_a != shown_b:
        visible.append("%s atoms shown: %d -> %d" % (mid, shown_b, shown_a))
    elif not same_atoms:
        visible.append("%s atoms shown: %d, different ones" % (mid, shown_a))
    elif a.get("atom_colors_shown") != b.get("atom_colors_shown"):
        visible.append("%s displayed atoms recoloured" % mid)
    elif a.get("atom_colors") != b.get("atom_colors"):
        hidden.append("%s: atom colours changed, but %s" % (
            mid, "its atoms are hidden" if shown_a == 0 else "only the %d displayed atoms count and their colours did not change" % shown_a))
    if same_atoms and shown_a and a.get("atom_styles") != b.get("atom_styles"):
        visible.append("%s atom style changed" % mid)
    if same_atoms and shown_a and a.get("atom_radii_shown") != b.get("atom_radii_shown"):
        visible.append("%s atom size changed" % mid)
    same_rib = rib_a == rib_b and _same_mask(b, a, "ribbons_shown_id")
    if rib_a != rib_b:
        visible.append("%s cartoon residues shown: %d -> %d" % (mid, rib_b, rib_a))
    elif not same_rib:
        visible.append("%s cartoon residues shown: %d, different ones" % (mid, rib_a))
    elif a.get("ribbon_colors_shown") != b.get("ribbon_colors_shown"):
        visible.append("%s cartoon recoloured" % mid)
    elif a.get("ribbon_colors") != b.get("ribbon_colors"):
        hidden.append("%s: cartoon colours changed, but its cartoon is hidden" % mid)
    if shown_a == 0 and rib_a == 0 and (shown_b or rib_b) and not a.get("surface_shown"):
        visible.append("%s now shows nothing: no atoms and no cartoon are displayed" % mid)
    if a.get("position") != b.get("position"):
        visible.append("%s moved" % mid)
    elif a.get("coords") != b.get("coords"):
        visible.append("%s coordinates changed" % mid)
    if a.get("labels") != b.get("labels"):
        visible.append("%s labels: %s -> %s" % (mid, b.get("labels"), a.get("labels")) if "labels_id" in a else "%s labels changed" % mid)
    elif a.get("labels_id") != b.get("labels_id"):
        visible.append("%s labels changed" % mid)


def _surface(mid: str, b: Dict[str, Any], a: Dict[str, Any], visible: List[str], hidden: List[str]) -> None:
    if bool(a.get("visible")) != bool(b.get("visible")):
        visible.append("surface %s %s" % (mid, "shown" if a.get("visible") else "hidden"))
        return
    if not a.get("visible"):
        if a.get("colors") != b.get("colors") or a.get("style") != b.get("style"):
            hidden.append("surface %s changed, but it is hidden" % mid)
        return
    if a.get("style") != b.get("style"):
        visible.append("surface %s style: %s -> %s" % (mid, b.get("style"), a.get("style")))
    if a.get("colors") != b.get("colors"):
        visible.append("surface %s recoloured" % mid)
    if a.get("transparency") != b.get("transparency"):
        visible.append("surface %s transparency changed" % mid)
    if a.get("mask") != b.get("mask"):
        visible.append("surface %s shown part changed" % mid)


_DISPLAY_WORDS = {"color", "colour", "show", "hide", "cartoon", "ribbon", "surface", "style", "transparency", "size", "rainbow",
                  "display", "~display", "~show", "~cartoon", "~ribbon", "~surface", "label", "~label", "2dlabels", "key", "set",
                  "lighting", "material", "graphics", "view", "turn", "move", "roll", "zoom", "align", "matchmaker", "mm", "fitmap",
                  "clip", "~clip", "coulombic", "mlp", "preset", "nucleotides", "cofr", "camera", "volume", "morph", "close",
                  "open", "delete", "swapaa", "addh", "~label", "interfaces", "hbonds", "contacts", "clashes", "distance", "~distance"}

# commands whose effect on screen the snapshot measures (analysis.visible_snapshot): which models exist and are
# drawn, atoms / cartoon / labels / surfaces / pseudobonds / volumes / 2D labels and their colours, positions,
# coordinates, the selection and the camera. The rest (lighting, background, silhouettes, clipping, cartoon
# geometry, keys, nucleotide styles, stick radii) is not measured, so no batch of them is called unchanged.
_MEASURED_WORDS = {"color", "colour", "show", "hide", "display", "~display", "~show", "rainbow", "transparency", "style",
                   "surface", "~surface", "label", "~label", "2dlabels", "view", "turn", "move", "roll", "zoom", "align",
                   "matchmaker", "mm", "fitmap", "coulombic", "mlp", "volume", "morph", "close", "open", "delete", "swapaa",
                   "addh", "interfaces", "hbonds", "contacts", "clashes", "distance", "~distance", "select", "combine"}
_CARTOON_SHOWN_RE = re.compile(r"^\s*~?(cartoon|ribbon)\s*(?!style\b|tether\b|byattr\w*\b|suppress\b)(hide\b|\S|$)", re.I)


def measured(command: str) -> bool:
    """Is what this command changes on screen covered by the snapshot?"""
    c = command.strip()
    w = c.split()[0].lower() if c else ""
    if w in ("cartoon", "ribbon", "~cartoon", "~ribbon"):
        return bool(_CARTOON_SHOWN_RE.match(c))
    if w == "size":
        return bool(re.search(r"\batomRadius\b", c, re.I))
    if w in ("view", "surface") and len(c.split()) > 1 and c.split()[1].lower() in ("name", "list", "delete", "dust", "cap"):
        return False
    return w in _MEASURED_WORDS


def summary(commands: List[str], before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The 'visible' field of a tool result: what changed on screen, and when nothing did for a batch that was
    meant to change something, why (with the fix for the usual cause)."""
    vis, hid = describe(before, after)
    out: Dict[str, Any] = {}
    words = {c.split()[0].lower() for c in commands if c.strip()}
    # a batch that restricted itself to atoms (`target a`): the one case where a hidden-only change is a mistake
    # worth naming next to a visible one. A plain `color #1/A red` colours the cartoon too; its hidden atoms are no news.
    atoms_only = any(_TARGET_ATOMS_RE.search(c) for c in commands)
    v2 = bool(before and after and before.get("v") == 2 and after.get("v") == 2)
    unmeasured = [c.strip() for c in commands if c.strip() and c.split()[0].lower() in _DISPLAY_WORDS and not measured(c)] if v2 else []
    if v2 and any(e.get("unmeasured") for e in list((after or {}).get("models", {}).values()) + list((before or {}).get("models", {}).values())):
        unmeasured.append("(a model the check could not read)")
    if vis:
        out["visible"] = "; ".join(vis[:8])
        if hid and atoms_only:
            out["visible"] += ". Not visible: " + "; ".join(hid[:4]) + "."
    elif words & _DISPLAY_WORDS:
        if unmeasured:
            # nothing measured changed, but the batch also did something the snapshot does not cover
            out["visible"] = "Not measured: %s.%s" % (", ".join("`%s`" % c[:50] for c in unmeasured[:3]),
                                                      (" Not visible: " + "; ".join(hid[:4]) + ".") if hid else "")
        elif hid:
            out["visible"] = "No visible change: " + "; ".join(hid[:4]) + "."
        elif v2 or not any(c.split()[0].lower() in ("set", "lighting", "material", "graphics", "camera", "cofr") for c in commands):
            out["visible"] = "No visible change."
    blank = [v for v in vis if "now shows nothing" in v]
    if blank:
        out["hint"] = ("The structure is now invisible: the batch hid its atoms and cartoon and showed nothing in their place. "
                       "`show <spec> atoms` (or `cartoon <spec>`) for what was meant to be seen.")
    if hid and (words & _DISPLAY_WORDS) and (atoms_only or not vis) and not blank:
        if any("atoms are hidden" in h for h in hid):
            out["hint"] = (("The structure shows its cartoon, not its atoms, so `color ... target a` changes nothing on screen. "
                            "Run the same `color` commands without `target a` (the default colours atoms, cartoon and surface), "
                            "or `show <spec> atoms` first if the atoms were meant to be seen.") if atoms_only else
                           "The coloured atoms are hidden, so nothing shows: `show <spec> atoms` first, or colour the cartoon.")
        elif any("cartoon is hidden" in h for h in hid):
            out["hint"] = "The cartoon is hidden; colour what is displayed, or show the cartoon first."
        elif any("surface" in h for h in hid):
            out["hint"] = "That surface is hidden; `surface <spec>` shows it again."
        elif any("model is hidden" in h for h in hid):
            out["hint"] = "That model is hidden as a whole, so changes to it do not show; `show <model> models` shows it."
    return out


def _key_entries(status: Optional[Dict[str, Any]]) -> List[Tuple[str, str]]:
    return [(str(e.get("color")), str(e.get("label"))) for e in (status or {}).get("entries") or []]


def stale_key(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A colour key a batch left describing colours it replaced: the same key (colours and labels) before and after,
    fewer of its colours on screen than before, and fewer than half of them now. `before` and `after` come from
    analysis.key_status. None when the key still fits, changed, or there is none."""
    ea = (after or {}).get("entries") or []
    if not ea or _key_entries(before) != _key_entries(after):
        return None
    n_before = sum(1 for e in (before or {}).get("entries") or [] if e.get("present"))
    n_after = sum(1 for e in ea if e.get("present"))
    if n_after >= n_before or 2 * n_after >= len(ea):
        return None
    names = [e.get("label") or e.get("color") for e in ea]
    return {"id": (after or {}).get("id") or "", "entries": _key_entries(after), "names": names,
            "gone": [e.get("label") or e.get("color") for e in ea if not e.get("present")],
            "present": n_after, "total": len(ea), "title": str((after or {}).get("title") or "")}


def still_stale(stale: Optional[Dict[str, Any]], now: Optional[Dict[str, Any]]) -> bool:
    """Does a key found stale earlier still stand unchanged, with fewer than half of its colours on screen?"""
    if not stale or _key_entries(now) != stale.get("entries"):
        return False
    ea = (now or {}).get("entries") or []
    return 2 * sum(1 for e in ea if e.get("present")) < len(ea)


def stale_key_text(stale: Dict[str, Any], removed: bool = False) -> str:
    """What the model is told about a stale key: in a tool result, or (removed=True) after Pellaeon deleted it."""
    what = "The color key %s (%s)" % (stale.get("id") or "", ", ".join(stale["names"][:8]))
    lost = "%d of its %d colours (%s) are no longer on any displayed structure" % (
        stale["total"] - stale["present"], stale["total"], ", ".join(stale["gone"][:8]))
    if removed:
        return ("%s was removed%s: %s, so it described the colouring this batch replaced. Say so in the reply."
                % (what, " with its title" if stale.get("title") else "", lost))
    return ("%s is still shown, but %s: it describes the colouring this batch replaced. `key delete` removes it%s; "
            "a new `key` command replaces it." % (what, lost, " (`2dlabels delete %s` its title)" % stale["title"] if stale.get("title") else ""))
