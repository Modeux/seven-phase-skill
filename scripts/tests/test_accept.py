# -*- coding: utf-8 -*-
"""Tests for accept.py (ACCEPTANCE 待驗 runner and `show`) on a fake ACCEPTANCE.md and a fake git repo.

Usage:
  python3 -m unittest discover -s scripts/tests -p 'test_accept.py' -v
"""
import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]

ACCEPTANCE = """# 驗收帳

說明段落 | 不是表格。

## 待驗
| 到期 | 改動 | 指令 | 判準 | 出處 |
|---|---|---|---|---|
| 2026-11-01 | {WHAT1} | `grep -c needle notes.txt`；`grep -n needle notes.txt \\| wc -l`；`grep -c '```mermaid' notes.txt`；`python3 scripts/numstat.py --help`；`grep -n x .claude/PRPs/plans/<slug>/PLAN.md`；手動：讀 transcript 數次數 | 判準甲：命中 ≥1 | 出處甲 |
| 事件：下一個任務；最晚 2026-12-31 | 改動乙 | 再 `python3 scripts/zz_missing_tool.py --x`；`rm -rf tmp/old`；`python3 scripts/numstat.py --apply` | 判準乙 | 出處乙 |
| 2027-01-15 | 改動丙 | `ls`；`grep -c nothere notes.txt` | 判準丙 | 出處丙 |
| 事件：某事；最晚 2026-10-31 | 改動丁（事件最晚已過） | 問 owner：有沒有用 | 判準丁 | 出處丁 |
| 2027-02-20 | 改動戊 | `bash -c 'sleep 5'` | 判準戊 | 出處戊 |

## 結案
| 日期 | 改動 | 判定 | 證據 | 處置 |
|---|---|---|---|---|
| 2026-10-01 | 舊 | 過 | x | y |
"""
WHAT1 = "改動甲：grep 驗證這一列的指令能不能跑而且這段文字超過四十個字元，再多加幾個字讓它超過"
ACCEPTANCE = ACCEPTANCE.replace("{WHAT1}", WHAT1)

GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@example.com"]


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *GIT_ID, *args], capture_output=True, text=True, check=True).stdout


class AcceptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.acc = base / "ACCEPTANCE.md"
        self.acc.write_text(ACCEPTANCE, encoding="utf-8")
        self.repo = base / "repo"
        (self.repo / "tmp" / "old").mkdir(parents=True)
        (self.repo / "notes.txt").write_text("needle\nneedle\nhay\n```mermaid\n", encoding="utf-8")
        self.out = base / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def accept(self, *args, ok=True):
        p = subprocess.run([sys.executable, str(SCRIPTS / "accept.py"), *args], capture_output=True, text=True,
                           timeout=120)
        if ok and p.returncode != 0:
            raise AssertionError(f"accept.py exit {p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}")
        return p

    def run_rows(self, *args, ok=True):
        return self.accept("--file", str(self.acc), "--repo", str(self.repo), "--today", "2026-11-07",
                           "--out", str(self.out), *args, ok=ok)

    def section(self, out, n):
        """Text of the `[n] …` section up to the next section."""
        lines, keep = [], False
        for line in out.splitlines():
            if line.startswith("["):
                keep = line.startswith(f"[{n}] ")
            if keep:
                lines.append(line)
        return "\n".join(lines)

    # --- status ---------------------------------------------------------------------------------------

    def test_status_counts_on_fixed_today(self):
        first = self.run_rows().stdout.splitlines()[0]
        self.assertEqual(first, "ACCEPT due=2 event=1 later=2 ran=0 failed=0")

    def test_today_moves_event_and_later_rows_to_due(self):
        first = self.run_rows("--today", "2027-03-01").stdout.splitlines()[0]
        self.assertEqual(first, "ACCEPT due=5 event=0 later=0 ran=0 failed=0")

    def test_default_due_shows_due_rows_in_full_and_event_rows_as_one_line(self):
        out = self.run_rows().stdout
        self.assertGreater(len(WHAT1), 40)
        self.assertIn(f"[1] 到期 2026-11-01 | 改動 {WHAT1[:40]}\n", out)
        self.assertIn("判準甲：命中 ≥1", out)
        self.assertIn("[4] 到期 事件：某事；最晚 2026-10-31", out)
        ev = self.section(out, 2)
        self.assertIn("事件：人判是否已發生", ev)
        self.assertNotIn("判準乙", out)
        self.assertNotIn("[3] ", out)
        self.assertNotIn("[5] ", out)

    # --- runnable spans -------------------------------------------------------------------------------

    def test_dry_run_lists_commands_without_running(self):
        out = self.run_rows().stdout
        s1 = self.section(out, 1)
        self.assertIn("grep -c needle notes.txt", s1)
        self.assertIn("grep -n needle notes.txt | wc -l", s1)  # `\|` restored
        self.assertIn("grep -c '```mermaid' notes.txt", s1)    # triple backticks inside a single-tick span
        self.assertIn(str(SCRIPTS / "numstat.py") + " --help", s1)  # scripts/ resolved into the skill repo
        self.assertFalse(self.out.exists())

    def test_placeholder_and_manual_prose_listed_for_human(self):
        s1 = self.section(self.run_rows().stdout, 1)
        human = s1.split("需人判", 1)[1]
        self.assertIn("grep -n x .claude/PRPs/plans/<slug>/PLAN.md", human)
        self.assertIn("<slug>", human)
        self.assertIn("手動：讀 transcript 數次數", human)
        self.assertNotIn("grep -c needle notes.txt", human)
        s4 = self.section(self.run_rows().stdout, 4)
        self.assertIn("問 owner：有沒有用", s4.split("需人判", 1)[1])

    def test_run_executes_in_repo_and_writes_out_files(self):
        p = self.run_rows("--run")
        self.assertEqual(p.stdout.splitlines()[0], "ACCEPT due=2 event=1 later=2 ran=4 failed=0")
        self.assertEqual((self.out / "row-1-1.out").read_text().strip(), "2")
        self.assertEqual((self.out / "row-1-2.out").read_text().strip(), "2")
        self.assertEqual((self.out / "row-1-3.out").read_text().strip(), "1")
        self.assertIn("usage", (self.out / "row-1-4.out").read_text().lower())
        s1 = self.section(p.stdout, 1)
        self.assertIn("rc=0", s1)
        self.assertIn(str(self.out / "row-1-1.out"), s1)

    def test_default_out_dir_is_repo_tmp_accept_today(self):
        self.accept("--file", str(self.acc), "--repo", str(self.repo), "--today", "2026-11-07", "--run")
        self.assertTrue((self.repo / "tmp" / "accept" / "2026-11-07" / "row-1-1.out").is_file())

    def test_side_effect_and_missing_script_spans_are_not_run(self):
        p = self.run_rows("--all", "--run", "--timeout", "1")
        s2 = self.section(p.stdout, 2)
        self.assertIn("有副作用", s2)
        self.assertIn("rm -rf tmp/old", s2)
        self.assertIn("numstat.py --apply", s2.split("需人判", 1)[1])
        self.assertIn("腳本不存在", s2)
        self.assertTrue((self.repo / "tmp" / "old").is_dir())
        self.assertFalse(list(self.out.glob("row-2-*.out")))

    def test_row_select_counts_failures_and_timeout(self):
        p = self.run_rows("--row", "3", "5", "--run", "--timeout", "1")
        self.assertEqual(p.stdout.splitlines()[0], "ACCEPT due=2 event=1 later=2 ran=3 failed=2")
        s3 = self.section(p.stdout, 3)
        self.assertIn("rc=1", s3)  # grep without a match
        self.assertIn("判準丙", s3)
        s5 = self.section(p.stdout, 5)
        self.assertIn("timeout", s5)
        self.assertNotIn("[1] ", p.stdout)

    def test_grep_fragment_without_file_is_not_run(self):
        # WHY: ACCEPTANCE writes `grep -c 'graphify-query-guard:'` as a fragment of a manual step; run with
        # stdin closed it prints 0, rc 1 and reads as "guard never fired".
        self.acc.write_text("## 待驗\n| 到期 | 改動 | 指令 | 判準 | 出處 |\n|---|---|---|---|---|\n"
                            "| 2026-11-01 | 甲 | ③ `grep -c 'guard:'` 確認還在擋；`grep -c -e needle notes.txt`；"
                            "`grep -rn needle .` | 判準 | 出處 |\n", encoding="utf-8")
        p = self.run_rows("--run")
        self.assertEqual(p.stdout.splitlines()[0], "ACCEPT due=1 event=0 later=0 ran=2 failed=0")
        human = self.section(p.stdout, 1).split("需人判", 1)[1]
        self.assertIn("grep -c 'guard:'", human)
        self.assertIn("沒有檔案", human)
        self.assertEqual(human.count("grep -c 'guard:'"), 1)  # listed once, not once per reason

    def test_unknown_row_exits_nonzero(self):
        p = self.run_rows("--row", "99", ok=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("99", p.stdout + p.stderr)

    def test_acceptance_file_not_modified(self):
        before = hashlib.sha256(self.acc.read_bytes()).hexdigest()
        self.run_rows("--all", "--run", "--timeout", "1")
        self.assertEqual(hashlib.sha256(self.acc.read_bytes()).hexdigest(), before)

    def test_missing_pending_table_fails(self):
        self.acc.write_text("# 驗收帳\n\n## 結案\n| 日期 | 改動 |\n|---|---|\n| a | b |\n", encoding="utf-8")
        p = self.run_rows(ok=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("待驗", p.stdout + p.stderr)

    # --- show -----------------------------------------------------------------------------------------

    def make_git(self):
        git(self.repo, "init", "-q")
        plan = self.repo / ".claude" / "PRPs" / "plans" / "demo-slug"
        plan.mkdir(parents=True)
        (plan / "PROGRESS.md").write_text("# demo PROGRESS\n第 5 階段紀錄\n", encoding="utf-8")
        (plan / "SPEC.md").write_text("# spec\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "docs(prps): demo-slug 七階段記錄")
        (self.repo / "code.txt").write_text("x\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "feat(x): demo-slug code")
        git(self.repo, "rm", "-q", "-r", str(plan))
        git(self.repo, "commit", "-q", "-m", "docs(prps): demo-slug 刪任務目錄")
        return git(self.repo, "log", "--format=%h", "--grep=七階段記錄").strip()

    def test_show_lists_only_docs_prps_commits(self):
        self.make_git()
        p = self.accept("show", "demo-slug", "--repo", str(self.repo))
        lines = p.stdout.splitlines()
        self.assertEqual(lines[0], "SHOW demo-slug commits=2")
        self.assertTrue(any("docs(prps): demo-slug 七階段記錄" in ln for ln in lines))
        self.assertFalse(any("feat(x)" in ln for ln in lines))

    def test_show_file_prints_from_newest_commit_that_has_it(self):
        h = self.make_git()
        p = self.accept("show", "demo-slug", "PROGRESS.md", "--repo", str(self.repo))
        self.assertEqual(p.stdout, "# demo PROGRESS\n第 5 階段紀錄\n")
        self.assertIn(h, p.stderr)

    def test_show_missing_file_exits_1_and_lists_commit_files(self):
        self.make_git()
        p = self.accept("show", "demo-slug", "NOPE.md", "--repo", str(self.repo), ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("PROGRESS.md", p.stdout + p.stderr)
        self.assertIn("SPEC.md", p.stdout + p.stderr)

    # --- --slug substitution ---------------------------------------------------------------------------

    def write_rows(self, *rows):
        self.acc.write_text("## 待驗\n| 到期 | 改動 | 指令 | 判準 | 出處 |\n|---|---|---|---|---|\n"
                            + "".join(f"| 2026-11-01 | 改動{i} | {r} | 判準{i} | 出處{i} |\n" for i, r in enumerate(rows, 1)),
                            encoding="utf-8")

    def make_plan(self, slug, body):
        plan = self.repo / ".claude" / "PRPs" / "plans" / slug
        plan.mkdir(parents=True, exist_ok=True)
        (plan / "PROGRESS.md").write_text(body, encoding="utf-8")

    def test_slug_substitutes_slug_and_task_placeholders(self):
        self.make_plan("s1", "context7\ncontext7\n")
        self.write_rows("`grep -c context7 <任務>/PROGRESS.md`；`grep -c context7 .claude/PRPs/plans/<slug>/PROGRESS.md`；"
                        "`grep -c x <任務目錄>/PROGRESS.md <other>`")
        p = self.run_rows("--slug", "s1", "--run")
        self.assertEqual(p.stdout.splitlines()[0], "ACCEPT due=1 event=0 later=0 ran=2 failed=0")
        self.assertIn("[1] slug=s1 到期 2026-11-01", p.stdout)
        self.assertEqual((self.out / "row-1-s1-1.out").read_text().strip(), "2")
        self.assertEqual((self.out / "row-1-s1-2.out").read_text().strip(), "2")
        human = p.stdout.split("需人判", 1)[1]
        self.assertIn("需填參數 <other>", human)
        self.assertIn(".claude/PRPs/plans/s1/PROGRESS.md <other>", human)

    def test_multiple_slugs_run_each_and_plain_rows_once(self):
        self.make_plan("s1", "context7\n")
        self.make_plan("s2", "context7\ncontext7\ncontext7\n")
        self.write_rows("`grep -c context7 <任務>/PROGRESS.md`", "`grep -c needle notes.txt`")
        p = self.run_rows("--slug", "s1", "--slug", "s2", "--run")
        self.assertEqual(p.stdout.splitlines()[0], "ACCEPT due=2 event=0 later=0 ran=3 failed=0")
        self.assertEqual((self.out / "row-1-s1-1.out").read_text().strip(), "1")
        self.assertEqual((self.out / "row-1-s2-1.out").read_text().strip(), "3")
        self.assertEqual((self.out / "row-2-1.out").read_text().strip(), "2")
        self.assertIn("[1] slug=s1 ", p.stdout)
        self.assertIn("[1] slug=s2 ", p.stdout)
        self.assertEqual(p.stdout.count("[2] "), 1)

    def test_hash_placeholder_resolves_to_newest_docs_commit_with_the_path(self):
        h = self.make_git()
        self.write_rows("`git show <docs(prps) hash>:.claude/PRPs/plans/<slug>/PROGRESS.md`；"
                        "`git show <hash>:.claude/PRPs/plans/<slug>/NOPE.md`；`git show <hash>:<path>`")
        p = self.run_rows("--slug", "demo-slug", "--run")
        self.assertEqual(p.stdout.splitlines()[0], "ACCEPT due=1 event=0 later=0 ran=1 failed=0")
        self.assertIn(f"git show {h}:.claude/PRPs/plans/demo-slug/PROGRESS.md", p.stdout)
        self.assertEqual((self.out / "row-1-demo-slug-1.out").read_text(), "# demo PROGRESS\n第 5 階段紀錄\n")
        human = p.stdout.split("需人判", 1)[1]
        self.assertIn("找不到 commit", human)
        self.assertIn("NOPE.md", human)
        self.assertIn("需填參數", human)

    def test_show_unknown_slug_exits_1(self):
        self.make_git()
        p = self.accept("show", "no-such-slug", "--repo", str(self.repo), ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("commits=0", p.stdout)


if __name__ == "__main__":
    unittest.main()
