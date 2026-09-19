"""Persistent settings and API-key storage.

Non-secret settings use ChimeraX's Settings class (saved in the ChimeraX
config dir). API keys are kept separately: environment variables win, then
the OS keyring if available, else a private JSON file (mode 0600) in
Pellaeon's config folder. Keys never go into ChimeraX settings or sessions.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from chimerax.core.settings import Settings

from .core.safety import AUTONOMY_AUTO



class PellaeonSettings(Settings):
    AUTO_SAVE = {
        "configured": False,
        "preset": "ollama",          # preset id (see core.providers.presets)
        "provider": "ollama",        # adapter name
        "model": "qwen3:8b",
        "base_url": "",
        "autonomy": AUTONOMY_AUTO,
        "allow_python": False,
        "readable_labels": True,
        "figures_dir": "",           # last folder chosen for figure bundles     # fixed-size, on-top, white-background labels unless the request styles them
        "vision": False,
        "think": False,              # Ollama: let thinking models think (slower)
        "temperature": 0.2,
        "docs_per_turn": 6,
        "show_tool_details": True,
        "last_conversation": "",
        "effort": "",                # Anthropic effort level, "" = default
    }


from .core.secrets import SecretStore, KEY_ENV, masked  # noqa: F401  (re-exported)
