# Changelog

Notes for earlier versions are on the [releases page](https://github.com/tggr-lab/pellaeon/releases).

## Unreleased

- ConSurf key: the ends are labelled "variable" and "conserved"; the yellow "too few sequences" bin appears only when a residue has that grade; ligands, ions and waters keep their colors.

## 0.2.2 (2026-09-24)

Changed:

- The panel opens as a floating window.
- Default scope is "exactly what was asked": no extra coloring, windows or suggestions. Settings ▸ Advanced ▸ "take some initiative" restores them.
- Sequence identity between open structures is reported as a table; no alignment windows are opened. "Close those sequence windows" closes any that are.
- Keys, legends and titles are removed together with the structure they belong to.

New tools, also available without a model as `pellaeon tool ...` commands:

- `membrane_view`: OPM orientation, membrane planes on request. AlphaFold models of GPCRs use their nearest GPCRdb structure.
- `gpcr_states`: GPCRdb structures and inactive/active AlphaFold models of a receptor.
- `view_axis`: look down a principal axis of an assembly.
- `undo_last_request`: restore the session to its state before the previous request (chip in the panel).
- ConSurf coloring uses ConSurf's nine grades; ClinVar variants are numbered by the MANE transcript and carry their condition names.

Fixed:

- Every command is parsed by ChimeraX before it runs. Invalid syntax from the model is rewritten (rainbow with a palette, submodel ranges, helical `sym`, class selectors) instead of failing.
- A request for help on a command is answered with the usage text in the panel; it no longer opens a browser.
- Files fetched from URLs are kept in Pellaeon's cache, not in the Downloads folder.
- Tables in replies render as tables.
- Default local model is `gemma4:12b`.

Evaluation protocol and results: `tests_chimerax/EVAL.md`.
