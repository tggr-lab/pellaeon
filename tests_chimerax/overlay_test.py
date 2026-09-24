"""Color keys and the Pellaeon title go away with the structure they were drawn for.

    chimerax --nogui --exit --script tests_chimerax/overlay_test.py
"""
from chimerax.core.commands import run
from chimerax.pellaeon.bridge import ChimeraXExecutor

ex = ChimeraXExecutor(session)  # noqa: F821
fails = 0


def check(name, ok):
    global fails
    fails += not ok
    print("%s %s" % ("PASS" if ok else "FAIL", name))


def key_shown():
    from chimerax.color_key.model import get_model
    m = get_model(session, create=False)  # noqa: F821
    return m is not None and not m.deleted


def title_shown():
    from chimerax.label.label2d import session_labels
    lm = session_labels(session, create=False)  # noqa: F821
    return lm is not None and lm.named_label("pellaeon_title") is not None


def draw(spec):
    res = ex.run_commands(["color bfactor %s" % spec, "key delete", "key blue:0 white: red:1 pos 0.30,0.075 size 0.40,0.04",
                           '2dlabels create pellaeon_title text "B-factor" xpos 0.30 ypos 0.165 size 20'])
    return all(r["ok"] for r in res)


run(session, "open 1ake; open 4ake", log=False)  # noqa: F821
check("drawn for #1", draw("#1") and key_shown() and title_shown())
run(session, "close #2", log=False)  # noqa: F821
check("closing another structure keeps them", key_shown() and title_shown())
run(session, "close #1", log=False)  # noqa: F821
check("closing their structure removes the key", not key_shown())
check("closing their structure removes the title", not title_shown())

run(session, "open 1ake", log=False)  # noqa: F821
check("drawn again", draw("#1") and key_shown())
ex.run_commands(["close #1"])
check("closed through Pellaeon's own command", not key_shown() and not title_shown())

run(session, "open 1ake", log=False)  # noqa: F821
run(session, "key red:0 blue:1", log=False)  # noqa: F821
run(session, "close #1", log=False)  # noqa: F821
check("a key the user made themselves is left alone", key_shown())
run(session, "key delete", log=False)  # noqa: F821

print("overlay test: %d failures" % fails)
