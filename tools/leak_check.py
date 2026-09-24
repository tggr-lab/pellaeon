"""Fail when a test request has leaked into the model's always-on or retrieved knowledge.

    python tools/leak_check.py

Exact (case-insensitive) matches between any scenario request and a `requests` entry of a workflow
in src/data/tutorials.json, or a line of src/data/gotchas.md / recipes.json, are leaks: the how-to
library is retrieved by the user's own words, so the test would look up its own answer.
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    requests = {}
    for f in glob.glob(os.path.join(ROOT, "tests_chimerax", "scenarios*.json")) + glob.glob(
            os.path.join(os.path.dirname(ROOT), "blind_sets", "*.json")):
        if f.endswith("_ref.json"):
            continue
        try:
            data = json.load(open(f))
        except Exception:  # noqa: BLE001
            continue
        for s in data:
            for t in [s.get("request", "")] + list(s.get("followups") or []):
                requests.setdefault(t.strip().lower(), []).append(os.path.basename(f) + ":" + s.get("id", "?"))
    leaks = []
    tut = json.load(open(os.path.join(ROOT, "src", "data", "tutorials.json")))
    for w in tut.get("workflows", []):
        for r in w.get("requests", []):
            if r.strip().lower() in requests:
                leaks.append(("tutorials.json workflow %r" % w.get("name"), r, requests[r.strip().lower()]))
    try:
        rec = json.load(open(os.path.join(ROOT, "src", "data", "recipes.json")))
        for r in rec:
            q = str(r.get("request", "")).strip().lower()
            if q in requests:
                leaks.append(("recipes.json", r.get("request"), requests[q]))
    except Exception:  # noqa: BLE001
        pass
    for line in open(os.path.join(ROOT, "src", "data", "gotchas.md")):
        for q, ids in requests.items():
            # a three-word phrase ("show the ligand") is a rule any user needs, not a leaked test
            if len(q.split()) > 3 and q in line.lower():
                leaks.append(("gotchas.md", q, ids))
    for where, text, ids in leaks:
        print("LEAK %s <- %r (%s)" % (where, text, ", ".join(ids)))
    print("%d leaks" % len(leaks))
    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
