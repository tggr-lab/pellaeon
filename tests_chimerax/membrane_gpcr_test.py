"""membrane_view (OPM orientation) and gpcr_states (GPCRdb) as plain commands, headless (needs internet).

    chimerax --nogui --exit --script tests_chimerax/membrane_gpcr_test.py
"""
import numpy as np
from chimerax.core.commands import run
from chimerax.pellaeon.bridge import ChimeraXExecutor

ex = ChimeraXExecutor(session)  # noqa: F821
fails = 0


def check(name, ok, extra=""):
    global fails
    fails += not ok
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, extra))


run(session, "open 3vw7", log=False)  # noqa: F821
r = ex.membrane_orient("#1")
check("membrane_orient returns without error", "error" not in r, r.get("error", ""))
check("OPM entry recognised from the model name", r.get("opm_entry") == "3VW7")
cam = session.main_view.camera  # noqa: F821
up = cam.position.inverse().transform_vector(np.array([0.0, 0.0, 1.0]))
check("membrane normal points up on screen", abs(up[1] - 1.0) < 0.05, str(np.round(up, 2)))
m1 = [m for m in session.models.list() if m.id_string == "1"][0]  # noqa: F821
ecl = m1.residues.filter((m1.residues.numbers == 100) & (m1.residues.chain_ids == "A"))[0].atoms[0].scene_coord
icl = m1.residues.filter((m1.residues.numbers == 300) & (m1.residues.chain_ids == "A"))[0].atoms[0].scene_coord
check("extracellular residue above the cytoplasmic one", ecl[2] > icl[2] + 20, "z %.1f vs %.1f" % (ecl[2], icl[2]))
names = [m.name for m in session.models.list()]  # noqa: F821
check("membrane slabs drawn", "pellaeon_membrane_out" in names and "pellaeon_membrane_in" in names)
check("OPM reference model closed again", not any("opm" in n.lower() or n.lower().startswith("3vw7.pdb") for n in names), str(names))

run(session, "close", log=False)  # noqa: F821
run(session, "open alphafold:P25116", log=False)  # noqa: F821
r = ex.membrane_orient("#1")
check("AlphaFold model of a GPCR finds its homologue through GPCRdb", "error" not in r and "GPCRdb" in r.get("reference_via", ""), r.get("error", ""))
run(session, "close", log=False)  # noqa: F821
run(session, "open alphafold:P25116", log=False)  # noqa: F821
r = ex.membrane_orient("#1", pdb="3vw7", slabs=False)
check("AlphaFold model oriented via a homologue's OPM entry", "error" not in r and r.get("slabs") is False, r.get("error", ""))

run(session, "close", log=False)  # noqa: F821
g = ex.gpcr_states("P25116", ["inactive", "active"])
check("GPCRdb structures listed", "error" not in g and g.get("count", 0) >= 3, g.get("error", ""))
pdbs = {s["pdb"] for s in g.get("structures", [])}
check("3VW7 and 8XOR among them", {"3VW7", "8XOR"} <= pdbs, str(sorted(pdbs)))
check("two multistate models opened", len(g.get("opened", [])) == 2, str(g.get("model_errors")))
check("animation hint names a morph", "morph" in g.get("animation", ""))
g2 = ex.gpcr_states("PAR1_HUMAN", [])
check("entry name accepted directly", "error" not in g2 and g2.get("entry") == "par1_human")
run(session, "pellaeon tool gpcr P25116", log=False)  # noqa: F821
run(session, "pellaeon tool membrane #1 pdb 3vw7", log=False)  # noqa: F821
check("plain commands run", True)
print("membrane/gpcr test: %d failures" % fails)
