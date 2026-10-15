"""Readable labels: ChimeraX's label defaults (0.7 Å tall, colored like the residue, hidden behind atoms) are hard to read
in screenshots. When the request does not say otherwise, Pellaeon adds a fixed pixel size, a white background and
on-top drawing, and shortens dense label sets to one-letter codes. A colour key too short for its labels is lengthened.
The command that actually runs is shown in the card."""
from __future__ import annotations

import math
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


# ---------------------------------------------------------------- colour keys whose labels would overlap
_KEY_OPTIONS = {"pos", "size", "fontsize", "bold", "italic", "colortreatment", "justification", "labelside", "numericlabelspacing",
                "labelcolor", "labeloffset", "font", "border", "bordercolor", "borderwidth", "showtool", "ticks", "ticklength",
                "tickthickness"}
_TOKEN_RE = re.compile(r'(?:[^\s"\']|"[^"]*"|\'[^\']*\')+')
KEY_SIZE, KEY_POS, KEY_FONT = (0.25, 0.05), (0.7, 0.08), 24
KEY_MAX_LENGTH, KEY_MARGIN = 0.8, 0.02          # fractions of the window


def _key_parts(command: str):
    """(entries as (color, label), {option: (value, span)}) of a `key` command that lists colours, or None."""
    m = re.match(r"^\s*key\s+", command, re.I)
    if not m:
        return None
    entries, opts = [], {}
    toks = [(t.group(0), t.span()) for t in _TOKEN_RE.finditer(command, m.end())]
    i = 0
    while i < len(toks) and toks[i][0].lower() not in _KEY_OPTIONS:
        tok = toks[i][0]
        if ":" not in tok:
            return None                        # a palette or attribute name, `key delete`, `key true`
        color, label = tok.split(":", 1)
        entries.append((color, label.strip().strip("\"'")))
        i += 1
    while i + 1 < len(toks):
        name = toks[i][0].lower()
        if name not in _KEY_OPTIONS:
            return None
        opts[name] = (toks[i + 1][0], (toks[i][1][0], toks[i + 1][1][1]))
        i += 2
    return entries, opts


def _numbers(text: str, n: int, default):
    try:
        vals = tuple(float(x) for x in text.split(","))
        return vals if len(vals) == n else default
    except ValueError:
        return default


def _label_fractions(labels, distinct: bool, proportional: bool):
    """Where ChimeraX centres each label along the key, as a fraction of its length (color_key's _rect_positions)."""
    n = len(labels)
    vals = list(range(n))
    if proportional:
        try:
            nums = [float(x) for x in labels]
            if (nums == sorted(nums) or nums == sorted(nums, reverse=True)) and nums[0] != nums[-1]:
                vals = nums
        except ValueError:
            pass
    if not distinct:
        span = abs(vals[-1] - vals[0]) or 1.0
        return [abs(v - vals[0]) / span for v in vals]
    v0 = vals[0] - (vals[1] - vals[0]) / 2.0
    vn = vals[-1] + (vals[-1] - vals[-2]) / 2.0
    span = abs(vn - v0) or 1.0
    pos = [abs(v - v0) / span for v in vals]
    rect = [0.0] + [(pos[i] + pos[i + 1]) / 2.0 for i in range(n - 1)] + [1.0]
    return [(rect[i] + rect[i + 1]) / 2.0 for i in range(n)]


def label_key(command: str, values) -> Tuple[str, str]:
    """A `key` command whose colours are those of a ChimeraX palette with numeric stops, and which leaves some labels
    empty, gets the palette's stops as those labels (the ends keep what the author wrote). `values` are the stops in
    the order of the key's colours. Returns (command, note); unchanged when there is nothing to fill."""
    m = re.match(r"^\s*key\s+", command, re.I)
    if not m or not values:
        return command, ""
    toks = [(t.group(0), t.span()) for t in _TOKEN_RE.finditer(command, m.end())]
    entries = []
    for tok, span in toks:
        if tok.lower() in _KEY_OPTIONS:
            break
        if ":" not in tok:
            return command, ""
        entries.append((tok, span))
    if len(entries) != len(values):
        return command, ""
    labels = [tok.split(":", 1)[1].strip().strip("\"'") for tok, _s in entries]
    if not any(labels) or all(labels):
        return command, ""            # no labels at all is the author's choice; all present needs nothing
    out, filled = command, []
    for (tok, (a, b)), v, lab in reversed(list(zip(entries, values, labels))):
        if lab:
            continue
        text = ("%d" % round(v)) if abs(v - round(v)) < 1e-9 else ("%g" % v)
        out = out[:a] + tok.split(":", 1)[0] + ":" + text + out[b:]
        filled.append(text)
    return out, "The key's empty labels were filled from its palette: %s." % ", ".join(reversed(filled))


def fit_key(command: str, metrics) -> Tuple[str, str]:
    """A `key` command whose labels would overlap at its size, or run off the window, is lengthened (and moved in, or
    its font made smaller when even KEY_MAX_LENGTH of the window is too short). metrics(labels, font_size, font,
    bold, italic) -> {"widths": [px], "height": px, "window": [w px, h px]} measures the labels as ChimeraX draws
    them. Returns (command, note); the command is unchanged when the labels fit."""
    parts = _key_parts(command)
    if not parts:
        return command, ""
    entries, opts = parts
    labels = [lab for _c, lab in entries]
    if len(entries) < 2 or not any(labels):
        return command, ""
    size = _numbers(opts.get("size", ("", None))[0], 2, KEY_SIZE)
    pos = _numbers(opts.get("pos", ("", None))[0], 2, KEY_POS)
    try:
        font_size = int(float(opts.get("fontsize", (KEY_FONT, None))[0]))
    except ValueError:
        return command, ""
    font = opts.get("font", ("Arial", None))[0].strip("\"'")
    flag = lambda k: opts.get(k, ("false", None))[0].lower() in ("true", "t", "1", "yes")  # noqa: E731
    met = metrics(labels, font_size, font, flag("bold"), flag("italic"))
    widths, height = list(met.get("widths") or []), float(met.get("height") or 0)
    win_w, win_h = (float(x) for x in met.get("window") or (0, 0))
    if len(widths) != len(labels) or win_w <= 0 or win_h <= 0:
        return command, ""
    distinct = opts.get("colortreatment", ("blended", None))[0].lower().startswith("d")
    proportional = not opts.get("numericlabelspacing", ("proportional", None))[0].lower().startswith("e")
    frac = _label_fractions(labels, distinct, proportional)
    horizontal = size[0] * win_w > size[1] * win_h
    long_px = (size[0] * win_w) if horizontal else (size[1] * win_h)
    gap = 0.4 * height
    need = 0.0
    for i in range(len(labels) - 1):
        step = abs(frac[i + 1] - frac[i])
        if not labels[i] and not labels[i + 1]:
            continue
        want = ((widths[i] + widths[i + 1]) / 2.0 if horizontal else height) + gap
        need = max(need, want / step if step > 1e-6 else float("inf"))
    new_size, new_font = list(size), font_size
    if need > long_px:
        limit = KEY_MAX_LENGTH * (win_w if horizontal else win_h)
        if need > limit:
            new_font = max(8, int(font_size * limit / need))
            widths = [w * new_font / font_size for w in widths]
            need = limit
        i = 0 if horizontal else 1
        new_size[i] = min(KEY_MAX_LENGTH, math.ceil(round(need / (win_w if horizontal else win_h) * 100, 4)) / 100)
    new_pos = list(pos)
    if horizontal:
        length = new_size[0] * win_w
        left = max(0.0, widths[0] / 2.0 - frac[0] * length)
        right = max(0.0, widths[-1] / 2.0 - (1.0 - frac[-1]) * length)
        if pos[0] * win_w - left < 0 or (pos[0] * win_w + length + right) > win_w:       # a label off the window
            lo = (KEY_MARGIN * win_w + left) / win_w
            hi = (win_w - KEY_MARGIN * win_w - right - length) / win_w
            new_pos[0] = round(max(lo, min(hi, pos[0])), 2)
    else:
        length = new_size[1] * win_h
        if (pos[1] * win_h + length) > win_h:
            new_pos[1] = round(max(KEY_MARGIN, (win_h - KEY_MARGIN * win_h - length) / win_h), 2)
        right_edge = (pos[0] + new_size[0]) * win_w + 8 + max(widths)
        if right_edge > win_w:
            new_pos[0] = round(max(KEY_MARGIN, pos[0] - (right_edge - (1 - KEY_MARGIN) * win_w) / win_w), 2)
    changes = []
    out = command.rstrip()
    for name, word, old, new in (("size", "size", size, new_size), ("pos", "pos", pos, new_pos)):
        if [round(x, 2) for x in old] != [round(x, 2) for x in new]:
            out = _set_key_option(out, name, word, "%g,%g" % tuple(new))
            changes.append("%s %g,%g" % (word, new[0], new[1]))
    if new_font != font_size:
        out = _set_key_option(out, "fontsize", "fontSize", str(new_font))
        changes.append("fontSize %d" % new_font)
    if not changes:
        return command, ""
    return out, ("`%s` -> %s (the %d labels would overlap or run off the window at the %s size)"
                 % (command.strip(), ", ".join(changes), len(labels), "given" if "size" in opts else "default"))


def _set_key_option(command: str, name: str, word: str, value: str) -> str:
    """The command with option `name` (lower case) set to value: replaced where it stands, or appended."""
    parts = _key_parts(command)
    if parts and name in parts[1]:
        start, end = parts[1][name][1]
        return command[:start] + "%s %s" % (word, value) + command[end:]
    return command + " %s %s" % (word, value)
