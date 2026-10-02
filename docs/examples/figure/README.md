# Example: prepare a figure

Shows Pellaeon tidying overlapping residue labels and saving a reproducible figure bundle.

## Structure

Open **4hhb** (hemoglobin), colored by chain, with residues 80-100 of chain A labeled (deliberately piled up, to have something to tidy).

## What to type

1. `open 4hhb`
2. Color by chain and label residues 80-100 of chain A (for example: "color 4hhb by chain, then label residues 80 to 100 of chain A").
3. "the labels overlap, tidy them"
4. "save this as a figure called hemoglobin"

## Expected result

The crowded label pile is nudged apart; labels that still overlap after nudging are removed (in the recorded run: 14 kept, 13 moved, 7 removed). A figure bundle (PNG, ChimeraX session, replay script, per-residue colors, draft legend) is written to a folder named after the figure, and the saved-files card lists all of them. See [`expected.png`](expected.png).

## About `commands.cxc`

[`commands.cxc`](commands.cxc) reproduces the label-tidy offsets and the save step, but the starting scene it operates on (4hhb colored by chain with residues 80-100 of chain A labeled) is not itself recorded as a command and is not included — the label positions Pellaeon computed depend on your window size and camera, so re-running the file alone on a freshly opened 4hhb will not reproduce the same offsets. Typing the requests above is the reproducible path; the `.cxc` is a record of the styling, not a standalone script.
