# -*- coding: utf-8 -*-
"""Tests for run_external.py — fake codex/grok/agy on a temp PATH, never the real CLIs.

Usage:
  python3 -m unittest discover -s scripts/tests -p 'test_run_external.py' -v
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "run_external.py"
REPORT = "### [HIGH] something broke\nbody\n\n## 已查證無問題\n- fine\n"

# WHY: one fake serves all three lanes; FAKE_MODE picks the failure shape and FAKE_CALLED proves
# whether the CLI was invoked at all (agy oversize must not invoke it).
FAKE = r'''#!{py}
import os, pathlib, sys
pathlib.Path(os.environ["FAKE_CALLED"]).write_text("called")
mode = os.environ.get("FAKE_MODE", "ok")
report = {report!r}
name = pathlib.Path(sys.argv[0]).name
if name == "codex":
    sys.stdin.read()
    out = sys.argv[sys.argv.index("-o") + 1]
    if mode == "ok":
        pathlib.Path(out).write_text(report)
    elif mode == "blank":
        pathlib.Path(out).write_text("  \n\n")
    elif mode == "partial-then-fail":
        pathlib.Path(out).write_text("### [LOW] half")
else:
    if mode == "ok":
        sys.stdout.write(report)
    elif mode == "blank":
        sys.stdout.write("  \n")
    elif mode == "partial-then-fail":
        sys.stdout.write("### [LOW] half")
    sys.stderr.write("log line\n")
sys.exit(7 if mode in ("rc7", "partial-then-fail") else 0)
'''


class RunExternalTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._td.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name in ("codex", "grok", "agy"):
            p = self.bin / name
            p.write_text(FAKE.format(py=sys.executable, report=REPORT))
            p.chmod(0o755)
        self.review = self.tmp / "review"
        self.review.mkdir()
        self.snap = self.tmp / "snap"
        self.snap.mkdir()
        self.packet = self.review / "packet.md"
        self.packet.write_text("review this\n")
        self.called = self.tmp / "called"

    def tearDown(self):
        self._td.cleanup()

    def run_lane(self, lane, mode="ok"):
        out = self.review / f"lane-{lane}.md"
        env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                   FAKE_MODE=mode, FAKE_CALLED=str(self.called))
        r = subprocess.run([sys.executable, str(SCRIPT), lane, "--packet", str(self.packet),
                            "--snapshot", str(self.snap), "--out", str(out)],
                           capture_output=True, text=True, env=env, timeout=60)
        return r, out

    def status(self, lane):
        return json.loads((self.review / f"lane-{lane}.status.json").read_text())

    def test_codex_ok_lands_report_and_ok_status(self):
        r, out = self.run_lane("codex")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(out.read_text(), REPORT)
        self.assertFalse(out.with_name(out.name + ".partial").exists())
        st = self.status("codex")
        self.assertEqual((st["lane"], st["rc"], st["ok"]), ("codex", 0, True))
        self.assertEqual(st["report"], str(out.resolve()))
        self.assertEqual(st["packet"], str(self.packet.resolve()))
        self.assertEqual(st["snapshot"], str(self.snap.resolve()))
        self.assertEqual(st["packet_bytes"], self.packet.stat().st_size)
        self.assertEqual(st["log"], str((self.review / "ext-codex.log").resolve()))
        self.assertIn("finished", st)
        self.assertIsInstance(st["secs"], int)

    def test_grok_ok_lands_report(self):
        r, out = self.run_lane("grok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(out.read_text(), REPORT)
        self.assertTrue(self.status("grok")["ok"])

    def test_agy_ok_lands_report(self):
        r, out = self.run_lane("agy")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(out.read_text(), REPORT)
        self.assertTrue(self.status("agy")["ok"])

    def test_nonzero_rc_propagates_and_status_fails(self):
        for lane in ("codex", "grok", "agy"):
            with self.subTest(lane=lane):
                r, out = self.run_lane(lane, "rc7")
                self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
                self.assertFalse(out.exists())
                st = self.status(lane)
                self.assertEqual((st["rc"], st["ok"], st["report"]), (7, False, None))
                self.assertIn("FAILED", r.stdout + r.stderr)

    def test_rc0_but_blank_report_exits_3(self):
        for lane in ("codex", "grok", "agy"):
            with self.subTest(lane=lane):
                r, out = self.run_lane(lane, "blank")
                self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
                self.assertFalse(out.exists())
                st = self.status(lane)
                self.assertFalse(st["ok"])
                self.assertIsNone(st["report"])

    def test_codex_rc0_but_no_report_file_exits_3(self):
        r, out = self.run_lane("codex", "none")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertFalse(out.exists())
        self.assertFalse(self.status("codex")["ok"])

    def test_failure_keeps_older_report_and_says_so(self):
        for lane in ("codex", "grok"):
            with self.subTest(lane=lane):
                out = self.review / f"lane-{lane}.md"
                out.write_text("OLD ROUND REPORT\n")
                r, _ = self.run_lane(lane, "partial-then-fail")
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(out.read_text(), "OLD ROUND REPORT\n")
                st = self.status(lane)
                self.assertFalse(st["ok"])
                self.assertIsNone(st["report"])
                self.assertIn("earlier run", r.stdout + r.stderr)
                partial = out.with_name(out.name + ".partial")
                self.assertEqual(partial.read_text(), "### [LOW] half")

    def test_leftover_partial_is_removed_before_run(self):
        out = self.review / "lane-codex.md"
        partial = out.with_name(out.name + ".partial")
        partial.write_text("### [HIGH] stale partial from an earlier run\n")
        r, _ = self.run_lane("codex", "none")
        self.assertEqual(r.returncode, 3)
        self.assertFalse(out.exists())
        self.assertFalse(partial.exists())

    def test_agy_oversize_writes_status_and_exits_2(self):
        self.packet.write_text("x" * 170_001)
        out = self.review / "lane-agy.md"
        out.write_text("OLD\n")
        r, _ = self.run_lane("agy")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertFalse(self.called.exists())
        self.assertEqual(out.read_text(), "OLD\n")
        st = self.status("agy")
        self.assertEqual((st["rc"], st["ok"], st["report"]), (2, False, None))
        self.assertIn("170001", st["error"])
        self.assertEqual(st["packet_bytes"], 170_001)

    def test_truncation_warning_only_on_final_report(self):
        r, _ = self.run_lane("codex")
        self.assertNotIn("WARNING", r.stdout)
        trunc = REPORT + "note: input was truncated\n"
        (self.bin / "codex").write_text(FAKE.format(py=sys.executable, report=trunc))
        r, _ = self.run_lane("codex")
        self.assertIn("truncation", r.stdout)


if __name__ == "__main__":
    unittest.main()
