"""Build the classic edition's knowledge data from downloaded Chimera doc pages.

    python classic/tools/build_chimera_docs.py classic/pellaeon_classic/data/chimera_docs classic/pellaeon_classic/data

Produces chimera_docs.json.gz (pre-parsed passages) and cheatsheet_chimera.json (command: purpose).
"""
import gzip, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))
from core.knowledge import parse_command_html  # noqa: E402

src, dst = sys.argv[1], sys.argv[2]
chunks, cheat = [], {}
for name in sorted(os.listdir(src)):
    if not name.endswith(".html"):
        continue
    html = open(os.path.join(src, name), encoding="utf-8", errors="replace").read()
    # Chimera pages put "Usage:" in an <h3>; make it inline so the shared parser picks it up
    html = re.sub(r"<h3>\s*<a[^>]*>Usage</a>\s*:\s*", "<p><b>Usage</b>: ", html, flags=re.I)
    html = re.sub(r"<h3>\s*Usage\s*:\s*", "<p><b>Usage</b>: ", html, flags=re.I)
    cmd = name[:-5]
    cs = parse_command_html(html, "midas/%s" % name)
    for c in cs:
        c.command = cmd
        c.url = "https://www.cgl.ucsf.edu/chimera/docs/UsersGuide/midas/%s" % name
    chunks.extend(cs)
    body = ""
    for c in cs:
        if c.kind != "doc":
            continue
        t = re.sub(r"\s+", " ", c.text)
        t = re.sub(r"^(Chimera Commands Index\s*)+", "", t)
        t = re.sub(r"Usage:.*?(?=[A-Z][a-z]+ [a-z])", "", t)  # skip usage lines to the first sentence
        if len(t) > 30:
            body = t
            break
    purpose = body[:150]
    cheat[cmd] = {"purpose": purpose, "keywords": [], "nl_queries": [], "notes": "", "related": ""}
with gzip.open(os.path.join(dst, "chimera_docs.json.gz"), "wt", encoding="utf-8") as f:
    json.dump([c.to_dict() for c in chunks], f)
json.dump(cheat, open(os.path.join(dst, "cheatsheet_chimera.json"), "w"), indent=1)
print("chunks", len(chunks), "commands", len(cheat), "gz bytes", os.path.getsize(os.path.join(dst, "chimera_docs.json.gz")))
