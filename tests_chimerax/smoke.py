"""Smoke test that runs INSIDE ChimeraX (no GUI needed):

    chimerax --nogui --exit --script tests_chimerax/smoke.py

Checks the bridge against a real session: docs index, command execution with
log capture, error capture, state snapshot, command usage.
"""
import sys
import time

from chimerax.pellaeon.bridge import ChimeraXExecutor, docs_dirs, chimerax_version

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(detail) if detail else ""))
    ok = ok and bool(cond)


ex = ChimeraXExecutor(session)  # noqa: F821
print("ChimeraX", chimerax_version(), "docs dirs:", docs_dirs())
t0 = time.time()
idx = ex.ensure_index()
check("docs index", len(idx.chunks) > 500, "%d passages in %.1fs" % (len(idx.chunks), time.time() - t0))
hits = ex.search_docs("color chain A blue", 3)
check("search", hits and hits[0]["command"] == "color", [h["command"] for h in hits])

r = ex.run_commands(["open 4hhb", "color red", "cartoon"])
check("run ok", all(x["ok"] for x in r) and len(r) == 3, [x["error"] for x in r])
check("log captured", any("4hhb" in i for i in r[0]["info"]), r[0]["info"][:2])

st = ex.get_state()
check("state models", st["models"] and st["models"][0]["id"] == "#1", st["models"][:1])
check("state chains", st["models"][0].get("chains"), st["models"][0].get("chains"))

r = ex.run_commands(["select #1:10-20", "colr red", "color blue"])
check("error captured", len(r) == 2 and not r[1]["ok"] and r[1]["error"], r[1]["error"])
st = ex.get_state()
check("selection", st["selection"].get("num_residues") == 44, st["selection"])
check("last error in state", "colr" in st.get("last_error", ""), st.get("last_error"))

u = ex.command_usage("color")
check("usage", "color" in u.lower() and len(u) > 20, u[:80].replace("\n", " "))
u2 = ex.command_usage("cartoon style")
check("usage multiword", "cartoon" in u2.lower(), u2[:60].replace("\n", " "))

py = ex.run_python("print(len(session.models.list()))")
check("python", py["ok"] and py["stdout"].strip().isdigit(), py)

ex.run_commands(["close"])
print("ALL PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
