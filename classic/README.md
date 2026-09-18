# Pellaeon Classic — for UCSF Chimera 1.x

The same assistant, for the classic Chimera. Because Chimera 1.x runs Python 2 and Tk, Pellaeon Classic is a small
separate program: it serves the Pellaeon panel to your web browser and drives Chimera through Chimera's built-in
REST server. It shares the agent, providers, safety gate and UI with the ChimeraX bundle; only the executor and
the command knowledge (Chimera's Midas-style syntax, models numbered from #0, `:10.A` chains) differ.

## Install and run

1. Python 3.9 or newer (python.org; on Windows tick "Add Python to PATH"). No other packages.
2. Download `pellaeon-classic.zip` from the releases page and unzip it anywhere.
3. `python run.py` (Windows: double-click `run.py`). Your browser opens `http://127.0.0.1:8765`.
4. In the settings page: choose an AI provider as in the ChimeraX edition, then under **UCSF Chimera connection**
   press **Launch Chimera** (it starts Chimera with `--start RESTServer` and picks up the port automatically),
   or start Chimera yourself, open **Tools ▸ Utilities ▸ RESTServer**, type the port shown in the Reply Log and press **Test Chimera**.

Chimera path auto-detection covers `C:\Program Files\Chimera*`, `/Applications/Chimera*.app` and `~/.local/UCSF-Chimera*`;
enter the path to the `chimera` executable if yours is elsewhere.

## Differences from the ChimeraX edition

- Commands use Chimera syntax (`color red :10`, `display`, `~ribbon`, `focus`, `turn y 1 360`, `background solid white`).
- AlphaFold models open from the EBI URL (`open https://alphafold.ebi.ac.uk/files/AF-<accession>-F1-model_v4.pdb`).
- No Python execution, no per-residue displacement coloring in *compare* (RMSD from matchmaker only).
- Chat exports are `.cmd` Chimera command files; `copy file` (images), `save`, `close`, `delete`, `system` ask first.
- Everything else is the same: local or cloud models, confirmation cards, click-less "what is open" awareness via `list models`.

## Developing

```
python classic/tools/build_chimera_docs.py classic/pellaeon_classic/data/chimera_docs classic/pellaeon_classic/data   # after re-downloading the docs
python classic/tools/build_classic.py                       # -> dist/pellaeon-classic/ and dist/pellaeon-classic.zip
python classic/tools/e2e_stub_test.py "open 1zik and color it red"   # end-to-end against a stub REST server (needs Ollama)
```
