from core.annotate import build_annotation, uniprot_items, clinvar_items, normalize_kind, is_accession


def test_kinds_and_accessions():
    assert normalize_kind("clinvar") == (None, False, True)
    assert normalize_kind("disease")[1] is True and normalize_kind("tm")[0] == ["Transmembrane"]
    assert is_accession("P07550") and is_accession("A0A024R161") and not is_accession("HBB")


def test_build_annotation_ranges_labels_and_chimera_syntax():
    items = uniprot_items({"features": [{"type": "Transmembrane", "start": 10, "end": 12, "description": "Helical", "spec": ":10-12"},
                                        {"type": "Binding site", "start": 20, "end": 20, "description": "ATP", "spec": ":20"}]}, False)
    mapping = {"model": "#1", "map": {10: {"chain": "A", "number": 10}, 11: {"chain": "A", "number": 11},
                                        12: {"chain": "A", "number": 12}, 20: {"chain": "B", "number": 20, "resname": "LYS"}}}
    cmds, summary = build_annotation(items, mapping, "orange", True)
    assert "color #1/A:10-12 orange target ac" in cmds and "color #1/B:20 orange target ac" in cmds
    assert "show #1/B:20 atoms" in cmds and any(c.startswith('label #1/B:20 text "ATP"') for c in cmds)
    assert summary["mapped"] == 2 and summary["chains"] == ["A", "B"]
    cmds_c, _ = build_annotation(items, {**mapping, "model": "#0"}, "orange", True, edition="chimera")
    assert "color orange,a,r #0:10-12.A" in cmds_c and any(c.startswith("rlabel #0:20.B") for c in cmds_c)


def test_clinvar_items_colors():
    items = clinvar_items({"variants": [{"position": 7, "ref": "E", "alt": "V", "significance": "Pathogenic", "id": "1"}]})
    assert items[0]["color"] == "red" and items[0]["label"] == "E7V" and items[0]["pathogenic"]
