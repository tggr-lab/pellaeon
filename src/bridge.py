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


# Color keys and the pellaeon_title label describe the structure they were drawn for. Closing that
# structure left them on screen, over whatever was opened next; now they go with it.
# a key made by an attribute coloring (`color bfactor #1 key true`, `mlp #1 key true`) belongs to that structure too
_OVERLAY_RE = re.compile(r"^\s*(key\s+(?!delete\b)\S|2dlabels\s+(create|change)\s+pellaeon_title\b|"
                         r"(colou?r\s+(bfactor|byattr\w*)|mlp|coulombic)\b.*\bkey\s+(true|t|yes|1)\b)", re.I)


def _entry_metadata(m) -> str:
    """Method, resolution and citation from the mmCIF header: asked for 7kkj's resolution and journal, models
    answered from memory (2.7 A in Nature Communications; 1.60 A in Nature) even after reading the Log."""
    try:
        from chimerax.mmcif import get_mmcif_tables_from_metadata
        refine, em, exptl, cit = get_mmcif_tables_from_metadata(m, ["refine", "em_3d_reconstruction", "exptl", "citation"])
    except Exception:  # noqa: BLE001
        return ""
    parts = []

    def first(tab, field):
        try:
            v = tab.fields([field])[0][0] if tab is not None else ""
        except Exception:  # noqa: BLE001
            return ""
        return "" if v in ("?", ".", None) else str(v)
    method = first(exptl, "method")
    res = first(refine, "ls_d_res_high") or first(em, "resolution")
    if method:
        parts.append(method.lower())
    if res:
        try:
            parts.append("%.2f A resolution" % float(res))
        except ValueError:
            pass
    title, journal, year = first(cit, "title"), first(cit, "journal_abbrev"), first(cit, "year")
    if journal or title:
        parts.append("published %s%s%s" % (journal, (" " + year) if year else "", (': "%s"' % title[:90]) if title else ""))
    return "; ".join(parts)


def parse_error(session, text: str) -> str:
    """ChimeraX's own parser, without executing: the error it would raise, or "". About 3 ms.

    The parse phase of chimerax.core.commands.cli.Command.run, stopped before the function call.
    A malformed command (`select :10 add`, `cartoon style helix tube`, `color #1 rde`) is caught
    here with zero side effects, so the model gets the usage on the first try and nothing before
    it in the batch has to be undone."""
    from chimerax.core.commands import cli
    c = cli.Command(session)
    c._reset()
    c.current_text = text
    try:
        while True:
            c._find_command_name(True)
            if c._error:
                return str(c._error)
            if not c._ci:
                if len(c.current_text) > c.amount_parsed and c.current_text[c.amount_parsed] == ";":
                    c.amount_parsed += 1
                    continue
                return ""
            prev = c._process_positional_arguments()
            if c._error:
                return str(c._error)
            c._process_keyword_arguments(True, prev)
            if c._error:
                return str(c._error)
            missing = [kw for kw in c._ci._required_arguments if kw not in c._kw_args]
            if missing:
                return "Missing required %s" % ", ".join('"%s"' % m for m in missing)
            for cond in c._ci._postconditions:
                if not cond.check(c._kw_args):
                    return str(cond.error_message())
            rest = c.current_text[c.amount_parsed:]
            if not rest.strip():
                return ""
            if rest.lstrip().startswith(";"):
                c.amount_parsed = len(c.current_text) - len(rest.lstrip()) + 1
                c._ci = None
                c._kw_args = {}
                continue
            return "Unexpected text after the command: %r" % rest.strip()[:40]
    except Exception:  # noqa: BLE001  (the parser is not ours to debug; let the real run report)
        return ""


def _alignments(session) -> list:
    return list(getattr(getattr(session, "alignments", None), "alignments", []) or [])


def _track_overlays(session, commands: List[str], results: List[Dict[str, Any]]) -> None:
    if not any(r.get("ok") and _OVERLAY_RE.match(str(r.get("command", ""))) for r in results):
        return
    from chimerax.atomic import all_atomic_structures
    structs = list(all_atomic_structures(session))
    ids = set(re.findall(r"#(\d+)", " ".join(commands)))
    # a key that names no model belongs to the only structure, never to "all of them"
    owners = [m for m in structs if m.id and str(m.id[0]) in ids] or (structs if len(structs) == 1 else [])
    if not owners:
        return
    session._pellaeon_overlay_owners = owners
    if getattr(session, "_pellaeon_overlay_handler", None) is None:
        session._pellaeon_overlay_handler = session.triggers.add_handler("remove models", _models_removed)


def _models_removed(trigger_name, models):
    session = next((m.session for m in models if getattr(m, "session", None) is not None), None)
    owners = getattr(session, "_pellaeon_overlay_owners", None) if session is not None else None
    if not owners or not any(m in owners for m in models):
        return
    session._pellaeon_overlay_owners = None
    if getattr(session.ui, "is_gui", False):
        # not from inside the trigger that is closing models: after the next frame
        def once(*_):
            _clear_overlays(session)
            from chimerax.core.triggerset import DEREGISTER
            return DEREGISTER
        session.triggers.add_handler("new frame", once)
    else:
        _clear_overlays(session)


def _clear_overlays(session) -> None:
    removed = []
    try:
        from chimerax.color_key.model import get_model
        key = get_model(session, create=False)
        if key is not None and not key.deleted:
            key.delete()
            removed.append("color key")
    except Exception:  # noqa: BLE001
        pass
    try:
        from chimerax.label.label2d import session_labels
        lm = session_labels(session, create=False)
        lbl = lm.named_label("pellaeon_title") if lm is not None else None
        if lbl is not None:
            lbl.delete()
            removed.append("title")
    except Exception:  # noqa: BLE001
        pass
    if removed:
        session.logger.info("Pellaeon: removed the %s drawn for the closed structure." % " and ".join(removed))


_REMOTE_FILE_RE = re.compile(r"^\s*(open|runscript)\s+(https?://\S+\.(?:py|cxc|pdb|cif|mmcif|ent|pdb\.gz|cif\.gz))(\s.*)?$", re.I)
_HELP_RE = re.compile(r"^\s*(?:help|open\s+help:(?:user/)?(?:commands/)?)\s*([A-Za-z0-9_ ]*?)(?:\.html)?\s*$", re.I)

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
        before = set(id(t) for t in self.session.tools.list())
        alns_before = set(id(a) for a in _alignments(self.session))
        self.last_returns: Dict[str, Any] = {}   # values returned by commands (e.g. matchmaker's atom pairs)
        for cmd in commands:
            if cancel is not None and cancel.is_set():
                break
            entry: Dict[str, Any] = {"command": cmd, "ok": False, "info": [], "warnings": [], "error": ""}
            models_before = set(id(m) for m in self.session.models.list())
            rm = _REMOTE_FILE_RE.match(cmd)
            if rm:
                # `open https://.../flip.py` makes ChimeraX save a copy in ~/Downloads every time; fetch it into
                # Pellaeon's cache and run the local copy instead
                try:
                    from .analysis import fetch_to_cache
                    local = fetch_to_cache(rm.group(2), "recipes" if rm.group(2).lower().endswith((".py", ".cxc")) else "downloads")
                    cmd = "%s %s%s" % ("runscript" if local.lower().endswith(".py") else "open",
                                       ('"%s"' % local) if " " in local else local, rm.group(3) or "")
                    entry["command"] = cmd
                    entry["fetched_from"] = rm.group(2)
                except Exception as e:  # noqa: BLE001
                    entry["error"] = "Could not download %s: %s" % (rm.group(2), e)
                    results.append(entry)
                    self.last_error = entry["error"]
                    break
            hm = _HELP_RE.match(cmd)
            if hm:
                # `help X` opens a browser (the system browser in a headless run); answer with the usage text instead
                entry["ok"] = True
                entry["not_run"] = True
                entry["info"] = [self.command_usage(hm.group(1)) if hm.group(1) else
                                 "help opens the documentation browser; ask for a command's usage instead."]
                results.append(entry)
                continue
            perr = parse_error(self.session, cmd)
            if perr:
                entry["error"] = perr
                entry["not_run"] = True     # refused by the parser; nothing changed
                results.append(entry)
                self.last_error = "%s -> %s" % (cmd, perr)
                break
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
            # trim very long info dumps. Read-only reports keep more: `log metadata` of an entry with many authors
            # was cut before its resolution line and a model then invented one; a long `info residues` list is
            # also summarized as ranges before it is cut (the first dozen residues were reported as the answer)
            reading = cmd.split()[0].lower() in ("log", "info", "measure") if cmd.split() else False
            if reading:
                from .core.fixups import residue_ranges_summary
                summary = residue_ranges_summary("\n".join(entry["info"]))
                if summary:
                    entry["summary"] = summary
            entry["info"] = [m[:3000 if reading else 500] for m in entry["info"]]
            try:   # e.g. `define centroid` makes #1.2 and says nothing about its number
                made = [m for m in self.session.models.list() if id(m) not in models_before]
                if made:
                    entry["new_models"] = ["#%s %s" % (m.id_string, m.name) for m in made][:8]
            except Exception:  # noqa: BLE001
                pass
            results.append(entry)
            if not entry["ok"]:
                self.last_error = "%s -> %s" % (cmd, entry["error"])
                break
        else:
            self.last_error = None
        new = [t for t in self.session.tools.list() if id(t) not in before and t.tool_name != "Pellaeon"]
        self.last_new_tools = [t.tool_name for t in new]
        if not getattr(self.session.ui, "is_gui", False):   # headless: an alignment is the window it would have opened
            self.last_new_tools += ["Sequence Viewer" for a in _alignments(self.session) if id(a) not in alns_before]
        self.session._pellaeon_opened_tools = [t for t in getattr(self.session, "_pellaeon_opened_tools", [])
                                               if t in self.session.tools.list()] + new
        try:
            _track_overlays(self.session, commands, results)
        except Exception:  # noqa: BLE001  (bookkeeping must never fail a command batch)
            pass
        try:
            from .analysis import note_checkpoint_activity
            note_checkpoint_activity(self.session, commands, results)
        except Exception:  # noqa: BLE001  (bookkeeping must never fail a command batch)
            pass
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

    def membrane_orient(self, model: str, pdb: Optional[str] = None, slabs: bool = True) -> Dict[str, Any]:
        from .analysis import membrane_orient
        return _run_on_main_thread(self.session, lambda: membrane_orient(self.session, model, pdb, slabs))

    def view_axis(self, model: str = "", axis: str = "short") -> Dict[str, Any]:
        from .analysis import view_axis
        return _run_on_main_thread(self.session, lambda: view_axis(self.session, model or "", axis or "short"))

    def gpcr_states(self, protein: str, open_states: Optional[List[str]] = None) -> Dict[str, Any]:
        from .analysis import gpcr_states
        cache = os.path.join(pellaeon_dir("cache"), "gpcrdb")
        return _run_on_main_thread(self.session, lambda: gpcr_states(self.session, protein, open_states, cache))

    def sequence_identity(self, spec: str = "", chain: Optional[str] = None) -> Dict[str, Any]:
        from .analysis import sequence_identity
        return _run_on_main_thread(self.session, lambda: sequence_identity(self.session, spec, chain))

    def close_windows(self, which: str = "sequence") -> Dict[str, Any]:
        from .analysis import close_windows
        return _run_on_main_thread(self.session, lambda: close_windows(
            self.session, which, getattr(self.session, "_pellaeon_opened_tools", [])))

    def checkpoint_request(self, request_text: str) -> None:
        """Record a restore point before this request runs (see undo_last_request)."""
        from .analysis import checkpoint_request as _checkpoint
        cache_dir = os.path.join(pellaeon_dir("cache"), "checkpoints")
        _run_on_main_thread(self.session, lambda: _checkpoint(self.session, request_text, cache_dir))

    def pending_undo(self) -> Dict[str, Any]:
        from .analysis import pending_undo
        return _run_on_main_thread(self.session, lambda: pending_undo(self.session))

    def undo_last_request(self) -> Dict[str, Any]:
        from .analysis import undo_last_request
        return _run_on_main_thread(self.session, lambda: undo_last_request(self.session))

    def label_layout(self) -> Dict[str, Any]:
        from .analysis import label_layout
        return _run_on_main_thread(self.session, lambda: label_layout(self.session))

    def spec_atoms(self, text: str) -> Dict[str, Any]:
        from .analysis import spec_atoms
        return _run_on_main_thread(self.session, lambda: spec_atoms(self.session, text))

    def residue_provenance(self, res_spec: str, journal) -> Dict[str, Any]:
        from .analysis import residue_provenance
        return _run_on_main_thread(self.session, lambda: residue_provenance(self.session, res_spec, journal))

    def figure_info(self) -> Dict[str, Any]:
        from .analysis import figure_info
        return _run_on_main_thread(self.session, lambda: figure_info(self.session))

    def residue_colors(self) -> List[List[Any]]:
        from .analysis import residue_colors
        return _run_on_main_thread(self.session, lambda: residue_colors(self.session))

    def find_sequence(self, seq: str) -> List[str]:
        from .analysis import find_sequence
        return _run_on_main_thread(self.session, lambda: find_sequence(self.session, seq))

    def count_residues(self, spec: str) -> int:
        from .analysis import count_residues
        return _run_on_main_thread(self.session, lambda: count_residues(self.session, spec))

    def list_residues(self, model: str) -> Dict[str, Any]:
        from .analysis import list_residues
        return _run_on_main_thread(self.session, lambda: list_residues(self.session, model))

    def set_residue_attr(self, model: str, attr: str, values: Dict[str, Any]) -> Dict[str, Any]:
        from .analysis import set_residue_attr
        return _run_on_main_thread(self.session, lambda: set_residue_attr(self.session, model, attr, values))

    def map_positions(self, model: str, accession: str, positions: List[int]) -> Dict[str, Any]:
        from .analysis import map_positions
        return _run_on_main_thread(self.session, lambda: map_positions(self.session, model, accession, positions))

    def chain_uniprot(self, model: str) -> Dict[str, Any]:
        from .analysis import chain_uniprot
        return _run_on_main_thread(self.session, lambda: chain_uniprot(self.session, model))

    def run_python(self, code: str) -> Dict[str, Any]:
        def f():
            import contextlib
            from chimerax.core.commands import run
            buf = io.StringIO()
            ns = {"session": self.session, "run": run}
            try:
                with contextlib.redirect_stdout(buf):
                    exec(compile(code, "<pellaeon>", "exec"), ns)
                try:   # run_python can do anything: the undo fast path (close what was opened) no longer applies
                    from .analysis import mark_checkpoint_dirty
                    mark_checkpoint_dirty(self.session)
                except Exception:  # noqa: BLE001
                    pass
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
                if AtomicStructure is not None and not isinstance(m, AtomicStructure):
                    entry["note"] = "not a structure: a key, label or surface model; do not compare or annotate it"
                if AtomicStructure is not None and isinstance(m, AtomicStructure):
                    try:   # polymer residues; the raw count includes waters, ions and ligands and misleads
                        entry["num_residues"] = int(sum(len(c.existing_residues) for c in m.chains))
                        entry["num_residues_all"] = int(m.num_residues)
                    except Exception:  # noqa: BLE001
                        entry["num_residues"] = int(m.num_residues)
                    if "alphafold" in (m.name or "").lower():
                        entry["note"] = "AlphaFold model, colored by pLDDT confidence when opened: dark blue very high, light blue confident, yellow low, orange very low"
                    entry["num_atoms"] = int(m.num_atoms)
                    meta = _entry_metadata(m)
                    if meta:
                        entry["entry"] = meta
                    try:   # the residue names of ligands and ions: models guessed "STI" for 1stp's biotin (BTN)
                        a = m.atoms
                        cats = a.structure_categories
                        het = {}
                        for kind in ("ligand", "ions"):
                            names = sorted(set(a.filter(cats == kind).unique_residues.names))
                            if names:
                                het[kind] = names[:12]
                        if het:
                            entry["hets"] = het
                            names = _het_names(m, set(het.get("ligand", [])))
                            if names:
                                entry["het_names"] = names
                    except Exception:  # noqa: BLE001
                        pass
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
                if m.__class__.__name__ == "Volume":
                    try:   # plane indices and "the level now shown": the model needs the grid size and contour levels
                        entry.pop("note", None)
                        entry["grid"] = "x".join(str(int(n)) for n in m.data.size)
                        entry["levels"] = [round(float(sf.level), 4) for sf in m.surfaces][:4]
                        entry["style"] = "image" if getattr(m, "image_shown", False) else "surface"
                    except Exception:  # noqa: BLE001
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
        try:
            # named views ("view name pocket" / "view pocket"): the model can only go back to a
            # bookmark it knows exists
            from chimerax.std_commands.view import _named_views
            names = sorted(_named_views(session).keys())
            if names:
                state["views"] = names[:20]
        except Exception:
            pass
        if self.last_error:
            state["last_error"] = self.last_error
        return state


def _het_names(m, codes) -> Dict[str, str]:
    """Common names of the ligand codes (CAU -> (S)-Carazolol): users name the drug, the state lists the code.
    From the mmCIF chem_comp table: the first synonym, else the name when it is short."""
    out: Dict[str, str] = {}
    if not codes:
        return out
    try:
        from chimerax.mmcif import get_mmcif_tables_from_metadata
        table = get_mmcif_tables_from_metadata(m, ["chem_comp"])[0]
        if table is None:
            return out
        try:
            rows = table.fields(["id", "name", "pdbx_synonyms"], allow_missing_fields=True)
        except Exception:  # noqa: BLE001
            rows = [(i, n, "") for i, n in table.fields(["id", "name"])]
        for cid, name, syn in rows:
            if cid not in codes:
                continue
            syn = (syn or "").strip()
            pick = syn.split(";")[0].strip() if syn and syn != "?" else ""
            if not pick and name and len(name) <= 32:
                pick = name.strip()
            if pick and pick.upper() != cid.upper():
                out[cid] = pick[:40]
    except Exception:  # noqa: BLE001
        pass
    return out


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


# --------------------------------------------------------------------------------------
# Contact comparison (append-only). Attached to ChimeraXExecutor here instead of being
# written inside the class body so that this file only ever grows at the end.
# --------------------------------------------------------------------------------------

def _matchmaker_returns(executor) -> List[Dict[str, Any]]:
    """The dicts the last matchmaker command returned (its residue correspondence lives there)."""
    returns: List[Dict[str, Any]] = []

    def flatten(v):
        if isinstance(v, dict):
            returns.append(v)
        elif isinstance(v, (list, tuple)):
            for x in v:
                flatten(x)
    for cmd, val in getattr(executor, "last_returns", {}).items():
        if cmd.startswith(("matchmaker", "mm ")):
            flatten(val)
    return returns


def _residue_pairing(self, prep: Dict[str, Any]) -> Dict[str, Any]:
    from .analysis import residue_pairing
    returns = _matchmaker_returns(self)
    return _run_on_main_thread(self.session, lambda: residue_pairing(self.session, prep, returns))


def _residue_contacts(self, model_spec: str, cutoff: float = 4.0,
                      restrict: Optional[str] = None) -> Dict[str, Any]:
    from .analysis import residue_contacts
    return _run_on_main_thread(self.session, lambda: residue_contacts(self.session, model_spec, cutoff, restrict))


ChimeraXExecutor.residue_pairing = _residue_pairing
ChimeraXExecutor.residue_contacts = _residue_contacts
