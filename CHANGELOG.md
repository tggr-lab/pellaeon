# Changelog

Notes for earlier versions are on the [releases page](https://github.com/tggr-lab/pellaeon/releases).

## 0.2.11 (2026-10-02)

- Prompt examples and bundled notes revised; a generic command-line overview is no longer part of the documentation index. Provider labels and privacy wording made precise.

## 0.2.10 (2026-10-02)

- Panel header: the provider line no longer slides under the autonomy menu in a narrow dock. The title block shrinks with an ellipsis, the provider is named short ("Mistral · ministral-8b-latest"; the full name is the tooltip), the line hides below 480 px and the title below 380 px, leaving the logo with the tooltip.

## 0.2.9 (2026-10-02)

- A **?** button in the panel: *Report a problem…* opens a new GitHub issue in the browser, prefilled with the Pellaeon and ChimeraX versions, the provider and model, and the last two turns of the chat with the commands that ran and their errors (paths and web addresses replaced; nothing is sent until the user posts it there); *Copy report* puts the same text on the clipboard, *Copy chat* the whole conversation as text, and the commands export sits there too.
- A deliberate `select X` followed by `select clear` runs as written (0.2.7 dropped the clear); a prompt line says a plain `select` already replaces the selection. A reply that describes a blocked batch as done, or an absent residue as selected, is sent back to the model once for a restatement; the facts are appended only if it insists (before, the correction was appended after the false claim, which read as a contradiction).

## 0.2.8 (2026-10-02)

- "Sequence identity is not the analysis I am requesting" no longer counts as asking for it: the negation after the phrase is read too.

## 0.2.7 (2026-10-02)

- Protein lookup: the organism word is a filter, not part of the name ("human ubiquitin"); the gene and name searches are merged and ranked by how well the name matches, so "lysozyme" is Lysozyme C and not a lysozyme-like protein; several equal matches resolve nothing and the model asks (ubiquitin: UBB, UBC or UBD), and a name no human protein carries is reported as such instead of a random hit. A residue spec without a chain takes the chain the request names (`color #1:63 green` after "His63 in chain B" becomes `#1/B:63`). A blocked selection request is told the one command it needs, with the complement syntax. An existing surface gets its model id for `surface style` even after a redundant `surface` command, and a chain spec without a model id (`/D`) is handled too. `select X` followed by `select clear` in one batch drops the clear. A `select add` of an absent residue is reported as absent.

## 0.2.6 (2026-10-02)

- Residue names in a request are now resolved with their sign and insertion code and within the chains named (`Gly184A`, `Thr-3`, `Ser82A in chain B`); a name that does not match what sits there gets no spec and the reply reports the mismatch. A request's insertion codes list the whole cluster (82, 82A, 82B, 82C). Exclusion is decided by the structure: a second set inside the first, with an inversion to follow or exclusion wording, is subtracted. A residue that does not exist in an existing chain is stated as absent in the reply, and a selection request runs no display commands. `renumber` and `changechains` run only when asked. "A surface for chain D only" hides other surfaces, before making the new one, and never the cartoons; "without changing any other chain" drops model-wide hides. `surface style` is given a surface model id, since a chain or residue spec is accepted by ChimeraX and does nothing. "Do not answer with sequence identity" no longer counts as asking for it. The state block says how many residues a chain has, apart from its numbering range.

## 0.2.5 (2026-10-01)

- A `select` that matches nothing no longer wipes the current selection (ChimeraX clears it; Pellaeon restores it and says so); "excluding residue 15" subtracts instead of adding, and a `select ~sel` the model adds to exclude is dropped; `surface style #1 mesh` with no surface open gets `surface #1` first; residue names in a request (Leu8) are resolved to specs and a mismatch is flagged; a sequence-identity question goes to the identity tool, and `compare_structures` refuses it; "the model" with two structures open asks which.
- `find_structures`: which structures exist for a protein, from 3D-Beacons (EBI): PDB entries with method, resolution, residues covered, chains, bound ligands and partner proteins, plus the AlphaFold model. Offered when the request asks which structures exist, for the best-resolved one, or for one bound to something; the method is filtered as the request words it (crystal, cryo-EM, NMR).
- Learned fixes: when ChimeraX refuses a command and a command with the same first word runs later in the same turn, the pair is kept in `learned.json` in Pellaeon's data folder and offered to the model the next time that command comes up, both up front and when the same refusal happens again. A complaint right after removes what that turn learned. Settings has the switch and a "view" page to forget entries; a "Share with the Pellaeon project" button on that page opens a prefilled GitHub discussion in the browser for the user to read and post, or not. Nothing is sent by Pellaeon itself. Paths and URLs in stored commands are replaced before storing.
- The static prompt is shorter: the identity section merges its repeated rules, and the worked examples (recipes) are no longer pasted into it; they stay reachable through documentation search. Fewer input tokens per request, same results.


## 0.2.4 (2026-09-30)

- A command the parser refuses now comes back with a hint built from ChimeraX's own command registration: the word the parser stopped at, whether it is a subcommand that belongs right after the command name (`surface #1 style mesh`), a misspelt option (`thick` for `thickness`), a built-in class that needs `&` (`show #1 ligand atoms`), or the list of options; an invalid option value names the allowed values and the default. The registry usage lines in the documentation index cover all registered commands (the old cap of 400 left out everything from `sequence` to `zoom`) and carry the defaults.

## 0.2.3 (2026-09-26)

- Review fixes: signing out of ChatGPT during a token refresh no longer restores the tokens; a reply cut off after a tool call runs nothing; `mutation_effect` builds the substitution only when the request asks for it; a palette name inside quoted label text is left alone; `measure sasa` of a residue asked for "in isolation" is not rewritten; `clip model #1 false` keeps the surface exempt when the request says "and its surface"; the UniProt-topology membrane orientation refuses PDB-numbered structures; "Review the view" also works with vision-capable Ollama models.
- ChatGPT is the first provider card and defaults to gpt-6-sol, the highest score in the feature comparison; recommended for anyone with a Plus or Pro account (no key to create).
- `view #1/B` right after `cartoon #1/B` in the same batch no longer fails with "No displayed objects specified": the ribbon geometry is built before the view is fitted.
- New provider: ChatGPT (Plus/Pro subscription). Settings ▸ Sign in with ChatGPT opens the browser, the tokens stay in the private key store, no API key; uses the Codex backend's models within the plan's usage limits. Sign out removes the tokens.
- New logo in the panel header.
- The manual index keeps the description of every command page. For 68 of 149 pages (among them snfg, kvfinder, mlp, nucleotides, preset) only the usage line was indexed, so requests in plain words did not find those commands.
- Docs retrieval: option names are found from plain words ("maximum intensity projection" finds `maximumIntensityProjection`), function words and bare numbers no longer count, an option's name stays with its description (`lighting gentle`), usage lines never displace a command's other passages, and a workflow is filed under its most specific command instead of the `open` it starts with. `tools/retrieval_bench.py` measures this offline.
- "3D print" no longer counts as a request to print, which had blocked `struts`.
- `clip model #1 false` asked for while slicing a surface runs as `#!1`: the structure unclipped, its surface still clipped.
- `snfg`, `worm` and `vr` are in the command directory; `snfg #1` runs as `snfg show structures #1`.
- "Each chain" with a palette runs `rainbow ... chains`; viridis, magma, plasma, inferno, cividis and turbo (not built into ChimeraX) are written out as colors.
- A command whose residue name matches nothing names the residue codes that exist (e.g. CAU for carazolol), also when ChimeraX reports a different error.
- `fitmap` of atoms says that the average map value is not a correlation.
- Placeholder paths such as `path/to/your_file.json` are never opened.
- H-bonds, contacts or clashes "between chain A and chain B" written as `restrict cross` run restricted to the other chain; `cross` pairs the chain with every other chain.
- A rainbow "from N to C" runs along each chain even when the model adds `chains`; `cartoon ... smooth off` runs as `smooth 0`.
- membrane_view tries older GPCRdb structures first (OPM lacks most recent entries) and, when OPM has none, orients a UniProt-numbered model from UniProt topology (extracellular helix ends up), with approximate boundaries.
- "Run the corrected command" is nudged only after a failed ChimeraX command, not after a tool reports that data is unavailable.
- Review the view switches on screenshots for a provider that can see them.
- What you do by hand in ChimeraX between two requests (commands typed or run from menus, their output or error, selections, models opened, closed, shown or hidden, whether the view moved) is passed to the model as a short note, so "this", "that one" and "the one I just opened" refer to it. Nothing is added when nothing changed.
- A question about one residue ("is residue 30 buried?", "what does His87 touch?") is answered from those measurements, taken before the model replies.
- `measure sasa` of a residue or range is measured inside its structure (`measure sasa #1 & ~solvent sum #1:30`); measured alone, a buried residue came out fully exposed.
- New tool `mutation_effect`, offered only when a request names a substitution (L99A, p.Arg175His) or a mutation: places the likely rotamers of the new side chain on the fixed backbone and reports clashes, contacts and polar partners lost or gained, size and charge change, and notes such as a likely cavity or a proline in a helix. It checks the wild-type residue first. `place` makes the mutation with `swapaa`.
- `show #N` on a hidden model also shows the model (`show #N models`); alone it shows only the atoms of a model that stays hidden.
- The session state says when a displayed model shows nothing (all atoms, cartoon and surface hidden).
- `compare_contacts` also ranks residues by the share of their contacts that change between the two conformations.
- `explain_residue` also reports a residue's measured surroundings: secondary structure, burial, neighbouring residues, hydrogen bonds, salt bridges, nearest ligand and other chains it touches.
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
