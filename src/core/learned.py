"""Fixes learned from earlier sessions: a command ChimeraX refused, and the command with the same first
word that ran afterwards in the same turn.

The model repeats last session's mistakes because nothing carries over between chats. The parser gives a
clean signal:
refused, then accepted. Entries live in a small JSON file, are offered to the model only when the same
command family comes up, and are dropped if the user complains right after the accepted command ran.
No ChimeraX imports: unit-testable on plain Python."""
from __future__ import annotations

import difflib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

MAX_ENTRIES = 200
MAX_LINES = 5           # offered per turn
MAX_CHARS = 1200        # about 300 tokens
MIN_SIMILARITY = 0.6    # the accepted command must be a corrected form of the refused one, not another command
_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*")


def first_word(cmd: str) -> str:
    m = _WORD_RE.match((cmd or "").strip())
    return m.group(0).lower() if m else ""


def _scrub(cmd: str) -> str:
    """Replace path-like strings and URLs before storing or sharing."""
    cmd = re.sub(r"https?://\S+", "<url>", cmd)
    cmd = re.sub(r"(?<![\w#:/])(?:[A-Za-z]:\\|~?/)[^\s'\"]+", "<path>", cmd)
    return cmd.strip()


class LearnedFixes:
    def __init__(self, path: Optional[str], enabled: bool = True):
        self.path = path
        self.enabled = enabled
        self.entries: List[Dict[str, Any]] = []
        self._recent: List[Dict[str, Any]] = []   # added in the current turn, revocable on a complaint
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self.entries = [e for e in (data.get("entries") if isinstance(data, dict) else data) or [] if isinstance(e, dict)]
            except Exception:  # noqa: BLE001  a damaged file is an empty store, not a crash
                self.entries = []

    # ---- writing
    def save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"format": 1, "entries": self.entries}, f, indent=1)
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001
            pass

    def add(self, refused: str, accepted: str, error: str = "") -> bool:
        """Record refused -> accepted; returns True when something new was stored."""
        if not self.enabled:
            return False
        refused, accepted = _scrub(refused), _scrub(accepted)
        w = first_word(refused)
        if not w or w != first_word(accepted) or refused == accepted or len(accepted) > 200 or len(refused) > 200:
            return False
        if difflib.SequenceMatcher(None, refused.lower(), accepted.lower()).ratio() < MIN_SIMILARITY:
            return False   # "distance a b c" -> "distance style color gold" is not a fix
        for e in self.entries:
            if e["refused"] == refused:
                # one lesson per refused command: the first accepted form, seen again
                e["count"] = int(e.get("count", 1)) + 1
                e["last"] = int(time.time())
                self.save()
                return False
        entry = {"word": w, "refused": refused, "accepted": accepted, "error": (error or "")[:160].strip(),
                 "count": 1, "last": int(time.time())}
        self.entries.append(entry)
        self._recent.append(entry)
        if len(self.entries) > MAX_ENTRIES:
            self.entries.sort(key=lambda e: (e.get("count", 1), e.get("last", 0)))
            del self.entries[: len(self.entries) - MAX_ENTRIES]
        self.save()
        return True

    def start_turn(self) -> None:
        self._recent = []

    def revoke_recent(self) -> int:
        """The user complained right after: what was just learned is not a fix."""
        n = 0
        for e in self._recent:
            if e in self.entries:
                self.entries.remove(e)
                n += 1
        self._recent = []
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
        self.save()

    # ---- reading
    def for_words(self, words, exclude: str = "") -> List[str]:
        """Lines for the command families in `words`, best-known first, within the token budget."""
        if not self.enabled or not self.entries:
            return []
        want = {w for w in (first_word(x) for x in words) if w}
        hits = [e for e in self.entries if e["word"] in want and e["accepted"] != _scrub(exclude)]
        hits.sort(key=lambda e: (-int(e.get("count", 1)), -int(e.get("last", 0))))
        out, used = [], 0
        for e in hits[:MAX_LINES]:
            line = "`%s` was refused%s; `%s` ran." % (e["refused"], (" (%s)" % e["error"]) if e.get("error") else "", e["accepted"])
            if used + len(line) > MAX_CHARS:
                break
            out.append(line)
            used += len(line)
        return out

    def for_failure(self, refused: str) -> List[str]:
        """Lines for a command that was just refused: same family, the closest refusals first."""
        w = first_word(refused)
        if not w:
            return []
        lines = self.for_words([refused])
        # a stored refusal that shares most words with this one goes first
        toks = set(refused.lower().split())
        def closeness(line: str) -> int:
            m = re.match(r"`([^`]*)`", line)
            return -len(toks & set((m.group(1) if m else "").lower().split()))
        return sorted(lines, key=closeness)

    def export(self) -> Dict[str, Any]:
        """What "share with the project" sends: the entries as stored (already scrubbed), nothing else."""
        return {"format": 1, "entries": [{k: e[k] for k in ("word", "refused", "accepted", "error", "count")} for e in self.entries]}
