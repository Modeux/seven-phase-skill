#!/usr/bin/env node
// Replays a walk kit (assets/walk-kit.md) in one process: login.js, then each step-*.js, on one page.
// The model reads summary.json and the screenshots afterwards instead of driving every step itself.
//
//   node run_kit.mjs --kit <kit dir> --round round5 [--steps step-01-drawer.js,step-13-...js | --plan plan.json]
//                    [--cfg '{"step-03-depart-threshold.js":{"motion":"reduce"}}'] [--base-url http://localhost:4312]
//                    [--fingerprint '<css selector>'] [--viewport 1280x800] [--headed] [--trace] [--step-timeout 240]
//                    [--repo <repo root>] [--evidence <dir>]
//
// plan.json: [{"step": "step-03-depart-threshold.js", "cfg": {"motion": "reduce"}}, ...] — the same step may repeat.
// Output: <evidence>/<round>/run/{summary.json,<n>-<step>.json,console.log[,trace.zip]}; stdout one line per step.
// Exit: 0 every step ok; 1 a step failed, threw, or returned ok:false; 2 setup failed (login, fingerprint, files).
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  if (i < 0) return def;
  const v = process.argv[i + 1];
  return v === undefined || v.startsWith('--') ? true : v;
}

const kit = path.resolve(arg('kit', ''));
const round = arg('round', '');
if (!arg('kit') || !round) {
  console.error('usage: --kit <dir> --round <name> [--steps a,b | --plan plan.json] (see header)');
  process.exit(2);
}
const repo = path.resolve(arg('repo', '') || execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim());
// WHY: evidence defaults to the kit's parent because the kit scripts already write screenshots to
// <walk dir>/<round>/ — the summary must sit next to the pictures it points at.
const evidence = path.resolve(arg('evidence', path.dirname(kit)));
const roundDir = path.join(evidence, round);
const runDir = path.join(roundDir, 'run');
fs.mkdirSync(runDir, { recursive: true });

const cfgAll = arg('cfg') ? JSON.parse(arg('cfg')) : {};
let plan;
if (arg('plan')) plan = JSON.parse(fs.readFileSync(arg('plan'), 'utf8'));
else {
  const s = arg('steps', 'all');
  const names = s === 'all' || s === true
    ? fs.readdirSync(kit).filter((f) => /^step-.*\.js$/.test(f)).sort()
    : String(s).split(',').map((x) => x.trim()).filter(Boolean);
  plan = names.map((step) => ({ step }));
}
plan = plan.map((p) => ({ ...p, cfg: { ...(cfgAll[p.step] || {}), ...(p.cfg || {}) } }));
for (const p of [{ step: 'login.js' }, ...plan]) {
  if (!fs.existsSync(path.join(kit, p.step))) { console.error(`missing ${p.step} in ${kit}`); process.exit(2); }
}

// INVARIANT: kit scripts stay `async (page) => {...}` expressions so the same file still runs through
// browser_run_code_unsafe in exploration. The runner only appends an Object.assign after the
// single-line `const CFG = {...};`; a multi-line CFG is left alone (the step then runs on its defaults).
function load(file, cfg) {
  let src = fs.readFileSync(path.join(kit, file), 'utf8').trim().replace(/;\s*$/, '');
  src = src.replace(/^(\s*const CFG = \{.*\};.*)$/m, '$1\n  Object.assign(CFG, globalThis.__WALK_CFG__ || {});');
  globalThis.__WALK_CFG__ = cfg;
  // eslint-disable-next-line no-new-func
  return new Function(`return (${src}\n);`)();
}

function pngs() {
  const out = new Map();
  const walk = (d) => {
    if (!fs.existsSync(d)) return;
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) { if (p !== runDir) walk(p); } else if (/\.png$/i.test(e.name)) out.set(p, fs.statSync(p).mtimeMs);
    }
  };
  walk(roundDir);
  return out;
}

function brief(v, n = 220) {
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return s && s.length > n ? `${s.slice(0, n)}…` : s;
}

const req = createRequire(path.join(repo, 'angular-workspace', 'package.json'));
const { chromium } = req('@playwright/test');
const [vw, vh] = String(arg('viewport', '1280x800')).split('x').map(Number);
const browser = await chromium.launch({ headless: !arg('headed') });
const context = await browser.newContext({ viewport: { width: vw, height: vh }, deviceScaleFactor: 1 });
if (arg('trace')) await context.tracing.start({ screenshots: true, snapshots: true });
const page = await context.newPage();
const consoleLog = fs.createWriteStream(path.join(runDir, 'console.log'));
page.on('console', (m) => { if (['error', 'warning'].includes(m.type())) consoleLog.write(`[${m.type()}] ${m.text()}\n`); });
page.on('pageerror', (e) => consoleLog.write(`[pageerror] ${e.message}\n`));

const timeoutMs = Number(arg('step-timeout', 240)) * 1000;
async function run(file, cfg) {
  const fn = load(file, { round, ...cfg });
  let timer;
  try {
    return await Promise.race([fn(page), new Promise((_, rej) => { timer = setTimeout(() => rej(new Error(`timeout ${timeoutMs / 1000}s`)), timeoutMs); })]);
  } finally { clearTimeout(timer); }
}

const summary = { round, kit, startedAt: new Date().toISOString(), steps: [] };
let setupFailed = false;
try {
  if (arg('base-url')) await page.goto(String(arg('base-url')));
  summary.login = await run('login.js', {});
  if (arg('fingerprint')) {
    const fp = String(arg('fingerprint'));
    summary.fingerprint = { selector: fp, found: (await page.locator(fp).count()) > 0, url: page.url() };
    if (!summary.fingerprint.found) throw new Error(`fingerprint ${fp} not on ${page.url()} — wrong tree or server`);
  }
} catch (e) {
  setupFailed = true;
  summary.setupError = String(e && e.stack || e);
  await page.screenshot({ path: path.join(runDir, 'setup-FAIL.png') }).catch(() => {});
}

if (!setupFailed) {
  for (const [i, p] of plan.entries()) {
    const tag = `${String(i + 1).padStart(2, '0')}-${p.step.replace(/\.js$/, '')}`;
    const before = pngs();
    const t0 = Date.now();
    const row = { n: i + 1, step: p.step, cfg: p.cfg };
    try {
      row.result = await run(p.step, p.cfg);
      row.status = row.result && row.result.ok === false ? 'FAIL' : row.result && row.result.ok === true ? 'PASS' : 'DONE';
    } catch (e) {
      row.status = 'ERROR';
      row.error = String(e && e.stack || e).slice(0, 2000);
      const shot = path.join(runDir, `${tag}-ERROR.png`);
      await page.screenshot({ path: shot }).catch(() => {});
      row.errorShot = shot;
    }
    row.seconds = Math.round((Date.now() - t0) / 100) / 10;
    row.screenshots = [...pngs()].filter(([f, m]) => before.get(f) !== m).map(([f]) => f);
    fs.writeFileSync(path.join(runDir, `${tag}.json`), JSON.stringify(row, null, 2));
    summary.steps.push(row);
    // WHY: one short line per step — the agent reads this stdout, the full JSON stays on disk.
    const head = row.error ? row.error.replace(/\x1b\[[0-9;]*m/g, '').split('\n')[0] : row.result;
    console.log(`${row.status.padEnd(5)} ${tag} ${row.seconds}s shots=${row.screenshots.length} ${brief(head)}`);
  }
}

if (arg('trace')) await context.tracing.stop({ path: path.join(runDir, 'trace.zip') });
await browser.close();
consoleLog.end();
summary.finishedAt = new Date().toISOString();
fs.writeFileSync(path.join(runDir, 'summary.json'), JSON.stringify(summary, null, 2));
const bad = summary.steps.filter((s) => s.status === 'FAIL' || s.status === 'ERROR');
const done = summary.steps.filter((s) => s.status === 'DONE');
if (setupFailed) console.log(`SETUP FAILED: ${brief(summary.setupError, 400)}`);
console.log(`summary ${path.join(runDir, 'summary.json')}  pass=${summary.steps.length - bad.length - done.length} done(no ok field)=${done.length} fail=${bad.length}`);
process.exit(setupFailed ? 2 : bad.length ? 1 : 0);
