"""Local knowledge: the user's own ChimeraX docs + curated cheatsheet, searched with BM25.

No third-party dependencies: HTML is parsed with ``html.parser`` and the index
is plain Python. The index is cached as gzipped JSON keyed by ChimeraX version.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Tuple

INDEX_FORMAT = 3

# ------------------------------------------------------------------ tokenizer
_SYNONYMS = {
    "colour": "color", "colours": "color", "coloring": "color", "colored": "color", "colouring": "color",
    "spin": "turn", "rotate": "turn", "rotation": "turn", "spinning": "turn",
    "transparent": "transparency", "opacity": "transparency", "seethrough": "transparency",
    "backbone": "cartoon", "ribbon": "cartoon", "ribbons": "cartoon",
    "residues": "residue", "residu": "residue", "aminoacid": "residue", "aminoacids": "residue",
    "amino": "residue", "acids": "residue", "position": "residue", "positions": "residue",
    "protein": "structure", "proteins": "structure", "model": "structure", "models": "structure",
    "molecule": "structure", "molecules": "structure",
    "af": "alphafold", "afdb": "alphafold",
    "picture": "save", "image": "save", "screenshot": "save", "png": "save",
    "publication": "preset", "pretty": "preset", "nice": "preset",
    "goodsell": "preset", "cartoonish": "preset", "cartoony": "preset", "illustration": "preset", "toon": "preset",
    "measure": "distance", "mesure": "distance", "measuring": "distance",
    "hbond": "hbonds", "hydrogen": "hbonds",
    "bg": "background", "bacground": "background", "backround": "background",
    "focus": "view", "focos": "view", "zoom": "view", "center": "view", "centre": "view",
    "remove": "hide", "unshow": "hide", "delete": "delete",
    "stracture": "structure", "structur": "structure",
    "ligands": "ligand", "waters": "water", "solvent": "water",
    "chains": "chain", "atoms": "atom", "bonds": "bond", "surfaces": "surface",
    "labels": "label", "labelling": "label", "labeling": "label",
    "sticks": "stick", "spheres": "sphere", "balls": "ball",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _stem(tok: str) -> str:
    if len(tok) > 5 and tok.endswith("ing"):
        tok = tok[:-3]
    elif len(tok) > 4 and tok.endswith("ed"):
        tok = tok[:-2]
    elif len(tok) > 4 and tok.endswith("es"):
        tok = tok[:-2]
    elif len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        tok = tok[:-1]
    return tok


def tokenize(text: str) -> List[str]:
    out = []
    for tok in _TOKEN_RE.findall(text.lower()):
        tok = _SYNONYMS.get(tok, tok)
        tok = _SYNONYMS.get(_stem(tok), _stem(tok))
        if len(tok) > 1:
            out.append(tok)
    return out


def _edit_distance(a: str, b: str, limit: int) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            cur.append(v)
            best = min(best, v)
        if best > limit:
            return limit + 1
        prev = cur
    return prev[-1]


# ------------------------------------------------------------------ chunks
@dataclass
class Chunk:
    id: int
    command: str      # primary command name, e.g. "color"
    title: str        # "color, rainbow"
    section: str      # section heading or "usage"
    text: str
    url: str          # help:user/commands/color.html#anchor
    kind: str = "doc"  # doc | usage | cheatsheet | registry
    boost: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class _DocParser(HTMLParser):
    """Turn a ChimeraX user-doc command page into (title, sections)."""

    _BLOCK = {"p", "div", "li", "tr", "blockquote", "br", "table", "ul", "ol", "dl", "dd", "dt", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip = 0           # inside script/style/nav
        self._heading: Optional[str] = None
        self._heading_class = ""
        self._heading_buf: List[str] = []
        self.sections: List[Tuple[str, str, str, str]] = []  # (heading, anchor, kind, text)
        self._cur_heading = "top"
        self._cur_anchor = ""
        self._cur_kind = "doc"
        self._buf: List[str] = []
        self._pending_anchor = ""

    def _flush(self):
        text = re.sub(r"[ \t\xa0]+", " ", "".join(self._buf))
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if text:
            self.sections.append((self._cur_heading, self._cur_anchor, self._cur_kind, text))
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag == "p" and a.get("class") == "nav":
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "a" and a.get("name"):
            self._pending_anchor = a["name"]
            return
        if tag in ("h2", "h3", "h4"):
            self._flush()
            self._heading = tag
            self._heading_class = a.get("class", "")
            self._heading_buf = []
            return
        if tag in self._BLOCK:
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if tag == "p" and self._skip:
            # a nav paragraph closed
            self._skip = max(0, self._skip - 1)
        if tag == "title":
            self._in_title = False
            return
        if tag in ("h2", "h3", "h4") and self._heading:
            head = re.sub(r"\s+", " ", "".join(self._heading_buf)).strip()
            self._heading = None
            if self._heading_class == "usage":
                self._cur_heading = "usage"
                self._cur_kind = "usage"
                self._buf.append(head + "\n")
            else:
                self._cur_heading = head or self._cur_heading
                self._cur_kind = "doc"
            self._cur_anchor = self._pending_anchor
            self._pending_anchor = ""
            return
        if tag in self._BLOCK:
            self._buf.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        if self._heading is not None:
            self._heading_buf.append(data)
            return
        self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def _split_text(text: str, max_chars: int) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+|\n", text):
        if not sent:
            continue
        if len(cur) + len(sent) + 1 > max_chars and cur:
            out.append(cur.strip())
            cur = ""
        cur += sent + " "
    if cur.strip():
        out.append(cur.strip())
    return out


def parse_command_html(html_text: str, rel_url: str, max_chars: int = 1200) -> List[Chunk]:
    """Parse one docs page (e.g. user/commands/color.html) into chunks."""
    p = _DocParser()
    p.feed(html_text)
    p.close()
    title = re.sub(r"^\s*Command:\s*", "", p.title).strip()
    if not title:
        title = os.path.splitext(os.path.basename(rel_url))[0]
    command = title.split(",")[0].strip().lower() or os.path.splitext(os.path.basename(rel_url))[0]
    chunks: List[Chunk] = []
    for heading, anchor, kind, text in p.sections:
        url = "help:%s" % rel_url + ("#%s" % anchor if anchor else "")
        # usage statements ("Usage: color spec color-spec ...") get their own boosted chunks
        for um in re.finditer(r"Usage:\s*(.+?)(?:\n\n|\Z)", text, re.S):
            usage = " ".join(um.group(1).split())
            if len(usage) > 3:
                chunks.append(Chunk(len(chunks), command, title, heading, "Usage: " + usage, url, "usage", 1.6))
        if kind == "usage":
            continue
        text = re.sub(r"\n{2,}", "\n", text)
        for piece in _split_text(text, max_chars):
            if len(piece) < 40 and kind != "usage":
                continue
            chunks.append(Chunk(len(chunks), command, title, heading, piece, url, kind,
                                1.6 if kind == "usage" else 1.0))
    return chunks


# ------------------------------------------------------------------ BM25
class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.chunks: List[Chunk] = []
        self.doc_len: List[int] = []
        self.avg_len = 1.0
        self.postings: Dict[str, Dict[int, int]] = {}
        self.df: Dict[str, int] = {}
        self.vocab_freq: Counter = Counter()

    @staticmethod
    def _doc_tokens(chunk: Chunk) -> List[str]:
        # title/command tokens weighted by repetition (cheap field boosting)
        toks = tokenize(chunk.text)
        toks += tokenize(chunk.title) * 3
        toks += tokenize(chunk.command) * 2
        return toks

    def build(self, chunks: Iterable[Chunk]) -> None:
        self.chunks = list(chunks)
        for i, c in enumerate(self.chunks):
            c.id = i
        self.postings = defaultdict(dict)
        self.doc_len = []
        self.vocab_freq = Counter()
        for c in self.chunks:
            toks = self._doc_tokens(c)
            self.doc_len.append(len(toks))
            tf = Counter(toks)
            self.vocab_freq.update(tf.keys())
            for t, n in tf.items():
                self.postings[t][c.id] = n
        self.postings = dict(self.postings)
        self.df = {t: len(d) for t, d in self.postings.items()}
        self.avg_len = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 1.0

    def _expand(self, tok: str) -> List[str]:
        if tok in self.postings:
            return [tok]
        if len(tok) < 4:
            return []
        limit = 1 if len(tok) < 7 else 2
        cands = []
        for v in self.postings:
            if abs(len(v) - len(tok)) > limit or v[0] != tok[0] and len(tok) < 6:
                continue
            d = _edit_distance(tok, v, limit)
            if d <= limit:
                cands.append((d, -self.vocab_freq[v], v))
        cands.sort()
        return [c[2] for c in cands[:2]]

    def search(self, query: str, k: int = 5, kinds: Optional[Iterable[str]] = None) -> List[Tuple[float, Chunk]]:
        if not self.chunks:
            return []
        scores: Dict[int, float] = defaultdict(float)
        n = len(self.chunks)
        for qt in set(tokenize(query)):
            for t in self._expand(qt):
                df = self.df.get(t, 0)
                if not df:
                    continue
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                for doc_id, tf in self.postings[t].items():
                    dl = self.doc_len[doc_id]
                    denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avg_len)
                    scores[doc_id] += idf * tf * (self.k1 + 1) / denom
        allowed = set(kinds) if kinds else None
        qtokens = set(tokenize(query))
        qwords = set(_TOKEN_RE.findall(query.lower()))
        ranked = []
        for doc_id, s in scores.items():
            c = self.chunks[doc_id]
            if allowed and c.kind not in allowed:
                continue
            boost = c.boost
            # the user literally named the command (e.g. "color chain A blue"): favour its docs
            if c.command in qwords or c.command in qtokens:
                boost *= 1.6
            ranked.append((s * boost, c))
        ranked.sort(key=lambda x: -x[0])
        # diversify: at most 2 chunks per command in the top k
        out, per_cmd = [], Counter()
        for s, c in ranked:
            if per_cmd[c.command] >= 2:
                continue
            per_cmd[c.command] += 1
            out.append((s, c))
            if len(out) >= k:
                break
        return out

    # ---- persistence ----
    def save(self, path: str, meta: Optional[Dict[str, Any]] = None) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        data = {"format": INDEX_FORMAT, "meta": meta or {}, "chunks": [c.to_dict() for c in self.chunks]}
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(data, f)

    @classmethod
    def load(cls, path: str) -> Tuple["BM25Index", Dict[str, Any]]:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("format") != INDEX_FORMAT:
            raise ValueError("stale index format")
        idx = cls()
        idx.build(Chunk(**c) for c in data["chunks"])
        return idx, data.get("meta", {})


# ------------------------------------------------------------------ cheatsheet
def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cheatsheet_chunks(cheatsheet: Dict[str, Dict[str, Any]]) -> List[Chunk]:
    chunks = []
    for cmd, info in cheatsheet.items():
        text = "%s: %s\nKeywords: %s\nExamples of requests: %s\n%s" % (
            cmd, info.get("purpose", ""), ", ".join(info.get("keywords", [])),
            "; ".join(info.get("nl_queries", [])), info.get("notes", ""))
        chunks.append(Chunk(0, cmd, cmd, "cheatsheet", text.strip(),
                            "help:user/commands/%s.html" % cmd, "cheatsheet", 1.4))
    return chunks


def workflow_chunks(workflows: List[Dict[str, Any]]) -> List[Chunk]:
    """Tutorial-derived workflows: name, goal, example requests, exact commands."""
    chunks = []
    for w in workflows:
        cmds = [str(c) for c in (w.get("commands") or [])]
        if not cmds:
            continue
        text = "Workflow: %s\nGoal: %s\nRequests: %s\nCommands:\n%s\n%s" % (
            w.get("name", ""), w.get("goal", ""), "; ".join(w.get("requests") or []),
            "\n".join(cmds[:25]), ("Notes: " + w["notes"]) if w.get("notes") else "")
        first = cmds[0].split()[0].lower() if cmds[0].split() else "workflow"
        chunks.append(Chunk(0, first, w.get("name", "workflow"), "tutorial workflow", text.strip()[:2500],
                            w.get("source", "") or "help:user/index.html", "tutorial", 1.5))
    return chunks


def guide_chunks(text: str, title: str, url: str = "", max_chars: int = 1200) -> List[Chunk]:
    """Plain-text guides (e.g. a long CLI overview): split at blank lines / headings into passages."""
    chunks: List[Chunk] = []
    section = title
    buf: List[str] = []

    def flush():
        body = " ".join(buf).strip()
        buf.clear()
        if len(body) < 60:
            return
        for piece in _split_text(body, max_chars):
            cmd = ""
            m = re.search(r"\b([a-z][a-z0-9]{2,})\b", piece.lower())
            if m:
                cmd = m.group(1)
            chunks.append(Chunk(0, cmd, title, section[:80], piece, url or "help:user/index.html", "guide", 1.1))

    for para in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        para = para.strip()
        if not para:
            continue
        if len(para) < 90 and not para.endswith(".") and "\n" not in para:
            flush()
            section = para
            continue
        buf.append(para)
        if sum(len(b) for b in buf) > max_chars:
            flush()
    flush()
    return chunks


def recipe_library_chunks(recipes: List[Dict[str, Any]]) -> List[Chunk]:
    """Community recipes (RBVI chimerax-recipes): what they do and how to use them."""
    chunks = []
    for r in recipes:
        text = "Recipe: %s\nWhat: %s\nRequests: %s\nHow to use: %s\n%s" % (
            r.get("name", ""), r.get("what", ""), "; ".join(r.get("requests") or []), r.get("how", ""),
            ("Example: " + " ; ".join(r.get("commands") or [])) if r.get("commands") else "")
        chunks.append(Chunk(0, "recipe", r.get("name", "recipe"), "community recipe", text.strip()[:2000],
                            "https://rbvi.github.io/chimerax-recipes/%s/" % r.get("dir", ""), "recipe", 1.2))
    return chunks


# ------------------------------------------------------------------ facade
class Knowledge:
    """Builds/loads the index and answers searches. ChimeraX-agnostic."""

    def __init__(self, cache_dir: str, version_key: str, docs_dirs: List[str],
                 cheatsheet: Optional[Dict[str, Any]] = None,
                 registry_entries: Optional[Dict[str, str]] = None,
                 workflows: Optional[List[Dict[str, Any]]] = None,
                 recipe_library: Optional[List[Dict[str, Any]]] = None,
                 guides: Optional[List[Tuple[str, str]]] = None,
                 log=None):
        self.cache_dir = cache_dir
        self.version_key = re.sub(r"[^A-Za-z0-9_.-]", "_", version_key)
        self.docs_dirs = docs_dirs
        self.cheatsheet = cheatsheet or {}
        self.registry_entries = registry_entries or {}
        self.workflows = workflows or []
        self.recipe_library = recipe_library or []
        self.guides = guides or []   # (title, text)
        self.index: Optional[BM25Index] = None
        self.log = log or (lambda msg: None)

    @property
    def cache_path(self) -> str:
        data_key = "%d-%d-%d-%d-%d" % (len(self.cheatsheet), len(self.workflows), len(self.recipe_library),
                                          sum(len(t) for _n, t in self.guides), INDEX_FORMAT)
        return os.path.join(self.cache_dir, "index-%s-%s.json.gz" % (self.version_key, data_key))

    def ensure(self, progress=None) -> BM25Index:
        if self.index is not None:
            return self.index
        try:
            self.index, meta = BM25Index.load(self.cache_path)
            self.log("Pellaeon: loaded docs index (%d passages)" % len(self.index.chunks))
            return self.index
        except Exception:
            pass
        return self.rebuild(progress)

    def rebuild(self, progress=None) -> BM25Index:
        chunks: List[Chunk] = []
        files: List[Tuple[str, str]] = []
        for d in self.docs_dirs:
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                if name.endswith(".html"):
                    rel = "user/commands/%s" % name if d.rstrip("/\\").endswith("commands") else "user/%s" % name
                    files.append((os.path.join(d, name), rel))
        for i, (path, rel) in enumerate(files):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    chunks.extend(parse_command_html(f.read(), rel))
            except Exception as e:
                self.log("Pellaeon: could not parse %s: %s" % (path, e))
            if progress and i % 10 == 0:
                progress(i + 1, len(files))
        chunks.extend(cheatsheet_chunks(self.cheatsheet))
        chunks.extend(workflow_chunks(self.workflows))
        chunks.extend(recipe_library_chunks(self.recipe_library))
        for title, text in self.guides:
            chunks.extend(guide_chunks(text, title))
        for name, usage in self.registry_entries.items():
            chunks.append(Chunk(0, name.split()[0], name, "usage", usage,
                                "help:user/commands/%s.html" % name.split()[0], "registry", 1.2))
        idx = BM25Index()
        idx.build(chunks)
        self.index = idx
        try:
            idx.save(self.cache_path, {"version": self.version_key, "files": len(files)})
        except Exception as e:
            self.log("Pellaeon: could not cache index: %s" % e)
        self.log("Pellaeon: indexed %d doc pages into %d passages" % (len(files), len(chunks)))
        return idx

    def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        idx = self.ensure()
        out = []
        for score, c in idx.search(query, k):
            out.append({"command": c.command, "title": c.title, "section": c.section,
                        "text": c.text, "url": c.url, "score": round(score, 2), "kind": c.kind})
        return out

    def command_directory(self) -> List[Tuple[str, str]]:
        return [(cmd, info.get("purpose", "")) for cmd, info in sorted(self.cheatsheet.items())]
