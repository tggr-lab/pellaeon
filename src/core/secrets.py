"""API-key storage shared by both editions: env vars first, then the OS keyring, else a private JSON file."""
from __future__ import annotations

import json
import os
import stat
from typing import Dict

KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
}


class SecretStore:
    def __init__(self, config_dir: str):
        self.path = os.path.join(config_dir, "secrets.json")

    # ---- helpers ----
    def _load(self) -> Dict[str, str]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, data: Dict[str, str]) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass

    @staticmethod
    def _keyring():
        try:
            import keyring  # optional
            return keyring
        except Exception:
            return None

    # ---- API ----
    def get(self, preset_id: str) -> str:
        env = KEY_ENV.get(preset_id)
        if env and os.environ.get(env):
            return os.environ[env].strip()
        kr = self._keyring()
        if kr is not None:
            try:
                v = kr.get_password("pellaeon-chimerax", preset_id)
                if v:
                    return v
            except Exception:
                pass
        return self._load().get(preset_id, "")

    def set(self, preset_id: str, value: str) -> None:
        value = (value or "").strip()
        kr = self._keyring()
        if kr is not None:
            try:
                if value:
                    kr.set_password("pellaeon-chimerax", preset_id, value)
                else:
                    kr.delete_password("pellaeon-chimerax", preset_id)
                # also clear any file copy
                data = self._load()
                if preset_id in data:
                    del data[preset_id]
                    self._save(data)
                return
            except Exception:
                pass
        data = self._load()
        if value:
            data[preset_id] = value
        else:
            data.pop(preset_id, None)
        self._save(data)

    def source(self, preset_id: str) -> str:
        env = KEY_ENV.get(preset_id)
        if env and os.environ.get(env):
            return "environment variable %s" % env
        if self._keyring() is not None:
            try:
                if self._keyring().get_password("pellaeon-chimerax", preset_id):
                    return "system keyring"
            except Exception:
                pass
        if self._load().get(preset_id):
            return "Pellaeon settings"
        return "not set"


def masked(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "…" + key[-4:]
