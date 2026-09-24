"""Guards and batch corrections added after the first blind-set reading (2026-09-23): each one is a
failure class seen in the transcripts, tested here on made-up requests."""
import json

from core.agent import Agent, AgentConfig, Callbacks, question_only
from core.schema import Message, TextPart, ToolCall, Usage

from test_agent import FakeExecutor, ScriptedProvider


def agent_for(text, ex=None, **cfg):
    ex = ex or FakeExecutor()
    ag = Agent(ScriptedProvider([]), ex, config=AgentConfig(nudge_on_no_action=False, **cfg))
    ag._turn_text = text
    ag._question_only = question_only(text)
    from core.agent import _DONT_RUN_RE
    ag._dont_run = bool(_DONT_RUN_RE.search(text))
    ag._failed_opens = set()
    return ag, ex


# ---------------------------------------------------------------- question-only requests
def test_question_classifier():
    assert question_only("which chains bind the cofactor?")
    assert question_only("is this prediction reliable? how confident is it")
    assert question_only("how many waters are there")
    assert not question_only("how do I make the surface transparent?")      # a request to do it
    assert not question_only("can you make it blue?")
    assert not question_only("is there a cartoon preset? apply it")
    assert not question_only("what is the closest contact, and can you show me where it is?")
    assert not question_only("color the helices red")


def test_a_question_does_not_restyle_but_may_read_and_measure():
    ag, ex = agent_for("which residues line the pocket?")
    out = ag.execute_commands(["color #1:30-40 red"])
    assert out.get("question") and ex.ran == []
    out = ag.execute_commands(["info residues #1:30-40", "measure sasa #1", "distance #1/A:10@CA #1/A:20@CA"])
    assert out["ok"] and ex.ran[:2] == ["info residues #1:30-40", "measure sasa #1"]


def test_dont_run_means_nothing_runs():
    ag, ex = agent_for("what command would hide the waters? just tell me the command, do not run it")
    out = ag.execute_commands(["hide solvent"])
    assert out.get("dont_run") and ex.ran == []


# ---------------------------------------------------------------- windows, substitutions, recipes
def test_unasked_windows_are_refused_asked_ones_run():
    ag, ex = agent_for("make a morph between the two and play it")
    assert ag.execute_commands(["coordset slider #3"]).get("window") and ex.ran == []
    ag, ex = agent_for("open a sequence viewer for chain B")
    assert ag.execute_commands(["sequence chain #1/B"])["ok"] and ex.ran == ["sequence chain #1/B"]


def test_no_other_entry_after_the_named_one_failed():
    ex = FakeExecutor(fail=["open 8xyz"])
    ag, ex = agent_for("open 8xyz", ex)
    ex.run_commands = (lambda orig: (lambda cmds: [dict(r, error="Fetching 8xyz: HTTP Error 404: Not Found") if not r["ok"] else r
                                                    for r in orig(cmds)]))(ex.run_commands)
    assert not ag.execute_commands(["open 8xyz"])["ok"]
    out = ag.execute_commands(["open 1abc"])
    assert out.get("substitute") and "8xyz" in out["error"] and ex.ran == ["open 8xyz"]


class _Knowledge:
    recipe_library = [
        {"name": "mirror", "dir": "mirror_z", "what": "Negates z coordinates, inverting handedness.",
         "requests": ["mirror my model"], "only_if": "mirror|handed"},
        {"name": "convex hull", "dir": "convexhull", "what": "Draws the convex hull of atoms as a surface.",
         "requests": ["draw a convex hull around the atoms"]},
    ]


def test_a_community_recipe_must_fit_the_request():
    url = "open https://raw.githubusercontent.com/RBVI/chimerax-recipes/master/%s/x.py"
    ex = FakeExecutor()
    ex.knowledge = _Knowledge()
    ag, ex = agent_for("turn it over", ex)
    assert ag.execute_commands([url % "mirror_z"]).get("recipe")            # only_if not met
    ag, ex = agent_for("write a gromacs topology for this", ex)
    assert ag.execute_commands([url % "convexhull"]).get("recipe")          # no shared word
    ag, ex = agent_for("draw a convex hull around chain A", ex)
    out = ag.execute_commands([url % "convexhull"])
    assert not out.get("recipe") and out.get("skipped")     # passes the guard; the safety gate then asks first


# ---------------------------------------------------------------- batch corrections
def test_two_selects_in_one_batch_are_a_union():
    ag, ex = agent_for("select the interface residues on both sides")
    ag.execute_commands(["select #1/A & (#1/B :<4)", "select #1/B & (#1/A :<4)", "show sel atoms"])
    assert ex.ran[:2] == ["select #1/A & (#1/B :<4)", "select add #1/B & (#1/A :<4)"]
    ag, ex = agent_for("color 10 red then 20 blue")
    ag.execute_commands(["select #1:10", "color sel red", "select #1:20", "color sel blue"])
    assert ex.ran == ["select #1:10", "color sel red", "select #1:20", "color sel blue"]   # sel used between


def test_key_for_an_attribute_coloring_comes_from_the_coloring():
    ag, ex = agent_for("put a color scale on it")
    ag.journal.append({"ts": 0, "origin": "model", "command": "color bfactor #1 palette bluered", "ok": True})
    out = ag.execute_commands(["key true"])
    assert ex.ran[-1] == "color bfactor #1 palette bluered key true" and "key_from_coloring" in out["fixups"]
    ag, ex = agent_for("put a legend on it")
    ag.execute_commands(["key red:low blue:high"])
    assert ex.ran[-1] == "key red:low blue:high"                          # a real key is left alone


def test_style_of_hidden_atoms_is_shown_when_the_user_asked_to_see_them():
    ex = FakeExecutor()
    ex.spec_atoms = lambda text: {"used": text.split()[0], "atoms": 20, "shown": 0, "residues": 3}
    ag, ex = agent_for("display those as sticks too", ex)
    out = ag.execute_commands(["style sel stick"])
    assert ex.ran == ["show sel atoms", "style sel stick"] and "show_before_style" in out["fixups"]
    ag, ex = agent_for("make them sticks", ex)                                # no 'show': left alone
    ag.execute_commands(["style sel stick"])
    assert ex.ran[-1:] == ["style sel stick"] and "show sel atoms" not in ex.ran[2:]


def test_a_ligand_with_a_metal_is_shown_whole():
    ex = FakeExecutor()
    ex.state = {"models": [{"id": "#1", "name": "x", "hets": {"ligand": ["HEM", "PO4"], "ions": ["HEM"]}}], "selection": {}}
    ag, ex = agent_for("let me see the bound cofactor", ex)
    ag.execute_commands(["show ligand atoms"])
    assert ex.ran == ["show ligand atoms", "show :HEM atoms"]


def test_guards_off_leaves_the_batch_alone():
    ag, ex = agent_for("which residues are exposed?", guards=False)
    ag.execute_commands(["select #1 & bfactor < 50", "color #1 red"])
    assert ex.ran == ["select #1 & bfactor < 50", "color #1 red"]


# ---------------------------------------------------------------- turn level
def test_commands_only_fallback_does_not_replace_a_refusal():
    prov = ScriptedProvider(["ChimeraX cannot undo actions from a previous session; reopen a saved session instead."] * 3
                            + ["undo"])
    ex = FakeExecutor()
    ag = Agent(prov, ex)
    res = ag.run_turn("undo the changes from last week")
    assert ex.ran == [] and "cannot" in res.reply


def test_commands_only_fallback_still_rescues_an_announced_action():
    prov = ScriptedProvider(["I'll color it red now.", "I'll color it red now.", "color #1 red"])
    ex = FakeExecutor()
    ag = Agent(prov, ex)
    ag.run_turn("color it red")
    assert ex.ran == ["color #1 red"]


def test_a_clarifying_question_in_text_after_the_ambiguity_guard_is_asked():
    asked = []
    ex = FakeExecutor()
    ex.state = {"models": [{"id": "#1", "name": "1abc", "type": "AtomicStructure", "chains": [{"id": "A"}]},
                           {"id": "#2", "name": "2xyz", "type": "AtomicStructure", "chains": [{"id": "A"}]}], "selection": {}}
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["color #1 blue"]})]},
                             "Which structure should be blue: #1, #2, or both?"])
    ag = Agent(prov, ex, callbacks=Callbacks(on_ask_user=lambda q, o: asked.append(q)))
    res = ag.run_turn("make it blue")
    assert res.asked_user and asked and ex.ran == []


def test_a_typed_sequence_is_located_for_the_model():
    ex = FakeExecutor()
    ex.find_sequence = lambda seq: ["#1/A:18-25"] if seq == "LEVEPSDT" else []
    prov = ScriptedProvider(["ok"])
    ag = Agent(prov, ex, config=AgentConfig(nudge_on_no_action=False))
    ag.run_turn("select LEVEPSDT please")
    assert "The sequence LEVEPSDT is residues #1/A:18-25" in ag.conversation[0].meta["context"]
