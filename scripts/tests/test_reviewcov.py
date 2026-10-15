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

    def test_severity_with_qualifier_inside_bracket_is_numbered(self):
        # chat-image-decider-gender shape: `### [LOW｜需要 owner 拍板]` used to fall out of the numbering,
        # so the map's spec#6 looked dangling while reviewcov's spec#2 silently meant the lane's 5th finding.
        self.task("spec")
        self.lane("spec", "### [LOW] a\n\n### [LOW｜需要 owner 拍板] b\n\n### [MEDIUM / 信心低] c\n\n## 已查證無問題\n")
        self.review(MAP + "| R1 | a | spec#1 |\n| L1 | b | spec#2 |\n| L2 | c | spec#3 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 0, out)
        self.assertRegex(out, r"spec\s+ok\s.*3 findings")

    def test_one_stray_heading_among_proper_ones_is_mixed_not_ok(self):
        self.task("go")
        self.lane("go", "### [HIGH] a\n\n## [MEDIUM] b\n\n## 已查證無問題\n")
        self.review(MAP + "| R1 | a | go#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"go\s+mixed")
        r, out = self.run_cov("--ack-manual", "go")
        self.assertEqual(r.returncode, 0, out)

    def test_schema_counts_disagreeing_with_headings_fail(self):
        self.task("go")
        self.lane("go", report("HIGH"))
        (self.dir / "lane-go.status.json").write_text(json.dumps(
            {"rc": 0, "ok": True, "counts": {"critical": 0, "high": 1, "medium": 1, "low": 0}}))
        self.review(MAP + "| R1 | a | go#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("MEDIUM counts 1 vs headings 0", out)

    def test_source_ids_pointing_at_no_finding_are_dangling(self):
        self.task("go", "comments")
        self.lane("go", report("HIGH"))
        self.lane("comments", report("LOW"))
        self.review(MAP + "| R1 | a | go#1、go#2 |\n| L1 | b | comments#1、comment#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("go#2 (go has 1)", out)
        self.assertIn("comment#1 (no such lane)", out)

    def test_repeated_map_ids_fail_and_section_scopes_one_round(self):
        # journey-entry-and-home shape: two packages' maps in one doc, both numbered from R1.
        self.task("go")
        self.lane("go", report("HIGH"))
        self.review("## 包 1\n### 去重地圖\n" + MAP + "| R1 | old | go#1 |\n"
                    "## 包 2a\n### 去重地圖\n" + MAP + "| R1 | new | spec#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("duplicate map id R1", out)
        r, out = self.run_cov("--section", "包 2a")
        self.assertEqual(r.returncode, 1, out)
        # WHY: inside 包 2a only, 包 1's go#1 no longer hides that this round never merged go#1.
        self.assertRegex(out, r"missing\s+HIGH\s+go#1")
        self.assertNotIn("duplicate", out)

    def test_index_writes_counts_table_and_rows_as_md_and_json(self):
        self.task("go", "ts")
        self.lane("go", "### [HIGH] a | pipe\n- 位置：`a.go:1`\n\n### [LOW] b\n\n## 已查證無問題\n- 位置：not a finding\n")
        self.lane("ts", "# ts\n\n## 已查證無問題\n- fine\n")
        idx = self.dir / "findings-index.md"
        r, out = self.run_cov("--index", str(idx), with_md=False)
        self.assertEqual(r.returncode, 0, out)
        md = idx.read_text(encoding="utf-8")
        self.assertIn("| go | ok | 0 | 1 | 0 | 1 | 2 | 0 |", md)
        self.assertIn("| ts | ok-zero | 0 | 0 | 0 | 0 | 0 | 0 |", md)
        self.assertIn("| go#1 | HIGH | a \\| pipe | `a.go:1` |", md)
        self.assertIn("| go#2 | LOW | b | （缺） |", md)
        rows = json.loads(idx.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual([(x["id"], x["severity"], x["location"]) for x in rows],
                         [("go#1", "HIGH", "`a.go:1`"), ("go#2", "LOW", "")])

    # Independent-review cases (10-15): each was a silent pass before.

    def test_qualified_or_fullwidth_tags_without_numbered_headings_are_not_ok_zero(self):
        for body in ("## [HIGH｜需要 owner 拍板] x\n", "### 【HIGH】 x\n", "**[MEDIUM｜需拍板]** x\n"):
            with self.subTest(body=body):
                self.task("go")
                self.lane("go", "# r\n\n" + body + "\n## 已查證無問題\n- ok\n")
                r, out = self.run_cov(with_md=False)
                self.assertEqual(r.returncode, 1, out)
                self.assertNotIn("ok-zero", out)

    def test_counts_above_zero_without_headings_and_non_integer_counts_fail(self):
        self.task("go")
        self.lane("go", "# r\n\n### H1 退款重複\n\n## 已查證無問題\n- ok\n")
        status = self.dir / "lane-go.status.json"
        for counts in ({"critical": 0, "high": 2, "medium": 0, "low": 0}, {"high": "0", "low": 0.0}):
            with self.subTest(counts=counts):
                status.write_text(json.dumps({"rc": 0, "ok": True, "counts": counts}))
                r, out = self.run_cov(with_md=False)
                self.assertEqual(r.returncode, 1, out)
                self.assertRegex(out, r"go\s+count-mismatch")

    def test_glued_heading_after_prose_is_mixed(self):
        # chat-menu-trim lane-grok shape: `…對不上。### [HIGH] title` on the report's first line.
        self.task("grok")
        self.lane("grok", "我從接縫著手，看有沒有對不上。### [HIGH] 回滾\n\n### [MEDIUM] b\n\n## 已查證無問題\n")
        self.review(MAP + "| R1 | b | grok#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"grok\s+mixed")

    def test_dangling_covers_ok_zero_lanes_and_zero(self):
        self.task("go", "spec")
        self.lane("go", report("HIGH"))
        self.lane("spec", "# spec\n\n## 已查證無問題\n- ok\n")
        self.review(MAP + "| R1 | a | go#1、go#0、spec#3 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("go#0 (go has 1)", out)
        self.assertIn("spec#3 (spec has 0)", out)

    def test_verdict_basis_cells_and_headings_merely_mentioning_exclusion_do_not_account(self):
        self.task("go")
        self.lane("go", report("HIGH", "LOW"))
        self.review("## 地圖（0 未收錄）\n" + MAP + "| R1 | a | go#1 |\n\ngo#2 待補列\n"
                    "## 收斂裁決\n| 編號 | 裁決 | 依據（來源報告） |\n|---|---|---|\n| R1 | confirmed | 與 go#2 不同根因 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 1, out)
        self.assertRegex(out, r"missing\s+LOW\s+go#2")

    def test_index_keeps_tags_stray_lines_and_json_path(self):
        self.task("go")
        self.lane("go", "### [LOW｜需要 owner 拍板] a\n- 位置：\n  - `a.go:1`\n\n## [CRITICAL] b\n\n### [HIGH] c\n"
                        "- **File**: `c.go:3`\n\n## 已查證無問題\n")
        idx = self.dir / "idx.json"
        r, out = self.run_cov("--index", str(idx), "--ack-manual", "go", with_md=False)
        self.assertEqual(r.returncode, 0, out)
        md = (self.dir / "idx.md").read_text(encoding="utf-8")
        self.assertIn("| go#1 | LOW｜需要 owner 拍板 | a | `a.go:1` |", md)
        self.assertIn("| go#2 | HIGH | c | `c.go:3` |", md)
        self.assertIn("| go | 5 | ## [CRITICAL] b |", md)
        rows = json.loads(idx.read_text(encoding="utf-8"))
        self.assertEqual([x["id"] for x in rows], ["go#1", "go#2", None])

    def test_required_field_gaps_warn_without_failing(self):
        self.task("go")
        self.lane("go", "### [HIGH] a\nno location\n\n## 已查證無問題\n")
        self.review("## 去重地圖\n| 編號 | 一句話 | 來源 |\n|---|---|---|\n| L1 | a | go#1 |\n")
        r, out = self.run_cov()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("WARN map table at line 4 has no 宣稱嚴重度／位置 column", out)
        self.assertIn("WARN go: 1 finding(s) without", out)
        self.review("## 去重地圖\n| 編號 | 宣稱嚴重度 | 位置 | 來源 |\n|---|---|---|---|\n| L1 | HIGH | x | go#1 |\n")
        r, out = self.run_cov()
        self.assertIn("WARN L1 is in the L series but claims HIGH", out)


if __name__ == "__main__":
    unittest.main()
