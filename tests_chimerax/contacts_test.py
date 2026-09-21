"""Contact comparison against real structures, INSIDE ChimeraX (no GUI needed):

    chimerax --nogui --exit --script tests_chimerax/contacts_test.py

Adenylate kinase is the textbook case: 1ake is the closed form with the bis-substrate
inhibitor AP5A, 4ake is the open apo form. Closing the LID and NMP lobes onto the
substrate must both break and make contacts, so an empty 'lost' or 'gained' list means
the collector or the pairing is wrong, not that nothing happened.

The modules are loaded from the repository's src/ (not from the installed bundle), so the
check tests the working copy without a `devel install`.
"""
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")

# Register src/ as a package so analysis.py's relative `from .core.contacts import ...` resolves.
_pkg = types.ModuleType("pellaeon_repo")
_pkg.__path__ = [SRC]
sys.modules["pellaeon_repo"] = _pkg
_core = types.ModuleType("pellaeon_repo.core")
_core.__path__ = [os.path.join(SRC, "core")]
sys.modules["pellaeon_repo.core"] = _core

import importlib

analysis = importlib.import_module("pellaeon_repo.analysis")
contacts_mod = importlib.import_module("pellaeon_repo.core.contacts")

from chimerax.core.commands import run

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(detail) if detail else ""))
    ok = ok and bool(cond)


ses = session  # noqa: F821

run(ses, "close")
run(ses, "open 1ake")          # closed, with AP5A
run(ses, "open 4ake")          # open, apo

prep = analysis.prepare_compare(ses, "#1", "#2", "A")
check("prepare_compare", not prep.get("error") and prep["ref_spec"] == "#1/A", prep)

mm = run(ses, "matchmaker %s to %s" % (prep["other_spec"], prep["ref_spec"]))
returns = mm if isinstance(mm, list) else [mm]
pairing_out = analysis.residue_pairing(ses, prep, returns)
check("pairing from matchmaker", pairing_out.get("basis") == "matchmaker alignment"
      and pairing_out.get("paired_residues", 0) > 150, pairing_out.get("paired_residues"))
pairing = pairing_out["pairing"]

ref = analysis.residue_contacts(ses, "#1/A")
oth = analysis.residue_contacts(ses, "#2/A")
check("collected reference contacts", ref.get("count", 0) > 200, ref.get("count"))
check("collected compared contacts", oth.get("count", 0) > 200, oth.get("count"))
check("contact dict shape", ref["contacts"] and set(ref["contacts"][0]) >=
      {"a", "a_name", "b", "b_name", "min_dist", "kind"}, sorted(ref["contacts"][0]) if ref.get("contacts") else None)
check("no intra-residue or sequential pairs",
      all(c["a"] != c["b"] and not (c["a"].split(":")[0] == c["b"].split(":")[0]
          and abs(int(re.sub(r"\D", "", c["a"].split(":")[1])) - int(re.sub(r"\D", "", c["b"].split(":")[1]))) <= 1)
          for c in ref["contacts"]))
kinds = {c["kind"] for c in ref["contacts"]}
check("kinds classified", kinds == {"salt bridge", "hbond-capable", "contact"}, sorted(kinds))
salt = [c for c in ref["contacts"] if c["kind"] == "salt bridge"]
check("1ake chain A has salt bridges", len(salt) > 5,
      "%d, e.g. %s" % (len(salt), ["%s%s-%s%s" % (c["a_name"], c["a"], c["b_name"], c["b"]) for c in salt[:3]]))

res = contacts_mod.compare_contacts(ref["contacts"], oth["contacts"], pairing)
counts = res["counts"]
print("COUNTS", counts)
print("SUMMARY", res["summary"])
print("BY KIND", res["by_kind"])
print("TOP LOST", [(c["a_name"] + c["a"], c["b_name"] + c["b"], c["kind"], c["min_dist"]) for c in res["lost"][:6]])
print("TOP GAINED", [(c["a_name"] + c["a"], c["b_name"] + c["b"], c["kind"], c["min_dist"]) for c in res["gained"][:6]])

check("closure both breaks and makes contacts", counts["lost"] > 10 and counts["gained"] > 10,
      "lost %d, gained %d, kept %d" % (counts["lost"], counts["gained"], counts["kept"]))
check("most contacts survive the hinge motion", counts["kept"] > counts["lost"], counts)
check("summary names residues", re.search(r"[A-Z]{3} \d+", res["summary"]) is not None, res["summary"])
check("salt bridges change", res["by_kind"].get("salt bridge", {}).get("lost", 0)
      + res["by_kind"].get("salt bridge", {}).get("gained", 0) > 0, res["by_kind"].get("salt bridge"))

# A structure compared with itself through the identity pairing must report no change at all:
# any difference here would be a bug in the collector or the comparison, not biology.
self_pairing = {k: k for k in {c["a"] for c in ref["contacts"]} | {c["b"] for c in ref["contacts"]}}
self_res = contacts_mod.compare_contacts(ref["contacts"], ref["contacts"], self_pairing)
check("self-comparison is empty", self_res["counts"]["lost"] == 0 and self_res["counts"]["gained"] == 0,
      self_res["counts"])

# Comparing by residue number instead of through the alignment is what this module exists to
# avoid; with these two entries (same numbering) it happens to agree, so check the machinery
# on a deliberately shifted pairing instead.
shifted = {k: "/A:%d" % (int(v.split(":")[1]) + 500) for k, v in pairing.items()}
shifted_res = contacts_mod.compare_contacts(ref["contacts"], oth["contacts"], shifted)
check("a wrong pairing loses everything (so the pairing is really used)",
      shifted_res["counts"]["kept"] == 0, shifted_res["counts"])

# Ligand question: "what does AP5A touch in the closed form?" - written the way the agent writes
# it, through model_restriction, so the rewriting is tested and not bypassed.
ref_restrict = contacts_mod.model_restriction(":AP5", "1")
check("a residue-name restriction is qualified for one model", ref_restrict == "#1:AP5", ref_restrict)
lig = analysis.residue_contacts(ses, "#1/A", restrict=ref_restrict)
check("ligand restrict works", lig.get("count", 0) > 10 and all(c["a_name"] == "AP5" for c in lig["contacts"]),
      "%d contacts, partners %s" % (lig.get("count", 0),
                                    sorted({c["b_name"] + c["b"] for c in lig.get("contacts", [])})[:6]))
arg_partners = {int(re.sub(r"\D", "", c["b"].split(":")[1])) for c in lig.get("contacts", []) if c["b_name"] == "ARG"}
check("the AP5A pocket arginines are reported", {36, 119, 123, 156, 167} <= arg_partners, sorted(arg_partners))
check("the restricted side is reported as `a`", all(c["a"] == "/A:215" for c in lig["contacts"]),
      sorted({c["a"] for c in lig["contacts"]}))
check("each restricted pair is reported once",
      len({(c["a"], c["b"]) for c in lig["contacts"]}) == len(lig["contacts"]), lig.get("count"))

# A selector word must become an intersection: '#1/ligand' would ask for a chain named "ligand".
sel = contacts_mod.model_restriction("ligand", "1")
lig_sel = analysis.residue_contacts(ses, "#1/A", restrict=sel)
check("the 'ligand' selector is intersected with the model",
      sel == "#1 & ligand" and lig_sel.get("count", 0) > 10, "%s -> %s" % (sel, lig_sel.get("count", lig_sel.get("error"))))

# The apo form: a valid restriction matching nothing is an answer, not a collector error, and the
# ligand cannot be addressed in it at all because no residue of it is in the alignment.
lig_open = analysis.residue_contacts(ses, "#2/A", restrict=contacts_mod.model_restriction(":AP5", "2"))
check("apo 4ake has no AP5A", lig_open.get("empty_selection") is True and not lig_open.get("error"),
      lig_open.get("reason"))
check("an unpaired ligand is not translated into the other model",
      contacts_mod.translate_restriction(lig.get("restrict_residues") or [], pairing, "2") == "",
      lig.get("restrict_residues"))
lig_cmp = contacts_mod.compare_contacts(lig["contacts"], [], pairing)
check("the ligand's contacts are unjudged, not lost",
      lig_cmp["counts"]["lost"] == 0 and lig_cmp["counts"]["unmapped_reference"] == lig["count"]
      and lig_cmp["summary"].startswith("No contacts could be compared"), lig_cmp["summary"])

# A restriction that IS in the alignment is carried across by residue, not by number.
pocket = analysis.residue_contacts(ses, "#1/A", restrict=contacts_mod.model_restriction("/A:36", "1"))
check("a paired restriction translates to the other model",
      contacts_mod.translate_restriction(pocket.get("restrict_residues") or [], pairing, "2") == "#2/A:36",
      pocket.get("restrict_residues"))

# The visualisation plan must be commands ChimeraX actually accepts.
cmds = contacts_mod.contact_commands(res, prep["ref_spec"], prep["other_spec"], max_drawn=8)
failed = []
for cmd in cmds:
    try:
        run(ses, cmd)
    except Exception as e:  # noqa: BLE001
        failed.append("%s -> %s" % (cmd, e))
check("every visualisation command runs", not failed, failed[:3])
from chimerax.atomic import all_pseudobond_groups
drawn = sum(len(g.pseudobonds) for g in all_pseudobond_groups(ses) if g.name == "distances")
check("pseudobonds drawn", drawn >= 8, drawn)


# --------------------------------------------------------------------------------------
# Synthetic geometry: the cases where a real structure cannot say which atom pair was used.
# --------------------------------------------------------------------------------------

def build(name, residues):
    """A tiny AtomicStructure: residues = [(resname, chain, number, insertion, [(atom, element, xyz)])]."""
    from chimerax.atomic import AtomicStructure
    st = AtomicStructure(ses, name=name)
    for res_name, chain_id, number, icode, atoms in residues:
        r = st.new_residue(res_name, chain_id, number, insert=icode)
        for atom_name, element, xyz in atoms:
            a = st.new_atom(atom_name, element)
            a.coord = xyz
            r.add_atom(a)
    ses.models.add([st])
    return "#" + st.id_string


run(ses, "close")

# A salt bridge whose charged atoms are NOT each other's nearest neighbours: classifying only the
# closest atom pair (or each atom's closest partner) reports a plain contact.
buried = build("buried salt bridge", [
    ("ARG", "A", 10, "", [("NH1", "N", (0, 0, 0)), ("CB", "C", (3.8, 3.5, 0))]),
    ("ASP", "A", 20, "", [("OD1", "O", (3.8, 0, 0)), ("CB", "C", (0, -3.5, 0))]),
])
syn = analysis.residue_contacts(ses, buried)
check("a salt bridge is found even when carbon is closer",
      len(syn.get("contacts", [])) == 1 and syn["contacts"][0]["kind"] == "salt bridge",
      syn.get("contacts", syn.get("error")))
check("the closest atom pair is still what gets drawn",
      syn["contacts"][0]["min_dist"] == 3.5, syn["contacts"][0])

# "At least one selected atom within the cutoff" must be measured from the selected atoms.
reach = build("atom restriction", [
    ("ALA", "A", 10, "", [("CA", "C", (0, 0, 0)), ("CB", "C", (5, 0, 0))]),
    ("LEU", "A", 30, "", [("CD1", "C", (8, 0, 0))]),
])
all_atoms = analysis.residue_contacts(ses, reach, restrict=reach + "/A:10")
one_atom = analysis.residue_contacts(ses, reach, restrict=reach + "/A:10@CA")
check("the whole residue does reach the partner", all_atoms.get("count") == 1, all_atoms.get("count"))
check("the selected atom alone does not", one_atom.get("count") == 0 and not one_atom.get("error"),
      one_atom.get("contacts", one_atom.get("error")))

# Two selected residues touching each other are a contact, reported once.
both = build("both selected", [
    ("ALA", "A", 10, "", [("CB", "C", (0, 0, 0))]),
    ("LEU", "A", 20, "", [("CD1", "C", (3.0, 0, 0))]),
    ("GLY", "A", 40, "", [("CA", "C", (20, 0, 0))]),
])
inside = analysis.residue_contacts(ses, both, restrict=both + "/A:10,20")
check("a contact between two selected residues is reported once",
      inside.get("count") == 1 and inside["contacts"][0]["b"] == "/A:20", inside.get("contacts", inside.get("error")))

# Insertion codes: the pairing and the contacts must name a residue the same way, or every
# contact of an inserted residue silently becomes "not in the alignment".
ins = [("ALA", "A", 10, "", [("CB", "C", (0, 0, 0))]),
       ("SER", "A", 10, "A", [("OG", "O", (3.0, 0, 0))]),
       ("LEU", "A", 12, "", [("CD1", "C", (3.0, 3.0, 0))])]
ins_a = build("inserted a", ins)
ins_b = build("inserted b", ins)
prep_i = analysis.prepare_compare(ses, ins_a, ins_b)
pair_i = analysis.residue_pairing(ses, prep_i)          # no matchmaker: the chain+number fallback
check("the fallback pairing keeps insertion codes", pair_i.get("pairing", {}).get("/A:10A") == "/A:10A",
      sorted(pair_i.get("pairing", {})))
ins_ref = analysis.residue_contacts(ses, ins_a)
ins_oth = analysis.residue_contacts(ses, ins_b)
ins_cmp = contacts_mod.compare_contacts(ins_ref["contacts"], ins_oth["contacts"], pair_i["pairing"])
check("an inserted residue's contacts are judged, not dropped",
      ins_cmp["counts"]["unmapped_reference"] == 0 and ins_cmp["counts"]["lost"] == 0
      and ins_cmp["counts"]["kept"] == len(ins_ref["contacts"]) > 0, ins_cmp["counts"])

run(ses, "close")

# --------------------------------------------------------------------------------------
# A deliberately renumbered copy of the same structure: same coordinates, different numbers.
# Everything must still pair through the alignment, and nothing may be reported as lost.
# --------------------------------------------------------------------------------------

run(ses, "open 1ake")
run(ses, "open 1ake")
run(ses, "renumber #2/A start 101")             # the whole chain moves by +100 ...
run(ses, "renumber #2/A:201-314 start 1001")    # ... and its second half again, so the offset is
                                                # not uniform and no number-based test can survive
prep_r = analysis.prepare_compare(ses, "#1", "#2", "A")
mm_r = run(ses, "matchmaker %s to %s" % (prep_r["other_spec"], prep_r["ref_spec"]))
pair_r = analysis.residue_pairing(ses, prep_r, mm_r if isinstance(mm_r, list) else [mm_r])
check("the renumbered copy pairs through the alignment",
      pair_r.get("basis") == "matchmaker alignment" and pair_r.get("paired_residues", 0) > 150,
      pair_r.get("paired_residues"))
check("the pairing carries the renumbering",
      pair_r["pairing"].get("/A:36") == "/A:136" and pair_r["pairing"].get("/A:150") == "/A:1050",
      [pair_r["pairing"].get("/A:36"), pair_r["pairing"].get("/A:150")])
ref_r = analysis.residue_contacts(ses, "#1/A")
oth_r = analysis.residue_contacts(ses, "#2/A")
res_r = contacts_mod.compare_contacts(ref_r["contacts"], oth_r["contacts"], pair_r["pairing"])
print("RENUMBERED COUNTS", res_r["counts"])
check("the same structure renumbered loses and gains nothing",
      res_r["counts"]["lost"] == 0 and res_r["counts"]["gained"] == 0, res_r["counts"])
check("and its interactions do not change type", res_r["counts"]["kind_changed"] == 0, res_r["counts"])

run(ses, "close")
print("ALL PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
