# -*- coding: utf-8 -*-
"""board.py process items: ACCEPTANCE.md due rows and last retro, tasks recorded since it, instruction-file sizes.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_board.py' -v
"""
import datetime
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import board  # noqa: E402

LEDGER = """# 流程改動驗收帳

## 待驗
| 到期 | 改動 | 指令 | 判準 | 出處 |
|---|---|---|---|---|
| 2026-10-19 | graphify 掛點（第 1／4 階段） | 手動 `a\\|b` | x | s |
| 事件：下一個 UI 包；最晚 2026-10-31 | 排程四改 | w | x | s |
| 下一個大包 | 沒寫日期的列 | w | x | s |
| 2026-11-07 | 驗收帳 | w | x | s |

## 結案
| 日期 | 改動 | 判定 | 證據 | 處置 |
|---|---|---|---|---|
| 2026-12-01 | 不該被當成待驗 | 有用 | e | d |

## 檢討紀錄
| 日期 | 範圍 | 報告 | 採用 |
|---|---|---|---|
| 2026-10-03 | a | r | x |
| 2026-10-17 | b | r | x |
"""


def g(cwd, *args, env=None):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          check=True, capture_output=True, text=True, env=env).stdout


class AcceptanceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self._tmp.name) / "ACCEPTANCE.md"
        self.path.write_text(LEDGER, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_due_rows_use_the_last_date_and_a_missing_date_counts_as_due(self):
        acc = board.read_acceptance(self.path, today=datetime.date(2026, 10, 31))
        self.assertEqual([p["what"] for p in acc["pending"]], ["graphify 掛點", "排程四改", "沒寫日期的列", "驗收帳"])
        self.assertEqual([p["what"] for p in acc["due"]], ["graphify 掛點", "排程四改", "沒寫日期的列"])

    def test_the_due_day_itself_is_due_and_the_day_before_is_not(self):
        due = lambda d: [p["what"] for p in board.read_acceptance(self.path, today=d)["due"]]  # noqa: E731
        self.assertNotIn("graphify 掛點", due(datetime.date(2026, 10, 18)))
        self.assertIn("graphify 掛點", due(datetime.date(2026, 10, 19)))

    def test_closed_rows_are_not_pending_and_last_retro_is_the_latest_date(self):
        acc = board.read_acceptance(self.path, today=datetime.date(2026, 10, 17))
        self.assertNotIn("不該被當成待驗", [p["what"] for p in acc["pending"]])
        self.assertEqual(acc["last_retro"], datetime.date(2026, 10, 17))

    def test_missing_file_is_none(self):
        self.assertIsNone(board.read_acceptance(pathlib.Path(self._tmp.name) / "nope.md"))


class RepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self._tmp.name).resolve()
        g(self.repo, "init", "-q", "-b", "dev")
        (self.repo / ".gitignore").write_text("tmp/\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, size):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x" * size, encoding="utf-8")

    def commit(self, subject, day):
        env = {**os.environ, "GIT_AUTHOR_DATE": f"{day}T12:00:00", "GIT_COMMITTER_DATE": f"{day}T12:00:00"}
        g(self.repo, "add", "-A", env=env)
        g(self.repo, "commit", "-q", "--allow-empty", "-m", subject, env=env)

    def test_tasks_since_counts_distinct_record_commits_after_the_retro_day(self):
        self.commit("docs(prps): before 七階段記錄", "2026-10-16")
        self.commit("docs(prps): same-day 七階段記錄（精簡路）", "2026-10-17")
        self.commit("docs(prps): a 包 2a 七階段記錄", "2026-10-18")
        self.commit("docs(prps): a 七階段記錄", "2026-10-19")
        self.commit("docs(prps): b 狀態改為待部署", "2026-10-19")
        self.commit("feat(b): code", "2026-10-19")
        self.commit("docs(prps): c 七階段記錄（精簡路）", "2026-10-20")
        self.assertEqual(board.tasks_since(self.repo, "dev", datetime.date(2026, 10, 17)), ["c", "a"])
        self.assertEqual(board.tasks_since(self.repo, "dev", None), [])

    def test_doc_report_flags_over_cap_and_fast_growth_since_the_retro_but_not_small_files(self):
        self.write("CLAUDE.md", 1_000)
        self.write("a/CLAUDE.md", 10_000)
        self.write(".claude/rules/x/big.md", 30_000)
        self.write("docs/NOT.md", 90_000)
        self.commit("init", "2026-10-10")
        self.write("CLAUDE.md", 2_500)                      # +150% but only +1.5KB: not flagged
        self.write("a/CLAUDE.md", 12_500)                   # +25% and +2.5KB: grew too fast
        self.write(".claude/rules/x/big.md", 41_000)        # over the cap
        self.write("tmp/wt/CLAUDE.md", 99_000)              # gitignored copy skipped
        self.commit("grow", "2026-10-12")
        self.write("a/CLAUDE.md", 12_200)                   # uncommitted edit: vs HEAD differs, still +22%
        rep = board.doc_report(self.repo, "dev", datetime.date(2026, 10, 10))
        rows = {r["path"]: r for r in rep["rows"]}
        self.assertEqual(sorted(rows), [".claude/rules/x/big.md", "CLAUDE.md", "a/CLAUDE.md"])
        self.assertEqual({p: r["flag"] for p, r in rows.items()},
                         {".claude/rules/x/big.md": "過大", "CLAUDE.md": "", "a/CLAUDE.md": "長太快"})
        self.assertEqual((rows["a/CLAUDE.md"]["head"], rows["a/CLAUDE.md"]["then"]), (12_500, 10_000))
        self.assertEqual(rep["total_then"], 41_000)

    def test_doc_report_lists_untracked_rule_files(self):
        self.write("CLAUDE.md", 100)
        self.commit("init", "2026-10-10")
        self.write(".claude/rules/x/new.md", 500)
        rows = {r["path"]: r for r in board.doc_report(self.repo, "dev", None)["rows"]}
        self.assertIn(".claude/rules/x/new.md", rows)
        self.assertIsNone(rows[".claude/rules/x/new.md"]["head"])


if __name__ == "__main__":
    unittest.main()
