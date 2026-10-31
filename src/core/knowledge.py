"""Local knowledge: the user's own ChimeraX docs + curated cheatsheet, searched with BM25.

No third-party dependencies: HTML is parsed with ``html.parser`` and the index
is plain Python. The index is cached as gzipped JSON keyed by the ChimeraX version and a hash of
everything it is built from (bundled data, registry usage, the doc pages), so any edit rebuilds it.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict, field
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

INDEX_FORMAT = 6   # 6: registry usage for every registered command, with defaults; 5: source line breaks collapsed, headers kept with their text, workflow topic command
# a passage handed to the model is cut to this many characters, its command examples first (see present()); six
# of them fit the 5000-character docs block that prompt.build_context fills, where whole passages left it at 3-5
PASSAGE_CHARS = 800

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
    "far": "distance", "apart": "distance",
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
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
# function words carry nothing for retrieval but used to count like any other term ("the", "as", "those");
# command names (show, set, view, turn, color ...) are never listed here
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "at", "for", "with", "as", "by", "from", "into",
    "it", "its", "this", "that", "these", "those", "there", "here", "is", "are", "be", "been", "was", "were",
    "do", "does", "did", "have", "has", "had", "can", "could", "would", "should", "will", "may", "might",
    "i", "me", "my", "we", "us", "our", "you", "your", "they", "them", "their",
    "so", "if", "then", "than", "too", "very", "just", "only", "also", "some", "any", "each", "every",
    "what", "which", "how", "when", "where", "why", "who",
    "please", "pls", "want", "like", "actually", "whatever", "something", "thing", "stuff",
    "s", "t", "re", "ve", "ll", "d", "m", "e", "g", "ie", "etc",
}
# plurals the suffix rules cannot reach
_PLURALS = {"helices": "helix", "vertices": "vertex", "matrices": "matrix", "indices": "index", "axes": "axis",
            "radii": "radius", "nuclei": "nucleus", "criteria": "criterion"}
_CAMEL_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")
# passage kinds whose command name is their topic (a workflow's "command" is only its most specific one)
_COMMAND_KINDS = {"doc", "usage", "cheatsheet", "registry", "gotcha"}


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


def _words(text: str) -> List[str]:
    """Lower-cased words; a camelCase option name (maximumIntensityProjection) also yields its parts, so the
    user's 'maximum intensity projection' finds it while the exact name still matches as one token."""
    out = []
    for w in _TOKEN_RE.findall(text):
        out.append(w.lower())
        if not w.islower() and not w.isupper() and not w.isdigit():
            parts = _CAMEL_RE.findall(w)
            if len(parts) > 1:
                out.extend(p.lower() for p in parts)
    return out


def tokenize(text: str) -> List[str]:
    out = []
    for tok in _words(text):
        # a bare number ("residue 10") matches every example that happens to use it
        if tok.isdigit() or tok in _STOPWORDS:
            continue
        tok = _PLURALS.get(tok, tok)
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
    examples: List[int] = field(default_factory=list)   # indexes of the lines that are command examples (tag_examples)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------ examples before prose
_CMD_LINE_RE = re.compile(r"^~?([A-Za-z][A-Za-z0-9_]*)(?:\s|$)")
_CODE_LINE_RE = re.compile(r"^`[^`]+`[.;,]?$")


def _is_example_line(line: str, commands: Set[str]) -> bool:
    """A line that shows a command rather than describing one: a usage statement, a code-formatted line, or a line
    that starts with a registered command name and reads like a command (no sentence inside, not a label)."""
    s = line.strip()
    if not s:
        return False
    if s.startswith("Usage:") or _CODE_LINE_RE.match(s):
        return True
    m = _CMD_LINE_RE.match(s)
    if not m or m.group(1).lower() not in commands:
        return False
    # "Show a chain's sequence in the viewer." is prose that happens to start with a command word
    return len(s) <= 160 and ". " not in s and not s.endswith((".", "?", "!"))


def tag_examples(chunks: Iterable[Chunk], commands: Optional[Set[str]] = None) -> None:
    """Record on every chunk which of its lines carry command examples. The command names are those the index
    knows (its doc pages, cheatsheet and registry entries) unless given."""
    chunks = list(chunks)
    if commands is None:
        commands = {c.command.lower() for c in chunks if c.kind in _COMMAND_KINDS and c.command}
    for c in chunks:
        lines = c.text.split("\n")
        tagged: List[int] = []
        in_commands = False
        for i, line in enumerate(lines):
            s = line.strip()
            if c.kind == "tutorial":
                # the workflow's own command list, whatever the commands are (tools, aliases, `ui tool show`)
                if s == "Commands:":
                    in_commands = True
                    continue
                if s.startswith("Notes:"):
                    in_commands = False
                if in_commands and s:
                    tagged.append(i)
                    continue
            if c.kind == "recipe" and s.startswith("Example: "):
                tagged.append(i)
                continue
            if _is_example_line(s, commands):
                tagged.append(i)
        c.examples = tagged


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`'\"(])")
_CODE_IN_PROSE_RE = re.compile(r"`[^`]+`|\b[a-z]+[A-Z][A-Za-z]+\b|(?:^|\s)[#/@:][A-Za-z0-9]")


def _sentences(line: str) -> List[str]:
    return [s for s in _SENTENCE_SPLIT_RE.split(line.strip()) if s]


def present(chunk: Chunk, budget: int = PASSAGE_CHARS) -> str:
    """The passage as the model sees it, cut to the budget: its command examples first, then the prose sentences
    that carry code (an option name, a spec, a backticked command), then the plain sentences; a workflow's example
    requests last. Example lines are kept whole while they fit; the last sentence is cut at a word."""
    lines = chunk.text.split("\n")
    tagged = set(chunk.examples or [])
    examples = [lines[i] for i in sorted(tagged) if i < len(lines) and lines[i].strip()]
    prose = [l for i, l in enumerate(lines) if i not in tagged and l.strip() and l.strip() != "Commands:"]
    tail = []
    if chunk.kind == "tutorial":
        tail = [l for l in prose if l.startswith("Requests:")]
        prose = [l for l in prose if not l.startswith("Requests:")]
    coded, plain = [], []
    for line in prose:
        for s in _sentences(line):
            (coded if _CODE_IN_PROSE_RE.search(s) else plain).append(s)
    out: List[str] = []
    used = 0
    for line in examples:
        if used + len(line) + 1 > budget:
            # a usage statement that runs on into its description (one line in the manual) is cut like prose
            room = budget - used - 1
            if not out or (room > 60 and " " in line[:room]):
                out.append(_cut(line, room) if len(line) > room else line)
            used = budget
            break
        out.append(line)
        used += len(line) + 1
    for piece in coded + plain + tail:
        if used + len(piece) + 1 <= budget:
            out.append(piece)
            used += len(piece) + 1
            continue
        room = budget - used - 1
        if room > 60 and " " in piece[:room]:
            cut = _cut(piece, room)
            if len(cut) > 40:
                out.append(cut)
        break
    return "\n".join(out) if out else chunk.text[:budget]


def _cut(text: str, room: int) -> str:
    """The first `room` characters of the text, ending at a word and marked as cut."""
    return text[:max(room - 4, 0)].rsplit(" ", 1)[0] + " ..."


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
        self._in_pre = 0

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
        if tag == "pre":
            self._in_pre += 1
        if tag in self._BLOCK:
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if tag == "pre":
            self._in_pre = max(0, self._in_pre - 1)
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
        if not self._in_pre:
            # a line break in the HTML source is just whitespace; only block tags end a line, so an option
            # name is not cut from its description ("gentle" / "– same as soft, except tuned to ...")
            data = data.replace("\r", " ").replace("\n", " ")
        self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def _split_text(text: str, max_chars: int) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    # a short line that is not a sentence is a header ("smooth factor", "• gentle"): keep it with what follows
    pieces: List[str] = []
    for s in re.split(r"(?<=[.!?])\s+|\n", text):
        if not s:
            continue
        if pieces and len(pieces[-1]) < 60 and not pieces[-1].rstrip().endswith((".", "!", "?")):
            pieces[-1] += " " + s
        else:
            pieces.append(s)
    out, cur = [], ""
    for sent in pieces:
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
            # the prose after a usage heading (what the command does, its options) belongs to the same
            # section when no further heading follows; dropping it left 68 of 149 pages (snfg, kvfinder,
            # mlp, nucleotides, preset, ...) with nothing but their usage line in the index
            text = re.sub(r"Usage:\s*.+?(?:\n\n|\Z)", "\n", text, flags=re.S)
            kind = "doc"
            heading = title
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
        if chunk.kind in _COMMAND_KINDS:
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
        tag_examples(self.chunks)

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
            # the user literally named the command (e.g. "color chain A blue"): favour its docs. Not for
            # workflows: every one whose commands start with "show" or "open" would be boosted by those words.
            if c.kind in _COMMAND_KINDS and (c.command in qwords or c.command in qtokens):
                boost *= 1.6
            ranked.append((s * boost, c))
        ranked.sort(key=lambda x: -x[0])
        # diversify: at most 3 passages per command in the top k. A usage line is short and precise, so it is
        # never held back by that cap (at most 2 per command: color, sequence and save have 14 usage lines each)
        # Gotcha passages (prompt variant e) have their own cap: eight color gotchas must not crowd out the
        # color usage lines, and the color docs must not use up the cap before the gotcha is reached.
        out, per_cmd, per_usage, per_gotcha = [], Counter(), Counter(), Counter()
        for s, c in ranked:
            if c.kind == "usage":
                if per_usage[c.command] >= 2:
                    continue
                per_usage[c.command] += 1
            elif c.kind == "gotcha":
                if per_gotcha[c.command] >= GOTCHAS_PER_COMMAND:
                    continue
                per_gotcha[c.command] += 1
            elif per_cmd[c.command] >= 3:
                continue
            else:
                per_cmd[c.command] += 1
            out.append((s, c))
            if len(out) >= k:
                break
        return out

    def search_with_gotchas(self, query: str, k: int = 5) -> List[Tuple[float, Chunk]]:
        """search(), then make sure the gotchas of a command the request names are in the list (variant e).
        Measured on the real index (1971 passages): named alone, a command's gotchas reached the top 6 for 33 of 59
        rules; the docs of a much-documented command (color, show, view, save) outscore them. Up to GOTCHAS_RESERVED
        of the lowest non-gotcha hits give way; the other slots stay with the manual."""
        out = self.search(query, k)
        if not any(c.kind == "gotcha" for c in self.chunks):
            return out
        named = set(_TOKEN_RE.findall(query.lower())) | set(tokenize(query))
        have = sum(1 for _s, c in out if c.kind == "gotcha")
        # a gotcha is "about" every command in its title ("Gotcha: hide, cartoon, show"), not only the first
        want = [(s, c) for s, c in self.search(query, k, kinds=["gotcha"])
                if c.command and (set(_TOKEN_RE.findall(c.title.lower())) - {"gotcha"}) & named
                and all(c.id != o.id for _os, o in out)][:max(0, GOTCHAS_RESERVED - have)]
        for pair in want:
            if len(out) >= k:
                for i in range(len(out) - 1, -1, -1):
                    if out[i][1].kind != "gotcha":
                        del out[i]
                        break
                else:
                    break
            out.append(pair)
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


_GOTCHA_TOOL_RE = re.compile(r"\b([a-z]+_[a-z_]+)\b")
_GOTCHA_CODE_RE = re.compile(r"`~?([a-z][a-z0-9]*)\b[^`]*`")
GOTCHAS_PER_COMMAND = 2      # gotcha passages about one command in a result list (the docs cap is separate)
GOTCHAS_RESERVED = 2         # slots kept for the gotchas of a command the request names, when BM25 alone ranks them lower


def gotcha_commands(line: str, known: Optional[Iterable[str]] = None) -> List[str]:
    """The command (and tool) names a gotcha line concerns: the first word of each backticked command, in order,
    plus the tools it names (view_axis, compare_structures); with `known` (cheatsheet commands and tool names) only those."""
    allowed = set(known) if known is not None else None
    out: List[str] = []
    for w in _GOTCHA_CODE_RE.findall(line) + _GOTCHA_TOOL_RE.findall(line):
        if allowed is not None and w not in allowed:
            continue
        if w not in out:
            out.append(w)
    return out


def gotcha_chunks(text: str, known: Optional[Iterable[str]] = None) -> List[Chunk]:
    """Prompt variant e: one passage per gotcha rule (a `- ` line of gotchas.md), carrying the command words it
    concerns so a request that names the command retrieves it; the rule's own quoted user phrasing is kept."""
    chunks = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        rule = line[2:].strip()
        cmds = gotcha_commands(rule, known)
        cmd = cmds[0] if cmds else ""
        body = rule + ("\nCommands: " + ", ".join(cmds) if cmds else "")
        chunks.append(Chunk(0, cmd, "Gotcha: " + (", ".join(cmds[:3]) or rule[:40]), "command gotcha", body,
                            ("help:user/commands/%s.html" % cmd) if cmd else "help:user/index.html", "gotcha", 1.5))
    return chunks


def workflow_chunks(workflows: List[Dict[str, Any]]) -> List[Chunk]:
    """Tutorial-derived workflows: name, goal, example requests, exact commands."""
    chunks = []
    names = []
    for w in workflows:
        names.append([c.split()[0].lower().lstrip("~") for c in (str(x) for x in (w.get("commands") or [])) if c.split()])
    freq = Counter(n for ns in names for n in set(ns))
    for w, ns in zip(workflows, names):
        cmds = [str(c) for c in (w.get("commands") or [])]
        if not cmds:
            continue
        text = "Workflow: %s\nGoal: %s\nRequests: %s\nCommands:\n%s\n%s" % (
            w.get("name", ""), w.get("goal", ""), "; ".join(w.get("requests") or []),
            "\n".join(cmds[:25]), ("Notes: " + w["notes"]) if w.get("notes") else "")
        # the workflow's topic is its rarest command (swapaa, volume ...), not the "open" it starts with
        topic = min(ns, key=lambda n: (freq[n], ns.index(n))) if ns else "workflow"
        chunks.append(Chunk(0, topic, w.get("name", "workflow"), "tutorial workflow", text.strip()[:2500],
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
                 log=None, gotchas: Optional[str] = None, tool_names: Optional[Iterable[str]] = None):
        self.cache_dir = cache_dir
        self.version_key = re.sub(r"[^A-Za-z0-9_.-]", "_", version_key)
        self.docs_dirs = docs_dirs
        self.cheatsheet = cheatsheet or {}
        self.registry_entries = registry_entries or {}
        self.workflows = workflows or []
        self.recipe_library = recipe_library or []
        self.guides = guides or []   # (title, text)
        self.gotchas = gotchas or ""   # prompt variant e: the gotcha rules served as passages (prompt.gotchas_for_index)
        self.tool_names = list(tool_names or [])
        self.index: Optional[BM25Index] = None
        self.log = log or (lambda msg: None)

    def _doc_files(self) -> List[Tuple[str, str]]:
        files: List[Tuple[str, str]] = []
        for d in self.docs_dirs:
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                if name.endswith(".html"):
                    rel = "user/commands/%s" % name if d.rstrip("/\\").endswith("commands") else "user/%s" % name
                    files.append((os.path.join(d, name), rel))
        return files

    @property
    def content_key(self) -> str:
        """A hash of every input of the index: an edit that keeps the sizes still gives a new key. Doc pages
        enter by name, size and modification time (reading 150 pages on every start costs more than it saves)."""
        if getattr(self, "_content_key", None) is None:
            h = hashlib.sha256()

            def feed(tag: str, obj: Any) -> None:
                h.update(tag.encode("utf-8") + b"\0")
                h.update(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8") + b"\0")

            docs = []
            for path, rel in self._doc_files():
                try:
                    st = os.stat(path)
                    docs.append((rel, st.st_size, int(st.st_mtime)))
                except OSError:
                    docs.append((rel, -1, 0))
            feed("format", INDEX_FORMAT)
            feed("cheatsheet", self.cheatsheet)
            feed("workflows", self.workflows)
            feed("recipes", self.recipe_library)
            feed("guides", [list(g) for g in self.guides])
            feed("registry", self.registry_entries)
            if self.gotchas:
                feed("gotchas", self.gotchas)
            feed("docs", docs)
            self._content_key = h.hexdigest()[:16]
        return self._content_key

    @property
    def cache_path(self) -> str:
        return os.path.join(self.cache_dir, "index-%s-%s.json.gz" % (self.version_key, self.content_key))

    def _prune(self) -> None:
        """An index of this ChimeraX version built from other inputs is never read again. Other versions keep
        theirs: the cache folder is shared by every ChimeraX installed. Known gap: two installs of the same
        ChimeraX version with different inputs (a release and a dev checkout) share the prefix and remove
        each other's index at every start, so each rebuilds; nothing is lost but the time."""
        keep = os.path.basename(self.cache_path)
        prefix = "index-%s-" % self.version_key
        try:
            for name in os.listdir(self.cache_dir):
                if name.startswith(prefix) and name.endswith(".json.gz") and name != keep:
                    os.remove(os.path.join(self.cache_dir, name))
        except OSError:
            pass

    def ensure(self, progress=None) -> BM25Index:
        if self.index is not None:
            return self.index
        try:
            self.index, meta = BM25Index.load(self.cache_path)
            if meta.get("key") not in (None, self.content_key):
                raise ValueError("index built from other inputs")
            self.log("Pellaeon: loaded docs index (%d passages)" % len(self.index.chunks))
            return self.index
        except Exception:
            pass
        return self.rebuild(progress)

    def rebuild(self, progress=None) -> BM25Index:
        chunks: List[Chunk] = []
        files = self._doc_files()
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
        if self.gotchas:
            chunks.extend(gotcha_chunks(self.gotchas, set(self.cheatsheet) | set(self.tool_names)))
        for name, usage in self.registry_entries.items():
            chunks.append(Chunk(0, name.split()[0], name, "usage", usage,
                                "help:user/commands/%s.html" % name.split()[0], "registry", 1.2))
        idx = BM25Index()
        idx.build(chunks)
        self.index = idx
        try:
            idx.save(self.cache_path, {"version": self.version_key, "key": self.content_key, "files": len(files)})
            self._prune()
        except Exception as e:
            self.log("Pellaeon: could not cache index: %s" % e)
        self.log("Pellaeon: indexed %d doc pages into %d passages" % (len(files), len(chunks)))
        return idx

    def search(self, query: str, k: int = 5, budget: int = PASSAGE_CHARS) -> List[Dict[str, Any]]:
        """The best passages for the query, each presented examples-first and cut to the budget (0 = whole)."""
        idx = self.ensure()
        out = []
        for score, c in (idx.search_with_gotchas(query, k) if self.gotchas else idx.search(query, k)):
            out.append({"command": c.command, "title": c.title, "section": c.section,
                        "text": present(c, budget) if budget else c.text,
                        "url": c.url, "score": round(score, 2), "kind": c.kind})
        return out

    def command_directory(self) -> List[Tuple[str, str]]:
        return [(cmd, info.get("purpose", "")) for cmd, info in sorted(self.cheatsheet.items())]
