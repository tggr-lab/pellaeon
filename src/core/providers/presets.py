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
        "id": "mistral", "provider": "openai", "label": "Mistral (free tier, no credit card)",
        "base_url": "https://api.mistral.ai/v1", "model": "ministral-8b-latest",
        "key_env": "MISTRAL_API_KEY", "free": True, "needs_key": True,
        "models_hint": ["ministral-8b-latest", "ministral-14b-latest", "ministral-3b-latest"],
        "blurb": "A free key from console.mistral.ai, no credit card, and by far the most room of any free tier: "
                 "about 190 requests a minute, where the others allow 15 a minute or 50 a day. The Ministral models answer "
                 "on a free key; mistral-small and mistral-medium need billing. Note that the free tier is for evaluation "
                 "and Mistral may use your requests to improve their models: for unpublished structures use a local model.",
        "key_url": "https://console.mistral.ai/api-keys",
    },
    {
        "id": "gemini", "provider": "gemini", "label": "Google Gemini (free, nothing to install)",
        "base_url": "https://generativelanguage.googleapis.com", "model": "gemini-flash-lite-latest",
        "key_env": "GOOGLE_API_KEY", "free": True, "needs_key": True,
        "models_hint": ["gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-flash-latest", "gemini-3.8-flash"],
        "blurb": "Easiest start: get a free key at aistudio.google.com (2 minutes, no credit card), paste it here. "
                 "Flash-Lite is the default because the free tier allows it 15 requests a minute, against 5 for Flash; "
                 "Pellaeon waits and retries when a limit hits. Pro models answer only with billing enabled.",
        "key_url": "https://aistudio.google.com/apikey",
    },
    {
        "id": "ollama", "provider": "ollama", "label": "Ollama (local, free, private)",
        "base_url": "http://localhost:11434", "model": "gemma4:12b", "key_env": "",
        "free": True, "needs_key": False,
        "models_hint": ["gemma4:12b", "qwen3:8b", "gemma4:e4b", "qwen3:4b", "qwen3:14b"],
        "blurb": "Private: the model runs on your own computer. Needs the free Ollama app from ollama.com and a one-time model download "
                 "(gemma4:12b for a 12 GB GPU, qwen3:8b for 8 GB, qwen3:4b for laptops; slow without a GPU).",
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
        "id": "openrouter", "provider": "openai", "label": "OpenRouter (many models, free ones too)", "compact_prompt": True,
        "base_url": "https://openrouter.ai/api/v1", "model": "openrouter/free",
        "key_env": "OPENROUTER_API_KEY", "free": True, "needs_key": True,
        "models_hint": ["openrouter/free", "nvidia/nemotron-3-super-120b-a12b:free", "qwen/qwen3.8-27b:free",
                        "google/gemma-4-31b-it:free", "anthropic/claude-sonnet-4.5"],
        "blurb": "One key for dozens of models. 'openrouter/free' routes to a free model that can use tools. "
                 "The free models allow 50 requests a day in total \u2014 roughly a dozen questions \u2014 so it suits trying things out "
                 "rather than a working day; paid models on the same key have no such cap.",
        "key_url": "https://openrouter.ai/keys",
    },
    {
        "id": "groq", "provider": "openai", "label": "Groq (fast, free tier)", "compact_prompt": True,
        "base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-20b",
        "key_env": "GROQ_API_KEY", "free": True, "needs_key": True,
        "models_hint": ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"],
        "blurb": "Very fast answers. The free tier allows 1000 requests a day but only 8000 tokens a minute, "
                 "which is about one Pellaeon request per minute; Pellaeon waits out the limit rather than failing.",
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
