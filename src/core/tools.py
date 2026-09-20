"""Tool definitions exposed to the model, and the Executor protocol.

The agent never touches ChimeraX directly: it calls an ``Executor``. Inside
ChimeraX the executor is ``bridge.ChimeraXExecutor`` (runs on the main
thread); in tests it is a fake.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from .schema import ToolSpec


class Executor(Protocol):
    def run_commands(self, commands: List[str]) -> List[Dict[str, Any]]: ...
    def get_state(self) -> Dict[str, Any]: ...
    def command_usage(self, name: str) -> str: ...
    def search_docs(self, query: str, k: int = 5) -> List[Dict[str, Any]]: ...
    def resolve_protein(self, query: str, organism: str = "human") -> Dict[str, Any]: ...
    def protein_features(self, accession: str, kinds: Optional[List[str]] = None) -> Dict[str, Any]: ...
    def run_python(self, code: str) -> Dict[str, Any]: ...
    # read-only helpers used by the agent's compare/annotate orchestration (mutations go through execute_commands)
    def prepare_compare(self, reference: str, other: str, chain: Optional[str] = None) -> Dict[str, Any]: ...
    def compute_displacement(self, prep: Dict[str, Any]) -> Dict[str, Any]: ...
    def map_positions(self, model: str, accession: str, positions: List[int]) -> Dict[str, Any]: ...
    def chain_uniprot(self, model: str) -> Dict[str, Any]: ...
    def residue_pairing(self, prep: Dict[str, Any]) -> Dict[str, Any]: ...
    def residue_contacts(self, model_spec: str, cutoff: float = 4.0, restrict: Optional[str] = None) -> Dict[str, Any]: ...
    def look_at_view(self) -> Dict[str, Any]: ...


RUN_COMMANDS = ToolSpec(
    "run_commands",
    "Run one or more ChimeraX commands in order. Put several related commands in ONE call. "
    "Execution stops at the first failing command; you get each command's log output, "
    "warnings and the exact error text.",
    {
        "type": "object",
        "properties": {
            "commands": {
                "type": "array",
                "items": {"type": "string"},
                # Deliberately not a runnable request: with a real accession here, small models
                # opened alphafold:P55085 on turns that had nothing to do with it.
                "description": "ChimeraX commands, one per item, each a command name followed by its arguments",
            }
        },
        "required": ["commands"],
    },
)

GET_STATE = ToolSpec(
    "get_state",
    "Describe what is currently open in ChimeraX: model ids and names, chains with residue "
    "counts, the current selection, background color. Call it after opening structures or when "
    "the user refers to 'it', 'this', 'the selected', 'the other one'.",
    {"type": "object", "properties": {}},
)

COMMAND_USAGE = ToolSpec(
    "command_usage",
    "Get the exact syntax (usage line and options) of a ChimeraX command from the running "
    "ChimeraX. Use it when a command fails with a syntax/keyword error or you are unsure of an option.",
    {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Command name, e.g. 'color', 'cartoon style', 'distance'"}},
        "required": ["name"],
    },
)

SEARCH_DOCS = ToolSpec(
    "search_docs",
    "Search the ChimeraX user documentation (commands, atom specifiers, color names) for a topic. "
    "Returns the most relevant passages. Use it for unfamiliar tasks or options.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What you want to know, e.g. 'measure distance between two residues'"},
            "k": {"type": "integer", "description": "Number of passages (default 5)", "default": 5},
        },
        "required": ["query"],
    },
)

RESOLVE_PROTEIN = ToolSpec(
    "resolve_protein",
    "Look up a protein by gene symbol or name in UniProt and return its accession (needed for "
    "'open alphafold:ACCESSION'), gene, full name, organism and length. NEVER guess accessions; "
    "always call this first when the user names a gene or protein. If the result carries an \"ambiguous\" list, the name matched several proteins: ask the user which one with ask_user before opening anything.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Gene symbol or protein name, e.g. 'F2RL1', 'PAR2', 'human insulin receptor'"},
            "organism": {"type": "string", "description": "Organism name or NCBI taxon id; default 'human'", "default": "human"},
        },
        "required": ["query"],
    },
)

PROTEIN_FEATURES = ToolSpec(
    "protein_features",
    "Fetch UniProt sequence annotations for an accession as residue ranges: domains, "
    "transmembrane/topological regions, binding sites, disulfides, glycosylation, variants. "
    "Use these ranges to color or select regions (e.g. 'color the transmembrane helices').",
    {
        "type": "object",
        "properties": {
            "accession": {"type": "string", "description": "UniProt accession, e.g. P55085"},
            "kinds": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional filter, e.g. [\"Transmembrane\", \"Domain\", \"Binding site\"]",
            },
        },
        "required": ["accession"],
    },
)

ASK_USER = ToolSpec(
    "ask_user",
    "Ask the user a short clarifying question when the request is genuinely ambiguous "
    "(e.g. which of two open models). Offer options when possible. This ends your turn.",
    {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "Optional short choices"},
        },
        "required": ["question"],
    },
)

RUN_PYTHON = ToolSpec(
    "run_python",
    "Run Python code inside ChimeraX with the variable `session` available (and `run(session, cmd)`). "
    "Only for things commands cannot do (custom calculations, loops over residues). "
    "The user must approve it. Print what you want returned.",
    {
        "type": "object",
        "properties": {"code": {"type": "string"}},
        "required": ["code"],
    },
)

LOOK_AT_VIEW = ToolSpec(
    "look_at_view",
    "Take a screenshot of the current 3D view and look at it. Use it to review a figure like a colleague would: is the "
    "subject framed and large enough, is anything clipped, do labels overlap or hide the structure, are the colors "
    "distinguishable, is the background right? Then FIX what you saw with commands (view, zoom, turn, label, hide, color) "
    "and look again to confirm. Also use it whenever the user asks how it looks or to review/check/adjust the view.",
    {"type": "object", "properties": {}},
)

COMPARE = ToolSpec(
    "compare_structures",
    "Superpose one model onto another (matchmaker), compute per-residue CA displacement, color the moved model by "
    "displacement (gray unchanged -> red 6 A) and return RMSD, mean/max displacement, the most shifted residues and "
    "contiguous moving regions. Use for 'what changed between these two structures'.",
    {"type": "object", "properties": {
        "reference": {"type": "string", "description": "reference model spec, e.g. '#1'"},
        "other": {"type": "string", "description": "model to superpose and color, e.g. '#2'"},
        "chain": {"type": "string", "description": "optional chain id to restrict the comparison, e.g. 'A'"}},
     "required": ["reference", "other"]},
)

ANNOTATE = ToolSpec(
    "annotate",
    "Overlay UniProt annotations on a model: color the residues and label them. kind: 'variant' (natural variants), "
    "'disease' (variants linked to a disease), 'domain', 'transmembrane', 'topology', 'binding', 'active', 'site', "
    "'glycosylation', 'disulfide', 'modified', 'ptm', 'region', 'motif', and 'clinvar' (ClinVar missense variants colored by "
    "clinical significance: red pathogenic, yellow uncertain, blue benign; accepts a gene symbol). Needs the UniProt accession (resolve_protein). "
    "Numbering matches AlphaFold models; PDB entries may be offset.",
    {"type": "object", "properties": {
        "model": {"type": "string", "description": "model spec, e.g. '#1'"},
        "accession": {"type": "string"},
        "kind": {"type": "string"},
        "color": {"type": "string", "description": "color name, default orange"},
        "label": {"type": "boolean", "description": "add labels (default true)"}},
     "required": ["model", "accession", "kind"]},
)

TABLE_OVERLAY = ToolSpec(
    "table_overlay",
    "Color a structure by a column of a table the user loaded (see 'Loaded tables' in the state). Numeric columns get a "
    "color ramp, text columns one color per category; residues missing from the table stay gray. Reports how many table "
    "positions were placed, which were not found, and reference-residue mismatches. Never invent values: if no table is "
    "loaded, say the user can load one with the Import table button.",
    {"type": "object", "properties": {
        "dataset": {"type": "string", "description": "table name as listed in the state (optional when only one is loaded)"},
        "column": {"type": "string", "description": "column to color by (optional: the first numeric column)"},
        "model": {"type": "string", "description": "model spec, default '#1'"},
        "chain": {"type": "string", "description": "restrict to one chain id (optional; otherwise the table's chain column or every chain)"},
        "palette": {"type": "string", "description": "blue-white-red (default), white-red, blue-white, viridis, rainbow, gray-orange-red, green-white-magenta"},
        "accession": {"type": "string", "description": "UniProt accession if the table uses UniProt numbering instead of the structure's residue numbers"},
        "label": {"type": "boolean", "description": "also label the placed residues (default false)"}},
     "required": []},
)

TIDY_LABELS = ToolSpec(
    "tidy_labels",
    "Make the current 3D labels readable: compute where every label lands on screen, nudge overlapping ones to a free spot "
    "nearby, remove the ones that cannot fit, and restyle them (fixed size, on top, white background). Call it when the user "
    "says labels overlap, are unreadable, too small, too many, or asks to tidy/clean them up. Returns counts and what was removed.",
    {"type": "object", "properties": {"keep": {"type": "string", "description": "optional atom spec of labels that must stay even if crowded, e.g. the selection"}},
     "required": []},
)

EXPLAIN_RESIDUE = ToolSpec(
    "explain_residue",
    "Why does a residue look the way it does? Returns its current ribbon/atom colors, labels, Pellaeon attributes (table "
    "values, displacement) and the recorded commands that colored, styled or labeled it, newest first, with their origin "
    "(your commands, an annotation, a table overlay, a comparison). Use it for 'why is this red?', 'where did this label "
    "come from?', 'what colored residue 87?'. Answer from what it returns; never guess a biological reason for a color.",
    {"type": "object", "properties": {"residue": {"type": "string", "description": "residue spec, e.g. '#1/A:87' (use the selection spec from the state for 'this')"}},
     "required": ["residue"]},
)

SAVE_FIGURE = ToolSpec(
    "save_figure",
    "Save the current view as a figure BUNDLE, not just a picture: a folder with the image, a ChimeraX session, the "
    "replayable command script, a per-residue color table, the sources of every model and a draft legend written only "
    "from what actually ran. Use it whenever the user wants to save/export a figure or picture. The image save asks for the "
    "user's OK. Optional close-up: a second image of an atom spec.",
    {"type": "object", "properties": {
        "name": {"type": "string", "description": "figure name (folder and file prefix), e.g. 'hemoglobin_pocket'"},
        "width": {"type": "integer", "description": "pixels, default 2400"},
        "height": {"type": "integer", "description": "pixels, default 1800"},
        "closeup": {"type": "string", "description": "optional atom spec to also render zoomed in, e.g. '#1/A:87 :<6'"},
        "transparent": {"type": "boolean", "description": "transparent background (default false)"}},
     "required": []},
)

COMPARE_CONTACTS = ToolSpec(
    "compare_contacts",
    "Compare the INTERACTIONS of two conformations: superpose them, collect every residue-residue contact in each "
    "(salt bridges, hydrogen-bond-capable pairs, van der Waals contacts) and report which contacts are lost, gained "
    "and kept, matched through the alignment rather than by residue number. Draws lost contacts as red dashes on the "
    "reference and gained ones as green dashes on the other model. Use it for 'which contacts are lost when it opens', "
    "'what does the ligand touch in the closed form but not the open one', 'which salt bridges break'. Use "
    "compare_structures instead when the question is how far things MOVED.",
    {"type": "object", "properties": {
        "reference": {"type": "string", "description": "reference model spec, e.g. '#1' (the form whose contacts can be 'lost')"},
        "other": {"type": "string", "description": "model to compare against it, e.g. '#2'"},
        "chain": {"type": "string", "description": "optional chain id to restrict the comparison, e.g. 'A'"},
        "restrict": {"type": "string", "description": "optional atom spec limiting one side of every contact, e.g. a ligand ':AP5' or '/A:87'; use it for 'what does the ligand/this residue touch'"},
        "cutoff": {"type": "number", "description": "heavy-atom distance in A that counts as a contact (default 4.0)"}},
     "required": ["reference", "other"]},
)

FETCH_ANNOTATION = ToolSpec(
    "fetch_annotation",
    "Fetch a published per-residue annotation from a database and color the structure by it. "
    "source='alphamissense': AlphaMissense predicted pathogenicity for a HUMAN protein, per position the mean over "
    "the 19 substitutions (0 benign, 1 pathogenic; classes at the published cutoffs 0.34 / 0.564); needs a UniProt "
    "accession and works on any model of that protein, AlphaFold included. source='conservation': ConSurf-DB "
    "evolutionary conservation grades 1 (variable) to 9 (conserved); indexed by PDB entry and chain ONLY, so give a "
    "4-character PDB ID, not an accession; there is none for an AlphaFold model without a PDB entry. Reports how many "
    "residues were colored and the value range. Never invent these numbers: if the source has no data, say so.",
    {"type": "object", "properties": {
        "source": {"type": "string", "enum": ["alphamissense", "conservation"], "description": "which database"},
        "protein": {"type": "string", "description": "UniProt accession for alphamissense (P55085); 4-character PDB ID for conservation (1UBQ)"},
        "model": {"type": "string", "description": "model spec to color, default '#1'"},
        "chain": {"type": "string", "description": "chain id; conservation needs it when the entry has several chains"}},
     "required": ["source", "protein"]},
)

MAP_NUMBERING = ToolSpec(
    "map_numbering",
    "Translate UniProt (sequence) positions into this structure's residue numbers, or say which are missing. "
    "Use it for 'where is UniProt residue 159 in this structure', 'which residue is the S1 pocket Asp', or "
    "before coloring positions taken from a paper, a database or the user's notes: structure numbering often "
    "differs from UniProt numbering (missing initiator Met, expression tags, construct boundaries).",
    {"type": "object", "properties": {
        "model": {"type": "string", "description": "model spec, e.g. '#1'"},
        "protein": {"type": "string", "description": "UniProt accession (P55085) or gene/protein name; the chain's own UniProt entry when omitted"},
        "positions": {"type": "array", "items": {"type": "integer"}, "description": "UniProt positions to map"}},
     "required": ["model", "positions"]},
)

APPLY_FIGURE_STYLE = ToolSpec(
    "apply_figure_style",
    "Re-use the look of a saved figure bundle on what is open now: replays that bundle's styling commands "
    "(colors, cartoon/surface style, lighting, background, silhouettes) but not its open/close/save steps. "
    "Use it for 'make this look like my pocket figure' or 'use the lab style'. The user names the figure "
    "(folder name in the figures directory) or gives a path.",
    {"type": "object", "properties": {
        "figure": {"type": "string", "description": "figure name (folder under the figures directory) or a folder/.cxc path"},
        "model": {"type": "string", "description": "apply to this model instead of the one the bundle names (optional)"}},
     "required": ["figure"]},
)

ALL_TOOLS = [RUN_COMMANDS, COMPARE, COMPARE_CONTACTS, ANNOTATE, FETCH_ANNOTATION, TABLE_OVERLAY, TIDY_LABELS, EXPLAIN_RESIDUE, SAVE_FIGURE, MAP_NUMBERING, APPLY_FIGURE_STYLE, GET_STATE, COMMAND_USAGE, SEARCH_DOCS, RESOLVE_PROTEIN,
             PROTEIN_FEATURES, ASK_USER, RUN_PYTHON, LOOK_AT_VIEW]


def tool_specs(allow_python: bool = False, vision: bool = False,
               tables: bool = False, compact: bool = False) -> List[ToolSpec]:
    """The tools offered to the model this turn.

    Every tool costs input tokens on each request, so the ones that only apply in a
    particular situation are offered only in that situation: table_overlay once the user
    has loaded a table, and tidy_labels / explain_residue whenever there is room (the
    panel's chips reach both of those directly if the model is not told about them).
    """
    specs = [RUN_COMMANDS, GET_STATE, COMMAND_USAGE, SEARCH_DOCS, RESOLVE_PROTEIN,
             PROTEIN_FEATURES, COMPARE, COMPARE_CONTACTS, ANNOTATE, FETCH_ANNOTATION, ASK_USER, SAVE_FIGURE]
    if tables:
        specs.append(TABLE_OVERLAY)
    # tidy_labels and explain_residue are demanded by nudges, map_numbering by a gotcha: a tool the
    # prompt orders the model to call must be on the list, compact or not
    specs.extend([TIDY_LABELS, EXPLAIN_RESIDUE, MAP_NUMBERING])
    if not compact:
        specs.append(APPLY_FIGURE_STYLE)
    if allow_python:
        specs.append(RUN_PYTHON)
    if vision:
        specs.append(LOOK_AT_VIEW)
    return specs
