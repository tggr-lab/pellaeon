# Electrostatics and hydrophobicity

The p53 core domain bound to DNA (PDB 1TSR), in four requests, recorded in ChimeraX with the Pellaeon panel.

<!--VIDEO gui/short_surfaces | The four requests in one video (66 s). | Electrostatics and hydrophobicity, 66 seconds-->

### 1. Open the structure

> open 1tsr, keep only chain B and the DNA, and show them as cartoons

```
open 1tsr
hide #1 target acs; cartoon #1/B; cartoon #1/E-F
```

The entry has three copies of the domain (chains A, B, C); chain B is the one on the DNA (chains E and F).

<!--STEP short_surfaces_open | The p53 core domain as a cartoon on the DNA double helix | Chain B on the DNA.-->

### 2. Electrostatic potential

> show the protein as a surface colored by electrostatic potential

```
coulombic #1/B
```

Red is negative, white neutral, blue positive.

<!--STEP short_surfaces_coulombic | The domain as a surface colored red to blue by Coulombic potential | Coulombic potential.-->

### 3. Hydrophobicity

> color the surface by hydrophobicity instead

```
mlp #1/B
```

Molecular lipophilic potential on the same surface: hydrophobic patches gold, polar ones teal.

<!--STEP short_surfaces_hydrophobic | The same surface colored teal to gold by hydrophobicity | Hydrophobicity.-->

### 4. Which face binds the DNA

> back to electrostatics, and make the DNA gray so the positive face it binds stands out

```
coulombic #1/B
color #1/E-F gray target c
```

The positively charged face of the domain lies against the phosphate backbone.

<!--STEP short_surfaces_dna | Electrostatic surface with the DNA in gray against its positive face | The positive face on the DNA.-->
