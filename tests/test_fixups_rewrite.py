"""Deterministic rewrites of the model's commands (fixups.rewrite) and the residue-range summary."""
from core.fixups import residue_ranges_summary, rewrite, suggest


def r(cmd, request=""):
    return rewrite(cmd, request)[0]


def test_attribute_tests_get_their_double_prefix():
    # all four forms were rejected by ChimeraX ("Expected a keyword") in blind and dev runs
    assert r("select #1 & bfactor < 50") == "select #1 & @@bfactor<50"
    assert r("select #1:bfactor<50") == "select #1@@bfactor<50"
    assert r("color #1:bfactor=0 gray") == "color #1@@bfactor=0 gray"
    assert r("hide #1@bfactor<50 target ac") == "hide #1@@bfactor<50 target ac"
    assert r("select #1:area > 50") == "select #1::area>50"
    assert r("select #1 & (sasa > 0)") == "select #1 & (::area>0)"
    assert r("show #1:seq_conservation>1.8 atoms") == "show #1::seq_conservation>1.8 atoms"   # any residue attribute
    assert r("select :HEM") == "select :HEM" and r("color #1:10-20 red") == "color #1:10-20 red"


def test_correct_attribute_syntax_and_other_commands_are_untouched():
    for c in ("hide @@bfactor<50 target ac", "select ::area>40", "select #1::area>40 & protein",
              "color bfactor #1 palette bluered", 'label #1:10 text "bfactor>3"', "open 1abc"):
        assert rewrite(c)[0] == c and rewrite(c)[1] == []


def test_comma_separated_specs_become_space_separated():
    assert r("select /A:25,/B:25") == "select /A:25 /B:25"
    assert r("label #1/A:25, #1/B:25") == "label #1/A:25 #1/B:25"
    assert r("select #1:10,20,30") == "select #1:10,20,30"          # a residue list is fine
    assert r('2dlabels text "a, /b"') == '2dlabels text "a, /b"'


def test_info_on_a_residue_lists_the_residue():
    assert r("info #1:48") == "info residues #1:48"
    assert r("info #1/A:48@NZ") == "info atoms #1/A:48@NZ"
    assert r("info models") == "info models" and r("info #1") == "info #1"


def test_matchmaker_alignment_window_only_when_asked():
    assert r("matchmaker #2 to #1 show true", "superpose the second onto the first") == "matchmaker #2 to #1"
    assert r("mm #2 to #1 showAlignment true", "superpose them") == "mm #2 to #1"
    assert r("matchmaker #2 to #1 show true", "align them and show me the alignment") == "matchmaker #2 to #1 show true"


def test_hiding_the_rest_hides_its_cartoon_too():
    assert r("hide ~sel", "zoom on the drug and hide everything else") == "hide ~sel target ac"
    assert r("hide #1 & ~:STI", "keep only the ligand") == "hide #1 & ~:STI target ac"
    assert r("hide ~sel", "hide everything else, leave the ribbon") == "hide ~sel"   # cartoon named
    assert r("hide ~sel target a", "hide the rest") == "hide ~sel target a"                   # target given
    assert r("hide ~sel", "hide the other atoms") == "hide ~sel"                              # no 'rest/only'


def test_residue_ranges_summary():
    text = "\n".join("residue id /A:%d name X index 0" % n for n in (23, 24, 25, 26, 27, 56, 57, 58))
    assert residue_ranges_summary(text) == "8 residues: /A:23-27,56-58"
    assert residue_ranges_summary("residue id /A:48 name LYS index 47") == ""


def test_new_suggestions():
    assert "compare_structures" in suggest("color #2 by displacement")
    assert "key true" in suggest("key true", "Fetching url https://www.colourlovers.com/api/palettes?keywords=true failed")
    assert "region" in suggest("volume #1 plane z,50")
