"""Provider presets shown in the setup wizard, and the provider factory."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import Provider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider
from .anthropic import AnthropicProvider
from .gemini import GeminiProvider

PROVIDER_CLASSES = {
    "ollama": OllamaProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "openai": OpenAICompatProvider,
}

# Each preset: which adapter, where, default model, how to get a key, cost note.
PRESETS: List[Dict[str, Any]] = [
    {
        "id": "gemini", "provider": "gemini", "label": "Google Gemini (free, nothing to install)",
        "base_url": "https://generativelanguage.googleapis.com", "model": "gemini-3.6-flash",
        "key_env": "GOOGLE_API_KEY", "free": True, "needs_key": True,
        "models_hint": ["gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview"],
        "blurb": "Easiest start: get a free key at aistudio.google.com (2 minutes, no credit card), paste it here. Flash models are free with daily limits.",
        "key_url": "https://aistudio.google.com/apikey",
    },
    {
        "id": "ollama", "provider": "ollama", "label": "Ollama (local, free, private)",
        "base_url": "http://localhost:11434", "model": "qwen3:8b", "key_env": "",
        "free": True, "needs_key": False,
        "models_hint": ["qwen3:8b", "qwen3:4b", "gemma4:e4b", "llama3.1:8b", "qwen3:14b"],
        "blurb": "Private: the model runs on your own computer. Needs the free Ollama app from ollama.com and a one-time model download "
                 "(qwen3:8b for a gaming GPU, qwen3:4b for laptops; slow without a GPU).",
        "key_url": "https://ollama.com/download",
    },
    {
        "id": "anthropic", "provider": "anthropic", "label": "Anthropic Claude (best quality)",
        "base_url": "https://api.anthropic.com", "model": "claude-opus-5",
        "key_env": "ANTHROPIC_API_KEY", "free": False, "needs_key": True,
        "models_hint": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
        "blurb": "Pay-as-you-go API key from console.anthropic.com. Claude Pro/Max subscriptions cannot be used here.",
        "key_url": "https://console.anthropic.com/settings/keys",
    },
    {
        "id": "openai", "provider": "openai", "label": "OpenAI",
        "base_url": "https://api.openai.com/v1", "model": "gpt-4.1",
        "key_env": "OPENAI_API_KEY", "free": False, "needs_key": True,
        "models_hint": ["gpt-4.1", "gpt-4.1-mini", "gpt-5"],
        "blurb": "Pay-as-you-go API key from platform.openai.com.",
        "key_url": "https://platform.openai.com/api-keys",
    },
    {
        "id": "openrouter", "provider": "openai", "label": "OpenRouter (many models, free ones too)",
        "base_url": "https://openrouter.ai/api/v1", "model": "openrouter/free",
        "key_env": "OPENROUTER_API_KEY", "free": True, "needs_key": True,
        "models_hint": ["openrouter/free", "anthropic/claude-sonnet-4.5", "google/gemini-2.5-flash", "qwen/qwen3-32b:free"],
        "blurb": "One key for dozens of models. 'openrouter/free' routes to a free model that supports tools (50 requests/day free).",
        "key_url": "https://openrouter.ai/keys",
    },
    {
        "id": "groq", "provider": "openai", "label": "Groq (fast, free tier)",
        "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile",
        "key_env": "GROQ_API_KEY", "free": True, "needs_key": True,
        "models_hint": ["llama-3.3-70b-versatile", "qwen/qwen3-32b", "meta-llama/llama-4-scout-17b-16e-instruct"],
        "blurb": "Very fast inference with a generous free tier.",
        "key_url": "https://console.groq.com/keys",
    },
    {
        "id": "lmstudio", "provider": "openai", "label": "LM Studio / llama.cpp / vLLM (local server)",
        "base_url": "http://localhost:1234/v1", "model": "", "key_env": "",
        "free": True, "needs_key": False,
        "models_hint": [],
        "blurb": "Any local server that speaks the OpenAI protocol. Start the server, then pick a model.",
        "key_url": "https://lmstudio.ai",
    },
]


def preset_by_id(pid: str) -> Optional[Dict[str, Any]]:
    for p in PRESETS:
        if p["id"] == pid:
            return p
    return None


def make_provider(provider: str, model: str, api_key: str = "", base_url: str = "",
                  timeout: float = 180.0, options: Optional[Dict[str, Any]] = None) -> Provider:
    cls = PROVIDER_CLASSES.get(provider)
    if cls is None:
        raise ValueError("unknown provider: %s" % provider)
    return cls(model=model, api_key=api_key, base_url=base_url, timeout=timeout, options=options)
