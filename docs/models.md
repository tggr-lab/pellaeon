# Tested models

Scores are from Pellaeon's own request sets, run through the real panel and checked by the program (the resulting ChimeraX state, the commands run, the cards shown). Local models ran on an RTX 4070 Super (12 GB). Last run: 2026-10-02, Pellaeon 0.2.8.

## Which model

| Want | Pick | Notes |
|---|---|---|
| Highest score in the feature comparison | ChatGPT `gpt-6-sol` | Plus/Pro subscription, sign in from Settings; about 10 s a request |
| Faster, same account | ChatGPT `gpt-6-luna` | about 7 s a request |
| Free, nothing to install | Mistral `ministral-8b-latest` | free key, about 3 s a request; vision |
| Free, alternative | Gemini `gemini-flash-lite-latest` | free key, 15 requests a minute; vision; 32/32 on the precise-request set below |
| Private, on a 12 GB GPU | Ollama `gemma4:12b` | runs on your computer |
| Private, faster | Ollama `qwen3.5:9b` | about 2.5 s a request; a few more mistakes on display and ligand requests |

## Scores

| Model | Everyday requests (152 × 3) | ChimeraX features (31 × 3) | Feature comparison (19 × 2) | Time per request |
|---|---|---|---|---|
| `gpt-6-sol` | | | 35/38 | 11 s |
| `gpt-6-luna` | | | 30/38 | 8 s |
| `ministral-8b-latest` | 376/456 | 69/93 | | 3 s |
| `gemini-flash-lite-latest` | | 28/31 (one repeat) | 29/38 | 3 s |
| `gemma4:12b` | 383/456 | 80/93 | 21/38 | 5 s |
| `qwen3.5:9b` | 357/456 | | | 2.5 s |

- **Everyday requests:** 152 requests mined from RBVI tutorials, the chimerax-users list and courses (open, colour, select, measure, label, compare, save). Used during development, so these scores are tuned.
- **ChimeraX features:** 31 requests covering the [ChimeraX feature highlights](https://www.rbvi.ucsf.edu/chimerax/features.html) (maps, morphs, glycans, symmetry, membranes, MD, ...); three repeats per model, one for Gemini.
- **Feature comparison:** 19 of the harder feature requests (map fitting, membrane orientation, palettes, worms, sequence, rotamers), run twice per model.
- Time is the median per request, from the same runs. A blank cell means that set was not run on that model.
- Differences of a few requests between runs are noise: two runs of the same code differ by up to 7 of 152.

**Precise requests (32, Pellaeon 0.2.8, one repeat):** chain scopes, gaps, insertion codes, signed numbering, conditionals, preservation. `gpt-6-sol`, `gpt-6-luna`, `claude-sonnet-5.5` and `gemini-flash-lite` 32/32; `gemma4:12b` 31/32; `claude-haiku-4.5` 30/32; `ministral-8b` 28/32. Median response latency: Mistral 1.9 s, Gemini 2.1 s, Haiku 3.1 s, Sonnet 5.5 3.6 s, gemma 4.2 s, luna 5.1 s, sol 8.5 s. These 32 requests were used during development, so the scores measure regression, not an unseen benchmark.

## Free-tier limits

As seen on new free accounts in September 2026; providers change them, and paid accounts differ.

| Provider | Free allowance | In practice |
|---|---|---|
| Mistral | about 190 requests a minute | no limit reached in any run |
| Gemini Flash-Lite | 15 a minute, plus a daily cap | enough for one person; a long run can hit the daily cap |
| Gemini Flash | 5 a minute | not usable: every miss was a rate limit |
| Groq | 7,000 to 8,000 input tokens a minute | not usable: about one request a minute |
| OpenRouter `openrouter/free` | 50 requests a day | about a dozen questions |

One question is two to four requests of a few thousand tokens each (the state of your structures and the tool definitions travel with it).

## Local models

Basic set of 38 requests, Pellaeon 0.2.0, September 2026. Larger did not mean better on this card: the 20B models spill out of 12 GB and slow down.

| Model | Basic set | Time | Notes |
|---|---|---|---|
| `qwen3:8b` | 38/38 | 3 s | |
| `qwen3:4b` | 37/38 | 20 s | 2.5 GB, the smallest that works |
| `gemma4:12b` | 37/38 | 8 s | the default; vision |
| `gemma4:e4b` | 37/38 | 5 s | vision, fits small cards |
| `granite4.1:8b` | 37/38 | 5 s | |
| `qwen3:14b` | 36/38 | 8 s | |
| `gpt-oss:20b` | 36/38 | 15 s | spills out of 12 GB |
| `ministral-3:3b` | 34/38 | 2 s | 3 GB; vision |
| `llama3.1:8b` | 33/38 | | |

Below 30/38 and not recommended: `command-r7b`, `cogito:8b`, `granite4:1b`, `qwen2.5:3b`, `granite4:micro`, `mistral-nemo:12b`, `lfm2.5:8b`, `llama3.2:3b`, `llama3-groq-tool-use:8b`, `granite3.3:2b`, `smollm2:1.7b`, `nemotron-mini:4b`. No tool calling, so unusable: `gemma3:12b`, `falcon3:3b`, `exaone3.5:2.4b`.

Ollama keeps the previous model loaded for a few minutes; on a full card the new one runs on the CPU until the old one unloads.


