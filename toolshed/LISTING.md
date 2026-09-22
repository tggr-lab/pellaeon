# ChimeraX Toolshed listing

**Live at https://cxtoolshed.rbvi.ucsf.edu/apps/chimeraxpellaeon** — users install it with
`toolshed install ChimeraX-Pellaeon` or from **Tools > More Tools...** inside ChimeraX.

This file is the source of the listing text below; keep the two in step. To post a new version, sign in at
https://cxtoolshed.rbvi.ucsf.edu (with Google) and upload `dist/chimerax_pellaeon-<version>-py3-none-any.whl`
against the existing bundle. The first submission was held for review by the ChimeraX team; later versions post
immediately.

## Name

ChimeraX-Pellaeon

## One-line summary

Control ChimeraX in plain English from a docked chat panel, with local or cloud models.

## Description

Pellaeon adds a chat panel to ChimeraX. Type what you want ("open the AlphaFold model of ADRB2 and color
residue 113 blue", "which contacts are lost between these two conformations", "color it by ClinVar variants")
and Pellaeon runs the ChimeraX commands, lists every command it ran so you can copy or re-run it, sends errors
back to the model for one corrected attempt, and asks before closing, deleting, saving or running scripts.

Models: free cloud tiers (Mistral, Google Gemini), local models through Ollama (nothing leaves your computer),
or paid Anthropic, OpenAI and OpenAI-compatible servers. Keys are stored on your computer, never in sessions.

Beyond commands, it fetches and places UniProt features, ClinVar variants, AlphaMissense scores and ConSurf
conservation on the structure; colors by your own residue tables in the structure's numbering; compares two
conformations by residue displacement and by lost and gained contacts; tidies overlapping labels; and saves
figures with a replayable .cxc script and the sources of everything shown. These analysis tools are also
ChimeraX commands (`pellaeon tool contacts #1 #2`, `pellaeon tool annotate #1 P07550 variant`) usable from
scripts, the command line, or an assistant connected through ChimeraX's own `mcp` command.

Privacy: with a cloud provider, the request, the list of open models, the current selection and any loaded
table are sent to that provider. With Ollama, nothing leaves the machine. Fetching structures and annotations
contacts PDB, AlphaFold DB, UniProt, ClinVar and ConSurf-DB. Screenshots are sent only if enabled in Settings.

Pure Python, no packages beyond ChimeraX. Not affiliated with UCSF. Named after Gilad Pellaeon, captain of the
Chimaera.

Website with tutorial and tested models: https://tggr-lab.github.io/pellaeon/
Source and issues: https://github.com/tggr-lab/pellaeon

## Differences from the built-in `mcp` command (for the reviewer)

`mcp` lets an assistant outside ChimeraX (Claude Desktop, Cursor, VS Code) send commands over a bridge process
and the REST server. Pellaeon is an in-app panel with its own model connection (including free and local
models), reads the session, selection, log and errors in-process, gates destructive commands behind a
confirmation card, and adds the analysis tools above. The two coexist; Pellaeon's tools are ordinary commands
that the `mcp` bridge can run. Detail: https://tggr-lab.github.io/pellaeon/mcp.html

## Categories

General

## License

MIT

## Authors

Yam Amir, Translational Genetics and Genomics Research (TGGR) Lab

## Citation

None yet. Cite the GitHub repository: https://github.com/tggr-lab/pellaeon

## Icon

`docs/img/logo_lockup.png` is the full artwork (mark and wordmark). `docs/logo.png` is the mark on its own, cropped
from it, and is what the website uses for its nav brand and favicon.

## Screenshots (this folder)

1. `1_panel_docked.png`: the panel docked beside hemoglobin, commands listed.
2. `2_contacts_lost_gained.png`: lost and gained contacts between two conformations.
3. `3_table_overlay.png`: a residue table colored onto ubiquitin with a key.
4. `4_clinvar_variants.png`: ClinVar missense variants placed and labeled.
5. `5_publication_look.png`: the publication look applied on request.
