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
    $("beacon").src = "pellaeon:" + action + "?" + q.toString();
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
  function scrollDown() {
    const t = $("transcript");
    t.scrollTop = t.scrollHeight;
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
    scrollDown();
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
    // replace the last streamed text segment with the rendered version of the whole final reply
    if (html) {
      if (t.textEl && t.textEl === t.el.lastElementChild) {
        t.textEl.className = "text rendered";
        t.textEl.innerHTML = html;
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
      const cmds = (args && args.commands) || [];
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
    return '<div class="cmd"><span class="dot ' + status + '"></span><code>' + esc(cmd) + '</code>' +
      '<span class="actions"><button title="Copy" data-copy="' + esc(cmd) + '">copy</button><button title="Run again" data-rerun="' + esc(cmd) + '">rerun</button></span></div>' +
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
    }
    scrollDown();
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
      const lines = card.querySelector("textarea").value.split("\n").map((s) => s.trim()).filter(Boolean);
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
  function rerunResult(results) {
    results.forEach((r) => toast((r.ok ? "OK: " : "Error: ") + (r.ok ? r.command : r.error), r.ok ? "ok" : "error"));
  }

  // ---------------------------------------------------------------- scene strip
  function renderScene(st) {
    const el = $("scene");
    if (!st || st.error) { el.textContent = ""; return; }
    const models = st.models || [];
    if (!models.length) { el.innerHTML = "<b>Nothing open</b> — try: open 4hhb"; return; }
    const parts = models.slice(0, 6).map((m) => "<b>" + esc(m.id) + "</b> " + esc(m.name) +
      (m.chains && m.chains.length ? " (" + m.chains.slice(0, 6).map((c) => c.id).join(",") + (m.chains.length > 6 ? "…" : "") + ")" : ""));
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
  }
  function fillSettings() {
    const s = state.settings;
    renderPresets(s.preset);
    selectPreset(s.preset || "ollama", false);
    $("s-model").value = s.model || (presetById(s.preset) || {}).model || "";
    $("s-url").value = s.base_url || (presetById(s.preset) || {}).base_url || "";
    $("s-key").value = "";
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
      api_key: key ? key : ($("s-key").dataset.unchanged ? "__unchanged__" : ""),
      autonomy: $("s-autonomy").value,
      allow_python: $("s-python").checked,
      vision: $("s-vision").checked,
      think: $("s-think").checked,
      effort: $("s-effort").value,
      temperature: parseFloat($("s-temp").value) || 0.2,
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
            const cmds = (c.args && c.args.commands) || [];
            html += '<details class="tool"><summary><span class="dot ok"></span><span class="label">Ran ' + cmds.length + " commands</span></summary><div class=\"cmds\">" + cmds.map((x) => cmdRow(x, "ok")).join("") + "</div></details>";
          } else {
            html += '<div class="tool"><div class="head"><span class="dot ok"></span><span class="label">' + esc(TOOL_LABELS[c.name] || c.name) + "</span></div></div>";
          }
        });
        if (m.html) html += '<div class="text rendered">' + m.html + "</div>";
        el.innerHTML = html;
        tr.appendChild(el);
      }
    });
    showPage("chat");
    scrollDown();
  }

  // ---------------------------------------------------------------- input
  function submit(text) {
    text = (text || "").trim();
    if (!text) return;
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
    clear() { $("transcript").innerHTML = ""; Object.keys(state.turns).forEach((k) => delete state.turns[k]); $("usage").textContent = ""; showPage("chat"); },
    rerun_result(m) { rerunResult(m.results || []); },
    test_result(m) {
      const el = $("test-result");
      el.textContent = m.text;
      el.className = "hints " + (m.ok ? "ok" : "err");
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
    conversation(m) { loadConversation(m); },
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
  document.addEventListener("DOMContentLoaded", () => {
    $("send").onclick = () => (state.busy ? send("stop") : submit($("input").value));
    $("input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit($("input").value); }
    });
    $("input").addEventListener("input", autosize);
    $("chips").querySelectorAll("button").forEach((b) => (b.onclick = () => submit(b.dataset.text)));
    document.querySelectorAll(".examples li").forEach((li) => (li.onclick = () => submit(li.dataset.text)));
    $("btn-settings").onclick = () => showPage("settings");
    $("btn-history").onclick = () => showPage("history");
    $("btn-new").onclick = () => send("new_chat");
    $("btn-cancel-settings").onclick = () => showPage("chat");
    $("btn-cancel-history").onclick = () => showPage("chat");
    $("autonomy").onchange = () => send("set_autonomy", {mode: $("autonomy").value});
    $("btn-save").onclick = () => send("settings_save", {}, settingsPayload());
    $("btn-test").onclick = () => { $("test-result").textContent = "Testing…"; $("test-result").className = "hints"; send("settings_test", {}, settingsPayload()); };
    $("btn-models").onclick = () => { $("test-result").textContent = "Fetching models…"; $("test-result").className = "hints"; send("list_models", {}, settingsPayload()); };
    $("btn-pull").onclick = () => send("pull_model", {model: $("s-pull").value.trim(), base_url: $("s-url").value.trim()});
    $("btn-index").onclick = () => { $("index-result").textContent = "Rebuilding…"; send("rebuild_index"); };
    $("btn-showkey").onclick = () => ($("s-key").type = $("s-key").type === "password" ? "text" : "password");
    $("key-link").onclick = (e) => { e.preventDefault(); if ($("key-link").href && $("key-link").href !== "#") send("open_url", {url: $("key-link").href}); };
    // delegated: copy / rerun buttons and help links inside the transcript
    $("transcript").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (b && b.dataset.copy) { send("copy", {text: b.dataset.copy}); return; }
      if (b && b.dataset.rerun) { send("rerun", {}, [b.dataset.rerun]); return; }
      const a = e.target.closest("a");
      if (a && a.href) { e.preventDefault(); send("open_url", {url: a.getAttribute("href")}); }
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.busy) send("stop"); });
    send("ready");
  });
})();
