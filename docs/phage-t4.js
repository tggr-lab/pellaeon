// Phage T4 page: hero loop, click-to-load film with chapters, gallery hotspots, before/after sliders, lightbox.
(function () {
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var saveData = navigator.connection && navigator.connection.saveData;

  // hero loop: poster only when motion is reduced or data saving is on
  var loop = document.getElementById("phage-loop");
  if (loop && !reduce && !saveData) {
    loop.setAttribute("autoplay", "");
    loop.preload = "auto";
    var p = loop.play();
    if (p && p.catch) p.catch(function () { loop.setAttribute("controls", ""); });
  }

  // film: nothing from YouTube until the click; chapters seek the player once it is there
  var yt = document.getElementById("ph-yt"), frame = null;
  function load(start) {
    if (!yt) return;
    if (frame) {
      frame.contentWindow.postMessage(JSON.stringify({ event: "command", func: "seekTo", args: [start, true] }), "*");
      frame.contentWindow.postMessage(JSON.stringify({ event: "command", func: "playVideo", args: [] }), "*");
      return;
    }
    frame = document.createElement("iframe");
    frame.src = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(yt.dataset.id) +
      "?autoplay=1&rel=0&playsinline=1&enablejsapi=1" + (start ? "&start=" + start : "");
    frame.title = "Phage T4 film";
    frame.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
    frame.allowFullscreen = true;
    yt.innerHTML = "";
    yt.appendChild(frame);
    yt.classList.add("on");
  }
  if (yt) {
    var playBtn = yt.querySelector(".ph-yt-play");
    if (playBtn) playBtn.addEventListener("click", function () { load(0); });
    document.querySelectorAll(".ph-ch").forEach(function (b) {
      b.addEventListener("click", function () {
        load(parseInt(b.dataset.t, 10) || 0);
        yt.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
      });
    });
  }

  // hotspots: the label and fact go in the line under the picture, never over it
  document.querySelectorAll(".pg-card").forEach(function (card) {
    var spots = card.querySelectorAll(".pg-spot"), note = card.querySelector(".pg-note");
    if (!spots.length || !note) return;
    var base = note.textContent;
    function show(s) {
      spots.forEach(function (o) { o.classList.toggle("on", o === s); o.setAttribute("aria-pressed", o === s ? "true" : "false"); });
      note.innerHTML = "";
      if (!s) { note.textContent = base; return; }
      var n = document.createElement("i"); n.textContent = s.textContent;
      var b = document.createElement("b"); b.textContent = s.dataset.label;
      note.appendChild(n); note.appendChild(b); note.appendChild(document.createTextNode(" " + s.dataset.fact));
    }
    spots.forEach(function (s) {
      s.setAttribute("aria-pressed", "false");
      s.addEventListener("mouseenter", function () { show(s); });
      s.addEventListener("focus", function () { show(s); });
      s.addEventListener("click", function () { show(s); });
    });
    card.querySelector(".pg-media").addEventListener("mouseleave", function () {
      if (!card.contains(document.activeElement) || !document.activeElement.classList.contains("pg-spot")) show(null);
    });
  });

  // sliders: the range input drives a clip of the second picture
  document.querySelectorAll(".pg-slider").forEach(function (sl) {
    var r = sl.querySelector(".pg-range");
    function set() { sl.style.setProperty("--pos", r.value + "%"); }
    r.addEventListener("input", set);
    set();
  });

  // lightbox
  var box = document.getElementById("pg-box");
  if (!box || !box.showModal) return;
  var cards = Array.prototype.slice.call(document.querySelectorAll(".pg-card")), cur = 0;
  var media = box.querySelector(".pg-box-media"), cap = box.querySelector(".pg-box-cap");
  function openAt(i) {
    cur = (i + cards.length) % cards.length;
    var c = cards[cur], z = c.querySelector(".pg-zoom");
    media.innerHTML = "";
    [z.dataset.full, z.dataset.full2].forEach(function (src) {
      if (!src) return;
      var im = document.createElement("img");
      im.src = src; im.alt = (c.querySelector("img") || {}).alt || "";
      media.appendChild(im);
    });
    media.classList.toggle("two", !!z.dataset.full2);
    cap.innerHTML = c.querySelector("figcaption").innerHTML;
    var spots = c.querySelectorAll(".pg-spot");
    if (spots.length) {
      var ol = document.createElement("ol");
      spots.forEach(function (s) { var li = document.createElement("li"); var b = document.createElement("b"); b.textContent = s.dataset.label; li.appendChild(b); li.appendChild(document.createTextNode(" " + s.dataset.fact)); ol.appendChild(li); });
      cap.appendChild(ol);
    }
    if (!box.open) box.showModal();
  }
  cards.forEach(function (c, i) {
    var z = c.querySelector(".pg-zoom");
    if (z) z.addEventListener("click", function () { openAt(i); });
    var im = c.querySelector(".pg-media img");
    if (im) { im.style.cursor = "zoom-in"; im.addEventListener("click", function () { openAt(i); }); }
  });
  box.querySelector(".pg-close").addEventListener("click", function () { box.close(); });
  box.querySelector(".pg-prev").addEventListener("click", function () { openAt(cur - 1); });
  box.querySelector(".pg-next").addEventListener("click", function () { openAt(cur + 1); });
  box.addEventListener("click", function (e) { if (e.target === box) box.close(); });
  box.addEventListener("keydown", function (e) {
    if (e.key === "ArrowRight") { openAt(cur + 1); e.preventDefault(); }
    if (e.key === "ArrowLeft") { openAt(cur - 1); e.preventDefault(); }
  });
})();
