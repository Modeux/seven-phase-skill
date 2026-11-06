// Shared browser helpers for walk kits and the agent's own node probes (assets/walk-kit.md).
//
//   import { launch } from '<skill>/scripts/walk_lib.mjs';
//   const { page, walk, close } = await launch({ kit: '<kit dir>', round: 'r1' });
//
// WHY: every ui-lane-reviewer run rewrote its login, launcher, screenshot paths and measurements (13 runs:
// login.js written 23 times, lib.mjs 11; a third of script lines repeated within one run; history 11-06).
// INVARIANT: only helpers used by more than one kit belong here. Page-specific steps, selectors and results stay
// in the kit, because stage 6 gets this library without them to keep its independent view (walk-kit.md).
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

export const DEFAULT_ACTION_TIMEOUT_MS = 10_000;
// INVARIANT: matches Playwright's own default; steps without `walk` run on it so live kits keep their timing.
export const PLAYWRIGHT_DEFAULT_TIMEOUT_MS = 30_000;
const NAVIGATION_TIMEOUT_MS = 30_000;

// Overridable from <kit>/walk.config.json.
const DEEPWEAVE_DEFAULTS = {
  // INVARIANT: the layout switch widths of the frontend; a width change there must change these.
  breakpoints: [840, 1180],
  loginPath: '/api/v1/auth/login',
  password: 'deepweave-demo-123',
  tokenKey: 'dw_token',
  userKey: 'dw_user',
  authSyncKey: 'dw_auth_sync',
  pg: 'deepweave-postgres',
  pgUser: 'deepweave',
  db: 'deepweave',
};
const MAIN_CHECKOUT = '/Users/huntwu/Desktop/deepweave/deepweave';

function loadPlaywright(repo) {
  const candidates = [repo, process.env.WALK_REPO];
  try {
    candidates.push(execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim());
  } catch { /* not in a git tree: the main checkout below still resolves */ }
  candidates.push(MAIN_CHECKOUT);
  for (const c of candidates.filter(Boolean)) {
    try {
      return createRequire(path.join(path.resolve(c), 'angular-workspace', 'package.json'))('@playwright/test');
    } catch { /* this tree has no install; try the next */ }
  }
  throw new Error(`@playwright/test not found under: ${candidates.filter(Boolean).join(', ')}`);
}

export function readConfig(kit, overrides = {}) {
  const file = kit && path.join(kit, 'walk.config.json');
  const fromKit = file && fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : {};
  const defined = Object.fromEntries(Object.entries(overrides).filter(([, v]) => v !== undefined && v !== null));
  return { ...DEEPWEAVE_DEFAULTS, ...fromKit, ...defined };
}

export async function launch({
  kit, round, roundDir, repo, engine, device, viewport = '1280x800', headed = false,
  storageState = true, actionTimeoutMs = DEFAULT_ACTION_TIMEOUT_MS, config = {}, onLog,
} = {}) {
  const resolvedRoundDir = roundDir || (kit && round ? path.join(path.dirname(path.resolve(kit)), round) : process.cwd());
  const pw = loadPlaywright(repo);
  const descriptor = device ? pw.devices[device] : null;
  if (device && !descriptor) throw new Error(`unknown device "${device}" (see Playwright's devices list)`);
  const engineName = engine || descriptor?.defaultBrowserType || 'chromium';
  if (!pw[engineName]?.launch) throw new Error(`unknown engine "${engineName}"`);

  const saved = path.join(resolvedRoundDir, 'run', 'storage-state.json');
  const statePath = typeof storageState === 'string' ? storageState : storageState && fs.existsSync(saved) ? saved : undefined;
  const [width, height] = String(viewport).split('x').map(Number);
  // WHY deviceScaleFactor 1: screenshot tokens scale with pixel area, and steps compare CSS pixels.
  const contextOptions = descriptor ? { ...descriptor } : { viewport: { width, height }, deviceScaleFactor: 1 };
  delete contextOptions.defaultBrowserType;
  if (statePath) contextOptions.storageState = statePath;

  const browser = await pw[engineName].launch({ headless: !headed });
  const context = await browser.newContext(contextOptions);
  context.setDefaultTimeout(actionTimeoutMs);
  context.setDefaultNavigationTimeout(NAVIGATION_TIMEOUT_MS);
  const page = await context.newPage();
  const errors = [];
  page.on('console', (m) => {
    if (['error', 'warning'].includes(m.type())) {
      if (m.type() === 'error') errors.push(m.text().slice(0, 300));
      onLog?.(`[${m.type()}] ${m.text()}`);
    }
  });
  page.on('pageerror', (e) => {
    errors.push(`pageerror: ${e.message}`.slice(0, 300));
    onLog?.(`[pageerror] ${e.message}`);
  });

  const cfg = readConfig(kit, config);
  const walk = makeWalk(page, { roundDir: resolvedRoundDir, cfg, errors, actionTimeoutMs });
  return { pw, browser, context, page, walk, errors, cfg, roundDir: resolvedRoundDir, close: () => browser.close() };
}

// Runs in the page, so it is passed to evaluate as source text.
const describe = (el) => {
  if (!el) return null;
  const id = el.id ? `#${el.id}` : '';
  const testid = el.getAttribute('data-testid');
  const cls = typeof el.className === 'string' ? el.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).map((c) => `.${c}`).join('') : '';
  return `${el.tagName.toLowerCase()}${id}${cls}${testid ? `[data-testid=${testid}]` : ''}`;
};
const inPage = (fn, arg) => `(() => { const describe = ${describe}; return (${fn})(${JSON.stringify(arg ?? null)}); })()`;

// WHY state on window: identical-looking controls have the same describe() text, so stops are compared by identity,
// and that needs the element references to stay in the page.
const tabLanding = ({ reset }) => {
  let el = document.activeElement;
  let dx = 0;
  let dy = 0;
  let opaque = false;
  while (el) {
    if (el.shadowRoot?.activeElement) { el = el.shadowRoot.activeElement; continue; }
    if (el.tagName !== 'IFRAME' && el.tagName !== 'FRAME') break;
    let inner = null;
    try { inner = el.contentDocument; } catch { /* cross-origin: same as null */ }
    if (!inner) { opaque = true; break; }
    const active = inner.activeElement;
    if (!active || active === inner.body) break;
    const f = el.getBoundingClientRect();
    dx += f.left;
    dy += f.top;
    el = active;
  }
  const atBody = !el || el === document.body || el === document.documentElement;
  if (reset) {
    window.__walkTab = { seen: [], prev: atBody ? null : el };
    return { start: atBody ? null : describe(el) };
  }
  const state = window.__walkTab;
  // INVARIANT: this order. Leaving the document also lands on <body>; only a <body> landing while the document keeps
  // focus is the focus-loss bug.
  if (!document.hasFocus()) return { kind: 'left' };
  if (atBody) return { kind: 'body' };
  if (el === state.prev) return { kind: 'stuck' };
  const earlier = state.seen.indexOf(el);
  if (earlier >= 0) return { kind: 'cycle', to: earlier };
  state.seen.push(el);
  state.prev = el;

  const r = el.getBoundingClientRect();
  const clipped = () => {
    if (getComputedStyle(el).position === 'fixed') return false;
    for (let p = el.parentElement; p; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
        const c = p.getBoundingClientRect();
        if (r.right <= c.left || r.left >= c.right || r.bottom <= c.top || r.top >= c.bottom) return true;
      }
      if (cs.position === 'fixed') return false;
    }
    return false;
  };
  const view = el.ownerDocument.defaultView;
  // WHY more than checkVisibility: visually-hidden 1px elements, off-canvas panels and collapsed sections all render
  // by its rules, and they are the invisible focus targets a keyboard user actually loses track of.
  const visible = el.checkVisibility({ opacityProperty: true, visibilityProperty: true, checkOpacity: true, checkVisibilityCSS: true })
    && r.width > 1 && r.height > 1
    && r.right > 0 && r.bottom > 0 && r.left < view.innerWidth && r.top < view.innerHeight
    && !clipped();
  const clean = (t) => (t || '').trim().replace(/\s+/g, ' ').slice(0, 80);
  const labelledBy = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
    .map((id) => el.ownerDocument.getElementById(id)?.textContent || '').join(' ');
  const name = clean(el.getAttribute('aria-label')) || clean(labelledBy)
    || clean([...(el.labels || [])].map((l) => l.textContent).join(' ')) || clean(el.textContent)
    || clean(el.querySelector('img[alt]')?.alt) || clean(el.title) || clean(el.placeholder);
  return {
    kind: opaque ? 'frame' : 'stop',
    el: describe(el),
    name,
    visible,
    ariaHidden: !!el.closest('[aria-hidden="true"]'),
    rect: { x: r.left + dx, y: r.top + dy, width: r.width, height: r.height },
  };
};

// WHY three candidates: agents write '/x', 'POST /x', ':7522' and /^\/x/ (corpus), and each should mean what it says.
function netMatcher(match) {
  if (match === undefined || match === null) return () => true;
  if (typeof match === 'function') return (row) => !!match(row);
  // WHY rebuilt: a /g or /y regex carries lastIndex between tests, so the same question would alternate answers.
  const re = match instanceof RegExp ? new RegExp(match.source, match.flags.replace(/[gy]/g, '')) : null;
  if (!re && (typeof match !== 'string' || !match)) throw new TypeError(`walk.net: matcher must be a non-empty string, RegExp or function, got ${String(match)}`);
  const test = re ? (text) => re.test(text) : (text) => text.includes(match);
  return ({ method, url, origin }) => test(`${method} ${url}`) || test(url) || test(`${origin}${url}`);
}

function splitUrl(raw) {
  try {
    const u = new URL(raw);
    if (u.origin !== 'null') return { origin: u.origin, url: `${u.pathname}${u.search}${u.hash}` };
  } catch { /* not parseable: keep the raw text below */ }
  return { origin: '', url: raw.slice(0, 200) };
}

const failureText = (e) => String(e?.message ?? e).replace(/\x1b\[[0-9;]*m/g, '').slice(0, 500);

function makeWalk(page, { roundDir, cfg, errors, actionTimeoutMs = DEFAULT_ACTION_TIMEOUT_MS }) {
  const locate = (target) => (typeof target === 'string' ? page.locator(target) : target);
  const round1 = (n) => Math.round(n * 10) / 10;
  const settle = () => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  let auth = null;

  const walk = {
    cfg,
    errors,
    settle,

// WHY at, tabOrder, net, scroll: 38 to 44 of 54 ui-lane runs rewrote each of these in 14 days (250 to 459 scripts apiece).
    async at(widths, fn, options) {
      if (typeof widths === 'function') { options = fn; fn = widths; widths = null; }
      const { height } = options || {};
      const vp = page.viewportSize();
      if (!vp) throw new Error('walk.at: this page has no fixed viewport to restore');
      if (typeof fn !== 'function') throw new Error('walk.at: fn must be a function');
      const isSize = (n) => Number.isInteger(n) && n > 0;
      if (height !== undefined && !isSize(height)) throw new Error(`walk.at: invalid height ${height}`);
      // WHY bp ± 1: frontend queries are written as both max-width: Npx and min-width: Npx, and only these two widths
      // land on opposite sides of N for either form.
      const list = widths ?? [...new Set([...cfg.breakpoints.flatMap((bp) => [bp - 1, bp + 1]), vp.width])].sort((a, b) => a - b);
      // INVARIANT: an empty list fails here. Returning [] would let `rows.every(...)` pass with nothing measured.
      if (!Array.isArray(list) || !list.length || !list.every(isSize)) throw new Error(`walk.at: widths must be a non-empty list of positive integers, got ${JSON.stringify(list)}`);
      const rows = [];
      for (const width of list) {
        try {
          await page.setViewportSize({ width, height: height ?? vp.height });
          await settle();
          rows.push({ width, result: await fn(width) });
        } catch (e) {
          rows.push({ width, error: failureText(e) });
        }
      }
      try {
        await page.setViewportSize(vp);
        await settle();
      } catch (e) {
        // WHY throw: later steps would measure at the wrong width with nothing to say so. Rows travel on the error.
        throw Object.assign(new Error(`walk.at: viewport not restored to ${vp.width}x${vp.height}: ${failureText(e)}; rows: ${JSON.stringify(rows).slice(0, 500)}`), { rows });
      }
      return rows;
    },

    async tabOrder({ from, max = 30, shift = false } = {}) {
      if (!Number.isInteger(max) || max < 1) throw new Error(`walk.tabOrder: max must be a positive integer, got ${max}`);
      if (from) {
        const start = locate(from);
        await start.focus();
        // WHY: focus() on a non-focusable element is a silent no-op, and the walk would start from the top of the page.
        if (!(await start.evaluate((el) => el.matches(':focus')))) throw new Error(`walk.tabOrder: from ${String(from)} did not take focus`);
      }
      // WHY Alt: plain Tab on macOS WebKit skips buttons and links, which would report a short order as the page's own.
      const macWebkit = process.platform === 'darwin' && page.context().browser()?.browserType().name() === 'webkit';
      const key = `${macWebkit ? 'Alt+' : ''}${shift ? 'Shift+' : ''}Tab`;
      const out = { start: null, stops: [], leftDocument: false, cycled: false, cycledTo: null, droppedToBody: false, stuck: false, truncated: false, invisible: [], ariaHidden: [] };
      try {
        ({ start: out.start } = await page.evaluate(inPage(tabLanding, { reset: true })));
        let ended = false;
        for (let i = 0; i < max && !ended; i++) {
          await page.keyboard.press(key);
          await settle();
          const hit = await page.evaluate(inPage(tabLanding, {}));
          ended = hit.kind !== 'stop';
          if (hit.kind === 'left') out.leftDocument = true;
          if (hit.kind === 'body') out.droppedToBody = true;
          if (hit.kind === 'stuck') out.stuck = true;
          if (hit.kind === 'cycle') { out.cycled = true; out.cycledTo = hit.to; }
          if (hit.kind === 'stop' || hit.kind === 'frame') {
            const { el, name, visible, rect, ariaHidden } = hit;
            out.stops.push({ i, el, name, visible, rect: { x: round1(rect.x), y: round1(rect.y), w: round1(rect.width), h: round1(rect.height) } });
            if (!visible) out.invisible.push(i);
            if (ariaHidden) out.ariaHidden.push(i);
          }
          if (hit.kind === 'frame') out.leftDocument = true;
        }
        out.truncated = !ended;
      } finally {
        await page.evaluate(() => { delete window.__walkTab; }).catch(() => {});
      }
      return out;
    },

    net({ match } = {}) {
      const base = netMatcher(match);
      const records = new Map();
      const waiters = new Set();
      let ended = page.isClosed() ? 'page closed' : null;
      const record = (request, startedAt = null) => {
        if (!records.has(request)) {
          const { origin, url } = splitUrl(request.url());
          records.set(request, { origin, startedAt, response: null, row: { method: request.method(), url, status: null, ms: null, failed: false, pending: true } });
        }
        return records.get(request);
      };
      const view = (r) => ({ ...r.row, origin: r.origin });
      const query = (m) => {
        const test = netMatcher(m);
        return [...records.values()].filter((r) => base(view(r)) && test(view(r))).map((r) => ({ ...r.row }));
      };
      const elapse = (r) => (r.startedAt === null ? null : Date.now() - r.startedAt);
      // INVARIANT: page events run these with nobody to catch a rejection, and a page can close at any point. So they
      // stay synchronous and never call an async Playwright method; that keeps a closing page from crashing the process.
      const onRequest = (request) => { record(request, Date.now()); };
      const onResponse = (response) => {
        const r = record(response.request());
        Object.assign(r.row, { status: response.status(), pending: false, ms: elapse(r) });
        r.response = response;
        for (const w of [...waiters]) w.seen(r);
      };
      const onFailed = (request) => {
        const r = record(request);
        Object.assign(r.row, { failed: request.failure()?.errorText || 'failed', pending: false, ms: r.row.ms ?? elapse(r) });
      };
      const finish = (reason) => {
        ended = ended || reason;
        for (const w of [...waiters]) w.end(new Error(`walk.net wait: ${reason}`));
        page.off('request', onRequest);
        page.off('response', onResponse);
        page.off('requestfailed', onFailed);
        page.off('close', onClose);
      };
      const onClose = () => finish('page closed');
      if (!ended) {
        page.on('request', onRequest);
        page.on('response', onResponse);
        page.on('requestfailed', onFailed);
        page.on('close', onClose);
      }
      return {
        list: (m) => query(m),
        count: (m) => query(m).length,
        // WHY not reject on the first failure: switchMap and retry interceptors fail one request and answer the next.
        // WHY not return an earlier response: it would pass a step whose action never fired.
        wait(m, { timeout = actionTimeoutMs } = {}) {
          const waiting = new Promise((resolve, reject) => {
            const test = netMatcher(m);
            if (!Number.isFinite(timeout) || timeout <= 0) throw new RangeError(`walk.net wait: timeout must be a positive number of ms, got ${timeout}`);
            if (ended) throw new Error(`walk.net wait: ${ended}`);
            const w = {
              timer: setTimeout(() => {
                // INVARIANT: a matcher can throw here too, and a throw from a timer ends the process.
                try {
                  const rows = query(m);
                  const failed = rows.filter((r) => r.failed);
                  const answered = rows.filter((r) => r.status !== null).length;
                  const hint = answered ? ' (arm wait before the action)' : '';
                  w.end(Object.assign(new Error(`walk.net wait(${String(m).slice(0, 80)}): no new response in ${timeout}ms; `
                    + `${answered} answered before this wait${hint}, ${rows.filter((r) => r.pending).length} pending, `
                    + `${failed.length} failed${failed.length ? ` (${failed[0].failed})` : ''}`), { rows }));
                } catch (e) { w.end(e); }
              }, timeout),
              seen(r) {
                try {
                  if (!base(view(r)) || !test(view(r))) return;
                } catch (e) { w.end(e); return; }
                clearTimeout(w.timer);
                waiters.delete(w);
                resolve(Object.defineProperty({ ...r.row }, 'response', { value: r.response, enumerable: false }));
              },
              end(e) {
                clearTimeout(w.timer);
                waiters.delete(w);
                reject(e);
              },
            };
            waiters.add(w);
          });
          // INVARIANT: steps arm a wait before an action that can throw, and may never reach `await`. Node ends the
          // process on an unhandled rejection; this marks it handled and an awaiting caller still gets the rejection.
          waiting.catch(() => {});
          return waiting;
        },
        stop() { finish('recording stopped'); },
      };
    },

    async scroll(target, to) {
      if (target === 'top' || target === 'bottom' || typeof target === 'number') {
        throw new Error(`walk.scroll(${target}): the first argument is the target; for the document use walk.scroll(null, ${JSON.stringify(target)})`);
      }
      const named = to !== undefined && to !== null;
      if (named && to !== 'top' && to !== 'bottom' && !(Number.isFinite(to) && to >= 0)) {
        throw new Error(`walk.scroll: to must be 'top', 'bottom' or a number >= 0, got ${String(to)}`);
      }
      let handle;
      if (target === undefined || target === null) {
        handle = await page.evaluateHandle(() => document.scrollingElement || document.documentElement);
      } else {
        const found = locate(target);
        const n = await found.count();
        if (n > 1) throw new Error(`walk.scroll(${String(target)}): matched ${n} elements; a wrong scroller measures quietly, so narrow it`);
        if (!n) return { found: false, scrollable: false, overflowY: null, top: null, height: null, client: null, max: null, atTop: null, atBottom: null };
        handle = await found.elementHandle();
      }
      try {
        if (named) {
          // WHY instant: under CSS scroll-behavior: smooth the measure below would read the start of the animation.
          await handle.evaluate((el, t) => el.scrollTo({ top: t === 'bottom' ? el.scrollHeight : t === 'top' ? 0 : t, behavior: 'instant' }), to);
          await settle();
        }
        const m = await handle.evaluate((el) => {
          const isRoot = el === (document.scrollingElement || document.documentElement);
          const own = getComputedStyle(el).overflowY;
          // WHY: <body>'s overflow propagates to the viewport, so a scroll-locked page shows it there and not on <html>.
          const overflowY = isRoot && own === 'visible' && document.body ? getComputedStyle(document.body).overflowY : own;
          return { overflowY, isRoot, top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight };
        });
        const max = m.height - m.client;
        // WHY isRoot aside: overflow:visible on anything else has content past its box and a non-zero max, yet cannot move.
        const scrollable = max > 0 && (m.isRoot || !['visible', 'clip'].includes(m.overflowY));
        return {
          found: true,
          scrollable,
          overflowY: m.overflowY,
          top: round1(m.top),
          height: m.height,
          client: m.client,
          max,
          atTop: scrollable ? m.top <= 0 : null,
          // WHY 1px: scrollTop is fractional on high-density screens while scrollHeight is rounded.
          atBottom: scrollable ? max - m.top <= 1 : null,
        };
      } finally {
        await handle.dispose().catch(() => {});
      }
    },

    // target: selector, Locator, { clip }, { fullPage: true }, or nothing for the viewport.
    // WHY element by default: image tokens scale with area and every opened screenshot stays in the agent's context.
    async shot(name, target) {
      fs.mkdirSync(roundDir, { recursive: true });
      const file = path.join(roundDir, `${name}.png`);
      if (target && (target.clip || target.fullPage)) await page.screenshot({ path: file, ...target });
      else if (target) await locate(target).first().screenshot({ path: file });
      else await page.screenshot({ path: file });
      return file;
    },

    async rect(target) {
      const box = await locate(target).first().count() ? await locate(target).first().boundingBox() : null;
      return box && { x: round1(box.x), y: round1(box.y), w: round1(box.width), h: round1(box.height) };
    },

    style(target, props) {
      return locate(target).first().evaluate((el, ps) => {
        const cs = getComputedStyle(el);
        return Object.fromEntries(ps.map((p) => [p, cs.getPropertyValue(p)]));
      }, props);
    },

    overflow() {
      return page.evaluate(inPage(() => {
        const vw = document.documentElement.clientWidth;
        const offenders = [];
        const listed = [];
        for (const el of document.querySelectorAll('body *')) {
          const r = el.getBoundingClientRect();
          // WHY right > 0: wholly left of the viewport adds no scroll and shows nothing (CDK's 1px focus-trap anchors).
          if (!r.width || !r.height || (r.right <= vw + 0.5 && (r.left >= -0.5 || r.right <= 0))) continue;
          if (listed.some((o) => o.contains(el))) continue;
          // WHY: neither a rail that scrolls or clips its own content nor a closed off-canvas drawer (fixed, so it
          // adds no page scroll) is page overflow. On the real sidebar these filled the list on a clean page.
          let node = el;
          let contained = getComputedStyle(el).position === 'fixed';
          while (!contained && node.parentElement && node.parentElement !== document.body) {
            node = node.parentElement;
            const cs = getComputedStyle(node);
            contained = cs.overflowX !== 'visible' || cs.position === 'fixed';
          }
          if (contained) continue;
          listed.push(el);
          offenders.push({ el: describe(el), left: Math.round(r.left), right: Math.round(r.right) });
          if (offenders.length >= 10) break;
        }
        const scrollWidth = document.documentElement.scrollWidth;
        return { viewport: vw, scrollWidth, overflowing: scrollWidth > vw, offenders };
      }));
    },

    // Whether a click at the element's centre would reach it, and if not what is on top.
    async hit(target) {
      const handle = await locate(target).first().elementHandle();
      return handle.evaluate((el, src) => {
        // eslint-disable-next-line no-new-func
        const name = new Function(`return (${src});`)();
        const r = el.getBoundingClientRect();
        const x = r.left + r.width / 2;
        const y = r.top + r.height / 2;
        const top = document.elementFromPoint(x, y);
        const ok = !!top && (top === el || el.contains(top));
        return { ok, point: [Math.round(x), Math.round(y)], covering: ok ? null : name(top) };
      }, describe.toString());
    },

    // Presses Enter on every visible button in a state and reports each one whose activation drops focus to <body>;
    // then, per breakpoint, leaves focus on it and crosses that width. A `navigated` or `crossed` row is judged
    // separately in the report (walk-kit.md). `enter` brings the page back to the state and runs before every press.
    async focusProbe(state, enter, breakpoints = cfg.breakpoints) {
      const sel = 'button:visible:enabled, [role="button"]:visible:not([aria-disabled="true"])';
      const lost = () => page.evaluate(() => (document.activeElement && document.activeElement !== document.body
        ? null : document.activeElement ? 'BODY' : 'null'));
      const vp = page.viewportSize();
      await enter();
      const names = await page.locator(sel).evaluateAll((els) => els.map((e) =>
        (e.getAttribute('aria-label') || e.textContent || e.title || '').trim().replace(/\s+/g, ' ')));
      const drops = [];
      for (let i = 0; i < names.length; i++) {
        const button = names[i] || `#${i}`;
        if (i) await enter();
        const url = page.url();
        await page.locator(sel).nth(i).focus();
        await page.keyboard.press('Enter');
        await settle();
        const active = await lost();
        if (active) drops.push({ state, button, active, navigated: page.url() !== url });
        for (const bp of breakpoints) {
          await enter();
          await page.locator(sel).nth(i).focus();
          const width = vp.width > bp ? bp - 1 : bp + 1;
          await page.setViewportSize({ width, height: vp.height });
          await settle();
          const crossedActive = await lost();
          await page.setViewportSize(vp);
          await settle();
          if (crossedActive) drops.push({ state, button, active: crossedActive, crossed: `${vp.width}→${width}` });
        }
      }
      return drops;
    },

    // Signs in through the API and stores the session before the next page load, so no step depends on the login form.
    async signIn(email, { password = cfg.password } = {}) {
      const res = await page.request.post(`${cfg.api}${cfg.loginPath}`, { data: { email, password } });
      const body = await res.json().catch(() => null);
      if (!body?.data?.token) throw new Error(`signIn ${email}: HTTP ${res.status()}`);
      auth = body.data;
      await page.addInitScript(([keys, token, user]) => {
        try {
          localStorage.setItem(keys.tokenKey, token);
          localStorage.setItem(keys.userKey, JSON.stringify(user));
          localStorage.removeItem(keys.authSyncKey);
        } catch { /* opaque origin: nothing to store on this document */ }
      }, [{ tokenKey: cfg.tokenKey, userKey: cfg.userKey, authSyncKey: cfg.authSyncKey }, auth.token, auth.user]);
      return auth;
    },

    // Calls the gateway as the signed-in user. For reading state a step needs, not for driving the page.
    async api(apiPath, { method = 'GET', data } = {}) {
      const res = await page.request.fetch(`${cfg.api}${apiPath}`, {
        method,
        data,
        headers: auth ? { Authorization: `Bearer ${auth.token}` } : {},
      });
      return { status: res.status(), body: await res.json().catch(() => null) };
    },

    // WHY the idle wait first: leaving a page mid-load cancels its API calls, pgx drops each cancelled connection,
    // and the gateway's 25-connection pool stalls rebuilding them (first symptom: a blank page for 20 s).
    async go(target, waitFor) {
      await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
      await page.goto(/^https?:/.test(target) ? target : `${cfg.app}${target}`, { waitUntil: 'domcontentloaded' });
      if (waitFor) await page.locator(waitFor).first().waitFor();
    },

    // Runs against the configured container and database; a walk with its own database sets `db` in walk.config.json.
    sql(query, { db = cfg.db } = {}) {
      return execFileSync('docker', ['exec', '-i', cfg.pg, 'psql', '-v', 'ON_ERROR_STOP=1', '-U', cfg.pgUser,
        '-d', db, '-At', '-F', '|', '-c', query], { encoding: 'utf8' }).trim();
    },
  };
  return walk;
}
