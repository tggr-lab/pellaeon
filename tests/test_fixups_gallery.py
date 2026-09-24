"""Fixups for the mistakes seen while making gallery figures: invented color schemes, build-as-symmetry, rotate."""
from core.fixups import suggest


def test_bychain_with_palette_word_is_redirected_to_rainbow():
    assert "rainbow" in suggest("color #2 bychain pastel", "Expected a collection")
    assert "rainbow" in suggest("color #2 bychain pastel target s", "Expected a collection")
    assert suggest("color #2 bychain target s", "") is None


def test_invented_color_scheme():
    assert "rainbow" in suggest("color smooth #2", "Unknown")


def test_build_for_helix_points_to_sym():
    assert "sym #1 h," in suggest('build start peptide "helical rod" 150 rise 1.408 angle 22.03', "Missing")


def test_sym_syntax_on_error():
    assert "assembly 1 copies true" in suggest("sym #1 helix 1.4 22", "Expected")


def test_rotate_is_turn():
    assert "turn y 90" in suggest("rotate 90", "Unknown command")


def test_move_model_points_to_axis_syntax_and_not_moving():
    t = suggest("move #1 up 10", "Axis argument requires 2 atoms")
    assert "move z 10 models #1" in t and "hide" in t
    assert "move z 10 models #1" in suggest("translate #1 +0,0,10", "Unknown command")
