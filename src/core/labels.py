"""Readable labels: ChimeraX's label defaults (0.7 Å tall, colored like the residue, hidden behind atoms) are hard to read
in screenshots. When the request does not say otherwise, Pellaeon adds a fixed pixel size, a white background and
on-top drawing, and shortens dense label sets to one-letter codes. The command that actually runs is shown in the card."""
from __future__ import annotations

import os
import re
from typing import Optional, Tuple

SIZE = int(os.environ.get("PELLAEON_LABEL_SIZE", "16") or 16)     # pixels; the photo shoot raises it for supersampled renders

_STYLE_WORDS = ("color", "bgcolor", "size", "height", "font", "ontop", "offset", "attribute", "text")
_LEVELS = ("atoms", "residues", "pseudobonds", "bonds", "models", "structures")
_SUBCOMMANDS = ("delete", "listfonts", "orient", "defaults", "ontop", "height", "settings")
_KEYWORDS = set(_STYLE_WORDS) | set(_LEVELS)
DENSE = 20


def label_spec(command: str) -> Optional[str]:
    """The atom spec a `label` command targets, or None if the command is not a plain label command."""
    m = re.match(r"^\s*label\s+(.*)$", command, re.I)
    if not m:
        return None
    rest = m.group(1).strip()
    first = rest.split()[0].lower() if rest else ""
    if first in _SUBCOMMANDS or first in _STYLE_WORDS:
        return None
    toks = rest.split()
    spec = []
    for t in toks:
        if t.lower() in _KEYWORDS:
            break
        spec.append(t)
    return " ".join(spec) if spec else None


def style_label_command(command: str, n_targets: Optional[int] = None) -> Tuple[str, str]:
    """Return (command, note). Untouched when it is not a plain label command or already carries style options."""
    spec = label_spec(command)
    if spec is None:
        return command, ""
    low = command.lower()
    if any(re.search(r"\b%s\b" % w, low) for w in ("color", "bgcolor", "size", "height", "font", "ontop", "offset")):
        return command, ""
    has_text = re.search(r"\btext\b", low) is not None
    level_atoms = re.search(r"\batoms\b", low) is not None
    dense = n_targets is not None and n_targets > DENSE
    extra = " height fixed onTop true color black bgColor #ffffffd9"
    note = ""
    if dense and not has_text and not level_atoms:
        extra = ' text "{0.one_letter_code}{0.number}" size %d' % max(8, SIZE * 3 // 4) + extra
        note = "%d residues matched, so the labels use one-letter codes (H87) at a smaller size to limit overlap." % n_targets
    else:
        extra = " size %d" % SIZE + extra
    return command.rstrip() + extra, note


# ---------------------------------------------------------------- decluttering (pure geometry, host-independent)
def _overlap(a, b, pad=2.0):
    return not (a[0] + a[2] + pad <= b[0] or b[0] + b[2] + pad <= a[0] or a[1] + a[3] + pad <= b[1] or b[1] + b[3] + pad <= a[1])


def pack_labels(boxes, max_shift=1.4):
    """boxes: [(id, x, y, w, h)] in pixels (x,y = lower-left). Keeps every box that fits, nudges colliding ones to a free
    spot nearby (up/down/sideways, up to max_shift box heights), and drops the rest. The cap is small on
    purpose: a label two box heights from its residue has no leader line and reads as belonging to whatever
    it happens to sit on, which is worse than no label.
    Returns {"kept": [id...], "moved": {id: (dx, dy)}, "dropped": [id...]}."""
    placed = []
    kept, moved, dropped = [], {}, []
    for bid, x, y, w, h in sorted(boxes, key=lambda b: (b[2], b[1])):
        cands = [(0, 0)]
        for k in (1.0, 1.6, 2.2):
            if k > max_shift:
                break
            cands += [(0, k * h), (0, -k * h), (0.7 * w * k, 0), (-0.7 * w * k, 0), (0.5 * w * k, k * h), (-0.5 * w * k, k * h),
                      (0.5 * w * k, -k * h), (-0.5 * w * k, -k * h)]
        home = None
        for dx, dy in cands:
            box = (x + dx, y + dy, w, h)
            if not any(_overlap(box, p) for p in placed):
                home = (dx, dy)
                break
        if home is None:
            dropped.append(bid)
            continue
        placed.append((x + home[0], y + home[1], w, h))
        kept.append(bid)
        if home != (0, 0):
            moved[bid] = home
    return {"kept": kept, "moved": moved, "dropped": dropped}
