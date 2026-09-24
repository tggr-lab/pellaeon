"""Known mistakes models make with ChimeraX commands, and what to suggest instead.

Applied to failed commands only: the suggestion is added to the tool result so
the model can correct itself deterministically instead of guessing again.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# (command regex, error regex or None, suggestion)
_RULES: List[Tuple[str, Optional[str], str]] = [
    (r"::\w+\s*(>=|<=|>|<|=)", r"not supported between instances",
     "An attribute test compares against a bare number with no space and no unit: select ::attr>3 . "
     "Quote text values: select ::attr=\"hydrophobic\" . If the attribute was set outside Pellaeon it may be "
     "unregistered; color by it instead: color byattribute r:attr #1 palette bluered"),
    (r"^alphafold\s+pae\b", r"No predicted aligned error",
     "Give the UniProt accession so ChimeraX fetches the PAE for the model already open: "
     "alphafold pae #1 uniprotId P00568 colorDomains true  (use the model's own accession; do not open it again)."),
    (r"^cartoon\s+style\b.*\bhelix\s+tube", None, "The option is modeHelix: cartoon style #1 modeHelix tube"),
    (r"^camera\s+(mode|mono\s+ortho|orthographic)\b", None, "The projection is a plain word: camera ortho  (back: camera mono)"),
    (r"^label\b.*\boffset\b", r"tuple|number", "offset takes x,y,z with commas and no spaces: label #1:10 offset 0,0,10"),
    (r"^size\s+by\w*", r"Expected|keyword|invalid", "Attribute first, then atoms, then value:radius pairs: size byattribute bfactor #1 20:.6 300:9"),
    (r"^(open|save)\b.*(your[_-]|/path/to/|<[^>]+>|example\.(cif|pdb))", None,
     "That is a placeholder, not a real file. Ask the user for the file name, or say what they should open."),
    (r"^key\s+\w", r"colourlovers|palettes\?keywords",
     "That word was read as a palette name. For a key of an attribute coloring, repeat that coloring with key true: "
     "color bfactor #1 palette bluered key true (also mlp #1 key true, coulombic #1 key true). A hand-made key lists "
     "color:label pairs: key blue:low white:mid red:high  (or key delete)."),
    (r"^rmsd\b", r"differs from number",
     "rmsd needs the same atoms in the same order, which different structures rarely have. matchmaker already "
     "reported the RMSD of every pair it superposed (the 'RMSD between N pruned atom pairs' lines of its result): "
     "report those numbers and stop."),
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
    (r"^(select|sel)\s+(?!add\b|subtract\b|intersect\b|clear\b|up\b|down\b).*\s(add|subtract)\s*$", None,
     "The subcommand comes first: select add #1:10 (or select subtract #1:10)."),
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
    (r"^color\b.*\bbychain\s+(?!target\b)\w", None,
     "color bychain takes no palette or style word. For your own per-chain colors use rainbow: "
     "rainbow #2 chains palette #7986cb:#4db6ac:#ffd54f:#e57373:#ba68c8 target acs  (soft/pastel), or rainbow #2 chains"),
    (r"^color\s+(smooth|soft|pastel|nice|pretty)\b", None,
     "There is no such color scheme. Pick real colors: rainbow #2 chains palette #7986cb:#4db6ac:#ffd54f:#e57373:#ba68c8 target acs, "
     "or color #2 cornflowerblue target acs"),
    (r"^build\b.*\b(rise|twist|helical|rod|angle)\b", None,
     "build makes new atoms; a helical assembly is symmetry: sym #1 h,<rise>,<twist>,<n> copies true  "
     "(TMV: sym #1 h,1.408,22.03,150 copies true). Biological assembly: sym #1 assembly 1 copies true"),
    (r"^(sym|symmetry)\b", r"expected|unknown|Missing|invalid",
     "Syntax: sym #1 assembly 1 copies true  (biological assembly);  sym #1 h,rise,twist,n copies true  (helix);  "
     "sym #1 c6 copies true  (cyclic).  Then work on the new model (#2)."),
    (r"^surface\b.*\b(smooth|pastel|soft)\b", None,
     "surface has no such option. Smoothness: surface #2 resolution 6 gridSpacing 1 (Gaussian) or surface #2 (molecular); "
     "colors afterwards: rainbow #2 chains palette ... target s"),
    (r"^(move|translate|shift|nudge)\s+#", None,
     "move takes an axis first: move z 10 models #1 (screen axes x, y, z; or an atom pair). Moving a whole model out of the "
     "membrane frame breaks the OPM orientation; a low-confidence tail that sits 'inside' the membrane is placed there by "
     "AlphaFold, not by the orientation: hide or fade it instead (hide #1:1-36 target ac, or transparency #1:1-36 70 target c)."),
    (r"^translate\b", None, "There is no translate command: move z 10 models #1"),
    (r"^(preset)\b", r"expected|no preset", "Try: preset \"overall look\" \"publication 1\"  or  lighting soft ; graphics silhouettes true"),
    (r"^colou?r\b.*\b(by[_ ]?)?(displacement|movement|moved|shift|ca[_ ]?rmsd|per[_ ]?residue[_ ]?rmsd)\b", None,
     "There is no such color scheme. Call the compare_structures tool (reference, other): it superposes the two models "
     "and colors the other one by how far each residue's CA moved."),
    (r"^volume\b.*\bplanes?\b", None,
     "One plane is a one-voxel-thick region shown as an image: volume #1 region 0,0,50,99,99,50 style image "
     "(grid indices x0,y0,z0,x1,y1,z1; the state gives the grid size, the middle is half of it)."),
]
_COMPILED = [(re.compile(c, re.I), re.compile(e, re.I) if e else None, s) for c, e, s in _RULES]


def suggest(command: str, error: str = "") -> Optional[str]:
    cmd = command.strip()
    for crx, erx, text in _COMPILED:
        if crx.search(cmd) and (erx is None or erx.search(error or "")):
            return text
    return None


# ---------------------------------------------------------------------------- deterministic rewrites
# Applied to the model's commands BEFORE they run, when the rewrite is certain: the original is a syntax
# ChimeraX rejects (or accepts but reads differently) and the intended meaning is unambiguous.

_OP = r"(<=|>=|!=|==|<|>|=)"
_NUM = r"(-?\d+(?:\.\d+)?)"
_ATOM_ATTRS = r"(bfactor|occupancy)"
_RES_ATTRS = r"(area|sasa)"
# `:bfactor<50`, `@bfactor<50`, `& bfactor < 50`, `(bfactor > 0)`: all mean the atom attribute test @@bfactor<50
_ATOM_ATTR_RE = re.compile(r"(?:(?<![@:])[:@](?![:@])|(?<![@:\w]))" + _ATOM_ATTRS + r"\s*" + _OP + r"\s*" + _NUM + r"(?![\w.])", re.I)
# `:area>40`, `& area > 40`, `sasa>40`: the residue attribute test ::area>40 (measure sasa sets `area`)
_RES_ATTR_RE = re.compile(r"(?:(?<![@:]):(?![:@])|(?<![@:\w]))" + _RES_ATTRS + r"\s*" + _OP + r"\s*" + _NUM + r"(?![\w.])", re.I)
# any other `:name>1.8` (seq_conservation, a table column): a residue name is never compared to a number, so a
# single colon before a comparison is a residue attribute test missing its second colon
_ANY_RES_ATTR_RE = re.compile(r"(?<![@:]):(?![:@])([A-Za-z_][A-Za-z0-9_]*)\s*" + _OP + r"\s*" + _NUM + r"(?![\w.])")
_SPEC_WORDS = ("select", "show", "hide", "color", "colour", "style", "label", "cartoon", "transparency", "view", "surface",
               "delete", "info", "rainbow", "size", "contacts", "hbonds", "clashes", "distance", "angle", "cofr", "zone", "sel")
_SPEC_LIST_RE = re.compile(r",\s*(?=[#/])")          # `#1/A:25, #1/B:25` or `/A:25,/B:25`: ChimeraX wants a space
_INFO_SPEC_RE = re.compile(r"^(info)\s+(?=[#/:])(\S+)\s*$", re.I)
_MM_SHOW_RE = re.compile(r"\s+show(?:Alignment)?\s+(?:true|t|1|yes)\b", re.I)
_WANTS_VIEWER_RE = re.compile(r"\balign(ment|ed)?\s+(window|viewer|panel)|\bviewer\b|\bwindow\b|\bpanel\b|"
                              r"\b(see|show|display|open)\b.{0,20}\b(sequences?|alignment)\b", re.I)
_REST_RE = re.compile(r"\b(the )?rest\b|\beverything (else|but|except)\b|\b(all|anything) else\b|\bexcept\b|\bonly\b|"
                      r"\bjust (keep|show|leave)\b|\bnothing (else|but)\b", re.I)
_CARTOON_WORD_RE = re.compile(r"\b(cartoons?|ribbons?|backbone trace)\b", re.I)
# `color #2 bychain palette a:b` is refused (bychain takes no palette); rainbow over chains does what was meant
_BYCHAIN_PALETTE_RE = re.compile(r"^colou?r\s+(\S+)\s+bychain\s+palette\s+(\S+)(.*)$", re.I)
# `#2.1-#2.60`: a submodel range repeats the parent; ChimeraX wants `#2.1-60`
_SUBMODEL_RANGE_RE = re.compile(r"#(\d+)\.(\d+)-#?\1\.(\d+)")     # #2.2-#2.60 and #2.2-2.60
# `show #1 protein atoms`: a built-in class after a spec needs `&`
_CLASS_AFTER_SPEC_RE = re.compile(r"^(\w+)\s+(#[\d.,\-/:@A-Za-z]+|sel)\s+(protein|nucleic|ligand|solvent|ions|water|backbone|sidechain|helix|strand|coil)\b", re.I)
# `#3/DNA`, `#3/RNA`, `#3/protein`: a class written as a chain id
_CLASS_AS_CHAIN_RE = re.compile(r"(#\d+(?:\.\d+)?)/(DNA|RNA|protein|nucleic|ligand)\b", re.I)
_TARGET_RE = re.compile(r"\btarget\s+\S+|\b(atoms|cartoons?|ribbons?|surfaces?|models?|bonds|pseudobonds|pbonds)\b", re.I)


def _outside_quotes(cmd: str, fn) -> str:
    """Apply fn to the unquoted stretches of cmd only (label text and file names stay as written)."""
    parts = re.split(r"(\"[^\"]*\"|'[^']*')", cmd)
    return "".join(p if (p[:1] in "\"'" and len(p) > 1) else fn(p) for p in parts)


def rewrite(command: str, request: str = "") -> Tuple[str, List[str]]:
    """Return (command, notes): the command in the syntax ChimeraX understands, and one note per change.

    `request` is the user's text for this turn: some rewrites depend on what was asked (a hide of "the rest"
    covers the cartoon too; an alignment window is only opened when the user asked to see it)."""
    cmd = command.strip()
    notes: List[str] = []
    word = (re.match(r"~?([A-Za-z0-9]+)", cmd) or [None, ""])[1].lower()
    if word in _SPEC_WORDS:
        new = _outside_quotes(cmd, lambda t: _ATOM_ATTR_RE.sub(lambda m: "@@%s%s%s" % (m.group(1).lower(), m.group(2), m.group(3)), t))
        new = _outside_quotes(new, lambda t: _RES_ATTR_RE.sub(lambda m: "::area%s%s" % (m.group(2), m.group(3)), t))
        new = _outside_quotes(new, lambda t: _ANY_RES_ATTR_RE.sub(lambda m: "::%s%s%s" % (m.group(1), m.group(2), m.group(3)), t))
        if new != cmd:
            notes.append("attribute test written as @@atomattr / ::resattr")
            cmd = new
        new = _outside_quotes(cmd, lambda t: _SPEC_LIST_RE.sub(" ", t))
        if new != cmd:
            notes.append("several specs are separated by spaces, not commas")
            cmd = new
    m = _INFO_SPEC_RE.match(cmd)
    if m and m.group(2).lower() not in ("models", "chains", "residues", "atoms", "selection", "sel"):
        spec = m.group(2)
        sub = "atoms" if "@" in spec else ("residues" if ":" in spec else "")
        if sub:
            cmd = "info %s %s" % (sub, spec)
            notes.append("`info <spec>` describes the model; `info %s` lists the %s" % (sub, sub))
    if word in ("matchmaker", "mm") and _MM_SHOW_RE.search(cmd) and not _WANTS_VIEWER_RE.search(request or ""):
        cmd = _MM_SHOW_RE.sub("", cmd)
        notes.append("the alignment window was not asked for")
    if (cmd.lower().startswith("hide ") and "~" in cmd and not _TARGET_RE.search(cmd[5:])
            and _REST_RE.search(request or "") and not _CARTOON_WORD_RE.search(request or "")):
        cmd = cmd + " target ac"
        notes.append("`hide` alone hides atoms only; hiding the rest hides its cartoon too")
    m = _BYCHAIN_PALETTE_RE.match(cmd)
    if m:
        cmd = "rainbow %s chains palette %s%s" % (m.group(1), m.group(2), m.group(3))
        notes.append("bychain takes no palette; rainbow over chains")
    new = _outside_quotes(cmd, lambda t: _SUBMODEL_RANGE_RE.sub(lambda mm: "#%s.%s-%s" % (mm.group(1), mm.group(2), mm.group(3)), t))
    if new != cmd:
        notes.append("submodel range written as #N.a-b")
        cmd = new
    m = _CLASS_AFTER_SPEC_RE.match(cmd)
    if m and m.group(1).lower() in _SPEC_WORDS:
        cmd = "%s %s & %s%s" % (m.group(1), m.group(2), m.group(3), cmd[m.end():])
        notes.append("a class after a spec needs &")
    new = _outside_quotes(cmd, lambda t: _CLASS_AS_CHAIN_RE.sub(lambda mm: "%s & %s" % (mm.group(1), {"dna": "nucleic", "rna": "nucleic"}.get(mm.group(2).lower(), mm.group(2).lower())), t))
    if new != cmd:
        notes.append("DNA/RNA/protein is a class, not a chain id")
        cmd = new
    return cmd, notes


_RESID_LINE_RE = re.compile(r"residue id /?([A-Za-z0-9]*):(-?\d+)([A-Za-z]?) name (\w+)")


def residue_ranges_summary(text: str, min_lines: int = 4) -> str:
    """'residue id /A:23 name ILE ...' lines (info residues) -> '12 residues: /A:23-34' (ranges per chain).
    Empty when there are fewer than min_lines residues. Long listings are cut before the model sees them,
    and a model then reported the first dozen residues as the whole answer."""
    hits = _RESID_LINE_RE.findall(text or "")
    if len(hits) < min_lines:
        return ""
    by_chain: dict = {}
    for chain, num, ins, _name in hits:
        by_chain.setdefault(chain, []).append(int(num))
    parts = []
    for chain, nums in by_chain.items():
        nums = sorted(set(nums))
        ranges, start, prev = [], nums[0], nums[0]
        for n in nums[1:]:
            if n == prev + 1:
                prev = n
                continue
            ranges.append("%d-%d" % (start, prev) if prev > start else "%d" % start)
            start = prev = n
        ranges.append("%d-%d" % (start, prev) if prev > start else "%d" % start)
        parts.append("/%s:%s" % (chain, ",".join(ranges)) if chain else ":" + ",".join(ranges))
    return "%d residues: %s" % (len(hits), " ".join(parts))
