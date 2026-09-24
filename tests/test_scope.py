"""Doing what was asked and no more: one data source per request, no piles of windows, and
the initiative setting reaching the system prompt."""
from __future__ import annotations

from core import prompt as P
from core.schema import ToolCall


class StubExecutor:
    def __init__(self):
        self.ran = []
        self.last_new_tools = []
        self.identity_calls = []

    def run_commands(self, commands):
        self.ran.extend(commands)
        self.last_new_tools = ["Sequence Viewer" for c in commands if c.startswith("sequence ")]
        return [{"command": c, "ok": True, "info": ["ok"]} for c in commands]

    def get_state(self):
        return {"models": [{"id": "#1", "name": "x"}], "selection": {}}

    def sequence_identity(self, spec="", chain=None):
        self.identity_calls.append((spec, chain))
        return {"pairs": [], "table": ""}


def _agent(executor=None):
    from core.agent import Agent, AgentConfig
    from core.safety import AUTONOMY_AUTO

    class Provider:
        name = "stub"
        supports_vision = False

        def stream(self, system, messages, tools, on_delta=None, cancel=None):
            raise AssertionError("the provider is not used by this test")

    ag = Agent(Provider(), executor or StubExecutor(), config=AgentConfig(autonomy=AUTONOMY_AUTO))
    ag._annotate = lambda *a, **k: {"ok": True, "commands": []}
    ag._fetch_annotation = lambda *a, **k: {"ok": True, "commands": []}
    return ag


def _start(ag, text):
    ag._turn_text = text
    ag._coloring_sources = []
    ag._windows_this_turn = []
    ag._failed_this_turn = set()


def test_clinvar_request_refuses_alphamissense():
    """ministral colored by AlphaMissense for 'look up clinvar varieties' and called it ClinVar."""
    ag = _agent()
    _start(ag, "look up clinvar varieties for this protein")
    _, p = ag._dispatch(ToolCall("t1", "fetch_annotation", {"source": "alphamissense", "protein": "P07550"}))
    assert "ClinVar" in p.get("error", "") and "annotate" in p["error"]
    _, p = ag._dispatch(ToolCall("t2", "annotate", {"model": "#1", "accession": "P07550", "kind": "clinvar"}))
    assert "error" not in p


def test_second_source_on_top_of_the_first_is_refused():
    """'show me the pathogenic mutations': ClinVar first, then AlphaMissense over it."""
    ag = _agent()
    _start(ag, "show me the pathogenic mutations")
    _, p = ag._dispatch(ToolCall("t1", "annotate", {"model": "#1", "accession": "P07550", "kind": "clinvar"}))
    assert "error" not in p
    _, p = ag._dispatch(ToolCall("t2", "fetch_annotation", {"source": "alphamissense", "protein": "P07550"}))
    assert "already used" in p.get("error", "")
    _, p = ag._dispatch(ToolCall("t3", "annotate", {"model": "#1", "accession": "P07550", "kind": "clinvar"}))
    assert "error" not in p, "the same source again is fine (e.g. a retry with labels)"


def test_both_sources_named_are_both_allowed():
    ag = _agent()
    _start(ag, "show the clinvar variants and color by alphamissense")
    _, p1 = ag._dispatch(ToolCall("t1", "annotate", {"model": "#1", "accession": "P07550", "kind": "clinvar"}))
    _, p2 = ag._dispatch(ToolCall("t2", "fetch_annotation", {"source": "alphamissense", "protein": "P07550"}))
    assert "error" not in p1 and "error" not in p2


def test_plain_tool_commands_are_not_guarded():
    """`pellaeon tool fetch ...`: the user picked the tool; there is no request text."""
    ag = _agent()
    _start(ag, "")
    ag._coloring_sources = ["clinvar"]
    _, p = ag._dispatch(ToolCall("t1", "fetch_annotation", {"source": "alphamissense", "protein": "P07550"}))
    assert "error" not in p


def test_identity_request_is_sent_to_the_tool_not_to_viewers():
    ex = StubExecutor()
    ag = _agent(ex)
    _start(ag, "tell me the sequence identity between every pair of these")
    # the viewer command is not run; what was meant, the identity table, is: the tool runs on the models it named
    _, p = ag._dispatch(ToolCall("t1", "run_commands", {"commands": ["sequence align #1/A #2/A"]}))
    assert p.get("tool") == "sequence_identity" and ex.identity_calls == [("#1/A #2/A", None)]
    assert not ex.ran
    ex.identity_calls.clear()
    _, p = ag._dispatch(ToolCall("t1b", "run_commands", {"commands": ["sequence identity #1,#2"]}))
    assert ex.identity_calls == [("#1 #2", None)]
    ex.identity_calls.clear()
    _, p = ag._dispatch(ToolCall("t2", "sequence_identity", {"models": ["#1", "#2"]}))
    assert "error" not in p and ex.identity_calls == [("#1 #2", None)]


def test_window_cap():
    ex = StubExecutor()
    ag = _agent(ex)
    _start(ag, "show me the chains in the sequence viewer")
    for i in range(3):
        _, p = ag._dispatch(ToolCall("t%d" % i, "run_commands", {"commands": ["sequence chain #%d/A" % (i + 1)]}))
        assert "error" not in p
    _, p = ag._dispatch(ToolCall("t9", "run_commands", {"commands": ["sequence chain #4/A"]}))
    assert "already opened 3 tool windows" in p.get("error", "")
    _, p = ag._dispatch(ToolCall("t10", "run_commands", {"commands": ["color #1 red"]}))
    assert "error" not in p, "other commands still run"


def test_initiative_reaches_the_system_prompt():
    minimal = P.build_system_prompt(gotchas="- g")
    more = P.build_system_prompt(gotchas="- g", initiative="initiative")
    assert "do exactly what the user asked, then stop" in minimal
    assert "you may take some initiative" in more
    for sp in (minimal, more):
        assert "ask_user" in sp and "ClinVar" in sp
    classic = P.build_system_prompt(gotchas="- g", edition="chimera")
    assert "do exactly what the user asked" in classic


def test_identity_tools_only_in_chimerax():
    from core.tools import tool_specs
    assert "sequence_identity" in {t.name for t in tool_specs()}
    assert "sequence_identity" not in {t.name for t in tool_specs(edition="chimera")}


class StateExecutor(StubExecutor):
    def get_state(self):
        return {"models": [{"id": "#1", "name": "4xt3"}, {"id": "#2", "name": "AlphaFold P00568"}], "selection": {}}


def test_already_open_structure_is_not_opened_again():
    """'open 4xt3 and color its surface' with 4xt3 loaded: the model opened a copy and colored the wrong one."""
    ex = StateExecutor()
    ag = _agent(ex)
    _start(ag, "color its surface by hydrophobicity")   # the model, not the user, decided to open 4xt3 again
    _, p = ag._dispatch(ToolCall("t1", "run_commands", {"commands": ["open 4xt3", "surface #1", "mlp #1", "open 4xt3"]}))
    assert ex.ran == ["surface #1", "mlp #1"]
    assert "already open as #1" in p.get("note", "")
    _, p = ag._dispatch(ToolCall("t2", "run_commands", {"commands": ["alphafold fetch P00568 pae true"]}))
    assert "alphafold pae #2" in p.get("note", "") and ex.ran == ["surface #1", "mlp #1"]


def test_open_again_when_asked_for_a_copy():
    ex = StateExecutor()
    ag = _agent(ex)
    _start(ag, "open another copy of 4xt3")
    ag._dispatch(ToolCall("t1", "run_commands", {"commands": ["open 4xt3"]}))
    assert ex.ran == ["open 4xt3"]


def test_preset_only_when_a_look_was_asked_for():
    ex = StubExecutor()
    ag = _agent(ex)
    _start(ag, "make the background white for the figure")
    _, p = ag._dispatch(ToolCall("t1", "run_commands", {"commands": ['preset "overall look" "publication 1"']}))
    assert "Do not use a preset" in p.get("error", "") and not ex.ran
    _start(ag, "make it publication ready")
    _, p = ag._dispatch(ToolCall("t2", "run_commands", {"commands": ['preset "overall look" "publication 1"']}))
    assert "error" not in p


class TwoModels(StubExecutor):
    def get_state(self):
        return {"models": [{"id": "#1", "name": "1ake", "type": "AtomicStructure", "chains": [{"id": "A", "range": "1-214"}]},
                           {"id": "#2", "name": "4ake", "type": "AtomicStructure", "chains": [{"id": "A", "range": "1-214"}]}],
                "selection": {}}


def test_broad_command_runs_before_the_narrow_one():
    """'imatinib as spheres and everything else as sticks': the whole-model style came last and undid it."""
    ex = StubExecutor()
    ag = _agent(ex)
    _start(ag, "show imatinib as spheres and everything else as sticks")
    _, p = ag._dispatch(ToolCall("t1", "run_commands", {"commands": ["style ligand sphere", "style #1 stick", "color byhetero"]}))
    assert ex.ran == ["style #1 stick", "style ligand sphere", "color byhetero"]
    assert "before the narrower" in p.get("note", "")


def test_terminus_words_are_refused_with_real_ranges():
    ex = TwoModels()
    ag = _agent(ex)
    _start(ag, "select the first and last residue of chain A")
    _, p = ag._dispatch(ToolCall("t1", "run_commands", {"commands": ["select #1/A:end"]}))
    assert "#1/A 1-214" in p.get("error", "") and not ex.ran


def test_chains_guessed_right_after_opening_are_held_back():
    ex = StubExecutor()
    ag = _agent(ex)
    _start(ag, "open 7kkl and color the three nanobodies gold")
    _, p = ag._dispatch(ToolCall("t1", "run_commands", {"commands": ["open 7kkl", "color /B,C,D gold"]}))
    assert ex.ran == ["open 7kkl"] and "get_state" in p.get("error", "")
    _start(ag, "open 4hhb and color chain A red")
    ag._dispatch(ToolCall("t2", "run_commands", {"commands": ["open 4hhb", "color /A red"]}))
    assert ex.ran[-1] == "color /A red", "the user named the chain"


def test_it_with_two_structures_open_asks_first():
    ex = TwoModels()
    ag = _agent(ex)
    _start(ag, "color it blue")
    ag._no_recent_focus = True
    _, p = ag._dispatch(ToolCall("t1", "run_commands", {"commands": ["color #1 blue"]}))
    assert "ask_user" in p.get("error", "") and not ex.ran
    ag._no_recent_focus = False   # something was opened or named recently: "it" refers to that
    ag._dispatch(ToolCall("t2", "run_commands", {"commands": ["color #1 blue"]}))
    assert ex.ran == ["color #1 blue"]


def test_named_models_are_not_ambiguous():
    ex = TwoModels()
    ag = _agent(ex)
    for text in ["Superimpose model 2 onto model 1, and drag model 3 along with it",
                 "color the second one's chains to match the first one"]:
        _start(ag, text)
        ag._no_recent_focus = True
        _, p = ag._dispatch(ToolCall("t", "run_commands", {"commands": ["color #2 blue"]}))
        assert "error" not in p, text


def test_claimed_save_without_a_save_is_sent_back():
    from test_agent import ScriptedProvider
    from core.agent import Agent, AgentConfig
    from core.safety import AUTONOMY_AUTO
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["hide ions"]})]},
                             "I hid the ions and saved the picture to your desktop.", "Sorry, not saved yet."])
    Agent(prov, StubExecutor(), config=AgentConfig(autonomy=AUTONOMY_AUTO)).run_turn("hid the ions and safe a pic to my desktop")
    assert "NOTHING was saved" in str(prov.requests[-1])


# ---- false positives found in review: each of these must go through untouched

def test_questions_are_never_pushed_into_action():
    from test_agent import ScriptedProvider
    from core.agent import Agent, AgentConfig
    from core.safety import AUTONOMY_AUTO

    class Open(StubExecutor):
        def get_state(self):
            return {"models": [{"id": "#1", "name": "4hhb", "chains": [{"id": "A"}]}], "selection": {}}
    for q in ["what does this structure show?", "is it possible to save as a PyMOL session?", "can I compare two maps in ChimeraX?"]:
        prov = ScriptedProvider(["An answer."])
        Agent(prov, Open(), config=AgentConfig(autonomy=AUTONOMY_AUTO)).run_turn(q)
        assert len(prov.requests) == 1, q


def test_save_path_is_not_a_chain_spec():
    ex = StubExecutor()
    ag = _agent(ex)
    _start(ag, "open 1ubq and save a picture to my desktop")
    ag.config.autonomy = "all"   # the save itself would ask for confirmation; that is not what this tests
    _, p = ag._dispatch(ToolCall("t", "run_commands", {"commands": ["open 1ubq", "save ~/Desktop/ubq.png width 1200"]}))
    assert "error" not in p and len(ex.ran) == 2


def test_camera_moves_are_not_ambiguous_and_focus_is_remembered():
    ex = TwoModels()
    ag = _agent(ex)
    _start(ag, "rotate it 90 degrees")
    ag._no_recent_focus = True
    _, p = ag._dispatch(ToolCall("t", "run_commands", {"commands": ["turn y 90"]}))
    assert "error" not in p
    # a structure opened in the previous request keeps "it" unambiguous
    from core.agent import Agent
    import time as _t
    ag.journal.append({"ts": _t.time(), "origin": "model", "command": "open 4ake", "ok": True})
    ag._prior_texts = ["open 4ake"]
    ag._turn_starts = [_t.time() - 1]
    ag.run_turn.__func__  # noqa: B018  (exists)
    ag._turn_text = "color it blue"
    # recompute the focus flag the way run_turn does
    since = ag._turn_starts[-2:][0]
    opened = any(str(j.get("command", "")).startswith("open") and float(j.get("ts", 0)) >= since for j in ag.journal)
    assert opened


def test_undo_an_alignment_is_not_a_window_request():
    from core.agent import _CLOSE_WIN_RE
    assert not _CLOSE_WIN_RE.search("remove the alignment")
    assert not _CLOSE_WIN_RE.search("hide the sequence of chain B")
    assert _CLOSE_WIN_RE.search("close those sequence windows")
    assert _CLOSE_WIN_RE.search("get rid of the alignment viewer")


def test_ranges_with_end_are_allowed_bare_end_is_not():
    from core.agent import _TERMINUS_RE
    assert not _TERMINUS_RE.search("select #1/A:start-5")
    assert not _TERMINUS_RE.search("select #1/A:70-end")
    assert _TERMINUS_RE.search("select #1/A:end")
    assert _TERMINUS_RE.search("select #1/A:1,end")


def test_identity_plus_viewer_request_may_open_the_viewer():
    ex = StubExecutor()
    ag = _agent(ex)
    _start(ag, "align the sequences in the viewer and show me the identity")
    _, p = ag._dispatch(ToolCall("t", "run_commands", {"commands": ["sequence align #1/A #2/A"]}))
    assert "error" not in p and ex.ran == ["sequence align #1/A #2/A"]


def test_users_own_open_of_an_open_structure_is_honoured():
    ex = StateExecutor()
    ag = _agent(ex)
    _start(ag, "open 4xt3")
    ag._dispatch(ToolCall("t", "run_commands", {"commands": ["open 4xt3"]}))
    assert ex.ran == ["open 4xt3"]


def test_predicted_pathogenic_after_clinvar_is_allowed():
    ag = _agent()
    _start(ag, "show the known variants and tell me which are predicted pathogenic")
    _, p1 = ag._dispatch(ToolCall("t1", "annotate", {"model": "#1", "accession": "P07550", "kind": "clinvar"}))
    _, p2 = ag._dispatch(ToolCall("t2", "fetch_annotation", {"source": "alphamissense", "protein": "P07550"}))
    assert "error" not in p1 and "error" not in p2


def test_denied_save_is_not_a_save_claim():
    from core.agent import _SAVE_CLAIM_RE, _SAVE_DENIED_RE
    t = "Nothing was saved because ChimeraX cannot email files."
    assert _SAVE_CLAIM_RE.search(t) and _SAVE_DENIED_RE.search(t)


def test_help_and_harmless_tools_do_not_count_against_the_window_cap():
    from core.agent import _WINDOW_CMD_RE, _HARMLESS_TOOLS
    assert not _WINDOW_CMD_RE.match("help hbonds") and not _WINDOW_CMD_RE.match("log show")
    assert "Distances" in _HARMLESS_TOOLS


def test_model_may_not_redo_a_tool_drawn_overlay():
    """fetch_annotation had colored ADRB2 and drawn the key; ministral recolored, deleted the key and said it failed."""
    ex = StubExecutor()
    ag = _agent(ex)
    _start(ag, "color ADRB2 by AlphaMissense pathogenicity")
    ag._overlay_done = ""
    _, p = ag._dispatch(ToolCall("t1", "fetch_annotation", {"source": "alphamissense", "protein": "P07550", "model": "#1"}))
    assert "error" not in p and ag._overlay_done == "fetch_annotation" and "Nothing else to run" in p.get("note", "")
    _, p = ag._dispatch(ToolCall("t2", "run_commands", {"commands": ["color byattribute pellaeon_alphamissense_p07550_am_mean #1 palette bluered",
                                                                       "key pellaeon_alphamissense_p07550_am_mean"]}))
    assert "already colored" in p.get("error", "") and not ex.ran
    _, p = ag._dispatch(ToolCall("t3", "run_commands", {"commands": ["view #1"]}))
    assert "error" not in p, "unrelated commands still run"
