"""AlphaMissense / ConSurf-DB fetchers, driven by recorded responses (no network in the unit suite).

The fixtures are trimmed copies of what the live endpoints actually returned for P55085 and 1UBQ on
2026-09-21; see the docstrings in core/annot_sources.py for the URLs.
"""
import json
import os
import time

import pytest

from conftest import FIXTURES
from core import annot_sources as A
from core.tables import guess_columns, table_rows, plan_overlay

AM_CSV = open(os.path.join(FIXTURES, "alphamissense_P55085_trimmed.csv"), encoding="utf-8").read()
AM_PRED = json.load(open(os.path.join(FIXTURES, "alphafold_prediction_P55085_trimmed.json"), encoding="utf-8"))
GRADES = open(os.path.join(FIXTURES, "consurf_6Q00_A_grades_trimmed.txt"), encoding="utf-8").read()
CONSURF_API = json.load(open(os.path.join(FIXTURES, "consurf_1UBQ_A_api_trimmed.json"), encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(A.time, "sleep", lambda *_: None)


def _fake_am(monkeypatch, pred=None, csv_text=None, calls=None):
    def request_json(method, url, **kw):
        if calls is not None:
            calls.append(url)
        return pred if pred is not None else AM_PRED

    def request_text(url, **kw):
        if calls is not None:
            calls.append(url)
        return AM_CSV if csv_text is None else csv_text

    monkeypatch.setattr(A, "request_json", request_json)
    monkeypatch.setattr(A, "request_text", request_text)


def _fake_consurf(monkeypatch, grades=GRADES, api=None, calls=None):
    api = api or CONSURF_API

    def request_json(method, url, **kw):
        if calls is not None:
            calls.append(url)
        for key in ("find_chains", "get_identical_chain", "get_final_data"):
            if key in url:
                return api[key]
        raise AssertionError("unexpected URL " + url)

    monkeypatch.setattr(A, "request_json", request_json)
    monkeypatch.setattr(A, "request_text", lambda url, **kw: grades)


# --- AlphaMissense -------------------------------------------------------------------------------

def test_alphamissense_aggregates_per_position(monkeypatch, tmp_path):
    _fake_am(monkeypatch)
    d = A.alphamissense("P55085", str(tmp_path))
    assert d["columns"] == ["position", "wt", "am_mean", "am_max", "am_class"]
    assert [r[0] for r in d["rows"]] == ["1", "2", "3", "11", "30", "35"]
    first = d["rows"][0]
    assert first[1] == "M" and first[2] == "0.2920" and first[3] == "0.6693"
    assert d["n_variants"] == 133 and d["n_positions"] == 6
    assert [r[4] for r in d["rows"]] == ["likely benign", "likely benign", "likely benign",
                                         "ambiguous", "likely benign", "likely pathogenic"]


def test_alphamissense_mean_and_max_match_hand_computation(monkeypatch, tmp_path):
    _fake_am(monkeypatch)
    d = A.alphamissense("P55085", str(tmp_path))
    scores = [float(l.split(",")[1]) for l in AM_CSV.splitlines()[1:] if l.startswith("R2")]
    assert float(d["rows"][1][2]) == pytest.approx(sum(scores) / len(scores), abs=5e-5)
    assert float(d["rows"][1][3]) == pytest.approx(max(scores), abs=5e-5)


def test_position_with_two_wild_types_uses_the_canonical_sequence(monkeypatch, tmp_path):
    # position 30 carries both N (canonical) and S substitutions; only N's 19 may be summarised
    _fake_am(monkeypatch)
    d = A.alphamissense("P55085", str(tmp_path))
    pos30 = [r for r in d["rows"] if r[0] == "30"][0]
    assert pos30[1] == "N" and float(pos30[2]) == pytest.approx(0.1134, abs=1e-4)


def test_am_class_cutoffs():
    assert A.am_class(0.0) == "likely benign"
    assert A.am_class(0.3399) == "likely benign"
    assert A.am_class(0.34) == "ambiguous"          # the boundary itself is not benign
    assert A.am_class(0.564) == "ambiguous"
    assert A.am_class(0.5641) == "likely pathogenic"
    assert A.am_class(1.0) == "likely pathogenic"


def test_am_class_of_the_mean_is_what_the_row_carries(monkeypatch, tmp_path):
    _fake_am(monkeypatch)
    for pos, wt, mean, mx, cls in A.alphamissense("P55085", str(tmp_path))["rows"]:
        assert cls == A.am_class(float(mean)) and float(mx) >= float(mean)


def test_alphamissense_dataset_is_uniprot_numbered_and_named(monkeypatch, tmp_path):
    _fake_am(monkeypatch)
    d = A.alphamissense("p55085", str(tmp_path))
    assert d["accession"] == "P55085" and d["position_numbering"] == "uniprot"
    assert d["source"].startswith("AlphaMissense") and "alphafold.ebi.ac.uk" in d["source_url"]
    assert d["palette"] == "blue-white-red"


def test_alphamissense_without_annotations_explains_why(monkeypatch, tmp_path):
    _fake_am(monkeypatch, pred=[{"organismScientificName": "Saccharomyces cerevisiae"}])
    d = A.alphamissense("P00330", str(tmp_path))
    assert "human" in d["error"] and "P00330" in d["error"] and "rows" not in d


def test_alphamissense_unknown_accession(monkeypatch, tmp_path):
    _fake_am(monkeypatch, pred=[])
    assert "no entry" in A.alphamissense("Q00000", str(tmp_path))["error"]


def test_alphamissense_network_failure_is_reported_not_raised(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise ConnectionError("Could not reach alphafold.ebi.ac.uk")
    monkeypatch.setattr(A, "request_json", boom)
    d = A.alphamissense("P55085", str(tmp_path))
    assert d["error"].startswith("AlphaMissense request failed") and "alphafold" in d["error"]


def test_alphamissense_requires_an_accession(tmp_path):
    assert "accession" in A.alphamissense("", str(tmp_path))["error"]


def test_alphamissense_cache_is_reused_and_expires(monkeypatch, tmp_path):
    calls = []
    _fake_am(monkeypatch, calls=calls)
    A.alphamissense("P55085", str(tmp_path))
    n = len(calls)
    A.alphamissense("P55085", str(tmp_path))
    assert len(calls) == n, "second call must come from disk"
    cached = tmp_path / "alphamissense_P55085.json"
    assert cached.exists()
    os.utime(cached, (0, time.time() - 31 * 86400))
    A.alphamissense("P55085", str(tmp_path))
    assert len(calls) == 2 * n, "a 31-day-old cache entry must be refetched"


def test_errors_are_not_cached(monkeypatch, tmp_path):
    _fake_am(monkeypatch, pred=[])
    A.alphamissense("Q00000", str(tmp_path))
    assert not list(tmp_path.glob("*.json"))


# --- ConSurf -------------------------------------------------------------------------------------

def test_consurf_rows_use_structure_numbering(monkeypatch, tmp_path):
    _fake_consurf(monkeypatch)
    d = A.conservation("1ubq", "A", str(tmp_path))
    assert d["columns"] == ["position", "chain", "wt", "consurf_grade", "consurf_score"]
    assert d["rows"][0] == ["1", "A", "M", "9", "-1.527"]
    assert d["rows"][1] == ["2", "A", "Q", "7", "-0.599"]
    assert d["position_numbering"] == "pdb" and "accession" not in d
    assert d["representative"] == "6Q00/A" and "6Q00" in d["note"]


def test_consurf_grades_are_1_to_9_with_9_conserved(monkeypatch, tmp_path):
    _fake_consurf(monkeypatch)
    d = A.conservation("1UBQ", "A", str(tmp_path))
    grades = [int(r[3]) for r in d["rows"]]
    assert all(1 <= g <= 9 for g in grades)
    # the ConSurf score is the normalised rate: conserved positions are the negative ones
    assert float(d["rows"][0][4]) < 0 and int(d["rows"][0][3]) == 9
    assert d["colors"]["9"] == "#A02560" and d["colors"]["1"] == "#10C8D1"


def test_consurf_skips_positions_with_no_atom_record(monkeypatch, tmp_path):
    synthetic = GRADES + "\n  77\t  X\t     -    \t 0.100\t   5 \n  78\t  A\tALA:78:A\t 0.100\t   5 \n"
    _fake_consurf(monkeypatch, grades=synthetic)
    d = A.conservation("1UBQ", "A", str(tmp_path))
    assert [r[0] for r in d["rows"]][-1] == "78" and "77" not in [r[0] for r in d["rows"]]


def test_consurf_rejects_a_uniprot_accession_with_an_actionable_message(tmp_path):
    d = A.conservation("P55085", "A", str(tmp_path))
    assert "PDB" in d["error"] and "P55085" in d["error"]


def test_consurf_unknown_entry(monkeypatch, tmp_path):
    _fake_consurf(monkeypatch, api=dict(CONSURF_API, find_chains=["error", "Invalid PDB ID."]))
    assert "no precomputed conservation" in A.conservation("9XYZ", "A", str(tmp_path))["error"]


def test_consurf_unknown_chain_lists_the_chains_it_has(monkeypatch, tmp_path):
    _fake_consurf(monkeypatch)
    d = A.conservation("1UBQ", "Z", str(tmp_path))
    assert "chain Z" in d["error"] and d["error"].rstrip(".").endswith("A")


def test_consurf_defaults_to_the_first_chain(monkeypatch, tmp_path):
    _fake_consurf(monkeypatch)
    assert A.conservation("1UBQ", "", str(tmp_path))["chain"] == "A"


def test_consurf_cache_is_reused(monkeypatch, tmp_path):
    calls = []
    _fake_consurf(monkeypatch, calls=calls)
    A.conservation("1UBQ", "A", str(tmp_path))
    n = len(calls)
    assert n == 3  # find_chains, get_identical_chain, get_final_data
    A.conservation("1UBQ", "A", str(tmp_path))
    assert len(calls) == n


def test_consurf_failure_is_reported(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise ConnectionError("Could not reach consurfdb.tau.ac.il")
    monkeypatch.setattr(A, "request_json", boom)
    assert A.conservation("1UBQ", "A", str(tmp_path))["error"].startswith("ConSurf-DB request failed")


# --- the whole point: the datasets drop straight into the overlay machinery ------------------------

def test_alphamissense_dataset_feeds_guess_and_plan(monkeypatch, tmp_path):
    _fake_am(monkeypatch)
    d = A.alphamissense("P55085", str(tmp_path))
    g = guess_columns(d["columns"], d["rows"])
    assert g["position"] == 0 and g["reference"] == 1 and g["default_value"] == 2 and g["categories"] == [4]
    rows = table_rows(d, g, 2)
    from core.tables import AA3
    residues = {("A", int(r[0])): AA3[r[1]] for r in d["rows"]}
    plan = plan_overlay(rows, residues, "#1", "pellaeon_am_mean",
                        numbering_map={int(r[0]): {"chain": "A", "number": int(r[0]), "resname": ""} for r in d["rows"]},
                        palette="blue-white-red")
    assert plan["mapped"] == 6 and plan["numeric"] and not plan["n_mismatches"]


def test_consurf_dataset_feeds_guess_and_plan(monkeypatch, tmp_path):
    _fake_consurf(monkeypatch)
    d = A.conservation("1UBQ", "A", str(tmp_path))
    g = guess_columns(d["columns"], d["rows"])
    assert g["position"] == 0 and g["chain"] == 1 and g["reference"] == 2 and g["default_value"] == 3
    rows = table_rows(d, g, 3, g["chain"])
    from core.tables import AA3
    residues = {("A", int(r[0])): AA3[r[2]] for r in d["rows"]}
    plan = plan_overlay(rows, residues, "#1", "pellaeon_consurf_grade", palette="blue-white-red")
    assert plan["mapped"] == len(residues) and plan["numeric"] and plan["chains"] == ["A"]
