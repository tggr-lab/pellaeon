"""Headless check of the fetched annotation overlays (needs the network):
chimerax --nogui --exit --script tests_chimerax/annot_sources_test.py

Fetches AlphaMissense for the PAR2 AlphaFold model and ConSurf-DB grades for 1ubq, then drives the
existing table-overlay path (agent._table_overlay) over each, exactly as table_overlay_test.py does.
"""
import os
import sys
import tempfile

from chimerax.core.commands import run
from chimerax.pellaeon.bridge import ChimeraXExecutor
from chimerax.pellaeon.core.agent import Agent
from chimerax.pellaeon.core.tables import guess_columns

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Prefer the working copy over whatever is installed, so the check tests what is being edited.
sys.path.insert(0, os.path.join(REPO, "src"))
from core import annot_sources  # noqa: E402

CACHE = os.path.join(tempfile.gettempdir(), "pellaeon_annot_cache")


class NoProvider:
    name = "none"
    supports_vision = False

    def stream(self, *a, **k):
        raise RuntimeError("not used")


def load(agent, dataset, name):
    """Put a fetched dataset where _table_overlay looks for it (the same dict an imported CSV makes)."""
    g = guess_columns(dataset["columns"], dataset["rows"])
    agent.tables[name] = {"id": name, "name": name, "path": dataset.get("source_url", ""),
                          "columns": dataset["columns"], "rows": dataset["rows"], "guess": g,
                          "accession": dataset.get("accession", "")}
    return g


def report(tag, out, expect_min):
    if out.get("error"):
        print("FAIL %s: %s" % (tag, out["error"]))
        return
    ok = out.get("mapped", 0) >= expect_min and out.get("colored")
    print("%s %s: %d residues colored, %s; numbering: %s"
          % ("PASS" if ok else "FAIL", tag, out.get("mapped", 0), out.get("legend"), out.get("numbering")))
    if out.get("n_mismatches"):
        print("  NOTE %s reference-residue mismatches, e.g. %s" % (out["n_mismatches"], out.get("mismatches", [])[:3]))


ex = ChimeraXExecutor(session)  # noqa: F821 - ChimeraX injects `session`
agent = Agent(NoProvider(), ex)

# --- AlphaMissense on the PAR2 AlphaFold model (UniProt numbering) --------------------------------
run(session, "open alphafold:P55085", log=False)  # noqa: F821
am = annot_sources.alphamissense("P55085", CACHE)
if am.get("error"):
    print("FAIL alphamissense fetch: %s" % am["error"])
else:
    print("alphamissense: %d positions, %d variants, source %s" % (am["n_positions"], am["n_variants"], am["source"]))
    load(agent, am, "alphamissense_P55085")
    out = agent._table_overlay("alphamissense_P55085", "am_mean", None, "blue-white-red", "#1", am["accession"], False)
    report("alphamissense mean", out, 350)
    out = agent._table_overlay("alphamissense_P55085", "am_class", None, "", "#1", am["accession"], False)
    print("  classes:", out.get("legend", out.get("error")))

# --- ConSurf-DB on 1ubq (PDB numbering, no accession) ---------------------------------------------
run(session, "close; open 1ubq", log=False)  # noqa: F821
cs = annot_sources.conservation("1UBQ", "A", CACHE)
if cs.get("error"):
    print("FAIL conservation fetch: %s" % cs["error"])
else:
    print("conservation: %d positions, source %s" % (cs["n_positions"], cs["source"]))
    load(agent, cs, "consurf_1UBQ_A")
    out = agent._table_overlay("consurf_1UBQ_A", "consurf_grade", None, "blue-white-red", "#1", None, False)
    report("consurf grade", out, 70)

# --- the error path a user will actually hit ------------------------------------------------------
print("PASS error path" if "PDB" in (annot_sources.conservation("P55085", "A", CACHE).get("error") or "")
      else "FAIL error path")
