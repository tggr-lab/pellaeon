"""End-to-end test of the classic edition against a stub Chimera REST server (no real Chimera needed).
    python classic/tools/e2e_stub_test.py "open 1zik and color it red"
"""
import json, os, re, subprocess, urllib.error, sys, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
KNOWN = set(json.load(open(os.path.join(ROOT, "classic/pellaeon_classic/data/cheatsheet_chimera.json"))))
STATE = {"models": []}

class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        cmd = (q.get("command") or [""])[0].strip()
        word = cmd.split()[0].lower().lstrip("~") if cmd else ""
        if cmd == "list models":
            out = "\n".join("model id #%d type Molecule name %s" % (i, n) for i, n in enumerate(STATE["models"]))
        elif cmd.startswith("list chains"):
            out = "chain id #0.A chain 1zik\nchain id #0.B chain 1zik"
        elif cmd.startswith("list selection"):
            out = ""
        elif word == "version":
            out = "UCSF Chimera version 1.19 (stub)"
        elif word == "usage":
            out = "Usage: color color_name[,a][,r][,s] atom-spec"
        elif word == "open":
            STATE["models"].append(cmd.split()[1]); out = "Opened %s" % cmd.split()[1]
        elif word == "close":
            STATE["models"] = []; out = ""
        elif re.search(r"#\d+/|\btarget\b|\bbgColor\b", cmd):
            out = "Error: Invalid atom specifier or option (ChimeraX syntax?): %s" % cmd
        elif word in KNOWN or word in ("mm", "rlabel", "sel"):
            out = ""
        else:
            out = "Error: Unrecognized command: \"%s\"" % word
        self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(out.encode())

if os.environ.get("PELLAEON_REAL_PORT"):   # test against a real Chimera REST server instead of the stub
    sport = int(os.environ["PELLAEON_REAL_PORT"])
else:
    stub = HTTPServer(("127.0.0.1", 0), Stub); sport = stub.server_address[1]
    threading.Thread(target=stub.serve_forever, daemon=True).start()
app = subprocess.Popen([sys.executable, "run.py", "--console", "--no-browser", "--port", "8799", "--chimera-port", str(sport)],
                       cwd=os.path.join(ROOT, "dist", "pellaeon-classic"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(2.0)
B = "http://127.0.0.1:8799"
import re as _re
TOKEN = _re.search(r'PELLAEON_TOKEN = "([^"]+)"', urllib.request.urlopen(B + "/").read().decode()).group(1)
def act(a, **kw):
    urllib.request.urlopen(urllib.request.Request(B + "/act/" + a + "?" + urllib.parse.urlencode(kw), method="POST",
                                                  headers={"X-Pellaeon-Token": TOKEN}), timeout=10).read()
events = []
def reader():
    r = urllib.request.urlopen(B + "/events?token=" + urllib.parse.quote(TOKEN), timeout=300)
    for line in r:
        line = line.decode().strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
threading.Thread(target=reader, daemon=True).start()
time.sleep(0.5)
html = urllib.request.urlopen(B + "/").read().decode()
print("page ok:", "PELLAEON_HTTP" in html and "Pellaeon" in html)
try:
    urllib.request.urlopen(urllib.request.Request(B + "/act/ready", method="POST"), timeout=5)
    print("AUTH BUG: unauthenticated action accepted")
except urllib.error.HTTPError as e:
    print("unauthenticated action rejected:", e.code)
act("ready")
act("settings_save", payload=json.dumps({"preset": "ollama", "model": "qwen3:8b", "base_url": "", "autonomy": "auto", "api_key": ""}))
act("chimera_test")
time.sleep(1)
print("init edition:", next((e.get("edition") for e in events if e["type"] == "init"), None), "| chimera:", next((e.get("text") for e in events if e["type"] == "chimera_status"), None))
req = sys.argv[1] if len(sys.argv) > 1 else "open 1zik and color it red"
act("send", text=req)
t0 = time.time()
while time.time() - t0 < 150:
    if any(e["type"] == "busy" and e["busy"] is False for e in events) and any(e["type"] == "assistant_done" for e in events):
        break
    time.sleep(0.5)
for e in events:
    if e["type"] == "tool_result" and e["name"] == "run_commands":
        print("RAN:", [(r["command"], r["ok"], r["error"][:60]) for r in e.get("results", [])])
    elif e["type"] == "tool_result":
        print("TOOL:", e["name"], e.get("summary"))
done = next((e for e in events if e["type"] == "assistant_done"), None)
print("REPLY:", (done or {}).get("html", "")[:200], "| error:", (done or {}).get("error"))
print("stub models:", STATE["models"])
app.terminate()
try:
    print("app log tail:", app.communicate(timeout=5)[0][-600:])
except Exception:
    pass
