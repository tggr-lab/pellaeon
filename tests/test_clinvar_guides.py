from core.clinvar import parse_hgvs_p, color_for, _rank, ClinVarClient
from core.knowledge import guide_chunks, BM25Index


def test_hgvs_parsing_and_severity():
    assert parse_hgvs_p("NM_000518.5(HBB):c.20A>T (p.Glu7Val)") == (7, "E", "V")
    assert parse_hgvs_p("p.Arg159Cys") == (159, "R", "C")
    assert parse_hgvs_p("p.Trp10Ter") == (10, "W", "*")
    assert parse_hgvs_p("c.1234G>A") is None
    assert _rank("Pathogenic") < _rank("Likely pathogenic") < _rank("Uncertain significance") < _rank("Benign")
    assert color_for("Pathogenic/Likely pathogenic") == "orangered" and color_for("weird") == "gray"


def test_clinvar_client_parses_summaries(monkeypatch):
    import core.clinvar as cv
    calls = []

    def fake(method, url, timeout=0, **kw):
        calls.append(url)
        if "esearch" in url:
            return {"esearchresult": {"idlist": ["1", "2", "3"]}}
        return {"result": {"uids": ["1", "2", "3"],
                           "1": {"title": "NM_1(HBB):c.20A>T (p.Glu7Val)", "germline_classification": {"description": "Pathogenic"}},
                           "2": {"protein_change": "p.Glu7Lys", "germline_classification": {"description": "Likely benign"}},
                           "3": {"protein_change": ["p.Gly17Asp"], "clinical_significance": {"description": "Uncertain significance"}}}}
    monkeypatch.setattr(cv, "request_json", fake)
    monkeypatch.setattr(cv.time, "sleep", lambda s: None)
    out = ClinVarClient(None).missense_variants("hbb")
    assert out["count"] == 2 and out["variants"][0] == {"position": 7, "ref": "E", "alt": "V", "significance": "Pathogenic", "id": "1"}
    assert out["variants"][1]["position"] == 17


def test_guide_chunks_are_searchable():
    text = "Selection and Model Specification\n\nSelecting Items - select: Highlights specified atoms. Basic Syntax: select <spec>. " \
           "A blank specification selects everything in all models.\n\nColoring\n\nThe color command applies a color to atoms, ribbons and surfaces. " \
           "Use color byelement for CPK colors and rainbow for sequential coloring along a chain of residues."
    ch = guide_chunks(text, "cli guide")
    assert len(ch) == 2 and ch[0].section == "Selection and Model Specification" and ch[1].kind == "guide"
    idx = BM25Index()
    idx.build(ch)
    assert idx.search("rainbow along the chain", 1)[0][1].section == "Coloring"


def test_clinvar_uses_a_real_field_tag_for_missense():
    """'missense[consequence]' is not a ClinVar field tag; NCBI degrades an unknown tag to free
    text, which returned zero variants for several genes we tried."""
    import inspect
    from core import clinvar
    src = inspect.getsource(clinvar)
    assert "missense[consequence]" not in src
    assert '"missense variant"[molecular consequence]' in src
