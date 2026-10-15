# A ligand in its pocket

HIV-1 protease with the inhibitor indinavir (PDB 1HSG), in four requests, recorded in ChimeraX with the Pellaeon panel.

<!--VIDEO gui/short_pocket | The four requests in one video (78 s). | A ligand in its pocket, 78 seconds-->

### 1. Open the structure

> open the HIV protease structure 1hsg and show the inhibitor indinavir as orange sticks

```
open 1hsg
show :MK1 atoms; style :MK1 stick; color :MK1 orange
```

MK1 is the residue name of indinavir in this entry.

<!--STEP short_pocket_open | Indinavir as orange sticks in the middle of the protease dimer, and the reply | Indinavir in the protease.-->

### 2. The pocket as a surface

> show the binding pocket: a white surface of the residues within 5 Å of indinavir, 70% transparent

```
surface (protein & (:MK1 :<5)) color white transparency 70
```

`:<5` selects the residues with any atom within 5 Å of the ligand.

<!--STEP short_pocket_pocket | A transparent white surface over the residues around the inhibitor | The pocket surface.-->

### 3. Hydrogen bonds

> show the hydrogen bonds between indinavir and the protein, and label the residues it binds

```
hbonds :MK1 restrict protein reveal true select true
label (sel & protein) residues
```

<!--STEP short_pocket_hbonds | Hydrogen bonds as dashes, the bonded residues labelled | Hydrogen bonds and the residues they reach.-->

### 4. A close-up

> hide the cartoon except around the pocket and give me a clean close-up of indinavir in its pocket

```
hide #1 target c
cartoon (#1 & protein & (:MK1 :<5))
view (#1 & (:MK1 :<5))
```

<!--STEP short_pocket_closeup | Close-up of indinavir in its pocket with the surface and hydrogen bonds | The close-up.-->

**Save figure** writes the image, the session and the commands to a folder ([tutorial, step 5b](tutorial.md#step-5b-save-a-figure-you-can-reproduce)).
