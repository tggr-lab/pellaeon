from core.tables import parse_table, guess_columns, table_rows, plan_overlay, attr_name

CSV = """# conservation scores
position,wt,chain,conservation,class
10,A,A,0.91,core
11,K,A,0.12,surface
12,G,A,0.55,surface
999,L,A,0.3,core
"""


def test_parse_and_guess():
    t = parse_table(CSV, "scores.csv")
    assert t["columns"] == ["position", "wt", "chain", "conservation", "class"] and len(t["rows"]) == 4 and t["delimiter"] == "comma"
    g = guess_columns(t["columns"], t["rows"])
    assert g["position"] == 0 and g["reference"] == 1 and g["chain"] == 2 and g["values"] == [3] and g["categories"] == [4]
    assert g["default_value"] == 3


def test_tsv_without_header_uses_first_int_column():
    t = parse_table("5\t0.1\n6\t0.9\n", "x.tsv")
    g = guess_columns(t["columns"], t["rows"])
    assert t["columns"] == ["col1", "col2"] and g["position"] == 0 and g["values"] == [1]


def test_numeric_overlay_reports_missing_and_mismatches():
    t = parse_table(CSV); g = guess_columns(t["columns"], t["rows"])
    rows = table_rows(t, g, 3, g["chain"])
    residues = {("A", 10): "ALA", ("A", 11): "ARG", ("A", 12): "GLY", ("B", 10): "ALA"}
    plan = plan_overlay(rows, residues, "#1", attr_name("scores", "conservation"))
    assert plan["numeric"] and plan["mapped"] == 2 and plan["missing"] == [999] and plan["n_mismatches"] == 1
    assert "A:11 is R, table says K" in plan["mismatches"][0]
    assert plan["commands"][0].startswith("color byattribute r:pellaeon_scores_conservation #1 palette blue:white:red range 0.55,0.91")
    assert any(c.startswith("key blue:0.55 white:0.73 red:0.91") for c in plan["commands"])
    assert plan["assignments"] == {"A:10": 0.91, "A:12": 0.55}


def test_categorical_overlay_colors_groups():
    t = parse_table(CSV); g = guess_columns(t["columns"], t["rows"])
    rows = table_rows(t, g, 4, None)   # ignore the chain column: apply to every chain that has the residue
    residues = {("A", 10): "ALA", ("B", 10): "ALA", ("A", 12): "GLY"}
    plan = plan_overlay(rows, residues, "#1", "pellaeon_x_class")
    assert not plan["numeric"] and plan["mapped"] == 3
    assert any("#1/A:10 #1/B:10" in c and "cornflowerblue" in c for c in plan["commands"])
    assert "core = cornflowerblue" in plan["legend"] and "surface = orange" in plan["legend"]


def test_uniprot_numbering_map_is_used_when_given():
    rows = [{"position": 5, "value": 1.0, "chain": None, "ref": None}, {"position": 6, "value": 2.0, "chain": None, "ref": None}]
    residues = {("A", 105): "ALA"}
    plan = plan_overlay(rows, residues, "#2", "pellaeon_t_v", numbering_map={5: {"chain": "A", "number": 105, "resname": "ALA"}})
    assert plan["assignments"] == {"A:105": 1.0} and plan["missing"] == [6]


def test_nothing_placed_is_an_error():
    plan = plan_overlay([{"position": 1, "value": 1.0, "chain": None, "ref": None}], {("A", 9): "GLY"}, "#1", "a")
    assert "error" in plan and plan["missing"] == [1]


def test_dataset_chain_absent_from_a_single_chain_model_falls_back():
    from core.tables import plan_overlay
    rows = [{"position": 42, "value": 9.0, "chain": "R", "ref": None}, {"position": 43, "value": 1.0, "chain": "R", "ref": None}]
    residues = {("A", 42): "SER", ("A", 43): "PHE"}
    out = plan_overlay(rows, residues, "#1", "consurf_grade")
    assert "error" not in out and out["chains"] == ["A"]
    out = plan_overlay(rows, residues, "#1", "consurf_grade", chains=["R"])   # the chain the user named, absent here
    assert "error" not in out and out["chains"] == ["A"]
