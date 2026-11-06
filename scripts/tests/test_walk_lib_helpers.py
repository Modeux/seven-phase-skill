# -*- coding: utf-8 -*-
"""walk.at, walk.tabOrder, walk.net and walk.scroll from walk_lib.mjs, each driven by a real browser through run_kit.mjs.

Usage:
  python3 -m unittest discover -s scripts/tests -p 'test_walk_lib_helpers.py' -v
  SEVEN_PHASE_PLAYWRIGHT_FROM=<dir with node_modules/@playwright/test> ...   # other checkout
"""
import http.server
import json
import pathlib
import socket
import sys
import threading
import time
import unittest
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_run_kit import KitCase  # noqa: E402

PRELUDE = """
  const hit = (u, init) => page.evaluate(([u, init]) => fetch(u, init || {}).then((r) => r.status).catch((e) => 'ERR ' + e.message), [u, init]);
  const fire = (u) => page.evaluate((u) => { fetch(u).catch(() => {}); }, u);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
"""


class NetApp(http.server.BaseHTTPRequestHandler):
    def _send(self, body, ctype="application/json", status=200):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/slow":
            ms = int(parse_qs(url.query)["ms"][0])
            time.sleep(ms / 1000)
            self._send(json.dumps({"slept": ms}))
        elif url.path == "/fast":
            self._send(json.dumps({"fast": True}))
        elif url.path == "/err500":
            self._send("{}", status=500)
        elif url.path == "/drop":
            # A real transport failure; route.abort would only show what Playwright itself fakes.
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
        elif url.path == "/inner":
            self._send("<!doctype html><button id=in1>in1</button>", "text/html")
        else:
            self._send('<!doctype html><div id="ready">app</div>', "text/html")

    do_POST = do_GET

    def log_message(self, format, *args):
        pass


class QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # WHY: the browser hanging up on a slow handler is what the page-close tests do on purpose.


class HelperCase(KitCase):
    def walk(self, body, *args):
        self.step("step-m.js", "async (page, walk) => {\n" + PRELUDE + body + "\n}")
        row = self.only_row(*args)
        self.assertEqual(row["status"], "PASS", row)
        return row["result"]


class AppCase(HelperCase):
    def setUp(self):
        super().setUp()
        self.server = QuietServer(("127.0.0.1", 0), NetApp)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.config(app=self.origin, api=self.origin)


SIZE = "({ w: innerWidth, h: innerHeight })"


class At(HelperCase):
    def test_visits_each_width_in_order_and_restores(self):
        got = self.walk(f"""
          const rows = await walk.at([500, 900], (w) => page.evaluate(() => ({SIZE})).then((s) => ({{ asked: w, ...s }})));
          return {{ ok: true, rows, after: page.viewportSize() }};
        """)
        self.assertEqual(got["rows"], [
            {"width": 500, "result": {"asked": 500, "w": 500, "h": 800}},
            {"width": 900, "result": {"asked": 900, "w": 900, "h": 800}},
        ])
        self.assertEqual(got["after"], {"width": 1280, "height": 800})

    def test_a_throwing_fn_marks_that_width_keeps_going_and_still_restores(self):
        got = self.walk(f"""
          const rows = await walk.at([500, 600, 700, 800], async (w) => {{
            if (w === 600) throw new Error('boom at 600');
            if (w === 700) await page.locator('#missing').click({{ timeout: 50 }});
            return w;
          }});
          return {{ ok: true, rows, after: page.viewportSize(), inner: await page.evaluate(() => ({SIZE})) }};
        """)
        self.assertEqual([r["width"] for r in got["rows"]], [500, 600, 700, 800])
        self.assertEqual(got["rows"][0]["result"], 500)
        self.assertIn("boom at 600", got["rows"][1]["error"])
        self.assertNotIn("result", got["rows"][1])
        self.assertIn("Timeout 50ms", got["rows"][2]["error"])
        self.assertNotIn("\x1b", got["rows"][2]["error"])
        self.assertEqual(got["rows"][3]["result"], 800)
        self.assertEqual(got["after"], {"width": 1280, "height": 800})
        self.assertEqual(got["inner"], {"w": 1280, "h": 800})

    def test_a_synchronous_throw_is_caught_the_same_way(self):
        got = self.walk("""
          const rows = await walk.at([500], () => { throw new Error('sync boom'); });
          return { ok: true, rows, after: page.viewportSize() };
        """)
        self.assertIn("sync boom", got["rows"][0]["error"])
        self.assertEqual(got["after"]["width"], 1280)

    def test_default_widths_straddle_each_breakpoint_and_include_the_original(self):
        # The media queries really switch across these widths; a list that lands on one side misses the layout change.
        self.config(breakpoints=[840, 1180])
        got = self.walk("""
          const narrow = () => page.evaluate(() => matchMedia('(max-width: 840px)').matches);
          return { ok: true, omitted: await walk.at(narrow), nulled: await walk.at(null, narrow), undef: await walk.at(undefined, narrow) };
        """)
        expected = [[839, True], [841, False], [1179, False], [1181, False], [1280, False]]
        for key in ("omitted", "nulled", "undef"):
            self.assertEqual([[r["width"], r["result"]] for r in got[key]], expected, key)

    def test_default_widths_do_not_visit_the_original_twice(self):
        self.config(breakpoints=[840])
        got = self.walk("return { ok: true, rows: await walk.at(() => 1) };", "--viewport", "841x800")
        self.assertEqual([r["width"] for r in got["rows"]], [839, 841])

    def test_height_option_applies_only_inside_the_call(self):
        got = self.walk(f"""
          const rows = await walk.at([500], () => page.evaluate(() => ({SIZE})), {{ height: 400 }});
          const opts = await walk.at(() => page.evaluate(() => innerHeight), {{ height: 410 }});
          return {{ ok: true, rows, opts, after: page.viewportSize() }};
        """)
        self.assertEqual(got["rows"][0]["result"], {"w": 500, "h": 400})
        self.assertEqual({r["result"] for r in got["opts"]}, {410})
        self.assertEqual(got["after"], {"width": 1280, "height": 800})

    def test_restores_even_when_fn_resized_the_viewport_itself(self):
        got = self.walk("""
          await walk.at([500], () => page.setViewportSize({ width: 333, height: 222 }));
          return { ok: true, after: page.viewportSize() };
        """)
        self.assertEqual(got["after"], {"width": 1280, "height": 800})

    def test_an_empty_or_invalid_list_is_an_error_not_a_vacuous_pass(self):
        got = self.walk("""
          let calls = 0;
          const fn = () => { calls++; };
          const bad = {};
          for (const [k, widths] of Object.entries({ empty: [], zero: [0], negative: [-5], text: ['390'], fraction: [390.5], scalar: 390 })) {
            bad[k] = await walk.at(widths, fn).then(() => 'resolved', (e) => e.message);
          }
          bad.noFn = await walk.at([500]).then(() => 'resolved', (e) => e.message);
          bad.badHeight = await walk.at([500], fn, { height: 0 }).then(() => 'resolved', (e) => e.message);
          return { ok: true, bad, calls, after: page.viewportSize() };
        """)
        for key, msg in got["bad"].items():
            self.assertIn("walk.at", msg, key)
        self.assertEqual(got["calls"], 0)
        self.assertEqual(got["after"], {"width": 1280, "height": 800})

    def test_a_restore_that_fails_throws_and_still_hands_over_the_rows(self):
        # A later step measuring at the wrong width with no error is worse than a loud stop here.
        got = self.walk("""
          const out = await walk.at([500, 600], async (w) => { if (w === 500) await page.close(); return w; })
            .then((rows) => ({ resolved: rows }), (e) => ({ message: e.message, rows: e.rows }));
          return { ok: true, ...out };
        """)
        self.assertNotIn("resolved", got)
        self.assertIn("not restored", got["message"])
        self.assertEqual(got["rows"][0], {"width": 500, "result": 500})
        self.assertEqual(got["rows"][1]["width"], 600)
        self.assertIn("error", got["rows"][1])


PLAIN = """`<!doctype html><body style="margin:0">
  <button id="a" aria-label="first" style="position:absolute;left:10px;top:20px;width:80px;height:30px">x</button>
  <a id="l" href="#x" style="position:absolute;left:10px;top:60px">link text</a>
  <label for="b">field name</label><input id="b">
  <button id="c">last</button></body>`"""


class TabOrder(AppCase):
    def order(self, body, *args):
        return self.walk(body, *args)

    def test_lists_stops_in_order_and_ends_by_leaving_the_document(self):
        got = self.order(f"""
          await page.setContent({PLAIN});
          return {{ ok: true, t: await walk.tabOrder() }};
        """)["t"]
        self.assertEqual([s["i"] for s in got["stops"]], [0, 1, 2, 3])
        self.assertEqual([s["el"] for s in got["stops"]], ["button#a", "a#l", "input#b", "button#c"])
        self.assertEqual([s["name"] for s in got["stops"]], ["first", "link text", "field name", "last"])
        self.assertTrue(all(s["visible"] for s in got["stops"]), got["stops"])
        self.assertEqual(got["stops"][0]["rect"], {"x": 10, "y": 20, "w": 80, "h": 30})
        # Running off the end is normal, so it must not read as the focus-loss bug below.
        self.assertTrue(got["leftDocument"])
        self.assertFalse(got["droppedToBody"])
        self.assertFalse(got["cycled"])
        self.assertFalse(got["truncated"])
        self.assertEqual(got["invisible"], [])

    def test_from_starts_after_the_element_and_accepts_a_locator(self):
        got = self.order(f"""
          await page.setContent({PLAIN});
          return {{
            ok: true,
            sel: await walk.tabOrder({{ from: '#l' }}),
            loc: await walk.tabOrder({{ from: page.locator('#b') }}),
            back: await walk.tabOrder({{ from: '#c', shift: true }}),
          }};
        """)
        self.assertEqual(got["sel"]["start"], "a#l")
        self.assertEqual([s["el"] for s in got["sel"]["stops"]], ["input#b", "button#c"])
        self.assertEqual([s["el"] for s in got["loc"]["stops"]], ["button#c"])
        self.assertEqual([s["el"] for s in got["back"]["stops"]], ["input#b", "a#l", "button#a"])
        self.assertTrue(got["back"]["leftDocument"])

    def test_a_focus_trap_reports_cycled_without_repeating_a_stop(self):
        trap = """`<!doctype html><button id="out">out</button><div id="d"><button id="a">a</button><button id="b">b</button><button id="c">c</button></div>
          <script>d.addEventListener('keydown', (e) => { if (e.key !== 'Tab') return;
            const els = [a, b, c]; const i = els.indexOf(document.activeElement);
            e.preventDefault(); els[(i + (e.shiftKey ? 2 : 1)) % 3].focus(); });</script>`"""
        got = self.order(f"""
          await page.setContent({trap});
          const ring = await walk.tabOrder({{ from: '#b' }});
          await page.setContent({trap});
          return {{ ok: true, ring, lead: await walk.tabOrder() }};
        """)
        ring = got["ring"]
        self.assertTrue(ring["cycled"])
        self.assertEqual(ring["cycledTo"], 0)
        self.assertEqual([s["el"] for s in ring["stops"]], ["button#c", "button#a", "button#b"])
        self.assertFalse(ring["leftDocument"])
        self.assertFalse(ring["truncated"])
        # Entering a ring from outside never returns to stop 0, and must still end instead of padding to max.
        lead = got["lead"]
        self.assertTrue(lead["cycled"])
        self.assertEqual(lead["cycledTo"], 1)
        self.assertEqual([s["el"] for s in lead["stops"]], ["button#out", "button#a", "button#b", "button#c"])

    def test_reaching_max_is_marked_truncated(self):
        got = self.order("""
          await page.setContent('<!doctype html>' + '<button>b</button>'.repeat(5));
          return { ok: true, t: await walk.tabOrder({ max: 3 }), bad: await walk.tabOrder({ max: 0 }).then(() => 'resolved', (e) => e.message) };
        """)
        self.assertEqual(len(got["t"]["stops"]), 3)
        self.assertTrue(got["t"]["truncated"])
        self.assertFalse(got["t"]["cycled"])
        self.assertFalse(got["t"]["leftDocument"])
        self.assertIn("walk.tabOrder", got["bad"])

    def test_identical_looking_buttons_are_distinct_stops(self):
        # describe() gives them the same text; comparing by that would call the second one a cycle.
        got = self.order("""
          await page.setContent('<!doctype html>' + '<button>b</button>'.repeat(3));
          return { ok: true, t: await walk.tabOrder() };
        """)["t"]
        self.assertEqual(len(got["stops"]), 3)
        self.assertFalse(got["cycled"])

    def test_focus_lost_mid_walk_is_dropped_to_body_not_left_document(self):
        got = self.order("""
          await page.setContent(`<!doctype html><button id="a">a</button><button id="gone" onfocus="this.remove()">g</button><button id="c">c</button>`);
          return { ok: true, t: await walk.tabOrder() };
        """)["t"]
        self.assertEqual([s["el"] for s in got["stops"]], ["button#a"])
        self.assertTrue(got["droppedToBody"])
        self.assertFalse(got["leftDocument"])

    def test_invisible_focus_targets_are_flagged(self):
        got = self.order("""
          await page.setContent(`<!doctype html><body style="margin:0">
            <button id="seen">seen</button>
            <button id="clear" style="opacity:0">clear</button>
            <button id="srOnly" style="position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);border:0">sr</button>
            <button id="off" style="position:absolute;left:-9999px">off</button>
            <div style="max-height:0;overflow:hidden"><button id="collapsed">collapsed</button></div>
            <div style="height:0;overflow:hidden"><button id="popover" style="position:fixed;left:200px;top:200px;width:80px;height:30px">popover</button></div>
            <div aria-hidden="true"><button id="muted">muted</button></div>
            <button id="end">end</button></body>`);
          return { ok: true, t: await walk.tabOrder() };
        """)["t"]
        by = {s["el"]: s for s in got["stops"]}
        for name in ("clear", "srOnly", "off", "collapsed"):
            self.assertFalse(by[f"button#{name}"]["visible"], name)
        for name in ("seen", "popover", "muted", "end"):
            self.assertTrue(by[f"button#{name}"]["visible"], name)
        order = [s["el"] for s in got["stops"]]
        self.assertEqual(got["invisible"], [order.index(f"button#{n}") for n in ("clear", "srOnly", "off", "collapsed")])
        self.assertEqual(got["ariaHidden"], [order.index("button#muted")])

    def test_a_from_that_cannot_start_the_walk_is_an_error(self):
        got = self.order("""
          await page.setContent('<!doctype html><div id="plain">text</div><button class="two">1</button><button class="two">2</button>');
          const msg = (o) => walk.tabOrder(o).then(() => 'resolved', (e) => e.message);
          return { ok: true, missing: await msg({ from: '#nope' }), many: await msg({ from: '.two' }), inert: await msg({ from: '#plain' }) };
        """, "--action-timeout", "1")
        self.assertIn("Timeout", got["missing"])
        self.assertIn("strict mode", got["many"])
        self.assertIn("did not take focus", got["inert"])

    def test_a_key_handler_that_blocks_tab_is_stuck_not_truncated(self):
        got = self.order("""
          await page.setContent(`<!doctype html><button id="a" onkeydown="event.preventDefault()">a</button><button id="c">c</button>`);
          return { ok: true, from: await walk.tabOrder({ from: '#a' }) };
        """)["from"]
        self.assertEqual(got["stops"], [])
        self.assertTrue(got["stuck"])
        self.assertFalse(got["truncated"])
        self.assertFalse(got["cycled"])

    def test_nothing_focusable_reports_no_stops(self):
        got = self.order("""
          await page.setContent('<!doctype html><p>no controls</p>');
          return { ok: true, t: await walk.tabOrder() };
        """)["t"]
        self.assertEqual(got["stops"], [])
        self.assertTrue(got["leftDocument"])

    def test_shadow_dom_stops_are_the_inner_controls(self):
        got = self.order("""
          await page.setContent(`<!doctype html><div id="host"></div><script>
            host.attachShadow({ mode: 'open' }).innerHTML = '<button id="s1">s1</button><button id="s2">s2</button>';</script>`);
          return { ok: true, t: await walk.tabOrder() };
        """)["t"]
        self.assertEqual([s["el"] for s in got["stops"]], ["button#s1", "button#s2"])
        self.assertFalse(got["stuck"])

    def test_same_origin_iframe_stops_are_listed_with_page_coordinates(self):
        got = self.order("""
          await page.setContent(`<!doctype html><body style="margin:0"><button id="a">a</button>
            <iframe id="f" style="position:absolute;left:100px;top:200px;border:0" srcdoc="<body style='margin:0'><button id=in1 style='display:block'>in1</button><button id=in2>in2</button>"></iframe>
            <button id="c">c</button></body>`);
          await page.frameLocator('#f').locator('#in2').waitFor();
          return { ok: true, t: await walk.tabOrder() };
        """)["t"]
        self.assertEqual([s["el"] for s in got["stops"]], ["button#a", "button#in1", "button#in2", "button#c"])
        self.assertEqual((got["stops"][1]["rect"]["x"], got["stops"][1]["rect"]["y"]), (100, 200))
        self.assertFalse(got["stuck"])

    def test_cross_origin_iframe_ends_the_walk_as_left_document(self):
        # The walk cannot see inside it, so carrying on would report the frame as a stuck or repeated stop.
        got = self.order(f"""
          await walk.go('/', '#ready');
          await page.setContent(`<!doctype html><button id="a">a</button><iframe id="f" src="http://localhost:{self.port}/inner"></iframe><button id="c">c</button>`);
          await page.frameLocator('#f').locator('#in1').waitFor();
          return {{ ok: true, t: await walk.tabOrder() }};
        """)["t"]
        self.assertEqual([s["el"] for s in got["stops"]], ["button#a", "iframe#f"])
        self.assertTrue(got["leftDocument"])
        self.assertFalse(got["stuck"])
        self.assertFalse(got["truncated"])

    def run_engine(self, engine):
        self.step("step-m.js", f"""async (page, walk) => {{
          await page.setContent({PLAIN});
          return {{ ok: true, t: await walk.tabOrder() }};
        }}""")
        p, summary = self.run_kit("--engine", engine)
        if p.returncode == 2:
            self.skipTest(engine + " not launchable here: " + (p.stderr + p.stdout)[:200])
        row = summary["steps"][0]
        self.assertEqual(row["status"], "PASS", row)
        return row["result"]["t"]

    def test_webkit_visits_buttons_and_links_too(self):
        # Plain Tab on macOS WebKit skips them, which would report a short order as if the page were broken.
        got = self.run_engine("webkit")
        self.assertEqual([s["el"] for s in got["stops"]], ["button#a", "a#l", "input#b", "button#c"])
        self.assertTrue(got["leftDocument"])

    def test_firefox_at_the_end_of_the_document_stops_instead_of_padding_to_max(self):
        # Firefox keeps focus on the last control there, so without this the list fills with one element up to max.
        got = self.run_engine("firefox")
        self.assertEqual([s["el"] for s in got["stops"]], ["button#a", "a#l", "input#b", "button#c"])
        self.assertTrue(got["stuck"])
        self.assertFalse(got["truncated"])


class Net(AppCase):
    def test_list_records_each_request_with_origin_removed(self):
        got = self.walk("""
          await walk.go('/', '#ready');
          const net = walk.net();
          await hit('/fast?x=1');
          await hit('/fast', { method: 'POST', body: '{}' });
          await hit('/err500');
          net.stop();
          return { ok: true, rows: net.list() };
        """)
        rows = got["rows"]
        self.assertEqual([(r["method"], r["url"], r["status"]) for r in rows],
                         [("GET", "/fast?x=1", 200), ("POST", "/fast", 200), ("GET", "/err500", 500)])
        for r in rows:
            self.assertFalse(r["pending"])
            self.assertFalse(r["failed"])  # a 500 is an answer; only a transport failure is `failed`
            self.assertIsInstance(r["ms"], int)
            self.assertEqual(sorted(r), ["failed", "method", "ms", "pending", "status", "url"])

    def test_match_filters_recording_counting_and_every_form_of_matcher(self):
        got = self.walk("""
          await walk.go('/', '#ready');
          const all = walk.net();
          const only = walk.net({ match: '/fast' });
          await hit('/fast?x=1');
          await hit('/fast', { method: 'POST', body: '{}' });
          await hit('/err500');
          const global = /GET \\/fast|POST \\/fast/g;
          return {
            ok: true,
            only: only.list().map((r) => r.url),
            onlyCount: only.count(),
            onlyWithin: only.count('POST'),
            onlyOutside: only.count('/err500'),
            all: all.count(),
            substring: all.count('/err500'),
            method: all.count('POST /fast'),
            fullUrl: all.count(new URL(page.url()).host),
            regex: all.count(/\\/fast$/),
            anchored: all.count(/^\\/err/),
            fn: all.count((r) => r.status === 500),
            fnOrigin: all.count((r) => r.origin === new URL(page.url()).origin),
            globalTwice: [all.count(global), all.count(global)],
            none: all.count('/nothing'),
          };
        """)
        self.assertEqual(got["only"], ["/fast?x=1", "/fast"])
        self.assertEqual(got["onlyCount"], 2)
        self.assertEqual(got["onlyWithin"], 1)
        self.assertEqual(got["onlyOutside"], 0)
        self.assertEqual(got["all"], 3)
        self.assertEqual(got["substring"], 1)
        self.assertEqual(got["method"], 1)
        self.assertEqual(got["fullUrl"], 3)
        self.assertEqual(got["regex"], 1)
        self.assertEqual(got["anchored"], 1)
        self.assertEqual(got["fn"], 1)
        self.assertEqual(got["fnOrigin"], 3)
        # A /g regex keeps lastIndex between tests; without a reset the same question gets different answers.
        self.assertEqual(got["globalTwice"], [2, 2])
        self.assertEqual(got["none"], 0)

    def test_an_unanswered_request_is_pending_until_its_response(self):
        got = self.walk("""
          await walk.go('/', '#ready');
          const net = walk.net();
          const asked = hit('/slow?ms=700');
          await sleep(150);
          const during = net.list();
          const counted = net.count('/slow');
          await asked;
          return { ok: true, during, counted, after: net.list() };
        """)
        self.assertEqual(got["during"][0]["url"], "/slow?ms=700")
        self.assertTrue(got["during"][0]["pending"])
        self.assertIsNone(got["during"][0]["status"])
        self.assertIsNone(got["during"][0]["ms"])
        self.assertEqual(got["counted"], 1)
        self.assertFalse(got["after"][0]["pending"])
        self.assertEqual(got["after"][0]["status"], 200)
        self.assertGreaterEqual(got["after"][0]["ms"], 600)

    def test_a_failed_request_is_marked_with_the_browser_reason(self):
        got = self.walk("""
          await walk.go('/', '#ready');
          await page.route('**/aborted', (r) => r.abort());
          const net = walk.net();
          const dropped = await hit('/drop');
          await hit('/aborted');
          return { ok: true, dropped, rows: net.list(), count: net.count() };
        """)
        self.assertTrue(got["dropped"].startswith("ERR "), got["dropped"])
        drop, aborted = got["rows"]
        self.assertEqual(drop["url"], "/drop")
        self.assertIn("net::", drop["failed"])
        self.assertIsNone(drop["status"])
        self.assertFalse(drop["pending"])
        self.assertIn("net::", aborted["failed"])
        self.assertEqual(got["count"], 2)

    def test_wait_armed_before_the_action_returns_that_response_with_its_body(self):
        got = self.walk("""
          await walk.go('/', '#ready');
          const net = walk.net();
          const waiting = net.wait('POST /fast');
          await hit('/fast');
          await hit('/err500', { method: 'POST', body: '{}' });
          await hit('/fast', { method: 'POST', body: '{"n":1}' });
          const r = await waiting;
          return { ok: true, r, body: await r.response.json(), sent: r.response.request().postData(), keys: Object.keys(r) };
        """)
        self.assertEqual(got["r"]["url"], "/fast")
        self.assertEqual(got["r"]["method"], "POST")
        self.assertEqual(got["r"]["status"], 200)
        self.assertEqual(got["body"], {"fast": True})
        self.assertEqual(got["sent"], '{"n":1}')
        self.assertNotIn("response", got["keys"])  # stays out of the step's JSON

    def test_wait_covers_a_request_sent_before_the_wait_call(self):
        got = self.walk("""
          await walk.go('/', '#ready');
          const net = walk.net();
          await fire('/slow?ms=600');
          await sleep(150);
          return { ok: true, r: await net.wait('/slow', { timeout: 5000 }) };
        """)
        self.assertEqual(got["r"]["status"], 200)
        self.assertGreaterEqual(got["r"]["ms"], 400)

    def test_wait_covers_a_request_sent_before_recording_started(self):
        got = self.walk("""
          await walk.go('/', '#ready');
          await fire('/slow?ms=600');
          await sleep(150);
          const net = walk.net({ match: '/slow' });
          return { ok: true, r: await net.wait('/slow', { timeout: 5000 }), rows: net.list() };
        """)
        self.assertEqual(got["r"]["status"], 200)
        self.assertIsNone(got["r"]["ms"])  # its start was not seen, so no duration is better than a wrong one
        self.assertEqual(len(got["rows"]), 1)

    def test_a_response_already_seen_is_not_the_next_one_and_the_timeout_says_so(self):
        # Returning the stale response would pass a step whose action never fired.
        got = self.walk("""
          await walk.go('/', '#ready');
          const net = walk.net();
          await hit('/fast');
          const t0 = Date.now();
          const out = await net.wait('/fast', { timeout: 300 }).then((r) => ({ resolved: r }), (e) => ({ message: e.message, rows: e.rows }));
          return { ok: true, ...out, took: Date.now() - t0 };
        """)
        self.assertNotIn("resolved", got)
        self.assertIn("1 answered before this wait", got["message"])
        self.assertEqual(len(got["rows"]), 1)
        self.assertLess(got["took"], 3000)

    def test_wait_on_a_failing_request_rejects_with_the_reason(self):
        got = self.walk("""
          await walk.go('/', '#ready');
          const net = walk.net();
          const waiting = net.wait('/drop', { timeout: 800 }).then((r) => ({ resolved: r }), (e) => ({ message: e.message, rows: e.rows }));
          await hit('/drop');
          return { ok: true, ...await waiting };
        """)
        self.assertNotIn("resolved", got)
        self.assertIn("net::", got["message"])
        self.assertIn("net::", got["rows"][0]["failed"])

    def test_wait_outlives_a_cancelled_request_when_a_later_one_answers(self):
        # switchMap and retry interceptors fail one request and send the next; rejecting on the first breaks real pages.
        got = self.walk("""
          await walk.go('/', '#ready');
          const net = walk.net();
          const waiting = net.wait(/drop|fast/, { timeout: 4000 });
          await hit('/drop');
          await hit('/fast');
          return { ok: true, r: await waiting };
        """)
        self.assertEqual(got["r"]["url"], "/fast")

    def test_closing_the_page_rejects_waits_and_leaves_no_unhandled_rejection(self):
        # Node ends the process on an unhandled rejection; the step would never report and summary.json would be missing.
        got = self.walk("""
          await walk.go('/', '#ready');
          const net = walk.net();
          await fire('/slow?ms=4000');
          await sleep(100);
          const armed = net.wait('/slow', { timeout: 3000 }).then(() => 'resolved', (e) => e.message);
          net.wait('/slow', { timeout: 3000 });
          net.wait('/never', { timeout: 100 });
          await sleep(300);
          const t0 = Date.now();
          await page.close();
          const message = await armed;
          const took = Date.now() - t0;
          await sleep(300);
          return { ok: true, message, took, rows: net.list(), late: await net.wait('/x').then(() => 'resolved', (e) => e.message) };
        """)
        self.assertIn("page closed", got["message"])
        self.assertLess(got["took"], 2500)
        self.assertTrue(got["rows"][0]["pending"])
        self.assertIn("page closed", got["late"])

    def test_stop_removes_its_listeners_and_ends_waits(self):
        got = self.walk("""
          await walk.go('/', '#ready');
          const events = ['request', 'response', 'requestfailed', 'close'];
          const counts = () => events.map((e) => page.listenerCount(e));
          const before = counts();
          const net = walk.net();
          const during = counts();
          await hit('/fast');
          const waiting = net.wait('/slow', { timeout: 5000 }).then(() => 'resolved', (e) => e.message);
          net.stop();
          net.stop();
          const after = counts();
          await hit('/err500');
          return { ok: true, before, during, after, rows: net.list(), stopped: await waiting, late: await net.wait('/x').then(() => 'resolved', (e) => e.message) };
        """)
        self.assertEqual(got["after"], got["before"])
        self.assertNotEqual(got["during"], got["before"])
        self.assertEqual([r["url"] for r in got["rows"]], ["/fast"])
        self.assertIn("stopped", got["stopped"])
        self.assertIn("stopped", got["late"])

    def test_invalid_matchers_fail_loudly_and_a_throwing_one_rejects_only_its_wait(self):
        got = self.walk("""
          await walk.go('/', '#ready');
          const msg = (f) => { try { return f(); } catch (e) { return e.message; } };
          const net = walk.net();
          const throwing = net.wait(() => { throw new Error('bad matcher'); }, { timeout: 2000 }).then(() => 'resolved', (e) => e.message);
          const healthy = net.wait('/fast', { timeout: 2000 });
          await hit('/fast');
          return {
            ok: true,
            badNet: msg(() => walk.net({ match: 5 })),
            emptyNet: msg(() => walk.net({ match: '' })),
            badCount: msg(() => net.count(5)),
            badWait: await net.wait(5).then(() => 'resolved', (e) => e.message),
            badTimeout: await net.wait('/fast', { timeout: -1 }).then(() => 'resolved', (e) => e.message),
            throwing: await throwing,
            healthy: (await healthy).status,
          };
        """)
        for key in ("badNet", "emptyNet", "badCount", "badWait"):
            self.assertIn("matcher", got[key], key)
        self.assertIn("timeout", got["badTimeout"])
        self.assertIn("bad matcher", got["throwing"])
        self.assertEqual(got["healthy"], 200)


class NetTimeoutMatcher(AppCase):
    def test_a_matcher_that_throws_when_the_timeout_builds_its_report_rejects_instead_of_killing_the_run(self):
        # The timeout runs from a timer with no caller to catch for it; a throw there would end the whole process.
        got = self.walk("""
          await walk.go('/', '#ready');
          const net = walk.net();
          await hit('/fast');
          const message = await net.wait((r) => { if (r.url === '/fast') throw new Error('bad matcher'); return false; }, { timeout: 100 })
            .then(() => 'resolved', (e) => e.message);
          return { ok: true, message };
        """)
        self.assertIn("bad matcher", got["message"])


BOX = """`<!doctype html><body style="margin:0">
  <div id="box" style="height:100px;overflow:auto"><div style="height:500px"></div></div>
  <div class="many"></div><div class="many"></div>
  </body>`"""


class Scroll(HelperCase):
    def test_measures_without_moving_when_to_is_omitted(self):
        got = self.walk(f"""
          await page.setContent({BOX});
          await page.evaluate(() => {{ document.querySelector('#box').scrollTop = 40; }});
          return {{ ok: true, s: await walk.scroll('#box') }};
        """)["s"]
        self.assertEqual((got["top"], got["height"], got["client"], got["max"]), (40, 500, 100, 400))
        self.assertFalse(got["atTop"])
        self.assertFalse(got["atBottom"])
        self.assertTrue(got["found"])
        self.assertTrue(got["scrollable"])

    def test_moves_to_bottom_top_and_a_number_and_a_locator_works(self):
        got = self.walk(f"""
          await page.setContent({BOX});
          return {{
            ok: true,
            bottom: await walk.scroll('#box', 'bottom'),
            top: await walk.scroll('#box', 'top'),
            number: await walk.scroll(page.locator('#box'), 150),
            past: await walk.scroll('#box', 9999),
            zero: await walk.scroll('#box', 0),
          }};
        """)
        self.assertEqual((got["bottom"]["top"], got["bottom"]["atBottom"], got["bottom"]["atTop"]), (400, True, False))
        self.assertEqual((got["top"]["top"], got["top"]["atTop"], got["top"]["atBottom"]), (0, True, False))
        self.assertEqual((got["number"]["top"], got["number"]["atTop"], got["number"]["atBottom"]), (150, False, False))
        self.assertEqual((got["past"]["top"], got["past"]["atBottom"]), (400, True))
        self.assertEqual((got["zero"]["top"], got["zero"]["atTop"]), (0, True))

    def test_smooth_scrolling_css_does_not_leave_the_measure_mid_animation(self):
        got = self.walk("""
          await page.setContent(`<!doctype html><div id="box" style="height:100px;overflow:auto;scroll-behavior:smooth"><div style="height:5000px"></div></div>`);
          return { ok: true, s: await walk.scroll('#box', 'bottom') };
        """)["s"]
        self.assertEqual(got["top"], 4900)
        self.assertTrue(got["atBottom"])

    def test_content_added_by_the_scroll_is_in_the_measure(self):
        got = self.walk("""
          await page.setContent(`<!doctype html><div id="box" style="height:100px;overflow:auto" onscroll="requestAnimationFrame(() => requestAnimationFrame(() => { inner.style.height='900px'; }))"><div id="inner" style="height:500px"></div></div>`);
          return { ok: true, s: await walk.scroll('#box', 'bottom') };
        """)["s"]
        self.assertEqual(got["height"], 900)
        self.assertFalse(got["atBottom"])

    def test_the_measure_after_moving_waits_for_the_two_settle_frames(self):
        # The content-growth test above only fails when the browser is quiet; this one does not depend on timing.
        got = self.walk("""
          await page.setContent(`<!doctype html><div id="box" style="height:100px;overflow:auto"><div style="height:500px"></div></div>
            <script>
              let frames = 0;
              const raf = window.requestAnimationFrame.bind(window);
              window.requestAnimationFrame = (cb) => { frames++; return raf(cb); };
              box.scrollTo = function (o) { frames = 0; Element.prototype.scrollTo.call(this, o); };
              const read = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollHeight').get;
              Object.defineProperty(box, 'scrollHeight', { get() { window.framesAtMeasure = frames; return read.call(this); } });
            </script>`);
          await walk.scroll('#box', 'bottom');
          return { ok: true, frames: await page.evaluate(() => window.framesAtMeasure) };
        """)
        self.assertEqual(got["frames"], 2)

    def test_no_target_means_the_document(self):
        got = self.walk("""
          await page.setContent(`<!doctype html><body style="margin:0"><div style="height:3000px"></div></body>`);
          return {
            ok: true,
            start: await walk.scroll(),
            bottom: await walk.scroll(null, 'bottom'),
            undef: await walk.scroll(undefined, 'top'),
            real: await page.evaluate(() => document.scrollingElement.scrollTop),
          };
        """)
        self.assertEqual((got["start"]["top"], got["start"]["height"], got["start"]["client"], got["start"]["max"]), (0, 3000, 800, 2200))
        self.assertTrue(got["start"]["atTop"])
        self.assertEqual(got["bottom"]["top"], 2200)
        self.assertTrue(got["bottom"]["atBottom"])
        self.assertEqual(got["undef"]["top"], 0)
        self.assertEqual(got["real"], 0)

    def test_missing_element_is_reported_as_not_found_without_waiting(self):
        self.step("step-m.js", "async (page, walk) => ({ ok: true, s: await walk.scroll('#nope'), moved: await walk.scroll('#nope', 'bottom') })")
        row = self.only_row("--action-timeout", "30")
        self.assertEqual(row["status"], "PASS", row)
        self.assertLess(row["seconds"], 8)
        for s in (row["result"]["s"], row["result"]["moved"]):
            self.assertFalse(s["found"])
            self.assertFalse(s["scrollable"])
            for key in ("top", "height", "client", "max", "atTop", "atBottom"):
                self.assertIsNone(s[key], key)

    def test_an_element_that_cannot_scroll_does_not_read_as_at_the_bottom(self):
        # `atBottom: true` after asking for the bottom would pass a step whose scroller is mis-sized.
        got = self.walk("""
          await page.setContent(`<!doctype html><body style="margin:0"><div id="fits" style="height:100px;overflow:auto"><div style="height:50px"></div></div>
            <div id="spills" style="height:100px"><div style="height:500px"></div></div></body>`);
          return { ok: true, fits: await walk.scroll('#fits', 'bottom'), spills: await walk.scroll('#spills', 'bottom') };
        """)
        self.assertEqual(got["fits"]["max"], 0)
        self.assertFalse(got["fits"]["scrollable"])
        self.assertTrue(got["fits"]["found"])
        self.assertIsNone(got["fits"]["atTop"])
        self.assertIsNone(got["fits"]["atBottom"])
        # overflow:visible has content past its box and a non-zero max, yet nothing can move.
        self.assertEqual(got["spills"]["max"], 400)
        self.assertEqual(got["spills"]["top"], 0)
        self.assertFalse(got["spills"]["scrollable"])
        self.assertIsNone(got["spills"]["atBottom"])

    def test_a_page_with_nothing_to_scroll_is_visible_as_such(self):
        got = self.walk("""
          await page.setContent('<!doctype html><body style="margin:0"><div style="height:10px"></div></body>');
          return { ok: true, s: await walk.scroll() };
        """)["s"]
        self.assertEqual(got["max"], 0)
        self.assertFalse(got["scrollable"])

    def test_overflow_hidden_is_reported_because_a_user_could_not_scroll_it(self):
        got = self.walk("""
          await page.setContent(`<!doctype html><div id="locked" style="height:100px;overflow:hidden"><div style="height:500px"></div></div>
            <div id="free" style="height:100px;overflow:auto"><div style="height:500px"></div></div>`);
          return { ok: true, locked: await walk.scroll('#locked', 'bottom'), free: await walk.scroll('#free') };
        """)
        self.assertEqual(got["locked"]["overflowY"], "hidden")
        self.assertEqual(got["free"]["overflowY"], "auto")

    def test_a_locked_document_reports_the_overflow_from_body(self):
        got = self.walk("""
          await page.setContent(`<!doctype html><body style="margin:0;overflow:hidden"><div style="height:3000px"></div></body>`);
          return { ok: true, s: await walk.scroll() };
        """)["s"]
        self.assertEqual(got["overflowY"], "hidden")

    def test_misuse_is_an_error_not_a_quiet_measure(self):
        got = self.walk(f"""
          await page.setContent({BOX});
          const msg = (...a) => walk.scroll(...a).then(() => 'resolved', (e) => e.message);
          return {{
            ok: true,
            swapped: await msg('bottom'),
            swappedNumber: await msg(100),
            badTo: await msg('#box', 'middle'),
            nan: await msg('#box', NaN),
            negative: await msg('#box', -1),
            many: await msg('.many'),
          }};
        """)
        for key, text in got.items():
            if key != "ok":
                self.assertIn("walk.scroll", text, key)
        self.assertIn("matched 2", got["many"])


if __name__ == "__main__":
    unittest.main()
