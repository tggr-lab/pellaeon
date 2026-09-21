# Example: compare structures

Shows Pellaeon superposing two conformations of the same protein, coloring by per-residue displacement after the fit, and then drawing the contacts that are lost and gained between the two forms.

## Structures

Open **1ake** (adenylate kinase, closed, no ligand) and **4ake** (adenylate kinase, open).

## What to type

1. `close everything, open 1ake, open 4ake` (or open them one at a time)
2. "compare these two models and tell me what changed"
3. "which salt bridges break when it opens?"

## Expected result

4ake (chain A) is superposed on 1ake (chain A) and shown alone, colored by Cα displacement after the fit: gray under 1 Å, gold, orange, dark red at 6 Å or more, with a key. Then contacts that break on opening are drawn as red dashes on the closed model (1ake) and contacts that form are drawn as green dashes on the open model (4ake), with a lost/gained key. See [`expected.png`](expected.png).

## About `commands.cxc`

[`commands.cxc`](commands.cxc) reproduces the coloring, transparency, key and distance-line styling, but the `pellaeon_disp` attribute it colors by, and the exact residue sets shown, only exist after Pellaeon's own compare step has run on your open models — running the `.cxc` file alone will fail on the `color byattribute` command. Typing the requests above is the reproducible path; the `.cxc` is a record of the styling, not a standalone script. The recorded run drew more lost- and gained-contact distance lines than are spelled out here; only one representative `distance` command of each kind is kept, and the rest are noted as omitted in the comment at the top of the file.
