"""Fixes learned from earlier sessions: a command ChimeraX refused, and the corrected form of the same
operation that ran afterwards in the same turn and changed something.

The model repeats last session's mistakes because nothing carries over between chats. The parser gives a
clean signal: refused, then accepted. An entry is syntax evidence for one operation, so it is kept only when
the accepted command does the same thing to the same target (same command, the same option words give or
take a spelling, the same models and residues) and when it had an effect. Each entry carries the ChimeraX
version and the registration of its command; a different version or registration retires it (entries from before
they carried a version are stamped with the running one). An entry is
offered when the same kind of refusal happens again, and is removed when the user complains about, or undoes,
the turn that made it.
No ChimeraX imports: unit-testable on plain Python."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

FORMAT = 2
MAX_ENTRIES = 200
MAX_LINES = 5           # offered per turn by family (for_words)
MAX_FAILURE_LINES = 2   # offered with one refusal
MAX_CHARS = 1200        # about 300 tokens
_WORD_RE = re.compile(r"^(?:[A-Za-z]|[0-9](?=[A-Za-z]))[A-Za-z0-9]*")   # a leading digit only in `2dlabels` (as safety.first_word)
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
# words that name atoms rather than say what to do with them
_SPEC_WORDS = {"sel", "ligand", "protein", "solvent", "ions", "ion", "nucleic", "helix", "strand", "coil", "all",
               "main", "side", "sidechain", "backbone", "water", "h", "het", "polymer", "dna", "rna"}


def first_word(cmd: str) -> str:
    m = _WORD_RE.match((cmd or "").strip())
    return m.group(0).lower() if m else ""


def _scrub(cmd: str) -> str:
    """Replace path-like strings and URLs before storing or sharing."""
    cmd = re.sub(r"https?://\S+", "<url>", cmd)
    cmd = re.sub(r"([\"'])(?:[A-Za-z]:\\|~?/)[^\"']*\1", "<path>", cmd)   # a quoted path may hold spaces
    cmd = re.sub(r"(?:/home|/Users)/[^/\s]+|[A-Za-z]:\\Users\\[^\\\s]+", "<home>", cmd)   # the user name goes first
    cmd = re.sub(r"(?<![\w#:/])(?:[A-Za-z]:\\|~?/)[^\s'\"]+", "<path>", cmd)
    return cmd.strip()


def _is_spec(tok: str) -> bool:
    return any(ch in tok for ch in "#/:@") or tok[:1] in ("~", "(", "!") or tok.lower() in _SPEC_WORDS


# a model spec: #1, #1.2, #1-3, #1,3; not the digits of a colour like #90a4ae80
_MODEL_RE = re.compile(r"#!?(\d+(?:\.\d+)*(?:[-,]\d+(?:\.\d+)*)*)(?![0-9A-Za-z])")


def _models(cmd: str) -> Set[str]:
    """The models a line addresses, one id each: `#1-3` is 1, 2 and 3; `#1.1` is the submodel, not #1."""
    out: Set[str] = set()
    for hit in _MODEL_RE.findall(cmd or ""):
        for part in hit.split(","):
            m = re.fullmatch(r"(\d+)-(\d+)", part)
            if m:
                a, b = sorted(int(x) for x in m.groups())
                out.update(str(i) for i in range(a, min(b, a + 50) + 1))
            elif part:
                out.add(part)
    return out


def parts(cmd: str) -> Tuple[str, Set[str], Set[str], Set[str]]:
    """(command word, option words and values, model ids, residue numbers) of a command line."""
    toks = [t for t in re.split(r"[\s=]+", (cmd or "").strip()) if t]
    word = first_word(cmd)
    ops: Set[str] = set()
    residues: Set[str] = set()
    for t in toks[1:]:
        t = t.strip(",;")
        if not t:
            continue
        if _is_spec(t):
            if ":" in t:
                residues.update(re.findall(r"\d+", t.split(":", 1)[1]))
            continue
        if _NAME_RE.match(t):
            ops.add(t.lower())
    return word, ops, _models(cmd), residues


def _chains(cmd: str) -> Set[str]:
    """Chain ids written as /A in the line's specs."""
    out: Set[str] = set()
    for t in (cmd or "").split():
        if _is_spec(t):
            out.update(c.upper() for c in re.findall(r"/([A-Za-z0-9]+)", t))
    return out


def _key_entries(cmd: str) -> Set[str]:
    """The colour:label entries of a `key` command, lower-cased."""
    if first_word(cmd) != "key":
        return set()
    return {t.strip(",;").lower() for t in (cmd or "").split()[1:] if ":" in t}


def _same_entry(a: str, b: str) -> bool:
    """Two key entries mean the same when the colour is the same give or take a spelling and the label is the same."""
    ca, la = a.split(":", 1)
    cb, lb = b.split(":", 1)
    return _alike(ca, cb) and la.strip() == lb.strip()


def _alike(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)):   # thick / thickness
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.75               # bleu / blue


def _numbers(cmd: str) -> Set[str]:
    """Numeric values of a command line, outside atom specs (#1.1, :87) and names (h12, modeHelix)."""
    out: Set[str] = set()
    for tok in re.split(r"[\s=]+", cmd or ""):
        if not tok or _is_spec(tok):       # `#1,2,3` is one spec, not the values 2 and 3
            continue
        for t in tok.split(","):
            if re.fullmatch(r"[-+]?\d+(\.\d+)?%?", t):
                out.add(t.lstrip("+"))
    return out


def _covered(xs: Set[str], ys: Set[str]) -> float:
    return sum(1 for x in xs if any(_alike(x, y) for y in ys)) / len(xs) if xs else 1.0


def same_operation(refused: str, accepted: str) -> str:
    """'' when `accepted` is a corrected form of `refused`, else why not. Same command word, mostly the same
    option words (spelling and order may differ), the same models and residues."""
    w1, o1, m1, r1 = parts(refused)
    w2, o2, m2, r2 = parts(accepted)
    if not w1 or w1 != w2:
        return "another command"
    if bool(o1) != bool(o2) or _covered(o1, o2) < 0.5 or _covered(o2, o1) < 0.5:
        return "another operation"     # `surface #1 style mesh` -> `surface #1` made a surface, it styled nothing
    dropped = {x for x in o1 if not any(_alike(x, y) for y in o2)}
    added = {y for y in o2 if not any(_alike(x, y) for x in o1)}
    if dropped and added:
        return "another operation"     # a word replaced by an unlike one: `modeStrand` -> `modeHelix` styles other residues
    if _numbers(refused) != _numbers(accepted):
        return "another value"         # `transparency #1 50` -> `transparency #1 0` is a different look, not a corrected form
    if m1 and m2 and m1 != m2:
        return "another model"         # #2 for #1, #1.2 for #1.1, #1-2 for #1-3
    if bool(r1) != bool(r2) or (r1 and r2 and not r1 & r2):
        return "another target"        # residues dropped, added or changed
    c1, c2 = _chains(refused), _chains(accepted)
    if c1 and c2 and not c1 & c2:
        return "another target"        # `#1/A` -> `#1/B` colours another chain
    k1, k2 = _key_entries(refused), _key_entries(accepted)
    if any(not any(_same_entry(x, y) for y in k2) for x in k1) and any(not any(_same_entry(x, y) for x in k1) for y in k2):
        return "another target"        # a key entry replaced by one with another label: a different legend, not a fix
    return ""


def error_kind(error: str) -> str:
    """The parser's complaint without what is specific to one command line (quoted text, specs, numbers)."""
    e = (error or "").lower()
    e = re.sub(r"\"[^\"]*\"|'[^']*'", " ", e)
    e = re.sub(r"\S*[#/:@]\S*|\d+(\.\d+)?", " ", e)
    return " ".join(re.findall(r"[a-z]+", e)[:8])


def _key(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:10]


def op_name(cmd: str) -> str:
    """The registered name a command most likely runs under: its first two words when the second is a word."""
    toks = (cmd or "").split()
    if len(toks) > 1 and _NAME_RE.match(toks[1]) and not _is_spec(toks[1]):
        return "%s %s" % (toks[0].lower(), toks[1].lower())
    return toks[0].lower() if toks else ""


class LearnedFixes:
    def __init__(self, path: Optional[str], enabled: bool = True, version: Optional[str] = None,
                 usage: Optional[Callable[[str], str]] = None):
        """`version`: the running ChimeraX; entries recorded under another one are dropped. `usage(name)`: the
        registered usage text of a command; an entry whose command is registered differently now is dropped."""
        self.path = path
        self.enabled = enabled
        self.version = version
        self._usage = usage
        self._usage_keys: Dict[str, str] = {}
        self.entries: List[Dict[str, Any]] = []
        self.last_reason = ""                       # why the last add() stored nothing
        self._recent: List[Dict[str, Any]] = []     # added in the current turn
        self._previous: List[Dict[str, Any]] = []   # added in the turn before
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self.entries = [e for e in (data.get("entries") if isinstance(data, dict) else data) or [] if isinstance(e, dict)]
            except Exception:  # noqa: BLE001  a damaged file is an empty store, not a crash
                self.entries = []
        if version is not None:
            self._adopt(version)

    def bind(self, version: Optional[str], usage: Optional[Callable[[str], str]] = None) -> None:
        """Attach the running ChimeraX after construction (the panel creates the store before the executor)."""
        self.version = version
        self._usage = usage
        self._usage_keys = {}
        if version is not None:
            self._adopt(version)

    def _adopt(self, version: str) -> None:
        """Entries from another ChimeraX are retired. Entries from before they carried a version (format 1) were
        learned on this computer, most likely under this ChimeraX: they are stamped with it once, not thrown away."""
        n, stamped = len(self.entries), 0
        for e in self.entries:
            if "chimerax" not in e:
                e["chimerax"] = version
                stamped += 1
        self.entries = [e for e in self.entries if e.get("chimerax") == version]
        if stamped or len(self.entries) != n:
            self.save()

    def _usage_key(self, name: str) -> Optional[str]:
        if self._usage is None or not name:
            return None
        if name not in self._usage_keys:
            try:
                self._usage_keys[name] = _key(self._usage(name) or "")
            except Exception:  # noqa: BLE001
                self._usage_keys[name] = ""
        return self._usage_keys[name] or None

    def _current(self, e: Dict[str, Any]) -> bool:
        """Still true here: same ChimeraX, same registration of the command."""
        if self.version is not None and e.get("chimerax") != self.version:
            return False
        if e.get("usage"):
            now = self._usage_key(e.get("op") or e.get("word", ""))
            if now is not None and now != e["usage"]:
                return False
        return True

    # ---- writing
    def save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"format": FORMAT, "entries": self.entries}, f, indent=1)
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001
            pass

    def add(self, refused: str, accepted: str, error: str = "") -> bool:
        """Record refused -> accepted; returns True when something new was stored (else `last_reason` says why).
        The caller passes only an accepted command that had an effect."""
        self.last_reason = ""
        if not self.enabled:
            self.last_reason = "off"
            return False
        refused, accepted = _scrub(refused), _scrub(accepted)
        if refused == accepted or len(accepted) > 200 or len(refused) > 200 or not first_word(refused):
            self.last_reason = "not a pair"
            return False
        why = same_operation(refused, accepted)
        if why:
            self.last_reason = why
            return False
        for e in self.entries:
            if e["refused"] == refused:
                # one lesson per refused command: the first accepted form, seen again
                e["count"] = int(e.get("count", 1)) + 1
                e["last"] = int(time.time())
                self.save()
                self.last_reason = "known"
                return False
        name = op_name(accepted)
        entry = {"word": first_word(refused), "op": name, "refused": refused, "accepted": accepted,
                 "error": _scrub((error or "")[:160]), "count": 1, "last": int(time.time())}
        if self.version is not None:
            entry["chimerax"] = self.version
        uk = self._usage_key(name)
        if uk:
            entry["usage"] = uk
        self.entries.append(entry)
        self._recent.append(entry)
        if len(self.entries) > MAX_ENTRIES:
            self.entries.sort(key=lambda e: (e.get("count", 1), e.get("last", 0)))
            del self.entries[: len(self.entries) - MAX_ENTRIES]
        self.save()
        return True

    def start_turn(self) -> None:
        self._previous = self._recent
        self._recent = []

    def revoke_recent(self) -> int:
        """What the last turn and this one recorded is not a fix: the user complained about it or undid it."""
        n = 0
        for e in self._previous + self._recent:
            if e in self.entries:
                self.entries.remove(e)
                n += 1
        self._previous, self._recent = [], []
        if n:
            self.save()
        return n

    def forget(self, index: int) -> bool:
        if 0 <= index < len(self.entries):
            del self.entries[index]
            self.save()
            return True
        return False

    def clear(self) -> None:
        self.entries = []
        self._recent = []
        self._previous = []
        self.save()

    # ---- reading
    @staticmethod
    def _line(e: Dict[str, Any]) -> str:
        err = _scrub(e.get("error") or "")
        return "`%s` was refused%s; `%s` ran." % (e["refused"], (" (%s)" % err) if err else "", e["accepted"])

    def _valid(self, words: Set[str]) -> List[Dict[str, Any]]:
        """Entries of these command words that still hold; the others are removed."""
        hits = [e for e in self.entries if e.get("word") in words]
        stale = [e for e in hits if not self._current(e)]
        if stale:
            for e in stale:
                self.entries.remove(e)
            self.save()
        return [e for e in hits if e not in stale]

    def for_words(self, words, exclude: str = "") -> List[str]:
        """Lines for the command families in `words`, best-known first, within the token budget."""
        if not self.enabled or not self.entries:
            return []
        want = {w for w in (first_word(x) for x in words) if w}
        hits = [e for e in self._valid(want) if e["accepted"] != _scrub(exclude)]
        hits.sort(key=lambda e: (-int(e.get("count", 1)), -int(e.get("last", 0))))
        out, used = [], 0
        for e in hits[:MAX_LINES]:
            line = self._line(e)
            if used + len(line) > MAX_CHARS:
                break
            out.append(line)
            used += len(line)
        return out

    def for_failure(self, refused: str, error: str = "") -> List[str]:
        """Lines for a command that was just refused: entries for the same refusal only, that is the same
        operation (command word and option words) refused with the same kind of error, or with the same option
        words when either error is unknown."""
        if not self.enabled or not self.entries:
            return []
        w, ops, _m, _r = parts(_scrub(refused))
        if not w:
            return []
        kind = error_kind(error)
        scored = []
        for e in self._valid({w}):
            _w, eops, _m2, _r2 = parts(e["refused"])
            same_ops = _covered(ops, eops) >= 0.5 and _covered(eops, ops) >= 0.5 and bool(ops) == bool(eops)
            ekind = error_kind(e.get("error") or "")
            if kind and ekind:
                if kind != ekind or not same_ops:
                    continue
            elif not same_ops:
                continue
            scored.append((-len(ops & eops), -int(e.get("count", 1)), -int(e.get("last", 0)), self._line(e)))
        scored.sort()
        return [s[-1] for s in scored[:MAX_FAILURE_LINES]]

    def export(self) -> Dict[str, Any]:
        """What "share with the project" copies: the entries, scrubbed again here so entries stored by an
        earlier version are covered too, and nothing else."""
        keys = ("word", "refused", "accepted", "error", "count", "chimerax")
        return {"format": FORMAT, "entries": [{k: (_scrub(str(e.get(k, ""))) if k in ("refused", "accepted", "error") else e.get(k))
                                               for k in keys} for e in self.entries]}
