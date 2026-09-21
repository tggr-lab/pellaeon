"""Unit tests for the pure-Python half of the contact comparison (no ChimeraX)."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from core.contacts import (
    CONTACT, GAINED_COLOR, HBOND_CAPABLE, LOST_COLOR, SALT_BRIDGE,
    atom_part, classify_contact, compare_contacts, contact_commands, model_restriction,
    normalize_spec, strongest_kind, translate_restriction,
)


def c(a: str, b: str, kind: str = CONTACT, dist: float = 3.5,
      a_name: str = "ALA", b_name: str = "LEU", a_atom: str = "CB", b_atom: str = "CD1") -> Dict[str, Any]:
    return {"a": a, "a_name": a_name, "a_atom": a_atom, "b": b, "b_name": b_name, "b_atom": b_atom,
            "min_dist": dist, "kind": kind}


IDENTITY = {"/A:10": "/A:10", "/A:20": "/A:20", "/A:30": "/A:30", "/A:40": "/A:40"}


# ------------------------------------------------------------------ specs


def test_normalize_spec_drops_model_and_slash():
    assert normalize_spec("#2/A:45") == "A:45"
    assert normalize_spec("/A:45") == "A:45"
    assert normalize_spec("A:45") == "A:45"


def test_strongest_kind_prefers_the_most_specific():
    assert strongest_kind([CONTACT, HBOND_CAPABLE, SALT_BRIDGE]) == SALT_BRIDGE
    assert strongest_kind([CONTACT, HBOND_CAPABLE]) == HBOND_CAPABLE
    assert strongest_kind([]) == CONTACT


# ------------------------------------------------------------------ lost / gained / kept


def test_lost_gained_and_kept():
    ref = [c("/A:10", "/A:20"), c("/A:10", "/A:30"), c("/A:20", "/A:40")]
    other = [c("/A:10", "/A:20", dist=3.9), c("/A:30", "/A:40")]
    r = compare_contacts(ref, other, IDENTITY)
    assert [(e["a"], e["b"]) for e in r["lost"]] == [("/A:10", "/A:30"), ("/A:20", "/A:40")]
    assert [(e["a"], e["b"]) for e in r["gained"]] == [("/A:30", "/A:40")]
    assert [(e["a"], e["b"]) for e in r["kept"]] == [("/A:10", "/A:20")]
    assert r["kept"][0]["delta"] == pytest.approx(0.4)
    assert r["counts"]["lost"] == 2 and r["counts"]["gained"] == 1


def test_contact_direction_does_not_matter():
    r = compare_contacts([c("/A:10", "/A:20")], [c("/A:20", "/A:10")], IDENTITY)
    assert r["counts"] == dict(r["counts"], lost=0, gained=0, kept=1)


def test_renumbered_structure_compares_through_the_pairing():
    """The other model is numbered +1000; comparing by number would report everything lost."""
    ref = [c("/A:10", "/A:20"), c("/A:10", "/A:30")]
    other = [c("/B:1010", "/B:1020")]
    pairing = {"/A:10": "/B:1010", "/A:20": "/B:1020", "/A:30": "/B:1030"}
    r = compare_contacts(ref, other, pairing)
    assert r["counts"]["kept"] == 1
    assert r["counts"]["lost"] == 1
    assert r["lost"][0]["mapped_a"] == "B:1010" and r["lost"][0]["mapped_b"] == "B:1030"
    # and the same data compared with an identity pairing would be garbage:
    assert compare_contacts(ref, other, IDENTITY)["counts"]["kept"] == 0


def test_unaligned_residues_are_not_reported_as_lost():
    ref = [c("/A:10", "/A:20"), c("/A:10", "/L:1", b_name="ATP")]
    other = [c("/A:10", "/A:20")]
    r = compare_contacts(ref, other, IDENTITY)
    assert r["counts"]["lost"] == 0
    assert r["counts"]["unmapped_reference"] == 1
    assert "outside the alignment" in r["note"]


def test_specs_with_model_prefixes_still_match():
    r = compare_contacts([c("#1/A:10", "#1/A:20")], [c("#2/A:10", "#2/A:20")],
                         {"#1/A:10": "#2/A:10", "#1/A:20": "#2/A:20"})
    assert r["counts"]["kept"] == 1


def test_by_kind_counts_each_bucket():
    ref = [c("/A:10", "/A:20", SALT_BRIDGE), c("/A:10", "/A:30", HBOND_CAPABLE), c("/A:20", "/A:40")]
    other = [c("/A:10", "/A:30", HBOND_CAPABLE), c("/A:30", "/A:40", SALT_BRIDGE)]
    r = compare_contacts(ref, other, IDENTITY)
    assert r["by_kind"][SALT_BRIDGE] == {"lost": 1, "gained": 1, "kept": 0}
    assert r["by_kind"][HBOND_CAPABLE] == {"lost": 0, "gained": 0, "kept": 1}
    assert r["by_kind"][CONTACT] == {"lost": 1, "gained": 0, "kept": 0}


# ------------------------------------------------------------------ summary


def test_summary_names_residues_and_salt_bridges():
    ref = [c("/A:10", "/A:20", SALT_BRIDGE, a_name="ARG", b_name="ASP", a_atom="NH1", b_atom="OD1"),
           c("/A:10", "/A:30")]
    other = [c("/A:30", "/A:40", SALT_BRIDGE, a_name="LYS", b_name="GLU")]
    s = compare_contacts(ref, other, IDENTITY)["summary"]
    assert "2 contacts lost and 1 gained" in s
    assert "ARG 10-ASP 20" in s and "LYS 30-GLU 40" in s
    assert s.endswith(".")


def test_summary_when_nothing_changed():
    r = compare_contacts([c("/A:10", "/A:20")], [c("/A:10", "/A:20")], IDENTITY)
    assert "No contact changes" in r["summary"]


def test_summary_shows_chain_only_when_several_chains():
    one = compare_contacts([c("/A:10", "/A:20")], [], IDENTITY)["summary"]
    assert "ALA 10" in one and "ALA A 10" not in one
    two = compare_contacts([c("/A:10", "/B:20")], [], {"/A:10": "/A:10", "/B:20": "/B:20"})["summary"]
    assert "ALA A 10" in two


# ------------------------------------------------------------------ kind classification


def test_classify_salt_bridge_both_directions():
    assert classify_contact("ARG", "NH1", "ASP", "OD2", 3.2) == SALT_BRIDGE
    assert classify_contact("GLU", "OE1", "LYS", "NZ", 3.8) == SALT_BRIDGE


def test_salt_bridge_needs_the_charged_atoms():
    assert classify_contact("ARG", "CB", "ASP", "OD2", 3.2) != SALT_BRIDGE
    assert classify_contact("ARG", "NH1", "ASP", "OD2", 5.0) == CONTACT


def test_hbond_capable_and_plain_contact():
    assert classify_contact("SER", "OG", "THR", "OG1", 2.8) == HBOND_CAPABLE
    assert classify_contact("SER", "OG", "LEU", "CD1", 3.4) == CONTACT
    assert classify_contact("SER", "OG", "THR", "OG1", 3.9) == CONTACT  # too far to hydrogen bond


def test_ligand_phosphate_oxygen_is_anionic():
    assert classify_contact("ARG", "NH2", "AP5", "O1A", 3.0, b_standard=False) == SALT_BRIDGE
    # a standard residue's O1A-like name is not treated as a charged ligand oxygen
    assert classify_contact("ARG", "NH2", "SER", "OG", 3.0) == HBOND_CAPABLE


def test_c_terminal_oxt_is_anionic():
    assert classify_contact("LYS", "NZ", "GLY", "OXT", 3.0) == SALT_BRIDGE


# ------------------------------------------------------------------ visualisation plan


def commands_for_simple_case() -> List[str]:
    ref = [c("/A:10", "/A:20", SALT_BRIDGE, a_name="ARG", b_name="ASP", a_atom="NH1", b_atom="OD1")]
    other = [c("/A:30", "/A:40", a_atom="CB", b_atom="CG")]
    r = compare_contacts(ref, other, IDENTITY)
    return contact_commands(r, "#1/A", "#2/A")


def test_commands_draw_lost_on_reference_and_gained_on_other():
    cmds = commands_for_simple_case()
    assert "distance #1/A:10@NH1 #1/A:20@OD1 color %s dashes 6 radius 0.08" % LOST_COLOR in cmds
    assert "distance #2/A:30@CB #2/A:40@CG color %s dashes 6 radius 0.08" % GAINED_COLOR in cmds


def test_commands_keep_both_models_visible_and_add_a_key():
    cmds = commands_for_simple_case()
    assert "show #1 models" in cmds and "show #2 models" in cmds
    assert not any(cmd.startswith("hide #1 models") for cmd in cmds)
    assert any(cmd.startswith("distance delete") for cmd in cmds)
    assert any(cmd.startswith("key ") and ":lost" in cmd and ":gained" in cmd for cmd in cmds)
    assert all(" " not in cmd.split("key ")[1].split()[0] for cmd in cmds if cmd.startswith("key "))
    assert any(cmd.startswith("2dlabels ") and "#1" in cmd and "#2" in cmd for cmd in cmds)


def test_commands_group_residues_per_chain():
    ref = [c("/A:10", "/A:20"), c("/A:10", "/B:30")]
    r = compare_contacts(ref, [], {"/A:10": "/A:10", "/A:20": "/A:20", "/B:30": "/B:30"})
    cmds = contact_commands(r, "#1", "#2")
    assert "show #1/A:10,20 atoms" in cmds
    assert "show #1/B:30 atoms" in cmds


def test_commands_cap_how_much_is_drawn():
    ref = [c("/A:%d" % i, "/A:%d" % (i + 100)) for i in range(1, 40)]
    pairing = {}
    for i in range(1, 40):
        pairing["/A:%d" % i] = "/A:%d" % i
        pairing["/A:%d" % (i + 100)] = "/A:%d" % (i + 100)
    r = compare_contacts(ref, [], pairing)
    cmds = contact_commands(r, "#1", "#2", max_drawn=5)
    assert sum(1 for cmd in cmds if cmd.startswith("distance #")) == 5
    assert "label delete pseudobonds" in cmds                       # dashes carry no distance numbers
    assert not any("showing" in cmd for cmd in cmds)               # the counts live in the result card, not the title
    assert any(cmd.startswith('2dlabels create pellaeon_title text "Contacts lost (red, #1) and gained (green, #2)"') for cmd in cmds)


def test_commands_without_atom_names_still_show_residues():
    entry = c("/A:10", "/A:20")
    entry.pop("a_atom"), entry.pop("b_atom")
    r = compare_contacts([entry], [], IDENTITY)
    cmds = contact_commands(r, "#1", "#2")
    assert not any(cmd.startswith("distance #") for cmd in cmds)
    assert "show #1/A:10,20 atoms" in cmds


# ------------------------------------------------------------------ interaction type changes


def test_a_pair_that_changes_type_is_a_change_not_unchanged():
    """The residues still touch, but the salt bridge is gone: that is the answer to the question."""
    ref = [c("/A:10", "/A:20", SALT_BRIDGE, a_name="ARG", b_name="ASP", a_atom="NH1", b_atom="OD1")]
    other = [c("/A:10", "/A:20", CONTACT, a_name="ARG", b_name="ASP")]
    r = compare_contacts(ref, other, IDENTITY)
    assert r["counts"]["kept"] == 1 and r["counts"]["lost"] == 0
    assert r["counts"]["kind_changed"] == 1
    assert r["kept"][0]["kind_changed"] is True
    assert "No contact changes" not in r["summary"]
    assert "change interaction type" in r["summary"]
    assert "ARG 10-ASP 20 salt bridge -> contact" in r["summary"]


def test_a_pair_that_keeps_its_type_is_not_counted_as_changed():
    r = compare_contacts([c("/A:10", "/A:20", HBOND_CAPABLE)], [c("/A:10", "/A:20", HBOND_CAPABLE)], IDENTITY)
    assert r["counts"]["kind_changed"] == 0
    assert "No contact changes" in r["summary"]


# ------------------------------------------------------------------ what could not be judged


def test_summary_says_how_much_could_not_be_judged():
    ref = [c("/A:10", "/A:20"), c("/A:10", "/L:1", b_name="ATP")]
    other = [c("/A:10", "/A:20")]
    s = compare_contacts(ref, other, IDENTITY)["summary"]
    assert "1 reference contact could not be judged" in s


def test_summary_when_nothing_could_be_compared():
    """The reviewer's case: one reference contact, its ligand endpoint unpaired, nothing else.
    Saying 'all 0 contacts are present in both conformations' would read as 'they agree'."""
    r = compare_contacts([c("/A:10", "/L:1", b_name="ATP")], [], IDENTITY)
    assert r["counts"]["lost"] == 0 and r["counts"]["unmapped_reference"] == 1
    assert r["summary"].startswith("No contacts could be compared")
    assert "not in the alignment" in r["summary"]


def test_summary_when_neither_side_has_anything():
    assert compare_contacts([], [], IDENTITY)["summary"].startswith("No contacts could be compared")


# ------------------------------------------------------------------ endpoint metadata


def test_mapped_endpoints_follow_the_contacts_own_order():
    """a=A:20, b=A:10 with a pairing that reverses them: mapped_a must still describe a."""
    ref = [c("/A:20", "/A:10", a_name="ARG", b_name="ASP")]
    pairing = {"/A:20": "/B:10", "/A:10": "/B:20"}
    r = compare_contacts(ref, [], pairing)
    assert (r["lost"][0]["mapped_a"], r["lost"][0]["mapped_b"]) == ("B:10", "B:20")


def test_gained_endpoint_metadata_follows_the_contacts_own_order():
    other = [c("/B:20", "/B:10")]
    pairing = {"/A:10": "/B:20", "/A:20": "/B:10"}
    r = compare_contacts([], other, pairing)
    assert (r["gained"][0]["mapped_a"], r["gained"][0]["mapped_b"]) == ("A:10", "A:20")


# ------------------------------------------------------------------ restriction translation


def test_model_restriction_qualifies_residue_specs():
    assert model_restriction(":AP5", "1") == "#1:AP5"
    assert model_restriction("/A:87", "1") == "#1/A:87"
    assert model_restriction("#1/A:87", "2") == "#2/A:87"
    assert model_restriction("A:87", "1") == "#1/A:87"
    assert model_restriction("/A:87@CA", "2") == "#2/A:87@CA"


def test_model_restriction_intersects_selector_words():
    """'#2/ligand' would ask for a chain literally named 'ligand' and select nothing."""
    for word in ("ligand", "protein", "solvent"):
        assert model_restriction(word, "2") == "#2 & %s" % word
    assert model_restriction("AP5", "1") == "#1:AP5"      # short token: a residue name
    assert model_restriction("ligand | ions", "1") == "#1 & (ligand | ions)"


def test_model_restriction_does_not_add_the_compared_chain():
    """chain='A' with restrict='/B:87' means 'what does B87 touch in chain A': the restriction
    keeps its own chain, and the chain limit applies to the partner side (see the docstring)."""
    assert model_restriction("/B:87", "1") == "#1/B:87"


def test_translated_restriction_goes_through_the_pairing():
    """The reviewer's failing input: A:87 -> A:187. Reusing '87' would search the wrong residue."""
    pairing = {"/A:87": "/A:187", "/A:99": "/A:199"}
    assert translate_restriction(["/A:87"], pairing, "2") == "#2/A:187"
    assert translate_restriction(["/A:87", "/A:99"], pairing, "2") == "#2/A:187,199"
    assert translate_restriction(["/A:87"], pairing, "2", atom_part("/A:87@CA")) == "#2/A:187@CA"


def test_translated_restriction_is_empty_when_nothing_is_paired():
    """An apo form has no ligand: the caller must say 'nothing there', not 'everything lost'."""
    assert translate_restriction(["/A:215"], {"/A:87": "/A:187"}, "2") == ""


def test_translated_restriction_spans_chains():
    pairing = {"/A:10": "/C:10", "/B:20": "/D:20"}
    assert translate_restriction(["/A:10", "/B:20"], pairing, "2") == "#2/C:10 | #2/D:20"


# ------------------------------------------------------------------ collector failures (agent level)


class StubExecutor:
    """The smallest executor `_compare_contacts` needs: two models, one alignment, and a
    collector whose answer for the compared model the test chooses."""

    def __init__(self, other_result):
        self.other_result = other_result
        self.asked = []

    def run_commands(self, commands):
        return [{"command": c, "ok": True, "info": ["ok"]} for c in commands]

    def get_state(self):
        return {"models": [], "selection": {}}

    def prepare_compare(self, reference, other, chain=None):
        return {"ref_id": reference.lstrip("#"), "other_id": other.lstrip("#"), "chain": chain or "A",
                "ref_spec": reference + "/A", "other_spec": other + "/A"}

    def residue_pairing(self, prep):
        return {"pairing": {"/A:10": "/A:110", "/A:20": "/A:120"}, "basis": "matchmaker alignment",
                "paired_residues": 2}

    def residue_contacts(self, model_spec, cutoff=4.0, restrict=None):
        self.asked.append((model_spec, restrict))
        if model_spec.startswith("#1"):
            return {"model": "#1", "contacts": [c("/A:10", "/A:20")], "count": 1,
                    "restrict_residues": ["/A:10"], "restrict": restrict or ""}
        return dict(self.other_result)


def _agent(executor):
    from core.agent import Agent, AgentConfig
    from core.safety import AUTONOMY_AUTO

    class Provider:
        name = "stub"
        supports_vision = False

        def stream(self, system, messages, tools, on_delta=None, cancel=None):
            raise AssertionError("the provider is not used by this test")

    return Agent(Provider(), executor, config=AgentConfig(autonomy=AUTONOMY_AUTO))


def test_collector_error_on_the_other_model_is_an_error_not_an_empty_comparison():
    """The reviewer's failing input: the other collector fails, so nothing was measured there.
    Comparing against [] would report the one mapped reference contact as lost."""
    ex = StubExecutor({"error": "Models changed during the comparison."})
    out = _agent(ex)._compare_contacts("#1", "#2", "A", None, 4.0)
    assert "error" in out and "Models changed" in out["error"]
    assert "lost" not in out


def test_empty_other_selection_is_reported_as_nothing_there():
    ex = StubExecutor({"model": "#2", "contacts": [], "count": 0, "empty_selection": True,
                       "restrict_residues": [], "reason": "The restrict spec '#2/A:110' matches nothing in #2/A."})
    out = _agent(ex)._compare_contacts("#1", "#2", "A", "/A:10", 4.0)
    assert "error" not in out
    assert "matches nothing" in out["compared_side"]
    assert "counts as lost" not in out["compared_side"]


def test_the_other_models_restriction_is_translated_through_the_pairing():
    ex = StubExecutor({"model": "#2", "contacts": [], "count": 0, "restrict_residues": []})
    out = _agent(ex)._compare_contacts("#1", "#2", "A", "/A:10", 4.0)
    assert ex.asked[0] == ("#1/A", "#1/A:10")
    assert ex.asked[1] == ("#2/A", "#2/A:110")     # NOT '#2/A:10'
    assert out["restrict"] == "#1/A:10" and out["compared_restrict"] == "#2/A:110"


def test_a_restriction_no_residue_of_which_is_paired_never_calls_the_other_collector():
    ex = StubExecutor({"model": "#2", "contacts": [], "count": 0, "restrict_residues": []})
    ex.residue_contacts_ref = None

    def only_reference(model_spec, cutoff=4.0, restrict=None):
        ex.asked.append((model_spec, restrict))
        assert model_spec.startswith("#1"), "the ligand has no counterpart: nothing to ask for"
        return {"model": "#1", "contacts": [c("/A:215", "/A:10", a_name="AP5")], "count": 1,
                "restrict_residues": ["/A:215"], "restrict": restrict or ""}

    ex.residue_contacts = only_reference
    out = _agent(ex)._compare_contacts("#1", "#2", "A", ":AP5", 4.0)
    assert out["restrict"] == "#1:AP5"
    assert "outside the alignment" in out["compared_side"]
    assert out["counts"]["lost"] == 0 and out["counts"]["unmapped_reference"] == 1
    assert out["summary"].startswith("No contacts could be compared")
