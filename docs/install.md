# Installation

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
open https://github.com/pellaeon-chimerax/pellaeon/releases/latest/download/install_pellaeon.py
```

3. The Pellaeon panel opens on the right and asks you to choose an AI. Later you find it under **Tools ▸ General ▸ Pellaeon**, and the `pellaeon` command works on the ChimeraX command line.

No terminal, no Python installation, no administrator rights. It works the same on Windows, macOS and Linux.

**Offline or blocked download?** Get the `.whl` file from the [releases page](https://github.com/pellaeon-chimerax/pellaeon/releases) and type (quotes needed when the path has spaces):

```
toolshed install "C:\Users\you\Downloads\chimerax_pellaeon-0.1.0-py3-none-any.whl"
```

**Uninstall:** `toolshed uninstall ChimeraX-Pellaeon` in ChimeraX.

## Classic edition (Chimera 1.x)

1. Install [Python 3.9 or newer](https://www.python.org/downloads/). On Windows tick **Add Python to PATH** in the installer.
2. Download `pellaeon-classic.zip` from the [releases page](https://github.com/pellaeon-chimerax/pellaeon/releases) and unzip it anywhere.
3. Windows: double-click **Start Pellaeon Classic.cmd**. macOS: double-click **Start Pellaeon Classic.command**. Linux: `./start.sh`.
   A small launcher window opens and your browser shows the Pellaeon panel.
4. In the launcher press **Launch Chimera** (it starts Chimera with its REST server and connects by itself), or start Chimera yourself, open **Tools ▸ Utilities ▸ RESTServer**, type the port shown in the Reply Log into the launcher and press **Test**.
5. Optional: `python install.py` puts a **Pellaeon Classic** shortcut on your desktop and Start menu.

## Choosing an AI

The first time the panel opens it shows provider cards. Any of these works; you can switch later in Settings.

| Option | Cost | What to do |
|---|---|---|
| **Ollama** (local, private) | free | Install [Ollama](https://ollama.com/download), then press **Pull** next to `qwen3:8b` in Pellaeon's settings (needs about 6 GB of GPU memory). On a laptop without a GPU pull `qwen3:4b` instead and expect 10–30 s per request. Nothing leaves your computer except UniProt/ClinVar lookups when you ask for them. |
| **Google Gemini** | free tier | Create a key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (no credit card), paste it in. |
| **Anthropic Claude** | pay per use | Key from [console.anthropic.com](https://console.anthropic.com/settings/keys). Best at long multi-step work. Claude Pro/Max subscriptions cannot be used by third-party tools. |
| **OpenAI** | pay per use | Key from [platform.openai.com](https://platform.openai.com/api-keys). |
| **OpenRouter / Groq** | free tiers | One key, many models. `openrouter/free` picks a free model that supports tools. |
| **LM Studio, llama.cpp, vLLM** | free | Any local server that speaks the OpenAI protocol: enter its address. |

Press **Test connection**, then **Save & use**. Keys are stored privately on your computer (system keyring or a private file), never in ChimeraX sessions or files you share. Environment variables (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`) are picked up automatically.

## Updating

ChimeraX edition: run the install line again; it fetches the newest release. Classic edition: unzip the new zip over the old folder; your settings and chats live elsewhere and are kept.

## Where things are stored

| | Windows | macOS | Linux |
|---|---|---|---|
| ChimeraX edition | `%LOCALAPPDATA%\UCSF\ChimeraX\Pellaeon\` | `~/Library/Application Support/ChimeraX/Pellaeon/` | `~/.local/share/ChimeraX/Pellaeon/` |
| Classic edition | `%LOCALAPPDATA%\Pellaeon-Classic\` | `~/Library/Application Support/Pellaeon-Classic/` | `~/.local/share/pellaeon-classic/` |

Chats are saved there as JSON; delete the folder to start clean.
