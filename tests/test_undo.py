"""Request-level undo: the checkpoint bookkeeping in analysis.py (plain Python, no ChimeraX needed --
every branch exercised here returns before touching a real `run`/`save`/`open`/`close` command), and the
undo_last_request tool's dispatch, confirmation and nudge at the agent level (FakeExecutor)."""
import json

import analysis
from core.agent import Agent, AgentConfig, Callbacks, _UNDO_RE, parse_tool_line
from core.safety import AUTONOMY_ALL
from test_agent import FakeExecutor, ScriptedProvider


class FakeModel:
    def __init__(self, id_string):
        self.id = (int(id_string.split(".")[0]),)
        self.id_string = id_string


class FakeModels:
    def __init__(self, models):
        self._models = list(models)

    def list(self):
        return self._models


class FakeSession:
    def __init__(self, model_ids=()):
        self.models = FakeModels(FakeModel(i) for i in model_ids)


def _cp(**kw):
    base = {"request": "", "ts": 0.0, "models_before": [], "cxs_path": None, "capped": False, "save_error": "",
            "new_model_ids": [], "only_opens": True, "any_activity": False}
    base.update(kw)
    return base


# ---------------------------------------------------------------- analysis.py: checkpoint bookkeeping (no ChimeraX)

def test_pending_undo_with_fewer_than_two_checkpoints_says_nothing_to_undo():
    s = FakeSession()
    assert analysis.pending_undo(s) == {"error": "There is no earlier request to undo yet."}
    analysis._checkpoints(s).append(_cp(request="open 1abc"))
    # only the checkpoint for THIS (the only) request exists: nothing earlier than it
    assert analysis.pending_undo(s) == {"error": "There is no earlier request to undo yet."}


def test_note_checkpoint_activity_open_only_keeps_the_fast_path():
    s = FakeSession()
    cps = analysis._checkpoints(s)
    cps.append(_cp(request="open the af model of adrb2"))
    analysis.note_checkpoint_activity(s, ["open alphafold:P07550"],
                                      [{"command": "open alphafold:P07550", "ok": True, "new_models": ["#1 P07550"]}])
    assert cps[-1]["only_opens"] is True and cps[-1]["new_model_ids"] == ["1"]
    cps.append(_cp(request="undo the last request"))   # the checkpoint recorded for the undo turn itself
    assert analysis.pending_undo(s) == {"request": "open the af model of adrb2", "kind": "models"}


def test_note_checkpoint_activity_any_other_command_forces_full_restore():
    s = FakeSession()
    cps = analysis._checkpoints(s)
    cps.append(_cp(request="open it and color it red", cxs_path="/tmp/does-not-matter.cxs"))
    analysis.note_checkpoint_activity(s, ["open 1abc", "color #1 red"],
                                      [{"command": "open 1abc", "ok": True, "new_models": ["#1 1abc"]},
                                       {"command": "color #1 red", "ok": True}])
    assert cps[-1]["only_opens"] is False and cps[-1]["new_model_ids"] == ["1"]
    cps.append(_cp(request="undo the last request"))
    assert analysis.pending_undo(s) == {"request": "open it and color it red", "kind": "session"}


def test_note_checkpoint_activity_ignores_failed_commands():
    s = FakeSession()
    cps = analysis._checkpoints(s)
    cps.append(_cp(request="open bogus"))
    analysis.note_checkpoint_activity(s, ["open zzzz"], [{"command": "open zzzz", "ok": False, "error": "not found"}])
    assert cps[-1]["new_model_ids"] == [] and cps[-1]["only_opens"] is True and cps[-1]["any_activity"] is False


def test_mark_checkpoint_dirty_defeats_the_fast_path():
    s = FakeSession()
    cps = analysis._checkpoints(s)
    cps.append(_cp(request="open it then run some python", new_model_ids=["1"], cxs_path="/tmp/x.cxs"))
    analysis.mark_checkpoint_dirty(s)   # e.g. after run_python, which can do anything
    assert cps[-1]["only_opens"] is False
    cps.append(_cp(request="undo the last request"))
    assert analysis.pending_undo(s) == {"request": "open it then run some python", "kind": "session"}


def test_pending_undo_reports_none_when_there_is_no_snapshot_and_more_than_opens_happened():
    s = FakeSession()
    cps = analysis._checkpoints(s)
    cps.append(_cp(request="color it red", only_opens=False, cxs_path=None))
    cps.append(_cp(request="undo the last request"))
    assert analysis.pending_undo(s) == {"request": "color it red", "kind": "none"}


def test_pending_undo_capped_with_no_new_models_reports_none():
    s = FakeSession()
    cps = analysis._checkpoints(s)
    cps.append(_cp(request="color the whole assembly", only_opens=False, capped=True, new_model_ids=[]))
    cps.append(_cp(request="undo the last request"))
    assert analysis.pending_undo(s) == {"request": "color the whole assembly", "kind": "none"}


def test_undo_last_request_with_no_checkpoint_is_a_clean_error():
    s = FakeSession()
    assert analysis.undo_last_request(s) == {"error": "There is no earlier request to undo yet."}


def test_undo_last_request_fast_path_when_the_opened_models_are_already_closed():
    s = FakeSession(model_ids=[])   # nothing open now: the request's models were already closed by hand
    cps = analysis._checkpoints(s)
    cps.append(_cp(request="open 1abc", new_model_ids=["1"], only_opens=True))
    cps.append(_cp(request="undo the last request"))
    out = analysis.undo_last_request(s)
    assert out == {"error": "The model(s) that request opened are already closed; nothing to undo.",
                   "request": "open 1abc"}
    assert len(cps) == 1   # the trivial checkpoint for the undo turn itself was dropped


def test_undo_last_request_capped_and_nothing_openable_is_a_clean_error():
    s = FakeSession()
    cps = analysis._checkpoints(s)
    cps.append(_cp(request="recolor the whole assembly", only_opens=False, capped=True, new_model_ids=[]))
    cps.append(_cp(request="undo the last request"))
    out = analysis.undo_last_request(s)
    assert "too large to snapshot" in out["error"] and out["request"] == "recolor the whole assembly"


def test_undo_last_request_missing_session_file_is_a_clean_error():
    s = FakeSession()
    cps = analysis._checkpoints(s)
    cps.append(_cp(request="color it red", only_opens=False, cxs_path="/no/such/file.cxs"))
    cps.append(_cp(request="undo the last request"))
    out = analysis.undo_last_request(s)
    assert "missing" in out["error"] and out["request"] == "color it red"


def test_checkpoint_request_records_open_models_and_prunes_to_three(tmp_path):
    s = FakeSession(model_ids=["1"])
    cache_dir = str(tmp_path)
    kept_file = tmp_path / "keep.cxs"
    kept_file.write_text("session")
    cps = analysis._checkpoints(s)
    cps.append(_cp(request="r1", cxs_path=str(kept_file)))
    for text in ("r2", "r3", "r4"):
        analysis.checkpoint_request(s, text, cache_dir)   # no ChimeraX here: the save fails and is recorded, not raised
    assert [c["request"] for c in cps] == ["r2", "r3", "r4"]
    assert not kept_file.exists()   # the oldest checkpoint's file is removed once past MAX_CHECKPOINTS
    assert cps[-1]["models_before"] == ["1"]
    assert cps[-1]["cxs_path"] is None and cps[-1]["save_error"]   # save failed cleanly (no ChimeraX here)


# ---------------------------------------------------------------- agent dispatch (FakeExecutor)

def test_undo_regex_leaves_a_bare_undo_alone():
    """'undo that' after a single color change is still plain ChimeraX `undo`, which handles it (and an
    existing scenario test expects exactly that); only a clear whole-request phrasing routes to the tool."""
    assert not _UNDO_RE.search("undo that")
    assert not _UNDO_RE.search("undo it")
    assert not _UNDO_RE.search("revert it")
    assert _UNDO_RE.search("undo the last request")
    assert _UNDO_RE.search("please undo the whole request")
    assert _UNDO_RE.search("go back to how it was before")
    assert _UNDO_RE.search("that whole thing was wrong, put it back")


def test_parse_tool_line_reads_undo_with_no_arguments():
    assert parse_tool_line("undo_last_request") == ("undo_last_request", {})


def test_undo_dispatch_asks_for_confirmation_naming_the_request():
    ex = FakeExecutor()
    ex.pending_undo = lambda: {"request": "color it red", "kind": "session"}
    ex.undo_last_request = lambda: {"restored": "session", "request": "color it red",
                                    "models_before": ["1"], "models_after": ["1"],
                                    "summary": "Undid the last request (\"color it red\")."}
    asked = {}

    def confirm(commands, reasons, python=False):
        asked["commands"] = list(commands)
        asked["reasons"] = list(reasons)
        return commands
    prov = ScriptedProvider([{"calls": [("undo_last_request", {})]}, "Undone."])
    agent = Agent(prov, ex, callbacks=Callbacks(on_confirm=confirm))
    agent.run_turn("undo the last request")
    assert asked["commands"] == ["undo_last_request"]
    assert "color it red" in asked["reasons"][0]
    tr = agent.conversation[2].tool_results_list()[0]
    assert not tr.is_error
    assert json.loads(tr.content)["restored"] == "session"


def test_undo_dispatch_declined_is_not_an_error_and_does_not_restore():
    ex = FakeExecutor()
    ex.pending_undo = lambda: {"request": "open 1abc", "kind": "models"}
    calls = []
    ex.undo_last_request = lambda: calls.append(1) or {"restored": "models"}
    prov = ScriptedProvider([{"calls": [("undo_last_request", {})]}, "Left it as it was."])
    agent = Agent(prov, ex, callbacks=Callbacks(on_confirm=lambda c, r, p=False: None))
    agent.run_turn("undo the last request")
    assert calls == []
    tr = agent.conversation[2].tool_results_list()[0]
    assert not tr.is_error
    assert json.loads(tr.content).get("skipped") is True


def test_undo_dispatch_skips_confirmation_when_autonomy_is_all():
    ex = FakeExecutor()
    ex.pending_undo = lambda: {"request": "open 1abc", "kind": "models"}
    ex.undo_last_request = lambda: {"restored": "models", "request": "open 1abc",
                                    "models_before": ["1"], "models_after": []}

    def confirm(*a, **k):
        raise AssertionError("must not ask when autonomy is 'all'")
    prov = ScriptedProvider([{"calls": [("undo_last_request", {})]}, "Undone."])
    agent = Agent(prov, ex, config=AgentConfig(autonomy=AUTONOMY_ALL), callbacks=Callbacks(on_confirm=confirm))
    res = agent.run_turn("undo the last request")
    tr = agent.conversation[2].tool_results_list()[0]
    assert not tr.is_error and "Undone" in res.reply


def test_undo_dispatch_with_nothing_to_undo_never_asks_to_confirm():
    ex = FakeExecutor()
    ex.pending_undo = lambda: {"error": "There is no earlier request to undo yet."}

    def confirm(*a, **k):
        raise AssertionError("must not ask when there is nothing to undo")
    ex.undo_last_request = lambda: (_ for _ in ()).throw(AssertionError("must not be called"))
    prov = ScriptedProvider([{"calls": [("undo_last_request", {})]}, "There is nothing to undo yet."])
    agent = Agent(prov, ex, callbacks=Callbacks(on_confirm=confirm))
    agent.run_turn("undo the last request")
    tr = agent.conversation[2].tool_results_list()[0]
    assert tr.is_error and "no earlier request" in tr.content


def test_undo_dispatch_unavailable_in_an_edition_without_it():
    ex = FakeExecutor()   # no pending_undo / undo_last_request: the classic edition's executor
    prov = ScriptedProvider([{"calls": [("undo_last_request", {})]}, "Undo isn't available here."])
    agent = Agent(prov, ex, config=AgentConfig(edition="chimera"))
    agent.run_turn("undo the last request")
    tr = agent.conversation[2].tool_results_list()[0]
    assert tr.is_error and "not available in this edition" in tr.content


def test_checkpoint_request_is_called_once_at_the_start_of_every_turn():
    seen = []
    ex = FakeExecutor()
    ex.checkpoint_request = lambda text: seen.append(text)
    agent = Agent(ScriptedProvider(["ok"]), ex, config=AgentConfig(nudge_on_no_action=False))
    agent.run_turn("color it red")
    assert seen == ["color it red"]


def test_checkpoint_request_is_not_called_in_the_classic_edition():
    seen = []
    ex = FakeExecutor()
    ex.checkpoint_request = lambda text: seen.append(text)
    agent = Agent(ScriptedProvider(["ok"]), ex, config=AgentConfig(edition="chimera", nudge_on_no_action=False))
    agent.run_turn("color it red")
    assert seen == []


def test_checkpoint_bookkeeping_failure_never_breaks_the_turn():
    ex = FakeExecutor()
    ex.checkpoint_request = lambda text: (_ for _ in ()).throw(RuntimeError("disk full"))
    agent = Agent(ScriptedProvider(["all good"]), ex, config=AgentConfig(nudge_on_no_action=False))
    res = agent.run_turn("hello")
    assert res.reply == "all good" and res.error is None
