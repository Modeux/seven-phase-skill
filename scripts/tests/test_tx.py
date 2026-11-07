# -*- coding: utf-8 -*-
"""Tests for tx.py (transcript tool_use query) on the hand-made transcripts in fixtures_tx/.

Usage:
  python3 -m unittest discover -s scripts/tests -p 'test_tx.py' -v
"""
import calendar
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures_tx"
SID_A = "aaaaaaaa-1111-2222-3333-444444444444"
SID_B = "bbbbbbbb-1111-2222-3333-444444444444"


class TxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "projects"
        # WHY: copytree's default copy2 keeps fixture mtimes; the tool skips files untouched since --since,
        # so a fresh copy (mtime = now) is what a live transcript looks like.
        shutil.copytree(FIXTURES, self.root, copy_function=shutil.copy)
        self.main = self.root / "-fake-repo"

    def tearDown(self):
        for p in self.root.rglob("*"):
            os.chmod(p, 0o755 if p.is_dir() else 0o644)
        self.tmp.cleanup()

    def tx(self, *args, ok=True):
        env = dict(os.environ, TZ="UTC")
        p = subprocess.run([sys.executable, str(SCRIPTS / "tx.py"), *args, "--projects-root", str(self.root),
                            "--checkout", "/fake/repo"], capture_output=True, text=True, timeout=60, env=env)
        if ok and p.returncode != 0:
            raise AssertionError(f"tx.py exit {p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}")
        return p

    def table(self, out):
        """{key: (rows, sessions, agents)} of a count table, plus the total line."""
        rows = {}
        for line in out.splitlines()[2:]:
            parts = line.split(None, 3)
            if len(parts) == 4 and parts[0].isdigit():
                rows[parts[3]] = tuple(int(x) for x in parts[:3])
        return rows

    def test_count_by_tool_skips_sidechain_old_and_blob_duplicates(self):
        p = self.tx("count", "--since", "2026-11-01", "--by", "tool")
        self.assertTrue(p.stdout.startswith("TX count 8 rows"), p.stdout)
        t = self.table(p.stdout)
        self.assertEqual(t["Bash"][0], 3)  # tu1, ta1, tb1 — not tu5 (sidechain), tu0 (old), blob copy of tu1
        self.assertEqual(t["Read"][0], 2)
        self.assertEqual((t["Write"][0], t["Agent"][0], t["Edit"][0]), (1, 1, 1))
        self.assertEqual(t["total"], (8, 2, 1))

    def test_count_by_agent(self):
        t = self.table(self.tx("count", "--since", "2026-11-01", "--by", "agent").stdout)
        self.assertEqual(t["main"], (6, 2, 0))
        self.assertEqual(t["tdd-guide"], (2, 1, 1))

    def test_count_by_session(self):
        t = self.table(self.tx("count", "--since", "2026-11-01", "--by", "session").stdout)
        self.assertEqual(t[SID_A[:8]][0], 7)
        self.assertEqual(t[SID_B[:8]][0], 1)

    def test_count_by_file_and_day(self):
        t = self.table(self.tx("count", "--since", "2026-11-01", "--by", "file", "--tool", "Read").stdout)
        self.assertEqual(t["/repo/backend-golang/a.go"], (2, 1, 1))
        t = self.table(self.tx("count", "--since", "2026-11-01", "--by", "day").stdout)
        self.assertEqual((t["2026-11-02"][0], t["2026-11-03"][0]), (7, 1))

    def test_agent_filter_main_and_type(self):
        self.assertTrue(self.tx("count", "--since", "2026-11-01", "--agent", "main").stdout.startswith("TX count 6 rows"))
        self.assertTrue(self.tx("count", "--since", "2026-11-01", "--agent", "tdd-guide").stdout
                        .startswith("TX count 2 rows"))
        self.assertTrue(self.tx("count", "--since", "2026-11-01", "--agent", "main", "tdd-guide").stdout
                        .startswith("TX count 8 rows"))

    def test_tool_filter_comma_list(self):
        self.assertTrue(self.tx("count", "--since", "2026-11-01", "--tool", "Bash,Read").stdout
                        .startswith("TX count 5 rows"))

    def test_grep_matches_main_input_field_per_tool(self):
        self.assertTrue(self.tx("count", "--since", "2026-11-01", "--grep", "backend-golang").stdout
                        .startswith("TX count 2 rows"))  # Read file_path, main + subagent
        self.assertTrue(self.tx("count", "--since", "2026-11-01", "--grep", "^tdd-guide").stdout
                        .startswith("TX count 1 rows"))  # Agent subagent_type
        self.assertTrue(self.tx("count", "--since", "2026-11-01", "--grep", "S1 測試").stdout
                        .startswith("TX count 1 rows"))  # Agent prompt
        self.assertTrue(self.tx("count", "--since", "2026-11-01", "--grep", "porcelain").stdout
                        .startswith("TX count 1 rows"))  # Bash command, sidechain/blob copies not counted

    def test_result_grep_reads_tool_result_and_hook_attachments(self):
        p = self.tx("list", "--since", "2026-11-01", "--result-grep", "generated-file-guard:")
        self.assertTrue(p.stdout.startswith("TX list 1 rows"), p.stdout)
        self.assertIn("/repo/db/migrations/0001_x.sql", p.stdout)
        # WHY: PostToolUse additionalContext (ts-lint) is an `attachment` record keyed by toolUseID, not
        # part of the tool_result — ACCEPTANCE counts `ts-lint:` and would read 0.
        p = self.tx("list", "--since", "2026-11-01", "--result-grep", "ts-lint:")
        self.assertTrue(p.stdout.startswith("TX list 1 rows"), p.stdout)
        self.assertIn("/repo/web/a.ts", p.stdout)

    def test_since_includes_older_records(self):
        self.assertTrue(self.tx("count", "--since", "2026-10-01").stdout.startswith("TX count 9 rows"))

    def test_days_window_relative_to_now(self):
        # Fixture records are from 2026-10/11; a 1-day window from now must not reach them unless now is close.
        newest = calendar.timegm(time.strptime("2026-11-03T02:00:00", "%Y-%m-%dT%H:%M:%S"))
        expect_zero = time.time() - newest > 2 * 86400
        p = self.tx("count", "--days", "1")
        if expect_zero:
            self.assertTrue(p.stdout.startswith("TX count 0 rows"), p.stdout)

    def test_file_mtime_before_window_is_skipped(self):
        old = calendar.timegm(time.strptime("2026-10-01", "%Y-%m-%d"))
        os.utime(self.main / f"{SID_B}.jsonl", (old, old))
        self.assertTrue(self.tx("count", "--since", "2026-11-01").stdout.startswith("TX count 7 rows"))

    def test_all_worktrees_adds_worktree_transcripts(self):
        self.assertTrue(self.tx("count", "--since", "2026-11-01", "--all-worktrees").stdout
                        .startswith("TX count 9 rows"))

    def test_list_line_shape_and_summary_cap(self):
        p = self.tx("list", "--since", "2026-11-01")
        lines = p.stdout.splitlines()
        self.assertTrue(lines[0].startswith("TX list 8 rows"), p.stdout)
        self.assertIn("2026-11-02 01:00 aaaaaaaa main Bash git status --porcelain", lines)
        self.assertIn("2026-11-02 01:03 aaaaaaaa main Agent tdd-guide", lines)
        self.assertIn("2026-11-02 01:03 aaaaaaaa tdd-guide Bash python3 -m unittest discover", lines)
        long = [ln for ln in lines if "git log --oneline" in ln]
        self.assertEqual(len(long), 1)
        summary = long[0].split(" Bash ", 1)[1]
        self.assertLessEqual(len(summary), 120)
        self.assertNotIn("\n", summary)

    def test_list_limit_keeps_newest(self):
        lines = self.tx("list", "--since", "2026-11-01", "--limit", "2").stdout.splitlines()
        body = [ln for ln in lines[1:] if ln.startswith("2026-")]
        self.assertEqual(len(body), 2)
        self.assertTrue(body[-1].startswith("2026-11-03 02:00 bbbbbbbb"), body)

    def test_list_jsonl_has_input_and_result(self):
        p = self.tx("list", "--since", "2026-11-01", "--jsonl", "--tool", "Write")
        recs = [json.loads(ln) for ln in p.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["tool"], "Write")
        self.assertEqual(r["input"]["file_path"], "/repo/db/migrations/0001_x.sql")
        self.assertIn("generated-file-guard:", r["result"])
        self.assertTrue(r["is_error"])
        self.assertEqual(r["session"], SID_A)
        self.assertEqual(r["agent"], "main")

    def test_zero_rows_exit_zero(self):
        p = self.tx("count", "--since", "2026-11-01", "--grep", "no-such-thing-anywhere")
        self.assertEqual(p.returncode, 0)
        self.assertIn("0 rows", p.stdout.splitlines()[0])

    def test_bad_json_line_skipped_and_reported(self):
        p = self.tx("count", "--since", "2026-11-01")
        self.assertIn("bad_lines=1", p.stdout.splitlines()[0])

    def test_unreadable_file_skipped_with_stderr_note(self):
        os.chmod(self.main / f"{SID_B}.jsonl", 0)
        p = self.tx("count", "--since", "2026-11-01")
        self.assertTrue(p.stdout.startswith("TX count 7 rows"), p.stdout)
        self.assertIn(f"{SID_B}.jsonl", p.stderr)

    def test_days_and_since_are_exclusive(self):
        p = self.tx("count", "--days", "3", "--since", "2026-11-01", ok=False)
        self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main()
