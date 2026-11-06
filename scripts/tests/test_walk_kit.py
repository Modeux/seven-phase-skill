# -*- coding: utf-8 -*-
"""The focus probe from walk_lib.mjs in headless Chromium, and the API table in assets/walk-kit.md against the code
(Playwright from the deepweave checkout).

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

SKILL = pathlib.Path(__file__).resolve().parents[2]
KIT = SKILL / "assets" / "walk-kit.md"
WALK_LIB = SKILL / "scripts" / "walk_lib.mjs"
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
import fs from 'node:fs';
const [lib, repo, pageFile, roundDir] = process.argv.slice(2);
const { launch } = await import(lib);
const { page, walk, close } = await launch({ repo, roundDir, storageState: false });
const html = fs.readFileSync(pageFile, 'utf8');
const drops = await walk.focusProbe('提示列', () => page.setContent(html), []);
await close();
console.log(JSON.stringify({ drops, api: Object.keys(walk) }));
"""


@unittest.skipUnless(shutil.which("node") and (PW_FROM / "node_modules" / "@playwright" / "test").is_dir(),
                     f"no node or @playwright/test under {PW_FROM}")
class WalkLibTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            (d / "h.mjs").write_text(HARNESS, encoding="utf-8")
            (d / "page.html").write_text(PAGE, encoding="utf-8")
            p = subprocess.run(["node", str(d / "h.mjs"), str(WALK_LIB), str(PW_FROM.parent), str(d / "page.html"), str(d)],
                               capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            raise AssertionError(p.stderr)
        cls.result = json.loads(p.stdout.strip().splitlines()[-1])

    def test_probe_reports_only_the_button_that_drops_focus_to_body(self):
        self.assertEqual(self.result["drops"], [{"state": "提示列", "button": "關閉提示", "active": "BODY", "navigated": False}])

    def test_every_call_the_walk_kit_documents_exists(self):
        # Agents write steps from this table instead of reading the library, so a stale name costs a failed run.
        text = KIT.read_text(encoding="utf-8")
        section = text[text.index("## 函式庫"):text.index("## 重走怎麼用")]
        table = [ln.split("|")[1] for ln in section.splitlines() if ln.startswith("| `")][1:]  # the first is the header
        documented = {name for cell in table for name in re.findall(r"`(\w+)(?:\(|`)", cell)}
        self.assertGreater(len(documented), 8, "the API table was not found")
        self.assertEqual(documented - set(self.result["api"]), set())


if __name__ == "__main__":
    unittest.main()
