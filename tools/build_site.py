"""Build the GitHub Pages site in docs/ from Markdown sources.   python tools/build_site.py

Pages: index.html (hand-written template below), install.html <- docs/install.md,
tutorial.html <- docs/tutorial.md, classic.html <- classic/README.md.
GitHub Pages serves docs/ as-is (docs/.nojekyll), so the generated HTML is committed.
"""
import html
import os
import re
import sys

import markdown  # python-markdown (pip install markdown) - only needed to build, not to view

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
REPO = "https://github.com/tggr-lab/pellaeon"
VERSION = re.search(r'__version__ = "([^"]+)"', open(os.path.join(ROOT, "src", "__init__.py")).read()).group(1)
NAV = [("index.html", "Home"), ("install.html", "Install"), ("tutorial.html", "Tutorial"), ("classic.html", "Classic edition")]


def layout(title, body, active, toc_html=""):
    nav = "".join('<a class="link%s" href="%s">%s</a>' % (" active" if f == active else "", f, n) for f, n in NAV)
    main = ('<div class="doc"><nav class="toc">%s</nav><article>%s</article></div>' % (toc_html, body)) if toc_html else body
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title><link rel="icon" href="logo.svg" type="image/svg+xml"><link rel="stylesheet" href="site.css">
<meta name="description" content="Pellaeon: talk to UCSF ChimeraX (and classic Chimera) in plain English. Local or cloud AI, every command shown, risky ones ask first."></head>
<body><div class="nav"><div class="in"><a class="brand" href="index.html"><img src="logo.svg" alt="">Pellaeon</a>%s<span class="spacer"></span><a class="link gh" href="%s">GitHub</a><button class="theme" id="theme-btn" type="button" title="Theme: follows your system. Click to switch" aria-label="Switch theme">&#9680;</button></div></div>
<script>(function(){var k="pellaeon-theme",r=document.documentElement;function ap(v){if(v)r.setAttribute("data-theme",v);else r.removeAttribute("data-theme");}var v=null;try{v=localStorage.getItem(k);}catch(e){}ap(v);document.addEventListener("DOMContentLoaded",function(){var b=document.getElementById("theme-btn");if(!b)return;var names={"":"system","light":"light","dark":"dark"};function lab(){b.title="Theme: "+names[r.getAttribute("data-theme")||""]+". Click to switch";}lab();b.onclick=function(){var cur=r.getAttribute("data-theme")||"";var nxt=cur===""?"light":cur==="light"?"dark":"";ap(nxt||null);try{nxt?localStorage.setItem(k,nxt):localStorage.removeItem(k);}catch(e){}lab();};});})();</script>
<main>%s</main>
<footer><div class="in">Pellaeon v%s · MIT license · Named after Gilad Pellaeon, captain of the <i>Chimaera</i>. Not affiliated with UCSF.<br><span class="credits"><a href="https://github.com/tggr-lab" title="Translational Genetics and Genomics Research Lab"><img class="lab" src="img/tggr.png" alt="TGGR Lab"></a><span>Made by <a href="https://github.com/YAMIR-1138">Yam Amir</a> at the <a href="https://github.com/tggr-lab">TGGR Lab</a>, with <a href="https://claude.com/claude-code">Claude Code</a>.</span></span></div></footer>
</body></html>""" % (html.escape(title), nav, REPO, main, VERSION)


def md_page(src, out, active, title=None):
    text = open(src, encoding="utf-8").read()
    # links between markdown docs -> generated pages; relative image paths stay (docs/img)
    text = (text.replace("../docs/img/", "img/").replace("classic/README.md", "classic.html").replace("docs/tutorial.md", "tutorial.html")
                .replace("(tutorial.md)", "(tutorial.html)").replace("(install.md)", "(install.html)").replace("(classic.md)", "(classic.html)"))
    md = markdown.Markdown(extensions=["fenced_code", "tables", "toc", "sane_lists"], extension_configs={"toc": {"toc_depth": "2-3"}})
    body = md.convert(text)
    t = title or (re.search(r"^# (.+)$", text, re.M).group(1) if re.search(r"^# (.+)$", text, re.M) else active)
    page = layout("%s - Pellaeon" % t, body, active, md.toc)
    open(os.path.join(DOCS, out), "w", encoding="utf-8").write(page)
    print("wrote", out)


INDEX = """
<section class="hero">
  <div>
    <div class="term"><span class="path">~/molecules</span>$ pellaeon <span class="blink">&#9646;</span></div>
    <h1>Talk to ChimeraX in plain English.</h1>
    <p class="lead">Pellaeon is a chat panel inside UCSF ChimeraX. Describe what you want; it runs the commands and shows every one of them. When a command fails it uses the error and the documentation to try a correction, and anything risky asks first.</p>
    <div class="cmdbox"><pre id="install-cmd">open %(installer)s</pre><button onclick="navigator.clipboard.writeText(document.getElementById('install-cmd').textContent).then(()=>this.textContent='Copied')">Copy</button></div>
    <p class="small">Paste that into ChimeraX's command line. That is the whole installation. <a href="install.html">Details, offline install and the classic Chimera edition</a>.</p>
    <div class="btns"><a class="btn primary" href="install.html">Install</a><a class="btn" href="tutorial.html">Tutorial with screenshots</a><a class="btn" href="%(repo)s/releases">Downloads</a></div>
  </div>
  <div class="shot hero-gif"><picture><source srcset="img/hero_poster.png" media="(prefers-reduced-motion: reduce)"><img src="img/hero.gif" alt="Typing a request into the Pellaeon panel; ChimeraX opens hemoglobin, colors it, shows the hemes as red spheres and spins it"></picture><p class="small">A real session: typed in the panel, run by a local model (qwen3:8b through Ollama), rendered by ChimeraX. Recorded, not live.</p></div>
</section>

<h2>Three steps to a first result</h2>
<ol class="steps">
  <li><b>Install the panel.</b> One line in ChimeraX's command line. <a href="install.html#chimerax-edition">Details</a></li>
  <li><b>Connect a model.</b> Local and private with Ollama, or a cloud key (Gemini has a free tier). <a href="install.html#choosing-an-ai">Local or cloud?</a></li>
  <li><b>Run a first request.</b> The settings page has a <i>Run a first request</i> button: it opens ubiquitin and colors it by chain through the AI, so you see the whole route work before you type your own.</li>
</ol>

<h2>Say it like you would say it to a colleague</h2>
<div class="example">open the AlphaFold model of F2RL1 and color residue 159 blue</div>
<div class="example">select amino acids 227 156 159 and 326 and show their atoms</div>
<div class="example">mesure the distance between 100 and 150</div>
<div class="example">compare these two models and tell me what changed</div>
<div class="example">show the disease variants on this model</div>
<p class="small">Typos, shorthand and "it" / "these" are fine: Pellaeon looks at what is open and selected.</p>

<h2>What you get</h2>
<div class="grid">
  <div class="card"><h3>Every command, visible</h3><p>Each reply shows the exact ChimeraX commands it ran, with copy, re-run and a link to ChimeraX's own documentation for that command. Export a whole chat as a replayable .cxc script.</p></div>
  <div class="card"><h3>Risky things ask first</h3><p>Closing models, deleting atoms, saving files and running scripts show an editable confirmation card. Everything else just happens.</p></div>
  <div class="card"><h3>Local or cloud AI, your choice</h3><p>Ollama on your own machine (free, private), Google Gemini's free tier, Claude, OpenAI, or any OpenAI-compatible server. Switch any time.</p></div>
  <div class="card"><h3>It knows your ChimeraX</h3><p>The documentation of the exact ChimeraX version you run is indexed on first launch, plus 130 tutorial workflows and 58 community recipes.</p></div>
  <div class="card"><h3>Click to ask</h3><p>Select a residue in the 3D view and ask "what is this?", "what is nearby?", or highlight it. Alt+click works too.</p></div>
  <div class="card"><h3>Your own data on the structure</h3><p>Import a CSV of per-residue values (conservation, mutational scans, your own groups). Pellaeon checks the numbering against the structure, reports what did not map, colors by value or category, and keeps each table as a layer you can ask about.</p></div>
  <div class="card"><h3>Compare and annotate</h3><p>"What changed between these two?" superposes and colors by displacement using MatchMaker's own alignment. "Show the disease variants" pulls UniProt or ClinVar, maps them onto the right chain with the right numbering, colors and labels.</p></div>
</div>

<h2>Two editions</h2>
<div class="grid">
  <div class="card"><h3>ChimeraX edition</h3><p>A native ChimeraX bundle: one line to install, docked panel, full session awareness. Windows, macOS, Linux.</p></div>
  <div class="card"><h3>Classic edition</h3><p>For the old UCSF Chimera 1.x: a small program with a launcher window and the same panel in your browser, driving Chimera through its REST server. <a href="classic.html">How it works</a>.</p></div>
</div>

<h2>Screens</h2>
<div class="shot"><img src="img/docked_4hhb.png" alt="ChimeraX with the Pellaeon panel docked on the right"><p class="small">The panel docks on the right of ChimeraX; the 3D view stays where it always was.</p></div>
<div class="grid">
  <div class="shot"><img src="img/06_compare.png" alt="Comparison result card"><p class="small">Comparison: RMSD, coverage, moving regions you can click.</p></div>
  <div class="shot"><img src="img/05_confirm.png" alt="Confirmation card"><p class="small">A risky command asks first, and the commands are editable.</p></div>
  <div class="shot"><img src="img/01_settings.png" alt="Provider settings"><p class="small">Choose an AI once; local Ollama, free Gemini, Claude, OpenAI.</p></div>
</div>

<h2>What to expect</h2>
<p>Pellaeon helps you inspect structures and run analyses. Its explanations stay traceable to the commands, measurements and sources behind them. Four kinds of things happen in a reply, and the cards tell them apart:</p>
<div class="grid">
  <div class="card"><h3>Commands run in ChimeraX</h3><p>Deterministic. Each one is listed with its result; a failed command shows the error and what was tried next.</p></div>
  <div class="card"><h3>Measurements</h3><p>Distances, RMSD, per-residue displacement, contacts: computed by ChimeraX, reported with the atom sets and thresholds used.</p></div>
  <div class="card"><h3>Database annotations</h3><p>UniProt features and ClinVar variants, mapped onto the structure with the numbering offset shown, and mismatches counted rather than hidden.</p></div>
  <div class="card"><h3>AI-written text</h3><p>The sentences around the cards come from the model. They summarize what ran; they are not a substitute for looking at the numbers. Smaller local models make more mistakes than cloud ones; known limitations are tracked on GitHub.</p></div>
</div>

<h2>Privacy, briefly</h2>
<p>With Ollama the model runs on your computer. Requests, the list of open models and the selection are sent only to the provider you choose. Gene and variant lookups go to UniProt and ClinVar when you ask for them. API keys are stored privately on your machine, never inside ChimeraX sessions.</p>
"""


def index_page():
    body = INDEX % {"installer": REPO + "/releases/latest/download/install_pellaeon.py", "repo": REPO}
    open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8").write(layout("Pellaeon: talk to ChimeraX in plain English", body, "index.html"))
    print("wrote index.html")


def check_links():
    bad = []
    for name in os.listdir(DOCS):
        if not name.endswith(".html"):
            continue
        page = open(os.path.join(DOCS, name), encoding="utf-8").read()
        for m in re.finditer(r'(?:href|src)="([^"#]+)"', page):
            ref = m.group(1)
            if ref.startswith(("http://", "https://", "mailto:")):
                continue
            if not os.path.exists(os.path.join(DOCS, ref)):
                bad.append((name, ref))
    if bad:
        print("BROKEN LOCAL LINKS:", bad)
        sys.exit(1)
    print("all local links resolve")


if __name__ == "__main__":
    open(os.path.join(DOCS, ".nojekyll"), "w").close()
    index_page()
    md_page(os.path.join(DOCS, "install.md"), "install.html", "install.html")
    md_page(os.path.join(DOCS, "tutorial.md"), "tutorial.html", "tutorial.html")
    md_page(os.path.join(ROOT, "classic", "README.md"), "classic.html", "classic.html", "Classic edition")
    check_links()
