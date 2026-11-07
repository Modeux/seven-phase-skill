# -*- coding: utf-8 -*-
"""Tests for synth.py — phase-6 synthesis dispatch without hand work (real wf_reports.py and reviewcov.py).

Usage:
  python3 -m unittest discover -s scripts/tests -p 'test_synth.py' -v
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "synth.py"
REPORT = "### [HIGH] {t}\n- 位置：internal/x/{t}.go:12\n\nbody\n"


class SynthTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._td.name)
        self.review = self.tmp / "slug" / "review"
        (self.review / "tasks").mkdir(parents=True)
        for lane in ("go", "ts"):
            (self.review / "tasks" / f"{lane}.md").write_text(f"# lane {lane}\n", encoding="utf-8")
        self.journal = self.tmp / "journal.jsonl"
        self.journal.write_text("".join(json.dumps(e) + "\n" for e in [
            {"type": "started", "agentId": "a1", "label": "lane-go"},
            {"type": "result", "agentId": "a1", "result": {"report_markdown": REPORT.format(t="g1"),
                                                            "counts": {"critical": 0, "high": 1, "medium": 0, "low": 0}}},
            {"type": "started", "agentId": "a2", "label": "lane-ts"},
            {"type": "result", "agentId": "a2", "result": {"report_markdown": REPORT.format(t="t1")}},
        ]), encoding="utf-8")
        # external lane codex: done; grok: CLI still running (log, no status)
        (self.review / "ext-codex.log").write_text("ran\n", encoding="utf-8")
        (self.review / "lane-codex.md").write_text(REPORT.format(t="c1"), encoding="utf-8")
        (self.review / "lane-codex.status.json").write_text(json.dumps(
            {"rc": 0, "ok": True, "source": "codex", "finished": "2026-11-07T10:00:00+00:00"}), encoding="utf-8")
        (self.review / "ext-grok.log").write_text("still running\n", encoding="utf-8")
        self.review_md = self.tmp / "REVIEW.md"

    def tearDown(self):
        self._td.cleanup()

    def run_synth(self, *extra, journal=True):
        cmd = [sys.executable, str(SCRIPT), "--review-dir", str(self.review), "--review-md", str(self.review_md)]
        if journal:
            cmd += ["--journal", str(self.journal)]
        r = subprocess.run(cmd + list(extra), capture_output=True, text=True, timeout=120)
        return r, r.stdout + r.stderr

    def test_pending_external_lane_exits_3_and_writes_nothing(self):
        r, out = self.run_synth()
        self.assertEqual(r.returncode, 3, out)
        self.assertIn("grok", out)
        self.assertFalse((self.review / "synth-task.md").exists())
        # the Workflow reports still landed, so a rerun needs no --journal
        self.assertEqual((self.review / "lane-go.md").read_text(encoding="utf-8"), REPORT.format(t="g1"))

    def test_waived_external_lane_writes_task_file_with_ids_and_lane_states(self):
        r, out = self.run_synth("--waive", "grok=80 分鐘零輸出，換鏡頭 perf", "--note", "ts 第一次 429 中斷後重跑", "--section", "第二輪")
        self.assertEqual(r.returncode, 0, out)
        task = (self.review / "synth-task.md").read_text(encoding="utf-8")
        self.assertIn(str(self.review_md), task)
        self.assertIn("3 條每一條", task)  # go#1, ts#1, codex#1
        self.assertIn("| grok | 外審 | 免：80 分鐘零輸出，換鏡頭 perf |", task)
        self.assertIn("| go | 內審 | ok | C0／H1／M0／L0 |", task)
        self.assertIn("| codex | 外審 | ok | — |", task)
        self.assertIn("ts 第一次 429 中斷後重跑", task)
        self.assertIn("第二輪", task)
        self.assertIn("--section 第二輪 --waive=grok=80 分鐘零輸出，換鏡頭 perf", task)
        self.assertNotIn("{{", task)
        self.assertIn('Agent(subagent_type="doc-slice"', out)
        self.assertTrue((self.review / "findings-index.json").is_file())

    def test_missing_internal_lane_exits_1_and_removes_stale_task_file(self):
        self.journal.write_text(json.dumps({"type": "started", "agentId": "a1", "label": "lane-go"}) + "\n"
                                + json.dumps({"type": "result", "agentId": "a1", "result": {"report_markdown": REPORT.format(t="g1")}}) + "\n",
                                encoding="utf-8")
        (self.review / "synth-task.md").write_text("stale\n", encoding="utf-8")
        r, out = self.run_synth("--waive", "grok=x")
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("ts", out)
        self.assertFalse((self.review / "synth-task.md").exists())

    def test_rerun_without_journal_after_external_lands(self):
        self.run_synth()
        (self.review / "lane-grok.md").write_text(REPORT.format(t="k1"), encoding="utf-8")
        (self.review / "lane-grok.status.json").write_text(json.dumps({"rc": 0, "ok": True, "finished": "2026-11-07T10:30:00+00:00"}), encoding="utf-8")
        r, out = self.run_synth(journal=False)
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("4 條每一條", (self.review / "synth-task.md").read_text(encoding="utf-8"))

    def test_bad_waive_is_usage_error(self):
        r, out = self.run_synth("--waive", "grok")
        self.assertEqual(r.returncode, 2, out)


if __name__ == "__main__":
    unittest.main()
