from core.fixups import suggest


def test_suggestions():
    assert "ala,val" in suggest("color #1 byaa")
    assert "chain" in suggest("distance #1:5@CA #1:25@CA", "You provided four atoms")
    assert suggest("distance #1/A:5@CA #1/A:25@CA", "") is None
    assert "bgColor" in suggest("set bg_color white")
    assert suggest("color #1 red") is None
    assert "solvent" in suggest("hide water")
