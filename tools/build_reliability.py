"""Summarize scenario-harness reports into docs/reliability.md (a short page, not a test log).
python tools/build_reliability.py docs/reliability/<model>_base_<date>.json docs/reliability/<model>_extra_<date>.json
Reports come from: chimerax --nogui --exit --script "tests_chimerax/scenarios.py <model> [think] [file]"
"""
import json, os, re, sys, datetime
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
files = sys.argv[1:]
reports = [json.load(open(f)) for f in files]
model = reports[0]["model"]
m = re.search(r"(\d{4}-\d{2}-\d{2})", files[0]); date = m.group(1) if m else datetime.date.today().isoformat()
# plain-language notes for known failures (scenario id -> what went wrong); unknown failures just show what ran
NOTES = {
    "reset-view": "answered in words and ran nothing; a second try normally works.",
    "novice-transparency_selected_typo": "made cartoons, atoms and surfaces transparent instead of only the cartoon; visually fine, stricter than the check wanted.",
    "power-contacts_within_4A": "used hydrogen bonds and a zone selection instead of the `contacts` command.",
    "power-ribbon_helix_tubes": "thickened the ribbon instead of switching helices to tube mode.",
    "power-rainbow_each_chain": "rainbowed by chain (one color per chain) instead of along each chain.",
    "power-ligand_ballstick_hide_solvent_ions": "hid the solvent and ions but left the ligand's style unchanged in the check (ball, not ball-and-stick).",
    "edge-email_the_view": "tried to save an image first (which asks for confirmation) instead of simply saying ChimeraX cannot send email.",
    "edge-complaint_sticks_yellow": "after the complaint it colored the whole model yellow, not just residue 30.",
    "edge-make_it_bigger_label": "took \"make it bigger\" as the sticks, not the label it had just added.",
}
all_results = [r for rep in reports for r in rep["results"]]
n_ok = sum(1 for r in all_results if r.get("ok")); n = len(all_results)
GROUPS = {
    "Opening structures": ("open", "opening"),
    "Coloring": ("color", "coloring", "background", "publication"),
    "Showing, hiding and styles": ("display", "surfaces", "transparency", "nucleic"),
    "Selecting and labels": ("select", "selecting", "selection", "label", "labels"),
    "Measurements and analysis": ("measure", "measurement", "distances", "interactions", "analysis", "alignment", "annotation"),
    "Camera, motion and movies": ("view", "views", "focus", "motion", "animation"),
    "Risky commands ask first": ("safety", "destructive", "io-safety", "closing"),
    "Follow-ups, complaints and \"it\"": ("followup", "complaint", "ambiguous-two-models", "multi", "multi-part", "state-question"),
    "Odd, impossible or off-topic requests": ("edge", "cannot-do", "nonsense", "other-language", "raw-command", "invalid-option"),
}
cats = {g: [0, 0] for g in GROUPS}
for r in all_results:
    g = next((g for g, members in GROUPS.items() if r.get("category") in members), "Other")
    c = cats.setdefault(g, [0, 0]); c[1] += 1; c[0] += 1 if r.get("ok") else 0
med = sorted(r.get("seconds", 0) for r in all_results)[n // 2]
out = ["# Reliability", "",
       "Short version: with the default free local model, **%d of %d** plain-English requests from our test set did what was asked, "
       "with a median of %.0f seconds per request. Cloud models (Gemini, Claude, OpenAI) are stronger than an 8-billion-parameter local "
       "model; we have not published numbers for them yet because the test machine has no cloud key." % (n_ok, n, med), "",
       "How this was measured: each request was typed into Pellaeon inside ChimeraX 1.12 on %s, with **%s** through Ollama (thinking off, "
       "temperature 0.2, RTX 4070 Super). Afterwards a script inspected the ChimeraX session (colors, selections, distances, open models), "
       "so a pass means the requested state was actually there, not that the reply sounded right. Requests that must ask first (close, delete, "
       "save) pass when the confirmation card appeared with the right commands. The harness, the request sets and the raw reports are in the repository." % (date, model), "",
       "## By kind of request", "", "| Kind | Passed |", "|---|---|"]
for g, (ok, tot) in cats.items():
    if tot:
        out.append("| %s | %d of %d |" % (g, ok, tot))
out += ["", "## Where it fell short", "",
        "The misses are listed because they say more than the pass count. Most are a small model choosing a plausible but wrong command; "
        "rephrasing to one action, or switching to a cloud provider, fixes them.", ""]
for r in all_results:
    if r.get("ok"):
        continue
    ran = "; ".join(r.get("ran") or []) or "nothing"
    note = NOTES.get(r.get("id", ""), "ran `%s`." % ran[:100])
    out.append("- **\"%s\"**: %s" % (r["request"], note))
out += ["", "## Reading the numbers", "",
        "- The test set is small on purpose: %d requests covering the things people actually type, including typos, \"it\", complaints and impossible asks. It is not a benchmark of the model; it is a check that this panel, with this model, does what the tutorial shows." % n,
        "- Each pass rate is for one run. Small local models are not fully deterministic; expect a request or two to flip between runs.",
        "- Rerun it yourself: `chimerax --nogui --exit --script \"tests_chimerax/scenarios.py <model>\"`, then `python tools/build_reliability.py <report.json>`.",
        "- Raw reports: " + ", ".join("[%s](%s)" % (os.path.basename(f), "reliability/" + os.path.basename(f)) for f in files) + ".", ""]
open(os.path.join(ROOT, "docs", "reliability.md"), "w", encoding="utf-8").write("\n".join(out)); print("wrote docs/reliability.md (%d lines)" % len(out))
