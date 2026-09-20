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


def test_token_metered_presets_start_with_the_compact_prompt():
    from core.providers.presets import preset_by_id
    assert preset_by_id("groq")["compact_prompt"] is True
    assert preset_by_id("openrouter")["compact_prompt"] is True
    assert not preset_by_id("gemini").get("compact_prompt")
    assert not preset_by_id("ollama").get("compact_prompt")


def test_build_agent_picks_up_the_preset_compact_flag():
    """_build_agent once re-imported preset_by_id inside itself, which made the name local to the
    function and raised UnboundLocalError on every turn. Exercise the real path."""
    class Panel(PanelBase):
        edition = "chimerax"

        def __init__(self, preset):
            self._init_panel_state()
            self.pushed = []
            self.secrets = {"get": lambda k: "key"}
            self.secrets = type("Sec", (), {"get": staticmethod(lambda k: "key")})()
            self.executor = type("Ex", (), {"knowledge": type("K", (), {
                "command_directory": staticmethod(lambda: [("color", "color things")])})()})()
            self.settings = type("S", (), {
                "preset": preset, "provider": "openai", "model": "m", "base_url": "", "autonomy": "auto",
                "allow_python": False, "vision": False, "docs_per_turn": 6, "readable_labels": True,
                "think": False, "temperature": 0.2, "effort": ""})()

        def data_path(self, name):
            return "/nonexistent/" + name      # gotchas/recipes are optional

        def push(self, obj):
            self.pushed.append(obj)

    assert Panel("groq")._build_agent().config.compact_prompt is True
    assert Panel("mistral")._build_agent().config.compact_prompt is False
