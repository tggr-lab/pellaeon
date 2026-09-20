# Installation

Three steps: install the panel, choose an AI when the panel first opens, press **Run a first request**.

Pellaeon comes in two editions. Pick the one for the program you use.

| | ChimeraX edition | Classic edition (old Chimera 1.x) |
|---|---|---|
| Runs | inside UCSF ChimeraX 1.9+ as a docked panel | as a small program next to UCSF Chimera 1.x, panel in your browser |
| Install | one line typed into ChimeraX | unzip, double-click |
| Needs | ChimeraX | Chimera 1.x and Python 3.9+ |
| Extras | click-to-ask, displacement-colored comparisons, in-place annotations | superposition (RMSD), annotations |

Both use the same AI providers, the same panel and the same safety rules.

## ChimeraX edition

1. Install [UCSF ChimeraX](https://www.cgl.ucsf.edu/chimerax/download.html) 1.9 or newer.
2. Start ChimeraX. In the **Command:** line at the bottom of the window, paste and press Enter:

```
open https://github.com/tggr-lab/pellaeon/releases/latest/download/install_pellaeon.py
```

3. The Pellaeon panel opens on the right and asks you to choose an AI. Later you find it under **Tools ▸ General ▸ Pellaeon**, and the `pellaeon` command works on the ChimeraX command line.

No terminal, no Python installation, no administrator rights. It works the same on Windows, macOS and Linux.

**Offline or blocked download?** Get the `.whl` file from the [releases page](https://github.com/tggr-lab/pellaeon/releases) and type (quotes needed when the path has spaces):

```
toolshed install "C:\Users\you\Downloads\chimerax_pellaeon-0.1.0-py3-none-any.whl"
```

**Uninstall:** `toolshed uninstall ChimeraX-Pellaeon` in ChimeraX.

## Classic edition (Chimera 1.x)

1. Install [Python 3.9 or newer](https://www.python.org/downloads/). On Windows tick **Add Python to PATH** in the installer.
2. Download `pellaeon-classic.zip` from the [releases page](https://github.com/tggr-lab/pellaeon/releases) and unzip it anywhere.
3. Windows: double-click **Start Pellaeon Classic.cmd**. macOS: double-click **Start Pellaeon Classic.command**. Linux: `./start.sh`.
   A small launcher window opens and your browser shows the Pellaeon panel.
4. In the launcher press **Launch Chimera** (it starts Chimera with its REST server and connects by itself), or start Chimera yourself, open **Tools ▸ Utilities ▸ RESTServer**, type the port shown in the Reply Log into the launcher and press **Test**.
5. Optional: `python install.py` puts a **Pellaeon Classic** shortcut on your desktop and Start menu.

## Choosing an AI

The first time the panel opens it shows provider cards. There is one decision to make: **local or cloud**.

- **Local** (Ollama): nothing leaves your computer, no account, free. Needs a machine with a reasonable GPU or patience on CPU, and a one-time model download of a few GB. Small local models make more mistakes than the cloud ones; keep requests to one thing at a time.
- **Cloud** (Mistral or Gemini free tiers, Claude, OpenAI): nothing to install, stronger models, needs a key. Your requests and the list of open models are sent to that provider. The free tiers of Mistral and Gemini are evaluation tiers, and both providers may use what you send to improve their models, so for unpublished structures use Ollama or a paid tier.

**Mistral is the one to start with:** a free key from [console.mistral.ai/api-keys](https://console.mistral.ai/api-keys), no credit card, and about 190 requests a minute against 15 a minute on Gemini. The free tier serves the **Ministral** models and Pellaeon picks `ministral-8b-latest`; `mistral-small` and `mistral-medium` sit on the same key but need billing and refuse a free request straight away. The [tutorial](tutorial.md#2-connect-an-ai-once) walks through it.

OpenRouter, Groq, LM Studio and custom endpoints are there too.

All options:

| Option | Cost | What to do |
|---|---|---|
| **Ollama** (local, private) | free | Install [Ollama](https://ollama.com/download), then press **Pull** next to `qwen3:8b` in Pellaeon's settings (needs about 6 GB of GPU memory). On a laptop without a GPU pull `qwen3:4b` instead and expect 10–30 s per request. Nothing leaves your computer except UniProt/ClinVar lookups when you ask for them. |
| **Mistral** (recommended free option) | free tier, about 190 requests a minute | Create a key at [console.mistral.ai/api-keys](https://console.mistral.ai/api-keys) (no credit card), paste it in. Pellaeon uses `ministral-8b-latest`. |
| **Google Gemini** | free tier, 15 requests a minute on Flash-Lite | Create a key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (no credit card), paste it in. |
| **Anthropic Claude** | pay per use | Key from [console.anthropic.com](https://console.anthropic.com/settings/keys). Best at long multi-step work. Claude Pro/Max subscriptions cannot be used by third-party tools. |
| **OpenAI** | pay per use | Key from [platform.openai.com](https://platform.openai.com/api-keys). |
| **OpenRouter / Groq** | free tiers, but tight | One key, many models. OpenRouter allows 50 requests a day in total; Groq allows plenty of requests but few tokens a minute, so expect about one request a minute. |
| **LM Studio, llama.cpp, vLLM** | free | Any local server that speaks the OpenAI protocol: enter its address. |

Press **Test connection**, then **Save & use** or **Run a first request** (it saves the settings and has the AI open ubiquitin and color it by chain). Keys are stored privately on your computer (system keyring or a private file), never in ChimeraX sessions or files you share. Environment variables (`MISTRAL_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) are picked up automatically.

## Choosing a model

Tested with Pellaeon's own request set (colors, selections, measurements, AlphaFold and UniProt lookups, comparisons, confirmations, typos, follow-ups), on an RTX 4070 Super with 12 GB.

| Want | Pick | Why |
|---|---|---|
| Nothing to install | **Mistral `ministral-8b-latest`** (free) | Handles the whole request set, answers in about two seconds, and allows roughly 190 requests a minute, so you never wait. Free key, no credit card. It can also see the screen. |
| Nothing to install, alternative | **Gemini `gemini-flash-lite-latest`** (free) | Just as reliable and can see the screen; 15 requests a minute is plenty for one person working normally. |
| Private, on a gaming GPU | **qwen3:8b** | The reference local model: reliable and quick on a 12 GB card. |
| Private, and able to see the screen | **gemma4:e4b** | Vision plus tools; the only local model here that can review the view. |
| Private, small GPU or CPU | **qwen3:4b** | Accurate but slower: expect 10 to 20 seconds a request. Below about 4B parameters models miss too much to be useful. |
| Best quality, paid | Claude, OpenAI, Gemini Pro, or Mistral Medium | Worth it for long multi-step sessions. |

### What the free tiers really allow

The limit matters more than the model. A single request carries your structure's state and the tool definitions, so it is a few thousand tokens, and one question usually takes two to four requests.

| Free tier | Room | Good for |
|---|---|---|
| Mistral | ~190 requests a minute | Normal work, all day |
| Gemini Flash-Lite | 15 requests a minute | Normal work |
| Gemini Flash / Pro | 5 a minute / none | Not usable free: Pellaeon spends the session waiting |
| Groq | 1000 a day, but ~7000 tokens a minute | About one request a minute: it works, slowly |
| OpenRouter | 50 requests a day, whole key | Trying it out; that is roughly a dozen questions |

When a limit is hit Pellaeon waits and retries rather than failing, and on the tightest tiers it automatically shortens its prompt to fit. A quota that resets tomorrow is reported plainly instead of being retried.

**Privacy:** the free tiers of Mistral and Gemini are evaluation tiers, and both may use your requests to improve their models. For unpublished structures use a local model through Ollama, where nothing leaves your machine, or a paid tier.

What did not work: models without tool calling (Gemma 3, Falcon 3, EXAONE, most vision-only models) cannot drive ChimeraX at all; 20B-class models spill out of a 12 GB card and take ten times longer for no gain; below 4B the small models get most requests wrong, including several sold as tool-calling specialists. Ollama keeps the previous model loaded for a few minutes, so switching models on a full card can make the new one run on the CPU until the old one unloads.

## Updating

ChimeraX edition: run the install line again; it fetches the newest release. Classic edition: unzip the new zip over the old folder; your settings and chats live elsewhere and are kept.

## Where things are stored

| | Windows | macOS | Linux |
|---|---|---|---|
| ChimeraX edition | `%LOCALAPPDATA%\UCSF\ChimeraX\Pellaeon\` | `~/Library/Application Support/ChimeraX/Pellaeon/` | `~/.local/share/ChimeraX/Pellaeon/` |
| Classic edition | `%LOCALAPPDATA%\Pellaeon-Classic\` | `~/Library/Application Support/Pellaeon-Classic/` | `~/.local/share/pellaeon-classic/` |

Chats are saved there as JSON; delete the folder to start clean.
