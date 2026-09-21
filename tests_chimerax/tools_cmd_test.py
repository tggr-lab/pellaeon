"""In-ChimeraX check of the `pellaeon tool ...` commands (no model involved):
    chimerax --nogui --exit --script tests_chimerax/tools_cmd_test.py
Prints PASS/FAIL per command."""
from chimerax.core.commands import run
def t(c):
    try:
        run(session, c); print("PASS", c)
    except Exception as e:
        print("FAIL", c, "->", str(e)[:300])
t("pellaeon tool list")
t("open 1ake"); t("open 4ake")
t("pellaeon tool contacts #1 #2 chain A")
t("pellaeon tool contacts #1 #2 chain A cutoff 3.5")
t("pellaeon tool compare #1 #2 chain A")
t("pellaeon tool explain #1/A:87")
t("label #1/A:1-40"); t("pellaeon tool tidy")
t("close #2")
t("pellaeon tool annotate #1 P69441 variant")
t("usage pellaeon tool contacts")
t("usage pellaeon")
