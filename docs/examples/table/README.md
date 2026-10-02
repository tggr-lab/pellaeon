# Example: map a table

Shows Pellaeon importing a per-residue CSV and coloring a structure by one of its columns, with a color key drawn in the view.

## Structure

Open **1ubq** (ubiquitin).

## Data file

[`ubiquitin_hydropathy.csv`](ubiquitin_hydropathy.csv) — Kyte-Doolittle hydropathy for every residue of ubiquitin, one row per position.

## What to type

1. `open 1ubq`
2. In the panel header, press the **Import table** button (⊞) and pick `ubiquitin_hydropathy.csv`.
3. In the preview card that appears (column `hydropathy`, model `#1`, chain left empty, numbering `structure`, palette `blue-white-red`), press **Apply**.

## Expected result

All 76 residues of chain A mapped, colored blue-to-red by hydropathy value, with a color key and title drawn in the corner of the view. See [`expected.png`](expected.png).

## About `commands.cxc`

[`commands.cxc`](commands.cxc) reproduces the coloring, key and label styling, but the `pellaeon_ubiquitin_hydropathy_hydropathy` attribute it colors by only exists after Pellaeon's own Import table step has run — running the `.cxc` file alone on a plain `open 1ubq` will fail on that command. Typing the requests above is the reproducible path; the `.cxc` is a record of the styling, not a standalone script.
