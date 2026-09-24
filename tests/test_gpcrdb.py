"""GPCRdb helpers without the network."""
import io
import zipfile

from core import gpcrdb


def test_entry_names():
    assert gpcrdb.entry_name("PAR1_HUMAN") == "par1_human"
    assert gpcrdb.entry_for_accession("PAR1_HUMAN") == "par1_human"   # no lookup needed
    assert gpcrdb.model_url("par1_human", "active").endswith("/par1_human_active_full/download_pdb")


def test_fetch_model_unzips_the_pdb(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ClassA_par1_human_Active_AFMS_2024-05-15_GPCRdb.pdb", "ATOM      1  N   MET A   1       0.0 0.0 0.0\n" * 20 + "END\n")
    monkeypatch.setattr(gpcrdb, "request_bytes", lambda *a, **k: buf.getvalue())
    got = gpcrdb.fetch_model("par1_human", "active", str(tmp_path))
    assert "error" not in got and got["path"].endswith("par1_human_active_afms.pdb")
    assert open(got["path"]).read().startswith("ATOM")
    again = gpcrdb.fetch_model("par1_human", "active", str(tmp_path))
    assert again.get("cached") is True
    assert "error" in gpcrdb.fetch_model("par1_human", "sideways", str(tmp_path))


def test_structures_parses_rows(monkeypatch):
    rows = [{"pdb_code": "8XOR", "state": "Active", "type": "Electron microscopy", "resolution": 3.0,
             "ligands": [{"name": "Tethered agonist"}], "publication_date": "2024-09-18", "preferred_chain": "R"},
            {"pdb_code": "3VW7", "state": "Inactive", "type": "X-ray diffraction", "resolution": 2.2, "ligands": [{"name": "vorapaxar"}]}]
    monkeypatch.setattr(gpcrdb, "request_json", lambda *a, **k: rows)
    out = gpcrdb.structures("par1_human")
    assert out["count"] == 2 and {s["pdb"] for s in out["structures"]} == {"8XOR", "3VW7"}
    assert out["structures"][0]["ligands"][0] in ("Tethered agonist", "vorapaxar") and "caveat" in out
