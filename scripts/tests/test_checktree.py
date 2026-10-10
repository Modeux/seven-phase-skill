# -*- coding: utf-8 -*-
"""checktree: baseline files verified exactly; dirty paths outside the change listed as warnings.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_checktree.py' -v
"""
import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import _common  # noqa: E402

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "checktree.py"


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          check=True, capture_output=True, text=True).stdout


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CheckTreeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        g(self.repo, "init", "-q")
        for rel, text in (("a.txt", "A\n"), ("keep.txt", "K\n"), ("gone.txt", "G\n")):
            self.write(self.repo, rel, text)
        g(self.repo, "add", ".")
        g(self.repo, "commit", "-qm", "init")
        self.apply_change(self.repo)
        self.review = self.base / "review"
        self.review.mkdir()
        self.baseline = self.review / "baseline.sha256"
        self.baseline.write_text(f"{sha('B' + chr(10))}  a.txt\n{sha('NEW' + chr(10))}  new.txt\n")

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def write(root, rel, text):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def apply_change(self, root):
        self.write(root, "a.txt", "B\n")
        self.write(root, "new.txt", "NEW\n")

    def run_cli(self, *trees, strict=False, outside=None):
        cmd = [sys.executable, str(SCRIPT), "--baseline", str(self.baseline)]
        if outside:
            cmd += ["--outside", str(outside)]
        if strict:
            cmd.append("--strict")
        cmd += [str(t) for t in trees]
        return subprocess.run(cmd, capture_output=True, text=True)

    def write_outside(self, text):
        (self.review / "outside.sha256").write_text(text)

    def test_all_match_exit_zero_without_warnings(self):
        r = self.run_cli(self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("checked 2, mismatches 0", r.stdout)
        self.assertNotIn("warning", r.stdout)

    def test_change_inside_baseline_exits_one(self):
        self.write(self.repo, "a.txt", "tampered\n")
        r = self.run_cli(self.repo)
        self.assertEqual(r.returncode, 1)
        self.assertIn("mismatches 1", r.stdout)
        self.assertIn("differs: a.txt", r.stdout)

    def test_outside_tracked_change_warns_and_strict_fails(self):
        self.write(self.repo, "keep.txt", "lane wrote here\n")
        r = self.run_cli(self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("warning: outside the change: keep.txt", r.stdout)
        self.assertEqual(self.run_cli(self.repo, strict=True).returncode, 1)

    def test_outside_new_file_warns(self):
        self.write(self.repo, "pkg/extra.go", "x\n")
        r = self.run_cli(self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("warning: outside the change: pkg/extra.go", r.stdout)

    def test_scratch_prefixes_do_not_warn(self):
        self.write(self.repo, "tmp/slug/review/notes.txt", "n\n")
        self.write(self.repo, ".claude/PRPs/plans/slug/PROGRESS.md", "p\n")
        r = self.run_cli(self.repo, strict=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("warning", r.stdout)

    def test_pre_existing_outside_change_unchanged_is_silent(self):
        self.write(self.repo, "keep.txt", "owner wip\n")
        (self.repo / "gone.txt").unlink()
        self.write_outside(f"{sha('owner wip' + chr(10))}  keep.txt\nDELETED  gone.txt\n")
        r = self.run_cli(self.repo, strict=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("warning", r.stdout)

    def test_pre_existing_outside_change_modified_warns(self):
        self.write(self.repo, "keep.txt", "owner wip, then a lane\n")
        self.write_outside(f"{sha('owner wip' + chr(10))}  keep.txt\n")
        r = self.run_cli(self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("warning: pre-existing change modified: keep.txt", r.stdout)

    def test_explicit_outside_flag_is_used(self):
        self.write(self.repo, "keep.txt", "owner wip\n")
        other = self.base / "elsewhere.sha256"
        other.write_text(f"{sha('owner wip' + chr(10))}  keep.txt\n")
        r = self.run_cli(self.repo, strict=True, outside=other)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_snapshot_like_worktree_has_no_warnings(self):
        self.write(self.repo, "keep.txt", "owner wip\n")
        self.write_outside(f"{sha('owner wip' + chr(10))}  keep.txt\n")
        snap = self.base / "snap"
        g(self.repo, "worktree", "add", "-q", "--detach", str(snap), "HEAD")
        self.apply_change(snap)
        self.write(snap, "tmp/mut/m1.txt", "mutation log\n")
        r = self.run_cli(snap, self.repo, strict=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("warning", r.stdout)

    def test_non_git_tree_skips_extra_check(self):
        plain = self.base / "plain"
        plain.mkdir()
        shutil.copy2(self.repo / "a.txt", plain / "a.txt")
        shutil.copy2(self.repo / "new.txt", plain / "new.txt")
        r = self.run_cli(plain)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("mismatches 0", r.stdout)
        self.assertIn("skipped", r.stdout)
        self.assertIsNone(_common.outside_changes(plain, self.baseline))


if __name__ == "__main__":
    unittest.main()
