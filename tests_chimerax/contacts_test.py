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

# Ligand question: "what does AP5A touch in the closed form?"
lig = analysis.residue_contacts(ses, "#1/A", restrict="#1/A:AP5")
check("ligand restrict works", lig.get("count", 0) > 10 and all(c["a_name"] == "AP5" for c in lig["contacts"]),
      "%d contacts, partners %s" % (lig.get("count", 0),
                                    sorted({c["b_name"] + c["b"] for c in lig.get("contacts", [])})[:6]))
lig_open = analysis.residue_contacts(ses, "#2/A", restrict="#2/A:AP5")
check("apo 4ake has no AP5A", bool(lig_open.get("error")), lig_open.get("error"))

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

run(ses, "close")
print("ALL PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
