# Tested models

Which model to run, and what each one did when Pellaeon's own request sets were run through the real panel.

## How these numbers were produced

- Tested on 2026-09-20 and 2026-09-21, with Pellaeon 0.2.0; 0.2.1 changes no model-facing behaviour except the compare_contacts guard.
- Three request sets: a basic set of **38 requests**; a set of **62 follow-ups and edge cases**; a harder set of **24 multi-turn and ambiguous requests**.
- Each request was scored automatically: the checks look at the resulting ChimeraX state, the commands that were executed, and the confirmation or question cards that appeared. Checked by the program, not by eye.
- Local models ran on one machine: RTX 4070 Super (12 GB) with a Ryzen 5950X. Times below are from that machine and will differ on yours.
- Blank cells mean that set was not run on that model.

Reviewing screenshots requires a vision-capable model and the review-the-view option enabled in Settings. Of the models below, `ministral-8b-latest`, `gemini-flash-lite-latest`, `gemma4:e4b` and the local `ministral-3:3b` can do it.

## Choosing a model

| Want | Pick | Why |
|---|---|---|
| Nothing to install | Mistral `ministral-8b-latest` (free) | 38/38 on the basic set, about two seconds a request, and roughly 190 requests a minute on the free key. Vision-capable. |
| Nothing to install, alternative | Gemini `gemini-flash-lite-latest` (free) | 37/38 on the basic set and vision-capable; 15 requests a minute is enough for one person working normally. |
| Private, on a gaming GPU | `qwen3:8b` | The local reference: 38/38 on the basic set, about three seconds a request on a 12 GB card. |
| Private, and able to review screenshots | `gemma4:e4b` | 37/38, and vision plus tool calling in one local model. |
| Private, small GPU or CPU | `qwen3:4b` | 37/38 but slower: 10 to 20 seconds a request. Of the models below 4B parameters tested here, on these request sets, most missed too much to be useful. |
| Long multi-step sessions, paid | Claude, OpenAI, Gemini Pro or Mistral Medium | No free-tier limit to work around. |

## Cloud models

| Model | Basic (38) | Follow-ups (62) | Hard (24) | Speed | Free allowance |
|---|---|---|---|---|---|
| Mistral `ministral-8b-latest` | 38/38 | 55/62 | 23/24 | 2 to 3 s | about 190 requests a minute |
| Mistral `ministral-14b-latest` | 38/38 | 56/62 | | 4 to 5 s | 30 a minute |
| Mistral `ministral-3b-latest` | 34/38 | | | 2 s | 750 a minute |
| Google `gemini-flash-lite-latest` | 37/38 | 57/62 | daily free quota reached during the run, so no score | 7 s | 15 a minute plus a per-day cap |
| Google `gemini-flash-latest` | not usable on the free tier | | | | 5 a minute; every miss was a rate limit, not a mistake |
| Groq `gpt-oss-20b`, `gpt-oss-120b`, `qwen3.8-27b` | not usable on the free tier | | | about 90 s once metered | 7000 to 8000 input tokens a minute, about one request a minute; every miss was a rate limit |
| OpenRouter `openrouter/free` | short set only | | | 15 s | 50 requests a day in total, roughly a dozen questions |

A single request carries the state of your structures and the tool definitions, so it is a few thousand tokens, and one question usually takes two to four requests. On the free tiers the allowance decided the outcome more often than the model did: every failure on Groq and on Gemini Flash was a rate limit rather than a wrong answer.

## Local models (Ollama)

| Model | Basic (38) | Hard (24) | Speed | Notes |
|---|---|---|---|---|
| `qwen3:8b` | 38/38 | 17/24 | 3 s | the local reference |
| `qwen3:4b` | 37/38 | | 20 s | 2.5 GB; the smallest that works |
| `granite4.1:8b` | 37/38 | | 5 s | |
| `gemma4:e4b` | 37/38 | | 5 s | vision-capable |
| `qwen3:14b` | 36/38 | | 8 s | |
| `gpt-oss:20b` | 36/38 | | 15 s | spills out of 12 GB |
| `ministral-3:3b` | 34/38 | | 2 s | 3 GB, vision-capable |
| `llama3.1:8b` | 33/38 | | | |
| `command-r7b` | 30/38 | | | |
| `cogito:8b` | 27/38 | | | |
| `granite4:1b` | 27/38 | | | |
| `qwen2.5:3b` | 26/38 | | | |
| `granite4:micro` | 25/38 | | | |
| `mistral-nemo:12b` | 25/38 | | | |
| `lfm2.5:8b` | 24/38 | | | |
| `llama3.2:3b` | 19/38 | | | |
| `llama3-groq-tool-use:8b` | 17/38 | | | sold as a tool-calling model |
| `granite3.3:2b` | 13/38 | | | |
| `smollm2:1.7b` | 9/38 | | | |
| `nemotron-mini:4b` | 6/38 | | | |
| `gemma3:12b`, `falcon3:3b`, `exaone3.5:2.4b` | no tool calling | | | cannot drive ChimeraX at all |
| `cogito:3b`, `hermes3:3b`, `granite3.1-moe:3b` | hung | | | the run did not finish |

Two things to keep in mind when reading the local numbers. On this machine, larger models did not improve the basic-set score and were slower: the 20B-class models spilled out of the 12 GB card and slowed down accordingly, and several models sold as tool-calling specialists scored near the bottom of this table. Ollama also keeps the previous model loaded for a few minutes, so switching models on a full card can make the new one run on the CPU until the old one unloads.

Request sets used for these scores are in the repository: [`scenarios.json`](https://github.com/tggr-lab/pellaeon/blob/main/tests_chimerax/scenarios.json) (basic set), [`scenarios_extra.json`](https://github.com/tggr-lab/pellaeon/blob/main/tests_chimerax/scenarios_extra.json) (follow-ups and edge cases), [`scenarios_hard.json`](https://github.com/tggr-lab/pellaeon/blob/main/tests_chimerax/scenarios_hard.json) (multi-turn and ambiguous requests).
