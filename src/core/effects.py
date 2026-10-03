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
        if bool(a.get("display")) != bool(b.get("display")):
            visible.append("%s %s" % (mid, "shown" if a.get("display") else "hidden"))
            continue
        if kind == "structure":
            _structure(mid, b, a, visible, hidden)
        elif kind == "surface":
            _surface(mid, b, a, visible, hidden)
        else:
            if a.get("digest") != b.get("digest"):
                visible.append("%s changed" % mid)
    if (after.get("selection") or 0) != (before.get("selection") or 0):
        visible.append("selection: %d -> %d atoms" % (before.get("selection") or 0, after.get("selection") or 0))
    if after.get("camera") != before.get("camera"):
        visible.append("view moved")
    return visible, hidden


def _structure(mid: str, b: Dict[str, Any], a: Dict[str, Any], visible: List[str], hidden: List[str]) -> None:
    shown_b, shown_a = int(b.get("atoms_shown") or 0), int(a.get("atoms_shown") or 0)
    rib_b, rib_a = int(b.get("ribbons_shown") or 0), int(a.get("ribbons_shown") or 0)
    if shown_a != shown_b:
        visible.append("%s atoms shown: %d -> %d" % (mid, shown_b, shown_a))
    elif a.get("atom_colors_shown") != b.get("atom_colors_shown"):
        visible.append("%s displayed atoms recoloured" % mid)
    elif a.get("atom_colors") != b.get("atom_colors"):
        hidden.append("%s: atom colours changed, but %s" % (
            mid, "its atoms are hidden" if shown_a == 0 else "only the %d displayed atoms count and their colours did not change" % shown_a))
    if shown_a == shown_b and a.get("atom_styles") != b.get("atom_styles") and shown_a:
        visible.append("%s atom style changed" % mid)
    if rib_a != rib_b:
        visible.append("%s cartoon residues shown: %d -> %d" % (mid, rib_b, rib_a))
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


_DISPLAY_WORDS = {"color", "colour", "show", "hide", "cartoon", "ribbon", "surface", "style", "transparency", "size", "rainbow",
                  "display", "~display", "~show", "~cartoon", "~ribbon", "~surface", "label", "~label", "2dlabels", "key", "set",
                  "lighting", "material", "graphics", "view", "turn", "move", "roll", "zoom", "align", "matchmaker", "mm", "fitmap",
                  "clip", "~clip", "coulombic", "mlp", "preset", "nucleotides", "cofr", "camera", "volume", "morph", "close",
                  "open", "delete", "swapaa", "addh", "~label", "interfaces", "hbonds", "contacts", "clashes", "distance", "~distance"}


def summary(commands: List[str], before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The 'visible' field of a tool result: what changed on screen, and when nothing did for a batch that was
    meant to change something, why (with the fix for the usual cause)."""
    vis, hid = describe(before, after)
    out: Dict[str, Any] = {}
    words = {c.split()[0].lower() for c in commands if c.strip()}
    # a batch that restricted itself to atoms (`target a`): the one case where a hidden-only change is a mistake
    # worth naming next to a visible one. A plain `color #1/A red` colours the cartoon too; its hidden atoms are no news.
    atoms_only = any(_TARGET_ATOMS_RE.search(c) for c in commands)
    if vis:
        out["visible"] = "; ".join(vis[:8])
        if hid and atoms_only:
            out["visible"] += ". Not visible: " + "; ".join(hid[:4]) + "."
    elif words & _DISPLAY_WORDS:
        if hid:
            out["visible"] = "No visible change: " + "; ".join(hid[:4]) + "."
        elif not any(c.split()[0].lower() in ("set", "lighting", "material", "graphics", "camera", "cofr") for c in commands):
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
    return out
