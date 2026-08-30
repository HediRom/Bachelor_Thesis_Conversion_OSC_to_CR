/*
 * Viewer smoke test
 * =================
 * Boots viewer/app.js inside jsdom against a real converted bundle and checks
 * that the pieces which are easy to break silently actually work:
 *
 *   - scenario.xml parses into lanelets / obstacles / goal regions
 *   - the triggers are read from the *embedded* block, not the sidecar
 *   - the trigger panel renders events and their conditions
 *   - the timeline scrubber spans the scenario and the condition strips exist
 *   - stepping updates the clock and the "holds now" state
 *
 * Run:  node tests/viewer_smoke.mjs [bundle-name]
 *
 * jsdom is not vendored here.  Install it anywhere and point JSDOM_DIR at the
 * containing folder, or install it next to this repo:
 *   npm install jsdom && node tests/viewer_smoke.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE_URL = fileURLToPath(import.meta.url);

/**
 * ESM ignores NODE_PATH, and recent jsdom releases are ESM-only (so plain
 * require() fails on Node 20).  Resolve the package path from a few likely
 * roots, then import it by file URL.
 */
async function loadJsdom() {
  const roots = [
    process.env.JSDOM_DIR,
    process.cwd(),
    path.resolve(path.dirname(HERE_URL), '..'),
  ].filter(Boolean);

  for (const root of roots) {
    try {
      const req = createRequire(path.join(root, 'noop.js'));
      const entry = req.resolve('jsdom');
      return await import(pathToFileURL(entry).href);
    } catch { /* try the next root */ }
  }
  console.error(
    'jsdom not found. Install it and retry, e.g.\n' +
    '  npm install jsdom\n' +
    '  JSDOM_DIR=/path/containing/node_modules node tests/viewer_smoke.mjs');
  return process.exit(2);
}

const { JSDOM } = await loadJsdom();

const HERE = path.dirname(HERE_URL);
const ROOT = path.resolve(HERE, '..');
const BUNDLE = process.argv[2] || 'cut-in_simple';
const BUNDLE_DIR = path.join(ROOT, 'output', BUNDLE);

let failures = 0;
const check = (label, cond, detail = '') => {
  const mark = cond ? '✓' : '✗';
  if (!cond) failures += 1;
  console.log(`  ${mark} ${label}${detail ? ` — ${detail}` : ''}`);
};

if (!fs.existsSync(path.join(BUNDLE_DIR, 'scenario.xml'))) {
  console.error(`No bundle at ${BUNDLE_DIR}. Convert one first:\n` +
    `  python -m osc2cr convert ${BUNDLE}`);
  process.exit(2);
}

/* ---------- fake backend ---------- */

const readBundle = (name) => fs.readFileSync(path.join(BUNDLE_DIR, name), 'utf8');
const manifest = JSON.parse(readBundle('bundle.json'));

const routes = {
  '/api/corpus': () => JSON.stringify({ scenarios: [{ name: BUNDLE, path: manifest.xosc_path }] }),
  '/api/scenarios': () => JSON.stringify({
    bundles: [{
      name: BUNDLE,
      xosc_path: manifest.xosc_path,
      stats: manifest.stats,
      timings_s: manifest.timings_s,
      files: ['scenario.xml', 'triggers.json', 'timeline.json'],
    }],
  }),
  [`/api/bundle/${BUNDLE}/scenario.xml`]: () => readBundle('scenario.xml'),
  [`/api/bundle/${BUNDLE}/triggers.json`]: () => readBundle('triggers.json'),
  [`/api/bundle/${BUNDLE}/timeline.json`]: () => readBundle('timeline.json'),
};

const fakeFetch = async (url) => {
  const route = String(url).split('?')[0];
  const handler = routes[route];
  if (!handler) {
    return { ok: false, status: 404, statusText: 'not found',
             json: async () => ({ error: 'not found' }), text: async () => '' };
  }
  const body = handler();
  return {
    ok: true,
    status: 200,
    json: async () => JSON.parse(body),
    text: async () => body,
  };
};

/* ---------- jsdom environment ---------- */

const dom = new JSDOM(fs.readFileSync(path.join(ROOT, 'viewer', 'index.html'), 'utf8'), {
  runScripts: 'outside-only',
  pretendToBeVisual: true,
  url: 'http://localhost:8000/',
});
const { window } = dom;

// canvas: record nothing, accept everything
const ctxStub = new Proxy({}, {
  get: (target, prop) => {
    if (prop in target) return target[prop];
    return typeof prop === 'string' ? () => {} : undefined;
  },
  set: (target, prop, value) => { target[prop] = value; return true; },
});
window.HTMLCanvasElement.prototype.getContext = () => ctxStub;

// jsdom returns a zero-sized box for everything; give the layout real numbers
window.Element.prototype.getBoundingClientRect = function rect() {
  const isStrip = this.classList && this.classList.contains('strip-canvas');
  return { width: isStrip ? 400 : 900, height: isStrip ? 12 : 600,
           top: 0, left: 0, right: 900, bottom: 600, x: 0, y: 0 };
};

window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
window.fetch = fakeFetch;
window.devicePixelRatio = 1;

// app.js is browser code — evaluate it with window as the global scope
window.eval(fs.readFileSync(path.join(ROOT, 'viewer', 'app.js'), 'utf8'));

// let boot() finish its awaits
await new Promise((r) => setTimeout(r, 400));

/* ---------- assertions ---------- */

const doc = window.document;
const $ = (sel) => doc.querySelector(sel);
const text = (sel) => ($(sel) || {}).textContent || '';

console.log(`\nViewer smoke test — bundle "${BUNDLE}"\n`);

console.log('loading');
check('status is not an error', !$('#status').classList.contains('error'), text('#status').slice(0, 70));
check('scenario selected', $('#scenario-select').value === BUNDLE);

console.log('\ntrigger source');
check('triggers came from the embedded CommonRoad block',
  text('#trigger-origin').includes('CommonRoad XML'), text('#trigger-origin'));

console.log('\ntrigger panel');
const events = manifest.stats.events || 0;
const eventCards = doc.querySelectorAll('.event').length;
check('renders one card per event (plus act-trigger card)',
  eventCards >= events, `${eventCards} cards for ${events} events`);
check('conditions rendered', doc.querySelectorAll('.cond').length > 0,
  `${doc.querySelectorAll('.cond').length} condition rows`);
check('stats tiles rendered', doc.querySelectorAll('.stat').length === 5);
check('event names appear', text('#triggers').length > 0);

console.log('\ntimeline');
const timeline = JSON.parse(readBundle('timeline.json'));
const maxStep = (manifest.stats.time_steps || 1) - 1;
check('scrubber spans the scenario', $('#scrub').max === String(maxStep),
  `max=${$('#scrub').max}, expected ${maxStep}`);
check('one strip per condition',
  doc.querySelectorAll('.strip-row').length === timeline.conditions.length,
  `${doc.querySelectorAll('.strip-row').length} strips`);

console.log('\nplayback');
const before = text('#clock-time');
$('#scrub').value = String(Math.min(66, maxStep));
$('#scrub').dispatchEvent(new window.Event('input'));
await new Promise((r) => setTimeout(r, 50));
const after = text('#clock-time');
check('scrubbing updates the clock', before !== after, `${before} → ${after}`);
check('step label follows', text('#clock-step').includes(String(Math.min(66, maxStep))));

// A condition that is true at this step should be reported as holding
const holdingNow = timeline.conditions.filter(
  (c) => c.values[timeline.time_steps.indexOf(Math.min(66, maxStep))] === 1);
if (holdingNow.length) {
  check('a holding condition is marked in the panel',
    doc.querySelectorAll('.cond.holds, .strip-row.active').length > 0,
    `${holdingNow.length} condition(s) true at this step`);
} else {
  console.log('  – no condition holds at this step; skipping holds check');
}

console.log(`\n${failures ? '✗' : '✓'} ${failures} failure(s)\n`);
process.exit(failures ? 1 : 0);
