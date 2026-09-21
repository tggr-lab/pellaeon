<img src="docs/logo.svg" width="72" align="left" alt="Pellaeon logo">

# Pellaeon: talk to ChimeraX in plain English

Pellaeon adds a chat panel to [UCSF ChimeraX](https://www.cgl.ucsf.edu/chimerax/). You type what you want:

> open the AlphaFold model of ADRB2 and color residue 113 blue
> measure the distance between residues 100 and 150
> make it look publication ready

Pellaeon runs the ChimeraX commands, lists each one so you can copy or re-run it, sends failures back to the model with the ChimeraX error, and stops for your approval before file writes and destructive actions.

It works with **local models** (Ollama on your own computer) and **cloud models** (Anthropic Claude, Google Gemini, OpenAI, and anything OpenAI-compatible such as OpenRouter, Groq or LM Studio). Gemini and OpenRouter have free tiers.

Named after Gilad Pellaeon, captain of the *Chimaera*.

![ChimeraX with the Pellaeon panel docked on the right](docs/img/docked_4hhb.png)

## Install (3 steps)

1. Install ChimeraX 1.9 or newer.
2. In ChimeraX's command line (bottom of the window) type:

   ```
   open https://github.com/tggr-lab/pellaeon/releases/latest/download/install_pellaeon.py
   ```

   This downloads the Pellaeon bundle and installs it with ChimeraX's own tool installer. No terminal, no Python setup.
3. The Pellaeon panel opens (later: **Tools > General > Pellaeon**). Pick a provider, paste a key if it needs one, press **Test connection**, then **Save & use**.

Offline install: download the `.whl` from the [releases page](https://github.com/tggr-lab/pellaeon/releases) and run `toolshed install /path/to/the/file.whl` in ChimeraX.

## Choosing an AI

Local: Ollama (`qwen3:8b` on a GPU, `qwen3:4b` on a CPU-only machine). Cloud: Google Gemini (free tier, no credit card), Anthropic Claude, OpenAI, or any OpenAI-compatible server (OpenRouter, Groq, LM Studio). Keys are stored on your computer, not in ChimeraX sessions or files you share. Details, per provider: [installation guide](https://tggr-lab.github.io/pellaeon/install.html#choosing-an-ai).

## What you can do

- **Explore a structure.** Select a residue, inspect its neighbours, measure distances, or add annotations. Click a residue in the 3D view, or Alt-click it, and ask what it is or what is nearby. "it", "this" and "the selected" resolve against what is open and selected.
- **Compare conformations.** Superpose structures and inspect residue displacements and contact differences. Displacement is reported over the fitted subset, not all pairs; contacts and salt bridges are listed as lost and gained.
- **Map your data.** Import a residue-score table, check its numbering, and color the structure by value or category. UniProt positions are mapped onto the structure's own numbering, with the offset reported. Fetch UniProt features, ClinVar variants, ConSurf conservation, or AlphaMissense: AlphaMissense is scored per substitution, and Pellaeon colors by the mean over the 19 substitutions at each position, with the maximum as a second column. Mapped to the structure, with a color legend. Reports when scores are unavailable.
- **Save figures and views.** Export images with their recorded commands and sources: the image, a ChimeraX session, a replayable `.cxc` script, a per-residue color table, where each model came from, and a draft legend built from what ran. Save views and reuse figure styles. Record spin, rock or view-tour movies.

Each assistant reply carries a collapsible **Ran N commands** block with copy, re-run and a link to the ChimeraX documentation for that command. Commands that match nothing are flagged. Select a residue and ask "why is this red?" and Pellaeon answers from its own record: the command, table column, annotation or comparison that set the color, with the value and its source. A `.cxc` script is not a session; both are written when you save a figure.

The dropdown at the top of the panel switches between *ask before every command*, *auto-run but ask for risky ones* (default) and *never ask*. Press **Stop** (or Esc) to interrupt; **+** starts a new chat; **☰** lists past conversations. The `pellaeon` command also works from ChimeraX's own command line and in scripts: `pellaeon color everything by chain`.

Try it with a fresh session: `open 4hhb`, then "color by chain", "show the ligand as spheres and hide water", "make it spin", "stop", "label residues 10 and 20", "close everything" (asks first).

## How it works

Pellaeon is a normal ChimeraX bundle (pure Python, no extra packages). It runs inside ChimeraX, so it can execute commands, read the log output and errors, and inspect the open models and selection. The documentation for the ChimeraX version you run is indexed on first launch from the docs that ship with ChimeraX. Requests are answered by the model you choose through a tool-calling loop: the model can run commands, check the session state, look up command syntax, search the docs, resolve proteins via UniProt, fetch annotations, or ask you a question.

## Old Chimera (1.x)

There is a classic edition that drives UCSF Chimera 1.x through its REST server from your browser: see [classic/README.md](classic/README.md). Both editions share the chat interface and model providers; table overlays and some analysis tools are ChimeraX-only. One zip plus Python 3, Chimera command syntax.

## Development

```
git clone https://github.com/tggr-lab/pellaeon
cd pellaeon
python -m pytest                        # unit tests, no ChimeraX needed
chimerax --nogui --exit --cmd "devel install \"$(pwd)\""   # install into your ChimeraX
chimerax --nogui --exit --script tests_chimerax/smoke.py    # in-ChimeraX checks
```

Website (GitHub Pages, served from `docs/`): install guide, illustrated tutorial and the classic edition. Rebuild it with `python tools/build_site.py` after editing the Markdown sources. See `RELEASING.md` for building the wheel. Licensed under MIT.

---
<sub>Made by [Yam Amir](https://github.com/YAMIR-1138) at the [TGGR Lab](https://github.com/tggr-lab), with [Claude Code](https://claude.com/claude-code).</sub>
