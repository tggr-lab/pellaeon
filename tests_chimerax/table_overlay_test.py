"""Headless check of table overlays: chimerax --nogui --exit --script tests_chimerax/table_overlay_test.py"""
import os, sys
from chimerax.core.commands import run
from chimerax.pellaeon.bridge import ChimeraXExecutor
from chimerax.pellaeon.core.agent import Agent
from chimerax.pellaeon.core.tables import parse_table, guess_columns
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
run(session, "open 1ubq", log=False)
ex = ChimeraXExecutor(session)
class NoProvider:
    name = "none"; supports_vision = False
    def stream(self, *a, **k): raise RuntimeError("not used")
agent = Agent(NoProvider(), ex)
path = os.path.join(ROOT, "docs", "examples", "ubiquitin_hydropathy.csv")
t = parse_table(open(path).read(), path); g = guess_columns(t["columns"], t["rows"])
agent.tables = {"ubiquitin_hydropathy": {"id": "u", "name": "ubiquitin_hydropathy", "path": path, "columns": t["columns"], "rows": t["rows"], "guess": g}}
out = agent._table_overlay("", "hydropathy", None, "", "#1", None, False)
print("PASS numeric" if out.get("mapped") == 76 and out.get("colored") and not out.get("n_mismatches") else "FAIL numeric %s" % out)
from chimerax.atomic import all_atomic_structures
m = all_atomic_structures(session)[0]
vals = [getattr(r, "pellaeon_ubiquitin_hydropathy_hydropathy", None) for r in m.residues if r.chain_id == "A"]
print("PASS attr" if vals[0] == 1.9 and vals[2] == 4.5 else "FAIL attr %s" % vals[:5])
cols = {tuple(r.ribbon_color) for r in m.residues}
print("PASS colors" if len(cols) > 5 else "FAIL colors %d" % len(cols))
out2 = agent._table_overlay("", "class", None, "", "#1", None, True)
print("PASS categorical" if out2.get("mapped") == 76 and "hydrophobic" in out2.get("legend", "") and any("label" in c["command"] for c in out2["commands"]) else "FAIL categorical %s" % out2)
print("layers", [(l["column"], l["mapped"]) for l in agent.layers])
# a table with wrong reference residues must be reported, not silently applied
bad = parse_table("position,wt,score\n1,W,1\n2,Q,2\n3,I,3\n500,A,4\n"); gb = guess_columns(bad["columns"], bad["rows"])
agent.tables["bad"] = {"id": "b", "name": "bad", "path": "bad.csv", "columns": bad["columns"], "rows": bad["rows"], "guess": gb}
out3 = agent._table_overlay("bad", "score", None, "white-red", "#1", None, False)
print("PASS mismatch" if out3.get("mapped") == 2 and out3.get("n_mismatches") == 1 and out3.get("missing") == [500] else "FAIL mismatch %s" % out3)
print("PASS state" if "Loaded table 'bad'" in __import__("chimerax.pellaeon.core.prompt", fromlist=["format_state"]).format_state(dict(ex.get_state(), tables=agent._tables_state())) else "FAIL state")
