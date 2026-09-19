"""Executor for classic UCSF Chimera through its RESTServer (Tools > Utilities > RESTServer,
or `chimera --start RESTServer`). Commands go to http://127.0.0.1:PORT/run?command=...
and the reply text comes back as text/plain."""
from __future__ import annotations

import base64
import gzip
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from core.knowledge import BM25Index, Chunk, Knowledge, cheatsheet_chunks, load_json
from core.uniprot import UniProtClient

_ERROR_RE = re.compile(r"^\s*(Error|Traceback|Invalid|Unrecognized|Unknown command|No such|Illegal|Bad |Cannot|Could not|Usage: )", re.I | re.M)


class ChimeraKnowledge(Knowledge):
    """Knowledge over the pre-parsed Chimera doc passages bundled with the classic edition."""

    def __init__(self, cache_dir: str, data_dir: str, cheatsheet, log=None):
        super().__init__(cache_dir, "chimera-classic", [], cheatsheet=cheatsheet, log=log)
        self.data_dir = data_dir

    def rebuild(self, progress=None) -> BM25Index:
        chunks: List[Chunk] = []
        try:
            with gzip.open(os.path.join(self.data_dir, "chimera_docs.json.gz"), "rt", encoding="utf-8") as f:
                chunks = [Chunk(**c) for c in json.load(f)]
        except Exception as e:  # noqa: BLE001
            self.log("Pellaeon: could not load Chimera docs: %s" % e)
        chunks.extend(cheatsheet_chunks(self.cheatsheet))
        idx = BM25Index()
        idx.build(chunks)
        self.index = idx
        try:
            idx.save(self.cache_path, {"version": self.version_key})
        except Exception:
            pass
        self.log("Pellaeon: indexed %d Chimera doc passages" % len(chunks))
        return idx


class ChimeraRestExecutor:
    def __init__(self, port: int, data_dir: str, cache_dir: str, host: str = "127.0.0.1", log=None, timeout: float = 120):
        self.host, self.port = host, int(port)
        self.data_dir, self.cache_dir = data_dir, cache_dir
        self.timeout = timeout
        self.log = log or (lambda m: None)
        self.last_error: Optional[str] = None
        self.uniprot = UniProtClient(os.path.join(cache_dir, "uniprot"))
        from core.clinvar import ClinVarClient
        self.clinvar = ClinVarClient(os.path.join(cache_dir, "clinvar"))
        cheat = {}
        try:
            cheat = load_json(os.path.join(data_dir, "cheatsheet_chimera.json"))
        except Exception:
            pass
        self.knowledge = ChimeraKnowledge(cache_dir, data_dir, cheat, log=self.log)

    @property
    def base_url(self) -> str:
        return "http://%s:%d" % (self.host, self.port)

    # ---- REST ----
    def rest(self, command: str) -> str:
        url = self.base_url + "/run?" + urllib.parse.urlencode({"command": command})
        with urllib.request.urlopen(url, timeout=self.timeout) as r:
            return r.read().decode("utf-8", "replace")

    def ping(self) -> str:
        try:
            self.rest("list models")
            return "Connected to Chimera on port %d." % self.port
        except Exception as e:  # noqa: BLE001
            return "Cannot reach Chimera's REST server on port %d (%s). In Chimera: Tools > Utilities > RESTServer." % (self.port, e)

    def ensure_index(self, progress=None):
        return self.knowledge.ensure(progress)

    def rebuild_index(self, progress=None):
        return self.knowledge.rebuild(progress)

    # ---- executor protocol ----
    def run_commands(self, commands: List[str]) -> List[Dict[str, Any]]:
        results = []
        for cmd in commands:
            entry: Dict[str, Any] = {"command": cmd, "ok": True, "info": [], "warnings": [], "error": ""}
            try:
                out = self.rest(cmd)
            except Exception as e:  # noqa: BLE001
                entry.update(ok=False, error="Could not reach Chimera: %s" % e)
                results.append(entry)
                self.last_error = "%s -> %s" % (cmd, entry["error"])
                break
            lines = [l.rstrip() for l in out.split("\n") if l.strip()]
            errs = [l for l in lines if _ERROR_RE.match(l)]
            if errs:
                entry["ok"] = False
                entry["error"] = " | ".join(errs)[:600]
            entry["info"] = [l[:500] for l in lines if l not in errs][:20]
            results.append(entry)
            if not entry["ok"]:
                self.last_error = "%s -> %s" % (cmd, entry["error"])
                break
        else:
            self.last_error = None
        return results

    def get_state(self) -> Dict[str, Any]:
        state: Dict[str, Any] = {"models": [], "selection": {}, "background": None}
        try:
            out = self.rest("list models")
        except Exception as e:  # noqa: BLE001
            return {"error": "Chimera not reachable: %s" % e}
        for m in re.finditer(r"model id (#[\d.]+) type (\S+) name (.*)", out):
            entry = {"id": m.group(1), "name": m.group(3).strip(), "type": m.group(2), "display": True}
            if m.group(2).lower().startswith("molecule"):
                try:
                    ch = self.rest("list chains spec %s" % m.group(1))
                    chains = []
                    for cm in re.finditer(r"chain id %s:\.(\S+)\s*(.*)" % re.escape(m.group(1)), ch):
                        chains.append({"id": cm.group(1), "range": "", "description": cm.group(2).strip()[:40]})
                    entry["chains"] = chains[:26]
                except Exception:
                    pass
            state["models"].append(entry)
        try:
            sel = self.rest("list selection level residue")
            nums = [int(x) for x in re.findall(r"residue id #[\d.]+:(-?\d+)", sel)]
            spec_parts = re.findall(r"residue id (#[\d.]+:-?\d+\.?\w*)", sel)
            state["selection"] = {"num_residues": len(nums), "num_atoms": len(nums), "spec": " ".join(spec_parts[:20])} if nums else {"num_atoms": 0, "num_residues": 0}
        except Exception:
            pass
        if self.last_error:
            state["last_error"] = self.last_error
        return state

    def command_usage(self, name: str) -> str:
        # classic Chimera has no `usage` command; answer from the bundled documentation
        word = name.strip().split()[0].lower() if name.strip() else ""
        idx = self.knowledge.ensure()
        lines = [c.text for c in idx.chunks if c.command == word and c.kind == "usage"]
        if not lines:
            hits = idx.search(name, 3)
            lines = [c.text for _s, c in hits]
        return ("\n".join(lines))[:1500] or "No documentation found for '%s'." % name

    def search_docs(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        return self.knowledge.search(query, k)

    def resolve_protein(self, query: str, organism: str = "human") -> Dict[str, Any]:
        r = self.uniprot.resolve(query, organism)
        if r.get("accession"):
            r["open_command"] = "open https://alphafold.ebi.ac.uk/files/AF-%s-F1-model_v4.pdb" % r["accession"]
        return r

    def protein_features(self, accession: str, kinds: Optional[List[str]] = None) -> Dict[str, Any]:
        return self.uniprot.features(accession, kinds)

    def run_python(self, code: str) -> Dict[str, Any]:
        return {"ok": False, "error": "Python execution is not available in the classic edition."}

    def look_at_view(self) -> Dict[str, Any]:
        path = os.path.join(tempfile.gettempdir(), "pellaeon_view.png")
        try:
            self.rest("copy file %s png width 800 height 600" % path.replace("\\", "/"))
            with open(path, "rb") as f:
                return {"text": "Screenshot of the current view attached.", "png_b64": base64.b64encode(f.read()).decode("ascii")}
        except Exception as e:  # noqa: BLE001
            return {"text": "Could not capture the view: %s" % e}

    def prepare_compare(self, reference: str, other: str, chain: Optional[str] = None) -> Dict[str, Any]:
        if not all(re.match(r"^#\d+(\.\d+)*$", x or "") for x in (reference, other)) or reference == other:
            return {"error": "Give two different model ids like '#0' and '#1'."}
        return {"ref_id": reference.lstrip("#"), "other_id": other.lstrip("#"), "chain": chain,
                "ref_spec": reference + (":.%s" % chain if chain else ""), "other_spec": other + (":.%s" % chain if chain else "")}

    def compute_displacement(self, prep: Dict[str, Any]) -> Dict[str, Any]:
        return {"unsupported": True, "note": "Classic edition: models are superposed (see RMSD); per-residue displacement coloring needs ChimeraX."}

    def residue_provenance(self, res_spec: str, journal) -> Dict[str, Any]:
        return {"error": "Explaining a residue's colors needs the ChimeraX edition."}

    def label_layout(self) -> Dict[str, Any]:
        return {"error": "Tidying labels needs the ChimeraX edition."}

    def list_residues(self, model: str) -> Dict[str, Any]:
        return {"error": "Table overlays need the ChimeraX edition (classic Chimera has no per-residue attribute coloring through REST)."}

    def set_residue_attr(self, model: str, attr: str, values: Dict[str, Any]) -> Dict[str, Any]:
        return {"error": "Table overlays need the ChimeraX edition."}

    def map_positions(self, model: str, accession: str, positions: List[int]) -> Dict[str, Any]:
        mid = model.strip() if re.match(r"^#\d+(\.\d+)*$", model.strip()) else "#0"
        chains = []
        try:
            ch = self.rest("list chains spec %s" % mid)
            chains = re.findall(r"chain id %s:\.(\S+)" % re.escape(mid), ch)
        except Exception:
            pass
        if len(chains) > 1:
            return {"error": "Model %s has several chains (%s); classic edition cannot map UniProt positions per chain. "
                             "Say which chain to use." % (mid, ",".join(chains))}
        cid = chains[0] if chains else "A"
        return {"model": mid, "map": {p: {"chain": cid, "number": p, "resname": ""} for p in positions}, "chains": [cid],
                "unmapped": [], "note": "Positions applied 1:1 (Chimera cannot report the file's UniProt mapping); correct for AlphaFold models."}
