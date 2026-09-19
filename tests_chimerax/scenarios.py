"""Scenario harness: runs many plain-English requests through the real agent inside
ChimeraX (no GUI) against a local Ollama model, and checks the outcome mechanically.

    chimerax --nogui --exit --script "tests_chimerax/scenarios.py [model] [think] [scenario_file.json] [filter]"

(ChimeraX passes the quoted string as the script's argv.)

Prints one line per scenario and a summary; writes a JSON report to /tmp/pellaeon_scenarios_<model>.json.
"""
import json
import os
import re
import sys
import time

from chimerax.core.commands import run
from chimerax.atomic import all_atoms, selected_residues
from chimerax.pellaeon.bridge import ChimeraXExecutor, data_path
from chimerax.pellaeon.core.agent import Agent, AgentConfig, Callbacks
from chimerax.pellaeon.core.knowledge import load_json
from chimerax.pellaeon.core.providers.ollama import OllamaProvider

args = [a for a in sys.argv[1:] if not a.endswith("scenarios.py")]
model = args[0] if args else "qwen3:8b"
think = (args[1].lower() in ("1", "true", "think")) if len(args) > 1 else False
scen_file = args[2] if len(args) > 2 else os.path.join(os.path.dirname(__file__), "scenarios.json")
flt = args[3] if len(args) > 3 else ""

with open(scen_file) as f:
    SCENARIOS = json.load(f)
if flt:
    SCENARIOS = [s for s in SCENARIOS if flt in s["id"] or flt in s.get("category", "")]

ex = ChimeraXExecutor(session)  # noqa: F821
ex.ensure_index()
gotchas = open(data_path("gotchas.md")).read()
recipes = load_json(data_path("recipes.json"))
directory = ex.knowledge.command_directory()


def make_agent(record):
    if ":" in model and model.split(":", 1)[0] in ("gemini", "anthropic", "openai", "openrouter", "groq"):
        # a cloud preset with the key stored by the panel: e.g. gemini:gemini-2.5-flash
        from chimerax.pellaeon.core.providers.presets import make_provider, preset_by_id
        from chimerax.pellaeon.core.secrets import SecretStore
        from chimerax.pellaeon.bridge import pellaeon_dir
        pid, mname = model.split(":", 1)
        preset = preset_by_id(pid) or {}
        prov = make_provider(preset.get("provider", pid), mname or preset.get("model", ""), api_key=SecretStore(pellaeon_dir("config")).get(pid),
                             base_url=preset.get("base_url", ""))
    else:
        prov = OllamaProvider(model, options={"think": think})
    cb = Callbacks(on_tool_start=lambda c: record["calls"].append((c.name, dict(c.args))),
                   on_confirm=lambda cmds, reasons: (record["confirms"].append(list(cmds)) or cmds),
                   on_ask_user=lambda q, o: record["asks"].append(q))
    return Agent(prov, ex, config=AgentConfig(), callbacks=cb, directory=directory, gotchas=gotchas, recipes=recipes)


def ran_commands(record):
    out = []
    for name, a in record["calls"]:
        if name == "run_commands":
            raw = a.get("commands") or []
            out.extend(raw if isinstance(raw, list) else [raw])
    return out


def evaluate(s, record, reply):
    ct = s["check_type"]
    ran = ran_commands(record)
    if ct == "no_commands":
        return len(ran) == 0, "ran %d commands" % len(ran)
    if ct == "confirm_requested":
        return bool(record["confirms"]), "confirms=%s" % record["confirms"]
    if ct == "ask_user":
        return bool(record["asks"]), "asks=%s" % record["asks"]
    if ct == "commands_regex":
        ok = any(re.search(s["check"], c, re.I) for c in ran)
        return ok, "ran=%s" % ran
    if ct == "state_expr":
        models = session.models.list()  # noqa: F821
        atoms = all_atoms(session)  # noqa: F821
        try:
            sel_res = sorted(int(r.number) for r in selected_residues(session))  # noqa: F821
        except Exception:
            sel_res = []
        bg = tuple(float(x) for x in session.main_view.background_color[:3])  # noqa: F821
        env = {"session": session, "models": models, "atoms": atoms, "ran": ran, "sel_res": sel_res, "bg": bg,  # noqa: F821
               "re": re, "reply": reply, "record": record}
        try:
            g = dict(env); g["__builtins__"] = __builtins__
            return bool(eval(s["check"], g)), "ran=%s" % ran
        except Exception as e:
            return False, "check error: %s; ran=%s" % (e, ran)
    return False, "unknown check"


PACE = float(os.environ.get("PELLAEON_PACE", "0") or 0)    # seconds between scenarios (free cloud tiers are per-minute limited)
report = {"model": model, "think": think, "results": []}
passed = 0
t_all = time.time()
for s in SCENARIOS:
    if PACE:
        time.sleep(PACE)
    run(session, "close", log=False)
    run(session, "set bgColor black", log=False)
    for cmd in s.get("setup_commands", []) or []:
        try:
            run(session, cmd, log=False)
        except Exception as e:
            print("SETUP FAIL %s: %s (%s)" % (s["id"], cmd, e))
    record = {"calls": [], "confirms": [], "asks": []}
    agent = make_agent(record)
    t0 = time.time()
    reply = ""
    err = None
    try:
        res = agent.run_turn(s["request"])
        reply = res.reply
        err = res.error
        for fu in s.get("followups", []) or []:
            res = agent.run_turn(fu)
            reply = res.reply
            err = err or res.error
    except Exception as e:  # noqa: BLE001
        err = str(e)
    dt = time.time() - t0
    ok, detail = evaluate(s, record, reply)
    passed += ok
    line = "%s %-28s %5.1fs  %s" % ("PASS" if ok else "FAIL", s["id"], dt, "" if ok else ("| " + detail[:300] + " | reply: " + (reply or "")[:160].replace("\n", " ")))
    print(line)
    report["results"].append({"id": s["id"], "category": s.get("category"), "request": s["request"], "ok": ok, "seconds": round(dt, 1),
                              "ran": ran_commands(record), "calls": [c[0] for c in record["calls"]], "confirms": record["confirms"],
                              "asks": record["asks"], "reply": reply, "error": err, "detail": detail,
                              "expected": s.get("expected"), "check_type": s["check_type"], "check": s["check"]})
print("SUMMARY %s think=%s: %d/%d passed in %.0fs" % (model, think, passed, len(SCENARIOS), time.time() - t_all))
out = "/tmp/pellaeon_scenarios_%s%s.json" % (model.replace(":", "_").replace("/", "_"), "_think" if think else "")
with open(out, "w") as f:
    json.dump(report, f, indent=1)
print("report:", out)
