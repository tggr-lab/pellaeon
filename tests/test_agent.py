import json

from core.agent import Agent, AgentConfig, Callbacks, MAX_CONSECUTIVE_ERRORS
from core.safety import AUTONOMY_AUTO, AUTONOMY_ASK
from core.schema import Message, TextPart, ToolCall, Usage


class FakeExecutor:
    def __init__(self, fail=()):
        self.ran = []
        self.fail = set(fail)
        self.state = {"models": [], "selection": {}}

    def run_commands(self, commands):
        out = []
        for c in commands:
            self.ran.append(c)
            if c in self.fail:
                out.append({"command": c, "ok": False, "error": "Unknown command: %s" % c.split()[0]})
                break
            out.append({"command": c, "ok": True, "info": ["ok"]})
            if c.startswith("open"):
                self.state["models"].append({"id": "#1", "name": c.split()[-1]})
        return out

    def get_state(self):
        return self.state

    def command_usage(self, name):
        return "Usage: %s spec" % name

    def search_docs(self, query, k=5):
        return [{"title": "color", "section": "usage", "text": "Usage: color spec color-spec"}]

    def resolve_protein(self, query, organism="human"):
        return {"accession": "P55085", "gene": "F2RL1", "open_command": "open alphafold:P55085"}

    def protein_features(self, accession, kinds=None):
        return {"features": []}

    def run_python(self, code):
        return {"ok": True, "stdout": "42"}

    def look_at_view(self):
        return {"text": "shot", "png_b64": "AAAA"}


class ScriptedProvider:
    """Returns pre-scripted assistant messages in order."""
    name = "fake"
    supports_vision = False

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def stream(self, system, messages, tools, on_delta=None, cancel=None):
        self.requests.append((system, list(messages), list(tools)))
        if not self.script:
            return Message.assistant("done"), Usage(1, 1)
        item = self.script.pop(0)
        if isinstance(item, str):
            if on_delta:
                on_delta(item)
            return Message.assistant(item), Usage(1, 1)
        return Message("assistant", [TextPart(item.get("text", "")),
                                     *[ToolCall("id%d" % i, n, a) for i, (n, a) in enumerate(item["calls"])]]), Usage(1, 1)


def test_happy_path_open_and_color():
    prov = ScriptedProvider([
        {"text": "Looking it up.", "calls": [("resolve_protein", {"query": "f2rl1"})]},
        {"calls": [("run_commands", {"commands": ["open alphafold:P55085", "color #1 white"]})]},
        "Opened F2RL1 and colored it white.",
    ])
    ex = FakeExecutor()
    seen = {"tools": [], "deltas": []}
    cb = Callbacks(on_text_delta=seen["deltas"].append, on_tool_start=lambda c: seen["tools"].append(c.name))
    agent = Agent(prov, ex, callbacks=cb, config=AgentConfig(autonomy=AUTONOMY_AUTO))
    res = agent.run_turn("open the af model for f2rl1 and color it white")
    assert res.reply == "Opened F2RL1 and colored it white."
    assert res.error is None
    assert ex.ran == ["open alphafold:P55085", "color #1 white"]
    assert seen["tools"] == ["resolve_protein", "run_commands"]
    roles = [m.role for m in agent.conversation]
    assert roles == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    # the user message carries a context block with state and docs
    assert "<chimerax_state>" in agent.conversation[0].meta["context"]
    assert "<docs>" in agent.conversation[0].meta["context"]
    # tool result for run_commands is JSON the model can read
    tr = agent.conversation[4].tool_results_list()[0]
    assert json.loads(tr.content)["ok"] is True


def test_confirmation_flow_for_close():
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["close #1", "open 1abc"]})]}, "ok"])
    ex = FakeExecutor()
    asked = {}

    def confirm(commands, reasons):
        asked["commands"] = commands
        asked["reasons"] = reasons
        return ["open 1abc"]  # user removed the close

    agent = Agent(prov, ex, callbacks=Callbacks(on_confirm=confirm))
    agent.run_turn("close it and open 1abc")
    assert asked["commands"] == ["close #1", "open 1abc"]
    assert asked["reasons"][0] and asked["reasons"][1] == ""
    assert ex.ran == ["open 1abc"]


def test_user_declines():
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["close"]})]}, "Understood, I did not close anything."])
    ex = FakeExecutor()
    agent = Agent(prov, ex, callbacks=Callbacks(on_confirm=lambda c, r: None))
    res = agent.run_turn("close everything")
    assert ex.ran == []
    tr = agent.conversation[2].tool_results_list()[0]
    assert not tr.is_error and "chose not" in tr.content
    assert "did not close" in res.reply


def test_ask_mode_confirms_everything():
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["color #1 red"]})]}, "ok"])
    calls = []
    agent = Agent(prov, FakeExecutor(), config=AgentConfig(autonomy=AUTONOMY_ASK),
                  callbacks=Callbacks(on_confirm=lambda c, r: calls.append(c) or c))
    agent.run_turn("color it red")
    assert calls == [["color #1 red"]]


def test_error_feedback_and_stop():
    bad = {"calls": [("run_commands", {"commands": ["colr #1 red"]})]}
    prov = ScriptedProvider([bad] * (MAX_CONSECUTIVE_ERRORS + 2) + ["I could not do it."])
    ex = FakeExecutor(fail={"colr #1 red"})
    agent = Agent(prov, ex)
    res = agent.run_turn("color it red")
    # the identical command is refused after its first failure; after MAX_CONSECUTIVE_ERRORS the agent stops
    assert ex.ran.count("colr #1 red") == 1
    assert "could not" in res.reply
    sys_msgs = [m for m in agent.conversation if m.role == "user" and m.text().startswith("(system)")]
    assert len(sys_msgs) == 1
    tr = agent.conversation[2].tool_results_list()[0]
    assert tr.is_error and "Unknown command" in tr.content


def test_ask_user_ends_turn():
    prov = ScriptedProvider([{"calls": [("ask_user", {"question": "Which model?", "options": ["#1", "#2"]})]}])
    asked = []
    agent = Agent(prov, FakeExecutor(), callbacks=Callbacks(on_ask_user=lambda q, o: asked.append((q, o))))
    res = agent.run_turn("color the other one")
    assert res.asked_user and asked == [("Which model?", ["#1", "#2"])]
    assert len(prov.requests) == 1  # no second model call until the user answers


def test_python_requires_confirmation_and_is_optional():
    prov = ScriptedProvider([{"calls": [("run_python", {"code": "print(42)"})]}, "42"])
    ex = FakeExecutor()
    agent = Agent(prov, ex, config=AgentConfig(allow_python=True), callbacks=Callbacks(on_confirm=lambda c, r: c))
    agent.run_turn("compute")
    tr = agent.conversation[2].tool_results_list()[0]
    assert not tr.is_error and "42" in tr.content
    # without allow_python the tool is not offered
    agent2 = Agent(ScriptedProvider(["x"]), ex, config=AgentConfig(nudge_on_no_action=False))
    agent2.run_turn("hi")
    assert "run_python" not in [t.name for t in agent2.provider.requests[0][2]]


def test_provider_error_is_reported():
    from core.providers.base import ProviderError

    class Boom:
        name = "boom"
        supports_vision = False

        def stream(self, *a, **k):
            raise ProviderError("bad key")
    agent = Agent(Boom(), FakeExecutor())
    res = agent.run_turn("hi")
    assert res.error == "bad key" and agent.conversation[-1].text().startswith("Error: bad key")


def test_compaction_keeps_recent_and_summarises():
    prov = ScriptedProvider(["r%d" % i for i in range(30)] + ["SUMMARY"])
    agent = Agent(prov, FakeExecutor(), config=AgentConfig(compact_after_messages=10, nudge_on_no_action=False))
    for i in range(6):
        agent.run_turn("turn %d" % i)
    assert agent.summary
    assert len(agent.conversation) <= 10
    assert "Earlier in this conversation" in agent.system_prompt


def test_nudge_when_model_only_talks():
    # first reply claims success without any tool call; after the nudge it actually runs the command
    prov = ScriptedProvider(["The atoms are now hidden.",
                             {"calls": [("run_commands", {"commands": ["hide #1 atoms"]})]},
                             "Atoms hidden."])
    ex = FakeExecutor()
    agent = Agent(prov, ex)
    res = agent.run_turn("hide the atoms")
    assert ex.ran == ["hide #1 atoms"]
    assert res.reply == "Atoms hidden."
    nudges = [m for m in agent.conversation if m.role == "user" and m.text().startswith("(system) You did not call")]
    assert len(nudges) == 1


def test_no_nudge_for_questions_and_small_talk():
    for text in ["what is open?", "is chain A visible", "thanks", "How do I color by chain?"]:
        prov = ScriptedProvider(["Sure."])
        agent = Agent(prov, FakeExecutor())
        agent.run_turn(text)
        assert len(prov.requests) == 1, text


def test_compaction_archives_old_messages():
    prov = ScriptedProvider(["r%d" % i for i in range(30)] + ["SUMMARY"])
    agent = Agent(prov, FakeExecutor(), config=AgentConfig(compact_after_messages=10, nudge_on_no_action=False))
    for i in range(6):
        agent.run_turn("turn %d" % i)
    assert agent.summary
    assert len(agent.conversation) <= 10
    assert agent.archived and agent.archived[0].text() == "turn 0"
    assert all("context" not in m.meta for m in agent.archived)
    assert len(agent.archived) + len(agent.conversation) == 12


def test_failed_command_result_includes_usage_and_hint():
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["color #1 byaa"]})]}, "fixed"])
    ex = FakeExecutor(fail={"color #1 byaa"})
    agent = Agent(prov, ex)
    agent.run_turn("color by aa type")
    tr = json.loads(agent.conversation[2].tool_results_list()[0].content)
    assert tr["usage_of_color"] == "Usage: color spec"
    assert "search_docs" in tr["hint"]


def test_complaint_adds_note_with_previous_commands():
    from core.agent import looks_like_complaint
    assert looks_like_complaint("you did not")
    assert looks_like_complaint("are they thou?")
    assert looks_like_complaint("that didn't work, try again")
    assert not looks_like_complaint("color it red")
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["color #1 byelement"]})]}, "done",
                             {"calls": [("run_commands", {"commands": ["color #1:asp,glu red"]})]}, "ok now"])
    agent = Agent(prov, FakeExecutor())
    agent.run_turn("color it by element")
    agent.run_turn("you did not")
    ctx = next(m.meta.get("context", "") for m in agent.conversation if m.role == "user" and m.text() == "you did not")
    assert "<note>" in ctx and "color #1 byelement" in ctx and "Do not repeat" in ctx


def test_nudge_after_unfixed_error():
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["color #1 byaa"]})]},
                             "You could color residue classes like this: color #1:ala white ...",
                             {"calls": [("run_commands", {"commands": ["color #1:ala white"]})]},
                             "Done."])
    ex = FakeExecutor(fail={"color #1 byaa"})
    agent = Agent(prov, ex)
    res = agent.run_turn("color by aa type")
    assert "color #1:ala white" in ex.ran and res.reply == "Done."
    tr = json.loads(agent.conversation[2].tool_results_list()[0].content)
    assert "suggestion" in tr and "ala,val" in tr["suggestion"]
    assert any(m.role == "user" and "stopped without running" in m.text() for m in agent.conversation)


def test_only_requests_get_a_hide_nudge():
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["show #1/B atoms"]})]}, "Shown.",
                             {"calls": [("run_commands", {"commands": ["hide #1 target acs", "cartoon #1/B", "show #1/B atoms"]})]}, "Only chain B is shown."])
    ex = FakeExecutor()
    agent = Agent(prov, ex)
    res = agent.run_turn("show only chain B")
    assert "hide #1 target acs" in ex.ran and res.reply == "Only chain B is shown."
    # a request that already hides gets no nudge
    prov2 = ScriptedProvider([{"calls": [("run_commands", {"commands": ["hide #1 target acs", "show #1/B atoms"]})]}, "Done."])
    agent2 = Agent(prov2, FakeExecutor())
    agent2.run_turn("show only chain B")
    assert len(prov2.requests) == 2


def test_cancel_closes_dangling_tool_calls():
    import threading
    from core.schema import ToolCall

    class SlowExecutor(FakeExecutor):
        def __init__(self, cancel):
            super().__init__()
            self.cancel = cancel

        def run_commands(self, commands):
            self.cancel.set()  # user presses Stop while the tool runs
            return super().run_commands(commands)

    cancel = threading.Event()
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["open 4hhb"]}), ("get_state", {})]}, "never"])
    agent = Agent(prov, SlowExecutor(cancel))
    res = agent.run_turn("open 4hhb", cancel)
    assert res.error == "cancelled"
    # every tool call has a matching result before the '(stopped)' message
    calls = [c.id for m in agent.conversation if m.role == "assistant" for c in m.tool_calls()]
    results = [r.call_id for m in agent.conversation if m.role == "tool" for r in m.tool_results_list()]
    assert set(calls) == set(results)


def test_identical_failed_command_is_not_rerun():
    bad = {"calls": [("run_commands", {"commands": ["colr red"]})]}
    prov = ScriptedProvider([bad, bad, "gave up"])
    ex = FakeExecutor(fail={"colr red"})
    agent = Agent(prov, ex, config=AgentConfig(nudge_on_no_action=False))
    agent.run_turn("color it red")
    assert ex.ran.count("colr red") == 1
    tr = json.loads(agent.conversation[4].tool_results_list()[0].content)
    assert tr.get("repeated") and "already ran" in tr["error"]
