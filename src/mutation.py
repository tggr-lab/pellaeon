"""What a substitution would do to a residue's surroundings, inspected over its rotamer ensemble.

For a substitution such as L99A or R175H on an open structure: the wild-type side chain's contacts,
hydrogen bonds and salt bridges; then every likely rotamer of the new residue (Dunbrack library, up to
95% cumulative probability) placed on the fixed backbone, each checked for clashes, contacts and polar
partners. The report says which partners are lost in every rotamer, which are gained in most clash-free
rotamers, how many rotamers fit at all, and the size and charge change. Rigid backbone, one structure:
a structural inspection, not a stability (ddG) or pathogenicity prediction. Read-only unless `place`.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .residue_env import MAX_ASA, local_atoms, relative_sasa, res_label

AA1 = {"A": "ALA", "R": "ARG", "N": "ASN", "D": "ASP", "C": "CYS", "Q": "GLN", "E": "GLU", "G": "GLY", "H": "HIS",
       "I": "ILE", "L": "LEU", "K": "LYS", "M": "MET", "F": "PHE", "P": "PRO", "S": "SER", "T": "THR", "W": "TRP",
       "Y": "TYR", "V": "VAL"}
AA3 = set(AA1.values())
# residue volumes, A^3 (Zamyatnin 1972)
VOLUME = {"ALA": 88.6, "ARG": 173.4, "ASN": 114.1, "ASP": 111.1, "CYS": 108.5, "GLN": 143.8, "GLU": 138.4, "GLY": 60.1,
          "HIS": 153.2, "ILE": 166.7, "LEU": 166.7, "LYS": 168.6, "MET": 162.9, "PHE": 189.9, "PRO": 112.7, "SER": 89.0,
          "THR": 116.1, "TRP": 227.8, "TYR": 193.6, "VAL": 140.0}
CHARGE = {"ARG": 1, "LYS": 1, "ASP": -1, "GLU": -1}
BACKBONE = {"N", "CA", "C", "O", "OXT"}
CLASH = 0.6         # vdW overlap (A) counted as a clash, ChimeraX's `clashes` default
SEVERE = 1.0
POLAR_HB = 3.5      # N/O to N/O distance counted as a possible hydrogen bond, A
CONTACT = 4.5       # heavy-atom packing contact, A (the usual residue-network cutoff)
MAX_ROTAMERS = 12
CUM_PROB = 0.95
_CATION = {("ARG", "NH1"), ("ARG", "NH2"), ("ARG", "NE"), ("LYS", "NZ"), ("HIS", "ND1"), ("HIS", "NE2")}   # as residue_env
_ANION = {("ASP", "OD1"), ("ASP", "OD2"), ("GLU", "OE1"), ("GLU", "OE2")}

_VARIANT_RE = re.compile(r"^(?:p\.)?([A-Za-z]{3}|[A-Z])(-?\d+)([A-Za-z]{3}|[A-Z])$")


def parse_variant(text: str):
    """'R175H', 'Arg175His', 'p.Arg175His' -> ('ARG', 175, 'HIS'); None when it is not one."""
    m = _VARIANT_RE.match((text or "").strip().replace(" ", ""))
    if not m:
        return None
    a, n, b = m.groups()

    def three(x):
        if len(x) == 1:
            return AA1.get(x.upper())
        x = x.upper()
        return x if x in AA3 else None
    wt, mt = three(a), three(b)
    if not wt or not mt:
        return None
    return wt, int(n), mt


def _side(atoms):
    """Side-chain heavy atoms (CB onward)."""
    keep = [a for a in atoms if a.element.name != "H" and a.name not in BACKBONE]
    return keep


def _env_for(r, env):
    """Environment atoms the new side chain can meet: everything near except the residue itself and the
    backbone atoms bonded to it (C of the previous residue, N of the next)."""
    out = []
    for a in env:
        if a.residue is r:
            continue
        out.append(a)
    return out


def _bond_depths(start, maxd: int = 3) -> Dict[int, int]:
    """Bond distance from `start` to every atom within `maxd` bonds (breadth-first over covalent bonds)."""
    depth = {id(start): 0}
    frontier = [start]
    for d in range(1, maxd + 1):
        nxt = []
        for a in frontier:
            for n in a.neighbors:
                if id(n) not in depth:
                    depth[id(n)] = d
                    nxt.append(n)
        frontier = nxt
    return depth


def _backbone_depths(r) -> Dict[str, Dict[int, int]]:
    """Bond distances from the residue's CA and N to the atoms around it (C and O of i-1, N and CA of i+1...)."""
    out = {}
    for name in ("CA", "N"):
        a = r.find_atom(name)
        out[name] = _bond_depths(a, 3) if a is not None else {}
    return out


def _side_depths(atoms) -> Dict[str, Dict[str, int]]:
    """For each side-chain atom name, its bond distance to CA and to N within its own residue (rotamer or wild type)."""
    by = {a.name: a for a in atoms}
    out: Dict[str, Dict[str, int]] = {}
    for anchor in ("CA", "N"):
        a = by.get(anchor)
        if a is None:
            continue
        for aid, d in _bond_depths(a, 4).items():
            for x in atoms:
                if id(x) == aid:
                    out.setdefault(x.name, {})[anchor] = d
    return out


def _excluded(bb: Dict[str, Dict[int, int]], sd: Dict[str, int], env_atom) -> bool:
    """True when the environment atom is within 3 bonds of this side-chain atom (through CA or N)."""
    for anchor, dmap in bb.items():
        da = dmap.get(id(env_atom))
        if da is not None and anchor in sd and da + sd[anchor] <= 3:
            return True
    return False


def _partners(coords_names, env_list, r_name, bb=None, depths=None):
    """Contacts, polar partners, salt bridges and clashes of a set of side-chain atoms (coords, names, radii).
    Pairs within 3 bonds (through the backbone: Pro CD to C(i-1), CB to N(i+1)) are never clashes."""
    bb = bb or {}
    depths = depths or {}
    import numpy as np
    if not coords_names or not env_list:
        return {"contacts": {}, "polar": set(), "salt": set(), "clashes": [], "worst": 0.0}
    xyz = np.array([c for c, _, _ in coords_names])
    names = [n for _, n, _ in coords_names]
    radii = np.array([rad for _, _, rad in coords_names])
    exyz = np.array([a.coord for a in env_list])
    from chimerax.atomic import Atoms
    erad = Atoms(env_list).default_radii     # not the display radii, which `size` changes
    d = np.linalg.norm(exyz[:, None, :] - xyz[None, :, :], axis=2)      # env x side
    contacts: Dict[Any, float] = {}
    polar, salt, clashes = set(), set(), []
    worst = 0.0
    for i, a in enumerate(env_list):
        dmin = float(d[i].min())
        if dmin > CONTACT + 1.5:
            continue
        rr = a.residue
        if dmin <= CONTACT:
            if rr not in contacts or dmin < contacts[rr]:
                contacts[rr] = dmin
        for j in range(len(names)):
            dij = float(d[i, j])
            if not _excluded(bb, depths.get(names[j], {}), a):
                ov = float(radii[j] + erad[i] - dij)
                # a donor-acceptor pair may sit closer than vdW radii allow (ChimeraX's 0.4 A allowance);
                # two carboxylate oxygens may not
                polar_pair = _hb_compatible(r_name, names[j], names[j][0], rr.name, a.name, a.element.name)
                if polar_pair:
                    ov -= 0.4
                if ov >= CLASH:
                    clashes.append((names[j], "%s@%s" % (res_label(rr), a.name), round(ov, 2), rr.chain_id))
                    worst = max(worst, ov)
            if dij <= POLAR_HB and _hb_compatible(r_name, names[j], names[j][0], rr.name, a.name, a.element.name):
                polar.add((rr, a.name))
            if dij <= 4.0 and (((r_name, names[j]) in _CATION and (rr.name, a.name) in _ANION)
                               or ((r_name, names[j]) in _ANION and (rr.name, a.name) in _CATION)):
                salt.add(rr)
    return {"contacts": contacts, "polar": polar, "salt": salt, "clashes": clashes, "worst": worst}


_DONORS = {("ARG", "NE"), ("ARG", "NH1"), ("ARG", "NH2"), ("LYS", "NZ"), ("ASN", "ND2"), ("GLN", "NE2"), ("HIS", "ND1"),
           ("HIS", "NE2"), ("TRP", "NE1"), ("SER", "OG"), ("THR", "OG1"), ("TYR", "OH")}
_ACCEPT_N = {("HIS", "ND1"), ("HIS", "NE2")}


def _hb_role(res_name: str, atom_name: str, element: str, standard: bool):
    """(can donate, can accept) for a heavy atom: standard residues by name, anything else by element."""
    if element not in ("N", "O"):
        return False, False
    if not standard:
        return True, True          # ligands, waters, modified residues: unknown protonation, either role
    if atom_name == "N":
        return res_name != "PRO", False
    if element == "O":
        return (res_name, atom_name) in _DONORS, True
    return (res_name, atom_name) in _DONORS, (res_name, atom_name) in _ACCEPT_N


def _hb_compatible(r_name, a_name, a_el, b_res, b_name, b_el) -> bool:
    from .residue_env import MAX_ASA
    da, aa = _hb_role(r_name, a_name, a_el, r_name in MAX_ASA)
    db, ab = _hb_role(b_res, b_name, b_el, b_res in MAX_ASA)
    return (da and ab) or (aa and db)


def _label(rr, chain) -> str:
    return res_label(rr) if rr.chain_id == chain else "%s/%s" % (rr.chain_id, res_label(rr))


def mutation_effect(session, r, mutant: str) -> Dict[str, Any]:
    import numpy as np
    wt = r.name
    mutant = mutant.upper()
    out: Dict[str, Any] = {"residue": "#%s/%s:%d%s" % (r.structure.id_string, r.chain_id, r.number, r.insertion_code or ""), "wild_type": wt, "mutant": mutant,
                           "substitution": "%s%d%s" % (wt.capitalize(), r.number, mutant.capitalize())}
    if wt not in AA3:
        return {"error": "%s is not a standard amino acid." % res_label(r)}
    if mutant == wt:
        return {"error": "Residue %s is already %s." % (res_label(r), wt)}
    try:
        out["secondary_structure"] = "helix" if r.is_helix else ("strand" if r.is_strand else "loop/coil")
    except Exception:  # noqa: BLE001
        pass
    env = local_atoms(r, 16.0)
    try:
        out["wild_type_burial"] = relative_sasa(r, local_atoms(r, 20.0))
    except Exception:  # noqa: BLE001
        pass
    truncated = False
    try:
        truncated = bool(r.is_missing_heavy_template_atoms())
    except Exception:  # noqa: BLE001
        pass
    if truncated:
        out["warning"] = ("the wild-type side chain is incomplete in this structure: its contacts and burial are "
                          "underestimated, and the cavity/burial notes are not given")
        out.pop("wild_type_burial", None)
    env_list = _env_for(r, env)
    bb = _backbone_depths(r)
    chain = r.chain_id

    from chimerax.atomic import Atoms
    _wt = _side(r.atoms)
    wt_side = [(a.coord, a.name, float(rad)) for a, rad in zip(_wt, Atoms(_wt).default_radii)] if _wt else []
    wt_p = _partners(wt_side, env_list, wt, bb, _side_depths(list(r.atoms)))
    wt_contacts = {rr for rr in wt_p["contacts"] if abs(rr.number - r.number) > 1 or rr.chain_id != chain}
    out["wild_type_side_chain"] = {
        "contacts": [_label(rr, chain) for rr in sorted(wt_contacts, key=lambda x: wt_p["contacts"][x])][:14],
        "polar_partners": sorted({"%s@%s" % (_label(rr, chain), n) for rr, n in wt_p["polar"]})[:10],
        "salt_bridges": sorted(_label(rr, chain) for rr in wt_p["salt"])}

    # the new residue's rotamers on the fixed backbone
    rotamers: List[Dict[str, Any]] = []
    if mutant == "GLY":
        rotamers = [{"prob": 1.0, "side": [], "chis": ()}]
    elif mutant == "ALA":
        cb = r.find_atom("CB")
        if cb is None:   # Gly -> Ala: place CB from a rotamer of ALA's nearest shape (VAL's CB is the same atom)
            from chimerax.swap_res import get_rotamers
            rots = get_rotamers(session, r, res_type="VAL")
            cbs = [a for a in rots[0].atoms if a.name == "CB"]
            rotamers = [{"prob": 1.0, "side": [(cbs[0].coord, "CB", 1.7)], "chis": ()}]
            for rs in rots:
                rs.delete()
        else:
            rotamers = [{"prob": 1.0, "side": [(cb.coord, "CB", 1.7)], "chis": ()}]
    else:
        from chimerax.swap_res import get_rotamers
        try:
            rots = get_rotamers(session, r, res_type=mutant, rot_lib="Dunbrack")
        except Exception as e:  # noqa: BLE001
            return {"error": "Could not place %s rotamers at %s: %s" % (mutant, res_label(r), e)}
        cum = 0.0
        try:   # rotamer structures are not in the session: delete them whatever happens
            for rs in rots:
                if len(rotamers) >= MAX_ROTAMERS or cum >= CUM_PROB:
                    break
                heavy = [(a, float(rad)) for a, rad in zip(rs.atoms, rs.atoms.default_radii) if a.element.name != "H" and a.name not in BACKBONE]
                side = [(a.coord, a.name, rad) for a, rad in heavy]
                rotamers.append({"prob": float(getattr(rs, "rotamer_prob", 0.0)), "side": side, "depths": _side_depths(list(rs.atoms)),
                                 "chis": tuple(round(float(c), 0) for c in (getattr(rs, "chis", ()) or ()))})
                cum += float(getattr(rs, "rotamer_prob", 0.0))
            out["rotamer_library_total"] = len(rots)
        finally:
            for rs in rots:
                try:
                    rs.delete()
                except Exception:  # noqa: BLE001
                    pass
    if not rotamers:
        return {"error": "No rotamers for %s at %s." % (mutant, res_label(r))}

    per = []
    for rot in rotamers:
        p = _partners(rot["side"], env_list, mutant, bb, rot.get("depths") or {"CB": {"CA": 1}})
        contacts = {rr for rr in p["contacts"] if abs(rr.number - r.number) > 1 or rr.chain_id != chain}
        severe = [c for c in p["clashes"] if c[2] >= SEVERE]
        per.append({"prob": rot["prob"], "chis": rot["chis"], "contacts": contacts, "polar": p["polar"], "salt": p["salt"],
                    "clashes": p["clashes"], "severe": severe, "worst": p["worst"]})
    n = len(per)
    fits = [x for x in per if not x["clashes"]]
    out["rotamers"] = {"sampled": n, "library_probability_covered": round(sum(x["prob"] for x in per), 2),
                       "clash_free": len(fits), "clash_free_library_probability": round(sum(x["prob"] for x in fits), 2),
                       "severe_clash_in_every_sampled": all(x["severe"] for x in per) if mutant not in ("GLY", "ALA") else bool(per[0]["severe"])}
    worst_clashes = {}
    for x in per:
        for atom, partner, ov, _ch in x["clashes"]:
            if partner not in worst_clashes or ov > worst_clashes[partner]:
                worst_clashes[partner] = ov
    if worst_clashes:
        out["clashing_with"] = ["%s %.1f A overlap (in %d of %d rotamers)" % (
            k, v, sum(1 for x in per if any(c[1] == k for c in x["clashes"])), n)
            for k, v in sorted(worst_clashes.items(), key=lambda kv: -kv[1])[:6]]
    pool = fits or per      # losses: judged over the fitting rotamers, or all when none fits
    # partners: lost in every rotamer, kept in some, gained in most of the fitting ones
    lost = sorted((rr for rr in wt_contacts if all(rr not in x["contacts"] for x in pool)), key=lambda rr: rr.number)
    gained_count: Dict[Any, int] = {}
    for x in pool:
        for rr in x["contacts"]:
            if rr not in wt_contacts:
                gained_count[rr] = gained_count.get(rr, 0) + 1
    # gains only from rotamers that fit, and only when more than half of them agree
    gained = sorted((rr for rr, k in gained_count.items() if fits and k > len(pool) / 2), key=lambda rr: rr.number)
    wt_polar = {(rr, nm) for rr, nm in wt_p["polar"]}
    polar_lost = sorted({"%s@%s" % (_label(rr, chain), nm) for rr, nm in wt_polar if all((rr, nm) not in x["polar"] for x in pool)})
    polar_new_count: Dict[Any, int] = {}
    for x in pool:
        for key in x["polar"]:
            if key not in wt_polar:
                polar_new_count[key] = polar_new_count.get(key, 0) + 1
    polar_new = sorted({"%s@%s" % (_label(rr, chain), nm) for (rr, nm), k in polar_new_count.items() if fits and k > len(pool) / 2})
    salt_lost = sorted(_label(rr, chain) for rr in wt_p["salt"] if all(rr not in x["salt"] for x in pool))
    salt_new = sorted({_label(rr, chain) for x in fits for rr in x["salt"] if rr not in wt_p["salt"]})
    out["contacts_lost_in_every_rotamer"] = [_label(rr, chain) for rr in lost][:12]
    out["contacts_gained_in_most_fitting_rotamers"] = [_label(rr, chain) for rr in gained][:12]
    out["possible_polar_partners_lost"] = polar_lost[:10]
    out["possible_polar_partners_new"] = polar_new[:10]
    if salt_lost:
        out["salt_bridges_lost"] = salt_lost
    if salt_new:
        out["salt_bridges_new"] = salt_new
    out["volume_change_A3"] = round(VOLUME[mutant] - VOLUME[wt], 1)
    dq = CHARGE.get(mutant, 0) - CHARGE.get(wt, 0)
    if dq:
        out["charge_change"] = dq
    best = sorted(per, key=lambda x: (len(x["severe"]), len(x["clashes"]), -x["prob"]))[0]
    out["best_rotamer"] = {"probability": round(best["prob"], 3), "chis": list(best["chis"]), "clashes": len(best["clashes"])}

    flags = []
    rel = (out.get("wild_type_burial") or {}).get("relative")
    buried = rel is not None and rel < 0.2
    if mutant == "PRO" and out.get("secondary_structure") in ("helix", "strand"):
        flags.append("proline inside a %s: its ring fixes phi near -65 and removes the backbone NH hydrogen bond" % out["secondary_structure"])
    if wt == "PRO":
        flags.append("loses a proline: backbone becomes more flexible here")
    if mutant == "GLY" and wt != "GLY":
        flags.append("glycine: side chain removed and backbone flexibility increased")
    if wt == "GLY":
        flags.append("replaces a glycine: check the backbone phi/psi, which may be allowed only for glycine")
    if wt == "CYS":
        try:
            sg = r.find_atom("SG")
            if sg is not None and any(n.name == "SG" for n in sg.neighbors):
                flags.append("breaks a disulfide bond")
        except Exception:  # noqa: BLE001
            pass
    if buried and out["volume_change_A3"] <= -40:
        flags.append("large-to-small in a buried position: a cavity is likely")
    if buried and CHARGE.get(mutant) and not CHARGE.get(wt):
        flags.append("introduces a charge into a buried position")
    if buried and mutant in ("ASN", "GLN", "SER", "THR", "HIS", "TYR") and wt in ("LEU", "ILE", "VAL", "PHE", "MET", "ALA", "TRP"):
        flags.append("polar side chain into a buried hydrophobic position")
    if not buried and rel is not None and rel > 0.5 and mutant in ("LEU", "ILE", "VAL", "PHE", "MET", "TRP") and wt not in ("LEU", "ILE", "VAL", "PHE", "MET", "TRP"):
        flags.append("hydrophobic side chain on the surface")
    if not fits and mutant not in ("GLY", "ALA"):
        flags.append("none of the %d sampled rotamers (%.0f%% of the library probability) fits the rigid backbone without "
                     "clashes: the backbone or neighbours would have to "
                     "move; real proteins often do (several such mutants have been crystallised), so read this as strain, "
                     "not impossibility" % (n, 100 * sum(x["prob"] for x in per)))
    if flags:
        out["notes"] = flags
    out["caveat"] = ("Rigid backbone and neighbours, this one model (no symmetry mates or other models), Dunbrack rotamers; "
                     "overlaps are geometric, not energies; polar partners are N/O pairs within 3.5 A (no angles); His counted "
                     "as charged. A structural inspection, not a stability (ddG) or pathogenicity prediction.")
    out["place_command"] = "swapaa %s %s rotLib Dunbrack criteria cp" % (out["residue"], mutant)
    return out


def mutation_effect_spec(session, model: str, variant: str = "", residue: str = "", to: str = "", chain: str = "") -> Dict[str, Any]:
    """Resolve the residue from a variant ('R175H') or a spec plus target, check the wild type, then inspect."""
    from chimerax.core.commands import atomspec
    if not variant and residue and parse_variant(residue):
        variant, residue = residue, ""
    parsed = parse_variant(variant) if variant else None
    if variant and not parsed:
        return {"error": "Could not read '%s' as a substitution; write it like R175H or Arg175His." % variant}
    target = parsed[2] if parsed else (AA1.get(to.upper()) if len(to or "") == 1 else (to or "").upper())
    if target not in AA3:
        if not (to or variant):
            return {"error": "Give the substitution as variant='L99A' (or residue='#1:99' with to='ALA')."}
        return {"error": "Unknown amino acid '%s'." % (to or variant)}
    # small models pass the model as a name ("2lzm", "T4 lysozyme") and the residue as "L99" or "99": accept those
    from chimerax.atomic import AtomicStructure
    structs = [x for x in session.models.list(type=AtomicStructure) if len(x.id) == 1 or not isinstance(x.parent, AtomicStructure)]
    m = (model or "").strip()
    if not re.match(r"^#?\d+(\.\d+)*$", m):
        named = [x for x in structs if m and (x.name.lower() == m.lower() or x.name.lower().startswith(m.lower() + " "))]
        if len(named) == 1:
            m = "#" + named[0].id_string
        elif len(structs) == 1 or not m:
            m = "#" + structs[0].id_string if structs else "#1"
        else:
            return {"error": "Which structure? Open: %s. Give model as '#N'." % ", ".join("#%s %s" % (x.id_string, x.name) for x in structs[:8])}
    m = m if m.startswith("#") else "#" + m
    rm = re.match(r"^(?:([A-Za-z]{3}|[A-Za-z])\s*)?(-?\d+)([A-Za-z]?)$", (residue or "").strip())
    if residue and rm and not residue.strip().startswith((":", "#", "/")):
        if not parsed and rm.group(1):
            wt3 = AA1.get(rm.group(1).upper()) if len(rm.group(1)) == 1 else rm.group(1).upper()
            parsed = (wt3, int(rm.group(2)), target) if wt3 in AA3 else None
        residue = "%s%s:%s%s" % (m, ("/" + chain) if chain else "", rm.group(2), rm.group(3))
    if residue and residue.strip().startswith((":", "/")):
        residue = m + residue.strip()      # a spec without a model means the requested model, not every model
    if residue:
        spec = residue
    else:
        spec = "%s%s:%d" % (m, ("/" + chain) if chain else "", parsed[1])
    try:
        sp, _, _ = atomspec.AtomSpecArg.parse(spec, session)
        res = sp.evaluate(session).atoms.unique_residues
    except Exception as e:  # noqa: BLE001
        return {"error": "Could not read '%s': %s" % (spec, e)}
    res = [x for x in res if x.polymer_type == x.PT_AMINO]
    if not res:
        return {"error": "No amino-acid residue matches %s. Check the numbering with map_numbering (structure and UniProt "
                         "numbering often differ)." % spec}
    if parsed:
        match = [x for x in res if x.name == parsed[0]]
        if not match and residue:
            # the model invented a residue spec next to a correct variant: try the variant's own number
            try:
                alt = "%s%s:%d" % (m, ("/" + chain) if chain else "", parsed[1])
                sp2, _, _ = atomspec.AtomSpecArg.parse(alt, session)
                res2 = [x for x in sp2.evaluate(session).atoms.unique_residues if x.polymer_type == x.PT_AMINO]
                match = [x for x in res2 if x.name == parsed[0]]
                if match:
                    spec = alt
            except Exception:  # noqa: BLE001
                pass
        if not match:
            return {"error": "%s is %s, not %s. The structure may number residues differently from the variant (UniProt "
                             "numbering): check the chain's residue range in the state, or use map_numbering with the protein's "
                             "UniProt accession, then call again with residue=<spec>." % (
                                 spec, "/".join(sorted({x.name for x in res})), parsed[0])}
        res = match
    out = mutation_effect(session, res[0], target)
    if len(res) > 1:
        out["other_chains_with_this_residue"] = ["/%s" % x.chain_id for x in res[1:]][:8]
    return out
