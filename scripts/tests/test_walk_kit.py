# -*- coding: utf-8 -*-
"""The focus probe pasted from assets/walk-kit.md, run in headless Chromium (Playwright from the deepweave checkout).

Usage:
  python3 -m unittest discover -s scripts/tests -p 'test_walk_kit.py' -v
  SEVEN_PHASE_PLAYWRIGHT_FROM=<dir with node_modules/@playwright/test> ...   # other checkout
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

KIT = pathlib.Path(__file__).resolve().parents[2] / "assets" / "walk-kit.md"
PW_FROM = pathlib.Path(os.environ.get("SEVEN_PHASE_PLAYWRIGHT_FROM",
                                      "~/Desktop/deepweave/deepweave/angular-workspace")).expanduser()

# Self-removing button (the explore-revamp R9 shape), a plain one, one that hands focus on before removing
# itself, a disabled one and a hidden one.
PAGE = """<!doctype html><body>
<input id="target">
<button id="gone" aria-label="關閉提示" onclick="this.remove()">x</button>
<button id="stay">保留</button>
<button id="handoff" onclick="document.getElementById('target').focus(); this.remove()">交棒</button>
<button disabled onclick="this.remove()">停用</button>
<button style="display:none" onclick="this.remove()">隱藏</button>
</body>"""

HARNESS = r"""
import { createRequire } from 'node:module';
import fs from 'node:fs';
const [pwFrom, snippetFile, pageFile] = process.argv.slice(2);
const { chromium } = createRequire(pwFrom + '/package.json')('@playwright/test');
const focusProbe = new Function(fs.readFileSync(snippetFile, 'utf8') + '\nreturn focusProbe;')();
const browser = await chromium.launch();
const page = await browser.newPage();
const html = fs.readFileSync(pageFile, 'utf8');
const drops = await focusProbe(page, '提示列', () => page.setContent(html));
await browser.close();
console.log(JSON.stringify(drops));
"""


def snippet():
    """The ```js block under the focus-probe rule in walk-kit.md."""
    m = re.search(r"```js\n(.*?const focusProbe\b.*?)```", KIT.read_text(encoding="utf-8"), re.S)
    return m and m.group(1)


class FocusProbeTest(unittest.TestCase):
    def test_walk_kit_has_a_pasteable_focus_probe(self):
        self.assertIsNotNone(snippet(), "walk-kit.md has no ```js block defining focusProbe")

    def test_probe_reports_only_the_button_that_drops_focus_to_body(self):
        src = snippet()
        if src is None:
            self.fail("walk-kit.md has no ```js block defining focusProbe")
        if not shutil.which("node") or not (PW_FROM / "node_modules" / "@playwright" / "test").is_dir():
            self.skipTest(f"no node or @playwright/test under {PW_FROM}")
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            (d / "h.mjs").write_text(HARNESS, encoding="utf-8")
            (d / "probe.js").write_text(src, encoding="utf-8")
            (d / "page.html").write_text(PAGE, encoding="utf-8")
            p = subprocess.run(["node", str(d / "h.mjs"), str(PW_FROM), str(d / "probe.js"), str(d / "page.html")],
                               capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        drops = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(drops, [{"state": "提示列", "button": "關閉提示", "active": "BODY", "navigated": False}])


if __name__ == "__main__":
    unittest.main()
