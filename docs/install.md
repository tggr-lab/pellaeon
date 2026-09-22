# Installation

Three steps: install the panel, choose an AI when the panel first opens, press **Run a first request**.

Pellaeon comes in two editions. Pick the one for the program you use.

| | ChimeraX edition | Classic edition (old Chimera 1.x) |
|---|---|---|
| Runs | inside UCSF ChimeraX 1.9+ as a docked panel | as a small program next to UCSF Chimera 1.x, panel in your browser |
| Install | from ChimeraX's own Toolshed, or one line typed into ChimeraX | unzip, double-click |
| Needs | ChimeraX | Chimera 1.x and Python 3.9+ |
| Extras | click-to-ask, displacement-colored comparisons, in-place annotations | superposition (RMSD), annotations |

Both use the same AI providers, the same panel and the same safety rules.

## ChimeraX edition

Pellaeon is on the [ChimeraX Toolshed](https://cxtoolshed.rbvi.ucsf.edu/apps/chimeraxpellaeon), the bundle
catalogue built into ChimeraX, so ChimeraX installs and updates it for you.

1. Install [UCSF ChimeraX](https://www.cgl.ucsf.edu/chimerax/download.html) 1.9 or newer.
2. Start ChimeraX and install Pellaeon, from the menu or from the command line. **From the menu:** **Tools ▸ More Tools…** opens the Toolshed; find **Pellaeon** (category *General*) and press **Install**. **From the command line:** paste this into the **Command:** line at the bottom of the window and press Enter:

```
toolshed install ChimeraX-Pellaeon
```

3. Open **Tools ▸ General ▸ Pellaeon**. The panel opens on the right and asks you to choose an AI. The `pellaeon` command works on the ChimeraX command line too.

No terminal, no Python installation, no administrator rights. It works the same on Windows, macOS and Linux.

**Straight from GitHub instead:** this one line fetches the newest release from the
[releases page](https://github.com/tggr-lab/pellaeon/releases), installs it and opens the panel. Use it to pick up a
release before it reaches the Toolshed.

```
open https://github.com/tggr-lab/pellaeon/releases/latest/download/install_pellaeon.py
```

**Offline install:** get the `.whl` file from the [releases page](https://github.com/tggr-lab/pellaeon/releases) and type (quotes needed when the path has spaces):

```
toolshed install "C:\Users\you\Downloads\chimerax_pellaeon-<version>-py3-none-any.whl"
```

`<version>` is a placeholder: type the name of the file you actually downloaded.

**Uninstall:** `toolshed uninstall ChimeraX-Pellaeon` in ChimeraX.

## Classic edition (Chimera 1.x)

1. Install [Python 3.9 or newer](https://www.python.org/downloads/). On Windows tick **Add Python to PATH** in the installer.
2. Download `pellaeon-classic.zip` from the [releases page](https://github.com/tggr-lab/pellaeon/releases) and unzip it anywhere.
3. Windows: double-click **Start Pellaeon Classic.cmd**. macOS: double-click **Start Pellaeon Classic.command**. Linux: `./start.sh`.
   A small launcher window opens and your browser shows the Pellaeon panel.
4. In the launcher press **Launch Chimera** (it starts Chimera with its REST server and connects by itself), or start Chimera yourself, open **Tools ▸ Utilities ▸ RESTServer**, type the port shown in the Reply Log into the launcher and press **Test**.
5. Optional: `python install.py` puts a **Pellaeon Classic** shortcut on your desktop and Start menu.

## Choosing an AI

The first time the panel opens it shows provider cards. There is one decision to make: **local or cloud**. Local (Ollama) needs no account and no key, but needs a machine with a reasonable GPU and a one-time model download of a few GB. Cloud needs a key and nothing else.

Start with Mistral: the free key takes two minutes at [console.mistral.ai/api-keys](https://console.mistral.ai/api-keys), needs no credit card, and Pellaeon uses `ministral-8b-latest` on it. If you would rather not send anything to a provider, install Ollama and pull `qwen3:8b` instead.

| Option | How to get a key | Free allowance |
|---|---|---|
| **Ollama** (local) | no key: install [Ollama](https://ollama.com/download), then press **Pull** next to `qwen3:8b` in Pellaeon's settings (about 6 GB of GPU memory; `qwen3:4b` on a machine without a GPU) | free, no limit |
| **Mistral** (recommended) | [console.mistral.ai/api-keys](https://console.mistral.ai/api-keys), no credit card | free tier, about 190 requests a minute |
| **Google Gemini** | [aistudio.google.com/apikey](https://aistudio.google.com/apikey), no credit card | free tier, 15 requests a minute on Flash-Lite plus a daily cap |
| **Anthropic Claude** | [console.anthropic.com](https://console.anthropic.com/settings/keys); Claude Pro/Max subscriptions cannot be used by third-party tools | none, pay per use |
| **OpenAI** | [platform.openai.com](https://platform.openai.com/api-keys) | none, pay per use |
| **OpenRouter** | one key, many models | 50 requests a day in total |
| **Groq** | one key, many models | 7000 to 8000 input tokens a minute, about one request a minute |
| **LM Studio, llama.cpp, vLLM** | no key: enter the address of any local server that speaks the OpenAI protocol | free, no limit |

Press **Test connection**, then **Save & use** or **Run a first request** (it saves the settings and has the AI open ubiquitin and color it by chain). Keys are stored privately on your computer (system keyring or a private file), never in ChimeraX sessions or files you share. Environment variables (`MISTRAL_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) are picked up automatically.

Which model to run on the provider you picked, and how each one did on Pellaeon's own request sets, is on [Tested models](models.html).

When a limit is hit Pellaeon waits and retries rather than failing, and on the tightest tiers it automatically shortens its prompt to fit. A quota that resets tomorrow is reported plainly instead of being retried.

## Privacy

With a local Ollama server, model requests are processed on your computer. Fetching structures and annotations still contacts external databases (PDB, AlphaFold DB, UniProt, ClinVar, ConSurf-DB). With a cloud provider, your request, the list of open models, the current selection and any table you loaded are sent to that provider. Screenshots are shared only when the review-the-view option is enabled in Settings.

The free tiers of Mistral and Gemini are evaluation tiers, and both may use your requests to improve their models. For unpublished work, use a local model through Ollama, or check the provider's data-handling terms before you paste a key: [Mistral](https://mistral.ai/terms), [Google](https://ai.google.dev/gemini-api/terms).

## Updating

ChimeraX edition: ChimeraX notes available bundle updates in **Tools ▸ More Tools…**; `toolshed update ChimeraX-Pellaeon` on the command line does the same thing. If you installed from GitHub, run that install line again and it fetches the newest release. Classic edition: unzip the new zip over the old folder; your settings and chats live elsewhere and are kept.

## Where things are stored

| | Windows | macOS | Linux |
|---|---|---|---|
| ChimeraX edition | `%LOCALAPPDATA%\UCSF\ChimeraX\Pellaeon\` | `~/Library/Application Support/ChimeraX/Pellaeon/` | `~/.local/share/ChimeraX/Pellaeon/` |
| Classic edition | `%LOCALAPPDATA%\Pellaeon-Classic\` | `~/Library/Application Support/Pellaeon-Classic/` | `~/.local/share/pellaeon-classic/` |

Chats are saved there as JSON; delete the folder to start clean.
