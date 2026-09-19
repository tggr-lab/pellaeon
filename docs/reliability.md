# Reliability

Measured, not promised. Every request below was typed into Pellaeon inside ChimeraX 1.12 on 2026-09-19 and checked mechanically afterwards (the check inspects the ChimeraX session: colors, selections, distances, opened models), not by reading the reply. The model is the default local one, **qwen3:8b** through Ollama on an RTX 4070 Super, thinking off, temperature 0.2. Cloud providers are not on this page yet because no key was available on the test machine; expect them to do better than an 8-billion-parameter local model, not worse.

Failed cases are listed with what actually ran, because they tell you more than the pass count. The harness and both scenario files are in the repository; rerun them with your own model and compare.

## Base set: 37 of 38 passed

38 requests, 89 s in total (median 1.6 s per request). Categories: analysis, annotation, color, display, edge, label, measure, motion, multi, open, safety, select, view.

### What failed

| Request | Expected | What happened |
|---|---|---|
| reset the view | view with no args | ran `nothing` (ran=[]) |

### Everything that was tested

| Category | Request | Result | s | Commands run |
|---|---|---|---|---|
| analysis | compare these two models and tell me what changed | pass | 3 | `-` |
| analysis | show hydrogen bonds | pass | 5 | `hbonds` |
| annotation | color the transmembrane regions orange | pass | 6 | `color #1:72-77,82-101,109-114,119-137,150-155,160-177,184-189,194-211,236-241,246-269,278-283,288-317,324-329,` |
| color | color by the type of aa | pass | 4 | `color #1 byresidue; color #1:ala,val,ile,leu,met,phe,trp,pro,gly white ; color #1:ser,thr,asn,gln,cys,tyr gree` |
| color | color by the type of aa | pass | 9 | `color #1 byresidue; color #1:ala,val,ile,leu,met,phe,trp,pro,gly white ; color #1:ser,thr,asn,gln,cys,tyr gree` |
| color | color by chain | pass | 5 | `color bychain` |
| color | color everything white but make residue 10 blue | pass | 1 | `color #1 white; color #1:10 blue` |
| color | color the surface by hydrophobicity | pass | 3 | `surface #1; mlp #1; color #1:ala,val,ile,leu,met,phe,trp,pro,gly white; color #1:ser,thr,asn,gln,cys,tyr green` |
| color | rainbow it | pass | 4 | `rainbow #1` |
| color | color helices red and strands yellow | pass | 1 | `color helix red; color strand yellow` |
| display | show only the cartoon | pass | 5 | `hide #1 atoms; cartoon #1` |
| display | hide the atoms | pass | 1 | `hide #1 atoms` |
| display | show the ligand as spheres and hide the water | pass | 1 | `style ligand sphere; hide solvent` |
| display | show all the atoms | pass | 1 | `show #1 atoms` |
| display | make the surface half transparent | pass | 1 | `transparency #1 50 target s` |
| edge | email this structure to my boss | pass | 2 | `-` |
| edge | color #1 red | pass | 1 | `color #1 red` |
| edge | colorea la proteina de verde | pass | 1 | `color #1 green` |
| edge | color it red | pass | 2 | `color #1 red; undo` |
| edge | what is open? | pass | 1 | `-` |
| edge | show only chain B | pass | 1 | `hide #1 target acs; show #1/B cartoon` |
| label | label residues 10 and 20 | pass | 1 | `label #1/A:10,20` |
| measure | measure the distance between residue 10 and residue 20 | pass | 3 | `distance #1:10@CA #1:20@CA; distance #1/A:10@CA #1/A:20@CA` |
| motion | make it spin | pass | 1 | `roll y 0.5` |
| motion | make it spin | pass | 2 | `roll y 0.5; stop` |
| multi | color chain A blue, chain B red, hide the water, show the heme as sticks and set a white background | pass | 2 | `color #1/A blue; color #1/B red; hide solvent; show :HEM atoms; style :HEM stick; set bgColor white` |
| open | open the af model for the gene f2rl1 and color it white | pass | 2 | `open alphafold:P55085; color #1 white` |
| open | open 4ake and 1ake | pass | 2 | `open 4ake; open 1ake` |
| safety | close everything | pass | 1 | `close` |
| safety | delete the water molecules | pass | 1 | `delete solvent` |
| safety | save a picture to my desktop | pass | 4 | `save ~/Desktop/chimerax.png; save ~/Desktop/chimerax.png width 2000 supersample 3` |
| select | select amino acids 10 15 and 20 and show their atoms | pass | 2 | `select #1:10,15,20; show sel atoms` |
| select | select amono acids 5 and 25 and mesure the distance between them | pass | 3 | `select #1:5,25; distance #1:5@CA #1:25@CA; distance #1/A:5@CA #1/A:25@CA` |
| view | color the bacground grey | pass | 1 | `set bgColor gray` |
| view | white background please | pass | 1 | `set bgColor white; graphics silhouettes true` |
| view | focos on residue 15 | pass | 1 | `view #1:15` |
| view | make it look publiction ready | pass | 2 | `preset "overall look" "publication 1"; lighting soft; graphics silhouettes true; set bgColor white` |
| view | reset the view | **fail** | 1 | `-` |

## Extra set: 54 of 62 passed

62 requests, 273 s in total (median 2.7 s per request). Categories: alignment, ambiguous-two-models, animation, background, cannot-do, closing, coloring, complaint, destructive, display, distances, focus, followup, interactions, invalid-option, io-safety, labels, measurement, motion, multi-part, nonsense, nucleic, opening, other-language, publication, raw-command, selecting, selection, state-question, surfaces, transparency, views.

### What failed

| Request | Expected | What happened |
|---|---|---|
| make the selected half transperent | The selected residues 1-100 become about 50% transparent on their cartoon, atoms and/or surface (transparency sel 50 with a target). | ran `transparency sel 50 target acs` (ran=['transparency sel 50 target acs']) |
| show every atom contact within 4 A between chain C (the inhibitor) and chain A as dashed lines | Runs contacts with a 4 A distance-only cutoff and reveal true, producing a contacts pseudobond group with bonds. | ran `interfaces #1 & protein; select zone #1/A #1/C distance 4.0; hbonds #1/A #1/C reveal true; select zone #1/A #1/C distanc` (ran=['interfaces #1 & protein', 'select zone #1/A #1/C distance 4.0', 'hbonds #1/A #1/C reveal true', 'select zone #1/A #1/C distance 4.0 range 4.0', 'hbonds #1) |
| render the coiled coil helices as fat tubes / cylinders instead of ribbons | Runs cartoon style with a tube or cylinder helix mode so the structure's helix ribbon mode is no longer the default. | ran `cartoon style protein thick 3.5 width 3.5` (ran=['cartoon style protein thick 3.5 width 3.5']) |
| rainbow each chain separately from blue at the N-terminus to red at the C-terminus | Runs rainbow at residue level so chains A and B each carry a gradient of many distinct ribbon colors. | ran `rainbow chain` (ran=['rainbow chain']) |
| show the glucose as ball and stick colored by element, and get the waters and the calcium ion out of the view | BGC atoms are displayed in ball style with element coloring while no solvent or ion atom remains visible (hide preferred over delete). | ran `style ligand ball; color byhet; hide solvent; hide ions` (ran=['style ligand ball', 'color byhet', 'hide solvent', 'hide ions']) |
| email this view to my PI | States plainly that ChimeraX cannot send email and runs no command; in particular it does not save an image unasked as a 'helpful' half-step. | ran `save ~/Desktop/view.png width 1000 supersample 3; save ~/Desktop/view.png width 1000 transparent true; save ~/Desktop/vi` (ran 4 commands) |
| highlight residue 30 | After the complaint it takes a different approach: shows residue 30 atoms, sets stick style and colors them yellow (the color the user named). | ran `color :30 orange; style #1 stick; color #1 yellow` (ran=['color :30 orange', 'style #1 stick', 'color #1 yellow']) |
| label residue 5 | 'it' is the label just added: increases the label height/size (zooming in is an acceptable fallback) rather than asking. | ran `label #1/A:5; size #1 stickrad 0.5` (ran=['label #1/A:5', 'size #1 stickrad 0.5']) |

### Everything that was tested

| Category | Request | Result | s | Commands run |
|---|---|---|---|---|
| alignment | superimpose 7bqy onto 6lu7 so i can compare the N3 binding poses | pass | 6 | `matchmaker #2 to #1; view; hide #2/C targ acs; show #2/C ligand; show #2 ligand; show #2 ligand atoms; show li` |
| ambiguous-two-models | hide the atoms of the small one | pass | 1 | `hide #2 atoms` |
| ambiguous-two-models | close the one i dont need | pass | 3 | `-` |
| animation | morph between the open (4ake) and closed (1ake) conformations, 40 frames | pass | 5 | `matchmaker #2 to #1; morph #1,#2 frames 40; morph #1,2 frames 40; coordset #3 1,51 bounce true loop 3` |
| animation | record a short movie of it spinning once around y and save it as ~/Desktop/zipper.mp4 | pass | 14 | `preset pub; lighting soft; roll y 1 360 center #1; movie record; roll y 1 360 center #1; wait 360; movie encod` |
| background | tuen the bacground black | pass | 1 | `-` |
| background | color the background grey | pass | 3 | `set bgColor gray` |
| cannot-do | email this view to my PI | **fail** | 12 | `save ~/Desktop/view.png width 1000 supersample 3; save ~/Desktop/view.png width 1000 transparent true; save ~/` |
| closing | clsoe everything | pass | 1 | `close` |
| coloring | color by chain | pass | 1 | `color bychain` |
| coloring | color it like a rainbow | pass | 1 | `rainbow #1` |
| coloring | color resdue 159 in blue please | pass | 1 | `select #1:159; show sel atoms; style sel stick; color sel blue` |
| coloring | make it white | pass | 3 | `color #1 white; color #1 & helix blue target c` |
| coloring | color it by pLDDT confidence again | pass | 2 | `color bfactor #1 palette alphafold` |
| coloring | color helices salmon, strands sky blue and the loops grey | pass | 11 | `color /A & helix salmon target c; color /A & strand sky blue target c; color /A & loop grey target c; color /A` |
| coloring | rainbow each chain separately from blue at the N-terminus to red at the C-terminus | **fail** | 7 | `rainbow chain` |
| complaint | make it look nicer | pass | 13 | `set bgColor white; graphics silhouettes true; lighting soft; preset "publication 1"` |
| complaint | highlight residue 30 | **fail** | 2 | `color :30 orange; style #1 stick; color #1 yellow` |
| destructive | delete all the water molecules | pass | 1 | `delete solvent` |
| destructive | get rid of it, i dont need it anymore | pass | 14 | `close #1` |
| destructive | im done, quit chimerax | pass | 3 | `close` |
| destructive | save a picture of this to my desktop | pass | 5 | `save ~/Desktop/chimerax.png; save ~/Desktop/chimerax.png; save ~/Desktop/chimerax.png` |
| display | hide all the atoms i only want the cartoon | pass | 1 | `hide #1 atoms; cartoon #1` |
| display | show the ligand as spheres and hide the water | pass | 1 | `style ligand sphere; hide solvent` |
| display | show it's atoms | pass | 1 | `show #1 atoms` |
| display | show the glucose as ball and stick colored by element, and get the waters and the calcium ion out of the view | **fail** | 2 | `style ligand ball; color byhet; hide solvent; hide ions` |
| display | render the coiled coil helices as fat tubes / cylinders instead of ribbons | **fail** | 1 | `cartoon style protein thick 3.5 width 3.5` |
| display | show only the beta1 subunit (chain B) - hide all the other chains including their hemes | pass | 2 | `hide #1/A,C,D targ acs; show #1/B targ acs` |
| distances | select amono acids 100 and 150 and mesure the distance between them | pass | 2 | `select #1:100,150; distance #1:100@CA #1:150@CA` |
| distances | color the entire protein white, after that select amino acids 160 and 300 - color these red and show their atoms. also, mesure the distance between these two | pass | 2 | `color #1 white; select #1:160,300; show sel atoms; style sel stick; color sel red; distance #1:160@CA #1:300@C` |
| focus | focus on these two amino acids | pass | 4 | `view sel` |
| focus | i want to focuse the view on 159 | pass | 4 | `select #1:159; view #1:159` |
| followup | label residue 5 | **fail** | 5 | `label #1/A:5; size #1 stickrad 0.5` |
| followup | color chain A orange | pass | 5 | `color #1/A orange; color #1/B purple` |
| followup | color it red | pass | 2 | `color #1 red; undo` |
| interactions | check for steric clashes between the N3 inhibitor (chain C) and the protease, show them if there are any | pass | 12 | `clashes #1/C #1/A make f; view #1/C; view #1/A` |
| interactions | show every atom contact within 4 A between chain C (the inhibitor) and chain A as dashed lines | **fail** | 12 | `interfaces #1 & protein; select zone #1/A #1/C distance 4.0; hbonds #1/A #1/C reveal true; select zone #1/A #1` |
| interactions | show the hydrogen bonds between the bound glucose and the protein in yellow and show the residues involved | pass | 7 | `hbonds #1 ligand reveal true color yellow; select hbonds; color sel orange; hbonds #1 ligand reveal true color` |
| invalid-option | color by aa type | pass | 5 | `color #1:ala,val,ile,leu,met,phe,trp,pro,gly white; color #1:ser,thr,asn,gln,cys,tyr green; color #1:lys,arg,h` |
| io-safety | save a 2400 px wide png with transparent background to ~/Desktop/mpro_n3.png, supersample 3 | pass | 7 | `save ~/Desktop/mpro_n3.png width 2400 transparent true supersample 3; save ~/Desktop/mpro_n3.png width 2400 tr` |
| io-safety | save this session to ~/Desktop/mpro_compare.cxs | pass | 1 | `save ~/Desktop/mpro_compare.cxs` |
| labels | put labels on aa 159 and 300 | pass | 6 | `label #1/A:159,300` |
| labels | label the four heme groups with their residue name and chain | pass | 17 | `label #1/A :46,50,51,53 bgcolor black; label #1/B :46,50,51,53 bgcolor black; label #1/C :46,50,51,53 bgcolor ` |
| measurement | measure the angle between the CA atoms of residues 5, 15 and 25 in chain A | pass | 2 | `angle #1/A:5@CA #1/A:15@CA #1/A:25@CA` |
| measurement | whats the CA rmsd between chain A of #1 and chain A of #2 as they are right now, dont move anything | pass | 2 | `rmsd #1/A@CA to #2/A@CA` |
| motion | make it spin | pass | 1 | `roll y 0.5` |
| motion | spin the view slowly | pass | 3 | `roll y 0.5; stop; roll y 0.1; stop` |
| multi-part | open 2gbp, hide the water, show the glucose as spheres, color the protein by secondary structure, set the background to white, and label the ligand | pass | 3 | `open 2gbp; hide solvent; show ligand atoms; style ligand sphere; color #1 & helix red; color #1 & strand yello` |
| nonsense | asdfgh jkl qwerty 🚀🚀 | pass | 2 | `-` |
| nucleic | show the DNA as a ladder | pass | 3 | `nucleotides #1 layout ladder; color nucleic tan; nucleotides #1 ladder; color nucleic tan` |
| opening | open the af model for the gene f2rl1 | pass | 6 | `open alphafold:P55085` |
| other-language | colorea la cadena A de azul y muestra los ligandos como esferas | pass | 3 | `color #1/A bychain blue target ac; show ligand atoms; style ligand sphere; color #1/A blue target ac; show lig` |
| publication | make it look publiction ready | pass | 5 | `preset "publication 1"; set bgColor white; graphics silhouettes true` |
| raw-command | color #1 red | pass | 1 | `color #1 red` |
| selecting | select postions 159 and 300 and color them green. the rest of the protein should be white | pass | 2 | `color #1 white; select #1:159,300; show sel atoms; style sel stick; color sel green` |
| selecting | select aas 159 and 300 | pass | 3 | `select #1:159,300; select ~sel` |
| selection | select all the protein residues within 4.5 A of the glucose | pass | 3 | `select zone ligand 4.5 protein; show sel atoms; style sel stick; label sel` |
| state-question | what is open? | pass | 1 | `-` |
| surfaces | show me an electrostatic potential surface of the protein so i can see the charge around the sugar site | pass | 15 | `coulombic #1; view ligand` |
| surfaces | surface the protease (chain A only) and color it by hydrophobicity | pass | 5 | `surface #1/A; mlp #1/A; hide #1 target acs; surface #1/A; mlp #1/A; color #1:ala,val,ile,leu,met,phe,trp,pro,g` |
| transparency | make the selected half transperent | **fail** | 1 | `transparency sel 50 target acs` |
| views | remember this orientation as 'chimaera', then turn 90 degrees about y, then go back to the chimaera view | pass | 2 | `view name chimaera; turn y 90; view chimaera` |

## How to read this

- *pass* means the mechanical check found the requested state in the session, for example four distinct chain colors after "color by chain". It does not grade the wording of the reply.
- Requests that need a confirmation (close, delete, save) count as passed when the confirmation card appeared with the right commands; the harness approves them.
- A local 8B model is the floor, not the ceiling. If a request fails for you, try rephrasing to one action, or switch to a cloud provider in Settings.
- Rerun: `chimerax --nogui --exit --script "tests_chimerax/scenarios.py <model>"` then `python tools/build_reliability.py /tmp/pellaeon_scenarios_<model>.json`.
