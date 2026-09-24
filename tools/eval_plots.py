"""Build the model-comparison figures and table for docs/models.md from scenario-harness reports.

    python tools/eval_plots.py <reports_dir> [--post <dir>]

<reports_dir> holds the JSON reports written by tests_chimerax/scenarios.py (PELLAEON_REPORT).
This script looks for these exact filenames (present or absent; missing ones are skipped with
a note, nothing else breaks):

  blind set (100 requests, blind.json / blind50.json):
    gemma_blind_guards_on.json    gemma_blind_guards_off.json     gemma4:12b, local, free
    mistral_blind_guards_on.json  mistral_blind_guards_off.json   ministral-8b-latest, cloud
    m14b_blind_guards_on.json                                     ministral-14b-latest, cloud
    haiku_blind.json                                              Claude Haiku 4.5, 100 requests
    sonnet_blind50.json                                           Claude Sonnet 5, 50 requests

  dev set (152 requests x3, scenarios_real.json), guards on/off only, used for the guard-delta
  cross-check, not for the headline figures:
    gemma_dev_guards_on.json      gemma_dev_guards_off.json
    mistral_dev_guards_on.json    mistral_dev_guards_off.json

--post points at a second directory with the same filenames (a fix round rerun); missing files,
or a missing/absent directory entirely, are fine -- the post series is just left out.

Difficulty (simple / multi-step / ambiguous / impossible) and source for each blind-set request
id are read from the blind-set metadata file (BLIND_META below) for the categories figure only;
no request text is copied into this script's output.

Writes, all under docs/img/eval/:
    scores.svg       one horizontal bar per model, blind set, mean pass rate with a min-max
                      whisker over the 3 repeats (paid single-run models get no whisker).
    categories.svg    small multiples, one panel per model, bars for the 4 difficulty buckets.
    speed_cost.svg    median seconds/request (x) vs pass rate (y), point size = cost/100
                      requests where a price is known.
    table.md          the summary table as a markdown fragment, wrapped in
                      <!-- eval:table:start/end --> markers so it can be pasted into
                      docs/models.md between the same markers.

No third-party plotting library is used (matplotlib was not importable in the dev environment
this was built in); the SVGs are written by hand.
"""
import argparse
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "docs", "img", "eval")
BLIND_META = os.environ.get("PELLAEON_BLIND_META") or os.path.join(
    os.path.dirname(ROOT), "blind_sets", "blind_2026-09-23.json")

TEAL = "#1d6f78"
TEAL_LIGHT = "#8fbcc0"
ORANGE = "#e8891a"
ORANGE_LIGHT = "#f3c58c"
GRAY = "#5f6670"   # 5.2:1 on white; the lighter gray failed AA
GRAY_LIGHT = "#c7cbd1"
INK = "#333"
GRID = "#d7dade"

# (key, filename, display name, tag, is a headline / guards-on config for scores+categories)
BLIND_FILES = [
    ("gemma_on",     "gemma_blind_guards_on.json",  "Gemma 4 12B",   "local, free", True),
    ("gemma_off",    "gemma_blind_guards_off.json", "Gemma 4 12B",   "local, free", False),
    ("mistral8_on",  "mistral_blind_guards_on.json",  "Ministral 8B", "cloud", True),
    ("mistral8_off", "mistral_blind_guards_off.json", "Ministral 8B", "cloud", False),
    ("mistral14_on", "m14b_blind_guards_on.json",      "Ministral 14B", "cloud", True),
    ("haiku",        "haiku_blind.json",   "Claude Haiku 4.5", "cloud, paid", True),
    ("sonnet50",     "sonnet_blind50.json", "Claude Sonnet 5", "cloud, paid", True),
]
DEV_FILES = [
    ("gemma_dev_on",     "gemma_dev_guards_on.json",  "Gemma 4 12B",  "local, free"),
    ("gemma_dev_off",    "gemma_dev_guards_off.json", "Gemma 4 12B",  "local, free"),
    ("mistral8_dev_on",  "mistral_dev_guards_on.json",  "Ministral 8B", "cloud"),
    ("mistral8_dev_off", "mistral_dev_guards_off.json", "Ministral 8B", "cloud"),
]
# $ per million tokens, (input, output); anything not listed here is left unpriced (n/a), not free.
PRICES = {
    "anthropic:claude-haiku-4-5": (1.0, 5.0),
    "anthropic:claude-sonnet-5": (3.0, 15.0),
}
DIFFICULTIES = ["simple", "multi-step", "ambiguous", "impossible"]
DIFF_LABEL = {"simple": "S", "multi-step": "M", "ambiguous": "A", "impossible": "I"}


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_dir(d):
    """filename -> parsed report, for whichever of BLIND_FILES/DEV_FILES exist in d."""
    out = {}
    if not d or not os.path.isdir(d):
        return out
    for _key, fname, *_rest in BLIND_FILES + DEV_FILES:
        p = os.path.join(d, fname)
        if not os.path.isfile(p):                       # a fix-round rerun is saved as <stem>_post.json
            p = os.path.join(d, fname[:-5] + "_post.json")
        if os.path.isfile(p):
            out[fname] = load_json(p)
    return out


def is_scope_failure(r):
    """A failure caused by doing more than asked, not by doing the wrong thing or nothing."""
    if r.get("ok"):
        return False
    guard_keys = set(g.split(":", 1)[0] for g in (r.get("guards") or []))
    if "scope" in guard_keys:
        return True
    detail = r.get("detail") or ""
    return any(kw in detail for kw in ("forbidden command", "forbidden tool", "opened tool windows", "structures/maps open"))


def stats(report):
    """Per-report summary: repeat mean/min/max pass rate, median seconds, scope failures, usage/cost."""
    results = report["results"]
    reps = report.get("repeats", 1)
    n_scen = len(results) // reps
    repeat_counts = [0] * reps
    for i, r in enumerate(results):
        repeat_counts[i % reps] += 1 if r["ok"] else 0
    repeat_rates = [c / n_scen for c in repeat_counts]
    passed = sum(r["ok"] for r in results)
    med_sec = statistics.median(r["seconds"] for r in results)
    scope_fails = sum(is_scope_failure(r) for r in results)
    total_fails = len(results) - passed
    has_usage = "usage" in results[0]
    tin = sum(r["usage"]["input"] for r in results) if has_usage else None
    tout = sum(r["usage"]["output"] for r in results) if has_usage else None
    price = PRICES.get(report.get("model"))
    cost_per_100 = None
    if has_usage and price:
        cost_total = tin / 1e6 * price[0] + tout / 1e6 * price[1]
        cost_per_100 = cost_total / len(results) * 100
    return dict(n_scen=n_scen, reps=reps, n_results=len(results), passed=passed,
                rate=passed / len(results), mean=statistics.mean(repeat_rates),
                minr=min(repeat_rates), maxr=max(repeat_rates), med_sec=med_sec,
                scope_fails=scope_fails, total_fails=total_fails,
                tokens_in=tin, tokens_out=tout, cost_per_100=cost_per_100)


def by_difficulty(report, diff_of):
    """{difficulty: (passed, total)} across all repeats, for a blind-set report."""
    out = {d: [0, 0] for d in DIFFICULTIES}
    for r in report["results"]:
        d = diff_of.get(r["id"])
        if d not in out:
            continue
        out[d][1] += 1
        out[d][0] += 1 if r["ok"] else 0
    return out


def haiku_on_sonnet_ids(haiku_report, sonnet_report):
    sonnet_ids = [r["id"] for r in sonnet_report["results"]]
    hmap = {r["id"]: r["ok"] for r in haiku_report["results"]}
    missing = [i for i in sonnet_ids if i not in hmap]
    matched = [hmap[i] for i in sonnet_ids if i in hmap]
    return sum(matched), len(matched), missing


# ---------------------------------------------------------------- SVG helpers

def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=12, anchor="start", fill=INK, weight="normal", family="-apple-system,Helvetica,Arial,sans-serif"):
    return ('<text x="%g" y="%g" font-size="%g" text-anchor="%s" fill="%s" font-weight="%s" '
            'font-family="%s">%s</text>' % (x, y, size, anchor, fill, weight, family, esc(s)))


def rect(x, y, w, h, fill, rx=2, opacity=1):
    if w < 0:
        x, w = x + w, -w
    return '<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="%s" opacity="%g"/>' % (x, y, w, h, rx, fill, opacity)


def line(x1, y1, x2, y2, stroke, width=1, dash=None, cap="round"):
    d = ' stroke-dasharray="%s"' % dash if dash else ""
    return '<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="%g" stroke-linecap="%s"%s/>' % (
        x1, y1, x2, y2, stroke, width, cap, d)


def circle(cx, cy, r, fill, opacity=1, stroke=None, stroke_width=1.5):
    s = ' stroke="%s" stroke-width="%g"' % (stroke, stroke_width) if stroke else ""
    return '<circle cx="%g" cy="%g" r="%g" fill="%s" opacity="%g"%s/>' % (cx, cy, r, fill, opacity, s)


def svg(w, h, body, extra_style=""):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %g %g" width="%g" height="%g" '
            'role="img">%s%s</svg>' % (w, h, w, h, extra_style, body))


def pct(x):
    return "%.0f%%" % round(x * 100)


def pct1(x):
    return "%.1f%%" % (x * 100)


# ---------------------------------------------------------------- charts

def chart_scores(rows, post_rows, path):
    """rows: [(label, tag, mean, minr, maxr, reps)], sorted caller-side. post_rows keyed same order or None."""
    left, top, plot_w, bar_h, gap = 210, 46, 420, 22, 20
    row_h = bar_h + gap
    w = left + plot_w + 70
    h = top + len(rows) * row_h + 30
    parts = []
    # axis gridlines 0/25/50/75/100
    for p in (0, 25, 50, 75, 100):
        x = left + plot_w * p / 100
        parts.append(line(x, top - 10, x, top + len(rows) * row_h - gap + 4, GRID, 1))
        parts.append(text(x, top - 16, "%d%%" % p, 10.5, "middle", GRAY))
    legend_y = 20
    parts.append(circle(left, legend_y - 4, 5, TEAL))
    parts.append(text(left + 11, legend_y, "current", 11, "start", INK))
    lx = left + 80
    if post_rows:
        parts.append(circle(lx, legend_y - 4, 5, ORANGE))
        parts.append(text(lx + 11, legend_y, "post-fix", 11, "start", INK))
    for i, (label, tag, mean, minr, maxr, reps) in enumerate(rows):
        y = top + i * row_h
        parts.append(text(left - 12, y + bar_h * 0.68, label, 12.5, "end", INK, "600"))
        parts.append(text(left - 12, y + bar_h + 11, tag, 9.5, "end", GRAY))
        has_post = bool(post_rows and post_rows.get(i))
        bw = plot_w * mean
        bh = bar_h if not has_post else (bar_h - 3) / 2
        parts.append(rect(left, y, bw, bh, TEAL))
        label_x_end = bw
        if reps > 1:
            wx1, wx2 = left + plot_w * minr, left + plot_w * maxr
            wy = y + bh / 2
            parts.append(line(wx1, wy, wx2, wy, "#0d4247", 2))
            parts.append(line(wx1, y + 3, wx1, y + bh - 3, "#0d4247", 2))
            parts.append(line(wx2, y + 3, wx2, y + bh - 3, "#0d4247", 2))
            label_val = "%s (%.0f-%.0f)" % (pct(mean), minr * 100, maxr * 100)
            label_x_end = max(bw, plot_w * maxr)
        else:
            label_val = "%s (n=1)" % pct(mean)
        label_y = y + (bh * 0.7 if has_post else bh * 0.68)
        parts.append(text(left + label_x_end + 10, label_y, label_val, 11, "start", INK))
        if has_post:
            pmean, pminr, pmaxr, preps = post_rows[i]
            py = y + bh + 3
            pbw = plot_w * pmean
            parts.append(rect(left, py, pbw, bh, ORANGE))
            p_label_x_end = pbw
            if preps > 1:
                wx1, wx2 = left + plot_w * pminr, left + plot_w * pmaxr
                wy = py + bh / 2
                parts.append(line(wx1, wy, wx2, wy, "#8a4c0a", 1.6))
                parts.append(line(wx1, py + 2, wx1, py + bh - 2, "#8a4c0a", 1.6))
                parts.append(line(wx2, py + 2, wx2, py + bh - 2, "#8a4c0a", 1.6))
                p_label = "-> %s (%.0f-%.0f)" % (pct(pmean), pminr * 100, pmaxr * 100)
                p_label_x_end = max(pbw, plot_w * pmaxr)
            else:
                p_label = "-> %s (n=1)" % pct(pmean)
            parts.append(text(left + p_label_x_end + 10, py + bh * 0.75, p_label, 9.5, "start", "#8a4c0a"))
    open(path, "w").write(svg(w, h, "".join(parts)))


def chart_categories(panels, post_panels, path):
    """panels: [(label, {difficulty: (ok,total)})]. post_panels: same keyed by label, or None."""
    panel_w, gap, top, bottom_labels_h = 120, 20, 40, 34
    plot_h = 190
    w = len(panels) * panel_w + (len(panels) - 1) * gap + 20
    h = top + plot_h + bottom_labels_h + (38 if post_panels else 20)
    parts = []
    for p in (0, 50, 100):
        y = top + plot_h - plot_h * p / 100
        parts.append(line(10, y, w - 10, y, GRID, 1))
        parts.append(text(8, y + 3, "%d" % p, 9, "end", GRAY))
    for i, (label, cats) in enumerate(panels):
        px = 10 + i * (panel_w + gap)
        parts.append(text(px + panel_w / 2, top - 12, label, 11.5, "middle", INK, "600"))
        pdata = post_panels.get(label) if post_panels else None
        n_bars = 4
        slot_w = panel_w / n_bars
        bar_w = slot_w * (0.42 if pdata else 0.5)
        for j, d in enumerate(DIFFICULTIES):
            ok, tot = cats.get(d, (0, 0))
            rate = (ok / tot) if tot else 0
            bx = px + j * slot_w + (slot_w - bar_w * (2 if pdata else 1) - (4 if pdata else 0)) / 2
            bh = plot_h * rate
            parts.append(rect(bx, top + plot_h - bh, bar_w, bh, TEAL))
            if tot:
                parts.append(text(bx + bar_w / 2, top + plot_h - bh - 4, "%d" % round(rate * 100), 8.3, "middle", GRAY))
            if pdata:
                pok, ptot = pdata.get(d, (0, 0))
                prate = (pok / ptot) if ptot else 0
                pbh = plot_h * prate
                pbx = bx + bar_w + 4
                parts.append(rect(pbx, top + plot_h - pbh, bar_w, pbh, ORANGE))
            parts.append(text(px + j * slot_w + slot_w / 2, top + plot_h + 14, DIFF_LABEL[d], 10, "middle", GRAY))
        parts.append(line(px, top + plot_h, px + panel_w, top + plot_h, GRID, 1))
    parts.append(text(10, top + plot_h + bottom_labels_h + 8,
                       "S simple  M multi-step  A ambiguous  I impossible -- pass rate, %", 10, "start", GRAY))
    if post_panels:
        ly = top + plot_h + bottom_labels_h + 30
        parts.append(circle(14, ly - 4, 4.5, TEAL))
        parts.append(text(22, ly, "current", 10, "start", INK))
        parts.append(circle(94, ly - 4, 4.5, ORANGE))
        parts.append(text(102, ly, "post-fix", 10, "start", INK))
    open(path, "w").write(svg(w, h, "".join(parts)))


def chart_speed_cost(points, post_points, path):
    """points: [(label, seconds, rate, cost_per_100_or_None, color)]"""
    left, top, plot_w, plot_h = 56, 44, 560, 260
    w = left + plot_w + 30
    h = top + plot_h + 76
    max_sec = max(p[1] for p in points) * 1.25
    parts = []
    for p in (0, 25, 50, 75, 100):
        y = top + plot_h - plot_h * p / 100
        parts.append(line(left, y, left + plot_w, y, GRID, 1))
        parts.append(text(left - 8, y + 3, "%d%%" % p, 10, "end", GRAY))
    n_xticks = 6
    for i in range(n_xticks + 1):
        sec = max_sec * i / n_xticks
        x = left + plot_w * i / n_xticks
        parts.append(line(x, top, x, top + plot_h, GRID, 0.6))
        parts.append(text(x, top + plot_h + 16, "%.0fs" % sec, 10, "middle", GRAY))
    parts.append(text(left + plot_w / 2, top + plot_h + 34, "median seconds per request", 10.5, "middle", GRAY))
    parts.append(text(left, top - 16, "pass rate, %", 10.5, "start", GRAY))

    def radius(cost):
        if cost is None:
            return 6
        return max(7, min(26, 7 + cost * 6))

    geo = [(label, left + plot_w * sec / max_sec, top + plot_h - plot_h * rate, radius(cost), cost, color)
           for label, sec, rate, cost, color in points]
    # two labels whose points sit close together would otherwise print on top of each other (this data
    # set has two Ministral points a few percent and a second apart); flip the later one below its point.
    placed = []
    sides = {}
    for label, x, y, r, cost, color in sorted(geo, key=lambda g: g[1]):
        side = "above"
        for (px, py, pside) in placed:
            if abs(x - px) < 105 and abs(y - py) < 44 and pside == side:
                side = "below"
                break
        placed.append((x, y, side))
        sides[label] = side

    for label, x, y, r, cost, color in geo:
        parts.append(circle(x, y, r, color, 0.82))
        cost_lbl = ("$%.2f/100" % cost) if cost is not None else "n/a"
        if sides[label] == "above":
            ny, cy = y - r - 16, y - r - 4
        else:
            ny, cy = y + r + 14, y + r + 26
        parts.append(text(x, ny, label, 11, "middle", INK, "600"))
        parts.append(text(x, cy, cost_lbl, 9.5, "middle", GRAY))
        if post_points and label in post_points:
            psec, prate, pcost = post_points[label]
            px = left + plot_w * psec / max_sec
            py = top + plot_h - plot_h * prate
            pr = radius(pcost)
            parts.append(line(x, y, px, py, GRAY, 1.2, dash="3,3"))
            parts.append(circle(px, py, pr, color, 0.5, stroke="#fff", stroke_width=1.5))
    # legend, below the plot so it never competes with a high-pass-rate point's label
    ly = top + plot_h + 58
    lx = left
    parts.append(circle(lx, ly - 4, 5, TEAL))
    parts.append(text(lx + 10, ly, "free / local", 10.5, "start", INK))
    parts.append(circle(lx + 95, ly - 4, 5, ORANGE))
    parts.append(text(lx + 105, ly, "priced (size = cost)", 10.5, "start", INK))
    parts.append(circle(lx + 235, ly - 4, 5, GRAY))
    parts.append(text(lx + 245, ly, "price not published here", 10.5, "start", INK))
    open(path, "w").write(svg(w, h, "".join(parts)))


# ---------------------------------------------------------------- table + report

def build_table_md(blind, dev, diff_stats, haiku50, path, post_blind=None, post_diff_stats=None, post_dev=None):
    lines = ["<!-- eval:table:start -->",
             "| Model | Runs | Passed (range) | Simple | Multi-step | Ambiguous | Impossible | Median time | Cost / 100 req |",
             "|---|---|---|---|---|---|---|---|---|"]
    order = ["sonnet50", "haiku", "gemma_on", "mistral14_on", "mistral8_on"]
    names = {"sonnet50": "Claude Sonnet 5 (paid)", "haiku": "Claude Haiku 4.5 (paid)",
             "gemma_on": "`gemma4:12b`, local", "mistral14_on": "Ministral 14B (cloud)",
             "mistral8_on": "Ministral 8B (cloud)"}
    for key in order:
        if key not in blind:
            continue
        st = blind[key]
        d = diff_stats[key]
        runs = "1 x %d" % st["n_results"] if st["reps"] == 1 else "%d" % st["reps"]
        passed = ("%d/%d" % (st["passed"], st["n_results"])) if st["reps"] == 1 else \
            "%.0f-%.0f (mean %.1f)" % (st["minr"] * 100, st["maxr"] * 100, st["mean"] * 100)
        cost = "$%.2f" % st["cost_per_100"] if st["cost_per_100"] is not None else "n/a"
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %.1f s | %s |" % (
            names[key], runs, passed,
            "%d/%d" % tuple(d["simple"]), "%d/%d" % tuple(d["multi-step"]),
            "%d/%d" % tuple(d["ambiguous"]), "%d/%d" % tuple(d["impossible"]),
            st["med_sec"], cost))
    lines.append("")
    if haiku50 is not None:
        okc, n, missing = haiku50
        lines.append("Claude Haiku 4.5 on the same 50 requests as Sonnet: %d/%d.%s" % (
            okc, n, "" if not missing else " (%d ids missing from the Haiku report)" % len(missing)))
    lines.append("")
    for on_key, off_key, label in (("gemma_on", "gemma_off", "`gemma4:12b`"), ("mistral8_on", "mistral8_off", "Ministral 8B")):
        if on_key in blind and off_key in blind:
            son, soff = blind[on_key], blind[off_key]
            delta = (son["rate"] - soff["rate"]) * 100
            lines.append("Guards off vs on, blind set: %s %d -> %d of %d (%+.0f points)." % (
                label, soff["passed"], son["passed"], son["n_results"], delta))
    if dev:
        lines.append("")
        for on_key, off_key, label in (("gemma_dev_on", "gemma_dev_off", "`gemma4:12b`"),
                                        ("mistral8_dev_on", "mistral8_dev_off", "Ministral 8B")):
            if on_key in dev and off_key in dev:
                son, soff = dev[on_key], dev[off_key]
                delta = (son["rate"] - soff["rate"]) * 100
                lines.append("Dev set (152 x 3), guards off vs on: %s %d -> %d of %d (%+.0f points)." % (
                    label, soff["passed"], son["passed"], son["n_results"], delta))
    if post_blind:
        lines += ["", "After the corrections (same set, 3 repeats):", "",
                  "| Model | Before | After | Simple | Multi-step | Ambiguous | Impossible | Scope-only failures |",
                  "|---|---|---|---|---|---|---|---|"]
        for key in order:
            if key in post_blind and key in blind:
                st, pst, d = blind[key], post_blind[key], post_diff_stats[key]
                lines.append("| %s | %.1f | %.1f (%.0f-%.0f) | %d/%d | %d/%d | %d/%d | %d/%d | %d -> %d |" % (
                    names[key], st["mean"] * 100, pst["mean"] * 100, pst["minr"] * 100, pst["maxr"] * 100,
                    d["simple"][0], d["simple"][1], d["multi-step"][0], d["multi-step"][1],
                    d["ambiguous"][0], d["ambiguous"][1], d["impossible"][0], d["impossible"][1],
                    st.get("scope_fails", 0), pst.get("scope_fails", 0)))
        for on_key, off_key, label in (("gemma_on", "gemma_off", "`gemma4:12b`"),):
            if on_key in post_blind and off_key in post_blind:
                son, soff = post_blind[on_key], post_blind[off_key]
                lines.append("")
                lines.append("Guards off vs on after the corrections, blind set: %s %d -> %d of %d (%+.0f points)." % (
                    label, soff["passed"], son["passed"], son["n_results"], (son["rate"] - soff["rate"]) * 100))
        if post_dev:
            lines.append("")
            for key, label in (("gemma_dev_on", "`gemma4:12b`"), ("mistral8_dev_on", "Ministral 8B")):
                if key in dev and key in post_dev:
                    lines.append("Dev set (152 x 3) before -> after: %s %d -> %d of %d." % (
                        label, dev[key]["passed"], post_dev[key]["passed"], dev[key]["n_results"]))
    lines.append("<!-- eval:table:end -->")
    open(path, "w").write("\n".join(lines) + "\n")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reports_dir")
    ap.add_argument("--post", default=None)
    args = ap.parse_args()

    raw = load_dir(args.reports_dir)
    raw_post = load_dir(args.post) if args.post else {}
    if args.post and not raw_post:
        print("note: --post %s has none of the expected report filenames yet; continuing without a post-fix overlay." % args.post,
              file=sys.stderr)

    missing = [fname for _, fname, *_ in BLIND_FILES + DEV_FILES if fname not in raw]
    if missing:
        print("note: not found in %s, skipping: %s" % (args.reports_dir, ", ".join(missing)), file=sys.stderr)

    meta = load_json(BLIND_META)
    diff_of = {m["id"]: m["difficulty"] for m in meta}

    blind = {}
    blind_reports = {}
    for key, fname, disp, tag, headline in BLIND_FILES:
        if fname in raw:
            blind[key] = stats(raw[fname])
            blind_reports[key] = raw[fname]
    dev = {}
    for key, fname, disp, tag in DEV_FILES:
        if fname in raw:
            dev[key] = stats(raw[fname])

    post_blind = {}
    post_blind_reports = {}
    for key, fname, disp, tag, headline in BLIND_FILES:
        if fname in raw_post:
            post_blind[key] = stats(raw_post[fname])
            post_blind_reports[key] = raw_post[fname]

    diff_stats = {}
    for key in blind_reports:
        diff_stats[key] = by_difficulty(blind_reports[key], diff_of)
    post_diff_stats = {key: by_difficulty(rep, diff_of) for key, rep in post_blind_reports.items()}

    haiku50 = None
    if "haiku" in blind_reports and "sonnet50" in blind_reports:
        haiku50 = haiku_on_sonnet_ids(blind_reports["haiku"], blind_reports["sonnet50"])

    os.makedirs(OUT_DIR, exist_ok=True)

    # -- scores.svg: headline (guards-on / only) config per model, sorted by mean pass rate desc.
    headline_keys = [k for k, fn, disp, tag, hl in BLIND_FILES if hl and k in blind]
    headline_keys.sort(key=lambda k: blind[k]["mean"], reverse=True)
    disp_of = {k: disp for k, fn, disp, tag, hl in BLIND_FILES}
    tag_of = {k: tag for k, fn, disp, tag, hl in BLIND_FILES}
    rows = [(disp_of[k], tag_of[k], blind[k]["mean"], blind[k]["minr"], blind[k]["maxr"], blind[k]["reps"]) for k in headline_keys]
    post_rows = None
    if post_blind:
        post_rows = {}
        for i, k in enumerate(headline_keys):
            if k in post_blind:
                st = post_blind[k]
                post_rows[i] = (st["mean"], st["minr"], st["maxr"], st["reps"])
    chart_scores(rows, post_rows, os.path.join(OUT_DIR, "scores.svg"))

    # -- categories.svg: same headline models, in the same order.
    panels = [(disp_of[k], diff_stats[k]) for k in headline_keys]
    post_panels = None
    if post_diff_stats:
        post_panels = {disp_of[k]: post_diff_stats[k] for k in headline_keys if k in post_diff_stats}
    chart_categories(panels, post_panels, os.path.join(OUT_DIR, "categories.svg"))

    # -- speed_cost.svg
    def color_for(key):
        tag = tag_of[key]
        if "free" in tag:
            return TEAL
        if "paid" in tag:
            return ORANGE
        return GRAY  # cloud but no published price in this comparison

    points = [(disp_of[k], blind[k]["med_sec"], blind[k]["mean"], blind[k]["cost_per_100"], color_for(k)) for k in headline_keys]
    post_points = None
    if post_blind:
        post_points = {}
        for k in headline_keys:
            if k in post_blind:
                st = post_blind[k]
                post_points[disp_of[k]] = (st["med_sec"], st["mean"], st["cost_per_100"])
    chart_speed_cost(points, post_points, os.path.join(OUT_DIR, "speed_cost.svg"))

    # -- table.md
    post_dev = {}
    for key, fname, disp, tag in DEV_FILES:
        if fname in raw_post:
            post_dev[key] = stats(raw_post[fname])
    table_txt = build_table_md(blind, dev, diff_stats, haiku50, os.path.join(OUT_DIR, "table.md"),
                               post_blind=post_blind, post_diff_stats=post_diff_stats, post_dev=post_dev)

    print("wrote docs/img/eval/{scores,categories,speed_cost}.svg and table.md")
    print()
    print(table_txt)
    if post_blind:
        print("\npost-fix overlay included for: %s" % ", ".join(sorted(post_blind)))


if __name__ == "__main__":
    main()
