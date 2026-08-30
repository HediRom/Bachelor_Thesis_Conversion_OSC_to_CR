/*
 * osc2cr interactive viewer
 * =========================
 * Renders a converted CommonRoad scenario and keeps the OpenSCENARIO triggers
 * visible while it plays.
 *
 * The trigger data is read straight out of the CommonRoad file's embedded
 * <osc:triggers> block — the same file any other CommonRoad tool consumes —
 * and only falls back to the triggers.json sidecar if the block is absent.
 * That is the point of the tool: the conditional structure travels with the
 * scenario instead of being lost at conversion time.
 */
'use strict';

const TRIGGER_NS = 'urn:osc2cr:triggers:1';

const state = {
  bundle: null,        // manifest entry for the loaded scenario
  scenario: null,      // parsed geometry from scenario.xml
  triggers: null,      // trigger document
  triggerOrigin: '',   // 'CommonRoad XML' | 'triggers.json'
  timeline: null,      // per-condition truth matrix for the *active* run
  step: 0,
  playing: false,
  speed: 1,
  view: { scale: 1, tx: 0, ty: 0, fitted: false },

  /*
   * Two runs of the same scenario, switched by the toolbar:
   *
   *   'replay'  the converted scenario — esmini drove every actor, ego included.
   *             Motion comes from scenario.xml, triggers from its embedded block.
   *   'cosim'   the closed loop — the CommonRoad reactive planner drove the ego
   *             inside esmini, so the storyboard re-timed around it. Motion and
   *             triggers come from cosim_trace_planner.json.
   *
   * Both are kept loaded at once: whichever is not active is drawn as a ghost,
   * which is the only way to *see* where the two diverge rather than infer it
   * from two numbers.
   */
  run: 'replay',
  cosim: null,          // parsed cosim_trace_planner.json
  cosimIndex: null,     // obstacleId → Map(step → state)
  replayTimeline: null, // timeline.json, kept while a cosim run is showing
};

const el = {
  canvas: document.getElementById('scene'),
  legend: document.getElementById('legend'),
  hud: document.getElementById('hud'),
  status: document.getElementById('status'),
  scenarioSelect: document.getElementById('scenario-select'),
  corpusSelect: document.getElementById('corpus-select'),
  convertBtn: document.getElementById('convert-btn'),
  cosimBtn: document.getElementById('cosim-btn'),
  fitBtn: document.getElementById('fit-btn'),
  playBtn: document.getElementById('play-btn'),
  stepBack: document.getElementById('step-back'),
  stepFwd: document.getElementById('step-fwd'),
  speedSelect: document.getElementById('speed-select'),
  scrub: document.getElementById('scrub'),
  fires: document.getElementById('fires'),
  strips: document.getElementById('strips'),
  triggers: document.getElementById('triggers'),
  runReplay: document.getElementById('run-replay'),
  runCosim: document.getElementById('run-cosim'),
  diffbar: document.getElementById('diffbar'),
  stats: document.getElementById('stats'),
  origin: document.getElementById('trigger-origin'),
  clockTime: document.getElementById('clock-time'),
  clockStep: document.getElementById('clock-step'),
};

const ctx = el.canvas.getContext('2d');

/* ------------------------------------------------------------------ */
/* utilities                                                           */
/* ------------------------------------------------------------------ */

function setStatus(text, kind = '') {
  el.status.textContent = text;
  el.status.className = 'status' + (kind ? ' ' + kind : '');
}

async function getJSON(url) {
  const res = await fetch(url);
  const body = await res.json().catch(() => ({ error: res.statusText }));
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

async function postJSON(url, payload) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({ error: res.statusText }));
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

const num = (node, tag, fallback = 0) => {
  const found = node && node.querySelector(tag);
  const value = found ? parseFloat(found.textContent) : NaN;
  return Number.isFinite(value) ? value : fallback;
};

/* ------------------------------------------------------------------ */
/* CommonRoad XML parsing                                              */
/* ------------------------------------------------------------------ */

function parsePoints(boundEl) {
  if (!boundEl) return [];
  return Array.from(boundEl.querySelectorAll('point')).map((p) => ({
    x: num(p, 'x'),
    y: num(p, 'y'),
  }));
}

function parseStateList(parent) {
  return Array.from(parent.children)
    .filter((c) => c.tagName === 'state')
    .map((s) => ({
      step: Math.round(num(s, 'time > exact', num(s, 'time > intervalStart'))),
      x: num(s, 'position > point > x'),
      y: num(s, 'position > point > y'),
      orientation: num(s, 'orientation > exact'),
      velocity: num(s, 'velocity > exact'),
    }));
}

function parseInitialState(stateEl) {
  if (!stateEl) return null;
  return {
    step: Math.round(num(stateEl, 'time > exact')),
    x: num(stateEl, 'position > point > x'),
    y: num(stateEl, 'position > point > y'),
    orientation: num(stateEl, 'orientation > exact'),
    velocity: num(stateEl, 'velocity > exact'),
  };
}

function parseGoal(problemEl) {
  const goals = [];
  problemEl.querySelectorAll('goalState').forEach((g) => {
    const rect = g.querySelector('position > rectangle');
    if (!rect) return;
    goals.push({
      length: num(rect, 'length', 0),
      width: num(rect, 'width', 0),
      orientation: num(rect, 'orientation', 0),
      x: num(rect, 'center > x'),
      y: num(rect, 'center > y'),
      timeStart: num(g, 'time > intervalStart', NaN),
      timeEnd: num(g, 'time > intervalEnd', NaN),
    });
  });
  return goals;
}

/** Read the <osc:triggers> block the converter embedded in the CR file. */
function parseEmbeddedTriggers(doc) {
  const blocks = doc.getElementsByTagNameNS(TRIGGER_NS, 'triggers');
  if (!blocks.length) return null;
  const payload = blocks[0].getElementsByTagNameNS(TRIGGER_NS, 'payload')[0];
  if (!payload || !payload.textContent) return null;
  try {
    return JSON.parse(payload.textContent);
  } catch (err) {
    console.warn('embedded trigger payload is not valid JSON', err);
    return null;
  }
}

function parseCommonRoad(xmlText) {
  const doc = new DOMParser().parseFromString(xmlText, 'application/xml');
  if (doc.querySelector('parsererror')) throw new Error('scenario.xml is not valid XML');

  const root = doc.documentElement;
  const dt = parseFloat(root.getAttribute('timeStepSize')) || 0.1;

  const lanelets = Array.from(doc.querySelectorAll('lanelet')).map((l) => ({
    id: l.getAttribute('id'),
    left: parsePoints(l.querySelector('leftBound')),
    right: parsePoints(l.querySelector('rightBound')),
  }));

  const obstacles = Array.from(doc.querySelectorAll('dynamicObstacle')).map((o) => {
    const rect = o.querySelector('shape > rectangle');
    const initial = parseInitialState(o.querySelector('initialState'));
    const traj = o.querySelector('trajectory');
    const states = traj ? parseStateList(traj) : [];
    if (initial) states.unshift(initial);

    const byStep = new Map();
    states.forEach((s) => byStep.set(s.step, s));

    return {
      id: o.getAttribute('id'),
      type: (o.querySelector('type') || {}).textContent || 'car',
      length: rect ? num(rect, 'length', 4.5) : 4.5,
      width: rect ? num(rect, 'width', 1.8) : 1.8,
      states: byStep,
      firstStep: states.length ? states[0].step : 0,
      lastStep: states.length ? states[states.length - 1].step : 0,
    };
  });

  const goals = [];
  doc.querySelectorAll('planningProblem').forEach((p) => goals.push(...parseGoal(p)));

  const maxStep = obstacles.reduce((m, o) => Math.max(m, o.lastStep), 0);

  return {
    dt,
    benchmarkId: root.getAttribute('benchmarkID') || '',
    lanelets,
    obstacles,
    goals,
    maxStep,
    embeddedTriggers: parseEmbeddedTriggers(doc),
  };
}

/* ------------------------------------------------------------------ */
/* view transform                                                      */
/* ------------------------------------------------------------------ */

function sceneBounds(scenario) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const grow = (x, y) => {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  };

  scenario.lanelets.forEach((l) => {
    l.left.forEach((p) => grow(p.x, p.y));
    l.right.forEach((p) => grow(p.x, p.y));
  });
  scenario.obstacles.forEach((o) => o.states.forEach((s) => grow(s.x, s.y)));
  scenario.goals.forEach((g) => grow(g.x, g.y));

  if (!Number.isFinite(minX)) return { minX: -50, minY: -50, maxX: 50, maxY: 50 };
  return { minX, minY, maxX, maxY };
}

function fitView() {
  if (!state.scenario) return;
  const { width, height } = el.canvas.getBoundingClientRect();
  const b = sceneBounds(state.scenario);
  const pad = 40;
  const spanX = Math.max(b.maxX - b.minX, 1);
  const spanY = Math.max(b.maxY - b.minY, 1);
  const scale = Math.min((width - 2 * pad) / spanX, (height - 2 * pad) / spanY);

  state.view.scale = scale;
  state.view.tx = width / 2 - ((b.minX + b.maxX) / 2) * scale;
  state.view.ty = height / 2 + ((b.minY + b.maxY) / 2) * scale;
  state.view.fitted = true;
  draw();
}

const toScreen = (x, y) => ({
  x: x * state.view.scale + state.view.tx,
  y: -y * state.view.scale + state.view.ty,
});

const toWorld = (sx, sy) => ({
  x: (sx - state.view.tx) / state.view.scale,
  y: -(sy - state.view.ty) / state.view.scale,
});

function resizeCanvas() {
  const rect = el.canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  el.canvas.width = Math.round(rect.width * dpr);
  el.canvas.height = Math.round(rect.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (state.scenario && !state.view.fitted) fitView();
  else draw();
}

/* ------------------------------------------------------------------ */
/* drawing                                                             */
/* ------------------------------------------------------------------ */

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Hold the last known state, so an actor whose run ends early stays visible. */
function holdAt(states, step) {
  if (states.has(step)) return states.get(step);
  let best = null;
  states.forEach((s) => {
    if (s.step <= step && (!best || s.step > best.step)) best = s;
  });
  return best;
}

/** Where the actor was in the converted (esmini-driven) run. */
function replayStateAt(obs, step) {
  return holdAt(obs.states, step);
}

/**
 * Where the actor was in the closed-loop run.
 *
 * Returns null rather than falling back to the replay: silently mixing the two
 * runs on one screen would defeat the entire point of being able to compare
 * them.
 */
function cosimStateAt(obs, step) {
  const byStep = state.cosimIndex && state.cosimIndex.get(obs.id);
  return byStep ? holdAt(byStep, step) : null;
}

function obstacleStateAt(obs, step) {
  return state.run === 'cosim'
    ? cosimStateAt(obs, step)
    : replayStateAt(obs, step);
}

/** The same actor in the run that is *not* on screen, for the ghost overlay. */
function otherRunStateAt(obs, step) {
  if (!state.cosim) return null;
  return state.run === 'cosim'
    ? replayStateAt(obs, step)
    : cosimStateAt(obs, step);
}

/** Last step the active run actually covers. */
function runMaxStep() {
  if (state.run === 'cosim' && state.cosim && state.cosim.timeline) {
    const steps = state.cosim.timeline.time_steps || [];
    if (steps.length) return steps[steps.length - 1];
  }
  return state.scenario ? state.scenario.maxStep : 0;
}

function entityName(obstacleId) {
  const map = (state.replayTimeline && state.replayTimeline.id_to_name)
    || (state.cosim && state.cosim.id_to_name);
  return (map && map[String(obstacleId)]) || `obstacle ${obstacleId}`;
}

function isEgo(obstacleId) {
  const ego = (state.replayTimeline && state.replayTimeline.ego)
    || (state.cosim && state.cosim.ego);
  return ego ? entityName(obstacleId) === ego : false;
}

function drawLanelets() {
  const road = css('--road');
  const line = css('--road-line');

  state.scenario.lanelets.forEach((l) => {
    if (l.left.length < 2 || l.right.length < 2) return;

    ctx.beginPath();
    l.left.forEach((p, i) => {
      const s = toScreen(p.x, p.y);
      i === 0 ? ctx.moveTo(s.x, s.y) : ctx.lineTo(s.x, s.y);
    });
    for (let i = l.right.length - 1; i >= 0; i -= 1) {
      const s = toScreen(l.right[i].x, l.right[i].y);
      ctx.lineTo(s.x, s.y);
    }
    ctx.closePath();
    ctx.fillStyle = road;
    ctx.fill();

    ctx.strokeStyle = line;
    ctx.lineWidth = 1;
    [l.left, l.right].forEach((bound) => {
      ctx.beginPath();
      bound.forEach((p, i) => {
        const s = toScreen(p.x, p.y);
        i === 0 ? ctx.moveTo(s.x, s.y) : ctx.lineTo(s.x, s.y);
      });
      ctx.stroke();
    });
  });
}

function drawGoals() {
  const goal = css('--goal');
  state.scenario.goals.forEach((g) => {
    if (!g.length || !g.width) return;
    const s = toScreen(g.x, g.y);
    ctx.save();
    ctx.translate(s.x, s.y);
    ctx.rotate(-g.orientation);
    ctx.fillStyle = goal + '33';
    ctx.strokeStyle = goal;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([6, 4]);
    const w = g.length * state.view.scale;
    const h = g.width * state.view.scale;
    ctx.fillRect(-w / 2, -h / 2, w, h);
    ctx.strokeRect(-w / 2, -h / 2, w, h);
    ctx.restore();
  });
  ctx.setLineDash([]);
}

function strokeTrail(states, colour, dashed) {
  ctx.save();
  ctx.strokeStyle = colour;
  ctx.lineWidth = dashed ? 1 : 1.5;
  if (dashed) ctx.setLineDash([4, 4]);
  ctx.beginPath();
  let started = false;
  states.forEach((s) => {
    if (s.step > state.step) return;
    const p = toScreen(s.x, s.y);
    started ? ctx.lineTo(p.x, p.y) : (ctx.moveTo(p.x, p.y), (started = true));
  });
  ctx.stroke();
  ctx.restore();
}

function drawTrails() {
  state.scenario.obstacles.forEach((obs) => {
    const colour = isEgo(obs.id) ? css('--ego') : css('--actor');

    // the run that is not on screen leaves a dashed trail, so the two paths
    // can be read against each other rather than one at a time
    if (state.cosim) {
      const other = state.run === 'cosim'
        ? obs.states
        : (state.cosimIndex && state.cosimIndex.get(obs.id));
      if (other) strokeTrail(other, css('--diff') + '66', true);
    }

    const active = state.run === 'cosim'
      ? (state.cosimIndex && state.cosimIndex.get(obs.id))
      : obs.states;
    if (active) strokeTrail(active, colour + '55', false);
  });
}

/** Separation, in metres, below which the two runs are drawn as coincident. */
const DIVERGENCE_EPS_M = 0.25;

function drawObstacles() {
  state.scenario.obstacles.forEach((obs) => {
    const st = obstacleStateAt(obs, state.step);
    if (!st) return;

    const s = toScreen(st.x, st.y);
    const ego = isEgo(obs.id);
    const colour = ego ? css('--ego') : css('--actor');

    // Where this actor is in the *other* run at the same instant. Drawing it
    // as a hollow ghost with a connector is the whole comparison: a number
    // saying "the brake fired 0.2 s earlier" does not show you that the ego
    // was 4 m further down the road when it happened.
    const other = otherRunStateAt(obs, state.step);
    const gap = other ? Math.hypot(st.x - other.x, st.y - other.y) : 0;
    if (other && gap > DIVERGENCE_EPS_M) {
      const g = toScreen(other.x, other.y);
      const w = obs.length * state.view.scale;
      const h = obs.width * state.view.scale;
      // one colour for "the other run", whichever actor it belongs to — the
      // ghost has to read as a *run*, not as a second vehicle
      const ghost = css('--diff');

      ctx.save();
      ctx.translate(g.x, g.y);
      ctx.rotate(-other.orientation);
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = ghost;
      ctx.globalAlpha = 0.85;
      ctx.strokeRect(-w / 2, -h / 2, w, h);
      ctx.restore();

      ctx.save();
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = ghost;
      ctx.lineWidth = 1.25;
      ctx.beginPath();
      ctx.moveTo(g.x, g.y);
      ctx.lineTo(s.x, s.y);
      ctx.stroke();
      ctx.restore();

      // only label a separation with room to print it; a cramped one is
      // already obvious from the two overlapping outlines
      if (Math.hypot(g.x - s.x, g.y - s.y) > 26) {
        ctx.fillStyle = ghost;
        ctx.font = '10px ui-monospace, monospace';
        ctx.textAlign = 'center';
        ctx.fillText(`Δ ${gap.toFixed(1)} m`,
                     (g.x + s.x) / 2, (g.y + s.y) / 2 - 14);
        ctx.textAlign = 'start';
      }
    }

    ctx.save();
    ctx.translate(s.x, s.y);
    ctx.rotate(-st.orientation);

    const w = obs.length * state.view.scale;
    const h = obs.width * state.view.scale;
    ctx.fillStyle = colour;
    ctx.globalAlpha = 1;
    ctx.fillRect(-w / 2, -h / 2, w, h);

    // heading wedge so orientation is readable at any zoom
    ctx.globalAlpha = 1;
    ctx.fillStyle = '#ffffffcc';
    ctx.beginPath();
    ctx.moveTo(w / 2, 0);
    ctx.lineTo(w / 2 - Math.min(8, w * 0.3), -h / 4);
    ctx.lineTo(w / 2 - Math.min(8, w * 0.3), h / 4);
    ctx.closePath();
    ctx.fill();
    ctx.restore();

    ctx.fillStyle = css('--text');
    ctx.font = '11px ui-monospace, monospace';
    ctx.textAlign = 'center';
    const label = entityName(obs.id) + (ego ? ' (ego)' : '');
    ctx.fillText(label, s.x, s.y - Math.max(h, 10) / 2 - 6);

    if (Number.isFinite(st.velocity)) {
      ctx.fillStyle = css('--text-dim');
      ctx.font = '10px ui-monospace, monospace';
      ctx.fillText(`${st.velocity.toFixed(1)} m/s`, s.x, s.y + Math.max(h, 10) / 2 + 12);
    }
  });
  ctx.textAlign = 'start';
}

function draw() {
  const rect = el.canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, rect.width, rect.height);
  if (!state.scenario) return;

  drawLanelets();
  drawGoals();
  drawTrails();
  drawObstacles();
  updateHud();
}

function updateHud() {
  if (!state.scenario) return;
  const t = (state.step * state.scenario.dt).toFixed(2);
  const noMap = state.scenario.lanelets.length === 0;
  el.hud.textContent =
    `${state.scenario.benchmarkId} · ${state.scenario.lanelets.length} lanelets · ` +
    `${state.scenario.obstacles.length} actors · t=${t}s`;
  // An empty lanelet network is easy to mistake for a rendering bug; name it.
  el.hud.classList.toggle('warn', noMap);
  el.hud.title = noMap
    ? 'This scenario has no lanelet network — the OpenDRIVE conversion failed. '
      + 'See stats.road_network in bundle.json for the reason.'
    : '';
}

function renderLegend() {
  const rows = [
    ['--ego', 'ego vehicle'],
    ['--actor', 'other actors'],
    ['--goal', 'goal region (Translation)'],
    ['--fire', 'event fired (Interpretation)'],
  ];
  if (state.cosim) {
    rows.push(['--diff', state.run === 'cosim'
      ? 'ghost: esmini run' : 'ghost: co-sim run']);
  }
  el.legend.innerHTML = rows
    .map(([v, label]) =>
      `<div><span class="swatch" style="background:${css(v)}"></span>${label}</div>`)
    .join('');
}

/* ------------------------------------------------------------------ */
/* trigger panel                                                       */
/* ------------------------------------------------------------------ */

/** Condition rows in timeline.json, keyed by condition name. */
function timelineByName() {
  const map = new Map();
  if (!state.timeline) return map;
  state.timeline.conditions.forEach((c) => {
    if (!map.has(c.name)) map.set(c.name, c);
  });
  return map;
}

function conditionHoldsNow(name) {
  const row = timelineByName().get(name);
  if (!row || !state.timeline) return null;
  const idx = state.timeline.time_steps.indexOf(state.step);
  if (idx < 0) return null;
  return row.values[idx] === 1;
}

function renderStats() {
  if (!state.triggers) { el.stats.innerHTML = ''; return; }
  const c = state.triggers.counts || {};
  const cells = [
    ['events', c.events],
    ['conditions', c.conditions],
    ['translation mapped', c.translation_mapped],
    ['translation skipped', c.translation_skipped],
    ['interpretation fires', c.interpretation_fired],
  ];
  el.stats.innerHTML = cells
    .map(([label, v]) => `<div class="stat"><b>${v ?? 0}</b><span>${label}</span></div>`)
    .join('');

  // Conditions the parser could not model leave their event with an empty
  // trigger, which fires unconditionally — say so rather than let the fire
  // count read as recovered reactivity.
  const cov = state.triggers.coverage;
  if (cov && cov.unsupported_conditions) {
    const types = Object.entries(cov.unsupported || {})
      .map(([t, n]) => `${t}×${n}`).join(', ');
    const uncond = c.interpretation_fired_unconditional || 0;
    el.stats.insertAdjacentHTML('beforeend',
      `<div class="coverage-warn" title="${escapeHtml(types)}">
         ⚠ ${cov.parsed_conditions}/${cov.source_conditions} conditions modelled
         (${cov.preserved_pct}%) — unsupported: ${escapeHtml(types)}
         ${uncond ? `· ${uncond} unconditional fire(s)` : ''}
       </div>`);
  }
}

/** Fire times of one event in the closed-loop run, if it has one. */
function cosimFiresFor(eventName) {
  if (!state.cosim) return null;
  const times = (state.cosim.events || [])
    .filter((e) => e.event === eventName)
    .map((e) => e.time_s);
  return times.length ? times : [];
}

/**
 * How this event moved between the two runs.
 *
 * Returns null when there is nothing to compare. `fired` / `never` are as
 * interesting as a shift: an event that only occurs under one of the two runs
 * is exactly the case a flattened scenario cannot express.
 */
function eventShift(eventName, replayTimes) {
  const cosimTimes = cosimFiresFor(eventName);
  if (cosimTimes === null) return null;
  const a = replayTimes.length ? replayTimes[0] : null;
  const b = cosimTimes.length ? cosimTimes[0] : null;
  if (a === null && b === null) return null;
  if (a === null) return { kind: 'only-cosim', b };
  if (b === null) return { kind: 'only-replay', a };
  const delta = b - a;
  return { kind: Math.abs(delta) < 0.05 ? 'same' : 'moved', a, b, delta };
}

function renderTriggers() {
  if (!state.triggers) {
    el.triggers.innerHTML = '<div class="empty">No trigger data.</div>';
    return;
  }

  const events = state.triggers.events || [];
  const sbTriggers = state.triggers.storyboard_triggers || [];

  if (!events.length && !sbTriggers.length) {
    el.triggers.innerHTML =
      '<div class="empty">This scenario declares no storyboard triggers.</div>';
    return;
  }

  const parts = events.map((ev) => {
    const fires = (ev.interpretation && ev.interpretation.fires) || [];
    const firedSteps = fires.map((f) => f.time_step);
    const firedNow = firedSteps.includes(state.step);
    const firedAlready = firedSteps.some((s) => s <= state.step);

    const conds = (ev.conditions || []).map((c) => {
      const holds = conditionHoldsNow(c.name);
      const cls = ['cond'];
      if (holds) cls.push('holds');

      const tags = [`<span class="tag b">${c.type.replace('Condition', '')}</span>`];

      // A condition we understand but cannot compute here must not look like
      // one that simply did not hold.
      if (c.unevaluable_reason) {
        tags.push(`<span class="tag declared" title="${escapeHtml(c.unevaluable_reason)}">`
          + 'declared · not evaluable</span>');
      }

      if (c.translation && c.translation.status) {
        const mapped = String(c.translation.status).startsWith('mapped');
        const detail = c.translation.time_step_interval
          ? ` ${c.translation.time_step_interval[0]}..${c.translation.time_step_interval[1] ?? ''}`
          : '';
        tags.push(
          `<span class="tag ${mapped ? 'translation' : 'translation-skip'}" title="${c.translation.reason || ''}">` +
          `translation:${mapped ? 'mapped' + detail : 'skipped'}</span>`);
      }
      if (holds !== null) {
        tags.push(`<span class="tag ${holds ? 'live-true' : 'live-false'}">` +
          `${holds ? 'holds now' : 'not held'}</span>`);
      }

      return `<div class="${cls.join(' ')}">
          <div class="cond-text">${escapeHtml(c.text || c.name)}</div>
          <div class="cond-meta">${tags.join('')}</div>
        </div>`;
    }).join('');

    const unconditional = !(ev.conditions || []).length;
    const fireTags = fires.length
      ? `<span class="tag interpretation">fires @ ${fires.map((f) => f.time_s + 's').join(', ')}</span>` +
        (unconditional
          ? '<span class="tag c-skip" title="This event has no parsed start ' +
            'condition, and an empty trigger is unconditionally true in ' +
            'OpenSCENARIO — the fire is not evidence of a recovered trigger.">' +
            'unconditional</span>'
          : '')
      : '<span class="tag">never fires in this run</span>';

    // What the closed loop did to this event, stated next to what the replay
    // did — the two numbers side by side are the point of having both runs.
    const shift = eventShift(ev.name, fires.map((f) => f.time_s));
    let shiftTag = '';
    if (shift && shift.kind === 'moved') {
      const sign = shift.delta > 0 ? '+' : '−';
      shiftTag = `<span class="tag shift" title="esmini ${shift.a}s → co-sim `
        + `${shift.b}s">co-sim ${shift.b}s (${sign}${Math.abs(shift.delta).toFixed(1)}s)</span>`;
    } else if (shift && shift.kind === 'same') {
      shiftTag = `<span class="tag shift-same" title="both runs fire it at the `
        + `same instant">co-sim: unchanged</span>`;
    } else if (shift && shift.kind === 'only-cosim') {
      shiftTag = `<span class="tag shift" title="this event does not occur when `
        + `esmini drives the ego — only under the planner">only under co-sim `
        + `@ ${shift.b}s</span>`;
    } else if (shift && shift.kind === 'only-replay') {
      shiftTag = '<span class="tag shift" title="the planner never brought the '
        + 'world into the state this event needs">not reached under co-sim</span>';
    }

    const cls = ['event'];
    if (firedNow) cls.push('fired-now');
    else if (firedAlready) cls.push('has-fired');

    return `<div class="${cls.join(' ')}">
        <div class="event-head">
          <span class="event-name">${escapeHtml(ev.name)}</span>
          <span class="event-path">${escapeHtml(ev.story || '')}/${escapeHtml(ev.act || '')}</span>
        </div>
        <div class="event-actors">actors: ${escapeHtml((ev.actors || []).join(', ') || '—')} ${fireTags}${shiftTag}</div>
        ${conds}
      </div>`;
  });

  if (sbTriggers.length) {
    const rows = sbTriggers.map((t) => {
      const status = (t.translation && t.translation.status) || 'unmapped';
      const mapped = String(status).startsWith('mapped');
      const win = t.translation && t.translation.time_step_interval
        ? ` ${t.translation.time_step_interval[0]}..${t.translation.time_step_interval[1] ?? ''}`
        : '';
      return `<div class="cond">
          <div class="cond-text">${escapeHtml(t.name)}</div>
          <div class="cond-meta">
            <span class="tag ${mapped ? 'translation' : 'translation-skip'}">${status}${win}</span>
          </div>
        </div>`;
    }).join('');
    parts.push(`<div class="event">
        <div class="event-head"><span class="event-name">Act / storyboard triggers</span></div>
        ${rows}
      </div>`);
  }

  el.triggers.innerHTML = parts.join('');
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ------------------------------------------------------------------ */
/* timeline strips + fire markers                                      */
/* ------------------------------------------------------------------ */

function renderStrips() {
  el.strips.innerHTML = '';
  if (!state.timeline || !state.timeline.conditions.length) return;

  state.timeline.conditions.forEach((cond) => {
    const row = document.createElement('div');
    row.className = 'strip-row';
    row.dataset.key = cond.key;

    const label = document.createElement('div');
    label.className = 'strip-label';
    label.textContent = cond.name;
    label.title = `${cond.type} — ${cond.event || cond.scope}`;

    const canvas = document.createElement('canvas');
    canvas.className = 'strip-canvas';

    row.append(label, canvas);
    el.strips.append(row);
    drawStrip(canvas, cond);
  });
}

function drawStrip(canvas, cond) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(rect.width, 1);
  const h = 12;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const c = canvas.getContext('2d');
  c.setTransform(dpr, 0, 0, dpr, 0, 0);

  c.clearRect(0, 0, w, h);
  const n = cond.values.length;
  if (!n) return;

  const bw = w / n;
  c.fillStyle = css('--goal');
  cond.values.forEach((v, i) => {
    if (v) c.fillRect(i * bw, 0, Math.max(bw, 1), h);
  });

  // playhead
  const idx = state.timeline.time_steps.indexOf(state.step);
  if (idx >= 0) {
    c.fillStyle = css('--text');
    c.fillRect(idx * bw, 0, Math.max(1.5, bw * 0.3), h);
  }
}

function redrawStrips() {
  if (!state.timeline) return;
  const rows = el.strips.querySelectorAll('.strip-row');
  rows.forEach((row) => {
    const cond = state.timeline.conditions.find((c) => c.key === row.dataset.key);
    if (!cond) return;
    const idx = state.timeline.time_steps.indexOf(state.step);
    row.classList.toggle('active', idx >= 0 && cond.values[idx] === 1);
    drawStrip(row.querySelector('canvas'), cond);
  });
}

function drawFires() {
  const rect = el.fires.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(rect.width, 1);
  const h = 14;
  el.fires.width = Math.round(w * dpr);
  el.fires.height = Math.round(h * dpr);
  const c = el.fires.getContext('2d');
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, w, h);

  if (!state.scenario) return;
  const maxStep = runMaxStep() || 1;

  // Both runs are marked whenever both exist: solid for the run on screen,
  // hollow for the other. A trigger that moved shows up as two marks that do
  // not line up — the shift is the thing worth seeing, so it is drawn rather
  // than left to be worked out from the panel.
  const marks = (fires, active) => {
    fires.forEach((step) => {
      const x = (step / maxStep) * w;
      c.beginPath();
      c.moveTo(x, 2);
      c.lineTo(x + 4, h - 2);
      c.lineTo(x - 4, h - 2);
      c.closePath();
      if (active) {
        c.fillStyle = css('--fire');
        c.fill();
      } else {
        c.strokeStyle = css('--diff');
        c.lineWidth = 1;
        c.stroke();
      }
    });
  };

  if (state.triggers) marks(replayFireSteps(), state.run === 'replay');
  if (state.cosim) marks(cosimFireSteps(), state.run === 'cosim');
}

function replayFireSteps() {
  const out = [];
  ((state.triggers && state.triggers.events) || []).forEach((ev) => {
    ((ev.interpretation && ev.interpretation.fires) || []).forEach((f) => out.push(f.time_step));
  });
  return out;
}

function cosimFireSteps() {
  return ((state.cosim && state.cosim.events) || []).map((e) => e.time_step);
}

/* ------------------------------------------------------------------ */
/* playback                                                            */
/* ------------------------------------------------------------------ */

function setStep(step) {
  if (!state.scenario) return;
  state.step = Math.max(0, Math.min(step, runMaxStep()));
  el.scrub.value = String(state.step);
  el.clockStep.textContent = `step ${state.step}`;
  el.clockTime.textContent = `${(state.step * state.scenario.dt).toFixed(2)} s`;
  draw();
  renderTriggers();
  redrawStrips();
  updateDiffbar();
}

let lastFrame = 0;
function tick(ts) {
  if (!state.playing) return;
  if (!lastFrame) lastFrame = ts;
  const elapsed = (ts - lastFrame) / 1000;
  const stepDuration = (state.scenario.dt || 0.1) / state.speed;

  if (elapsed >= stepDuration) {
    lastFrame = ts;
    if (state.step >= runMaxStep()) setPlaying(false);
    else setStep(state.step + 1);
  }
  if (state.playing) requestAnimationFrame(tick);
}

function setPlaying(on) {
  state.playing = on && !!state.scenario;
  el.playBtn.textContent = state.playing ? '❚❚' : '▶';
  lastFrame = 0;
  if (state.playing) requestAnimationFrame(tick);
}

/* ------------------------------------------------------------------ */
/* run switching                                                       */
/* ------------------------------------------------------------------ */

/**
 * Index the closed-loop run by obstacle id.
 *
 * The trace records motion per *entity name*, because that is what the
 * storyboard talks about; the canvas draws per CommonRoad *obstacle id*. The
 * bundle's id→name map is what joins them, and it is carried in the trace so
 * the viewer does not have to guess.
 */
function indexCosim(trace) {
  const byId = new Map();
  if (!trace || !trace.entity_trajectories) return byId;
  const nameToId = new Map();
  Object.entries(trace.id_to_name || {}).forEach(([id, name]) => nameToId.set(name, id));

  Object.entries(trace.entity_trajectories).forEach(([name, states]) => {
    const id = nameToId.get(name);
    if (id === undefined) return;
    const byStep = new Map();
    states.forEach((s) => byStep.set(s.step, {
      step: s.step, x: s.x, y: s.y, orientation: s.h, velocity: s.v,
    }));
    byId.set(id, byStep);
  });
  return byId;
}

function setRun(run) {
  if (run === 'cosim' && !state.cosim) return;
  state.run = run;
  el.runReplay.setAttribute('aria-pressed', String(run === 'replay'));
  el.runCosim.setAttribute('aria-pressed', String(run === 'cosim'));

  // Each run carries its own condition truth matrix; the strips must follow
  // the run on screen or they would describe a scenario nobody is watching.
  state.timeline = run === 'cosim' && state.cosim && state.cosim.timeline
    ? Object.assign({}, state.cosim.timeline, {
        id_to_name: state.cosim.id_to_name,
        ego: state.cosim.ego,
      })
    : state.replayTimeline;

  el.scrub.max = String(runMaxStep());
  setStep(Math.min(state.step, runMaxStep()));
  renderStrips();
  renderLegend();
  drawFires();
  updateDiffbar();

  setStatus(run === 'cosim'
    ? 'co-sim: the CommonRoad reactive planner drove the ego inside esmini'
    : 'esmini: the converted scenario, esmini driving every actor');
}

/** Live divergence readout: how far apart the two runs are, right now. */
function updateDiffbar() {
  if (!state.cosim || !state.scenario) {
    el.diffbar.hidden = true;
    return;
  }
  el.diffbar.hidden = false;

  const gaps = state.scenario.obstacles.map((obs) => {
    const a = replayStateAt(obs, state.step);
    const b = cosimStateAt(obs, state.step);
    if (!a || !b) return null;
    return { name: entityName(obs.id), d: Math.hypot(a.x - b.x, a.y - b.y) };
  }).filter(Boolean);

  const moved = gaps.filter((g) => g.d > DIVERGENCE_EPS_M)
    .sort((x, y) => y.d - x.d);

  const shifts = ((state.triggers && state.triggers.events) || [])
    .map((ev) => ({
      name: ev.name,
      s: eventShift(ev.name, ((ev.interpretation && ev.interpretation.fires) || []).map((f) => f.time_s)),
    }))
    .filter((r) => r.s && r.s.kind !== 'same');

  const planner = state.cosim.planner || {};
  const parts = [
    `<span class="diff-key">esmini</span> vs <span class="diff-key alt">co-sim</span>`,
    moved.length
      ? `now apart: ${moved.map((g) => `${escapeHtml(g.name)} ${g.d.toFixed(1)} m`).join(' · ')}`
      : 'positions coincide at this step',
  ];
  if (shifts.length) {
    parts.push(shifts.map((r) => {
      if (r.s.kind === 'moved') {
        const sign = r.s.delta > 0 ? '+' : '−';
        return `${escapeHtml(r.name)} ${sign}${Math.abs(r.s.delta).toFixed(1)}s`;
      }
      if (r.s.kind === 'only-cosim') return `${escapeHtml(r.name)} only under co-sim`;
      return `${escapeHtml(r.name)} not reached under co-sim`;
    }).join(' · '));
  } else {
    parts.push('no trigger moved');
  }
  if (planner.status) parts.push(`planner: ${escapeHtml(String(planner.status))}`);

  el.diffbar.innerHTML = parts.map((p) => `<span>${p}</span>`).join('');
}

/* ------------------------------------------------------------------ */
/* loading                                                             */
/* ------------------------------------------------------------------ */

async function loadBundle(name) {
  setStatus(`loading ${name} …`, 'busy');
  setPlaying(false);
  state.cosim = null;
  state.cosimIndex = null;
  state.run = 'replay';

  const bundles = await getJSON('/api/scenarios');
  state.bundle = bundles.bundles.find((b) => b.name === name) || { name };

  const xmlText = await fetch(`/api/bundle/${name}/scenario.xml`).then((r) => {
    if (!r.ok) throw new Error(`scenario.xml unavailable (${r.status})`);
    return r.text();
  });
  state.scenario = parseCommonRoad(xmlText);

  // Prefer the triggers carried inside the CommonRoad file. Falling back to
  // the sidecar is flagged loudly rather than silently: it means scenario.xml
  // carries no trigger block, so the triggers on screen may come from an
  // older conversion than the geometry.
  if (state.scenario.embeddedTriggers) {
    state.triggers = state.scenario.embeddedTriggers;
    state.triggerOrigin = 'from CommonRoad XML';
    el.origin.classList.remove('stale');
    el.origin.title = 'Triggers read from the embedded block in scenario.xml';
  } else {
    state.triggers = await getJSON(`/api/bundle/${name}/triggers.json`).catch(() => null);
    state.triggerOrigin = state.triggers ? '⚠ sidecar only' : 'unavailable';
    el.origin.classList.add('stale');
    el.origin.title = state.triggers
      ? 'scenario.xml has no embedded trigger block, so these came from '
        + 'triggers.json and may be from an earlier conversion. Re-convert '
        + 'this scenario to resync.'
      : 'No trigger data found for this bundle.';
  }
  el.origin.textContent = state.triggerOrigin;

  state.replayTimeline = await getJSON(`/api/bundle/${name}/timeline.json`)
    .catch(() => null);
  state.timeline = state.replayTimeline;

  // The closed-loop run is optional: a bundle only has one after
  // `osc2cr cosim <bundle> --driver planner`. Absent, the co-sim button stays
  // disabled and says why rather than failing when pressed.
  const runs = (state.bundle && state.bundle.cosim_runs) || {};
  const planner = runs.planner;
  let staleTrace = false;
  if (planner && planner.ok) {
    const trace = await getJSON(`/api/bundle/${name}/cosim_trace_planner.json`)
      .catch(() => null);
    const index = indexCosim(trace);
    if (index.size) {
      state.cosim = trace;
      state.cosimIndex = index;
    } else if (trace) {
      // A trace written before cosim.py recorded per-entity motion has fire
      // times but nothing to draw. Half-showing it — a divergence bar over
      // replay geometry — would be worse than not offering it at all.
      staleTrace = true;
    }
  }

  const haveCosim = !!state.cosim;
  el.runCosim.disabled = !haveCosim;
  el.runCosim.title = haveCosim
    ? 'Closed loop — the CommonRoad reactive planner drove the ego inside esmini'
    : staleTrace
      ? 'This closed-loop run carries no per-entity motion — it predates that '
        + 'being recorded. Press "Run co-sim" to redo it.'
      : planner
        ? `No usable closed-loop run: planner ${planner.status}`
          + (planner.reason ? ` — ${planner.reason}` : '')
          + '. Press "Run co-sim" to try again.'
        : 'No closed-loop run yet — press "Run co-sim" to produce one.';

  // Producing a run is always available once a bundle is loaded; the switch
  // above only chooses which existing run to draw.
  el.cosimBtn.disabled = false;
  el.cosimBtn.textContent = haveCosim ? 'Re-run co-sim' : 'Run co-sim';
  el.runReplay.setAttribute('aria-pressed', 'true');
  el.runCosim.setAttribute('aria-pressed', 'false');

  el.scrub.max = String(state.scenario.maxStep);
  state.view.fitted = false;
  fitView();

  renderStats();
  renderStrips();
  renderLegend();
  drawFires();
  updateDiffbar();
  setStep(0);

  const st = state.bundle.stats || {};
  setStatus(
    `${name}: ${st.obstacles ?? state.scenario.obstacles.length} actors, ` +
    `${st.events ?? 0} events, ${st.interpretation_fired ?? 0} fires` +
    (state.bundle.timings_s ? ` · converted in ${state.bundle.timings_s.total}s` : ''));
}

async function refreshBundles(selectName) {
  const { bundles } = await getJSON('/api/scenarios');
  el.scenarioSelect.innerHTML = bundles.length
    ? bundles.map((b) => `<option value="${b.name}">${b.name}</option>`).join('')
    : '<option value="">— none converted yet —</option>';

  if (selectName && bundles.some((b) => b.name === selectName)) {
    el.scenarioSelect.value = selectName;
  }
  return bundles;
}

async function refreshCorpus() {
  const { scenarios } = await getJSON('/api/corpus');
  el.corpusSelect.innerHTML =
    '<option value="">— pick a source file —</option>' +
    scenarios.map((s) => `<option value="${s.name}">${s.name}</option>`).join('');
}

/* ------------------------------------------------------------------ */
/* events                                                              */
/* ------------------------------------------------------------------ */

el.scenarioSelect.addEventListener('change', (e) => {
  if (e.target.value) loadBundle(e.target.value).catch((err) => setStatus(err.message, 'error'));
});

el.corpusSelect.addEventListener('change', (e) => {
  el.convertBtn.disabled = !e.target.value;
});

el.convertBtn.addEventListener('click', async () => {
  const name = el.corpusSelect.value;
  if (!name) return;
  el.convertBtn.disabled = true;
  setStatus(`converting ${name} … (esmini simulation, may take a few seconds)`, 'busy');
  try {
    const result = await postJSON('/api/convert', { xosc: name });
    setStatus(`converted ${name} in ${result.timings_s.total}s`);
    await refreshBundles(result.name);
    await loadBundle(result.name);
  } catch (err) {
    setStatus(`conversion failed: ${err.message}`, 'error');
  } finally {
    el.convertBtn.disabled = false;
  }
});

el.cosimBtn.addEventListener('click', async () => {
  const name = el.scenarioSelect.value;
  if (!name) return;
  const label = el.cosimBtn.textContent;
  el.cosimBtn.disabled = true;
  el.convertBtn.disabled = true;
  setStatus(`running ${name} closed-loop … (esmini + reactive planner, `
    + 'a few seconds)', 'busy');
  try {
    const result = await postJSON('/api/cosim', { scenario: name, driver: 'planner' });
    const sum = (result.differential && result.differential.summary) || null;
    const status = (result.planner && result.planner.status) || 'done';
    setStatus(`co-sim: planner ${status} after ${result.steps ?? '?'} steps`
      + (sum && sum.conclusive
        ? ` · ${sum.agreement_pct}% agreement (${sum.agree}/${sum.conclusive})`
        : ''));
    // The run rewrote the bundle, so reload it rather than patching state —
    // the trace, the embedded triggers and bundle.json all changed.
    await loadBundle(name);
    setRun('cosim');
  } catch (err) {
    setStatus(`co-sim failed: ${err.message}`, 'error');
  } finally {
    el.cosimBtn.disabled = false;
    el.cosimBtn.textContent = label;
    el.convertBtn.disabled = !el.corpusSelect.value;
  }
});

el.fitBtn.addEventListener('click', fitView);

el.runReplay.addEventListener('click', () => setRun('replay'));
el.runCosim.addEventListener('click', () => setRun('cosim'));

el.playBtn.addEventListener('click', () => setPlaying(!state.playing));
el.stepBack.addEventListener('click', () => setStep(state.step - 1));
el.stepFwd.addEventListener('click', () => setStep(state.step + 1));
el.speedSelect.addEventListener('change', (e) => { state.speed = parseFloat(e.target.value); });
el.scrub.addEventListener('input', (e) => setStep(parseInt(e.target.value, 10)));

// pan / zoom / drag
let panning = null;

el.canvas.addEventListener('mousedown', (e) => {
  if (!state.scenario) return;
  const rect = el.canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;

  panning = { sx, sy, tx: state.view.tx, ty: state.view.ty };
  el.canvas.classList.add('dragging');
});

el.canvas.addEventListener('mousemove', (e) => {
  const rect = el.canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;

  if (panning) {
    state.view.tx = panning.tx + (sx - panning.sx);
    state.view.ty = panning.ty + (sy - panning.sy);
    draw();
  }
});

window.addEventListener('mouseup', () => {
  panning = null;
  el.canvas.classList.remove('dragging');
});

el.canvas.addEventListener('wheel', (e) => {
  if (!state.scenario) return;
  e.preventDefault();
  const rect = el.canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;
  const before = toWorld(sx, sy);

  const factor = Math.exp(-e.deltaY * 0.0015);
  state.view.scale = Math.max(0.05, Math.min(200, state.view.scale * factor));

  const after = toWorld(sx, sy);
  state.view.tx += (after.x - before.x) * state.view.scale;
  state.view.ty -= (after.y - before.y) * state.view.scale;
  draw();
}, { passive: false });

window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT') return;
  if (e.code === 'Space') { e.preventDefault(); setPlaying(!state.playing); }
  if (e.code === 'ArrowRight') setStep(state.step + 1);
  if (e.code === 'ArrowLeft') setStep(state.step - 1);
  // R toggles between the two runs — the comparison is the main interaction
  if (e.code === 'KeyR' && state.cosim) {
    setRun(state.run === 'cosim' ? 'replay' : 'cosim');
  }
});

// Test hook: lets the viewer tests drive the run switch and read back what is
// on screen without guessing pixel positions. Read-only from the app's side.
window.__osc2cr = {
  state,
  toScreen,
  toWorld,
  setRun: (run) => setRun(run),
  runMaxStep,
  replayStateAt,
  cosimStateAt,
  eventShift,
};

const ro = new ResizeObserver(() => {
  resizeCanvas();
  drawFires();
  redrawStrips();
});
ro.observe(el.canvas);

/* ------------------------------------------------------------------ */
/* boot                                                                */
/* ------------------------------------------------------------------ */

(async function boot() {
  renderLegend();
  resizeCanvas();
  try {
    await refreshCorpus();
    const bundles = await refreshBundles();
    if (bundles.length) await loadBundle(bundles[0].name);
    else setStatus('no bundles yet — pick a .xosc above and press Convert');
  } catch (err) {
    setStatus(`startup failed: ${err.message}`, 'error');
  }
})();
