# -*- coding: utf-8 -*-
"""Tests for wf_reports.py — landing Workflow reports and per-lane status files from a journal.

Usage:
  python3 -m unittest discover -s scripts/tests -p 'test_wf_reports.py' -v
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "wf_reports.py"


class WfReportsTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._td.name)
        self.out = self.tmp / "review"
        self.journal = self.tmp / "journal.jsonl"

    def tearDown(self):
        self._td.cleanup()

    def write_journal(self, *events):
        self.journal.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    def run_wf(self, *extra):
        r = subprocess.run([sys.executable, str(SCRIPT), "--journal", str(self.journal),
                            "--out-dir", str(self.out), *extra], capture_output=True, text=True, timeout=60)
        return r, r.stdout + r.stderr

    def status(self, label):
        return json.loads((self.out / f"{label}.status.json").read_text())

    def test_started_and_result_lands_report_and_ok_status(self):
        self.write_journal({"type": "started", "agentId": "a1", "label": "lane-go"},
                           {"type": "result", "agentId": "a1", "result": {"report_markdown": "### [LOW] x\n"}})
        r, out = self.run_wf()
        self.assertEqual(r.returncode, 0, out)
        self.assertEqual((self.out / "lane-go.md").read_text(), "### [LOW] x\n")
        st = self.status("lane-go")
        self.assertEqual((st["source"], st["agent_id"], st["rc"], st["ok"]), ("workflow", "a1", 0, True))
        self.assertEqual(st["journal"], str(self.journal.resolve()))
        self.assertEqual(st["report"], str((self.out / "lane-go.md").resolve()))
        self.assertIsNone(st["error"])
        self.assertIn("finished", st)

    def test_started_without_result_writes_failed_status(self):
        self.out.mkdir()
        (self.out / "lane-go.md").write_text("old round\n")
        self.write_journal({"type": "started", "agentId": "a1", "label": "lane-go"})
        r, out = self.run_wf()
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("MISSING", out)
        st = self.status("lane-go")
        self.assertEqual((st["rc"], st["ok"], st["report"]), (1, False, None))
        self.assertIn("no result", st["error"])
        self.assertEqual((self.out / "lane-go.md").read_text(), "old round\n")

    def test_result_without_field_writes_failed_status(self):
        self.write_journal({"type": "started", "agentId": "a1", "label": "lane-go"},
                           {"type": "result", "agentId": "a1", "result": {"summary": "x"}})
        r, out = self.run_wf()
        self.assertEqual(r.returncode, 1, out)
        self.assertEqual(self.status("lane-go")["rc"], 1)

    def test_task_stem_never_started_is_missing(self):
        (self.out / "tasks").mkdir(parents=True)
        for n in ("go", "ts"):
            (self.out / "tasks" / f"{n}.md").write_text("dispatch\n")
        self.write_journal({"type": "started", "agentId": "a1", "label": "lane-go"},
                           {"type": "result", "agentId": "a1", "result": {"report_markdown": "ok\n"}})
        r, out = self.run_wf()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"MISSING lane-ts.*never started")
        st = self.status("lane-ts")
        self.assertEqual((st["rc"], st["ok"]), (1, False))
        self.assertIn("never started", st["error"])
        self.assertTrue(self.status("lane-go")["ok"])

    def test_only_excludes_never_started_task_stems(self):
        (self.out / "tasks").mkdir(parents=True)
        for n in ("go", "ts"):
            (self.out / "tasks" / f"{n}.md").write_text("dispatch\n")
        self.write_journal({"type": "started", "agentId": "a1", "label": "lane-go"},
                           {"type": "result", "agentId": "a1", "result": {"report_markdown": "ok\n"}})
        r, out = self.run_wf("--only", "lane-go")
        self.assertEqual(r.returncode, 0, out)
        self.assertFalse((self.out / "lane-ts.status.json").exists())


if __name__ == "__main__":
    unittest.main()
