"""ChimeraX side of the executor: runs commands on the main thread, captures
the log, and snapshots the session state. This is the only module (besides
tool.py and cmd.py) that imports ChimeraX.
"""
from __future__ import annotations

import base64
import io
import re
import os
import queue
import threading
import traceback
from typing import Any, Callable, Dict, List, Optional

from chimerax.core.logger import StringPlainTextLog

from .core.knowledge import Knowledge, load_json
from .core.uniprot import UniProtClient


class _CapturingLog(StringPlainTextLog):
    """Collect messages by level while letting them reach the ChimeraX Log too."""

    def __init__(self, logger, echo: bool = True):
        super().__init__(logger)
        self.by_level = {d: [] for d in self.LEVEL_DESCRIPTS}
        self.echo = echo

    def log(self, level, msg):
        self.by_level[self.LEVEL_DESCRIPTS[level]].append(msg)
        return not self.echo  # True = consumed (not shown elsewhere)


def _run_on_main_thread(session, func: Callable[[], Any], timeout: Optional[float] = None) -> Any:
    """Run ``func`` on ChimeraX's main thread and return its result (or re-raise)."""
    ui = session.ui
    if not getattr(ui, "is_gui", False) or threading.current_thread() is threading.main_thread():
        return func()
    q: "queue.Queue" = queue.Queue()

    def wrapper():
        try:
            q.put((True, func()))
        except BaseException as e:  # noqa: BLE001
            q.put((False, e))
    ui.thread_safe(wrapper)
    ok, value = q.get(timeout=timeout)
    if ok:
        return value
    raise value


def data_path(*parts: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", *parts)


def app_dirs():
    from chimerax import app_dirs as dirs
    return dirs


def pellaeon_dir(kind: str = "data") -> str:
    """Pellaeon's own folder, outside ChimeraX's per-version directory so keys and chats survive upgrades."""
    d = app_dirs()
    base = {"data": d.user_data_dir, "config": d.user_config_dir, "cache": d.user_cache_dir}[kind]
    if re.match(r"^\d+(\.\d+)*$", os.path.basename(base.rstrip("/\\"))):
        base = os.path.dirname(base.rstrip("/\\"))
    path = os.path.join(base, "Pellaeon")
    os.makedirs(path, exist_ok=True)
    return path


def chimerax_version() -> str:
    try:
        from chimerax.core import version
        return str(version)
    except Exception:
        try:
            from chimerax.core import buildinfo
            return str(buildinfo.version)
        except Exception:
            return "unknown"


def docs_dirs() -> List[str]:
    dirs = []
    try:
        from chimerax.core import toolshed
        for d in toolshed.get_help_directories():
            cmd_dir = os.path.join(d, "user", "commands")
            if os.path.isdir(cmd_dir):
                dirs.append(cmd_dir)
    except Exception:
        pass
    if not dirs:
        try:
            from chimerax import app_data_dir
            cmd_dir = os.path.join(app_data_dir, "docs", "user", "commands")
            if os.path.isdir(cmd_dir):
                dirs.append(cmd_dir)
        except Exception:
            pass
    return dirs


def registry_usage_entries(session, max_entries: int = 400) -> Dict[str, str]:
    """usage strings for registered commands (covers commands the docs miss)."""
    out: Dict[str, str] = {}
    try:
        from chimerax.core.commands import cli
        names = cli.registered_commands(multiword=True)
    except Exception:
        return out
    for name in names[:max_entries]:
        try:
            out[name] = cli.usage(session, name, show_hidden=False)
        except Exception:
            continue
    return out


class ChimeraXExecutor:
    """Executor implementation for the agent. Safe to call from any thread."""

    def __init__(self, session, log=None):
        self.session = session
        self.last_error: Optional[str] = None
        self.uniprot = UniProtClient(os.path.join(pellaeon_dir("cache"), "uniprot"))
        from .core.clinvar import ClinVarClient
        self.clinvar = ClinVarClient(os.path.join(pellaeon_dir("cache"), "clinvar"))
        self._knowledge: Optional[Knowledge] = None
        self._log = log or session.logger.info

    # ---------------------------------------------------------- knowledge
    @property
    def knowledge(self) -> Knowledge:
        if self._knowledge is None:
            cheat = {}
            try:
                cheat = load_json(data_path("cheatsheet.json"))
            except Exception:
                pass
            entries = _run_on_main_thread(self.session, lambda: registry_usage_entries(self.session))
            workflows, recipe_library = [], []
            for name, target in (("tutorials.json", "workflows"), ("recipe_library.json", "recipes")):
                try:
                    data = load_json(data_path(name))
                    if target == "workflows":
                        workflows = data.get("workflows", data) if isinstance(data, dict) else data
                    else:
                        recipe_library = data.get("recipes", data) if isinstance(data, dict) else data
                except Exception:
                    pass
            guides = []
            gdir = data_path("guides")
            if os.path.isdir(gdir):
                for name in sorted(os.listdir(gdir)):
                    if name.endswith(".txt"):
                        try:
                            with open(os.path.join(gdir, name), "r", encoding="utf-8", errors="replace") as f:
                                guides.append((name[:-4].replace("_", " "), f.read()))
                        except Exception:
                            pass
            self._knowledge = Knowledge(pellaeon_dir("cache"), chimerax_version(), docs_dirs(),
                                        cheatsheet=cheat, registry_entries=entries,
                                        workflows=workflows, recipe_library=recipe_library, guides=guides, log=self._log)
        return self._knowledge

    def ensure_index(self, progress=None):
        return self.knowledge.ensure(progress)

    def rebuild_index(self, progress=None):
        return self.knowledge.rebuild(progress)

    # ---------------------------------------------------------- commands
    def run_commands(self, commands: List[str], cancel=None) -> List[Dict[str, Any]]:
        return _run_on_main_thread(self.session, lambda: self._run_commands_main(commands, cancel))

    def _run_commands_main(self, commands: List[str], cancel=None) -> List[Dict[str, Any]]:
        from chimerax.core.commands import run
        from chimerax.core.errors import UserError, NotABug
        results: List[Dict[str, Any]] = []
        self.last_returns: Dict[str, Any] = {}   # values returned by commands (e.g. matchmaker's atom pairs)
        for cmd in commands:
            if cancel is not None and cancel.is_set():
                break
            entry: Dict[str, Any] = {"command": cmd, "ok": False, "info": [], "warnings": [], "error": ""}
            with _CapturingLog(self.session.logger, echo=True) as cap:
                try:
                    self.last_returns[cmd] = run(self.session, cmd, log=True, return_list=True)
                    entry["ok"] = True
                except (UserError, NotABug) as e:
                    entry["error"] = str(e)
                except Exception as e:  # noqa: BLE001
                    entry["error"] = "%s: %s" % (e.__class__.__name__, e)
                    entry["traceback"] = traceback.format_exc()[-1500:]
            # ChimeraX calls the info level "note"; drop the command echo line
            infos = cap.by_level.get("note", []) + cap.by_level.get("info", [])
            entry["info"] = [m.strip() for m in infos
                             if m.strip() and not m.startswith("Executing:") and m.strip() != cmd.strip()][:20]
            entry["warnings"] = [m.strip() for m in cap.by_level.get("warning", []) if m.strip()][:10]
            errs = [m.strip() for m in cap.by_level.get("error", []) + cap.by_level.get("bug", []) if m.strip()]
            if errs and not entry["error"]:
                entry["error"] = errs[0]
                entry["ok"] = False
            elif errs:
                entry["error_log"] = errs[:3]
            # trim very long info dumps
            entry["info"] = [m[:500] for m in entry["info"]]
            results.append(entry)
            if not entry["ok"]:
                self.last_error = "%s -> %s" % (cmd, entry["error"])
                break
        else:
            self.last_error = None
        return results

    def command_usage(self, name: str) -> str:
        def f():
            from chimerax.core.commands import cli
            try:
                return cli.usage(self.session, name.strip(), show_hidden=False)
            except Exception as e:  # noqa: BLE001
                # try the first word only (e.g. "cartoon style" -> "cartoon")
                first = name.strip().split()[0] if name.strip() else ""
                try:
                    return cli.usage(self.session, first, show_hidden=False)
                except Exception:
                    return "No such command: %s (%s)" % (name, e)
        return _run_on_main_thread(self.session, f)

    def search_docs(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        return self.knowledge.search(query, k)

    def resolve_protein(self, query: str, organism: str = "human") -> Dict[str, Any]:
        return self.uniprot.resolve(query, organism)

    def protein_features(self, accession: str, kinds: Optional[List[str]] = None) -> Dict[str, Any]:
        return self.uniprot.features(accession, kinds)

    def prepare_compare(self, reference: str, other: str, chain: Optional[str] = None) -> Dict[str, Any]:
        from .analysis import prepare_compare
        return _run_on_main_thread(self.session, lambda: prepare_compare(self.session, reference, other, chain))

    def compute_displacement(self, prep: Dict[str, Any]) -> Dict[str, Any]:
        from .analysis import compute_displacement
        returns = []

        def flatten(v):
            if isinstance(v, dict):
                returns.append(v)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    flatten(x)
        for cmd, val in getattr(self, "last_returns", {}).items():
            if cmd.startswith(("matchmaker", "mm ")):
                flatten(val)
        return _run_on_main_thread(self.session, lambda: compute_displacement(self.session, prep, returns))

    def list_residues(self, model: str) -> Dict[str, Any]:
        from .analysis import list_residues
        return _run_on_main_thread(self.session, lambda: list_residues(self.session, model))

    def set_residue_attr(self, model: str, attr: str, values: Dict[str, Any]) -> Dict[str, Any]:
        from .analysis import set_residue_attr
        return _run_on_main_thread(self.session, lambda: set_residue_attr(self.session, model, attr, values))

    def map_positions(self, model: str, accession: str, positions: List[int]) -> Dict[str, Any]:
        from .analysis import map_positions
        return _run_on_main_thread(self.session, lambda: map_positions(self.session, model, accession, positions))

    def run_python(self, code: str) -> Dict[str, Any]:
        def f():
            import contextlib
            from chimerax.core.commands import run
            buf = io.StringIO()
            ns = {"session": self.session, "run": run}
            try:
                with contextlib.redirect_stdout(buf):
                    exec(compile(code, "<pellaeon>", "exec"), ns)
                return {"ok": True, "stdout": buf.getvalue()[-4000:]}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "stdout": buf.getvalue()[-2000:], "error": "%s: %s" % (e.__class__.__name__, e),
                        "traceback": traceback.format_exc()[-1500:]}
        return _run_on_main_thread(self.session, f)

    def look_at_view(self) -> Dict[str, Any]:
        def f():
            try:
                img = self.session.main_view.image(width=800, height=600, supersample=1)
                bio = io.BytesIO()
                img.save(bio, format="PNG")
                return {"text": "Screenshot of the current view attached.",
                        "png_b64": base64.b64encode(bio.getvalue()).decode("ascii")}
            except Exception as e:  # noqa: BLE001
                return {"text": "Could not capture the view: %s" % e}
        return _run_on_main_thread(self.session, f)

    # ---------------------------------------------------------- state
    def get_state(self) -> Dict[str, Any]:
        return _run_on_main_thread(self.session, self._get_state_main)

    def _get_state_main(self) -> Dict[str, Any]:
        session = self.session
        state: Dict[str, Any] = {"models": [], "selection": {}, "background": None}
        try:
            from chimerax.atomic import AtomicStructure, selected_atoms, selected_residues
        except Exception:
            AtomicStructure = None  # type: ignore
            selected_atoms = selected_residues = None  # type: ignore
        try:
            for m in session.models.list():
                if m.parent is not None and getattr(m.parent, "id", None) is not None and m.parent.id != ():
                    # skip deep submodels except atomic structures
                    if AtomicStructure is None or not isinstance(m, AtomicStructure):
                        continue
                entry: Dict[str, Any] = {"id": "#" + m.id_string, "name": m.name, "type": m.__class__.__name__,
                                         "display": bool(getattr(m, "display", True))}
                if AtomicStructure is not None and isinstance(m, AtomicStructure):
                    entry["num_residues"] = int(m.num_residues)
                    entry["num_atoms"] = int(m.num_atoms)
                    chains = []
                    for c in m.chains:
                        try:
                            res = c.existing_residues
                            nums = res.numbers
                            rng = "%d-%d" % (int(nums.min()), int(nums.max())) if len(nums) else ""
                        except Exception:
                            rng = ""
                        chains.append({"id": c.chain_id, "range": rng,
                                       "description": (c.description or "")[:40] if getattr(c, "description", None) else ""})
                    entry["chains"] = chains[:26]
                    try:
                        ligs = m.atoms.filter(m.atoms.structure_categories == "ligand")
                        if len(ligs):
                            entry["ligands"] = sorted(set(r.name for r in ligs.residues))[:10]
                    except Exception:
                        pass
                state["models"].append(entry)
        except Exception as e:  # noqa: BLE001
            state["error"] = "could not list models: %s" % e
        try:
            if selected_atoms is not None:
                atoms = selected_atoms(session)
                res = selected_residues(session)
                sel: Dict[str, Any] = {"num_atoms": len(atoms), "num_residues": len(res)}
                if len(res):
                    sel["spec"] = _residues_spec(res)
                state["selection"] = sel
        except Exception:
            pass
        try:
            rgba = session.main_view.background_color
            state["background"] = "rgb(%d,%d,%d)" % tuple(int(255 * c) for c in rgba[:3])
        except Exception:
            pass
        if self.last_error:
            state["last_error"] = self.last_error
        return state


def _residues_spec(residues, max_len: int = 200) -> str:
    """Compact spec like '#1/A:10-15,42' for a residue collection."""
    by_model: Dict[str, Dict[str, List[int]]] = {}
    for r in residues:
        mid = "#" + r.structure.id_string
        by_model.setdefault(mid, {}).setdefault(r.chain_id, []).append(int(r.number))
    parts = []
    for mid, chains in by_model.items():
        for cid, nums in chains.items():
            nums = sorted(set(nums))
            ranges = []
            start = prev = nums[0]
            for n in nums[1:]:
                if n == prev + 1:
                    prev = n
                    continue
                ranges.append("%d-%d" % (start, prev) if start != prev else str(start))
                start = prev = n
            ranges.append("%d-%d" % (start, prev) if start != prev else str(start))
            parts.append("%s/%s:%s" % (mid, cid, ",".join(ranges)))
    spec = " ".join(parts)
    return spec if len(spec) <= max_len else spec[:max_len] + "…"
