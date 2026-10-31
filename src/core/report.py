"""The figure report: legend, methods, sources and commands, written from recorded facts only.

A figure bundle (agent._figure_bundle) saves the image; this module turns what the agent recorded
while the view was made into the text that goes with it. Nothing here is guessed: every sentence
comes from the models that are open (executor.figure_info), the colour commands that ran
(agent._colorings), the annotations placed (agent.annotations, one record per annotate /
fetch_annotation / table overlay, with the source's version and retrieval date when the source
gave one), the key command on screen (the journal) and the versions of ChimeraX and Pellaeon.
A fact that was not recorded is written as "(not recorded)", never filled in.

`collect_facts` builds one plain dict; `render_markdown` / `render_html` write it out; the same
dict goes to figure.json. Pure Python: unit-tested with a fake agent.
"""
from __future__ import annotations

import datetime
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .labels import _key_parts
from .markdown import render as md_to_html

NOT_RECORDED = "(not recorded)"
PELLAEON_DOI = "10.5281/zenodo.23395411"

# Citation strings. ChimeraX and Pellaeon are always cited; a source is cited only when it was used.
REFERENCES = {
    "chimerax": "Meng EC, Goddard TD, Pettersen EF, Couch GS, Pearson ZJ, Morris JH, Ferrin TE. UCSF ChimeraX: tools for "
                "structure building and analysis. Protein Sci. 2023;32(11):e4792. doi:10.1002/pro.4792",
    "pellaeon": "Amir Y. Pellaeon: an AI assistant for UCSF ChimeraX. Zenodo. doi:%s" % PELLAEON_DOI,
    "uniprot": "The UniProt Consortium. UniProt: the Universal Protein Knowledgebase in 2025. Nucleic Acids Res. "
               "2025;53(D1):D609-D617. doi:10.1093/nar/gkae1010",
    "clinvar": "Landrum MJ, Chitipiralla S, Brown GR, et al. ClinVar: improvements to accessing data. Nucleic Acids Res. "
               "2020;48(D1):D835-D844. doi:10.1093/nar/gkz972",
    "alphamissense": "Cheng J, Novati G, Pan J, et al. Accurate proteome-wide missense variant effect prediction with "
                     "AlphaMissense. Science. 2023;381(6664):eadg7492. doi:10.1126/science.adg7492",
    "consurf": "Ben Chorin A, Masrati G, Kessel A, et al. ConSurf-DB: an accessible repository for the evolutionary "
               "conservation patterns of the majority of PDB proteins. Protein Sci. 2020;29(1):258-267. doi:10.1002/pro.3779",
}

_SCHEME_WORDS = {"bychain": "by chain", "bypolymer": "by polymer", "byhetero": "by element, carbons kept",
                 "byelement": "by element", "byatom": "by element", "bymodel": "by model", "bynucleotide": "by nucleotide",
                 "byidentity": "by sequence identity", "rainbow": "rainbow, N to C", "zone": "by nearest atom (zone)",
                 "coulombic": "by Coulombic electrostatic potential", "mlp": "by molecular lipophilicity potential",
                 "sequential": "sequentially", "fromatoms": "from the atom colours", "fromribbons": "from the ribbon colours",
                 "single": "a single colour", "electrostatic": "by electrostatic potential"}
_BASIS_WORDS = {"exact": "by the file's UniProt reference (same numbering)", "offset": "by the file's UniProt reference (offset %s)",
                "alignment": "by aligning the chain to the UniProt sequence (%s%% identity)",
                "structure": "by the structure's own residue numbers", "unresolved": "not placed"}
_SOURCE_REF = (("alphamissense", "alphamissense"), ("consurf", "consurf"), ("clinvar", "clinvar"), ("uniprot", "uniprot"))


def today() -> str:
    return datetime.date.today().isoformat()


# ------------------------------------------------------------------ facts
def collect_facts(agent, info: Dict[str, Any], image: Dict[str, Any], name: str = "") -> Dict[str, Any]:
    """Everything the report states, as one dict. `info` is executor.figure_info(); `image` has width, height,
    supersample, transparent and the saved file names (`saved` False when the image save failed)."""
    journal = list(getattr(agent, "journal", None) or [])
    models = [_model_facts(m) for m in (info.get("models") or [])]
    colorings = _coloring_facts(getattr(agent, "_colorings", None) or {}, models, journal)
    annotations = [dict(a) for a in (getattr(agent, "annotations", None) or [])]
    key = _key_facts(journal)
    return {
        "name": name, "date": today(),
        "software": {"chimerax": str(info.get("chimerax_version") or "") or NOT_RECORDED,
                     "pellaeon": str(getattr(agent, "version", "") or "") or NOT_RECORDED, "pellaeon_doi": PELLAEON_DOI},
        "image": {"file": image.get("file", ""), "width": image.get("width"), "height": image.get("height"),
                  "supersample": image.get("supersample"), "transparent": bool(image.get("transparent")),
                  "closeup": image.get("closeup") or None, "closeup_file": image.get("closeup_file") or None,
                  "saved": image.get("saved", True)},
        "background": info.get("background") or NOT_RECORDED,
        "models": models, "colorings": colorings, "annotations": annotations, "key": key,
        "labels": int(info.get("labels") or 0),
        "notes": [str(n) for n in (getattr(agent, "figure_notes", None) or [])],
        "commands": [j for j in journal if j.get("ok") and not j.get("noop")],
        "commands_failed": sum(1 for j in journal if not j.get("ok")),
        "references": _references(annotations),
    }


def _model_facts(m: Dict[str, Any]) -> Dict[str, Any]:
    src = str(m.get("source") or m.get("name") or "")
    kind, ident = "other", ""
    hit = re.match(r"^PDB\s+(\S+)$", src)
    if hit:
        kind, ident = "pdb", hit.group(1).upper()
    hit = re.match(r"^AlphaFold DB\s+(\S+)$", src)
    if hit:
        kind, ident = "alphafold", hit.group(1).upper()
    if src.startswith("file "):
        kind, ident = "file", src[5:]
    chains = [str(c) for c in (m.get("chains") or [])]
    shown = m.get("chains_shown")
    return {"id": m.get("id", ""), "name": m.get("name", ""), "source": src, "kind": kind, "identifier": ident,
            "chains": chains, "chains_shown": [str(c) for c in shown] if shown is not None else None,
            "residues": m.get("residues"), "shown": bool(m.get("shown", True))}


def _coloring_facts(store: Dict[str, List[Tuple[str, str]]], models: List[Dict[str, Any]], journal: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One entry per model from the agent's colour record: schemes in words, named colours with their specs."""
    out = []
    by_id = {m["id"]: m for m in models}
    for model, entries in store.items():
        for col, spec in entries:
            if col in _SCHEME_WORDS or col.startswith("by") or col in ("bfactor",):
                words = _SCHEME_WORDS.get(col)
                if col == "bfactor":
                    words = "by pLDDT (the B-factor column of an AlphaFold model)" if _is_alphafold(by_id.get(model, {})) else "by B-factor"
                if col == "byattribute":
                    attr = _last_attribute(journal, model)
                    words = ("by the attribute %s" % attr) if attr else "by a residue attribute"
                out.append({"model": model, "scheme": col, "text": words or col, "spec": spec})
            else:
                out.append({"model": model, "color": col, "spec": spec, "text": "%s = %s" % (col, spec)})
    return out


def _is_alphafold(m: Dict[str, Any]) -> bool:
    """An AlphaFold DB entry, or a GPCRdb AlphaFold-Multistate model (named so when it is opened): B-factor holds pLDDT."""
    return m.get("kind") == "alphafold" or "alphafold" in ("%s %s" % (m.get("name", ""), m.get("source", ""))).lower()


def _last_attribute(journal: List[Dict[str, Any]], model: str) -> str:
    for j in reversed(journal):
        c = str(j.get("command", ""))
        m = re.match(r"^colou?r\s+(\S+)\s+byattr(?:ibute)?\s+(\S+)", c, re.I)
        if m and j.get("ok") and (m.group(1).startswith(model) or model == "all"):
            return m.group(2)
    return ""


def _key_facts(journal: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The colour key on screen at the end of the journal: its entries and Pellaeon's title label, or None."""
    key, title = None, ""
    for j in journal:
        if not j.get("ok"):
            continue
        c = str(j.get("command", "")).strip()
        low = c.lower()
        if low.startswith("key delete") or low in ("close", "close #all", "close all", "close session") or low.startswith("close session"):
            key, title = None, ""
            continue
        if low.startswith("key "):
            parts = _key_parts(c)
            if parts and parts[0]:
                key = {"entries": [{"color": col, "label": lab} for col, lab in parts[0]], "command": c}
        m = re.match(r'^2dlabels\s+(?:create|change)\s+pellaeon_title\s+text\s+"([^"]*)"', c, re.I)
        if m:
            title = m.group(1)
        elif low.startswith("2dlabels delete pellaeon_title"):
            title = ""
    if key is None and not title:
        return None
    out = key or {"entries": [], "command": ""}
    out["title"] = title
    return out


def _references(annotations: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    refs = [{"id": "chimerax", "text": REFERENCES["chimerax"]}, {"id": "pellaeon", "text": REFERENCES["pellaeon"]}]
    seen = {"chimerax", "pellaeon"}
    for a in annotations:
        blob = ("%s %s" % (a.get("database", ""), a.get("source", ""))).lower()
        for word, rid in _SOURCE_REF:
            if word in blob and rid not in seen:
                refs.append({"id": rid, "text": REFERENCES[rid]})
                seen.add(rid)
    return refs


# ------------------------------------------------------------------ words
def _join(items: List[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _chain_words(m: Dict[str, Any]) -> str:
    """'chains A and B shown of A, B, C and D': a chain counts as shown with atoms, a ribbon or a molecular surface."""
    shown = m.get("chains_shown")
    chains = shown if shown is not None else m.get("chains") or []
    if not chains:
        return ""
    if shown is not None and len(shown) < len(m.get("chains") or []):
        return "chain%s %s shown of %s" % ("s" if len(shown) > 1 else "", _join(list(shown)), _join(list(m["chains"])))
    return "chain%s %s" % ("s" if len(chains) > 1 else "", _join(list(chains)))


def model_sentence(m: Dict[str, Any]) -> str:
    """'PDB 4HHB (#1; chains A, B, C and D)'."""
    head = {"pdb": "PDB %s" % m["identifier"], "alphafold": "AlphaFold DB model %s" % m["identifier"],
            "file": "file %s" % m["identifier"]}.get(m["kind"], m["source"] or m["name"])
    inner = [m["id"], _chain_words(m)]
    return "%s (%s)" % (head, "; ".join(i for i in inner if i))


def basis_words(status: Dict[str, Any]) -> str:
    st = str(status.get("status") or "")
    text = _BASIS_WORDS.get(st, st or NOT_RECORDED)
    if st == "offset":
        off = status.get("offset")
        text = text % (("%+d" % off) if isinstance(off, int) else NOT_RECORDED)
    elif st == "alignment":
        ident = status.get("identity")
        text = text % (ident if ident is not None else "identity " + NOT_RECORDED)
    chain = str(status.get("chain") or "").split("/")[-1]
    return ("chain %s %s" % (chain, text)) if chain else text


def mapping_sentence(a: Dict[str, Any]) -> str:
    """How the annotation's positions reached the structure, per chain."""
    statuses = [s for s in (a.get("chain_status") or []) if s.get("status") and s.get("status") != "unresolved"]
    if not statuses:
        if a.get("numbering") == "structure":
            return "placed by the structure's own residue numbers"
        return "placement basis " + NOT_RECORDED
    return "placed " + "; ".join(basis_words(s) for s in statuses)


def source_sentence(a: Dict[str, Any]) -> str:
    """'UniProt P69905 Natural variant features (release 2025_04, retrieved 2026-10-30)'."""
    src = a.get("source") or a.get("database") or NOT_RECORDED
    bits = []
    if a.get("release"):
        bits.append("release %s" % a["release"])
    bits.append("retrieved %s" % (a.get("fetched") or NOT_RECORDED))
    return "%s (%s)" % (src, ", ".join(bits))


def legend_lines(facts: Dict[str, Any]) -> List[str]:
    """The figure legend, one fact per sentence; no interpretation."""
    out: List[str] = []
    models = facts["models"]
    if models:
        out.append("%s: %s." % ("Structures" if len(models) > 1 else "Structure", "; ".join(model_sentence(m) for m in models)))
    else:
        out.append("Structures: " + NOT_RECORDED + ".")
    cols = facts["colorings"]
    if cols:
        by_model: Dict[str, List[str]] = {}
        for c in cols:
            by_model.setdefault(c["model"], []).append(c["text"])
        out.append("Colours: " + "; ".join("%s %s" % (m, ", ".join(t)) for m, t in by_model.items()) + ".")
    for a in facts["annotations"]:
        what = a.get("legend") or a.get("column") or a.get("kind") or ""
        out.append("%s on %s%s: %s; %s." % (a.get("label") or a.get("source") or "Annotation", a.get("model", ""),
                                            (" chains %s" % _join([str(c) for c in a["chains"]])) if a.get("chains") else "",
                                            what or NOT_RECORDED, mapping_sentence(a)))
    key = facts.get("key")
    if key:
        if key.get("entries"):
            out.append("Key: " + ", ".join("%s %s" % (e["color"], e["label"]) if e["label"] else e["color"] for e in key["entries"]) + ".")
        if key.get("title"):
            out.append("Title: %s." % key["title"])
    for n in facts.get("notes") or []:
        n = n.strip().rstrip(".")
        if n and not any(n in line for line in out):
            out.append(n + ".")
    if facts.get("labels"):
        out.append("%d label%s shown." % (facts["labels"], "" if facts["labels"] == 1 else "s"))
    out.append("Background %s." % facts.get("background", NOT_RECORDED))
    return out


def methods_paragraph(facts: Dict[str, Any]) -> str:
    """Neutral academic register, from the same facts; references are numbered as in `references`."""
    num = {r["id"]: i + 1 for i, r in enumerate(facts["references"])}
    sw = facts["software"]
    s = ["Structures were rendered in UCSF ChimeraX %s [%d] with Pellaeon %s [%d]." % (sw["chimerax"], num["chimerax"], sw["pellaeon"], num["pellaeon"])]
    models = facts["models"]
    if models:
        opened = []
        for m in models:
            opened.append({"pdb": "PDB entry %s" % m["identifier"], "alphafold": "the AlphaFold DB model of %s" % m["identifier"],
                           "file": "the file %s" % m["identifier"]}.get(m["kind"], m["source"] or m["name"]))
        s.append("%s %s opened in ChimeraX." % (_join(opened), "was" if len(opened) == 1 else "were"))
    cols = facts["colorings"]
    if cols:
        parts = []
        for c in cols:
            if c.get("scheme"):
                parts.append("%s was coloured %s" % (c["model"], c["text"]))
            else:
                parts.append("%s was coloured %s" % (c["spec"], c["color"]))
        s.append(_join(parts)[0].upper() + _join(parts)[1:] + ".")
    for a in facts["annotations"]:
        ref = ""
        blob = ("%s %s" % (a.get("database", ""), a.get("source", ""))).lower()
        for word, rid in _SOURCE_REF:
            if word in blob and rid in num:
                ref = " [%d]" % num[rid]
                break
        what = a.get("column") or a.get("kind") or "annotations"
        s.append("Residues of %s were coloured by %s from %s%s, %s%s." % (
            a.get("model", NOT_RECORDED), what, source_sentence(a), ref, mapping_sentence(a),
            ("; colours: %s" % a["legend"]) if a.get("legend") else ""))
    img = facts["image"]
    if img.get("saved") is False:
        s.append("The image was not saved.")
    elif img.get("width") and img.get("height"):
        s.append("The image was saved at %s x %s pixels with %sx supersampling%s." % (
            img["width"], img["height"], img.get("supersample") or NOT_RECORDED,
            " and a transparent background" if img.get("transparent") else ""))
    s.append("The ChimeraX commands that produced the view are listed below.")
    return " ".join(s)


# ------------------------------------------------------------------ output
def render_markdown(facts: Dict[str, Any]) -> str:
    name = facts.get("name") or "figure"
    lines = ["# %s" % name, ""]
    if facts["image"].get("file"):
        lines += ["![%s](%s)" % (name, facts["image"]["file"]), ""]
    lines += ["## Legend", "", "**%s.** %s" % (name, " ".join(legend_lines(facts))), ""]
    lines += ["## Methods", "", methods_paragraph(facts), ""]
    lines += ["## Sources", ""]
    for m in facts["models"]:
        lines.append("- %s: %s" % (m["id"], m["source"] or m["name"]))
    for a in facts["annotations"]:
        lines.append("- %s: %s; %s%s" % (a.get("model", ""), source_sentence(a), mapping_sentence(a),
                                        ("; %s" % a["url"]) if a.get("url") else ""))
    if not facts["models"] and not facts["annotations"]:
        lines.append("- " + NOT_RECORDED)
    lines += ["", "## Commands", "", "Every command that ran in this chat, in order (the same script is in the .cxc file).", "", "```"]
    lines += [str(j.get("command", "")) for j in facts["commands"]] or ["# none recorded"]
    lines += ["```", ""]
    if facts.get("commands_failed"):
        lines += ["%d command%s failed and %s left out; the .cxc file lists them as comments." % (
            facts["commands_failed"], "" if facts["commands_failed"] == 1 else "s", "is" if facts["commands_failed"] == 1 else "are"), ""]
    lines += ["## References", ""]
    lines += ["%d. %s" % (i + 1, r["text"]) for i, r in enumerate(facts["references"])]
    lines += ["", "_Written by Pellaeon from the recorded commands and sources on %s. Check it before use._" % facts.get("date", today())]
    return "\n".join(lines) + "\n"


def render_html(facts: Dict[str, Any]) -> str:
    name = facts.get("name") or "figure"
    body = md_to_html(render_markdown(facts))
    # the chat renderer nests headings two levels down and has no images; a standalone page wants both
    body = re.sub(r"<(/?)h([3-5])>", lambda m: "<%sh%d>" % (m.group(1), int(m.group(2)) - 2), body)
    body = re.sub(r"<p>!\[([^\]]*)\]\(([^)\s]+)\)</p>", lambda m: '<p><img src="%s" alt="%s"></p>' % (m.group(2), m.group(1)), body)
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>%s</title>\n"
            "<style>body{font:15px/1.5 system-ui,sans-serif;max-width:52em;margin:2em auto;padding:0 1em;color:#222}"
            "img{max-width:100%%}code,pre{font:13px/1.4 ui-monospace,monospace}pre{background:#f4f4f4;padding:.8em;overflow:auto}"
            "h1,h2{font-weight:600}</style></head>\n<body>\n%s\n</body></html>\n" % (_esc(name), body))


def facts_json(facts: Dict[str, Any]) -> str:
    return json.dumps(facts, indent=2, ensure_ascii=False)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
