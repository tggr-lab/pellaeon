"""A residue's structural surroundings, measured rather than guessed: secondary structure, burial,
neighbouring residues, hydrogen bonds and salt bridges, the nearest ligand, other chains it touches,
AlphaFold confidence. Read-only; used by explain_residue and the mutation tool.
"""
from __future__ import annotations

from typing import Any, Dict, List

# Maximum accessible surface area per residue type, theoretical values (Tien et al. 2013, PLoS ONE 8:e80635), A^2
MAX_ASA = {"ALA": 129.0, "ARG": 274.0, "ASN": 195.0, "ASP": 193.0, "CYS": 167.0, "GLN": 225.0, "GLU": 223.0,
           "GLY": 104.0, "HIS": 224.0, "ILE": 197.0, "LEU": 201.0, "LYS": 236.0, "MET": 224.0, "PHE": 240.0,
           "PRO": 159.0, "SER": 155.0, "THR": 172.0, "TRP": 285.0, "TYR": 263.0, "VAL": 174.0}
_CATION = {("ARG", "NH1"), ("ARG", "NH2"), ("ARG", "NE"), ("LYS", "NZ"), ("HIS", "ND1"), ("HIS", "NE2")}
_ANION = {("ASP", "OD1"), ("ASP", "OD2"), ("GLU", "OE1"), ("GLU", "OE2")}
CONTACT = 4.0      # heavy-atom distance for a contact, A
SALT = 4.0         # N-O distance for a salt bridge, A


def res_label(r) -> str:
    return "%s%d%s" % (r.name, r.number, r.insertion_code or "")


def res_spec(r) -> str:
    return "#%s/%s:%d%s" % (r.structure.id_string, r.chain_id, r.number, r.insertion_code or "")


def _heavy(atoms):
    return atoms.filter(atoms.element_names != "H")


def local_atoms(r, radius: float = 14.0):
    """Heavy atoms of the residue's structure within `radius` of the residue (no waters): the only ones that matter."""
    import numpy as np
    s = r.structure
    atoms = _heavy(s.atoms)
    cats = atoms.structure_categories
    atoms = atoms.filter(cats != "solvent")
    c = r.atoms.coords.mean(axis=0)
    d = np.linalg.norm(atoms.coords - c, axis=1)
    return atoms.filter(d < radius)


def relative_sasa(r, env_atoms=None) -> Dict[str, Any]:
    """Solvent accessible area of the residue in its structure (1.4 A probe; ligands and other chains count as
    burying it, waters do not), absolute and relative to the residue type's maximum."""
    from chimerax.surface import spheres_surface_area
    import numpy as np
    env = env_atoms if env_atoms is not None else local_atoms(r, 20.0)
    ids = set(id(a) for a in _heavy(r.atoms))
    mine = np.array([id(a) in ids for a in env], dtype=bool)
    if not mine.any():
        return {}
    areas = spheres_surface_area(env.coords, env.default_radii + 1.4, 600)   # not display radii (`size` changes those)
    areas[areas < 0] = 0    # -1 marks a sphere whose area calculation failed
    sasa = max(0.0, float(areas[mine].sum()))
    out = {"sasa": round(sasa, 1)}
    mx = MAX_ASA.get(r.name)
    if mx:
        rel = sasa / mx
        out["relative"] = round(rel, 2)
        out["class"] = "buried" if rel < 0.2 else ("partly exposed" if rel < 0.5 else "exposed")
    return out


def neighbours(r, env=None, cutoff: float = CONTACT) -> Dict[Any, float]:
    """Residues with a heavy atom within `cutoff` of one of r's heavy atoms -> closest distance. Excludes r itself."""
    import numpy as np
    env = env if env is not None else local_atoms(r)
    mine = _heavy(r.atoms)
    if not len(mine) or not len(env):
        return {}
    d = np.linalg.norm(env.coords[:, None, :] - mine.coords[None, :, :], axis=2).min(axis=1)
    out: Dict[Any, float] = {}
    for a, dist in zip(env, d):
        if dist <= cutoff and a.residue is not r:
            rr = a.residue
            if rr not in out or dist < out[rr]:
                out[rr] = float(dist)
    return out


def hbond_partners(session, r) -> List[Dict[str, Any]]:
    """Hydrogen bonds made by the residue (ChimeraX's hbonds criteria with the command's default slop)."""
    import numpy as np
    from chimerax.hbonds import find_hbonds
    s = r.structure
    out = []
    try:
        pairs = list(find_hbonds(session, [s], donors=r.atoms, dist_slop=0.4, angle_slop=20.0, status=False))
        pairs += list(find_hbonds(session, [s], acceptors=r.atoms, dist_slop=0.4, angle_slop=20.0, status=False))
    except Exception:  # noqa: BLE001
        return out
    seen = set()
    for d, a in pairs:
        if d.residue is r and a.residue is r:
            continue
        other, mine = (a, d) if d.residue is r else (d, a)
        if other.structure_category == "solvent":
            continue
        key = (mine.name, id(other))
        if key in seen:
            continue
        seen.add(key)
        out.append({"atom": mine.name, "partner": "%s@%s" % (res_label(other.residue), other.name),
                    "partner_spec": "%s@%s" % (res_spec(other.residue), other.name),
                    "chain": other.residue.chain_id, "distance": round(float(np.linalg.norm(d.scene_coord - a.scene_coord)), 2),
                    "backbone": mine.name in ("N", "O")})
    return out


def _charged(a) -> int:
    """+1 / -1 for the charged atoms of standard residues, the free N-terminal amine and C-terminal carboxylate
    (His counted as cationic, as in Barlow and Thornton's salt-bridge survey); 0 otherwise."""
    key = (a.residue.name, a.name)
    if key in _CATION:
        return 1
    if key in _ANION:
        return -1
    if a.name == "OXT":
        return -1
    if a.name == "N" and a.residue.polymer_type == a.residue.PT_AMINO and not any(
            n.name == "C" and n.residue is not a.residue for n in a.neighbors):
        return 1     # N-terminus: no peptide bond to a previous residue
    return 0


def salt_bridges(r, env=None) -> List[Dict[str, Any]]:
    import numpy as np
    env = env if env is not None else local_atoms(r)
    out = []
    mine = [a for a in r.atoms if _charged(a)]
    for a in mine:
        qa = _charged(a)
        for b in env:
            if b.residue is r or _charged(b) != -qa:
                continue
            dist = float(np.linalg.norm(a.coord - b.coord))
            if dist <= SALT:
                out.append({"atom": a.name, "partner": "%s@%s" % (res_label(b.residue), b.name),
                            "chain": b.residue.chain_id, "distance": round(dist, 2),
                            "his": "HIS" in (r.name, b.residue.name)})
    return out


def environment(session, r) -> Dict[str, Any]:
    """Everything above for one residue, compact enough for a model to read."""
    import numpy as np
    out: Dict[str, Any] = {}
    try:
        out["secondary_structure"] = "helix" if r.is_helix else ("strand" if r.is_strand else "loop/coil")
    except Exception:  # noqa: BLE001
        pass
    env = local_atoms(r)
    try:
        out["burial"] = relative_sasa(r, local_atoms(r, 20.0))
        if r.is_missing_heavy_template_atoms():
            out["burial"]["warning"] = "side chain incomplete in this structure: burial and contacts are underestimated"
    except Exception as e:  # noqa: BLE001
        out["burial"] = {"error": str(e)[:80]}
    near = neighbours(r, env)
    same, other_chain, ligands = [], [], []
    for rr, dist in sorted(near.items(), key=lambda kv: kv[1]):
        cat = rr.atoms[0].structure_category if len(rr.atoms) else ""
        entry = "%s %.1f" % (res_label(rr), dist)
        if cat in ("ligand", "ions"):
            ligands.append("%s (chain %s) %.1f A" % (res_label(rr), rr.chain_id, dist))
        elif rr.chain_id != r.chain_id:
            other_chain.append("%s/%s %.1f" % (rr.chain_id, res_label(rr), dist))
        elif abs(rr.number - r.number) > 1:
            same.append(entry)
    out["neighbours_within_4A"] = same[:14]
    if other_chain:
        out["other_chains_within_4A"] = other_chain[:10]
    if ligands:
        out["ligands_within_4A"] = ligands[:6]
    else:
        try:   # the nearest ligand even when it does not touch
            s = r.structure
            lig = s.atoms.filter(s.atoms.structure_categories == "ligand")
            if len(lig):
                d = np.linalg.norm(lig.scene_coords[:, None, :] - r.atoms.scene_coords[None, :, :], axis=2).min(axis=1)
                i = int(np.argmin(d))
                out["nearest_ligand"] = "%s (chain %s) %.1f A" % (res_label(lig[i].residue), lig[i].residue.chain_id, float(d[i]))
        except Exception:  # noqa: BLE001
            pass
    try:   # covalent and coordination partners are not "neighbours": name them
        special = []
        for a in r.atoms:
            for n in a.neighbors:
                if n.residue is not r and a.name == "SG" and n.name == "SG":
                    special.append("disulfide bond to %s" % (res_label(n.residue) if n.residue.chain_id == r.chain_id
                                                             else "%s/%s" % (n.residue.chain_id, res_label(n.residue))))
        metals = [b for b in env if b.element.is_metal]
        for b in metals:
            d = min(float(np.linalg.norm(a.coord - b.coord)) for a in r.atoms if a.element.name in ("N", "O", "S"))\
                if any(a.element.name in ("N", "O", "S") for a in r.atoms) else 99.0
            if d <= 2.8:
                special.append("coordinates %s of %s (chain %s) %.1f A" % (b.name, res_label(b.residue), b.residue.chain_id, d))
        if special:
            out["bonds_and_coordination"] = special[:6]
    except Exception:  # noqa: BLE001
        pass
    hb = hbond_partners(session, r)
    if hb:
        out["hydrogen_bonds"] = ["%s-%s %.1f A%s" % (h["atom"], h["partner"] if h["chain"] == r.chain_id else "%s/%s" % (h["chain"], h["partner"]),
                                                   h["distance"], " (backbone)" if h["backbone"] else "") for h in hb[:10]]
    sb = salt_bridges(r, env)
    best = {}
    for b in sb:   # one line per partner residue: its closest charged pair
        key = (b["chain"], b["partner"].split("@")[0])
        if key not in best or b["distance"] < best[key]["distance"]:
            best[key] = b
    sb = sorted(best.values(), key=lambda b: b["distance"])
    if sb:
        out["salt_bridges"] = ["%s-%s %.1f A%s" % (b["atom"], b["partner"] if b["chain"] == r.chain_id else "%s/%s" % (b["chain"], b["partner"]),
                                                  b["distance"], " (His: only if protonated)" if b.get("his") else "") for b in sb[:6]]
    try:
        if "alphafold" in (r.structure.name or "").lower() or getattr(r.structure, "_pellaeon_alphafold", False):
            ca = r.find_atom("CA")
            if ca is not None:
                out["plddt"] = round(float(ca.bfactor), 1)
    except Exception:  # noqa: BLE001
        pass
    return out
