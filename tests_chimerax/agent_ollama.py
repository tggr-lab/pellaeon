"""Headless end-to-end turn against a local Ollama model (inside ChimeraX):

    chimerax --nogui --exit --script tests_chimerax/agent_ollama.py [model] ["request"]
"""
import json
import sys
import time

from chimerax.pellaeon.bridge import ChimeraXExecutor, data_path
from chimerax.pellaeon.core.agent import Agent, AgentConfig, Callbacks
from chimerax.pellaeon.core.knowledge import load_json
from chimerax.pellaeon.core.providers.ollama import OllamaProvider

args = [a for a in sys.argv[1:] if not a.endswith(".py")]
model = args[0] if args else "qwen3:8b"
request = args[1] if len(args) > 1 else "open 4hhb, color it by chain and show the ligand as spheres"

ex = ChimeraXExecutor(session)  # noqa: F821
ex.ensure_index()
with open(data_path("gotchas.md")) as f:
    gotchas = f.read()
recipes = load_json(data_path("recipes.json"))
prov = OllamaProvider(model, options={"think": False})


def on_tool(call):
    print("  -> tool %s %s" % (call.name, json.dumps(call.args)[:160]))


def on_result(call, result, payload):
    print("  <- %s %s" % ("ERROR" if result.is_error else "ok", result.content[:200].replace("\n", " ")))


agent = Agent(prov, ex, config=AgentConfig(), callbacks=Callbacks(on_tool_start=on_tool, on_tool_result=on_result,
              on_confirm=lambda c, r: c),
              directory=ex.knowledge.command_directory(), gotchas=gotchas, recipes=recipes)
print("system prompt: %d chars" % len(agent.system_prompt))
t0 = time.time()
res = agent.run_turn(request)
print("REPLY (%.1fs): %s" % (time.time() - t0, res.reply))
print("usage:", res.usage, "error:", res.error)
print("state:", json.dumps(ex.get_state())[:400])
