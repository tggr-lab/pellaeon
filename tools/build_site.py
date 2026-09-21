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
NAV = [("index.html", "Home"), ("install.html", "Install"), ("tutorial.html", "Tutorial"), ("models.html", "Tested models"), ("classic.html", "Classic edition")]


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
<script>(function(){function sync(){var open=window.innerWidth>=820;document.querySelectorAll(".toc details").forEach(function(d){if(open)d.setAttribute("open","");else d.removeAttribute("open");});}sync();window.addEventListener("resize",sync);})();</script>
<footer><div class="in">Pellaeon v%s · MIT license · Named after Gilad Pellaeon, captain of the <i>Chimaera</i>. Not affiliated with UCSF. · <a href="https://github.com/tggr-lab/pellaeon/releases">Releases</a><br><span class="credits"><a href="https://github.com/tggr-lab" title="Translational Genetics and Genomics Research Lab"><img class="lab" src="img/tggr.png" alt="TGGR Lab"></a><span>Made by <a href="https://github.com/YAMIR-1138">Yam Amir</a> at the <a href="https://github.com/tggr-lab">TGGR Lab</a>.</span></span></div></footer>
<script>
(function(){
  document.querySelectorAll(".demo-panel").forEach(function(panel){
    var btn = panel.querySelector(".demo-play"), ctl = panel.querySelector(".demo-ctl"),
        img = btn.querySelector("img"), lbl = ctl.querySelector(".lbl");
    btn.dataset.playing = "0";
    function toggle(){
      var playing = btn.dataset.playing !== "1";
      btn.dataset.playing = playing ? "1" : "0";
      ctl.dataset.playing = btn.dataset.playing;
      img.setAttribute("src", playing ? img.dataset.gif + "?t=" + Date.now() : img.dataset.poster);
      lbl.textContent = playing ? "Stop" : "Play recording";
      btn.setAttribute("aria-label", playing ? "Stop the recording" : "Play the recording");
    }
    btn.addEventListener("click", toggle);
    ctl.addEventListener("click", toggle);
  });
  var tabs = document.querySelectorAll(".demo-tab");
  function selectDemo(tab){
    tabs.forEach(function(t){
      var on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.classList.toggle("active", on);
    });
    document.querySelectorAll(".demo-panel").forEach(function(p){
      var show = p.id === tab.dataset.target;
      if (!show) {
        var b = p.querySelector(".demo-play");
        if (b && b.dataset.playing === "1") b.click();
      }
      p.hidden = !show;
    });
  }
  tabs.forEach(function(tab, i){
    tab.addEventListener("click", function(){ selectDemo(tab); });
    tab.addEventListener("keydown", function(e){
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      var next = tabs[(i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) %% tabs.length];
      next.focus();
      selectDemo(next);
      e.preventDefault();
    });
  });
  var hint = document.getElementById("cmd-hint"), base = hint ? hint.textContent : "";
  document.querySelectorAll("#cmd span[data-t]").forEach(function(sp){
    sp.tabIndex = 0;
    ["mouseenter","focus","click"].forEach(function(ev){ sp.addEventListener(ev, function(){ hint.textContent = sp.dataset.t; }); });
    ["mouseleave","blur"].forEach(function(ev){ sp.addEventListener(ev, function(){ hint.textContent = base; }); });
  });
})();
</script>
</body></html>""" % (html.escape(title), DESC, html.escape(title), DESC, SITE, SITE, active, nav, REPO, main, VERSION)


def md_page(src, out, active, title=None):
    text = open(src, encoding="utf-8").read()
    # links between markdown docs -> generated pages; relative image paths stay (docs/img)
    text = (text.replace("../docs/img/", "img/").replace("classic/README.md", "classic.html").replace("docs/tutorial.md", "tutorial.html"))
    text = re.sub(r"\((tutorial|install|classic|models)\.md(#[^)]*)?\)", lambda m: "(%s.html%s)" % (m.group(1), m.group(2) or ""), text)
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


HOW_VSVG = '<svg class="how how-v" viewBox="0 0 420 1084" role="img" aria-label="How Pellaeon works, step by step" xmlns="http://www.w3.org/2000/svg"><defs><marker id="ahv" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--accent)"/></marker><marker id="ah2v" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--accent2)"/></marker></defs>\n<g class="wires">\n<path d="M200,138 L200,172"/>\n<path d="M200,290 L200,324"/>\n<path d="M200,442 L200,476"/>\n<path d="M200,594 L200,628"/>\n<path d="M200,746 L200,780"/>\n<path d="M200,898 L200,932"/>\n<path class="loop" d="M350,839 C410,839 410,383 350,383" marker-end="url(#ah2v)"/>\n<path class="next" d="M50,991 C-10,991 -10,79 50,79"/>\n</g>\n<g class="node n1" transform="translate(50,20)"><rect width="300" height="118" rx="12"/><foreignObject x="0" y="0" width="300" height="118"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>1 · You type</b><span>“color the transmembrane helices orange and the rest white”. Typos, “it” and gene names are fine.</span></div></foreignObject></g>\n<g class="node n2" transform="translate(50,172)"><rect width="300" height="118" rx="12"/><rect class="acc" x="14" y="0" width="272" height="4" rx="2"/><foreignObject x="0" y="0" width="300" height="118"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>2 · Context</b><span>What is open and selected. The documentation of your ChimeraX version, indexed locally. 131 tutorial workflows, 58 recipes, a list of known model mistakes.</span></div></foreignObject></g>\n<g class="node n3" transform="translate(50,324)"><rect width="300" height="118" rx="12"/><foreignObject x="0" y="0" width="300" height="118"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>3 · The model plans</b><span>Ollama on your PC, or Mistral, Gemini, Claude or OpenAI. It cannot type into ChimeraX; it can only call tools.</span></div></foreignObject></g>\n<g class="node n4" transform="translate(50,476)"><rect width="300" height="118" rx="12"/><rect class="acc" x="14" y="0" width="272" height="4" rx="2"/><foreignObject x="0" y="0" width="300" height="118"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>4 · Tools</b><span>run commands · read state · search docs · UniProt lookup · annotate (UniProt, ClinVar) · compare · your tables. Every call is logged.</span></div></foreignObject></g>\n<g class="node n5" transform="translate(50,628)"><rect width="300" height="118" rx="12"/><foreignObject x="0" y="0" width="300" height="118"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>5 · Safety gate</b><span>Closing, deleting, saving and scripts stop for an editable OK card. Everything else runs.</span></div></foreignObject></g>\n<g class="node n6" transform="translate(50,780)"><rect width="300" height="118" rx="12"/><rect class="acc" x="14" y="0" width="272" height="4" rx="2"/><foreignObject x="0" y="0" width="300" height="118"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>6 · ChimeraX runs it</b><span>Pellaeon reads the log: what changed, what failed. An error goes back with the real syntax and a suggestion, and the model retries a bounded number of times.</span></div></foreignObject></g>\n<g class="node n7" transform="translate(50,932)"><rect width="300" height="118" rx="12"/><foreignObject x="0" y="0" width="300" height="118"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>7 · Reply</b><span>Cards with every command: re-run, copy, open its ChimeraX docs. Export the chat as a .cxc script.</span></div></foreignObject></g>\n<text class="lab" transform="translate(410,472) rotate(90)">fix and retry</text>\n<text class="lab" transform="translate(20,536) rotate(-90)">next request</text>\n</svg>'

HOW_SVG = '<svg class="how" viewBox="0 0 1180 500" role="img" aria-labelledby="how-title" xmlns="http://www.w3.org/2000/svg"><title id="how-title">How Pellaeon turns a sentence into ChimeraX commands and checks the result</title>\n<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--accent)"/></marker><marker id="ah2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--accent2)"/></marker></defs>\n<g class="wires"><path d="M280,110 L315,110"/><path d="M565,110 L600,110"/><path d="M850,110 L885,110"/><path d="M1010,180 L1010,330"/><path d="M885,400 L850,400"/><path d="M600,400 L565,400"/><path class="loop" d="M725,330 C725,270 725,230 725,180" marker-end="url(#ah2)"/><path class="next" d="M315,400 L155,400 L155,180"/></g>\n<g class="node n1" transform="translate(30,40)"><rect width="250" height="140" rx="12"/><foreignObject x="0" y="0" width="250" height="140"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>1 · You type</b><span>“color the transmembrane helices orange and the rest white”. Typos, “it” and gene names are fine.</span></div></foreignObject></g>\n<g class="node n2" transform="translate(315,40)"><rect width="250" height="140" rx="12"/><rect class="acc" x="14" y="0" width="222" height="4" rx="2"/><foreignObject x="0" y="0" width="250" height="140"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>2 · Context</b><span>What is open and selected. The documentation of your ChimeraX version, indexed locally. 131 tutorial workflows, 58 recipes, a list of known model mistakes.</span></div></foreignObject></g>\n<g class="node n3" transform="translate(600,40)"><rect width="250" height="140" rx="12"/><foreignObject x="0" y="0" width="250" height="140"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>3 · The model plans</b><span>Ollama on your PC, or Mistral, Gemini, Claude or OpenAI. It cannot type into ChimeraX; it can only call tools.</span></div></foreignObject></g>\n<g class="node n4" transform="translate(885,40)"><rect width="250" height="140" rx="12"/><rect class="acc" x="14" y="0" width="222" height="4" rx="2"/><foreignObject x="0" y="0" width="250" height="140"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>4 · Tools</b><span>run commands · read state · search docs · UniProt lookup · annotate (UniProt, ClinVar) · compare · your tables. Every call is logged.</span></div></foreignObject></g>\n<g class="node n5" transform="translate(885,330)"><rect width="250" height="140" rx="12"/><foreignObject x="0" y="0" width="250" height="140"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>5 · Safety gate</b><span>Closing, deleting, saving and scripts stop for an editable OK card. Everything else runs.</span></div></foreignObject></g>\n<g class="node n6" transform="translate(600,330)"><rect width="250" height="140" rx="12"/><rect class="acc" x="14" y="0" width="222" height="4" rx="2"/><foreignObject x="0" y="0" width="250" height="140"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>6 · ChimeraX runs it</b><span>Pellaeon reads the log: what changed, what failed. An error goes back with the real syntax and a suggestion, and the model retries a bounded number of times.</span></div></foreignObject></g>\n<g class="node n7" transform="translate(315,330)"><rect width="250" height="140" rx="12"/><foreignObject x="0" y="0" width="250" height="140"><div xmlns="http://www.w3.org/1999/xhtml" class="nb"><b>7 · Reply</b><span>Cards with every command: re-run, copy, open its ChimeraX docs. Export the chat as a .cxc script.</span></div></foreignObject></g>\n<text class="lab" x="735" y="262">error + real syntax → try again</text><text class="lab" x="165" y="300">next request</text></svg>'

INDEX = """
<section class="hero">
  <div>
    <div class="term"><span class="path">~/molecules</span>$ pellaeon <span class="blink">&#9646;</span></div>
    <h1>Talk to ChimeraX in plain English.</h1>
    <p class="lead">Open structures, inspect residues, compare models, and make figures from a chat panel inside ChimeraX. See the commands Pellaeon runs and copy or reuse them.</p>
    <div class="cmdbox"><pre id="install-cmd">open %(installer)s</pre><button onclick="navigator.clipboard.writeText(document.getElementById('install-cmd').textContent).then(()=>this.textContent='Copied')">Copy</button></div>
    <p class="small">Paste into ChimeraX's command line.</p>
    <div class="btns"><a class="btn primary" href="#see-it-work">See it work</a><a class="btn" href="install.html">Install guide</a></div>
  </div>
  <div class="shot hero-gif"><picture><source srcset="img/hero_poster.png" media="(prefers-reduced-motion: reduce)"><img src="img/hero.gif" alt="Typing a request into the Pellaeon panel; ChimeraX opens hemoglobin, colors it, shows the hemes as red spheres and spins it"></picture></div>
</section>

<h2>Three steps</h2>
<ol class="steps">
  <li><b>Install the panel.</b> Paste the line above into ChimeraX's command line. <a href="install.html#chimerax-edition">Details</a></li>
  <li><b>Connect a model.</b> Point Pellaeon at Ollama on your computer, or paste a key for a cloud provider. <a href="install.html#choosing-an-ai">Local or cloud</a></li>
  <li><b>Run the built-in test request.</b> A button on the settings page opens ubiquitin and colors it by chain.</li>
</ol>
<p class="small">Setup steps: <a href="install.html">install guide</a>. Choosing a local or cloud model: <a href="models.html#choosing-a-model">model guidance</a>. With the default confirmation setting, file writes and destructive actions wait for your approval.</p>

<h2>How it works</h2>
<p>Pellaeon uses your current session and ChimeraX documentation to turn requests into commands. Each command is shown, and errors are returned to the model for correction.</p>
<div class="howwrap">
%(svg)s
</div>
<div class="howwrap-v">
%(vsvg)s
</div>

<h2>Reading a command</h2>
<p class="small">Every reply shows the ChimeraX commands it ran. Tap or focus on a part to see what it means.</p>
<div class="cmd" id="cmd">
<span data-t="color: the command; paints atoms and cartoons">color</span> <span data-t="#1: model 1, the first structure opened">#1</span><span data-t="/A: chain A of that model">/A</span><span data-t=":113: residue 113 of that chain">:113</span> <span data-t="blue: a named ChimeraX color; hex values work too">blue</span>
</div>
<p class="small" id="cmd-hint">model 1, chain A, residue 113, colored blue</p>

<h2>What you can do</h2>
<div class="group">
  <div><h3>Explore a structure</h3><p>Select a residue, inspect its neighbours, measure distances, or add annotations.</p></div>
  <a class="shot" href="img/tut/03_nearby_b.png" title="Click to enlarge"><img src="img/tut/03_nearby_b.png" alt="Residues around a heme group, labelled, with contacts drawn"></a>
</div>
<div class="group">
  <div><h3>Compare conformations</h3><p>Superpose structures and inspect residue displacements and contact differences.</p></div>
  <a class="shot" href="img/home_compare.png" title="Click to enlarge"><img src="img/home_compare.png" alt="A structure colored by C-alpha shift after fitting, with a color legend"></a>
</div>
<div class="group">
  <div><h3>Map your data</h3><p>Import a residue-score table, check its numbering, and color the structure by value or category.</p></div>
  <a class="shot" href="img/home_table.png" title="Click to enlarge"><img src="img/home_table.png" alt="Ubiquitin colored by a hydropathy column from an imported table, with a color legend"></a>
</div>
<div class="group">
  <div><h3>Save figures and views</h3><p>Export images with their recorded commands and sources. Save views and reuse figure styles.</p></div>
  <a class="shot" href="img/tut/04_pub.png" title="Click to enlarge"><img src="img/tut/04_pub.png" alt="A publication-style close-up of a histidine side chain next to a heme"></a>
</div>

<h2 id="see-it-work">See it work</h2>
<p class="small">Recorded in ChimeraX with the panel. Choose an example, then play or stop the recording.</p>
%(demos)s

<h2>Two editions</h2>
<p>The ChimeraX edition is a native bundle with a docked panel (<a href="install.html">install it</a>); the classic edition drives UCSF Chimera 1.x through its REST server and shows the panel in your browser (<a href="classic.html">classic edition</a>). Both editions share the chat interface and model providers. Table overlays and some analysis tools are ChimeraX-only: <a href="classic.html#differences-from-the-chimerax-edition">see the feature differences</a>.</p>

<h2>Privacy</h2>
<p>With a local Ollama server, model requests are processed on your computer. Fetching structures and annotations still contacts external databases (PDB, AlphaFold DB, UniProt, ClinVar, ConSurf-DB). With a cloud provider, your request, the list of open models, the current selection and any table you loaded are sent to that provider. Screenshots are shared only when the review-the-view option is enabled in Settings.</p>
<p class="small">Keys are stored on your computer, never in ChimeraX sessions. <a href="install.html#privacy">Privacy detail, including the free tiers</a> · <a href="install.html#where-things-are-stored">Where things are stored</a></p>
"""



DEMOS = [
    {
        "key": "table", "label": "Map a table",
        "req": "Import hydropathy scores and color ubiquitin.",
        "res": "76 residues mapped, with a color legend.",
        "guide": "tutorial.html#step-11-your-own-data-on-the-structure",
    },
    {
        "key": "compare", "label": "Compare structures",
        "req": "Compare structures and inspect contact differences.",
        "res": "Residue displacements and lost/gained contacts shown.",
        "guide": "tutorial.html#step-9-compare-two-conformations",
    },
    {
        "key": "figure", "label": "Prepare a figure",
        "req": "Rearrange overlapping labels, then save the figure.",
        "res": "Labels rearranged; 7 removed. Figure and session saved.",
        "guide": "tutorial.html#step-8b-when-labels-pile-up",
    },
]


def _demo_commands(raw):
    """Strip parenthetical asides ("(and 14 more ... commands)") out of the command log;
    return the cleaned command lines plus a human note about what was left out."""
    omissions = re.findall(r"\(and [^)]*\)", raw)
    cleaned = re.sub(r"\s*\(and [^)]*\)", "", raw)
    cmd_lines = [c.strip() for c in cleaned.split(" ; ") if c.strip()]
    notes = [o[len("(and "):-1].strip() for o in omissions]
    return cmd_lines, notes


def demos_html():
    """One large player plus a row of selectable examples above it; poster shown until played."""
    tabs, panels = [], []
    for i, ex in enumerate(DEMOS):
        key = ex["key"]
        p = os.path.join(DOCS, "img", "demo_%s.txt" % key)
        lines = [l.strip() for l in open(p, encoding="utf-8").read().splitlines() if l.strip()] if os.path.exists(p) else []
        starting, outcome, commands = (lines + ["", "", ""])[:3]
        cmd_lines, notes = _demo_commands(commands)

        tabs.append(
            '<button type="button" class="demo-tab%s" data-target="demo-%s" aria-selected="%s">%s</button>'
            % (" active" if i == 0 else "", key, "true" if i == 0 else "false", html.escape(ex["label"])))

        details_bits = []
        if starting:
            details_bits.append("<p>%s</p>" % html.escape(starting))
        if outcome:
            details_bits.append("<p>%s</p>" % html.escape(outcome))
        if notes:
            details_bits.append("<p>Selected commands below omit: %s.</p>" % "; ".join(html.escape(n) for n in notes))

        panels.append(
            '<figure class="demo-panel" id="demo-%s"%s>'
            '<button class="demo-play" type="button" aria-label="Play the recording">'
            '<img src="img/demo_%s_poster.png" data-poster="img/demo_%s_poster.png" data-gif="img/demo_%s.gif" alt="%s" loading="lazy">'
            '</button>'
            '<div class="demo-bar"><button class="demo-ctl" type="button"><span class="tri" aria-hidden="true"></span><span class="lbl">Play recording</span></button>'
            '<figcaption><span class="req">%s</span><span class="res">%s</span></figcaption></div>'
            '<p class="small"><a href="%s">Follow this guide</a> &middot; '
            '<a href="%s/tree/main/docs/examples/%s">Get the example files</a> &middot; '
            '<a href="img/demo_%s.gif">Open full size</a></p>'
            '<details><summary>Details</summary>%s</details>'
            '<details><summary>Selected commands</summary><pre>%s</pre></details>'
            '</figure>' % (
                key, "" if i == 0 else " hidden",
                key, key, key, html.escape(ex["label"]),
                html.escape(ex["req"]), html.escape(ex["res"]),
                ex["guide"], REPO, key, key,
                "".join(details_bits), html.escape("\n".join(cmd_lines)),
            ))
    return (
        '<div class="demo-tabs" role="tablist" aria-label="Choose an example">%s</div>'
        '<div class="demo-panels">%s</div>' % ("".join(tabs), "".join(panels))
    )


def index_page():
    body = INDEX % {"installer": REPO + "/releases/latest/download/install_pellaeon.py", "repo": REPO, "svg": HOW_SVG, "vsvg": HOW_VSVG, "demos": demos_html()}
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
    md_page(os.path.join(DOCS, "models.md"), "models.html", "models.html", "Tested models")
    md_page(os.path.join(ROOT, "classic", "README.md"), "classic.html", "classic.html", "Classic edition")
    check_links()
