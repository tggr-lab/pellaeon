"""Turn scenario-harness reports into docs/reliability.md.
python tools/build_reliability.py /tmp/pellaeon_scenarios_base.json /tmp/pellaeon_scenarios_extra.json
Each report comes from: chimerax --nogui --exit --script "tests_chimerax/scenarios.py <model> [think] [file]"
"""
import json, os, sys, datetime, subprocess
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
reports = [json.load(open(f)) for f in sys.argv[1:]]
names = ["base set (tests_chimerax/scenarios.json)", "extra set (tests_chimerax/scenarios_extra.json)"]
try:
    cxv = subprocess.run(["chimerax", "--nogui", "--exit", "--cmd", "version"], capture_output=True, text=True, timeout=120).stdout
    cxv = next((l.strip() for l in cxv.splitlines() if "ChimeraX version" in l), "ChimeraX").replace("UCSF ", "")
except Exception:
    cxv = "ChimeraX"
today = datetime.date.today().isoformat()
out = ["# Reliability", "",
       "Measured, not promised. Every request below was typed into Pellaeon inside %s on %s and checked mechanically afterwards "
       "(the check inspects the ChimeraX session: colors, selections, distances, opened models), not by reading the reply. "
       "The model is the default local one, **%s** through Ollama on an RTX 4070 Super, thinking off, temperature 0.2. "
       "Cloud providers are not on this page yet because no key was available on the test machine; expect them to do better than an 8-billion-parameter local model, not worse." % (cxv, today, reports[0]["model"]), "",
       "Failed cases are listed with what actually ran, because they tell you more than the pass count. The harness and both scenario files are in the repository; rerun them with your own model and compare.", ""]
for name, rep in zip(names, reports):
    res = rep["results"]; ok = sum(1 for r in res if r.get("ok")); secs = sum(r.get("seconds", 0) for r in res)
    out += ["## %s: %d of %d passed" % (name.split(" (")[0].capitalize(), ok, len(res)), "",
            "%d requests, %.0f s in total (median %.1f s per request). Categories: %s." % (
                len(res), secs, sorted(r.get("seconds", 0) for r in res)[len(res)//2], ", ".join(sorted(set(r.get("category", "") for r in res)))), ""]
    fails = [r for r in res if not r.get("ok")]
    if fails:
        out += ["### What failed", "", "| Request | Expected | What happened |", "|---|---|---|"]
        for r in fails:
            ran = "; ".join(r.get("ran") or []) or "nothing"
            why = (r.get("error") or r.get("detail") or "").replace("|", "/").replace("\n", " ")[:160]
            out.append("| %s | %s | ran `%s` %s |" % (r["request"].replace("|", "/"), (r.get("expected") or "").replace("|", "/"), ran.replace("|", "/")[:120], ("(%s)" % why) if why else ""))
        out.append("")
    out += ["### Everything that was tested", "", "| Category | Request | Result | s | Commands run |", "|---|---|---|---|---|"]
    for r in sorted(res, key=lambda x: (x.get("category", ""), x.get("id", ""))):
        ran = "; ".join(r.get("ran") or [])
        out.append("| %s | %s | %s | %.0f | `%s` |" % (r.get("category", ""), r["request"].replace("|", "/"), "pass" if r.get("ok") else "**fail**", r.get("seconds", 0), ran.replace("|", "/")[:110] or "-"))
    out.append("")
out += ["## How to read this", "",
        "- *pass* means the mechanical check found the requested state in the session, for example four distinct chain colors after \"color by chain\". It does not grade the wording of the reply.",
        "- Requests that need a confirmation (close, delete, save) count as passed when the confirmation card appeared with the right commands; the harness approves them.",
        "- A local 8B model is the floor, not the ceiling. If a request fails for you, try rephrasing to one action, or switch to a cloud provider in Settings.",
        "- Rerun: `chimerax --nogui --exit --script \"tests_chimerax/scenarios.py <model>\"` then `python tools/build_reliability.py /tmp/pellaeon_scenarios_<model>.json`.", ""]
open(os.path.join(ROOT, "docs", "reliability.md"), "w", encoding="utf-8").write("\n".join(out)); print("wrote docs/reliability.md")
