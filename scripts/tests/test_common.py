# -*- coding: utf-8 -*-
"""_common status parsing and mkpatch outputs (outside.sha256, renames, non-ASCII paths).

Usage: python3 -m unittest discover -s scripts/tests -p 'test_common.py' -v
"""
import hashlib
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import _common  # noqa: E402

MKPATCH = pathlib.Path(__file__).resolve().parents[1] / "mkpatch.py"
ODD = "資料 夾/中文 檔.txt"


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          check=True, capture_output=True, text=True).stdout


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CommonTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        g(self.repo, "init", "-q")
        for rel, text in (("a.txt", "A\n"), ("b.txt", "b\n"), ("vendor/x.txt", "x\n"),
                          ("vendor/gone.txt", "g\n")):
            self.write(self.repo, rel, text)
        g(self.repo, "add", ".")
        g(self.repo, "commit", "-qm", "init")

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def write(root, rel, text):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_status_entries_reports_rename_origin_and_raw_non_ascii(self):
        g(self.repo, "mv", "b.txt", "c.txt")
        self.write(self.repo, ODD, "odd\n")
        entries = _common.status_entries(self.repo)
        self.assertIn(("R ", "c.txt", "b.txt"), entries)
        self.assertIn(("??", ODD, None), entries)

    def test_changed_paths_staged_rename_and_non_ascii(self):
        g(self.repo, "mv", "b.txt", "c.txt")
        self.write(self.repo, ODD, "odd\n")
        self.write(self.repo, "tmp/scratch.txt", "s\n")
        got = _common.changed_paths(self.repo)
        self.assertEqual(sorted(got), sorted([("R ", "c.txt"), ("D ", "b.txt"), ("??", ODD)]))

    def test_mkpatch_writes_outside_sha256_for_excluded_dirty_paths(self):
        self.write(self.repo, "a.txt", "B\n")
        self.write(self.repo, ODD, "odd\n")
        self.write(self.repo, "vendor/x.txt", "owner wip\n")
        self.write(self.repo, "vendor/new.txt", "owner new\n")
        (self.repo / "vendor/gone.txt").unlink()
        self.write(self.repo, "tmp/slug/notes.txt", "n\n")
        self.write(self.repo, ".claude/PRPs/plans/slug/PLAN.md", "p\n")
        out = self.base / "out"
        r = subprocess.run([sys.executable, str(MKPATCH), "--out", str(out), "--root", str(self.repo),
                            "--exclude", "tmp/", "--exclude", ".claude/PRPs/plans/", "--exclude", "vendor/"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((out / "outside.sha256").read_text(), (
            "DELETED  vendor/gone.txt\n"
            f"{sha('owner new' + chr(10))}  vendor/new.txt\n"
            f"{sha('owner wip' + chr(10))}  vendor/x.txt\n"))
        self.assertIn("outside.sha256", r.stdout)
        self.assertIn("3", r.stdout.split("outside.sha256", 1)[1].splitlines()[0])
        self.assertEqual((out / "baseline.sha256").read_text(),
                         f"{sha('B' + chr(10))}  a.txt\n{sha('odd' + chr(10))}  {ODD}\n")

    def test_mkpatch_with_no_outside_dirt_writes_empty_outside(self):
        self.write(self.repo, "a.txt", "B\n")
        out = self.base / "out"
        r = subprocess.run([sys.executable, str(MKPATCH), "--out", str(out), "--root", str(self.repo)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((out / "outside.sha256").read_text(), "")

    def test_mkpatch_staged_rename_round_trips_into_fresh_worktree(self):
        g(self.repo, "mv", "b.txt", "c.txt")
        self.write(self.repo, ODD, "odd\n")
        out = self.base / "out"
        r = subprocess.run([sys.executable, str(MKPATCH), "--out", str(out), "--root", str(self.repo)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("DELETED  b.txt", (out / "baseline.sha256").read_text())
        wt = self.base / "wt"
        g(self.repo, "worktree", "add", "-q", "--detach", str(wt), "HEAD")
        g(wt, "apply", "--whitespace=nowarn", str(out / "change.patch"))
        self.assertEqual(_common.verify_tree(wt, out / "baseline.sha256"), [])
        self.assertFalse((wt / "b.txt").exists())
        self.assertEqual(_common.outside_changes(wt, out / "baseline.sha256"), [])


if __name__ == "__main__":
    unittest.main()
