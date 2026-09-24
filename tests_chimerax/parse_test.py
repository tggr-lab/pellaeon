"""Commands are parsed by ChimeraX before they run: a malformed one is refused with the parser's
error and no side effects.    chimerax --nogui --exit --script tests_chimerax/parse_test.py"""
from chimerax.core.commands import run
from chimerax.atomic import all_atoms
from chimerax.pellaeon.bridge import ChimeraXExecutor, parse_error

ex = ChimeraXExecutor(session)  # noqa: F821
fails = 0


def check(name, ok):
    global fails
    fails += not ok
    print("%s %s" % ("PASS" if ok else "FAIL", name))


run(session, "open 1ubq", log=False)  # noqa: F821
check("good command parses", parse_error(session, "color #1 red") == "")  # noqa: F821
check("two commands with ; parse", parse_error(session, "color #1 red; show #1 atoms") == "")  # noqa: F821
check("wrong keyword order is caught", "keyword" in parse_error(session, "select :10 add").lower())  # noqa: F821
check("unknown command is caught", "Unknown command" in parse_error(session, "colr red"))  # noqa: F821
check("bad color is caught", "Expected a color" in parse_error(session, "color #1 rde"))  # noqa: F821
res = ex.run_commands(["color #1 white", "cartoon style #1 helix tube", "color #1 red"])
check("batch stops at the malformed command", [r["ok"] for r in res] == [True, False] and res[1].get("not_run"))
check("parser error carries the usage hint text", "keyword" in res[1]["error"].lower())
check("nothing after it ran", all(tuple(a.color[:3]) == (255, 255, 255) for a in all_atoms(session)))  # noqa: F821
print("parse test: %d failures" % fails)
