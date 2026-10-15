"""One residue identity: (model, chain, number, insertion code).

Every place that names a residue (tables, annotations, picking, exports, displacement) uses this
key and these spellings, so 82, 82A and 82B stay three residues and #1.1 and #1.2 stay two models.
ChimeraX-free.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Tuple

_NUM_RE = re.compile(r"^\s*(-?\d+)\s*([A-Za-z]?)\s*$")
_KEY_RE = re.compile(r"^\s*(#[\d.]+)?\s*/?\s*([^:/\s#]+)\s*:\s*(-?\d+)([A-Za-z]?)\s*$")

# how a residue came to be the target of a table row or a sequence position
STATUS_EXACT = "exact"            # UniProt numbering, the structure numbered the same way
STATUS_OFFSET = "offset"          # UniProt numbering, the file's constant offset applied
STATUS_ALIGNMENT = "alignment"    # UniProt numbering, placed by aligning the chain to the UniProt sequence
STATUS_STRUCTURE = "structure"    # the table's own numbers are the structure's residue numbers
STATUS_UNRESOLVED = "unresolved"  # no basis: nothing is placed

# Aligned identity at which a chain without a usable UniProt reference is taken to be that protein; a chain the
# user names is accepted down to the lower value (an ortholog, a construct with many mutations).
ALIGN_IDENTITY = 0.9
ALIGN_IDENTITY_CHOSEN = 0.5


class ResId(NamedTuple):
    model: str      # "#1", "#1.2", or "" when the caller holds one model only
    chain: str
    number: int
    icode: str = ""

    def short(self) -> str:
        """'A:82A', the key used in tables and attributes."""
        return "%s:%s" % (self.chain, number_text(self.number, self.icode))

    def spec(self, model: str = "") -> str:
        """'#1/A:82A' (ChimeraX: ':82' matches 82 only, ':82A' matches 82A only)."""
        return "%s/%s:%s" % (model or self.model, self.chain, number_text(self.number, self.icode))

    @classmethod
    def parse(cls, text: Any, model: str = "") -> Optional["ResId"]:
        """'A:82', 'A:82A', '/A:82a', '#1.2/A:-3' -> ResId; None when it is not a residue key."""
        if isinstance(text, ResId):
            return text
        if isinstance(text, (tuple, list)):
            return from_tuple(tuple(text), model)
        m = _KEY_RE.match(str(text or ""))
        if not m:
            return None
        return cls(m.group(1) or model, m.group(2), int(m.group(3)), m.group(4).upper())


def from_tuple(t: Tuple[Any, ...], model: str = "") -> Optional[ResId]:
    """(chain, number) or (chain, number, icode) or (model, chain, number, icode), as older code wrote them."""
    try:
        if len(t) == 2:
            return ResId(model, str(t[0]), int(t[1]), "")
        if len(t) == 3:
            return ResId(model, str(t[0]), int(t[1]), str(t[2] or "").upper())
        if len(t) == 4:
            return ResId(str(t[0]) or model, str(t[1]), int(t[2]), str(t[3] or "").upper())
    except (TypeError, ValueError):
        return None
    return None


def number_text(number: int, icode: str = "") -> str:
    return "%d%s" % (int(number), (icode or "").strip().upper())


def parse_number(text: Any) -> Optional[Tuple[int, str]]:
    """'82' -> (82, ''), '82A' -> (82, 'A'), '-3' -> (-3, ''), 82.0 -> (82, ''); None otherwise."""
    if isinstance(text, bool):
        return None
    if isinstance(text, int):
        return int(text), ""
    if isinstance(text, float):
        return (int(text), "") if text == int(text) else None
    m = _NUM_RE.match(str(text or ""))
    if m:
        return int(m.group(1)), m.group(2).upper()
    try:
        f = float(str(text))
    except (TypeError, ValueError):
        return None
    return (int(f), "") if f == int(f) else None


def entry_targets(entry: Any, model: str = "") -> List[Dict[str, Any]]:
    """A mapping entry as a list of target dicts. Older mappings hold one dict per position."""
    if not entry:
        return []
    items = entry if isinstance(entry, list) else [entry]
    out = []
    for it in items:
        if not isinstance(it, dict) or it.get("chain") is None or it.get("number") is None:
            continue
        num = parse_number(it["number"])
        if num is None:
            continue
        icode = str(it.get("icode") or "").upper() or num[1]
        out.append(dict(it, model=it.get("model") or model, number=num[0], icode=icode))
    return out


def target_id(t: Dict[str, Any], model: str = "") -> ResId:
    return ResId(t.get("model") or model, str(t["chain"]), int(t["number"]), str(t.get("icode") or "").upper())


def residue_list(numbers: Iterable[Tuple[int, str]], insertion_numbers: Iterable[int] = ()) -> str:
    """Numbers of one chain as a ChimeraX residue list that matches exactly those residues.

    A range (':80-84') also matches the insertion residues numbered inside it (82A, 82B), so a run
    never includes a number that carries insertion residues in the structure; those numbers and the
    insertion residues themselves are listed one by one.
    """
    special = {int(n) for n in insertion_numbers}
    items = sorted({(int(n), (c or "").upper()) for n, c in numbers})
    out: List[str] = []
    run: List[int] = []

    def flush():
        if run:
            out.append("%d-%d" % (run[0], run[-1]) if len(run) > 1 else str(run[0]))
            run.clear()

    for n, c in items:
        if c or n in special or n < 0:   # ':-3-5' reads as 3-5: negative numbers are listed one by one
            flush()
            out.append(number_text(n, c))
            continue
        if run and n == run[-1] + 1:
            run.append(n)
        else:
            flush()
            run.append(n)
    flush()
    return ",".join(out)


def specs_by_chain(ids: Iterable[ResId], insertion_numbers: Optional[Dict[Tuple[str, str], Iterable[int]]] = None,
                   model: str = "") -> List[str]:
    """['#1/A:10-12,82,82A', '#1/B:5'] for a set of residues, one spec per model and chain."""
    groups: Dict[Tuple[str, str], List[Tuple[int, str]]] = {}
    for r in ids:
        groups.setdefault((r.model or model, r.chain), []).append((r.number, r.icode))
    ins = insertion_numbers or {}
    return ["%s/%s:%s" % (m, c, residue_list(nums, ins.get((m, c), ins.get(("", c), ()))))
            for (m, c), nums in sorted(groups.items())]


def insertion_numbers(ids: Iterable[ResId]) -> Dict[Tuple[str, str], List[int]]:
    """{(model, chain): numbers that carry insertion residues} for the residues a structure has."""
    out: Dict[Tuple[str, str], set] = {}
    for r in ids:
        if r.icode:
            out.setdefault((r.model, r.chain), set()).add(r.number)
    return {k: sorted(v) for k, v in out.items()}
