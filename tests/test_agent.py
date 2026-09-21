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
            if any(c == f or c.startswith(f) for f in self.fail):
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
        return {"accession": "P07550", "gene": "ADRB2", "open_command": "open alphafold:P07550"}

    def protein_features(self, accession, kinds=None):
        return {"features": []}

    def list_residues(self, model):
        return {"model": "#1", "residues": {"A:10": "ALA", "A:11": "ARG", "A:12": "GLY", "B:10": "ALA"}}

    def count_residues(self, spec):
        return 40 if "1-40" in spec else 1

    def spec_atoms(self, text):
        if text.startswith("#1:226-232"):
            return {"used": "#1:226-232", "atoms": 0, "residues": 0}
        if text.startswith("#") or text.startswith(":"):
            return {"used": text.split()[0], "atoms": 12, "residues": 1}
        return {"used": ""}

    def residue_provenance(self, res_spec, journal):
        colors = [j for j in journal if j["command"].startswith("color")]
        return {"residue": "HIS 87", "chain": "A", "model": "#1", "spec": "#1/A:87", "ribbon_color": "#ff0000", "atoms_shown": 0,
                "selected": True, "attributes": {}, "labels": [],
                "history": [{"command": c["command"], "origin": c["origin"], "kind": "color", "commands_ago": 1, "everything": False} for c in colors[-1:]],
                "last_color_command": ({"command": colors[-1]["command"], "origin": colors[-1]["origin"], "kind": "color", "commands_ago": 1, "everything": False} if colors else None)}

    def figure_info(self):
        return {"models": [{"id": "#1", "name": "4hhb", "source": "PDB 4HHB", "chains": ["A", "B"], "residues": 574, "atoms": 4779, "shown": True}],
                "background": "#ffffff", "camera": {"type": "mono", "position": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 50]], "field_of_view": 30.0},
                "window": [800, 600], "labels": 2, "chimerax_version": "1.12"}

    def residue_colors(self):
        return [["#1", "A", 87, "HIS", "#ff0000", "#ff0000"], ["#1", "A", 88, "ALA", "#7b68ee", ""]]

    def label_layout(self):
        return {"labels": [{"spec": "#1/A:10", "level": "residues", "text": "HIS 10", "x": 100, "y": 100, "w": 60, "h": 16, "per_px": 0.05, "offset": [0.0, 0.0, 0.5]},
                           {"spec": "#1/A:11", "level": "residues", "text": "LYS 11", "x": 104, "y": 102, "w": 60, "h": 16, "per_px": 0.05, "offset": [0.0, 0.0, 0.5]},
                           {"spec": "#1/A:50", "level": "residues", "text": "GLY 50", "x": 400, "y": 300, "w": 60, "h": 16, "per_px": 0.05, "offset": [0.0, 0.0, 0.5]}],
                "window": [800, 600]}

    def set_residue_attr(self, model, attr, values):
        self.attrs = (attr, dict(values))
        return {"set": len(values), "attr": attr}

    def map_positions(self, model, accession, positions):
        return {"model": "#1", "map": {p: {"chain": "A", "number": p + 100, "resname": "ALA"} for p in positions}, "offsets": {"A": 100}, "unmapped": []}

    def run_python(self, code):
        return {"ok": True, "stdout": "42"}

    def look_at_view(self):
        return {"text": "shot", "png_b64": "AAAA"}

    def prepare_compare(self, reference, other, chain=None):
        if reference == other:
            return {"error": "same model"}
        return {"ref_id": reference.lstrip("#"), "other_id": other.lstrip("#"), "chain": chain or "A",
                "ref_spec": reference + "/A", "other_spec": other + "/A"}

    def compute_displacement(self, prep):
        return {"pairing": "fake", "paired_residues": 10, "mean_displacement": 1.5, "max_displacement": 4.0, "residues_over_2A": 3,
                "moving_regions": [{"chain": "A", "range": "30-32", "max": 4.0, "spec": "#2/A:30-32"}],
                "color_commands": ["color byattribute r:pellaeon_disp #2/A palette bluered", "color #1/A #9ecae1 target ac"]}

    def map_positions(self, model, accession, positions):
        # pretend the model is numbered one lower than UniProt (initiator Met missing) and residue 7 is GLU
        return {"model": "#1", "map": {p: {"chain": "A", "number": p - 1, "resname": "GLU" if p == 7 else "ALA"} for p in positions if p > 1},
                "chains": ["A"], "unmapped": [p for p in positions if p <= 1], "note": ""}


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
        {"text": "Looking it up.", "calls": [("resolve_protein", {"query": "adrb2"})]},
        {"calls": [("run_commands", {"commands": ["open alphafold:P07550", "color #1 white"]})]},
        "Opened ADRB2 and colored it white.",
    ])
    ex = FakeExecutor()
    seen = {"tools": [], "deltas": []}
    cb = Callbacks(on_text_delta=seen["deltas"].append, on_tool_start=lambda c: seen["tools"].append(c.name))
    agent = Agent(prov, ex, callbacks=cb, config=AgentConfig(autonomy=AUTONOMY_AUTO))
    res = agent.run_turn("open the af model for adrb2 and color it white")
    assert res.reply == "Opened ADRB2 and colored it white."
    assert res.error is None
    assert ex.ran == ["open alphafold:P07550", "color #1 white"]
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


def test_named_groups_by_invented_numbers_are_nudged():
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["open 4hhb", "style #1:226-232 sphere"]})]}, "Hemes shown.",
                             {"calls": [("run_commands", {"commands": ["show :HEM atoms", "style :HEM sphere", "color :HEM red"]})]}, "Hemes are red spheres."])
    ex = FakeExecutor()
    agent = Agent(prov, ex)
    res = agent.run_turn("open 4hhb and show the heme groups as red spheres")
    assert "style :HEM sphere" in ex.ran and res.reply == "Hemes are red spheres."
    assert any("residue NAME" in str(m.parts[0].text) for _, msgs, _ in prov.requests[-1:] for m in msgs if m.role == "user")
    # residue names or selectors used: no nudge
    prov2 = ScriptedProvider([{"calls": [("run_commands", {"commands": ["show ligand atoms", "style ligand sphere"]})]}, "Done."])
    agent2 = Agent(prov2, FakeExecutor())
    agent2.run_turn("show the ligands as spheres")
    assert len(prov2.requests) == 2
    # the user gave the numbers: no nudge
    prov3 = ScriptedProvider([{"calls": [("run_commands", {"commands": ["color #1:142 red"]})]}, "Done."])
    agent3 = Agent(prov3, FakeExecutor())
    agent3.run_turn("color the heme at residue 142 red")
    assert len(prov3.requests) == 2


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


def test_resolve_protein_with_pdb_id_gives_hint():
    prov = ScriptedProvider([{"calls": [("resolve_protein", {"query": "4ake"})]}, "ok"])
    agent = Agent(prov, FakeExecutor())
    agent.run_turn("open 4ake")
    tr = agent.conversation[2].tool_results_list()[0]
    assert "open 4ake" in tr.content and not tr.is_error


def test_empty_reply_is_retried_once():
    prov = ScriptedProvider([{"text": "", "calls": []}, "second try worked"])
    agent = Agent(prov, FakeExecutor(), config=AgentConfig(nudge_on_no_action=False))
    res = agent.run_turn("what is open?")
    assert res.reply == "second try worked" and len(prov.requests) == 2


def test_confirmation_fails_closed_without_callback():
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["close"]})]}, "ok"])
    ex = FakeExecutor()
    Agent(prov, ex).run_turn("close everything")
    assert ex.ran == []


def test_disabled_python_is_refused_at_dispatch():
    prov = ScriptedProvider([{"calls": [("run_python", {"code": "print(1)"})]}, "ok"])
    agent = Agent(prov, FakeExecutor(), callbacks=Callbacks(on_confirm=lambda c, r, p=False: c))
    agent.run_turn("compute")
    tr = agent.conversation[2].tool_results_list()[0]
    assert tr.is_error and "disabled" in tr.content


def test_multiline_python_reaches_executor_verbatim():
    code = "def f():\n    return 42\nprint(f())"
    seen = {}

    class Ex(FakeExecutor):
        def run_python(self, c):
            seen["code"] = c
            return {"ok": True, "stdout": "42"}
    prov = ScriptedProvider([{"calls": [("run_python", {"code": code})]}, "42"])
    agent = Agent(prov, Ex(), config=AgentConfig(allow_python=True), callbacks=Callbacks(on_confirm=lambda c, r, p=False: c))
    agent.run_turn("compute")
    assert seen["code"] == code


def test_ask_user_stops_remaining_calls():
    prov = ScriptedProvider([{"calls": [("ask_user", {"question": "Which?"}), ("run_commands", {"commands": ["color #1 red"]})]}])
    ex = FakeExecutor()
    agent = Agent(prov, ex, callbacks=Callbacks(on_ask_user=lambda q, o: None))
    agent.run_turn("color the other one")
    assert ex.ran == []


def test_cancel_keeps_completed_results():
    import threading

    class SlowExecutor(FakeExecutor):
        def __init__(self, cancel):
            super().__init__()
            self.cancel = cancel

        def run_commands(self, commands):
            self.cancel.set()
            return super().run_commands(commands)
    cancel = threading.Event()
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["open 4hhb"]}), ("get_state", {})]}, "never"])
    ex = SlowExecutor(cancel)
    agent = Agent(prov, ex, cancel := cancel) if False else Agent(prov, ex)
    agent.run_turn("open 4hhb", cancel)
    results = {r.call_id: r for m in agent.conversation if m.role == "tool" for r in m.tool_results_list()}
    assert not results["id0"].is_error and '"ok": true' in results["id0"].content
    assert results["id1"].is_error and "Cancelled" in results["id1"].content


def test_compare_goes_through_the_policy_boundary():
    prov = ScriptedProvider([{"calls": [("compare_structures", {"reference": "#1", "other": "#2"})]}, "done"])
    ex = FakeExecutor()
    asked = []
    agent = Agent(prov, ex, config=AgentConfig(autonomy=AUTONOMY_ASK), callbacks=Callbacks(on_confirm=lambda c, r, p=False: asked.append(list(c)) or c))
    agent.run_turn("compare these")
    assert asked[0] == ["matchmaker #2/A to #1/A"] and asked[1][0].startswith("color byattribute")
    assert [j["origin"] for j in agent.journal] == ["compare"] * 3
    payload = json.loads(agent.conversation[2].tool_results_list()[0].content)
    assert payload["moving_regions"][0]["range"] == "30-32" and payload["colored"] is True and "commands" not in payload


def test_annotate_uses_mapping_and_checks_reference_residue(monkeypatch):
    class Ex(FakeExecutor):
        def protein_features(self, accession, kinds=None):
            return {"gene": "HBB", "features": [
                {"type": "Natural variant", "start": 7, "end": 7, "description": "in SKCA; Hb S", "spec": ":7"},
                {"type": "Domain", "start": 3, "end": 6, "description": "Globin", "spec": ":3-6"},
                {"type": "Disulfide bond", "start": 2, "end": 5, "description": "", "spec": ":2,5"}]}
    prov = ScriptedProvider([{"calls": [("annotate", {"model": "#1", "accession": "P68871", "kind": "variant"})]}, "done"])
    ex = Ex()
    agent = Agent(prov, ex, callbacks=Callbacks(on_confirm=lambda c, r, p=False: c))
    agent.run_turn("show the variants")
    # positions shifted by the mapping (7 -> 6, 3-6 -> 2-5), disulfide colors only its two cysteines (2,5 -> 1,4)
    assert "color #1/A:1-6 orange target ac" in ex.ran or any(c.startswith("color #1/A:") and "orange" in c for c in ex.ran)
    assert any('label #1/A:6 text "in SKCA"' in c for c in ex.ran)
    payload = json.loads(agent.conversation[2].tool_results_list()[0].content)
    assert payload["mapped"] == 3 and payload["unmapped"] == 0 and payload["ok"] is True
    assert all(j["origin"] == "annotate" for j in agent.journal)


def test_clinvar_skips_reference_mismatch(monkeypatch):
    import core.agent as agent_mod

    class FakeCV:
        def missense_variants(self, gene):
            return {"gene": gene, "count": 2, "searched": 2, "variants": [
                {"position": 7, "ref": "E", "alt": "V", "significance": "Pathogenic", "id": "1"},
                {"position": 9, "ref": "K", "alt": "R", "significance": "Benign", "id": "2"}]}  # model has ALA at 9-1
    ex = FakeExecutor()
    ex.clinvar = FakeCV()
    prov = ScriptedProvider([{"calls": [("annotate", {"model": "#1", "accession": "HBB", "kind": "clinvar"})]}, "done"])
    agent = Agent(prov, ex, callbacks=Callbacks(on_confirm=lambda c, r, p=False: c))
    agent.run_turn("show clinvar variants")
    payload = json.loads(agent.conversation[2].tool_results_list()[0].content)
    assert payload["mapped"] == 1 and payload["reference_mismatch"] == 1
    assert any("color #1/A:6 red" in c for c in ex.ran) and not any(":8" in c for c in ex.ran)


def test_rerun_path_uses_execute_commands_and_journal():
    agent = Agent(ScriptedProvider([]), FakeExecutor(), callbacks=Callbacks(on_confirm=lambda c, r, p=False: None))
    out = agent.execute_commands(["close"], origin="rerun")
    assert out["skipped"] and agent.journal == []
    out = agent.execute_commands(["color #1 red"], origin="rerun")
    assert out["ok"] and agent.journal[-1]["origin"] == "rerun"


def test_tool_name_typed_as_command_is_refused():
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["annotate #1 model P68871 kind clinvar"]})]}, "ok"])
    ex = FakeExecutor()
    agent = Agent(prov, ex, config=AgentConfig(nudge_on_no_action=False))
    agent.run_turn("show variants")
    assert ex.ran == []
    tr = json.loads(agent.conversation[2].tool_results_list()[0].content)
    assert tr["tool_misuse"] == "annotate" and "YOUR TOOLS" in tr["error"]


def test_the_wizard_offers_the_free_options_first():
    from core.providers.presets import PRESETS
    # measured order: Mistral (38/38, ~190 requests a minute), then Gemini (37/38, 15 a minute), then local
    assert [p["id"] for p in PRESETS[:3]] == ["mistral", "gemini", "ollama"]
    assert all(PRESETS[i]["free"] for i in range(3))


def test_variant_requests_are_nudged_to_the_annotate_tool():
    prov = ScriptedProvider(["Here is how you could show variants: ...",
                             {"calls": [("annotate", {"model": "#1", "accession": "HBB", "kind": "clinvar"})]}, "done"])
    ex = FakeExecutor()
    ex.clinvar = type("CV", (), {"missense_variants": lambda self, g: {"gene": g, "count": 0, "searched": 0, "variants": []}})()
    agent = Agent(prov, ex, callbacks=Callbacks(on_confirm=lambda c, r, p=False: c))
    agent.run_turn("show the clinvar disease variants on it")
    assert any(c.name == "annotate" for m in agent.conversation if m.role == "assistant" for c in m.tool_calls())


def test_unverified_alphafold_accession_is_refused():
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["open alphafold:P69905"]})]},
                             {"calls": [("resolve_protein", {"query": "HBB"})]},
                             {"calls": [("run_commands", {"commands": ["open alphafold:P07550"]})]}, "done"])
    ex = FakeExecutor()
    agent = Agent(prov, ex, config=AgentConfig(nudge_on_no_action=False))
    agent.run_turn("open the alphafold model of the gene HBB")
    assert "open alphafold:P69905" not in ex.ran and "open alphafold:P07550" in ex.ran  # P07550 came from resolve_protein
    tr = json.loads(agent.conversation[2].tool_results_list()[0].content)
    assert tr["unverified_accession"] == "P69905"
    # an accession typed by the user is fine without a lookup
    prov2 = ScriptedProvider([{"calls": [("run_commands", {"commands": ["open alphafold:Q8IY22"]})]}, "ok"])
    ex2 = FakeExecutor()
    Agent(prov2, ex2, config=AgentConfig(nudge_on_no_action=False)).run_turn("open alphafold q8iy22")
    assert ex2.ran == ["open alphafold:Q8IY22"]


def test_rmsd_line_parsing_separates_fit_subset_from_all_pairs():
    from core.agent import parse_rmsd_line
    r = parse_rmsd_line("RMSD between 141 pruned atom pairs is 1.083 angstroms; (across all 214 pairs: 5.310)")
    assert r == {"fit_pairs": 141, "fit_rmsd": 1.083, "all_pairs": 214, "all_rmsd": 5.31}
    assert parse_rmsd_line("RMSD between 50 pruned atom pairs is 0.500 angstroms") == {"fit_pairs": 50, "fit_rmsd": 0.5}
    assert parse_rmsd_line("no rmsd here") == {}


def _loaded_table():
    from core.tables import parse_table, guess_columns
    t = parse_table("position,wt,score\n10,A,0.9\n11,K,0.1\n12,G,0.5\n99,L,0.2\n", "scores.csv")
    g = guess_columns(t["columns"], t["rows"])
    return {"scores": {"id": "scores", "name": "scores", "path": "scores.csv", "columns": t["columns"], "rows": t["rows"], "guess": g}}


def test_table_overlay_tool_colors_and_reports_mapping():
    prov = ScriptedProvider([{"calls": [("table_overlay", {"column": "score"})]}, "Colored by score."])
    ex = FakeExecutor()
    agent = Agent(prov, ex)
    agent.tables = _loaded_table()
    res = agent.run_turn("color by the conservation score")
    assert res.reply == "Colored by score."
    assert ex.attrs[0] == "pellaeon_scores_score" and ex.attrs[1] == {"A:10": 0.9, "A:12": 0.5, "B:10": 0.9}
    assert any(c.startswith("color byattribute r:pellaeon_scores_score #1 palette blue:white:red") for c in ex.ran)
    assert agent.layers and agent.layers[0]["mapped"] == 3
    # the state block told the model about the table
    assert "Loaded table 'scores'" in str(prov.requests[0])
    # mismatch (A:11 is ARG, table says K -> same; make a real mismatch) and missing position reported
    out = agent._table_overlay("scores", "score", None, "", "#1", None, False)
    assert out["n_missing"] == 1 and out["missing"] == [99] and out["n_mismatches"] == 1   # A:11 is Arg, the table says K


def test_table_overlay_without_table_or_column_errors():
    agent = Agent(ScriptedProvider([]), FakeExecutor())
    assert "No table is loaded" in agent._table_overlay("", "", None, "", "", None, False)["error"]
    agent.tables = _loaded_table()
    assert "no value column" in agent._table_overlay("scores", "nope", None, "", "", None, False)["error"]


def test_table_overlay_uses_uniprot_numbering_when_asked():
    agent = Agent(ScriptedProvider([]), FakeExecutor())
    agent.tables = _loaded_table()
    out = agent._table_overlay("scores", "score", None, "", "#1", "P12345", False)
    assert "error" in out   # positions 110-112 do not exist in the fake structure, so nothing is placed
    assert out["numbering"].startswith("UniProt P12345")


def test_table_attribute_selector_hint_after_failed_command():
    prov = ScriptedProvider([{"calls": [("table_overlay", {"column": "score"})]},
                             {"calls": [("run_commands", {"commands": ["label #1:score>0.5 residues"]})]},
                             {"calls": [("run_commands", {"commands": ["label #1::pellaeon_scores_score>0.5 residues"]})]}, "Labeled."])
    ex = FakeExecutor(fail=("label #1:score>0.5 residues",))
    agent = Agent(prov, ex)
    agent.tables = _loaded_table()
    res = agent.run_turn("label residues with score above 0.5")
    assert res.reply == "Labeled."
    hint = str(prov.requests[2])
    assert "two colons" in hint and "#1::pellaeon_scores_score" in hint
    assert "#1::pellaeon_scores_score" in str(prov.requests[-1])   # the state block lists the attribute too


def test_table_column_words_do_not_trigger_residue_type_recoloring():
    # "hydrophobic" normally triggers the residue-type nudge/fallback; with a loaded table whose column is named in the request it must not
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["label #1::pellaeon_scores_score>0.5 residues"]})]}, "Labeled."])
    ex = FakeExecutor()
    agent = Agent(prov, ex)
    agent.tables = _loaded_table()
    res = agent.run_turn("label the hydrophobic residues with score above 0.5")
    assert res.reply == "Labeled." and len(prov.requests) == 2 and not any(":ala,val" in c for c in ex.ran)


def test_guessed_accession_is_verified_against_uniprot_instead_of_refused():
    class Ex(FakeExecutor):
        def protein_features(self, accession, kinds=None):
            return {"gene": "ADRB2", "names": ["Adrenergic receptor"], "features": []}
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["open alphafold:P07550"]})]}, "Opened ADRB2."])
    ex = Ex()
    agent = Agent(prov, ex)
    res = agent.run_turn("open the alphafold model of ADRB2")
    assert res.reply == "Opened ADRB2." and "open alphafold:P07550" in ex.ran and len(prov.requests) == 2
    # a guessed accession for a protein the user did not name is still refused, with the real gene named
    prov2 = ScriptedProvider([{"calls": [("run_commands", {"commands": ["open alphafold:P07550"]})]}, "Hmm."])
    ex2 = Ex()
    Agent(prov2, ex2).run_turn("open the alphafold model of TERT")
    assert "open alphafold:P07550" not in ex2.ran and "UniProt says it is ADRB2" in str(prov2.requests[1])


def test_plain_label_commands_get_readable_style_and_dense_sets_short_text():
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["label #1/A:87", "label #1/A:1-40 residues", "label #1:5 height 2"]})]}, "Labeled."])
    ex = FakeExecutor()
    agent = Agent(prov, ex)
    agent.run_turn("label residue 87 and the first 40 residues")
    assert ex.ran[0] == "label #1/A:87 size 16 height fixed onTop true color black bgColor #ffffffd9"
    assert 'text "{0.one_letter_code}{0.number}" size 12' in ex.ran[1]
    assert ex.ran[2] == "label #1:5 height 2"          # explicit style is respected
    assert "one-letter" in str(prov.requests[1])       # the note reached the model
    prov2 = ScriptedProvider([{"calls": [("run_commands", {"commands": ["label #1/A:87"]})]}, "ok"])
    ex2 = FakeExecutor()
    Agent(prov2, ex2, config=AgentConfig(readable_labels=False)).run_turn("label 87")
    assert ex2.ran == ["label #1/A:87"]


def test_list_valued_tool_args_are_reduced_to_scalars():
    from core.agent import Agent as A
    assert A._scalar(["clinvar"]) == "clinvar" and A._scalar("['clinvar']") == "clinvar" and A._scalar('["disease", "x"]') == "disease"
    assert A._scalar("clinvar") == "clinvar" and A._scalar(None, "variant") == "variant" and A._scalar([], "d") == "d"


def test_tidy_labels_moves_overlaps_and_restyles():
    prov = ScriptedProvider([{"calls": [("tidy_labels", {})]}, "Tidied."])
    ex = FakeExecutor()
    agent = Agent(prov, ex)
    res = agent.run_turn("the labels overlap, tidy them")
    assert res.reply.startswith("Tidied 3 labels")
    assert any(c.startswith("label #1/A:11 residues offset") for c in ex.ran)
    assert any(c.startswith("label #1/A:10 #1/A:11 #1/A:50 size") and "bgColor" in c for c in ex.ran)
    # a complaint about labels runs the tool before the model even answers, whatever the model would have done
    prov2 = ScriptedProvider(["The labels are tidy now."])
    ex2 = FakeExecutor()
    agent2 = Agent(prov2, ex2)
    r = agent2.run_turn("the labels are unreadable")
    assert r.reply.startswith("Tidied 3 labels: 1 moved") and not prov2.requests   # answered without the model
    assert any(c.startswith("label #1/A:11 residues offset") for c in ex2.ran)
    # combined with another action the model still gets the turn, with the tidy result in front of it
    prov3 = ScriptedProvider(["Done both."])
    Agent(prov3, FakeExecutor()).run_turn("tidy the labels and color the protein white")
    assert "tidy_labels" in str(prov3.requests[0])


def test_commands_that_match_nothing_are_flagged_not_green():
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["open 4hhb", "style #1:226-232 sphere"]})]},
                             {"calls": [("run_commands", {"commands": ["style #1:HEM sphere"]})]}, "Spheres."])
    ex = FakeExecutor()
    agent = Agent(prov, ex)
    res = agent.run_turn("show the hemes as spheres")
    assert res.reply == "Spheres."
    first = str(prov.requests[1])
    assert "matched NOTHING" in first and "#1:226-232" in first
    assert any(j["command"].startswith("style #1:226-232") and j["noop"] for j in agent.journal)
    assert any(j["command"] == "style #1:HEM sphere" and not j["noop"] for j in agent.journal)


def test_why_is_this_red_is_answered_from_the_journal():
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["open 4hhb", "color #1/A:87 red"]})]}, "Colored.", "unused"])
    ex = FakeExecutor()
    agent = Agent(prov, ex)
    agent.run_turn("color residue 87 of chain A red")
    r = agent.run_turn("why is residue #1/A:87 red?")
    assert "HIS 87" in r.reply and "color #1/A:87 red" in r.reply and "from your request" in r.reply
    assert len(prov.requests) == 2        # the question was answered without the model


def test_why_regex_does_not_hijack_ordinary_requests():
    from core.agent import _WHY_RE
    for q in ("What residues and ligands are within 5 A of residue 87 (HIS) of chain A in #1? Show them as sticks and label them.",
              "label the residues with hydropathy above 3", "what is this residue", "color it red and label it"):
        assert not _WHY_RE.search(q), q
    for q in ("why is this red?", "Why is residue 87 (HIS) of chain A in 4hhb this color?", "what colored residue 87", "where did this label come from", "which command made it blue"):
        assert _WHY_RE.search(q), q


def test_figure_bundle_writes_all_files_and_a_legend(tmp_path):
    from core.agent import Callbacks
    prov = ScriptedProvider([{"calls": [("run_commands", {"commands": ["open 4hhb", "color #1 bychain", "color #1/A:87 red", "label #1/A:87"]})]}, "Done."])
    ex = FakeExecutor()
    agent = Agent(prov, ex)
    agent.run_turn("open 4hhb, color by chain, make residue 87 red and label it")
    out = agent._figure_bundle(str(tmp_path), "pocket", 1600, 1200, 2, False, "#1/A:87 :<6", True, preapproved=True)
    assert "error" not in out and out["ok"]
    folder = tmp_path / "pocket"
    for f in ("pocket.cxc", "pocket_colors.csv", "pocket_legend.md", "pocket.json"):
        assert (folder / f).exists(), f
    assert any(c.startswith('save "%s" width 1600 height 1200 supersample 2' % (folder / "pocket.png")) for c in ex.ran)
    assert any(c.startswith('save "%s" width 1600' % (folder / "pocket_closeup.png")) for c in ex.ran)
    assert any(c == 'save "%s"' % (folder / "pocket.cxs") for c in ex.ran)
    legend = (folder / "pocket_legend.md").read_text()
    assert "PDB 4HHB" in legend and "color #1/A:87 red" in legend and "2 labels" in legend
    script = (folder / "pocket.cxc").read_text()
    assert "color #1 bychain" in script and "# #1 4hhb: PDB 4HHB" in script
    import json as _json
    meta = _json.loads((folder / "pocket.json").read_text())
    assert meta["image"] == {"width": 1600, "height": 1200, "supersample": 2, "transparent": False} and meta["closeup"] == "#1/A:87 :<6"
    # a model-initiated save asks first: with no way to confirm it is refused
    out2 = agent._figure_bundle(str(tmp_path), "x", 800, 600, 1, False, "", False, preapproved=False)
    assert out2.get("skipped") and "approve" in out2["error"]


def test_look_nudge_only_when_the_model_can_see():
    from core.agent import _LOOK_RE
    assert _LOOK_RE.search("how does it look?") and _LOOK_RE.search("review the figure") and not _LOOK_RE.search("color it red")
    prov = ScriptedProvider(["Looks fine.", {"calls": [("look_at_view", {})]}, "I looked: fine."])
    prov.supports_vision = True
    ex = FakeExecutor()
    ex.look_at_view = lambda: {"text": "Screenshot attached.", "png_b64": "aGVsbG8="}
    agent = Agent(prov, ex, config=AgentConfig(vision=True))
    assert agent.run_turn("take a look at the view and tell me how it looks").reply == "I looked: fine."
    prov2 = ScriptedProvider(["Looks fine.", "Still fine."])   # no vision: the look nudge never fires
    agent2 = Agent(prov2, FakeExecutor(), config=AgentConfig(vision=False))
    agent2.run_turn("take a look at the view")
    assert not any("LOOK at the view" in str(r) for r in prov2.requests)


def test_tool_call_written_as_text_is_recovered_and_run():
    prov = ScriptedProvider(['run_commands "color #1 white ; color #1:10 blue"', "Done: residue 10 is blue."])
    ex = FakeExecutor()
    agent = Agent(prov, ex)
    res = agent.run_turn("color everything white but residue 10 blue")
    assert ex.ran == ["color #1 white", "color #1:10 blue"] and agent.recovered_calls == 1
    assert res.reply == "Done: residue 10 is blue."


def test_commands_only_fallback_rescues_a_model_that_never_calls_tools():
    # words, words again after the nudge, then bare command lines when asked for commands only
    prov = ScriptedProvider(["I have colored the chains.", "The chains are now colored by chain ID.", "color #1 bychain\nshow ligand atoms"])
    ex = FakeExecutor()
    agent = Agent(prov, ex)
    res = agent.run_turn("color by chain and show the ligand")
    assert ex.ran == ["color #1 bychain", "show ligand atoms"] and agent.commands_only_turns == 1
    assert res.reply.startswith("Ran: `color #1 bychain`")


def test_asking_which_model_with_one_open_gets_a_pointed_nudge():
    prov = ScriptedProvider(["I need to know which model you want me to color.", {"calls": [("run_commands", {"commands": ["color #1 red"]})]}, "Colored #1 red."])
    ex = FakeExecutor(); ex.state = {"models": [{"id": "#1", "name": "1zik"}], "selection": {}}
    agent = Agent(prov, ex)
    assert agent.run_turn("color it red").reply == "Colored #1 red."
    assert any("Exactly ONE model is open" in str(r) for r in prov.requests)


def test_compact_prompt_drops_recipes_and_shortens_the_directory():
    from core import prompt as P
    directory = [("cmd%02d" % i, "purpose number %d, described at some length" % i) for i in range(200)]
    recipes = [{"request": "color it blue", "commands": ["color blue"]}] * 30
    full = P.build_system_prompt(directory=directory, gotchas="- rule one\n- rule two", recipes=recipes)
    small = P.build_system_prompt(directory=directory, gotchas="- rule one\n- rule two", recipes=recipes, compact=True)
    assert len(small) < 0.6 * len(full)
    assert "color it blue" in full and "color it blue" not in small     # recipes dropped
    assert "rule one" in small and "rule two" in small                  # gotchas kept: they are what small models need
    assert "cmd00" in small                                             # the directory is shortened, not removed


def test_trim_context_shrinks_only_the_docs_block():
    from core import prompt as P
    ctx = P.build_context({"models": []}, [{"title": "color", "section": "usage", "text": "x" * 4000}])
    small = P.trim_context(ctx, 500)
    assert "<chimerax_state>" in small and len(small) < len(ctx)


def test_agent_retries_compactly_when_the_request_is_too_large():
    from core.providers.base import ProviderError
    ex = FakeExecutor()
    prov = ScriptedProvider([])
    sizes = []

    def stream(system, messages, tools, on_delta=None, cancel=None):
        sizes.append(len(system))
        if len(sizes) == 1:
            raise ProviderError("HTTP 413: Request too large ... on input tokens per minute (ITPM): Limit 7000")
        return Message("assistant", [TextPart("ok")]), Usage()

    prov.stream = stream
    agent = Agent(prov, ex, directory=[("color", "color things")] * 300,
                  gotchas="- a rule", recipes=[{"request": "r", "commands": ["c"]}])
    result = agent.run_turn("color it blue")
    assert result.reply == "ok"
    assert len(sizes) >= 2 and sizes[1] < sizes[0]     # retried with a shorter prompt
    assert all(z == sizes[1] for z in sizes[1:])       # and stayed compact for the rest of the turn
    assert agent.compact


def test_agent_does_not_loop_when_compact_is_still_too_large():
    from core.providers.base import ProviderError
    prov = ScriptedProvider([])
    calls = {"n": 0}

    def stream(system, messages, tools, on_delta=None, cancel=None):
        calls["n"] += 1
        raise ProviderError("HTTP 413: Request too large")

    prov.stream = stream
    agent = Agent(prov, FakeExecutor(), directory=[("color", "color things")], gotchas="- a rule")
    result = agent.run_turn("color it blue")
    assert calls["n"] == 2 and result.error


def test_the_model_is_offered_the_tools_the_prompt_tells_it_to_use():
    from core.tools import tool_specs
    names = {t.name for t in tool_specs()}
    # the gotchas tell the model to use these by name, so they have to be on the list
    assert "save_figure" in names and "tidy_labels" in names and "explain_residue" in names
    assert "table_overlay" not in names                       # nothing loaded yet
    assert "table_overlay" in {t.name for t in tool_specs(tables=True)}
    lean = {t.name for t in tool_specs(tables=True, compact=True)}
    assert "table_overlay" in lean and "save_figure" in lean   # kept: no other way to reach them
    # the nudges order the model to call tidy_labels / explain_residue and a gotcha names map_numbering:
    # a tool the prompt demands must be on the list even when compacting
    assert {"tidy_labels", "explain_residue", "map_numbering"} <= lean
    assert "apply_figure_style" not in lean


def test_agent_offers_table_overlay_once_a_table_is_loaded():
    ex = FakeExecutor()
    prov = ScriptedProvider(["done"])
    agent = Agent(prov, ex)
    agent.run_turn("hello")
    assert "table_overlay" not in {t.name for t in prov.requests[0][2]}
    agent.tables["hydropathy"] = {"columns": ["kd"]}
    prov.script = ["done"]
    agent.run_turn("color by the table")
    offered = {t.name for _sys, _msgs, tools in prov.requests[1:] for t in tools}
    assert "table_overlay" in offered


def test_saving_is_refused_when_the_user_asked_for_something_chimerax_cannot_do():
    ex = FakeExecutor()
    prov = ScriptedProvider([{"text": "", "calls": [("run_commands", {"commands": ["save ~/Desktop/x.png"]})]},
                             "ChimeraX cannot email a structure."])
    agent = Agent(prov, ex, config=AgentConfig(autonomy=AUTONOMY_AUTO),
                  callbacks=Callbacks(on_confirm=lambda c, r: c))
    agent.run_turn("email this structure to my boss")
    assert not any(j["command"].startswith("save") for j in agent.journal)
    told = [r.content for m in agent.conversation if m.role == "tool" for r in m.tool_results_list()]
    assert any("cannot do" in t and "not run any other command" in t for t in told)


def test_saving_is_allowed_when_the_user_asked_to_save_and_send():
    ex = FakeExecutor()
    prov = ScriptedProvider([{"text": "", "calls": [("run_commands", {"commands": ["save ~/Desktop/x.png"]})]},
                             "Saved."])
    agent = Agent(prov, ex, config=AgentConfig(autonomy=AUTONOMY_AUTO),
                  callbacks=Callbacks(on_confirm=lambda c, r: c))
    agent.run_turn("save a picture to my desktop so I can email it to my boss")
    assert any(j["command"].startswith("save") for j in agent.journal)


def test_no_action_nudge_is_skipped_for_requests_chimerax_cannot_fulfil():
    ex = FakeExecutor()
    # the model answers in words; without the gate the nudge would push it to run something
    prov = ScriptedProvider(["ChimeraX cannot email a structure.",
                             {"text": "", "calls": [("run_commands", {"commands": ['preset "overall look" "publication 1"']})]}])
    agent = Agent(prov, ex, config=AgentConfig(autonomy=AUTONOMY_AUTO))
    result = agent.run_turn("email this structure to my boss")
    assert agent.journal == []
    assert "cannot email" in result.reply


def test_save_figure_is_refused_for_a_request_chimerax_cannot_do():
    """The figure bundle runs its saves with origin='figure', which the save/export guard in
    execute_commands does not inspect, so save_figure walked straight past it."""
    ex = FakeExecutor()
    prov = ScriptedProvider([{"text": "", "calls": [("save_figure", {"name": "for_my_boss"})]},
                             "ChimeraX cannot email anything."])
    agent = Agent(prov, ex, config=AgentConfig(autonomy=AUTONOMY_AUTO),
                  callbacks=Callbacks(on_confirm=lambda c, r: c))
    agent.run_turn("email this structure to my boss")
    assert not any("save" in j["command"] for j in agent.journal)
    told = [r.content for m in agent.conversation if m.role == "tool" for r in m.tool_results_list()]
    assert any("not a step towards it" in t for t in told)


def test_the_run_commands_example_is_not_a_runnable_request():
    """With a real accession in the tool schema, small models opened alphafold:P07550 on turns
    that had nothing to do with it."""
    from core.tools import RUN_COMMANDS
    desc = json.dumps(RUN_COMMANDS.to_dict())
    assert "P07550" not in desc and "alphafold:" not in desc


def test_the_only_gotchas_are_one_recipe_each():
    from pathlib import Path
    text = Path("src/data/gotchas.md").read_text()
    only_lines = [l for l in text.splitlines() if l.lower().startswith('- "only') or l.lower().startswith('- "show only')]
    assert len(only_lines) >= 3                       # one bullet per phrasing, not three recipes in one
    cartoon = [l for l in only_lines if "cartoon" in l and "chain" not in l][0]
    assert "show #1 atoms" not in cartoon             # the trailing show re-displayed what was hidden


def test_commands_before_a_misused_tool_name_still_run():
    """The whole batch used to be discarded: 'open the AlphaFold model and annotate it' lost the
    open as well, and the model then said the structure was open when nothing was."""
    ex = FakeExecutor()
    agent = Agent(ScriptedProvider([]), ex)
    out = agent.execute_commands(["open 1ubq", "color red", "annotate #1 accession P1 kind domain", "view"])
    assert ex.ran == ["open 1ubq", "color red"]          # the prefix ran
    assert out["ok"] is False and out.get("tool_misuse") == "annotate"
    assert [r["command"] for r in out["results"]] == ["open 1ubq", "color red"]
    assert [j["command"] for j in agent.journal] == ["open 1ubq", "color red"]   # and was journalled


def test_a_misused_tool_name_first_still_runs_nothing():
    ex = FakeExecutor()
    agent = Agent(ScriptedProvider([]), ex)
    out = agent.execute_commands(["annotate #1 accession P1 kind domain", "color red"])
    assert ex.ran == [] and out["ok"] is False and out.get("tool_misuse") == "annotate"


def test_annotate_says_so_when_the_model_is_not_open():
    """With nothing open, annotate returned 'no matching annotations' and the model told the user
    the structure was open."""
    ex = FakeExecutor()
    ex.spec_atoms = lambda text: {"used": text, "atoms": 0}
    ex.protein_features = lambda acc, kinds=None: {"gene": "ADRB2", "features": []}
    agent = Agent(ScriptedProvider([]), ex)
    out = agent._annotate("#1", "P07550", "domain", "orange", False)
    assert "error" in out and "No model matches" in out["error"]


def test_compact_mode_stops_retrieving_docs_every_turn():
    """The static prompt caches; the per-turn docs block cannot, so it is most of the billed cost.
    In compact mode the model fetches documentation on demand with search_docs instead."""
    calls = []

    class Ex(FakeExecutor):
        def search_docs(self, q, k):
            calls.append(k)
            return [{"title": "color", "section": "usage", "text": "color spec color"}]

    ex = Ex()
    agent = Agent(ScriptedProvider(["done"]), ex, config=AgentConfig(docs_per_turn=6))
    agent.run_turn("color it blue")
    assert calls == [6]
    assert agent.go_compact()
    calls.clear()
    agent.run_turn("color it red")
    assert calls == []                                   # nothing retrieved automatically
    assert "search_docs" in {t.name for t in __import__("core.tools", fromlist=["x"]).tool_specs(compact=True)}


def test_resolve_protein_flags_a_family_name_as_ambiguous(monkeypatch):
    """'the adrenergic receptor' matches ADRA1A, ADRA2A, ADRB1 and ADRB2 equally well; picking the first
    silently opens a different protein from the one the user meant."""
    from core import uniprot

    def entry(acc, gene, name, syn=()):
        return {"primaryAccession": acc, "entryType": "UniProtKB reviewed (Swiss-Prot)",
                "genes": [{"geneName": {"value": gene}, "synonyms": [{"value": s} for s in syn]}],
                "proteinDescription": {"recommendedName": {"fullName": {"value": name}}},
                "organism": {"scientificName": "Homo sapiens"}, "sequence": {"length": 100}}

    results = [entry("P08913", "ADRA2A", "Adrenergic receptor", ["ADRA2A"]),
               entry("P07550", "ADRB2", "Adrenergic receptor", ["ADRB2"])]
    monkeypatch.setattr(uniprot, "request_json", lambda *a, **k: {"results": results})
    u = uniprot.UniProtClient(None)
    out = u.resolve("the adrenergic receptor")
    assert [c["gene"] for c in out["ambiguous"]] == ["ADRA2A", "ADRB2"]
    assert "ask_user" in out["note"]
    assert out["accession"] == "P08913"                       # still resolves, but says it is unsure
    assert "ambiguous" not in u.resolve("ADRB2")               # a synonym names one exactly
    assert "ambiguous" not in u.resolve("ADRA2A")


def test_map_numbering_reports_the_offset_and_the_missing_positions():
    ex = FakeExecutor()
    ex.spec_atoms = lambda text: {"used": text, "atoms": 10}
    ex.map_positions = lambda model, acc, positions: {
        "model": "#1", "accession": acc,
        "map": {p: {"chain": "A", "number": p - 1, "resname": "ALA"} for p in positions if p != 200},
        "unmapped": [p for p in positions if p == 200], "note": ""}
    agent = Agent(ScriptedProvider([]), ex)
    out = agent._map_numbering("#1", "P07550", [159, 160, 200])
    assert out["offset"] == -1 and "UniProt - 1" in out["summary"]
    assert out["missing"] == [200] and out["found"] == 2
    assert out["positions"][0]["spec"] == "#1/A:158"


def test_map_numbering_uses_the_chains_own_entry_when_no_protein_is_named():
    ex = FakeExecutor()
    ex.spec_atoms = lambda text: {"used": text, "atoms": 10}
    ex.chain_uniprot = lambda model: {"accessions": ["P0CG48"]}
    seen = {}
    ex.map_positions = lambda model, acc, positions: seen.update(acc=acc) or {
        "model": "#1", "map": {p: {"chain": "A", "number": p, "resname": "MET"} for p in positions}, "unmapped": []}
    agent = Agent(ScriptedProvider([]), ex)
    out = agent._map_numbering("#1", "", [1])
    assert seen["acc"] == "P0CG48" and out["offset"] == 0


def test_apply_figure_style_replays_only_the_styling(tmp_path):
    folder = tmp_path / "pocket"
    folder.mkdir()
    (folder / "pocket.cxc").write_text("\n".join([
        "# Figure bundle 'pocket'", "open 4hhb", "color #1 bychain", "show #1:HEM atoms", "style #1:HEM sphere",
        "lighting soft", "set bgColor white", "view name pocket", "view #1:HEM",
        'save "/tmp/x/pocket.png" width 2400', "close #1"]))
    ex = FakeExecutor()
    agent = Agent(ScriptedProvider([]), ex, callbacks=Callbacks(on_confirm=lambda c, r: c))
    agent.figures_dir = str(tmp_path)
    out = agent._apply_figure_style("pocket")
    assert "error" not in out
    assert ex.ran == ["color #1 bychain", "show #1:HEM atoms", "style #1:HEM sphere", "lighting soft", "set bgColor white", "view #1:HEM"]
    # aimed at another model: the specs are rewritten
    ex.ran.clear()
    agent._apply_figure_style("pocket", model="#2")
    assert ex.ran[0] == "color #2 bychain" and "save" not in " ".join(ex.ran)
    assert "No figure bundle" in agent._apply_figure_style("nope")["error"]


def test_narration_inside_a_tool_call_is_refused_not_run():
    """qwen3:4b answered the commands-only prompt by putting its reasoning into the commands list;
    the prose filter in recovery only covers text replies, so execute_commands needs its own."""
    ex = FakeExecutor()
    agent = Agent(ScriptedProvider([]), ex)
    out = agent.execute_commands(["color #1 red", "Wait, no. Let me re-read the user's message. The user's actual request is different."])
    assert ex.ran == ["color #1 red"] and out.get("prose") and "not a ChimeraX command" in out["error"]
    assert agent.execute_commands(['label #1:5 text "the active site"'])["ok"]   # English inside quotes is fine


def test_annotate_asks_when_the_structure_has_several_proteins_and_the_user_named_none():
    """4hhb is alpha- and beta-globin; 'show the disease variants on this structure' must not
    quietly pick one."""
    ex = FakeExecutor()
    ex.spec_atoms = lambda text: {"used": text, "atoms": 10}
    ex.chain_uniprot = lambda model: {"accessions": ["P69905", "P68871"]}
    ex.protein_features = lambda acc, kinds=None: {"gene": "HBA1", "features": []}
    agent = Agent(ScriptedProvider([]), ex)
    agent._user_text_upper = " SHOW THE DISEASE VARIANTS ON THIS STRUCTURE"
    out = agent._annotate("#1", "P69905", "clinvar", "orange", False)
    assert "ambiguous" in out and "ask_user" in out["error"]
    agent._user_text_upper = " SHOW THE HBA1 VARIANTS"            # the user named it: no question
    out = agent._annotate("#1", "P69905", "clinvar", "orange", False)
    assert "ambiguous" not in out


def test_fetch_annotation_loads_the_dataset_as_a_table_and_colors_by_it(monkeypatch):
    from core import annot_sources
    ds = {"name": "alphamissense_p07550", "columns": ["position", "wt", "am_mean", "am_max", "am_class"],
          "rows": [["1", "M", "0.2", "0.4", "likely benign"], ["2", "R", "0.8", "0.9", "likely pathogenic"]],
          "delimiter": ",", "accession": "P07550", "source": "AlphaMissense via AlphaFold DB", "source_url": "u",
          "palette": "alphamissense", "n_positions": 2, "value_range": [0.2, 0.8]}
    monkeypatch.setattr(annot_sources, "alphamissense", lambda acc, cache, **k: ds)
    ex = FakeExecutor()
    ex.spec_atoms = lambda text: {"used": text, "atoms": 10}
    seen = {}
    agent = Agent(ScriptedProvider([]), ex)
    agent._table_overlay = lambda *a: seen.update(args=a) or {"summary": "2 residues colored", "placed": 2}
    out = agent._fetch_annotation("alphamissense", "P07550", "#1", "")
    assert "alphamissense_p07550" in agent.tables and agent.tables["alphamissense_p07550"]["accession"] == "P07550"
    assert seen["args"][:2] == ("alphamissense_p07550", "am_mean") and seen["args"][3] == "alphamissense"
    assert out["source"].startswith("AlphaMissense") and out["placed"] == 2
    monkeypatch.setattr(annot_sources, "conservation", lambda pdb, chain, cache, **k: {"error": "ConSurf-DB has no entry for XXXX."})
    assert "no entry" in agent._fetch_annotation("conservation", "XXXX", "#1", "A")["error"]


def test_the_model_may_not_fetch_and_run_a_script_from_the_internet():
    """Asked to color by residue type, one model reached for a recipe script on GitHub."""
    ex = FakeExecutor()
    agent = Agent(ScriptedProvider([]), ex, callbacks=Callbacks(on_confirm=lambda c, r: c))
    out = agent.execute_commands(["open https://example.org/evil/recipe.cxc"])
    assert ex.ran == [] and out.get("remote_script") and "search_docs" in out["error"]
    # RBVI's own recipes define commands ChimeraX lacks: allowed, and the safety gate still asks first
    asked = []
    agent.cb.on_confirm = lambda c, r: asked.append(list(c)) or c
    assert agent.execute_commands(["open https://raw.githubusercontent.com/RBVI/chimerax-recipes/master/convexhull/convexhull.py"])["ok"]
    assert asked and "convexhull.py" in asked[0][0]
    assert agent.execute_commands(["open https://files.rcsb.org/download/1ubq.pdb"])["ok"]     # data files are fine
    assert agent.execute_commands(["open 1ubq"])["ok"]


def test_bookmarking_is_not_a_step_towards_emailing_either():
    ex = FakeExecutor()
    agent = Agent(ScriptedProvider([{"text": "", "calls": [("run_commands", {"commands": ["view name for_boss"]})]},
                                    "ChimeraX cannot email."]), ex, callbacks=Callbacks(on_confirm=lambda c, r: c))
    agent.run_turn("email the view to my boss")
    assert agent.journal == []


def test_a_purely_impossible_request_runs_no_command_at_all():
    ex = FakeExecutor()
    agent = Agent(ScriptedProvider([{"text": "", "calls": [("run_commands", {"commands": ["surface zone #1 near :C dist 8"]})]},
                                    "ChimeraX cannot text anyone."]), ex, callbacks=Callbacks(on_confirm=lambda c, r: c))
    agent.run_turn("dock the ligand and text me the result")
    assert ex.ran == [] and agent.journal == []
    # but a request that also asks for a real action keeps that action
    agent = Agent(ScriptedProvider([{"text": "", "calls": [("run_commands", {"commands": ["color #1 red"]})]}, "done"]),
                  ex, callbacks=Callbacks(on_confirm=lambda c, r: c))
    agent.run_turn("color it red and email it to my boss")
    assert ex.ran == ["color #1 red"]


def test_no_annotate_nudge_when_features_were_fetched_and_colored_by_hand():
    """Asked to color the transmembrane helices, the model fetched the features and colored them;
    the nudge then sent it off to annotate variants nobody wanted."""
    ex = FakeExecutor()
    ex.protein_features = lambda acc, kinds=None: {"gene": "ADRB2", "features": [{"type": "Transmembrane", "start": 30, "end": 56}]}
    prov = ScriptedProvider([{"text": "", "calls": [("protein_features", {"accession": "P07550", "kinds": ["Transmembrane"]})]},
                             {"text": "", "calls": [("run_commands", {"commands": ["color #1 white", "color #1:30-56 orange"]})]},
                             "The transmembrane helices are orange."])
    agent = Agent(prov, ex, callbacks=Callbacks(on_confirm=lambda c, r: c))
    agent.run_turn("color the transmembrane helices orange and the rest white")
    assert not any("annotate" in (m.text() or "") for m in agent.conversation if m.role == "user")
    assert [c.name for m in agent.conversation if m.role == "assistant" for c in m.tool_calls()] == ["protein_features", "run_commands"]


def test_distances_are_restyled_to_contrast_with_a_light_background():
    ex = FakeExecutor()
    ex.get_state = lambda: {"models": [], "background": "rgb(255,255,255)"}
    agent = Agent(ScriptedProvider([]), ex)
    agent.execute_commands(["distance #1:87@NE2 #1:142@FE", "view"])
    assert ex.ran == ["distance #1:87@NE2 #1:142@FE", "distance style color #1f3a5f", "view"]
    ex.ran.clear(); ex.get_state = lambda: {"models": [], "background": "rgb(0,0,0)"}
    agent.execute_commands(["distance delete", "distance #1:1@CA #1:2@CA"])
    assert ex.ran == ["distance delete", "distance #1:1@CA #1:2@CA", "distance style color gold"]
