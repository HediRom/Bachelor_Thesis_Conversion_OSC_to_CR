// ==UserScript==
// @name         CommonRoad Trigger Panel (Storyboard Transcription/Translation/Interpretation)
// @namespace    tum-thesis-storyboard-parsing
// @version      1.2.0
// @description  Overlays OpenSCENARIO trigger information (Transcription/Translation/Interpretation) on the CommonRoad Scenario Viewer, synced to the playback timeline. Load the triggers.json produced by the Storyboard_parsing pipeline.
// @match        https://crdesigner.cps.cit.tum.de/*
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// ==/UserScript==

(function () {
  "use strict";

  const DEFAULT_URL = "http://127.0.0.1:8765/triggers.json";
  const URL_KEY = "crtp-url";
  const POLL_MS = 150;

  let data = null;        // parsed triggers.json
  let currentStep = null; // last time step read from the viewer's slider
  let sliderUnit = "steps"; // "steps" | "seconds"

  // ------------------------------------------------------------------
  // Styles
  // ------------------------------------------------------------------
  const style = document.createElement("style");
  style.textContent = `
  #crtp-panel {
    position: fixed; top: 70px; right: 12px; width: 360px; max-height: 82vh;
    display: flex; flex-direction: column; z-index: 99999;
    background: #ffffff; color: #1a1a2e; border: 1px solid #d0d4dc;
    border-radius: 10px; box-shadow: 0 6px 24px rgba(0,0,0,.18);
    font: 12px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  #crtp-head {
    display: flex; align-items: center; gap: 8px; padding: 8px 10px;
    background: #f2f4f8; border-bottom: 1px solid #d0d4dc;
    border-radius: 10px 10px 0 0; cursor: grab; user-select: none;
  }
  #crtp-head b { font-size: 12.5px; }
  #crtp-time {
    margin-left: auto; font-variant-numeric: tabular-nums;
    background: #1a1a2e; color: #fff; padding: 2px 8px; border-radius: 999px;
    font-size: 11px; white-space: nowrap;
  }
  #crtp-collapse {
    border: none; background: none; cursor: pointer; font-size: 14px;
    color: #555; padding: 0 2px;
  }
  #crtp-body { overflow-y: auto; padding: 10px; }
  #crtp-panel.crtp-min #crtp-body { display: none; }

  .crtp-loadbar { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
  .crtp-row { display: flex; gap: 6px; }
  .crtp-row input[type=text] {
    flex: 1; padding: 4px 6px; border: 1px solid #c6cbd4; border-radius: 6px;
    font-size: 11px; min-width: 0;
  }
  .crtp-btn {
    padding: 4px 10px; border: 1px solid #c6cbd4; border-radius: 6px;
    background: #fff; cursor: pointer; font-size: 11.5px; white-space: nowrap;
  }
  .crtp-btn:hover { background: #eef1f6; }
  #crtp-drop {
    border: 1.5px dashed #b7bdc9; border-radius: 8px; padding: 8px;
    text-align: center; color: #667; font-size: 11px;
  }
  #crtp-drop.crtp-over { border-color: #2563eb; background: #eff4ff; color: #2563eb; }
  #crtp-status { font-size: 11px; color: #667; margin: 4px 0 8px; }
  #crtp-status.crtp-err { color: #b3261e; }

  .crtp-meta { font-size: 11px; color: #445; margin-bottom: 8px; }
  .crtp-meta b { color: #1a1a2e; }
  .crtp-sec { margin: 10px 0 4px; font-size: 11px; font-weight: 700;
    letter-spacing: .04em; text-transform: uppercase; color: #556; }

  .crtp-card {
    border: 1px solid #dfe3ea; border-radius: 8px; padding: 7px 8px;
    margin-bottom: 7px; background: #fbfcfe;
  }
  .crtp-card.crtp-fired { border-color: #15803d; background: #f2fbf5; }
  .crtp-evname { font-weight: 700; font-size: 12px; }
  .crtp-evpath { color: #778; font-size: 10.5px; margin-bottom: 4px; }
  .crtp-cond { margin: 5px 0 0; padding: 5px 6px; border-radius: 6px; background: #fff;
    border: 1px solid #e8ebf1; }
  .crtp-cond.crtp-active { border-color: #2563eb; box-shadow: 0 0 0 1px #2563eb33; }
  .crtp-condname { font-weight: 600; }
  .crtp-condtext { color: #334; }

  .crtp-badge {
    display: inline-block; padding: 1px 7px; border-radius: 999px;
    font-size: 10px; font-weight: 700; margin-right: 4px; vertical-align: 1px;
  }
  .crtp-b-type   { background: #eef1f6; color: #445; font-weight: 600; }
  .crtp-b-mapped { background: #dcf5e4; color: #15803d; }
  .crtp-b-skip   { background: #fdf1d7; color: #92600a; }
  .crtp-b-fired  { background: #15803d; color: #fff; }
  .crtp-b-wait   { background: #e6e9ef; color: #556; }
  .crtp-b-active { background: #2563eb; color: #fff; }
  .crtp-reason { color: #92600a; font-size: 10.5px; margin-top: 2px; }
  .crtp-interpline { margin-top: 5px; font-size: 11px; }

  @keyframes crtp-pop { 0% { box-shadow: 0 0 0 0 #15803d88; }
                        100% { box-shadow: 0 0 0 12px #15803d00; } }
  .crtp-flash { animation: crtp-pop .7s ease-out 2; }
  .crtp-select { border: 1px solid #c6cbd4; border-radius: 6px; font-size: 11px;
    padding: 3px 4px; background: #fff; }
  `;
  document.head.appendChild(style);

  // ------------------------------------------------------------------
  // Panel skeleton
  // ------------------------------------------------------------------
  const panel = document.createElement("div");
  panel.id = "crtp-panel";
  panel.innerHTML = `
    <div id="crtp-head">
      <b>Storyboard Triggers</b>
      <span id="crtp-time">step –</span>
      <button id="crtp-collapse" title="Collapse">▾</button>
    </div>
    <div id="crtp-body">
      <div class="crtp-loadbar">
        <div id="crtp-drop">drop <b>triggers.json</b> here — or</div>
        <div class="crtp-row">
          <button class="crtp-btn" id="crtp-file-btn">Open file…</button>
          <button class="crtp-btn" id="crtp-fit"
            title="Zoom the viewer to the loaded scenario (the viewer has no auto-zoom; flat road networks sit invisibly on the top edge)">Fit view</button>
          <select class="crtp-select" id="crtp-unit" title="Unit of the viewer's timeline slider">
            <option value="steps" selected>slider = steps</option>
            <option value="seconds">slider = seconds</option>
          </select>
          <input type="file" id="crtp-file" accept=".json" style="display:none">
        </div>
        <div class="crtp-row">
          <input type="text" id="crtp-url" spellcheck="false">
          <button class="crtp-btn" id="crtp-fetch">Fetch</button>
        </div>
      </div>
      <div id="crtp-status">No data loaded yet.</div>
      <div id="crtp-content"></div>
    </div>`;
  document.body.appendChild(panel);

  const $ = (id) => document.getElementById(id);
  $("crtp-url").value = localStorage.getItem(URL_KEY) || DEFAULT_URL;

  $("crtp-collapse").addEventListener("click", () => {
    panel.classList.toggle("crtp-min");
    $("crtp-collapse").textContent = panel.classList.contains("crtp-min") ? "▸" : "▾";
  });
  $("crtp-unit").addEventListener("change", (e) => { sliderUnit = e.target.value; });

  // Dragging the panel by its header
  (function makeDraggable() {
    const head = $("crtp-head");
    let sx = 0, sy = 0, ox = 0, oy = 0, moving = false;
    head.addEventListener("mousedown", (e) => {
      if (e.target.id === "crtp-collapse") return;
      moving = true; sx = e.clientX; sy = e.clientY;
      const r = panel.getBoundingClientRect(); ox = r.left; oy = r.top;
      e.preventDefault();
    });
    window.addEventListener("mousemove", (e) => {
      if (!moving) return;
      panel.style.left = ox + (e.clientX - sx) + "px";
      panel.style.top = oy + (e.clientY - sy) + "px";
      panel.style.right = "auto";
    });
    window.addEventListener("mouseup", () => { moving = false; });
  })();

  // ------------------------------------------------------------------
  // Data loading
  // ------------------------------------------------------------------
  function setStatus(msg, isErr) {
    const el = $("crtp-status");
    el.textContent = msg;
    el.classList.toggle("crtp-err", !!isErr);
  }

  function acceptJson(text, sourceLabel) {
    let parsed;
    try { parsed = JSON.parse(text); }
    catch (e) { setStatus("Not valid JSON: " + e.message, true); return; }
    if (!parsed || !Array.isArray(parsed.events)) {
      setStatus("This file has no 'events' list — load the triggers.json " +
                "written by the pipeline (not conditions_transcription.json / conditions_translation.json).", true);
      return;
    }
    data = parsed;
    render();
    const c = data.counts || {};
    setStatus(`Loaded ${sourceLabel}: ${c.events ?? data.events.length} events, ` +
              `${c.translation_mapped ?? "?"} mapped / ${c.translation_skipped ?? "?"} skipped (translation), ` +
              `${c.interpretation_fired ?? "?"} fired (interpretation).`);
  }

  function loadFile(file) {
    const reader = new FileReader();
    reader.onload = () => acceptJson(String(reader.result), file.name);
    reader.onerror = () => setStatus("Could not read file.", true);
    reader.readAsText(file);
  }

  $("crtp-file-btn").addEventListener("click", () => $("crtp-file").click());
  $("crtp-file").addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) loadFile(e.target.files[0]);
    e.target.value = "";
  });

  // Drop zone — stop propagation so the viewer doesn't try to load the JSON
  // as a scenario.
  const drop = $("crtp-drop");
  for (const ev of ["dragenter", "dragover"]) {
    drop.addEventListener(ev, (e) => {
      e.preventDefault(); e.stopPropagation(); drop.classList.add("crtp-over");
    });
  }
  drop.addEventListener("dragleave", (e) => {
    e.preventDefault(); e.stopPropagation(); drop.classList.remove("crtp-over");
  });
  drop.addEventListener("drop", (e) => {
    e.preventDefault(); e.stopPropagation(); drop.classList.remove("crtp-over");
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) loadFile(f);
  });

  function fetchUrl() {
    const url = $("crtp-url").value.trim();
    localStorage.setItem(URL_KEY, url);
    setStatus("Fetching " + url + " …");
    if (typeof GM_xmlhttpRequest === "function") {
      GM_xmlhttpRequest({
        method: "GET", url,
        onload: (r) => (r.status >= 200 && r.status < 300)
          ? acceptJson(r.responseText, url)
          : setStatus(`HTTP ${r.status} from ${url}`, true),
        onerror: () => setStatus("Fetch failed — is the local server running? " +
          "(python -m http.server 8765 --directory <output dir>)", true),
      });
    } else {
      fetch(url).then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      }).then((t) => acceptJson(t, url))
        .catch((e) => setStatus("Fetch failed: " + e.message, true));
    }
  }
  $("crtp-fetch").addEventListener("click", fetchUrl);

  // ------------------------------------------------------------------
  // Fit view — the viewer never auto-zooms to content, so scenarios with
  // near-zero y extent (straight roads at y≈0) render as an invisible
  // sliver on the top edge. Setting the SVG viewBox to the drawn bounding
  // box fixes that; the app's own MutationObserver redraws the axes.
  // ------------------------------------------------------------------
  function fitView() {
    const svg = document.querySelector("#canvas-container svg");
    if (!svg) {
      setStatus("No scenario canvas found — load a scenario first.", true);
      return;
    }
    let bb = null;
    try { bb = svg.getBBox(); } catch (e) { /* detached svg */ }
    if (!bb || (!bb.width && !bb.height)) {
      setStatus("Canvas is empty — load a scenario first.", true);
      return;
    }
    // 5% margin, and keep a sane minimum height so a flat road network
    // doesn't fill the whole screen height with 6 m of road.
    const mx = Math.max(bb.width * 0.05, 5);
    const my = Math.max(bb.height * 0.05, 5);
    let x = bb.x - mx, y = bb.y - my;
    let w = bb.width + 2 * mx, h = bb.height + 2 * my;
    const minH = w / 8;
    if (h < minH) { y -= (minH - h) / 2; h = minH; }
    svg.setAttribute("viewBox", `${x} ${y} ${w} ${h}`);
    setStatus(`View fitted to scenario extents (${Math.round(w)} × ${Math.round(h)} m).`);
  }
  $("crtp-fit").addEventListener("click", fitView);

  // ------------------------------------------------------------------
  // Rendering
  // ------------------------------------------------------------------
  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g,
      (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
  }

  function windowLabel(interval) {
    if (!interval) return "";
    const [a, b] = interval;
    return `[${a}, ${b == null ? "∞" : b}]`;
  }

  function translationBadge(c) {
    if (!c) return "";
    if (String(c.status || "").startsWith("mapped")) {
      return `<span class="crtp-badge crtp-b-mapped" data-role="cwin">C ${
        windowLabel(c.time_step_interval)}</span>`;
    }
    return `<span class="crtp-badge crtp-b-skip" title="${esc(c.reason)}">C skipped → D</span>`;
  }

  function render() {
    const box = $("crtp-content");
    if (!data) { box.innerHTML = ""; return; }
    const dt = data.dt || 0.1;
    let html = `
      <div class="crtp-meta"><b>${esc(data.scenario || "scenario")}</b>
        &nbsp;·&nbsp; dt = ${dt}s
        &nbsp;·&nbsp; ${data.events.length} events</div>`;

    const sbTriggers = data.storyboard_triggers || [];
    if (sbTriggers.length) {
      html += `<div class="crtp-sec">Storyboard triggers — Translation</div>`;
      for (const t of sbTriggers) {
        const c = t.translation || {};
        const mapped = String(c.status || "").startsWith("mapped");
        html += `
          <div class="crtp-cond" ${mapped ? `data-win="${esc(JSON.stringify(c.time_step_interval))}"` : ""}>
            <span class="crtp-condname">${esc(t.name)}</span>
            ${translationBadge(c)}
            ${mapped ? `<span class="crtp-badge crtp-b-wait" data-role="state">pending</span>` : ""}
            ${c.reason ? `<div class="crtp-reason">${esc(c.reason)}</div>` : ""}
          </div>`;
      }
    }

    html += `<div class="crtp-sec">Events — Transcription &amp; Interpretation</div>`;
    for (const ev of data.events) {
      const d = ev.interpretation || { fired: false, fires: [] };
      const firstFire = d.fires && d.fires[0];
      let interpretationLine;
      if (firstFire) {
        const extra = d.fires.length > 1 ? ` (+${d.fires.length - 1} more)` : "";
        interpretationLine = `<span class="crtp-badge crtp-b-wait" data-role="interpstate">pending</span>
                 Interpretation replay: fires @ step ${firstFire.time_step}
                 (t = ${firstFire.time_s}s)${extra}`;
      } else {
        interpretationLine = `<span class="crtp-badge crtp-b-wait">interpretation</span> did not fire in replay`;
      }
      let conds = "";
      for (const c of ev.conditions || []) {
        conds += `
          <div class="crtp-cond">
            <span class="crtp-condname">${esc(c.name)}</span>
            <span class="crtp-badge crtp-b-type">${esc(c.type).replace("Condition", "")}</span>
            ${translationBadge(c.translation)}
            <div class="crtp-condtext">${esc(c.text)}${
              c.edge && c.edge !== "rising" ? ` — edge: ${esc(c.edge)}` : ""}${
              c.delay_s ? ` — delay ${c.delay_s}s` : ""}</div>
            ${c.translation && c.translation.reason ? `<div class="crtp-reason">${esc(c.translation.reason)}</div>` : ""}
          </div>`;
      }
      html += `
        <div class="crtp-card" ${firstFire ? `data-fire="${firstFire.time_step}"` : ""}>
          <div class="crtp-evname">${esc(ev.name)}</div>
          <div class="crtp-evpath">${esc(ev.story)} / ${esc(ev.act)}
            · actors: ${esc((ev.actors || []).join(", "))}</div>
          ${conds}
          <div class="crtp-interpline">${interpretationLine}</div>
        </div>`;
    }
    box.innerHTML = html;
    applyStep(true);
  }

  // ------------------------------------------------------------------
  // Timeline sync
  // ------------------------------------------------------------------
  function findSlider() {
    const els = document.querySelectorAll('[role="slider"], input[type="range"]');
    let best = null, bestMax = -1;
    for (const el of els) {
      if (panel.contains(el)) continue;
      const max = parseFloat(el.getAttribute("aria-valuemax") ?? el.max);
      if (Number.isFinite(max) && max > bestMax) { bestMax = max; best = el; }
    }
    return best;
  }

  function readStep() {
    const el = findSlider();
    if (!el) return null;
    let v = parseFloat(el.getAttribute("aria-valuenow") ?? el.value);
    if (!Number.isFinite(v)) return null;
    if (sliderUnit === "seconds" && data && data.dt) v = v / data.dt;
    return Math.round(v);
  }

  function applyStep(force) {
    const step = readStep();
    if (step === currentStep && !force) return;
    const prev = currentStep;
    currentStep = step;

    const dt = (data && data.dt) || 0.1;
    $("crtp-time").textContent = step == null
      ? "step –"
      : `step ${step} · ${(step * dt).toFixed(1)}s`;
    if (!data || step == null) return;

    // Translation time windows → active / pending / passed
    for (const el of document.querySelectorAll("#crtp-content [data-win]")) {
      let win;
      try { win = JSON.parse(el.getAttribute("data-win")); } catch { continue; }
      const [a, b] = win;
      const active = step >= a && (b == null || step <= b);
      el.classList.toggle("crtp-active", active);
      const badge = el.querySelector('[data-role="state"]');
      if (badge) {
        badge.textContent = active ? "ACTIVE" : (step < a ? "pending" : "passed");
        badge.className = "crtp-badge " + (active ? "crtp-b-active" : "crtp-b-wait");
      }
    }

    // Interpretation fire markers → pending / fired (flash on crossing)
    for (const card of document.querySelectorAll("#crtp-content [data-fire]")) {
      const fireStep = parseInt(card.getAttribute("data-fire"), 10);
      const fired = step >= fireStep;
      card.classList.toggle("crtp-fired", fired);
      const badge = card.querySelector('[data-role="interpstate"]');
      if (badge) {
        badge.textContent = fired ? "FIRED" : "pending";
        badge.className = "crtp-badge " + (fired ? "crtp-b-fired" : "crtp-b-wait");
      }
      if (fired && prev != null && prev < fireStep && !force) {
        card.classList.remove("crtp-flash");
        void card.offsetWidth; // restart the animation
        card.classList.add("crtp-flash");
      }
    }
  }

  setInterval(() => applyStep(false), POLL_MS);

  // Try the saved/default localhost URL once, quietly.
  fetchUrl();
})();
