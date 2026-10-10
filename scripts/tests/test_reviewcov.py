# -*- coding: utf-8 -*-
"""Tests for reviewcov.py — lane classification and dedup-map coverage over temp review dirs.

Usage:
  python3 -m unittest discover -s scripts/tests -p 'test_reviewcov.py' -v
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "reviewcov.py"
MAP = "| # | 條目 | 來源 |\n|---|---|---|\n"


def report(*sevs):
    body = "# lane report\n\n"
    for i, s in enumerate(sevs, 1):
        body += f"### [{s}] finding {i}\ndetail\n\n"
    return body + "## 已查證無問題\n- the rest\n"


class ReviewCovTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._td.name) / "review"
        (self.dir / "tasks").mkdir(parents=True)
        self.md = pathlib.Path(self._td.name) / "REVIEW.md"

    def tearDown(self):
        self._td.cleanup()

    def task(self, *names):
        for n in names:
            (self.dir / "tasks" / f"{n}.md").write_text("dispatch\n")

    def lane(self, name, text):
        p = self.dir / f"lane-{name}.md"
        p.write_text(text, encoding="utf-8")
        return p

    def review(self, text):
        self.md.write_text(text, encoding="utf-8")

    def run_cov(self, *extra, with_md=True):
        args = [sys.executable, str(SCRIPT), str(self.dir)]
        if with_md:
            args.append(str(self.md))
        r = subprocess.run(args + list(extra), capture_output=True, text=True, timeout=60)
        return r, r.stdout + r.stderr

    def test_all_ok_lanes_with_ids_in_table_pass(self):
        self.task("go", "ts")
        self.lane("go", report("HIGH", "LOW"))
        self.lane("ts", report("MEDIUM"))
        self.review("| 編號 | 宣稱嚴重度 | 一句話 | 位置 | 與已知限制的關係 | 來源 |\n|---|---|---|---|---|---|\n"
                    "| 1 | HIGH | a | f.go | - | go#1, ts#1 |\n| 2 | LOW | b | f.go | - | go#2 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("3/3", out)

    def test_ok_zero_lane_passes(self):
        self.task("go")
        self.lane("go", "# lane go\n\n## 已查證無問題\n- nothing wrong\n")
        self.review("no findings\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("ok-zero", out)

    def test_expected_lane_without_report_is_missing(self):
        self.task("go", "ts")
        self.lane("go", report("LOW"))
        self.review(MAP + "| 1 | x | go#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"ts\s.*missing")
        self.assertIn("FAIL:", out)

    def test_empty_lane_file_fails(self):
        self.task("go")
        self.lane("go", "")
        self.review("x\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"go\s.*empty")

    def test_blank_lane_file_fails(self):
        self.task("go")
        self.lane("go", "  \n\n")
        self.review("x\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"go\s.*empty")

    def test_unformatted_fails_without_ack_and_passes_with_ack(self):
        (self.dir / "ext-codex.log").write_text("")
        self.lane("codex", "Here are my thoughts: the code is mostly fine.\n")
        self.review("x\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"codex\s.*unformatted")
        r, out = self.run_cov("--ack-manual", "codex")
        self.assertEqual(r.returncode, 0, out)
        self.assertRegex(out, r"codex\s.*acked")
        r, out = self.run_cov("--ack-manual", "lane-codex")
        self.assertEqual(r.returncode, 0, out)

    def test_report_older_than_baseline_is_stale(self):
        self.task("go")
        p = self.lane("go", report("LOW"))
        (self.dir / "baseline.sha256").write_text("abc  file\n")
        old = time.time() - 3600
        os.utime(p, (old, old))
        self.review(MAP + "| 1 | x | go#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"go\s.*stale")

    def test_report_newer_than_baseline_is_ok(self):
        self.task("go")
        base = self.dir / "baseline.sha256"
        base.write_text("abc  file\n")
        old = time.time() - 3600
        os.utime(base, (old, old))
        self.lane("go", report("LOW"))
        self.review(MAP + "| 1 | x | go#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 0, out)

    def test_failed_status_beats_present_report(self):
        self.lane("codex", report("HIGH"))
        (self.dir / "lane-codex.status.json").write_text(json.dumps({"lane": "codex", "rc": 1, "ok": False}))
        self.review(MAP + "| 1 | x | codex#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"codex\s.*failed")

    def test_ok_status_with_report_passes(self):
        self.lane("codex", report("HIGH"))
        (self.dir / "lane-codex.status.json").write_text(json.dumps({"lane": "codex", "rc": 0, "ok": True}))
        self.review(MAP + "| 1 | x | codex#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 0, out)

    def test_id_only_in_narrative_is_missing(self):
        self.task("go")
        self.lane("go", report("HIGH"))
        self.review("## 合併\nWe looked at go#1 and decided it was a dup.\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"missing\s+HIGH\s+go#1")

    def test_id_in_table_row_is_covered(self):
        self.task("go")
        self.lane("go", report("HIGH"))
        self.review("prose\n\n  | 條目 | 來源 | 嚴重度 | 裁決 | 處置 |\n  |---|---|---|---|---|\n"
                    "  | dup | go#1 | HIGH | confirmed | fixed |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 0, out)

    def test_id_in_excluded_section_is_covered_until_next_peer_heading(self):
        self.task("go")
        self.lane("go", report("HIGH", "LOW"))
        self.review("## 未收錄（intake gate）\n- go#1 not a bug\n### detail\n- still inside\n\n"
                    "## 其他\n- go#2 mentioned in prose only\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertNotRegex(out, r"missing\s+HIGH\s+go#1")
        self.assertRegex(out, r"missing\s+LOW\s+go#2")

    def test_waived_lane_passes_and_prints_reason(self):
        self.task("go", "ts")
        self.lane("go", report("LOW"))
        self.review(MAP + "| 1 | x | go#1 |\n")
        r, out = self.run_cov("--waive", "ts=no TS changes in this diff")
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("no TS changes in this diff", out)

    def test_no_expected_list_fails_as_unknown(self):
        (self.dir / "tasks").rmdir()
        self.lane("go", report("LOW"))
        self.review(MAP + "| 1 | x | go#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("unknown", out)

    def test_expect_flag_supplies_expected_list(self):
        (self.dir / "tasks").rmdir()
        self.lane("go", report("LOW"))
        self.review(MAP + "| 1 | x | go#1 |\n")
        r, out = self.run_cov("--expect", "go", "--expect", "lane-ts")
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"ts\s.*missing")
        r, out = self.run_cov("--expect", "go")
        self.assertEqual(r.returncode, 0, out)

    def test_list_is_informational_without_expected_list(self):
        (self.dir / "tasks").rmdir()
        self.lane("go", report("LOW", "HIGH"))
        r, out = self.run_cov("--list", with_md=False)
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("go#2", out)

    def test_id_boundary_go1_not_matched_by_go12_or_algo1(self):
        self.task("go")
        self.lane("go", report("HIGH"))
        self.review(MAP + "| 1 | x | go#12 |\n| 2 | y | algo#1 |\n| 3 | z | lane-go#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"missing\s+HIGH\s+go#1")

    def test_ext_log_makes_lane_expected(self):
        self.task("go")
        self.lane("go", report("LOW"))
        (self.dir / "ext-grok.log").write_text("")
        self.review(MAP + "| 1 | x | go#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"grok\s.*missing")

    def test_misformatted_severity_headings_are_unformatted_not_ok_zero(self):
        shapes = {
            "h2": "## [HIGH] x\nbody\n",
            "h4": "#### [MEDIUM] y\nbody\n",
            "numbered": "### 1. [HIGH] z\nbody\n",
            "bold": "**[HIGH]** w\nbody\n",
        }
        for label, finding in shapes.items():
            with self.subTest(shape=label):
                self.lane("codex", "# report\n\n" + finding + "\n## 已查證無問題\n- rest\n")
                (self.dir / "ext-codex.log").write_text("")
                self.review("x\n")
                r, out = self.run_cov()
                self.assertEqual(r.returncode, 1, out)
                self.assertRegex(out, r"codex\s.*unformatted")
                self.assertNotIn("ok-zero", out)
                r, out = self.run_cov("--ack-manual", "codex")
                self.assertEqual(r.returncode, 0, out)

    def test_no_review_md_gates_lanes(self):
        self.task("go", "ts")
        self.lane("go", report("LOW"))
        r, out = self.run_cov(with_md=False)
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("FAIL:", out)
        self.lane("ts", report())
        r, out = self.run_cov(with_md=False)
        self.assertEqual(r.returncode, 0, out)

    def test_no_review_md_and_unknown_coverage_fails(self):
        (self.dir / "tasks").rmdir()
        self.lane("go", report("LOW"))
        r, out = self.run_cov(with_md=False)
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("unknown", out)

    def test_list_stays_informational_with_failed_lanes(self):
        self.task("go", "ts")
        self.lane("go", report("LOW"))
        r, out = self.run_cov("--list", with_md=False)
        self.assertEqual(r.returncode, 0, out)
        self.assertRegex(out, r"ts\s.*missing")

    def test_id_only_in_basis_cell_of_verdict_table_is_missing(self):
        self.task("go")
        self.lane("go", report("HIGH"))
        self.review("| 編號 | 裁決 | 依據 |\n|---|---|---|\n| R5 | confirmed | 見 go#1 的分析 |\n\n"
                    "| 測試 | 結果 |\n|---|---|\n| go#1 repro | pass |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"missing\s+HIGH\s+go#1")

    def test_id_in_other_column_of_map_table_is_missing(self):
        self.task("go")
        self.lane("go", report("HIGH"))
        self.review("| # | 條目 | 來源 | 處置 |\n|---|---|---|---|\n| 1 | dup of go#1 | ts#3 | 見 go#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"missing\s+HIGH\s+go#1")

    def test_id_in_source_cell_is_covered(self):
        self.task("go")
        self.lane("go", report("HIGH", "LOW"))
        self.review("| # | 條目 | 來源 | 嚴重度 | 裁決 | 處置 |\n|---|---|---|---|---|---|\n"
                    "| 1 | a | go#1 | HIGH | ok | fix |\n\nprose\n\n"
                    "| 條目 | 來源 | 嚴重度 | 裁決 | 處置 |\n|---|---|---|---|---|\n| b | go#2 | LOW | ok | fix |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 0, out)

    def test_id_in_excluded_section_table_is_covered(self):
        self.task("go")
        self.lane("go", report("HIGH"))
        self.review("## 未收錄\n\n| 條目 | 理由 |\n|---|---|\n| go#1 | not a bug |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 0, out)


if __name__ == "__main__":
    unittest.main()
