"""Batch corrections seen in the gallery and variants sessions: submodel compaction, unasked atoms, label flood, rewrites."""
from core.fixups import rewrite
from core.agent import Agent, AgentConfig


class Exec:
    def __init__(self, residues=0):
        self.residues = residues

    def spec_atoms(self, text):
        return {"used": text, "atoms": self.residues * 8, "residues": self.residues, "shown": 0}


def agent(residues=0):
    a = Agent.__new__(Agent)
    a.executor = Exec(residues)
    return a


def test_rewrites_from_gallery_session():
    assert rewrite("color #2 bychain palette #7986cb:#4db6ac target s")[0] == "rainbow #2 chains palette #7986cb:#4db6ac target s"
    assert rewrite("hide #2.1-#2.60 target acs")[0] == "hide #2.1-60 target acs"
    assert rewrite("surface #2.2-2.60")[0] == "surface #2.2-60"
    assert rewrite("show #1 protein atoms")[0] == "show #1 & protein atoms"
    assert rewrite("show #3/DNA atoms")[0] == "show #3 & nucleic atoms"
    assert rewrite("color #1 red")[1] == []


def test_submodel_commands_are_merged():
    cmds = ["surface #2.%d resolution 6" % i for i in range(1, 61)] + ["lighting soft"]
    out, notes, tag = agent()._compact_submodels(cmds, "")
    assert out == ["surface #2 resolution 6", "lighting soft"] and "60 commands" in notes[0]
    out, notes, _ = agent()._compact_submodels(["surface #2.1", "surface #2.2", "color red"], "")
    assert out == ["surface #2.1", "surface #2.2", "color red"] and not notes


def test_unasked_atoms_dropped_for_cartoon_requests():
    cmds = ["hide #1 target acs", "cartoon #1/A", "show #1/A atoms"]
    out, notes, _ = agent()._unasked_atoms(cmds, "open 3vw7 and keep only the receptor chain A, as a cartoon")
    assert out == ["hide #1 target acs", "cartoon #1/A"] and notes
    out, _, _ = agent()._unasked_atoms(cmds, "show chain A atoms as sticks")
    assert out == cmds
    out, _, _ = agent()._unasked_atoms(cmds, "color chain A red")
    assert out == cmds


def test_label_flood_is_refused_unless_asked_for_all():
    out, notes, _ = agent(649)._label_cap(["label sel size 16"], "label the residues that touch the DNA")
    assert out == [] and "649 residues" in notes[0]
    out, notes, _ = agent(12)._label_cap(["label sel size 16"], "label them")
    assert out == ["label sel size 16"] and not notes
    out, _, _ = agent(649)._label_cap(["label sel"], "label all of the residues")
    assert out == ["label sel"]
    out, _, _ = agent(649)._label_cap(["label delete", "~label #1"], "clear labels")
    assert out == ["label delete", "~label #1"]


def test_matchmaker_to_a_non_structure_model_is_redirected():
    a = agent()
    a.executor.state = {"models": [
        {"id": "#1", "name": "AlphaFold P04637", "num_atoms": 3000},
        {"id": "#2", "name": "distances", "note": "not a structure: a key, label or surface model; do not compare or annotate it"},
        {"id": "#3", "name": "1tsr", "num_atoms": 9000}]}
    a.executor.get_state = lambda: a.executor.state
    out, notes, _ = a._structure_targets(["matchmaker #3/A to #2/A", "color #3 red"], "align it")
    assert out == ["matchmaker #3/A to #1/A", "color #3 red"] and "#2 is not a structure" in notes[0]
    a.executor.state["models"].append({"id": "#4", "name": "4hhb", "num_atoms": 4000})
    out, notes, _ = a._structure_targets(["matchmaker #3/A to #2/A"], "")
    assert out == ["matchmaker #3/A to #2/A"] and "name the structure" in notes[0]
