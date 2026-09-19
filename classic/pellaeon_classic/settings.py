"""JSON-backed settings for the classic edition (same attribute names as the ChimeraX Settings)."""
from __future__ import annotations

import json
import os
import sys

DEFAULTS = {
    "configured": False, "preset": "ollama", "provider": "ollama", "model": "qwen3:8b", "base_url": "",
    "autonomy": "auto", "allow_python": False, "readable_labels": True, "vision": False, "think": False, "temperature": 0.2,
    "docs_per_turn": 6, "show_tool_details": True, "last_conversation": "", "effort": "",
    "chimera_port": 0, "chimera_path": "",
}


def user_dirs():
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        root = os.path.join(base, "Pellaeon-Classic")
        return {"data": root, "config": root, "cache": os.path.join(root, "cache")}
    if sys.platform == "darwin":
        root = os.path.join(home, "Library", "Application Support", "Pellaeon-Classic")
        return {"data": root, "config": root, "cache": os.path.join(home, "Library", "Caches", "Pellaeon-Classic")}
    return {"data": os.path.join(home, ".local", "share", "pellaeon-classic"),
            "config": os.path.join(home, ".config", "pellaeon-classic"),
            "cache": os.path.join(home, ".cache", "pellaeon-classic")}


class JsonSettings:
    def __init__(self, path: str):
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_data", dict(DEFAULTS))
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._data.update(json.load(f))
        except Exception:
            pass

    def __getattr__(self, name):
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self._data[name] = value

    def save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=1)
