"""Known mistakes models make with ChimeraX commands, and what to suggest instead.

Applied to failed commands only: the suggestion is added to the tool result so
the model can correct itself deterministically instead of guessing again.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# (command regex, error regex or None, suggestion)
_RULES: List[Tuple[str, Optional[str], str]] = [
    (r"^color\b.*\bby(aa|aminoacid|residue|restype|type|residuetype)\b", None,
     "There is no such color scheme. Color residue classes explicitly: "
     "color #1:ala,val,ile,leu,met,phe,trp,pro,gly white ; color #1:ser,thr,asn,gln,cys,tyr green ; "
     "color #1:lys,arg,his blue ; color #1:asp,glu red"),
    (r"^color\b.*\b(by(ss|secondary\w*|structure)|byattr\w*\s+(ss|secondary\w*))\b", None,
     "Use built-in specifiers: color #1 & helix red ; color #1 & strand yellow ; color #1 & coil gray"),
    (r"^color\b.*\bby(hydrophobicity|hydrophobic|lipophilicity)\b", None,
     "For a surface use: surface #1 ; mlp #1 . For cartoons color residue classes explicitly."),
    (r"^color\b.*\bby(plddt|confidence|bfactor)\b", None,
     "Use: color bfactor #1 palette alphafold"),
    (r"^color\s+\S+\s+bfactor\b", None, "Put bfactor right after color: color bfactor #1 palette alphafold"),
    (r"^morph\b", None, "Model list without repeated #: morph #1,2 frames 40  (after matchmaker #2 to #1)"),
    (r"^(contacts|clashes)\b.*&", None,
     "Do not intersect the two sets with &; use restrict: contacts #1/A restrict #1/C distance 4 reveal true"),
    (r"^color\s+~sel\b", None,
     "color does not accept ~sel. Color everything first, then the selection: color #1 white ; color sel blue"),
    (r"^distance\b", r"four atoms|atoms|exactly two|more than",
     "Each residue number matched several atoms (the model has several chains). Name the chain and the atom: "
     "distance #1/A:10@CA #1/A:20@CA"),
    (r"^(show|hide)\b.*\bwater\b", None, "Use the built-in specifier: hide solvent  (or :HOH)"),
    (r"^set\s+bg_color\b", None, "The option is camelCase: set bgColor white"),
    (r"^(select|sel)\s+(add|subtract)\b", None, "The spec comes first: select #1:10 add"),
    (r"^(turn|roll|rock)\b", r"expected", "Syntax: roll y 0.5 (continuous) or turn y 90 (once). Stop with: stop"),
    (r"^(label)\b", r"expected", "Syntax: label #1:10,20  (residues) or label #1:10@CA atoms"),
    (r"^(show|hide)\s+only\b", None,
     "There is no 'only'. Hide everything, then show the part: hide #1 target acs ; cartoon #1/B ; show #1/B atoms"),
    (r"^(cartoon|ribbon)\s+hide\b", None, "Use: ~cartoon #1  or  cartoon hide #1 (spec after hide)"),
    (r"^transparency\b", r"expected", "Syntax: transparency #1 50 target s   (s=surfaces, c=cartoons, a=atoms)"),
    (r"^open\s+alphafold\s", None, "Use the prefix form without a space: open alphafold:P07550"),
    (r"^(alphafold|esmfold)\s+fetch\b", None, "Use: open alphafold:ACCESSION (get the accession with resolve_protein)"),
    (r"^open\b", r"404|not found|failed", "That identifier does not exist. Use resolve_protein for gene/protein names, or a real 4-character PDB id."),
    (r"^(surface|surf)\b.*\bcolor\b", None, "Make the surface first, then color it: surface #1 ; color #1 red target s"),
    (r"^view\s+(reset|all|everything)\b", None, "Use: view   (no arguments) to see everything"),
    (r"^(measure|calc|calculate)\s+distance\b", None, "Use: distance #1/A:10@CA #1/A:20@CA"),
    (r"^(measure|calc|calculate)\s+angle\b", None, "Use: angle #1/A:10@CA #1/A:11@CA #1/A:12@CA"),
    (r"^(hbond|hbonds|hydrogenbonds)\b", r"unknown command|expected",
     "Syntax: hbonds #1 reveal true  (add color yellow, restrict ligand, etc.)"),
    (r"^rainbow\b", r"expected", "Syntax: rainbow #1  or  rainbow #1 chains palette red:blue"),
    (r"^(spin|rotate)\b", None, "Use: roll y 0.5  (continuous spin) or turn y 90 (rotate once)"),
    (r"^(zoom|focus|center|centre)\b", r"unknown command", "Use: view #1:10  (focus on residues) or view (everything)"),
    (r"^(background|bg)\b", None, "Use: set bgColor white"),
    (r"^(preset)\b", r"expected|no preset", "Try: preset \"overall look\" \"publication 1\"  or  lighting soft ; graphics silhouettes true"),
]
_COMPILED = [(re.compile(c, re.I), re.compile(e, re.I) if e else None, s) for c, e, s in _RULES]


def suggest(command: str, error: str = "") -> Optional[str]:
    cmd = command.strip()
    for crx, erx, text in _COMPILED:
        if crx.search(cmd) and (erx is None or erx.search(error or "")):
            return text
    return None
