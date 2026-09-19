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


DESC = "Pellaeon: talk to UCSF ChimeraX (and classic Chimera) in plain English. Local or cloud AI, every command shown, risky ones ask first."
SITE = "https://tggr-lab.github.io/pellaeon"


def layout(title, body, active, toc_html=""):
    nav = "".join('<a class="link%s" href="%s">%s</a>' % (" active" if f == active else "", f, n) for f, n in NAV)
    main = ('<div class="doc"><nav class="toc"><details open><summary>Contents</summary>%s</details></nav><article>%s</article></div>' % (toc_html, body)) if toc_html else body
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title><link rel="icon" href="logo.svg" type="image/svg+xml"><link rel="stylesheet" href="site.css">
<meta name="description" content="%s">
<meta property="og:title" content="%s"><meta property="og:description" content="%s"><meta property="og:image" content="%s/img/hero_poster.png"><meta property="og:url" content="%s/%s"><meta property="og:type" content="website"><meta name="twitter:card" content="summary_large_image"></head>
<body><div class="nav"><div class="in"><a class="brand" href="index.html"><img src="logo.svg" alt="">Pellaeon</a>%s<span class="spacer"></span><a class="link gh" href="%s">GitHub</a><button class="theme" id="theme-btn" type="button" title="Theme: follows your system. Click to switch" aria-label="Switch theme">&#9680;</button></div></div>
<script>(function(){var k="pellaeon-theme",r=document.documentElement;function ap(v){if(v)r.setAttribute("data-theme",v);else r.removeAttribute("data-theme");}var v=null;try{v=localStorage.getItem(k);}catch(e){}ap(v);document.addEventListener("DOMContentLoaded",function(){var b=document.getElementById("theme-btn");if(!b)return;var names={"":"system","light":"light","dark":"dark"};function lab(){b.title="Theme: "+names[r.getAttribute("data-theme")||""]+". Click to switch";}lab();b.onclick=function(){var cur=r.getAttribute("data-theme")||"";var nxt=cur===""?"light":cur==="light"?"dark":"";ap(nxt||null);try{nxt?localStorage.setItem(k,nxt):localStorage.removeItem(k);}catch(e){}lab();};});})();</script>
<script data-goatcounter="https://pellaeon.goatcounter.com/count" async src="https://gc.zgo.at/count.js"></script>
<main>%s</main>
<script>if(window.innerWidth<820){document.querySelectorAll(".toc details[open]").forEach(function(d){d.removeAttribute("open");});}</script>
<footer><div class="in">Pellaeon v%s · MIT license · Named after Gilad Pellaeon, captain of the <i>Chimaera</i>. Not affiliated with UCSF.<br><span class="credits"><a href="https://github.com/tggr-lab" title="Translational Genetics and Genomics Research Lab"><img class="lab" src="img/tggr.png" alt="TGGR Lab"></a><span>Made by <a href="https://github.com/YAMIR-1138">Yam Amir</a> at the <a href="https://github.com/tggr-lab">TGGR Lab</a>, with <a href="https://claude.com/claude-code">Claude Code</a>.</span></span></div></footer>
</body></html>""" % (html.escape(title), DESC, html.escape(title), DESC, SITE, SITE, active, nav, REPO, main, VERSION)


def md_page(src, out, active, title=None):
    text = open(src, encoding="utf-8").read()
    # links between markdown docs -> generated pages; relative image paths stay (docs/img)
    text = (text.replace("../docs/img/", "img/").replace("classic/README.md", "classic.html").replace("docs/tutorial.md", "tutorial.html")
                .replace("(tutorial.md)", "(tutorial.html)").replace("(install.md)", "(install.html)").replace("(classic.md)", "(classic.html)"))
    # developer sections stay in the repository README, not on the public page
    text = re.split(r"^## (?:Developing|Development|Building)\b.*$", text, maxsplit=1, flags=re.M)[0].rstrip() + "\n"
    md = markdown.Markdown(extensions=["fenced_code", "tables", "toc", "sane_lists"], extension_configs={"toc": {"toc_depth": "2-3"}})
    body = md.convert(text)
    body = body.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
    # standalone images become figures with the alt text as caption
    def _fig(m):
        tag = m.group(1)
        alt = re.search(r'alt="([^"]*)"', tag)
        if not alt or not alt.group(1).strip():
            return m.group(0)
        return "<figure>%s<figcaption>%s</figcaption></figure>" % (tag, alt.group(1))
    body = re.sub(r"<p>(<img [^>]*>)</p>", _fig, body)
    t = title or (re.search(r"^# (.+)$", text, re.M).group(1) if re.search(r"^# (.+)$", text, re.M) else active)
    page = layout("%s - Pellaeon" % t, body, active, md.toc)
    open(os.path.join(DOCS, out), "w", encoding="utf-8").write(page)
    print("wrote", out)


HOW_VSVG = '<svg class="how how-v" viewBox="0 0 420 1084" role="img" aria-label="How Pellaeon works, step by step" xmlns="http://www.w3.org/2000/svg"><defs><marker id="ahv" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--accent)"/></marker><marker id="ah2v" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--accent2)"/></marker></defs>\n<g class="wires">\n<path d="M200,138 L200,172"/>\n<path d="M200,290 L200,324"/>\n<path d="M200,442 L200,476"/>\n<path d="M200,594 L200,628"/>\n<path d="M200,746 L200,780"/>\n<path d="M200,898 L200,932"/>\n<path class="loop" d="M350,839 C410,839 410,383 350,383" marker-end="url(#ah2v)"/>\n<path class="next" d="M50,991 C-10,991 -10,79 50,79"/>\n</g>\n<g class="node n1" transform="translate(50,20)"><rect width="300" height="118" rx="12"/><foreignObject x="0" y="0" width="300" height="118"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>1 · You type</b><span>“color the transmembrane helices orange and the rest white”. Typos, “it” and gene names are fine.</span></div></foreignObject></g>\n<g class="node n2" transform="translate(50,172)"><rect width="300" height="118" rx="12"/><rect class="acc" x="14" y="0" width="272" height="4" rx="2"/><foreignObject x="0" y="0" width="300" height="118"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>2 · Context</b><span>What is open and selected. The documentation of your ChimeraX version, indexed locally. 131 tutorial workflows, 58 recipes, a list of known model mistakes.</span></div></foreignObject></g>\n<g class="node n3" transform="translate(50,324)"><rect width="300" height="118" rx="12"/><foreignObject x="0" y="0" width="300" height="118"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>3 · The model plans</b><span>Ollama on your PC, Gemini, Claude or OpenAI. It cannot type into ChimeraX; it can only call tools.</span></div></foreignObject></g>\n<g class="node n4" transform="translate(50,476)"><rect width="300" height="118" rx="12"/><rect class="acc" x="14" y="0" width="272" height="4" rx="2"/><foreignObject x="0" y="0" width="300" height="118"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>4 · Tools</b><span>run commands · read state · search docs · UniProt lookup · annotate (UniProt, ClinVar) · compare · your tables. Every call is logged.</span></div></foreignObject></g>\n<g class="node n5" transform="translate(50,628)"><rect width="300" height="118" rx="12"/><foreignObject x="0" y="0" width="300" height="118"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>5 · Safety gate</b><span>Closing, deleting, saving and scripts stop for an editable OK card. Everything else runs.</span></div></foreignObject></g>\n<g class="node n6" transform="translate(50,780)"><rect width="300" height="118" rx="12"/><rect class="acc" x="14" y="0" width="272" height="4" rx="2"/><foreignObject x="0" y="0" width="300" height="118"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>6 · ChimeraX runs it</b><span>Pellaeon reads the log: what changed, what failed. An error goes back with the real syntax and a suggestion, and the model retries a bounded number of times.</span></div></foreignObject></g>\n<g class="node n7" transform="translate(50,932)"><rect width="300" height="118" rx="12"/><foreignObject x="0" y="0" width="300" height="118"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>7 · Reply</b><span>Cards with every command: re-run, copy, open its ChimeraX docs. Export the chat as a .cxc script.</span></div></foreignObject></g>\n<text class="lab" transform="translate(410,472) rotate(90)">fix and retry</text>\n<text class="lab" transform="translate(20,536) rotate(-90)">next request</text>\n</svg>'

HOW_SVG = '<svg class="how" viewBox="0 0 1180 500" role="img" aria-labelledby="how-title" xmlns="http://www.w3.org/2000/svg"><title id="how-title">How Pellaeon turns a sentence into ChimeraX commands and checks the result</title>\n<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--accent)"/></marker><marker id="ah2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--accent2)"/></marker></defs>\n<g class="wires"><path d="M280,110 L315,110"/><path d="M565,110 L600,110"/><path d="M850,110 L885,110"/><path d="M1010,180 L1010,330"/><path d="M885,400 L850,400"/><path d="M600,400 L565,400"/><path class="loop" d="M725,330 C725,270 725,230 725,180" marker-end="url(#ah2)"/><path class="next" d="M315,400 L155,400 L155,180"/></g>\n<g class="node n1" transform="translate(30,40)"><rect width="250" height="140" rx="12"/><foreignObject x="0" y="0" width="250" height="140"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>1 · You type</b><span>“color the transmembrane helices orange and the rest white”. Typos, “it” and gene names are fine.</span></div></foreignObject></g>\n<g class="node n2" transform="translate(315,40)"><rect width="250" height="140" rx="12"/><rect class="acc" x="14" y="0" width="222" height="4" rx="2"/><foreignObject x="0" y="0" width="250" height="140"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>2 · Context</b><span>What is open and selected. The documentation of your ChimeraX version, indexed locally. 131 tutorial workflows, 58 recipes, a list of known model mistakes.</span></div></foreignObject></g>\n<g class="node n3" transform="translate(600,40)"><rect width="250" height="140" rx="12"/><foreignObject x="0" y="0" width="250" height="140"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>3 · The model plans</b><span>Ollama on your PC, Gemini, Claude or OpenAI. It cannot type into ChimeraX; it can only call tools.</span></div></foreignObject></g>\n<g class="node n4" transform="translate(885,40)"><rect width="250" height="140" rx="12"/><rect class="acc" x="14" y="0" width="222" height="4" rx="2"/><foreignObject x="0" y="0" width="250" height="140"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>4 · Tools</b><span>run commands · read state · search docs · UniProt lookup · annotate (UniProt, ClinVar) · compare · your tables. Every call is logged.</span></div></foreignObject></g>\n<g class="node n5" transform="translate(885,330)"><rect width="250" height="140" rx="12"/><foreignObject x="0" y="0" width="250" height="140"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>5 · Safety gate</b><span>Closing, deleting, saving and scripts stop for an editable OK card. Everything else runs.</span></div></foreignObject></g>\n<g class="node n6" transform="translate(600,330)"><rect width="250" height="140" rx="12"/><rect class="acc" x="14" y="0" width="222" height="4" rx="2"/><foreignObject x="0" y="0" width="250" height="140"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>6 · ChimeraX runs it</b><span>Pellaeon reads the log: what changed, what failed. An error goes back with the real syntax and a suggestion, and the model retries a bounded number of times.</span></div></foreignObject></g>\n<g class="node n7" transform="translate(315,330)"><rect width="250" height="140" rx="12"/><foreignObject x="0" y="0" width="250" height="140"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>7 · Reply</b><span>Cards with every command: re-run, copy, open its ChimeraX docs. Export the chat as a .cxc script.</span></div></foreignObject></g>\n<text class="lab" x="735" y="262">error + real syntax → try again</text><text class="lab" x="165" y="300">next request</text></svg>'

INDEX = """
<section class="hero">
  <div>
    <div class="term"><span class="path">~/molecules</span>$ pellaeon <span class="blink">&#9646;</span></div>
    <h1>Talk to ChimeraX in plain English.</h1>
    <p class="lead">A chat panel inside UCSF ChimeraX. Say what you want; Pellaeon runs the commands, shows each one, fixes failures from ChimeraX's own errors and documentation, and asks before anything risky.</p>
    <div class="cmdbox"><pre id="install-cmd">open %(installer)s</pre><button onclick="navigator.clipboard.writeText(document.getElementById('install-cmd').textContent).then(()=>this.textContent='Copied')">Copy</button></div>
    <p class="small">Paste into ChimeraX's command line. <a href="install.html">Offline install and the classic Chimera edition</a>.</p>
    <div class="btns"><a class="btn primary" href="install.html">Install</a><a class="btn" href="tutorial.html">Tutorial</a><a class="btn" href="%(repo)s/releases">Downloads</a></div>
  </div>
  <div class="shot hero-gif"><picture><source srcset="img/hero_poster.png" media="(prefers-reduced-motion: reduce)"><source srcset="img/hero_mobile.gif" media="(max-width: 700px)"><img src="img/hero.gif" alt="Typing a request into the Pellaeon panel; ChimeraX opens hemoglobin, colors it, shows the hemes as red spheres and spins it"></picture></div>
</section>

<h2>Three steps</h2>
<ol class="steps">
  <li><b>Install the panel.</b> The one line above. <a href="install.html#chimerax-edition">Details</a></li>
  <li><b>Connect a model.</b> Ollama on your own machine, or a cloud key; Gemini has a free tier. <a href="install.html#choosing-an-ai">Local or cloud?</a></li>
  <li><b>Run a first request.</b> A button on the settings page opens ubiquitin and colors it by chain, so you know the whole route works before you type your own.</li>
</ol>

<h2>Say it like you would to a colleague</h2>
<div class="example">open the AlphaFold model of F2RL1 and color residue 159 blue</div>
<div class="example">select amino acids 227 156 159 and 326 and show their atoms</div>
<div class="example">mesure the distance between 100 and 150</div>
<div class="example">compare these two models and tell me what changed</div>
<div class="example">show the disease variants on this model</div>

<h2>How it works</h2>
<p class="small">A plain chat model guesses commands from memory and never sees what happened. Pellaeon gives the model your ChimeraX's documentation and the live session, restricts it to logged tools, stops risky actions for your OK, and feeds every error back so it can correct itself.</p>
<div class="howwrap">
%(svg)s
</div>
<div class="howwrap-v">
%(vsvg)s
</div>

<h2>What you get</h2>
<div class="grid">
  <div class="card"><h3>Every command, visible</h3><p>Each reply lists the exact ChimeraX commands it ran, with copy, re-run and a link to ChimeraX's documentation for that command. Export a chat as a replayable .cxc script.</p></div>
  <div class="card"><h3>Risky things ask first</h3><p>Closing models, deleting atoms, saving files and running scripts show an editable confirmation card. Everything else just happens.</p></div>
  <div class="card"><h3>Local or cloud AI</h3><p>Ollama on your own machine (free, private), Google Gemini's free tier, Claude, OpenAI, or any OpenAI-compatible server. Switch any time.</p></div>
  <div class="card"><h3>Figures you can reproduce</h3><p>Save figure writes a folder, not a file: the image, a ChimeraX session, the replayable script, a per-residue color table, where every model came from, and a draft legend built only from what actually ran.</p></div>
  <div class="card"><h3>Ask why</h3><p>Select a residue and ask "why is this red?". Pellaeon answers from its own record: the command, table column, annotation or comparison that colored it, with the value and where it came from. A command that matched nothing is marked as such instead of getting a green tick.</p></div>
  <div class="card"><h3>Labels you can read</h3><p>Labels come out at a fixed size, on top of everything, black on white. Say "the labels overlap" and Pellaeon measures where each one lands on screen, nudges the ones that collide and removes the ones that cannot fit.</p></div>
  <div class="card"><h3>Click to ask</h3><p>Select a residue in the 3D view and ask what it is or what is nearby, or Alt-click it.</p></div>
  <div class="card"><h3>Your own data on the structure</h3><p>Import a CSV of per-residue values. Pellaeon checks the numbering against the structure, reports what did not map, and colors by value or category. Each table stays available as a layer.</p></div>
  <div class="card"><h3>Compare and annotate</h3><p>Superpose two models and color by displacement using MatchMaker's own alignment. Pull UniProt features or ClinVar variants onto the right chain with the right numbering.</p></div>
</div>

<h2>Two editions</h2>
<div class="grid">
  <div class="card"><h3>ChimeraX</h3><p>A native bundle: one line to install, docked panel, full session awareness. Windows, macOS, Linux.</p></div>
  <div class="card"><h3>Classic Chimera 1.x</h3><p>A small program with a launcher window and the same panel in your browser, driving Chimera through its REST server. <a href="classic.html">How it works</a>.</p></div>
</div>

<h2>Screens</h2>
<div class="shot"><img src="img/docked_4hhb.png" alt="ChimeraX with the Pellaeon panel docked on the right"><p class="small">The panel docks on the right; the 3D view stays where it always was.</p></div>
<div class="grid">
  <div class="shot"><img src="img/screen_compare.png" alt="Comparison result card"><p class="small">Comparing two conformations: fit RMSD, per-residue displacement, moving regions you can click.</p></div>
  <div class="shot"><img src="img/screen_confirm.png" alt="Confirmation card"><p class="small">A risky command waits for your OK; the commands are editable.</p></div>
  <div class="shot"><img src="img/01_settings.png" alt="Provider settings"><p class="small">Settings: pick a provider, test it, run a first request.</p></div>
</div>

<h2>Privacy</h2>
<p>With Ollama the model runs on your computer. Requests, the list of open models and the current selection go only to the provider you chose. Gene and variant lookups go to UniProt and ClinVar when you ask for them. API keys are stored on your machine, never in ChimeraX sessions.</p>
"""


def index_page():
    body = INDEX % {"installer": REPO + "/releases/latest/download/install_pellaeon.py", "repo": REPO, "svg": HOW_SVG, "vsvg": HOW_VSVG}
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
            if ref.startswith(("http://", "https://", "mailto:", "//")):
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
