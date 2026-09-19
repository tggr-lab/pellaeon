# Reliability

Short version: with the default free local model, **91 of 100** plain-English requests from our test set did what was asked, with a median of 2 seconds per request. Cloud models (Gemini, Claude, OpenAI) are stronger than an 8-billion-parameter local model; we have not published numbers for them yet because the test machine has no cloud key.

How this was measured: each request was typed into Pellaeon inside ChimeraX 1.12 on 2026-09-19, with **qwen3:8b** through Ollama (thinking off, temperature 0.2, RTX 4070 Super). Afterwards a script inspected the ChimeraX session (colors, selections, distances, open models), so a pass means the requested state was actually there, not that the reply sounded right. Requests that must ask first (close, delete, save) pass when the confirmation card appeared with the right commands. The harness, the request sets and the raw reports are in the repository.

## By kind of request

| Kind | Passed |
|---|---|
| Opening structures | 3 of 3 |
| Coloring | 16 of 17 |
| Showing, hiding and styles | 12 of 15 |
| Selecting and labels | 8 of 8 |
| Measurements and analysis | 11 of 12 |
| Camera, motion and movies | 13 of 14 |
| Risky commands ask first | 10 of 10 |
| Follow-ups, complaints and "it" | 8 of 10 |
| Odd, impossible or off-topic requests | 10 of 11 |

## Where it fell short

The misses are listed because they say more than the pass count. Most are a small model choosing a plausible but wrong command; rephrasing to one action, or switching to a cloud provider, fixes them.

- **"reset the view"**: answered in words and ran nothing; a second try normally works.
- **"make the selected half transperent"**: made cartoons, atoms and surfaces transparent instead of only the cartoon; visually fine, stricter than the check wanted.
- **"show every atom contact within 4 A between chain C (the inhibitor) and chain A as dashed lines"**: used hydrogen bonds and a zone selection instead of the `contacts` command.
- **"render the coiled coil helices as fat tubes / cylinders instead of ribbons"**: thickened the ribbon instead of switching helices to tube mode.
- **"rainbow each chain separately from blue at the N-terminus to red at the C-terminus"**: rainbowed by chain (one color per chain) instead of along each chain.
- **"show the glucose as ball and stick colored by element, and get the waters and the calcium ion out of the view"**: hid the solvent and ions but left the ligand's style unchanged in the check (ball, not ball-and-stick).
- **"email this view to my PI"**: tried to save an image first (which asks for confirmation) instead of simply saying ChimeraX cannot send email.
- **"highlight residue 30"**: after the complaint it colored the whole model yellow, not just residue 30.
- **"label residue 5"**: took "make it bigger" as the sticks, not the label it had just added.

## Reading the numbers

- The test set is small on purpose: 100 requests covering the things people actually type, including typos, "it", complaints and impossible asks. It is not a benchmark of the model; it is a check that this panel, with this model, does what the tutorial shows.
- Each pass rate is for one run. Small local models are not fully deterministic; expect a request or two to flip between runs.
- Rerun it yourself: `chimerax --nogui --exit --script "tests_chimerax/scenarios.py <model>"`, then `python tools/build_reliability.py <report.json>`.
- Raw reports: [qwen3-8b_base_2026-09-19.json](reliability/qwen3-8b_base_2026-09-19.json), [qwen3-8b_extra_2026-09-19.json](reliability/qwen3-8b_extra_2026-09-19.json).
