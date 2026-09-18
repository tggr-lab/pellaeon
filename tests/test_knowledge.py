import json
import os
import tempfile

from conftest import FIXTURES, DATA
from core.knowledge import parse_command_html, BM25Index, Knowledge, tokenize, cheatsheet_chunks

DOCS = os.path.join(FIXTURES, "docs_html")


def _chunks(name):
    with open(os.path.join(DOCS, name + ".html"), encoding="utf-8") as f:
        return parse_command_html(f.read(), "user/commands/%s.html" % name)


def test_parse_color_page():
    ch = _chunks("color")
    assert ch and ch[0].command == "color"
    usage = [c for c in ch if c.kind == "usage"]
    assert usage, "usage statements should be extracted"
    assert any("color" in c.text.lower() and "spec" in c.text.lower() for c in usage)
    assert all(len(c.text) <= 1300 for c in ch)
    assert all(c.url.startswith("help:user/commands/color.html") for c in ch)
    assert not any("<" in c.text and ">" in c.text and "href" in c.text for c in ch)


def test_parse_select_page_has_usage():
    ch = _chunks("select")
    assert any(c.kind == "usage" for c in ch)
    assert any("zone" in c.section.lower() or "zone" in c.text.lower() for c in ch)


def test_tokenize_synonyms_and_stems():
    toks = tokenize("Colour the residues and make it transparent")
    assert "color" in toks and "residue" in toks and "transparency" in toks


def _index():
    chunks = []
    for name in ("color", "select", "open", "cartoon", "atomspec", "hbonds"):
        chunks.extend(_chunks(name))
    with open(os.path.join(DATA, "cheatsheet.json"), encoding="utf-8") as f:
        chunks.extend(cheatsheet_chunks(json.load(f)))
    idx = BM25Index()
    idx.build(chunks)
    return idx


def test_search_ranks_right_command():
    idx = _index()
    top = [c.command for _s, c in idx.search("color chain A blue", 5)]
    assert top[0] == "color"
    top = [c.command for _s, c in idx.search("show hydrogen bonds", 5)]
    assert "hbonds" in top[:2]
    top = [c.command for _s, c in idx.search("mesure the distance between two residues", 5)]
    assert "distance" in top[:3]
    top = [c.command for _s, c in idx.search("make it spin", 5)]
    assert "roll" in top[:3] or "turn" in top[:3]


def test_typo_tolerance():
    idx = _index()
    top = [c.command for _s, c in idx.search("hydrogne bonds", 5)]
    assert "hbonds" in top[:3]


def test_knowledge_cache_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(DATA, "cheatsheet.json"), encoding="utf-8") as f:
            cheat = json.load(f)
        k = Knowledge(tmp, "1.12", [DOCS], cheatsheet=cheat, registry_entries={"foo bar": "Usage: foo bar spec"})
        idx = k.ensure()
        assert os.path.exists(k.cache_path)
        n = len(idx.chunks)
        k2 = Knowledge(tmp, "1.12", [DOCS], cheatsheet=cheat)
        assert len(k2.ensure().chunks) == n
        hits = k2.search("foo bar", 3)
        assert hits and hits[0]["command"] == "foo"
        assert len(k2.command_directory()) > 100
