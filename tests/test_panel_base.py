import json

from panel_base import PanelBase, export_lines
from core.agent import Agent, AgentConfig, Callbacks
from core.schema import Message, TextPart, ToolCall, ToolResult
from test_agent import FakeExecutor, ScriptedProvider


class FakePanel(PanelBase):
    def __init__(self, agent):
        self._init_panel_state()
        self.agent = agent
        self.pushed = []

    def push(self, obj):
        self.pushed.append(obj)


def test_history_rendering_uses_real_results():
    ex = FakeExecutor(fail={"colr red"})
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["open 4hhb", "colr red"]})]}, "partly"])
    agent = Agent(prov, ex, config=AgentConfig(nudge_on_no_action=False))
    agent.run_turn("open it and color it")
    panel = FakePanel(agent)
    msgs = panel._render_messages()
    call = msgs[1]["calls"][0]
    assert call["ok"] is False
    assert [(r["command"], r["ok"]) for r in call["results"]] == [("open 4hhb", True), ("colr red", False)]
    lines = export_lines(agent)
    assert "open 4hhb" in lines and any(l.startswith("# failed: colr red") for l in lines)


def test_skipped_commands_are_rendered_as_skipped():
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["close"]})]}, "ok"])
    agent = Agent(prov, FakeExecutor(), callbacks=Callbacks(on_confirm=lambda c, r, p=False: None))
    agent.run_turn("close everything")
    call = FakePanel(agent)._render_messages()[1]["calls"][0]
    assert call["skipped"] is True and call["results"] == []
