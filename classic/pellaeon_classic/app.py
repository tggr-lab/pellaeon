"""Pellaeon Classic: the same chat panel, served to your browser, driving UCSF Chimera 1.x
through its REST server.   python -m pellaeon_classic  [--port 8765] [--chimera-port N] [--no-browser]
"""
from __future__ import annotations

import argparse
import glob
import secrets
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
# the shared core lives in the ChimeraX bundle's src/ (dev checkout) or is bundled next to us (pyz)
for cand in (os.path.join(HERE, "..", "..", "src"), os.path.join(HERE, "..", "src"), HERE):
    if os.path.isdir(os.path.join(cand, "core")) and cand not in sys.path:
        sys.path.insert(0, os.path.abspath(cand))
        break

import panel_base  # noqa: E402  (src/panel_base.py, imports core.* relatively)
from core.secrets import SecretStore  # noqa: E402
from pellaeon_classic.chimera_rest import ChimeraRestExecutor  # noqa: E402
from pellaeon_classic.settings import JsonSettings, user_dirs  # noqa: E402

UI_DIR = None
for cand in (os.path.join(HERE, "..", "..", "src", "ui"), os.path.join(HERE, "..", "src", "ui"), os.path.join(HERE, "ui")):
    if os.path.isfile(os.path.join(cand, "panel.html")):
        UI_DIR = os.path.abspath(cand)
        break
DATA_DIR = os.path.join(HERE, "data")


def find_chimera() -> str:
    cands = []
    if sys.platform == "win32":
        for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            cands += glob.glob(os.path.join(root, "Chimera*", "bin", "chimera.exe"))
    elif sys.platform == "darwin":
        cands += glob.glob("/Applications/Chimera*.app/Contents/MacOS/chimera")
    else:
        cands += glob.glob(os.path.expanduser("~/.local/UCSF-Chimera*/bin/chimera")) + glob.glob("/opt/UCSF/Chimera*/bin/chimera") \
            + glob.glob("/usr/local/chimera*/bin/chimera")
    p = shutil.which("chimera")
    if p:
        cands.append(p)
    return sorted(cands)[-1] if cands else ""


class ClassicPanel(panel_base.PanelBase):
    edition = "chimera"

    def __init__(self, chimera_port: int = 0):
        self._init_panel_state()
        self.dirs = user_dirs()
        for d in self.dirs.values():
            os.makedirs(d, exist_ok=True)
        self.data_dir = DATA_DIR
        self.settings = JsonSettings(os.path.join(self.dirs["config"], "settings.json"))
        if chimera_port:
            self.settings.chimera_port = int(chimera_port)
        self.secrets = SecretStore(self.dirs["config"])
        self.executor = ChimeraRestExecutor(int(self.settings.chimera_port or 0), DATA_DIR, self.dirs["cache"], log=self.log)
        self.clients: List["queue.Queue"] = []
        self.listeners: List[Any] = []      # callables(obj) for a native launcher window
        self.lock = threading.Lock()
        self._chimera_proc = None

    # ---- host hooks ----
    def push(self, obj: Dict[str, Any]) -> None:
        for fn in list(self.listeners):
            try:
                fn(obj)
            except Exception:
                pass
        if not self._page_ready:
            self._queued.append(obj)
            return
        data = json.dumps(obj, ensure_ascii=False)
        with self.lock:
            for q in list(self.clients):
                q.put(data)

    def push_ts(self, obj):
        self.push(obj)

    def call_soon(self, fn, *args):
        fn(*args)

    def log(self, msg):
        print(msg, flush=True)
        for fn in list(self.listeners):
            try:
                fn({"type": "log", "text": str(msg)})
            except Exception:
                pass

    def _act_copy(self, params, payload):
        self.push({"type": "toast", "kind": "warn", "text": "Select the command text and copy it (clipboard access is browser-only)."})

    def _act_open_url(self, params, payload):
        url = params.get("url", "")
        if url.startswith("help:user/commands/"):
            url = "https://www.cgl.ucsf.edu/chimera/docs/UsersGuide/midas/%s" % os.path.basename(url)
        if url.startswith("http"):
            webbrowser.open(url)

    def _act_export_cxc(self, params, payload):
        if not self.agent:
            return self.push({"type": "toast", "kind": "warn", "text": "Nothing to export yet."})
        lines = ["# Chimera command script exported by Pellaeon Classic (open it with: open script.cmd)"]
        for m in self.agent.archived + self.agent.conversation:
            if m.role == "tool":
                for r in m.tool_results_list():
                    if r.name == "run_commands":
                        try:
                            for x in json.loads(r.content).get("results", []):
                                if x.get("ok"):
                                    lines.append(x["command"])
                        except Exception:
                            pass
        path = os.path.join(self.dirs["data"], "pellaeon_session_%s.cmd" % time.strftime("%Y%m%d-%H%M%S"))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self.push({"type": "toast", "kind": "ok", "text": "Saved " + path})

    # ---- Chimera connection actions ----
    def _act_chimera_test(self, params, payload):
        port = int((payload or {}).get("chimera_port") or params.get("port") or self.settings.chimera_port or 0)
        if port:
            self.settings.chimera_port = port
            self.settings.save()
            self.executor.port = port
        self.push({"type": "chimera_status", "text": self.executor.ping(), "ok": "Connected" in self.executor.ping(), "port": port})

    def _act_chimera_launch(self, params, payload):
        path = (payload or {}).get("chimera_path") or self.settings.chimera_path or find_chimera()
        if not path or not os.path.exists(path):
            return self.push({"type": "chimera_status", "ok": False, "text": "Chimera not found. Enter the path to the chimera executable."})
        self.settings.chimera_path = path
        self.settings.save()
        threading.Thread(target=self._launch_chimera, args=(path,), daemon=True).start()

    def _launch_chimera(self, path):
        try:
            self._chimera_proc = subprocess.Popen([path, "--start", "RESTServer"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except Exception as e:  # noqa: BLE001
            return self.push({"type": "chimera_status", "ok": False, "text": "Could not start Chimera: %s" % e})
        self.push({"type": "chimera_status", "ok": False, "text": "Starting Chimera…"})
        t0 = time.time()
        for line in self._chimera_proc.stdout:
            m = re.search(r"REST server on host \S+ port (\d+)", line)
            if m:
                port = int(m.group(1))
                self.settings.chimera_port = port
                self.settings.save()
                self.executor.port = port
                self.push({"type": "chimera_status", "ok": True, "port": port, "text": "Chimera started; REST server on port %d." % port})
                break
            if time.time() - t0 > 120:
                self.push({"type": "chimera_status", "ok": False, "text": "Chimera started but no REST port was reported. Use Tools > Utilities > RESTServer and enter the port."})
                break

    def _init_message(self):
        m = super()._init_message()
        m["edition"] = "chimera"
        m["chimera_port"] = int(self.settings.chimera_port or 0)
        m["chimera_path"] = self.settings.chimera_path or find_chimera()
        return m

    def _build_agent(self):
        agent = super()._build_agent()
        agent.config.edition = "chimera"
        return agent


# ------------------------------------------------------------ HTTP server
PANEL: Optional[ClassicPanel] = None
TOKEN = secrets.token_urlsafe(24)


def _local_request(handler) -> bool:
    host = (handler.headers.get("Host") or "").split(":")[0]
    origin = handler.headers.get("Origin")
    if host not in ("127.0.0.1", "localhost"):
        return False
    if origin and not origin.startswith(("http://127.0.0.1:", "http://localhost:")):
        return False
    return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if not _local_request(self):
            return self._send(403, b"forbidden", "text/plain")
        if path in ("/", "/index.html"):
            html = open(os.path.join(UI_DIR, "panel.html"), encoding="utf-8").read()
            html = html.replace('<script src="panel.js"></script>',
                                '<script>window.PELLAEON_HTTP = true; window.PELLAEON_TOKEN = "%s";</script><script src="panel.js"></script>' % TOKEN)
            return self._send(200, html.encode("utf-8"))
        if path in ("/panel.css", "/panel.js"):
            data = open(os.path.join(UI_DIR, path[1:]), "rb").read()
            return self._send(200, data, "text/css" if path.endswith(".css") else "application/javascript")
        if path == "/events":
            qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if not secrets.compare_digest(qs.get("token", ""), TOKEN):
                return self._send(403, b"forbidden", "text/plain")
            q: "queue.Queue" = queue.Queue()
            with PANEL.lock:
                PANEL.clients.append(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                while True:
                    try:
                        data = q.get(timeout=15)
                        self.wfile.write(("data: " + data + "\n\n").encode("utf-8"))
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with PANEL.lock:
                    if q in PANEL.clients:
                        PANEL.clients.remove(q)
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/act/"):
            return self._send(404, b"not found", "text/plain")
        if not _local_request(self) or not secrets.compare_digest(self.headers.get("X-Pellaeon-Token", ""), TOKEN):
            return self._send(403, b"forbidden", "text/plain")
        action = parsed.path[len("/act/"):]
        params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        payload = None
        if "payload" in params:
            try:
                payload = json.loads(params["payload"])
            except Exception:
                payload = None
        handler = getattr(PANEL, "_act_" + action, None)
        if handler is None:
            return self._send(404, b"unknown action", "text/plain")
        try:
            handler(params, payload)
            self._send(200, b"ok", "text/plain")
        except Exception as e:  # noqa: BLE001
            import traceback
            PANEL.log("action %s failed: %s\n%s" % (action, e, traceback.format_exc()))
            PANEL.push({"type": "toast", "kind": "error", "text": "Pellaeon: %s" % e})
            self._send(500, str(e).encode("utf-8"), "text/plain")


def start_server(port: int = 8765, chimera_port: int = 0):
    """Create the panel and start the local web server in a background thread. Returns (server, panel, url)."""
    global PANEL
    PANEL = ClassicPanel(chimera_port)
    srv = None
    for p in [port] + [port + i for i in range(1, 20)]:
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        raise RuntimeError("No free port near %d" % port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, PANEL, "http://127.0.0.1:%d/" % port


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pellaeon Classic: plain-English control of UCSF Chimera 1.x")
    ap.add_argument("--port", type=int, default=8765, help="port for the panel web page (default 8765)")
    ap.add_argument("--chimera-port", type=int, default=0, help="port of Chimera's REST server (or set it in the panel)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--console", action="store_true", help="no launcher window; run in the terminal")
    args = ap.parse_args(argv)
    if not args.console:
        try:
            from pellaeon_classic.launcher import run_launcher
        except Exception:  # no Tk available: console mode
            run_launcher = None
        if run_launcher is not None:
            return run_launcher(port=args.port, chimera_port=args.chimera_port, open_browser=not args.no_browser)
    srv, panel, url = start_server(args.port, args.chimera_port)
    print("Pellaeon Classic is running at %s  (Ctrl+C to stop)" % url, flush=True)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
