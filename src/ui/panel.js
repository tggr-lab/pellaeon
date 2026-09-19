/* Pellaeon panel logic. No frameworks, no network: talks to Python through
   pellaeon:<action>?params navigations (in a hidden iframe) and receives
   messages via Pellaeon.push(obj). */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  let seq = 0;
  const state = {presets: [], settings: {}, keyMasked: "", keySource: "", busy: false, turns: {}, configured: false, config: {}};

  // ---------------------------------------------------------------- bridge
  function send(action, params, payload) {
    const q = new URLSearchParams(params || {});
    if (payload !== undefined) q.set("payload", JSON.stringify(payload));
    q.set("_", String(++seq));
    if (window.PELLAEON_HTTP) {
      // classic edition: the panel runs in a normal browser and talks to a local server (token-authenticated)
      fetch("/act/" + action + "?" + q.toString(), {method: "POST", headers: {"X-Pellaeon-Token": window.PELLAEON_TOKEN || ""}})
        .then((r) => { if (!r.ok) toast("Pellaeon refused the request (" + r.status + ")", "error"); })
        .catch((e) => toast("Lost connection to Pellaeon: " + e.message, "error"));
      return;
    }
    $("beacon").src = "pellaeon:" + action + "?" + q.toString();
  }
  if (window.PELLAEON_HTTP) {
    const es = new EventSource("/events?token=" + encodeURIComponent(window.PELLAEON_TOKEN || ""));
    es.onmessage = (e) => { try { window.Pellaeon.push(JSON.parse(e.data)); } catch (err) { console.error(err); } };
    // say "ready" only once the event stream is open, otherwise the server's replies to it are lost
    es.onopen = () => send("ready");
    es.onerror = () => { $("status").textContent = "Reconnecting…"; };
  }

  // ---------------------------------------------------------------- helpers
  function toast(text, kind) {
    const el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = text;
    $("toasts").appendChild(el);
    requestAnimationFrame(() => el.classList.add("show"));
    setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 250); }, 2600);
  }
  function showPage(name) {
    document.querySelectorAll(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + name));
    if (name === "chat") setTimeout(() => $("input").focus(), 50);
    if (name === "settings") fillSettings();
    if (name === "history") send("history");
  }
  function scrollDown(force) {
    const t = $("transcript");
    const nearBottom = t.scrollHeight - t.scrollTop - t.clientHeight < 120;
    if (force || nearBottom) t.scrollTop = t.scrollHeight;
  }
  function setBusy(b) {
    state.busy = b;
    const btn = $("send");
    btn.textContent = b ? "Stop" : "Send";
    btn.classList.toggle("stop", b);
    if (!b) { $("status").textContent = ""; }
  }

  // ---------------------------------------------------------------- transcript
  function userMessage(id, text) {
    $("welcome") && $("welcome").remove();
    const el = document.createElement("div");
    el.className = "msg user";
    el.textContent = text;
    $("transcript").appendChild(el);
    scrollDown(true);
  }
  function assistantStart(id) {
    const el = document.createElement("div");
    el.className = "msg assistant";
    el.dataset.id = id;
    el.innerHTML = '<div class="thinking" title="working"><i></i><i></i><i></i></div>';
    $("transcript").appendChild(el);
    state.turns[id] = {el: el, text: "", textEl: null, tools: {}};
    scrollDown();
    return el;
  }
  function turn(id) { return state.turns[id] || (state.turns[id] = {el: assistantStart(id), text: "", textEl: null, tools: {}}); }
  function ensureTextEl(t) {
    // a new text element after any tool blocks, so order is preserved
    if (!t.textEl || t.textEl !== t.el.lastElementChild) {
      t.textEl = document.createElement("div");
      t.textEl.className = "text";
      t.el.appendChild(t.textEl);
      t.segText = "";
    }
    t.lastTextEl = t.textEl;
    return t.textEl;
  }
  function assistantDelta(id, text) {
    const t = turn(id);
    const el = ensureTextEl(t);
    t.segText = (t.segText || "") + text;
    el.textContent = t.segText;
    const th = t.el.querySelector(".thinking");
    if (th) th.remove();
    scrollDown();
  }
  function assistantDone(id, html, error, askedUser) {
    const t = turn(id);
    const th = t.el.querySelector(".thinking");
    if (th) th.remove();
    // replace the last streamed text segment with the rendered version of the final reply
    if (html && error !== "cancelled") {
      if (t.lastTextEl) {
        t.lastTextEl.className = "text rendered";
        t.lastTextEl.innerHTML = html;
      } else {
        const el = document.createElement("div");
        el.className = "text rendered";
        el.innerHTML = html;
        t.el.appendChild(el);
      }
    }
    if (error && error !== "cancelled") t.el.classList.add("error");
    if (error === "cancelled") {
      const el = document.createElement("div");
      el.className = "text muted";
      el.textContent = "Stopped.";
      t.el.appendChild(el);
    }
    t.textEl = null;
    scrollDown();
  }

  const TOOL_LABELS = {run_commands: "Running commands", get_state: "Checking what is open", command_usage: "Looking up command syntax",
    search_docs: "Searching the docs", resolve_protein: "Looking up the protein in UniProt", protein_features: "Fetching UniProt annotations",
    run_python: "Running Python", look_at_view: "Looking at the view", ask_user: "Asking you"};

  function toolStart(id, callId, name, args) {
    const t = turn(id);
    const th = t.el.querySelector(".thinking");
    if (th) th.remove();
    let el;
    if (name === "run_commands") {
      el = document.createElement("details");
      el.className = "tool";
      el.open = true;
      let cmds = (args && args.commands) || [];
      if (!Array.isArray(cmds)) cmds = [String(cmds)];
      el.innerHTML = '<summary><span class="dot run"></span><span class="label">Running ' + cmds.length + (cmds.length === 1 ? " command" : " commands") + "…</span></summary>" +
        '<div class="cmds">' + cmds.map((c) => cmdRow(c, "run")).join("") + "</div>";
    } else {
      el = document.createElement("div");
      el.className = "tool";
      el.innerHTML = '<div class="head"><span class="dot run"></span><span class="label">' + esc(TOOL_LABELS[name] || name) + "…</span></div>";
    }
    el.dataset.call = callId;
    t.el.appendChild(el);
    t.tools[callId] = el;
    t.textEl = null;
    scrollDown();
  }
  function cmdRow(cmd, status, error, info) {
    const word = (String(cmd).trim().replace(/^~/, "").match(/^[A-Za-z][A-Za-z0-9_]*/) || [""])[0].toLowerCase();
    return '<div class="cmd"><span class="dot ' + status + '"></span><code>' + esc(cmd) + '</code>' +
      '<span class="actions"><button title="Copy" data-copy="' + esc(cmd) + '">copy</button><button title="Run again" data-rerun="' + esc(cmd) + '">rerun</button>' +
      (word ? '<button title="ChimeraX documentation for ' + esc(word) + '" data-help="' + esc(word) + '">?</button>' : "") + '</span></div>' +
      (error ? '<div class="cmd-err">' + esc(error) + "</div>" : "") +
      (info && info.length ? '<div class="cmd-info">' + esc(info.join("\n")) + "</div>" : "");
  }
  function toolResult(msg) {
    const t = turn(msg.id);
    const el = t.tools[msg.call_id];
    if (!el) return;
    if (msg.name === "run_commands") {
      const results = msg.results || [];
      const nOk = results.filter((r) => r.ok).length;
      const label = el.querySelector(".label");
      const dot = el.querySelector("summary .dot");
      if (msg.skipped) {
        label.textContent = "Commands skipped";
        dot.className = "dot skip";
      } else if (msg.ok) {
        label.textContent = "Ran " + nOk + (nOk === 1 ? " command" : " commands");
        dot.className = "dot ok";
        el.open = false;
      } else {
        label.textContent = "Command failed (" + nOk + " ok)";
        dot.className = "dot err";
        el.open = true;
      }
      const box = el.querySelector(".cmds");
      const rows = [];
      results.forEach((r) => rows.push(cmdRow(r.command, r.ok ? "ok" : "err", r.ok ? "" : r.error, r.ok ? (r.info || []).slice(0, 6) : [])));
      (msg.not_run || []).forEach((c) => rows.push(cmdRow(c, "skip", "", ["not run"])));
      if (msg.skipped) {
        // keep the planned commands visible but mark them skipped
        el.querySelectorAll(".cmd .dot").forEach((d) => (d.className = "dot skip"));
      } else {
        box.innerHTML = rows.join("");
      }
    } else {
      const dot = el.querySelector(".dot");
      dot.className = "dot " + (msg.ok ? "ok" : "err");
      el.querySelector(".label").textContent = msg.summary || (TOOL_LABELS[msg.name] || msg.name);
      if (msg.card) el.appendChild(resultCard(msg.name, msg.card, msg.results || []));
    }
    scrollDown();
  }
  function viewLink(spec, text) {
    return '<a href="#" class="spec" data-view="' + esc(spec) + '">' + esc(text || spec) + "</a>";
  }
  function resultCard(name, c, results) {
    const box = document.createElement("div");
    box.className = "rcard";
    let h = "";
    if (c.error) {
      h = '<div class="rrow err">' + esc(c.error) + "</div>";
    } else if (name === "compare_structures") {
      h += '<div class="rrow"><b>' + esc(c.compared) + "</b> superposed onto <b>" + esc(c.reference) + "</b>" + (c.chain && c.chain !== "all" ? " (chain " + esc(c.chain) + ")" : "") + "</div>";
      if (c.fit_rmsd != null) {
        h += '<div class="rrow"><b>Fit</b> (MatchMaker, CA atoms): RMSD <b>' + c.fit_rmsd + " Å</b> over the " + c.fit_pairs + " pairs kept after pruning" +
          (c.all_rmsd != null ? "; across all " + c.all_pairs + " aligned pairs: " + c.all_rmsd + " Å" : "") + "</div>";
      } else if (c.rmsd) h += '<div class="rrow muted">' + esc(c.rmsd) + "</div>";
      if (c.paired_residues != null) {
        h += '<div class="rrow"><b>Per-residue displacement</b> (CA to CA after the fit, ' + c.paired_residues + " paired residues, " + esc(c.pairing || "") + "): mean <b>" + c.mean_displacement + " Å</b> · max <b>" + c.max_displacement + " Å</b> · " + c.residues_over_2A + " residues moved over 2 Å" + (c.coverage ? " · " + esc(c.coverage) : "") + "</div>";
        if (c.pairing_fallback) h += '<div class="rrow warn">' + esc(c.pairing_note || "Paired by residue number, not by alignment.") + "</div>";
        if ((c.moving_regions || []).length) h += '<div class="rrow">Moving regions: ' + c.moving_regions.map((r) => viewLink(r.spec, r.chain + ":" + r.range + " (" + r.max + " Å)")).join(", ") + "</div>";
        if (c.coloring) h += '<div class="rrow muted">' + esc(c.coloring) + "</div>";
      }
      if (c.note) h += '<div class="rrow muted">' + esc(c.note) + "</div>";
    } else if (name === "table_overlay") {
      h += '<div class="rrow"><b>' + esc(c.column) + "</b> from table <b>" + esc(c.dataset) + "</b> on " + esc(c.model) + " · " + esc(c.numbering || "") + "</div>";
      if (c.mapped != null) h += '<div class="rrow">' + c.mapped + " residues placed (chains " + esc((c.chains || []).join(", ")) + ") · " + (c.n_missing || 0) + " table positions not in the structure" + (c.n_mismatches ? " · <b>" + c.n_mismatches + " reference-residue mismatches skipped</b>" : "") + "</div>";
      if (c.legend) h += '<div class="rrow muted">' + esc(c.legend) + "</div>";
      if ((c.missing || []).length) h += '<div class="rrow muted">Not found: ' + esc(c.missing.slice(0, 30).join(", ")) + (c.n_missing > 30 ? " …" : "") + "</div>";
      if ((c.mismatches || []).length) h += '<div class="rrow warn">' + esc(c.mismatches.slice(0, 6).join("; ")) + (c.n_mismatches > 6 ? " …" : "") + "</div>";
      if (c.note) h += '<div class="rrow muted">' + esc(c.note) + "</div>";
    } else if (name === "annotate") {
      h += '<div class="rrow"><b>' + esc(c.kind || "") + "</b> · " + esc(c.source || "") + "</div>";
      if (c.message) h += '<div class="rrow muted">' + esc(c.message) + "</div>";
      if (c.mapped != null) h += '<div class="rrow">' + c.mapped + " mapped, " + c.unmapped + " unmapped" + (c.reference_mismatch ? ", <b>" + c.reference_mismatch + " reference-residue mismatches skipped</b>" : "") + " · chains " + esc((c.chains || []).join(", ")) + (c.labeled ? " · " + c.labeled + " labels" : "") + "</div>";
      if (c.legend) h += '<div class="rrow muted">' + esc(c.legend) + "</div>";
      if (c.mapping_note) h += '<div class="rrow warn">' + esc(c.mapping_note) + "</div>";
      if ((c.pathogenic || []).length) h += '<div class="rrow">Pathogenic: ' + c.pathogenic.slice(0, 20).map((p) => { const m = String(p).match(/(\d+)/); return m ? viewLink((c.model || "#1") + ":" + m[1], p) : esc(p); }).join(", ") + (c.pathogenic.length > 20 ? " …" : "") + "</div>";
      if ((c.annotations || []).length && !(c.pathogenic || []).length) h += '<div class="rrow muted">' + c.annotations.slice(0, 8).map((a) => esc(a.type + " " + a.residues + (a.description ? ": " + a.description.slice(0, 40) : ""))).join(" · ") + (c.annotations.length > 8 ? " …" : "") + "</div>";
      if (c.commands_failed) h += '<div class="rrow err">' + c.commands_failed + " command(s) failed</div>";
      if (c.note) h += '<div class="rrow muted">' + esc(c.note) + "</div>";
    }
    if (results.length) {
      h += '<details class="tool inner"><summary><span class="dot ' + (results.every((r) => r.ok) ? "ok" : "err") + '"></span><span class="label">' + results.length + " command" + (results.length === 1 ? "" : "s") + ' run</span></summary><div class="cmds">' +
        results.map((r) => cmdRow(r.command, r.ok ? "ok" : "err", r.ok ? "" : r.error, [])).join("") + "</div></details>";
    }
    box.innerHTML = h;
    return box;
  }
  function confirmCard(msg) {
    const t = turn(msg.id);
    const card = document.createElement("div");
    card.className = "card";
    const why = msg.reasons.map((r, i) => (r ? "• " + msg.commands[i] + " — " + r : "")).filter(Boolean).join("\n");
    card.dataset.confirm = msg.confirm_id;
    card.innerHTML = "<b>Pellaeon wants to run these commands. OK?</b>" +
      (why ? '<div class="why">' + esc(why) + "</div>" : "") +
      "<textarea>" + esc(msg.commands.join("\n")) + "</textarea>" +
      '<div class="btns"><button class="secondary skip">Skip</button><button class="primary run">Run</button></div>';
    const finish = (decision) => {
      const raw = card.querySelector("textarea").value;
      const lines = msg.python ? [raw] : raw.split("\n").map((s) => s.trim()).filter(Boolean);
      send("confirm", {confirm_id: msg.confirm_id, decision: decision}, lines);
      card.classList.add("done");
      card.querySelectorAll("button, textarea").forEach((b) => (b.disabled = true));
    };
    card.querySelector(".run").onclick = () => finish("run");
    card.querySelector(".skip").onclick = () => finish("skip");
    t.el.appendChild(card);
    t.textEl = null;
    scrollDown();
  }
  function questionCard(msg) {
    const t = turn(msg.id);
    const card = document.createElement("div");
    card.className = "card question";
    card.innerHTML = "<b>" + esc(msg.question) + "</b>" +
      ((msg.options || []).length ? '<div class="opts">' + msg.options.map((o) => '<button class="secondary" data-answer="' + esc(o) + '">' + esc(o) + "</button>").join("") + "</div>" : "") +
      '<div class="muted small">…or just type your answer below.</div>';
    card.querySelectorAll("[data-answer]").forEach((b) => (b.onclick = () => { submit(b.dataset.answer); card.classList.add("done"); }));
    t.el.appendChild(card);
    t.textEl = null;
    scrollDown();
  }
  function rerunResult(msg) {
    const results = msg.results || [];
    if (msg.skipped) { toast("Skipped", "warn"); return; }
    if (!results.length && msg.error) { toast(msg.error, "error"); return; }
    const el = document.createElement("div");
    el.className = "msg assistant";
    el.innerHTML = '<details class="tool" open><summary><span class="dot ' + (results.every((r) => r.ok) ? "ok" : "err") + '"></span><span class="label">Re-ran ' + results.length + " command" + (results.length === 1 ? "" : "s") + '</span></summary><div class="cmds">' +
      results.map((r) => cmdRow(r.command, r.ok ? "ok" : "err", r.ok ? "" : r.error, r.ok ? (r.info || []).slice(0, 4) : [])).join("") + "</div></details>";
    $("welcome") && $("welcome").remove();
    $("transcript").appendChild(el);
    scrollDown(true);
  }

  // ---------------------------------------------------------------- scene strip
  function renderScene(st) {
    const el = $("scene");
    if (!st || st.error) { el.textContent = ""; return; }
    const models = st.models || [];
    if (!models.length) { el.innerHTML = "<b>Nothing open</b> — the <i>Chimaera</i> awaits orders. Try: open 4hhb"; return; }
    const parts = models.slice(0, 6).map((m) => "<b>" + esc(m.id) + "</b> " + esc(m.name) +
      (m.chains && m.chains.length ? " (" + m.chains.slice(0, 6).map((c) => esc(c.id)).join(",") + (m.chains.length > 6 ? "…" : "") + ")" : ""));
    let s = parts.join(" · ");
    if (models.length > 6) s += " · +" + (models.length - 6) + " more";
    const sel = st.selection || {};
    if (sel.num_atoms) s += " · selected: " + sel.num_residues + " res";
    el.innerHTML = s;
  }

  // ---------------------------------------------------------------- settings page
  function presetById(id) { return state.presets.find((p) => p.id === id); }
  function renderPresets(selected) {
    const box = $("presets");
    box.innerHTML = state.presets.map((p) => '<div class="preset' + (p.id === selected ? " selected" : "") + '" data-id="' + p.id + '">' +
      '<div class="name">' + esc(p.label) + "</div>" + (p.free ? '<div class="tag">free option</div>' : "") +
      '<div class="blurb">' + esc(p.blurb) + "</div></div>").join("");
    box.querySelectorAll(".preset").forEach((el) => (el.onclick = () => selectPreset(el.dataset.id, true)));
  }
  function selectPreset(id, userClicked) {
    const p = presetById(id);
    if (!p) return;
    document.querySelectorAll(".preset").forEach((el) => el.classList.toggle("selected", el.dataset.id === id));
    $("presets").dataset.selected = id;
    const isOllama = p.provider === "ollama";
    $("f-key").hidden = !p.needs_key;
    $("f-url").hidden = !(isOllama || p.id === "lmstudio");
    $("f-pull").hidden = !isOllama;
    $("f-think").hidden = !isOllama;
    $("f-effort").hidden = p.provider !== "anthropic";
    $("key-link").href = p.key_url || "#";
    $("key-link").textContent = p.needs_key ? "get a key ↗" : "";
    if (userClicked || !$("s-model").value) {
      $("s-model").value = (id === state.settings.preset && state.settings.model) ? state.settings.model : (p.model || "");
      $("s-url").value = (id === state.settings.preset && state.settings.base_url) ? state.settings.base_url : (p.base_url || "");
    }
    if (userClicked) {
      $("s-key").value = "";
      $("s-key").placeholder = "paste your key";
      $("key-note").textContent = id === state.settings.preset && state.keyMasked ? "Saved key: " + state.keyMasked + " (" + state.keySource + "). Leave empty to keep it." : "";
      $("s-key").dataset.unchanged = id === state.settings.preset && state.keyMasked ? "1" : "";
    }
    $("model-hints").innerHTML = (p.models_hint || []).map((m) => '<span class="pill" data-model="' + esc(m) + '">' + esc(m) + "</span>").join("");
    $("model-hints").querySelectorAll(".pill").forEach((el) => (el.onclick = () => ($("s-model").value = el.dataset.model)));
    $("s-pull").value = p.model || "";
    $("test-result").textContent = "";
    $("test-result").className = "hints";
    if (p.provider === "ollama") { $("test-result").textContent = "Checking for Ollama…"; send("settings_test", {}, settingsPayload()); }
  }
  function fillSettings() {
    const s = state.settings;
    renderPresets(s.preset);
    selectPreset(s.preset || "ollama", false);
    $("s-model").value = s.model || (presetById(s.preset) || {}).model || "";
    $("s-url").value = s.base_url || (presetById(s.preset) || {}).base_url || "";
    $("s-key").value = "";
    $("s-key").dataset.clear = "";
    $("s-key").dataset.unchanged = state.keyMasked ? "1" : "";
    $("key-note").textContent = state.keyMasked ? "Saved key: " + state.keyMasked + " (" + state.keySource + "). Leave empty to keep it." : "";
    $("s-autonomy").value = s.autonomy || "auto";
    $("s-python").checked = !!s.allow_python;
    $("s-vision").checked = !!s.vision;
    $("s-think").checked = !!s.think;
    $("s-effort").value = s.effort || "";
    $("s-temp").value = s.temperature != null ? s.temperature : 0.2;
  }
  function settingsPayload() {
    const key = $("s-key").value.trim();
    return {
      preset: $("presets").dataset.selected || "ollama",
      model: $("s-model").value.trim(),
      base_url: $("s-url").value.trim(),
      api_key: key,
      autonomy: $("s-autonomy").value,
      allow_python: $("s-python").checked,
      vision: $("s-vision").checked,
      think: $("s-think").checked,
      effort: $("s-effort").value,
      temperature: isNaN(parseFloat($("s-temp").value)) ? 0.2 : parseFloat($("s-temp").value),
      clear_key: !!$("s-key").dataset.clear,
    };
  }

  // ---------------------------------------------------------------- history page
  function renderHistory(items) {
    const box = $("history-list");
    if (!items.length) { box.innerHTML = '<p class="muted">No saved conversations yet.</p>'; return; }
    box.innerHTML = items.map((c) => '<div class="item" data-id="' + esc(c.id) + '"><span class="t">' + esc(c.title || c.id) + '</span><span class="d">' +
      new Date((c.updated || 0) * 1000).toLocaleString() + '</span><button class="x" title="Delete" data-del="' + esc(c.id) + '">✕</button></div>').join("");
    box.querySelectorAll(".item").forEach((el) => (el.onclick = (e) => {
      if (e.target.dataset.del) { send("delete_chat", {id: e.target.dataset.del}); e.stopPropagation(); return; }
      send("load_chat", {id: el.dataset.id});
    }));
  }
  function loadConversation(msg) {
    const tr = $("transcript");
    tr.innerHTML = "";
    Object.keys(state.turns).forEach((k) => delete state.turns[k]);
    (msg.messages || []).forEach((m, i) => {
      if (m.role === "user") userMessage("h" + i, m.text);
      else {
        const el = document.createElement("div");
        el.className = "msg assistant";
        let html = "";
        (m.calls || []).forEach((c) => {
          if (c.name === "run_commands") {
            let cmds = (c.args && c.args.commands) || [];
            if (!Array.isArray(cmds)) cmds = [String(cmds)];
            const res = c.results || [];
            let rows, label, dot;
            if (c.skipped) { rows = cmds.map((x) => cmdRow(x, "skip", "", ["skipped by you"])); label = "Commands skipped"; dot = "skip"; }
            else if (res.length) {
              rows = res.map((r) => cmdRow(r.command, r.ok ? "ok" : "err", r.ok ? "" : r.error, []));
              (c.not_run || []).forEach((x) => rows.push(cmdRow(x, "skip", "", ["not run"])));
              const nOk = res.filter((r) => r.ok).length;
              label = nOk === res.length && !(c.not_run || []).length ? "Ran " + nOk + (nOk === 1 ? " command" : " commands") : "Command failed (" + nOk + " ok)";
              dot = nOk === res.length ? "ok" : "err";
            } else { rows = cmds.map((x) => cmdRow(x, c.ok === false ? "err" : "skip", "", ["no record of execution"])); label = "Proposed " + cmds.length + " commands"; dot = "skip"; }
            html += '<details class="tool"><summary><span class="dot ' + dot + '"></span><span class="label">' + esc(label) + '</span></summary><div class="cmds">' + rows.join("") + "</div></details>";
          } else {
            const ok = c.ok !== false;
            html += '<div class="tool" data-card-idx="' + (c.card ? "1" : "") + '"><div class="head"><span class="dot ' + (ok ? "ok" : "err") + '"></span><span class="label">' + esc(c.summary || TOOL_LABELS[c.name] || c.name) + "</span></div></div>";
          }
        });
        if (m.html) html += '<div class="text rendered">' + m.html + "</div>";
        el.innerHTML = html;
        // attach result cards to their tool blocks
        const tools = el.querySelectorAll(".tool");
        let ti = 0;
        (m.calls || []).forEach((c) => { const t = tools[ti++]; if (t && c.card && c.name !== "run_commands") t.appendChild(resultCard(c.name, c.card, [])); });
        tr.appendChild(el);
      }
    });
    showPage("chat");
    scrollDown();
  }

  // ---------------------------------------------------------------- tables (bring your own data)
  const PALETTES = ["blue-white-red", "white-red", "blue-white", "viridis", "rainbow", "gray-orange-red", "green-white-magenta"];
  function hideWelcome() { const w = $("welcome"); if (w) w.hidden = true; }
  function tablePreview(m) {
    const el = document.createElement("div"); el.className = "msg assistant";
    const box = document.createElement("div"); box.className = "rcard tcard";
    const colOpts = m.values.concat(m.categories).map((i) => '<option value="' + esc(m.columns[i]) + '"' + (i === m.value ? " selected" : "") + ">" + esc(m.columns[i]) + (m.kinds[i] === "text" ? " (categories)" : "") + "</option>").join("");
    const modelOpts = (m.models.length ? m.models : ["#1"]).map((id) => '<option value="' + esc(id) + '">' + esc(id) + "</option>").join("");
    let h = '<div class="rrow"><b>Table loaded: ' + esc(m.dataset) + "</b> · " + m.rows + " rows · " + esc(m.delimiter) + "-separated</div>";
    h += '<div class="rrow muted">Position column: <b>' + esc(m.columns[m.position]) + "</b>" +
      (m.reference != null ? " · reference residue: <b>" + esc(m.columns[m.reference]) + "</b> (checked against the structure)" : " · no reference-residue column (numbering cannot be verified)") +
      (m.chain_col != null ? " · chain column: <b>" + esc(m.columns[m.chain_col]) + "</b>" : "") + (m.accession_col != null ? " · accession column: <b>" + esc(m.columns[m.accession_col]) + "</b>" : "") + "</div>";
    h += '<div class="rrow form">Color by <select data-col>' + colOpts + "</select> on <select data-model>" + modelOpts + '</select> chain <input data-chain size="2" placeholder="all"> numbering <select data-num><option value="">structure</option><option value="uniprot">UniProt</option></select><input data-acc placeholder="accession, e.g. P0DTC2" hidden> palette <select data-pal>' + PALETTES.map((p) => "<option>" + p + "</option>").join("") + '</select> <label class="check"><input type="checkbox" data-label> labels</label> <button class="secondary small" data-apply>Apply</button></div>';
    h += '<table class="sample"><tr>' + m.columns.map((c) => "<th>" + esc(c) + "</th>").join("") + "</tr>" + m.sample.map((r) => "<tr>" + r.map((c) => "<td>" + esc(c) + "</td>").join("") + "</tr>").join("") + "</table>";
    box.innerHTML = h;
    const num = box.querySelector("[data-num]"), acc = box.querySelector("[data-acc]");
    num.onchange = () => { acc.hidden = num.value !== "uniprot"; };
    box.querySelector("[data-apply]").onclick = () => {
      send("table_apply", {dataset: m.dataset, column: box.querySelector("[data-col]").value, model: box.querySelector("[data-model]").value,
        chain: box.querySelector("[data-chain]").value.trim(), palette: box.querySelector("[data-pal]").value,
        accession: num.value === "uniprot" ? acc.value.trim() : "", label: box.querySelector("[data-label]").checked ? "1" : "0"});
    };
    el.appendChild(box); $("transcript").appendChild(el); hideWelcome(); scrollDown(true);
  }
  function renderLayers(layers) {
    const bar = $("layers");
    bar.innerHTML = "";
    if (!layers.length) { bar.hidden = true; return; }
    bar.appendChild(document.createTextNode("Layers: "));
    layers.forEach((l) => {
      const b = document.createElement("button"); b.className = "chip"; b.textContent = l.dataset + " · " + l.column + " (" + l.mapped + ")";
      b.title = "Re-apply this overlay: " + (l.legend || "");
      b.onclick = () => send("table_apply", {dataset: l.dataset, column: l.column, model: l.model, chain: l.chain || "", palette: l.palette || "", accession: l.accession || "", label: "0"});
      bar.appendChild(b);
      const x = document.createElement("button"); x.className = "chip x"; x.textContent = "×"; x.title = "Forget this table";
      x.onclick = () => send("table_remove", {dataset: l.dataset});
      bar.appendChild(x);
    });
    bar.hidden = false;
  }

  // ---------------------------------------------------------------- selection bar
  let currentSel = null;
  function showSelection(m) {
    const bar = $("selbar");
    if (!m.spec) { bar.hidden = true; currentSel = null; return; }
    currentSel = m;
    const what = m.n === 1 ? (m.name + " " + m.number + " (chain " + m.chain + ", " + m.model + ")") : m.n + " residues: " + m.spec;
    $("sel-text").innerHTML = "Selected: <b>" + esc(what) + "</b>";
    bar.hidden = false;
  }
  function describeSel() {
    if (!currentSel) return "the selection";
    return currentSel.n === 1 ? "residue " + currentSel.number + " (" + currentSel.name + ") of chain " + currentSel.chain + " in " + currentSel.model : "the selected residues " + currentSel.spec;
  }

  // ---------------------------------------------------------------- input
  function submit(text) {
    text = (text || "").trim();
    if (!text) return;
    if (/artistically done/i.test(text)) { toast("Thrawn would approve.", "ok"); $("input").value = ""; return; }
    if (state.busy) { toast("Still working — press Stop first", "warn"); return; }
    if (!state.configured) { showPage("settings"); toast("Choose an AI provider first", "warn"); return; }
    send("send", {text: text});
    $("input").value = "";
    autosize();
  }
  function autosize() {
    const ta = $("input");
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  }

  // ---------------------------------------------------------------- incoming
  const handlers = {
    init(m) {
      state.edition = m.edition || "chimerax";
      if (state.edition === "chimera") {
        document.title = "Pellaeon Classic";
        $("f-chimera").hidden = false;
        $("s-chimera-port").value = m.chimera_port || "";
        $("s-chimera-path").value = m.chimera_path || "";
        $("chip-bind").hidden = true;
        document.querySelectorAll("#chips button[data-text]").forEach((b) => { if (/variants|Domains/.test(b.textContent)) b.hidden = false; });
        $("s-python").parentElement.hidden = true;
        $("btn-export").title = "Export this chat's commands as a Chimera command file (.cmd)";
        const sub = document.querySelector(".welcome .muted"); if (sub) sub.textContent = "Pellaeon Classic drives UCSF Chimera 1.x. Risky commands (close, delete, save…) ask first.";
        const h2 = document.querySelector(".welcome h2"); if (h2) h2.textContent = "Tell Chimera what you want.";
        $("input").placeholder = "What should Chimera do?  (Enter to send, Shift+Enter for a new line)";
      }
      state.presets = m.presets || [];
      state.settings = m.settings || {};
      state.keyMasked = m.key_masked || "";
      state.keySource = m.key_source || "";
      state.configured = !!m.configured;
      state.config = m.config || {};
      updateConfigLine();
      $("autonomy").value = state.settings.autonomy || "auto";
      if (!state.configured) showPage("settings");
    },
    settings(m) {
      state.settings = m.settings || {};
      state.keyMasked = m.key_masked || "";
      state.keySource = m.key_source || "";
      state.configured = !!state.settings.configured;
      $("autonomy").value = state.settings.autonomy || "auto";
    },
    config(m) { state.config = m.config || {}; state.configured = !!state.config.configured; updateConfigLine(); showPage("chat"); },
    show_page(m) { showPage(m.page); },
    state(m) { renderScene(m.state); },
    user_message(m) { userMessage(m.id, m.text); },
    assistant_start(m) { assistantStart(m.id); },
    assistant_delta(m) { assistantDelta(m.id, m.text); },
    assistant_done(m) {
      assistantDone(m.id, m.html, m.error, m.asked_user);
      if (m.usage) {
        const u = m.usage;
        $("usage").textContent = "this turn " + u.input + "→" + u.output + " tokens" + (u.cached ? " (" + u.cached + " cached)" : "") + " · total " + u.total_input + "→" + u.total_output;
      }
    },
    tool_start(m) { toolStart(m.id, m.call_id, m.name, m.args); },
    tool_result(m) { toolResult(m); },
    confirm(m) { confirmCard(m); },
    confirm_done(m) {
      const card = document.querySelector('.card[data-confirm="' + m.confirm_id + '"]');
      if (card) { card.classList.add("done"); card.querySelectorAll("button, textarea").forEach((b) => (b.disabled = true)); }
    },
    question(m) { questionCard(m); },
    status(m) { $("status").textContent = m.text || ""; },
    busy(m) { setBusy(!!m.busy); },
    toast(m) { toast(m.text, m.kind); },
    clear() { $("transcript").innerHTML = WELCOME_HTML; wireWelcome(); Object.keys(state.turns).forEach((k) => delete state.turns[k]); $("usage").textContent = ""; showPage("chat"); },
    rerun_result(m) { rerunResult(m); },
    test_result(m) {
      const el = $("test-result");
      el.textContent = m.text;
      el.className = "hints " + (m.ok ? "ok" : "err");
      if (!m.ok && /not running|Could not reach|Cannot reach/i.test(m.text || "") && ($("presets").dataset.selected === "ollama")) {
        el.innerHTML = esc("Ollama is not installed or not running. ") + '<a href="#" id="dl-ollama">Download Ollama (free)</a>' + esc(", install it, then come back and press Test connection. Prefer no installation? Pick the Gemini card instead.");
        $("dl-ollama").onclick = (e) => { e.preventDefault(); send("open_url", {url: "https://ollama.com/download"}); };
      } else if (m.ok && /no models|not pulled/i.test(m.text || "")) {
        el.innerHTML = esc(m.text + " ") + "Press Pull below to download it.";
      }
      if (m.models && m.models.length) handlers.models_list(m);
    },
    models_list(m) {
      $("model-list").innerHTML = (m.models || []).map((x) => '<option value="' + esc(x) + '">').join("");
      if (m.error) { $("test-result").textContent = m.error; $("test-result").className = "hints err"; }
      else if (m.models && m.models.length) {
        $("model-hints").innerHTML = m.models.slice(0, 12).map((x) => '<span class="pill" data-model="' + esc(x) + '">' + esc(x) + "</span>").join("") + (m.models.length > 12 ? '<span class="muted"> +' + (m.models.length - 12) + " more (type to search)</span>" : "");
        $("model-hints").querySelectorAll(".pill").forEach((el) => (el.onclick = () => ($("s-model").value = el.dataset.model)));
      }
    },
    pull_progress(m) {
      const box = $("pull-progress");
      box.hidden = false;
      const pct = m.total ? Math.round(100 * m.completed / m.total) : (m.done ? 100 : 0);
      box.querySelector(".bar").style.width = pct + "%";
      box.querySelector(".ptext").textContent = m.done ? (m.error ? m.status : "Done — " + m.model + " is ready") : (m.status + (m.total ? " " + pct + "%" : ""));
      if (m.done && !m.error) { $("s-model").value = m.model; toast("Model ready: " + m.model, "ok"); }
    },
    index_progress(m) {
      const el = $("index-result");
      el.textContent = m.finished ? "Docs index ready (" + m.count + " passages)" : "Indexing " + m.done + "/" + m.total + "…";
      el.className = "hints " + (m.finished ? "ok" : "");
    },
    history(m) { renderHistory(m.conversations || []); },
    chimera_status(m) { const el = $("chimera-status"); el.textContent = m.text || ""; el.className = "hints " + (m.ok ? "ok" : "err"); if (m.port) $("s-chimera-port").value = m.port; },
    selection(m) { showSelection(m); },
    picked(m) { showSelection({spec: m.pick.spec, n: 1, name: m.pick.name, number: m.pick.number, chain: m.pick.chain, model: m.pick.model});
                $("input").value = "Tell me about residue " + m.pick.number + " (" + m.pick.name + ") of chain " + m.pick.chain + " in " + m.pick.model + ". "; autosize(); $("input").focus(); },
    conversation(m) { loadConversation(m); },
    table_preview(m) { tablePreview(m); },
    table_result(m) { const el = document.createElement("div"); el.className = "msg assistant"; el.appendChild(resultCard("table_overlay", m.card || {}, m.results || [])); $("transcript").appendChild(el); hideWelcome(); scrollDown(true); },
    layers(m) { renderLayers(m.layers || []); },
  };
  function updateConfigLine() {
    const c = state.config || {};
    $("config-line").textContent = c.configured ? (c.label || c.preset) + " · " + (c.model || "") : "Not configured — open settings";
  }

  window.Pellaeon = {
    push(m) {
      try { const h = handlers[m.type]; if (h) h(m); else console.warn("unknown message", m); }
      catch (e) { console.error(e); send("log", {text: "JS error in " + m.type + ": " + e.message}); }
    },
    send: send,
  };

  // ---------------------------------------------------------------- wiring
  let WELCOME_HTML = "";
  function wireWelcome() { document.querySelectorAll(".examples li").forEach((li) => (li.onclick = () => submit(li.dataset.text))); }
  document.addEventListener("DOMContentLoaded", () => {
    WELCOME_HTML = $("transcript").innerHTML;
    $("btn-clearkey").onclick = () => { $("s-key").value = ""; $("s-key").dataset.clear = "1"; $("key-note").textContent = "Key will be removed when you save."; };
    $("s-key").addEventListener("input", () => { $("s-key").dataset.clear = ""; });
    $("send").onclick = () => (state.busy ? send("stop") : submit($("input").value));
    $("input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit($("input").value); }
    });
    $("input").addEventListener("input", autosize);
    $("chips").querySelectorAll("button").forEach((b) => (b.onclick = () => submit(b.dataset.text)));
    wireWelcome();
    $("btn-settings").onclick = () => showPage("settings");
    $("btn-history").onclick = () => showPage("history");
    $("btn-new").onclick = () => send("new_chat");
    $("btn-export").onclick = () => send("export_cxc");
    $("sel-ask").onclick = () => submit("Tell me about " + describeSel() + ": what is it, what does UniProt say about it, and what is it interacting with?");
    $("sel-near").onclick = () => submit("What residues and ligands are within 5 A of " + describeSel() + "? Show them as sticks and label them.");
    $("sel-color").onclick = () => submit("Highlight " + describeSel() + ": show its atoms as sticks in yellow and focus the view on it.");
    $("chip-bind").onclick = () => send("bind_click");
    $("btn-cancel-settings").onclick = () => showPage("chat");
    $("btn-cancel-history").onclick = () => showPage("chat");
    $("autonomy").onchange = () => send("set_autonomy", {mode: $("autonomy").value});
    $("btn-save").onclick = () => send("settings_save", {}, settingsPayload());
    $("btn-table").onclick = () => send("table_import");
    $("btn-first").onclick = () => { send("settings_save", {}, settingsPayload()); setTimeout(() => { showPage("chat"); submit("open 1ubq and color it by chain"); }, 400); };
    $("btn-test").onclick = () => { $("test-result").textContent = "Testing…"; $("test-result").className = "hints"; send("settings_test", {}, settingsPayload()); };
    $("btn-models").onclick = () => { $("test-result").textContent = "Fetching models…"; $("test-result").className = "hints"; send("list_models", {}, settingsPayload()); };
    $("btn-pull").onclick = () => send("pull_model", {model: $("s-pull").value.trim(), base_url: $("s-url").value.trim()});
    $("btn-index").onclick = () => { $("index-result").textContent = "Rebuilding…"; send("rebuild_index"); };
    $("btn-chimera-test").onclick = () => send("chimera_test", {port: $("s-chimera-port").value.trim()});
    $("btn-chimera-launch").onclick = () => send("chimera_launch", {}, {chimera_path: $("s-chimera-path").value.trim()});
    $("btn-showkey").onclick = () => ($("s-key").type = $("s-key").type === "password" ? "text" : "password");
    $("key-link").onclick = (e) => { e.preventDefault(); if ($("key-link").href && $("key-link").href !== "#") send("open_url", {url: $("key-link").href}); };
    // delegated: copy / rerun buttons and help links inside the transcript
    $("transcript").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (b && b.dataset.copy) {
        if (window.PELLAEON_HTTP && navigator.clipboard) { navigator.clipboard.writeText(b.dataset.copy).then(() => toast("Copied", "ok"), () => send("copy", {text: b.dataset.copy})); }
        else send("copy", {text: b.dataset.copy});
        return;
      }
      if (b && b.dataset.rerun) { send("rerun", {}, [b.dataset.rerun]); return; }
      if (b && b.dataset.help) { send("open_url", {url: "help:user/commands/" + b.dataset.help + ".html"}); return; }
      const a = e.target.closest("a");
      if (a && a.dataset.view) { e.preventDefault(); send("rerun", {}, ["view " + a.dataset.view]); return; }
      if (a && a.href) { e.preventDefault(); send("open_url", {url: a.getAttribute("href")}); }
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.busy) send("stop"); });
    if (!window.PELLAEON_HTTP) send("ready");
  });
})();
