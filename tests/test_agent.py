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
    assert tr.is_error and "chose not" in tr.content
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
    # after MAX_CONSECUTIVE_ERRORS failures the agent asks the model to stop and explain
    assert ex.ran.count("colr #1 red") == MAX_CONSECUTIVE_ERRORS
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
    agent2 = Agent(ScriptedProvider(["x"]), ex)
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
    agent = Agent(prov, FakeExecutor())
    import core.agent as agent_mod
    old = agent_mod.COMPACT_AFTER_MESSAGES
    agent_mod.COMPACT_AFTER_MESSAGES = 10
    try:
        for i in range(6):
            agent.run_turn("turn %d" % i)
    finally:
        agent_mod.COMPACT_AFTER_MESSAGES = old
    assert agent.summary
    assert len(agent.conversation) <= 10
    assert "Earlier in this conversation" in agent.system_prompt
