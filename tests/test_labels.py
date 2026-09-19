from core.labels import style_label_command, label_spec


def test_plain_label_gets_readable_defaults():
    cmd, note = style_label_command("label #1/A:87 residues")
    assert cmd == "label #1/A:87 residues size 16 height fixed onTop true color black bgColor #ffffffd9" and note == ""
    assert style_label_command("label sel")[0].startswith("label sel size 16 height fixed")


def test_styled_or_subcommands_are_left_alone():
    for c in ("label #1:5 height 2 color red", "label delete", "label #1 text \"x\" size 30", "label listfonts", "~label #1", "color #1 red", "label #1:5 offset 1,1,1"):
        assert style_label_command(c) == (c, "")


def test_dense_label_sets_use_short_text():
    cmd, note = style_label_command("label #1/A:1-100", n_targets=100)
    assert 'text "{0.one_letter_code}{0.number}" size 12' in cmd and "one-letter" in note
    # explicit text or atom labels keep their text
    cmd2, _ = style_label_command('label #1/A:1-100 text "{0.name}"', n_targets=100)
    assert "one_letter" not in cmd2


def test_label_spec_extraction():
    assert label_spec("label #1/A:87 residues size 10") == "#1/A:87"
    assert label_spec("label #1:HEM | #1:87 atoms") == "#1:HEM | #1:87"
    assert label_spec("label delete") is None and label_spec("show #1") is None


def test_pack_labels_moves_or_drops_overlaps():
    from core.labels import pack_labels
    boxes = [("a", 0, 0, 60, 16), ("b", 10, 4, 60, 16), ("c", 200, 200, 60, 16)]
    r = pack_labels(boxes)
    assert set(r["kept"]) >= {"a", "c"} and not r["dropped"]
    assert "b" in r["moved"] and r["moved"]["b"] != (0, 0)
    # a pile of twenty labels on one spot: some get dropped, none overlap
    pile = [("p%d" % i, 100, 100, 80, 16) for i in range(20)]
    r2 = pack_labels(pile)
    assert r2["dropped"] and len(r2["kept"]) + len(r2["dropped"]) == 20
