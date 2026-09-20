"""Contact comparison between two conformations (pure Python, no ChimeraX).

The ChimeraX side (``analysis.residue_contacts``) collects residue-residue contacts as
plain dicts; everything that decides what *changed* lives here so it can be unit tested
without a session.

The one rule that matters biologically: two structures are compared **through an
alignment-derived pairing**, never by residue number. Renumbered entries, construct
tags, missing loops and different chain ids are the normal case, and comparing
"residue 45 in A" with "residue 45 in B" silently produces nonsense.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

SALT_BRIDGE = "salt bridge"
HBOND_CAPABLE = "hbond-capable"
CONTACT = "contact"

# Most specific first: a residue pair is reported with the strongest interaction any of its
# atom pairs supports.
KIND_ORDER = (SALT_BRIDGE, HBOND_CAPABLE, CONTACT)

# Formally charged side-chain atoms at physiological pH. HIS is listed as cationic because a
# His-Asp/Glu pair within 4 A is a salt bridge whenever the ring is protonated, and the
# structure cannot tell us whether it is; the label says "capable", not "proven".
CATIONIC_ATOMS = frozenset({
    ("ARG", "NE"), ("ARG", "NH1"), ("ARG", "NH2"),
    ("LYS", "NZ"),
    ("HIS", "ND1"), ("HIS", "NE2"), ("HIP", "ND1"), ("HIP", "NE2"),
})
ANIONIC_ATOMS = frozenset({
    ("ASP", "OD1"), ("ASP", "OD2"),
    ("GLU", "OE1"), ("GLU", "OE2"),
})
# Backbone carboxy terminus is anionic in every residue type.
ANIONIC_ATOM_NAMES = frozenset({"OXT"})
# Phosphate/carboxylate oxygens of common ligands (AP5A, ATP, GTP, ...) are anionic too.
LIGAND_ANIONIC_PREFIXES = ("O1", "O2", "O3", "O5A", "OP")

HBOND_ELEMENTS = frozenset({"N", "O", "S"})

SALT_BRIDGE_CUTOFF = 4.0
HBOND_CUTOFF = 3.5


def _element_of(atom_name: str, element: Optional[str] = None) -> str:
    """Element symbol for an atom: what the caller measured, else the first letter of the
    PDB atom name (ND1 -> N, OE2 -> O), which is right for every protein/nucleic atom."""
    if element:
        return element.strip().upper()
    for ch in (atom_name or ""):
        if ch.isalpha():
            return ch.upper()
    return ""


def _is_cationic(res_name: str, atom_name: str) -> bool:
    return (res_name.upper(), atom_name.upper()) in CATIONIC_ATOMS


def _is_anionic(res_name: str, atom_name: str, element: str, standard: bool) -> bool:
    key = (res_name.upper(), atom_name.upper())
    if key in ANIONIC_ATOMS or atom_name.upper() in ANIONIC_ATOM_NAMES:
        return True
    if not standard and element == "O" and atom_name.upper().startswith(LIGAND_ANIONIC_PREFIXES):
        return True     # nucleotide/phosphate oxygens: ligands carry most of the negative charge
    return False


def classify_contact(a_res: str, a_atom: str, b_res: str, b_atom: str, dist: float,
                     a_element: Optional[str] = None, b_element: Optional[str] = None,
                     a_standard: bool = True, b_standard: bool = True) -> str:
    """Strongest interaction an atom pair supports: 'salt bridge', 'hbond-capable' or 'contact'.

    Geometry (donor-H-acceptor angles) is deliberately not used: crystal structures rarely have
    hydrogens, so an angle test would silently drop real bonds. 'hbond-capable' says what it is:
    two heteroatoms close enough to hydrogen bond.
    """
    ael = _element_of(a_atom, a_element)
    bel = _element_of(b_atom, b_element)
    if dist <= SALT_BRIDGE_CUTOFF:
        if (_is_cationic(a_res, a_atom) and _is_anionic(b_res, b_atom, bel, b_standard)) or \
           (_is_cationic(b_res, b_atom) and _is_anionic(a_res, a_atom, ael, a_standard)):
            return SALT_BRIDGE
    if dist <= HBOND_CUTOFF and ael in HBOND_ELEMENTS and bel in HBOND_ELEMENTS:
        return HBOND_CAPABLE
    return CONTACT


def strongest_kind(kinds: Sequence[str]) -> str:
    """The most specific of several kinds (a residue pair may touch through several atom pairs)."""
    for k in KIND_ORDER:
        if k in kinds:
            return k
    return CONTACT


# ------------------------------------------------------------------ specs and pairing


def normalize_spec(spec: str) -> str:
    """'#2/A:45', '/A:45' and 'A:45' all mean the same residue of whichever model we are in.

    Contacts and pairings arrive from different places (the collector, matchmaker, a model's
    own JSON), so every key is reduced to 'CHAIN:NUMBER' before anything is compared.
    """
    s = (spec or "").strip()
    if "/" in s:
        s = s.rsplit("/", 1)[1]
    return s


def _pair_key(a: str, b: str) -> Tuple[str, str]:
    """Unordered residue pair as a sorted tuple: a contact has no direction."""
    x, y = normalize_spec(a), normalize_spec(b)
    return (x, y) if x <= y else (y, x)


def _res_number(spec: str) -> int:
    """Residue number out of 'A:45' (0 when there is none), for readable ordering only."""
    s = normalize_spec(spec)
    num = s.split(":")[-1]
    digits = "".join(ch for ch in num if ch.isdigit() or ch == "-")
    try:
        return int(digits)
    except ValueError:
        return 0


def _chain(spec: str) -> str:
    s = normalize_spec(spec)
    return s.split(":")[0] if ":" in s else ""


def _label(name: str, spec: str, show_chain: bool) -> str:
    """'ARG 36' or, when several chains are in play, 'ARG A 36' - how a biologist names it."""
    num = normalize_spec(spec).split(":")[-1]
    chain = _chain(spec)
    if show_chain and chain:
        return "%s %s %s" % (name or "?", chain, num)
    return "%s %s" % (name or "?", num)


def _pair_label(entry: Dict[str, Any], show_chain: bool) -> str:
    return "%s-%s" % (_label(entry.get("a_name", ""), entry.get("a", ""), show_chain),
                      _label(entry.get("b_name", ""), entry.get("b", ""), show_chain))


# ------------------------------------------------------------------ comparison


def compare_contacts(ref_contacts: List[Dict[str, Any]], other_contacts: List[Dict[str, Any]],
                     pairing: Dict[str, str]) -> Dict[str, Any]:
    """Which contacts the other conformation lost, gained and kept, compared through `pairing`.

    `pairing` maps reference residue specs to the other model's specs, exactly as
    ``analysis.residue_pairing`` builds it from matchmaker's own residue correspondence
    (the same correspondence ``compute_displacement`` uses). Specs may be written with or
    without a model prefix.

    A contact whose residues are not both in the pairing cannot be judged: the partner may
    simply be absent from the alignment (a ligand, a tag, an unmodelled loop). Those are
    counted as `unmapped`, never as lost or gained, because reporting an unaligned residue
    as "lost contact" is the single most misleading thing this comparison could do.
    """
    pair = {normalize_spec(k): normalize_spec(v) for k, v in (pairing or {}).items()}
    reverse = {v: k for k, v in pair.items()}

    other_index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for c in other_contacts or []:
        other_index[_pair_key(c.get("a", ""), c.get("b", ""))] = c
    ref_index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for c in ref_contacts or []:
        ref_index[_pair_key(c.get("a", ""), c.get("b", ""))] = c

    lost: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []
    gained: List[Dict[str, Any]] = []
    unmapped_ref = 0
    unmapped_other = 0

    for key, c in ref_index.items():
        ma, mb = pair.get(key[0]), pair.get(key[1])
        if ma is None or mb is None:
            unmapped_ref += 1
            continue
        entry = dict(c)
        entry["mapped_a"], entry["mapped_b"] = ma, mb
        twin = other_index.get(_pair_key(ma, mb))
        if twin is None:
            lost.append(entry)
        else:
            entry["other_min_dist"] = twin.get("min_dist")
            entry["other_kind"] = twin.get("kind")
            try:
                entry["delta"] = round(float(twin.get("min_dist", 0)) - float(c.get("min_dist", 0)), 2)
            except (TypeError, ValueError):
                entry["delta"] = None
            kept.append(entry)

    for key, c in other_index.items():
        ra, rb = reverse.get(key[0]), reverse.get(key[1])
        if ra is None or rb is None:
            unmapped_other += 1
            continue
        if _pair_key(ra, rb) in ref_index:
            continue        # already counted as kept from the reference side
        entry = dict(c)
        entry["mapped_a"], entry["mapped_b"] = ra, rb
        gained.append(entry)

    order = {k: i for i, k in enumerate(KIND_ORDER)}
    sort_key = lambda e: (order.get(e.get("kind"), 9), _chain(e.get("a", "")), _res_number(e.get("a", "")))
    lost.sort(key=sort_key)
    gained.sort(key=sort_key)
    kept.sort(key=sort_key)

    by_kind: Dict[str, Dict[str, int]] = {}
    for bucket_name, bucket in (("lost", lost), ("gained", gained), ("kept", kept)):
        for e in bucket:
            row = by_kind.setdefault(str(e.get("kind", CONTACT)), {"lost": 0, "gained": 0, "kept": 0})
            row[bucket_name] += 1

    result: Dict[str, Any] = {
        "lost": lost, "gained": gained, "kept": kept, "by_kind": by_kind,
        "counts": {"lost": len(lost), "gained": len(gained), "kept": len(kept),
                   "reference_contacts": len(ref_index), "other_contacts": len(other_index),
                   "unmapped_reference": unmapped_ref, "unmapped_other": unmapped_other,
                   "paired_residues": len(pair)},
    }
    result["summary"] = summarize(result)
    if unmapped_ref or unmapped_other:
        result["note"] = ("%d reference and %d compared contacts involve residues outside the alignment "
                          "(ligands, tags, unmodelled regions) and were not judged." % (unmapped_ref, unmapped_other))
    return result


def summarize(result: Dict[str, Any], max_named: int = 3) -> str:
    """One sentence a biologist would write: the counts, the broken/formed polar contacts by
    name, and the residues that gained or lost the most."""
    lost = result.get("lost") or []
    gained = result.get("gained") or []
    kept = result.get("kept") or []
    chains = set()
    for e in lost + gained + kept:
        chains.add(_chain(e.get("a", "")))
        chains.add(_chain(e.get("b", "")))
    show_chain = len({c for c in chains if c}) > 1

    parts = ["%d contact%s lost and %d gained (%d unchanged)" % (
        len(lost), "" if len(lost) == 1 else "s", len(gained), len(kept))]

    broken = [e for e in lost if e.get("kind") == SALT_BRIDGE]
    formed = [e for e in gained if e.get("kind") == SALT_BRIDGE]
    if broken:
        parts.append("%d salt bridge%s break (%s)" % (len(broken), "" if len(broken) == 1 else "s",
                                                      ", ".join(_pair_label(e, show_chain) for e in broken[:max_named])))
    if formed:
        parts.append("%d salt bridge%s form (%s)" % (len(formed), "" if len(formed) == 1 else "s",
                                                     ", ".join(_pair_label(e, show_chain) for e in formed[:max_named])))
    hot_lost = _busiest_residues(lost, max_named, show_chain)
    if hot_lost and not broken:
        parts.append("the residues losing most contacts are " + ", ".join(hot_lost))
    elif hot_lost:
        parts.append("most of the loss is around " + ", ".join(hot_lost))
    hot_gained = _busiest_residues(gained, max_named, show_chain)
    if hot_gained:
        parts.append("the new contacts cluster on " + ", ".join(hot_gained))
    if not lost and not gained:
        return "No contact changes: all %d contacts are present in both conformations." % len(kept)
    return "; ".join(parts) + "."


def _busiest_residues(entries: List[Dict[str, Any]], limit: int, show_chain: bool) -> List[str]:
    """Residues appearing in the most changed contacts, named with their counts."""
    counts: Dict[str, int] = {}
    names: Dict[str, str] = {}
    for e in entries:
        for spec_key, name_key in (("a", "a_name"), ("b", "b_name")):
            spec = normalize_spec(e.get(spec_key, ""))
            if not spec:
                continue
            counts[spec] = counts.get(spec, 0) + 1
            names.setdefault(spec, str(e.get(name_key, "")))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], _chain(kv[0]), _res_number(kv[0])))
    return ["%s (%d)" % (_label(names.get(s, ""), s, show_chain), n) for s, n in ranked[:limit]]


# ------------------------------------------------------------------ visualisation plan

LOST_COLOR = "firebrick"
GAINED_COLOR = "forest green"


def _model_of(spec: str) -> str:
    """'#2/A' -> '#2': pseudobonds and shows are built from the model id plus the contact's
    own '/chain:number', so any chain restriction in the spec must be dropped first."""
    s = (spec or "").strip()
    return s.split("/")[0] if "/" in s else s


def _atom_spec(model: str, res_spec: str, atom_name: Optional[str]) -> str:
    res = normalize_spec(res_spec)
    if "/" not in res:
        res = "/" + res
    return "%s%s%s" % (model, res, "@" + atom_name if atom_name else "")


def _residue_spec_list(model: str, specs: Sequence[str]) -> List[str]:
    """One spec per chain, e.g. '#1/A:45,118,119' - short enough to stay readable in the log."""
    by_chain: Dict[str, List[str]] = {}
    for s in specs:
        n = normalize_spec(s)
        if ":" not in n:
            continue
        chain, num = n.split(":", 1)
        nums = by_chain.setdefault(chain, [])
        if num not in nums:
            nums.append(num)
    return ["%s/%s:%s" % (model, chain, ",".join(sorted(nums, key=lambda x: _res_number("X:" + x))))
            for chain, nums in sorted(by_chain.items())]


def contact_commands(result: Dict[str, Any], ref_spec: str, other_spec: str,
                     max_drawn: int = 24) -> List[str]:
    """ChimeraX commands that show the change: lost contacts as red dashes on the reference,
    gained ones as green dashes on the compared model, with the residues shown as sticks and
    a key.

    Unlike ``compute_displacement``'s plan, the reference stays visible: a lost contact only
    exists on the reference, so hiding it would hide half the answer. Both cartoons are made
    transparent instead, so the dashed pseudobonds read against them.

    Only the most informative `max_drawn` contacts of each kind are drawn (salt bridges first,
    then hydrogen-bond-capable pairs, then van der Waals contacts); a 2D label says so.
    """
    ref_model = _model_of(ref_spec)
    other_model = _model_of(other_spec)
    lost = (result.get("lost") or [])[:max_drawn]
    gained = (result.get("gained") or [])[:max_drawn]
    n_lost = len(result.get("lost") or [])
    n_gained = len(result.get("gained") or [])

    cmds: List[str] = ["show %s models" % ref_model, "show %s models" % other_model,
                       "transparency %s,%s 55 target c" % (ref_model, other_model),
                       "distance delete"]      # a previous comparison's dashes must not be read as this one's

    for entries, model, color in ((lost, ref_model, LOST_COLOR), (gained, other_model, GAINED_COLOR)):
        if not entries:
            continue
        specs = [e.get("a", "") for e in entries] + [e.get("b", "") for e in entries]
        for group in _residue_spec_list(model, specs):
            cmds.append("show %s atoms" % group)
            cmds.append("color %s %s target a" % (group, color))
        for e in entries:
            a = _atom_spec(model, e.get("a", ""), e.get("a_atom"))
            b = _atom_spec(model, e.get("b", ""), e.get("b_atom"))
            # `distance` needs exactly one atom on each side; without measured atom names the
            # residues are still shown and colored above, just not joined by a dash.
            if e.get("a_atom") and e.get("b_atom"):
                cmds.append("distance %s %s color %s dashes 6 radius 0.08" % (a, b, color))

    cmds.append('key %s:"lost in %s" %s:"gained in %s" colorTreatment distinct pos 0.68,0.05 size 0.28,0.035 fontSize 15'
                % (LOST_COLOR.replace(" ", ""), other_model, GAINED_COLOR.replace(" ", ""), other_model))
    shown = "showing %d of %d lost and %d of %d gained" % (len(lost), n_lost, len(gained), n_gained)
    cmds.append('2dlabels text "Contacts %s vs %s (%s)" xpos 0.68 ypos 0.10 size 14 color black'
                % (ref_model, other_model, shown))
    return cmds
