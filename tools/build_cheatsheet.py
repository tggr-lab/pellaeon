"""One-off: build src/data/cheatsheet.json and recipes.json from the alpha project.

Usage: python tools/build_cheatsheet.py "/path/to/Pellaeon_alpha_01 (Copy 1)"
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "src", "data")


def parse_llm_block(text: str):
    m = re.search(r"### LLM &(?:amp;)? Retrieval Context\s*\n(.*?)(?:\n###|\Z)", text, re.S)
    if not m:
        return None
    block = m.group(1)
    fields = {}
    for key in ("Purpose", "Keywords", "Use Cases", "Natural Language Queries", "Relation to Other Commands", "Notes"):
        fm = re.search(r"\*\*%s:\*\*\s*(.*?)(?=\n\*\s+\*\*|\Z)" % re.escape(key), block, re.S)
        if fm:
            fields[key] = " ".join(fm.group(1).split())
    if not fields.get("Purpose"):
        return None
    keywords = [k.strip() for k in fields.get("Keywords", "").split(",") if k.strip()]
    nl = re.findall(r'"([^"]+)"', fields.get("Natural Language Queries", ""))
    return {
        "purpose": fields.get("Purpose", ""),
        "keywords": keywords[:30],
        "nl_queries": nl[:15],
        "notes": fields.get("Notes", "")[:600],
        "related": fields.get("Relation to Other Commands", "")[:400],
    }


# Hand-written purposes for commands the alpha never annotated.
FALLBACK_PURPOSE = {
    "cartoon": "Show or hide cartoon/ribbon backbone representation; set cartoon style.",
    "clashes": "Find and show steric clashes between atoms.",
    "coordset": "Play or step through trajectory frames / coordinate sets.",
    "crossfade": "Smoothly fade between two views or displays (for movies).",
    "device": "Configure VR/space-navigator devices.",
    "dssp": "Recompute secondary structure assignments.",
    "esmfold": "Fetch or predict ESMFold structures.",
    "fitmap": "Fit atomic models or maps into density maps.",
    "fly": "Animate the camera through a series of saved views.",
    "foldseek": "Search for structurally similar proteins.",
    "info": "Report information about models, chains, residues, atoms, selection in the log.",
    "key": "Draw a color key (legend) for value-based coloring.",
    "kvfinder": "Detect cavities and pockets.",
    "pwd": "Print the current working directory.",
    "rename": "Rename models or chains.",
    "renumber": "Renumber residues.",
    "resfit": "Show residue fit to density.",
    "rmsd": "Compute RMSD between two atom sets.",
    "rna": "Build RNA models.",
    "rock": "Rock the view back and forth continuously.",
    "roll": "Spin the view continuously (roll y 0.5); smaller step = slower. Stop with 'stop'.",
    "runscript": "Run a Python or ChimeraX command script file with arguments.",
    "save": "Save images (png), sessions (cxs), structures (pdb/cif), maps and other files.",
    "scalebar": "Show a scale bar.",
    "segger": "Segment maps with Segger.",
    "segmentation": "Work with segmentations of maps.",
    "select": "Select atoms/residues/chains/models by specifier; add, subtract, invert, clear; select by zone.",
    "setattr": "Set attribute values on atoms, residues, chains or models.",
    "ui": "Show/hide tools and control the graphical interface (ui tool show Log).",
    "hide": "Hide atoms, cartoons, surfaces, ribbons for a specifier.",
    "show": "Show atoms, cartoons, surfaces for a specifier.",
    "stop": "Stop ongoing motion (roll, rock, turn, wait).",
    "echo": "Echo text to the log.",
    "contacts": "Find atoms in contact (within a distance) between specified sets.",
    "rainbow": "Color sequentially along chains (blue to red).",
    "undo": "Undo the last undoable command (redo to reverse).",
    "time": "Time how long a command takes.",
    "mousemode": "Assign functions to mouse buttons.",
}

RECIPES = [
    {"request": "open the af model for the gene f2rl1", "commands": ["open alphafold:P55085"],
     "note": "P55085 came from resolve_protein('F2RL1'); never guess accessions."},
    {"request": "open the alphafold model for the gene f2rl1, after that color it in white but color amino acid 159 in blue",
     "commands": ["open alphafold:P55085", "color #1 white", "show #1:159 atoms", "style #1:159 stick", "color #1:159 blue", "view #1:159"]},
    {"request": "i want to see the human protein coded by the gene f2rl2", "commands": ["open alphafold:P55085"],
     "note": "resolve_protein first; open the AlphaFold model by accession."},
    {"request": "open 1zik", "commands": ["open 1zik"]},
    {"request": "select amino acids 227 156 159 and 326 and show their atoms",
     "commands": ["select #1:227,156,159,326", "show sel atoms", "style sel stick"]},
    {"request": "color each one of these in a diffrent color",
     "commands": ["color #1:227 red", "color #1:156 green", "color #1:159 blue", "color #1:326 orange"],
     "note": "'these' = the residues just selected; get_state shows the selection."},
    {"request": "select amono acids 100 and 150 and mesure the distance between them",
     "commands": ["select #1:100,150", "distance #1:100@CA #1:150@CA"]},
    {"request": "delete chains A B E and G", "commands": ["delete #1/A,B,E,G"], "note": "delete needs confirmation."},
    {"request": "close it, instead open f2rl3, and make it look publiction ready",
     "commands": ["close #1", "open alphafold:Q96RI0", "preset \"overall look\" \"publication 1\"", "lighting soft", "graphics silhouettes true", "set bgColor white", "view"]},
    {"request": "select postions 159 and 300 and color them green. the rest of the protein should be white",
     "commands": ["color #1 white", "select #1:159,300", "show sel atoms", "style sel stick", "color sel green"]},
    {"request": "make it spin", "commands": ["roll y 0.5"]},
    {"request": "make it spin in place", "commands": ["view", "roll y 0.5"]},
    {"request": "stop it", "commands": ["stop"]},
    {"request": "slower", "commands": ["stop", "roll y 0.1"]},
    {"request": "reverse selection", "commands": ["select ~sel"]},
    {"request": "deselect all", "commands": ["select clear"]},
    {"request": "make the selected half transperent", "commands": ["transparency sel 50 target acs"]},
    {"request": "color the bacground grey", "commands": ["set bgColor gray"]},
    {"request": "show the surface and color it by hydrophobicity", "commands": ["surface #1", "mlp #1"]},
    {"request": "show hydrogen bonds", "commands": ["hbonds #1 reveal true"]},
    {"request": "show the ligand as spheres and hide water", "commands": ["show ligand atoms", "style ligand sphere", "hide solvent"]},
    {"request": "color by chain", "commands": ["color #1 bychain"]},
    {"request": "rainbow it", "commands": ["rainbow #1"]},
    {"request": "focos on the selection", "commands": ["view sel"]},
    {"request": "reset the view", "commands": ["view"]},
    {"request": "label residues 159 and 300", "commands": ["label #1:159,300"]},
    {"request": "show the transmembrane helices of PAR2 in orange",
     "commands": ["color #1 white", "color #1:47-73,84-107,124-147,169-192,222-247,271-294,305-329 orange target ac"],
     "note": "ranges from protein_features(accession, ['Transmembrane']) after resolve_protein('PAR2')."},
    {"request": "save a picture", "commands": ["save ~/Desktop/chimerax.png width 2000 supersample 3"],
     "note": "save needs confirmation; the interface asks."},
    {"request": "close everything", "commands": ["close"], "note": "close needs confirmation."},
    {"request": "align these two proteins", "commands": ["matchmaker #2 to #1", "view"]},
    {"request": "what is open?", "commands": [], "note": "Use get_state and answer in words; no commands needed."},
]


def main(alpha_dir: str) -> None:
    docs = os.path.join(alpha_dir, "cleaned_docs", "commands")
    cheatsheet = {}
    for name in sorted(os.listdir(docs)):
        if not name.endswith(".md"):
            continue
        cmd = name[:-3]
        if cmd in ("commands", "usageconventions", "symbols", "linux", "colornames", "atomspec", "palette"):
            continue
        with open(os.path.join(docs, name), "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        info = parse_llm_block(text)
        if info is None:
            info = {"purpose": FALLBACK_PURPOSE.get(cmd, ""), "keywords": [], "nl_queries": [], "notes": "", "related": ""}
        cheatsheet[cmd] = info
    for cmd, purpose in FALLBACK_PURPOSE.items():
        cheatsheet.setdefault(cmd, {"purpose": purpose, "keywords": [], "nl_queries": [], "notes": "", "related": ""})
        if not cheatsheet[cmd].get("purpose"):
            cheatsheet[cmd]["purpose"] = purpose
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "cheatsheet.json"), "w", encoding="utf-8") as f:
        json.dump(cheatsheet, f, indent=1, ensure_ascii=False)
    with open(os.path.join(OUT, "recipes.json"), "w", encoding="utf-8") as f:
        json.dump(RECIPES, f, indent=1, ensure_ascii=False)
    annotated = sum(1 for v in cheatsheet.values() if v.get("keywords"))
    print("cheatsheet: %d commands (%d with full annotations); recipes: %d" % (len(cheatsheet), annotated, len(RECIPES)))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "Pellaeon_alpha_01 (Copy 1)"))
