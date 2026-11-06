# -*- coding: utf-8 -*-
"""run_kit.mjs and walk_lib.mjs in a real browser: kits written before the library keep working, new steps
fail fast but can still wait on purpose, and the shared helpers report what a walk relies on.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_run_kit.py' -v
"""
import http.server
import json
import os
import pathlib
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent
RUN_KIT = SCRIPTS / "run_kit.mjs"
WALK_LIB = SCRIPTS / "walk_lib.mjs"
REPO = pathlib.Path(os.environ.get("SEVEN_PHASE_PLAYWRIGHT_FROM", "~/Desktop/deepweave/deepweave/angular-workspace")).expanduser().parent
LOGIN = """async (page) => { await page.setContent('<div id="fp">ok</div>'); return { ok: true }; }"""


class FakeApp(http.server.BaseHTTPRequestHandler):
    logins = []

    def _send(self, body, ctype="application/json"):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/v1/me":
            self._send(json.dumps({"authorization": self.headers.get("Authorization")}))
        else:
            self._send('<!doctype html><div id="ready">app</div>', "text/html")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        FakeApp.logins.append(json.loads(body))
        self._send(json.dumps({"data": {"token": "tok-1", "user": {"id": "u1"}}}))

    def log_message(self, format, *args):
        pass


@unittest.skipUnless((REPO / "angular-workspace" / "node_modules" / "@playwright").exists(), "needs @playwright/test")
class KitCase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.kit = self.tmp / "kit"
        self.kit.mkdir()
        (self.kit / "login.js").write_text(LOGIN)

    def step(self, name, src):
        (self.kit / name).write_text(src)

    def config(self, **cfg):
        (self.kit / "walk.config.json").write_text(json.dumps(cfg))

    def run_kit(self, *args, env=None):
        p = subprocess.run(["node", str(RUN_KIT), "--kit", str(self.kit), "--round", "r1", "--repo", str(REPO), *args],
                           capture_output=True, text=True, timeout=120, cwd=self.tmp, env={**os.environ, **(env or {})})
        summary = self.tmp / "r1" / "run" / "summary.json"
        return p, json.loads(summary.read_text()) if summary.exists() else {}

    def only_row(self, *args, env=None):
        p, summary = self.run_kit(*args, env=env)
        self.assertIn("steps", summary, p.stderr + p.stdout)
        self.assertEqual(len(summary["steps"]), 1)
        return summary["steps"][0]


class StepContract(KitCase):
    def test_step_written_before_the_library_keeps_the_long_default_timeout(self):
        # A live kit with a soft wait would flip results silently if the runner shortened its waits.
        self.step("step-old.js", "async (page) => { await page.locator('#missing').click(); return { ok: true }; }")
        row = self.only_row("--action-timeout", "1", "--step-timeout", "3")
        self.assertEqual(row["status"], "ERROR")
        self.assertIn("timeout 3s", row["error"])
        self.assertNotIn("1000ms", row["error"])

    def test_library_step_fails_a_missing_element_at_the_short_default(self):
        self.step("step-new.js", "async (page, walk) => { await page.locator('#missing').click(); return { ok: true }; }")
        row = self.only_row("--action-timeout", "1")
        self.assertEqual(row["status"], "ERROR")
        self.assertIn("Timeout 1000ms", row["error"])
        self.assertLess(row["seconds"], 8)

    def test_explicit_timeout_outlasts_the_short_default(self):
        # Slow things are real (generation, media): naming a timeout must still wait for them.
        self.step("step-late.js", """async (page, walk) => {
          await page.setContent('<script>setTimeout(() => { document.body.innerHTML = "<div id=late>here</div>"; }, 1500)</script>');
          await page.locator('#late').waitFor({ timeout: 6000 });
          return { ok: true };
        }""")
        self.assertEqual(self.only_row("--action-timeout", "1")["status"], "PASS")

    def test_cfg_override_reaches_a_library_step(self):
        self.step("step-cfg.js", """async (page, walk) => {
          const CFG = { x: 1, round: 'default' };
          return { ok: CFG.x === 5 && CFG.round === 'r1', x: CFG.x, round: CFG.round };
        }""")
        row = self.only_row("--cfg", '{"step-cfg.js":{"x":5}}')
        self.assertEqual(row["status"], "PASS", row)

    def test_failed_steps_are_listed_for_a_targeted_rerun(self):
        self.step("step-1.js", "async () => ({ ok: true })")
        self.step("step-2.js", "async () => ({ ok: false })")
        self.step("step-3.js", "async () => { throw new Error('boom'); }")
        p, _ = self.run_kit()
        self.assertEqual(p.returncode, 1)
        self.assertIn("rerun: --steps step-2.js,step-3.js", p.stdout)

    def test_a_clean_run_prints_no_rerun_line(self):
        self.step("step-1.js", "async () => ({ ok: true })")
        p, _ = self.run_kit()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("rerun:", p.stdout)

    def test_wrong_tree_fingerprint_is_a_setup_failure(self):
        self.step("step-1.js", "async () => ({ ok: true })")
        p, summary = self.run_kit("--fingerprint", "#not-this-change")
        self.assertEqual(p.returncode, 2)
        self.assertEqual(summary["steps"], [])

    def test_unknown_device_is_a_setup_failure(self):
        self.step("step-1.js", "async () => ({ ok: true })")
        p, _ = self.run_kit("--device", "Nokia 3310")
        self.assertEqual(p.returncode, 2)
        self.assertIn("Nokia 3310", p.stderr + p.stdout)

    def test_device_brings_touch_and_its_viewport(self):
        self.step("step-d.js", """async (page) => ({
          ok: true,
          touch: await page.evaluate(() => navigator.maxTouchPoints),
          width: page.viewportSize().width,
          engine: page.context().browser().browserType().name(),
        })""")
        row = self.only_row("--engine", "chromium", "--device", "iPhone 14")
        self.assertGreater(row["result"]["touch"], 0)
        self.assertEqual(row["result"]["width"], 390)
        self.assertEqual(row["result"]["engine"], "chromium")

    def test_engine_flag_selects_webkit(self):
        self.step("step-e.js", "async (page) => ({ ok: true, engine: page.context().browser().browserType().name() })")
        p, summary = self.run_kit("--engine", "webkit")
        if p.returncode == 2:
            self.skipTest("webkit not launchable here: " + (p.stderr + p.stdout)[:200])
        self.assertEqual(summary["steps"][0]["result"]["engine"], "webkit")


class Measurements(KitCase):
    def walk(self, body, *args):
        self.step("step-m.js", "async (page, walk) => {\n" + body + "\n}")
        row = self.only_row(*args)
        self.assertEqual(row["status"], "PASS", row)
        return row["result"]

    def test_focus_probe_reports_a_button_that_removes_itself_and_one_lost_across_a_breakpoint(self):
        got = self.walk("""
          const HTML = `<style>@media (max-width: 840px) { #wide { display: none; } }</style>
            <button id="ok">stays</button>
            <button id="gone" onclick="this.remove()">removes itself</button>
            <button id="wide">wide only</button>`;
          const drops = await walk.focusProbe('s', () => page.setContent(HTML), [840]);
          return { ok: true, drops };
        """)
        by = {(d["button"], "crossed" in d) for d in got["drops"]}
        self.assertIn(("removes itself", False), by)
        self.assertIn(("wide only", True), by)
        self.assertFalse([d for d in got["drops"] if d["button"] == "stays"], got["drops"])
        self.assertEqual([d["crossed"] for d in got["drops"] if d["button"] == "wide only"], ["1280→839"])

    def test_focus_probe_defaults_to_the_configured_breakpoints(self):
        # A step that forgets the argument must not silently skip the viewport-crossing check.
        self.config(breakpoints=[840])
        got = self.walk("""
          const HTML = `<style>@media (max-width: 840px) { #wide { display: none; } }</style><button id="wide">wide only</button>`;
          return { ok: true, drops: await walk.focusProbe('s', () => page.setContent(HTML)) };
        """)
        self.assertEqual([d.get("crossed") for d in got["drops"]], ["1280→839"])

    def test_overflow_names_the_element_past_the_viewport_but_not_one_in_a_scroller(self):
        got = self.walk("""
          await page.setContent(`<body style="margin:0">
            <div id="rail" style="overflow-x:auto;width:200px"><div style="width:3000px;height:10px"></div></div>
            <div id="wide" style="width:2000px;height:10px"></div></body>`);
          const dirty = await walk.overflow();
          await page.setContent('<body style="margin:0"><div style="width:100px;height:10px"></div></body>');
          return { ok: true, dirty, clean: await walk.overflow() };
        """)
        self.assertTrue(got["dirty"]["overflowing"])
        names = [o["el"] for o in got["dirty"]["offenders"]]
        self.assertTrue(any("#wide" in n for n in names), names)
        self.assertFalse(any("#rail" in n for n in names), names)
        self.assertFalse(got["clean"]["overflowing"])
        self.assertEqual(got["clean"]["offenders"], [])

    def test_overflow_ignores_a_closed_drawer_and_lists_only_the_outermost_offender(self):
        # Real page: a closed off-canvas sidebar and its children filled the list while nothing overflowed.
        got = self.walk("""
          await page.setContent(`<body style="margin:0">
            <aside id="drawer" style="position:fixed;left:1300px;top:0;width:300px;height:100px"><a id="link" style="display:block;height:10px">x</a></aside>
            <div id="anchor" style="position:absolute;left:-1px;width:1px;height:1px"></div>
            </body>`);
          const closed = await walk.overflow();
          await page.setContent(`<body style="margin:0">
            <div id="outer" style="width:2000px;height:20px"><div id="inner" style="width:1900px;height:10px"></div></div>
            </body>`);
          return { ok: true, closed, nested: await walk.overflow() };
        """)
        self.assertEqual(got["closed"]["offenders"], [])
        self.assertEqual([o["el"] for o in got["nested"]["offenders"]], ["div#outer"])

    def test_hit_names_what_covers_a_button(self):
        got = self.walk("""
          await page.setContent(`<button id="b" style="position:absolute;left:10px;top:10px;width:80px;height:40px">x</button>
            <button id="free" style="position:absolute;left:200px;top:10px;width:80px;height:40px">y</button>
            <div id="overlay" style="position:absolute;left:0;top:0;width:150px;height:150px"></div>`);
          return { ok: true, covered: await walk.hit('#b'), free: await walk.hit('#free') };
        """)
        self.assertFalse(got["covered"]["ok"])
        self.assertIn("#overlay", got["covered"]["covering"])
        self.assertTrue(got["free"]["ok"])

    def test_rect_and_style_read_the_element_and_a_missing_one_is_null_not_a_wait(self):
        got = self.walk("""
          await page.setContent('<div id="box" style="position:absolute;left:5px;top:6px;width:70px;height:20px;color:rgb(1, 2, 3)"></div>');
          return { ok: true, rect: await walk.rect('#box'), style: await walk.style('#box', ['color']), none: await walk.rect('#nope') };
        """, "--action-timeout", "30")
        self.assertEqual(got["rect"], {"x": 5, "y": 6, "w": 70, "h": 20})
        self.assertEqual(got["style"], {"color": "rgb(1, 2, 3)"})
        self.assertIsNone(got["none"])

    def test_shot_clips_to_the_element_and_lands_in_the_round_listed_by_the_runner(self):
        self.step("step-s.js", """async (page, walk) => {
          await page.setContent('<div id="box" style="width:100px;height:50px;background:red"></div>');
          return { ok: true, el: await walk.shot('el', '#box'), view: await walk.shot('view') };
        }""")
        row = self.only_row()
        self.assertEqual(row["status"], "PASS", row)

        def width(path):
            return struct.unpack(">I", pathlib.Path(path).read_bytes()[16:20])[0]

        self.assertEqual(width(row["result"]["el"]), 100)
        self.assertEqual(width(row["result"]["view"]), 1280)
        self.assertEqual(sorted(pathlib.Path(s).name for s in row["screenshots"]), ["el.png", "view.png"])

    def test_sql_goes_to_the_configured_container_and_database(self):
        # The walk database is separate on purpose; a helper that hits the shared dev DB reads the wrong rows.
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        calls = self.tmp / "docker-args"
        fake = bin_dir / "docker"
        fake.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > {calls}\necho "1|x"\n')
        fake.chmod(0o755)
        self.config(pg="my-pg", db="walkdb")
        self.step("step-q.js", "async (page, walk) => ({ ok: true, out: walk.sql('SELECT 1') })")
        row = self.only_row(env={"PATH": f"{bin_dir}:{os.environ['PATH']}"})
        self.assertEqual(row["result"]["out"], "1|x", row)
        args = calls.read_text().split("\n")
        self.assertEqual(args[:3], ["exec", "-i", "my-pg"])
        self.assertEqual(args[args.index("-d") + 1], "walkdb")
        self.assertEqual(args[args.index("-c") + 1], "SELECT 1")
        self.assertIn("ON_ERROR_STOP=1", args)


class AgainstAnApp(KitCase):
    def setUp(self):
        super().setUp()
        FakeApp.logins = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeApp)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.config(app=self.origin, api=self.origin)

    def test_sign_in_is_in_place_before_the_page_loads_and_api_calls_carry_the_token(self):
        self.step("step-a.js", """async (page, walk) => {
          await walk.signIn('viewer@example.test');
          await walk.go('/', '#ready');
          return {
            ok: true,
            token: await page.evaluate(() => localStorage.getItem('dw_token')),
            user: await page.evaluate(() => localStorage.getItem('dw_user')),
            me: await walk.api('/api/v1/me'),
          };
        }""")
        row = self.only_row()
        self.assertEqual(row["status"], "PASS", row)
        self.assertEqual(row["result"]["token"], "tok-1")
        self.assertEqual(json.loads(row["result"]["user"]), {"id": "u1"})
        self.assertEqual(row["result"]["me"]["body"]["authorization"], "Bearer tok-1")
        self.assertEqual(FakeApp.logins[0]["email"], "viewer@example.test")
        self.assertTrue(FakeApp.logins[0]["password"])

    def test_failed_sign_in_is_an_error_not_an_anonymous_walk(self):
        self.step("step-a.js", "async (page, walk) => { await walk.signIn('x@example.test'); return { ok: true }; }")
        self.config(app=self.origin, api="http://127.0.0.1:1")
        row = self.only_row()
        self.assertEqual(row["status"], "ERROR")

    def test_node_probe_reuses_the_session_run_kit_saved(self):
        (self.kit / "login.js").write_text("""async (page, walk) => {
          await walk.signIn('viewer@example.test');
          await walk.go('/', '#ready');
          return { ok: true };
        }""")
        self.step("step-1.js", "async () => ({ ok: true })")
        p, _ = self.run_kit()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        logins_after_kit = len(FakeApp.logins)
        probe = self.tmp / "probe.mjs"
        probe.write_text(f"""
          import {{ launch }} from {json.dumps(str(WALK_LIB))};
          const {{ page, walk, close }} = await launch({{ kit: process.argv[2], round: 'r1', repo: process.argv[3] }});
          await page.goto(walk.cfg.app + '/');
          console.log(await page.evaluate(() => localStorage.getItem('dw_token')));
          await close();
        """)
        out = subprocess.run(["node", str(probe), str(self.kit), str(REPO)], capture_output=True, text=True, timeout=60, cwd=self.tmp)
        self.assertEqual(out.stdout.strip(), "tok-1", out.stderr)
        self.assertEqual(len(FakeApp.logins), logins_after_kit)


if __name__ == "__main__":
    unittest.main()
