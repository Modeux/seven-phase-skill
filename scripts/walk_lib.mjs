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
  const walk = makeWalk(page, { roundDir: resolvedRoundDir, cfg, errors });
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

function makeWalk(page, { roundDir, cfg, errors }) {
  const locate = (target) => (typeof target === 'string' ? page.locator(target) : target);
  const round1 = (n) => Math.round(n * 10) / 10;
  const settle = () => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  let auth = null;

  const walk = {
    cfg,
    errors,
    settle,

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
