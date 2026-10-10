# -*- coding: utf-8 -*-
"""merge_back: fixes are found relative to the snapshot, not to HEAD.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_merge_back.py' -v
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import merge_back  # noqa: E402

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "merge_back.py"
ODD = "docs dir/中文 檔.txt"


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          check=True, capture_output=True, text=True).stdout


class MergeBackTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        g(self.repo, "init", "-q")
        self.write(self.repo, "a.txt", "A\n")
        self.write(self.repo, "b.txt", "b\n")
        self.write(self.repo, ODD, "X\n")
        g(self.repo, "add", ".")
        g(self.repo, "commit", "-qm", "init")
        self.apply_change(self.repo)
        self.snap = self.tree("snap")

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
        self.write(root, ODD, "Y\n")

    def tree(self, name):
        path = self.base / name
        g(self.repo, "worktree", "add", "-q", "--detach", str(path), "HEAD")
        self.apply_change(path)
        return path

    def run_cli(self, *trees, apply=False, allow=()):
        cmd = [sys.executable, str(SCRIPT), "--root", str(self.repo), "--snapshot", str(self.snap)]
        for label, path in trees:
            cmd += ["--tree", f"{label}={path}"]
        for spec in allow:
            cmd += ["--allow-revert", spec]
        if apply:
            cmd.append("--apply")
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_ordinary_fix_is_found_and_applied(self):
        fix = self.tree("fix")
        self.write(fix, "a.txt", "C\n")
        self.assertEqual(merge_back.tree_changes(fix, self.snap), (["a.txt"], []))
        r = self.run_cli(("F", fix), apply=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.repo / "a.txt").read_text(), "C\n")

    def test_fix_restoring_head_content_is_found_and_needs_allow_revert(self):
        fix = self.tree("fix")
        self.write(fix, "a.txt", "A\n")
        self.assertEqual(merge_back.tree_changes(fix, self.snap), (["a.txt"], []))
        stopped = self.run_cli(("F", fix), apply=True)
        self.assertEqual(stopped.returncode, 1)
        self.assertIn("F: reverts a.txt to HEAD", stopped.stdout)
        self.assertEqual((self.repo / "a.txt").read_text(), "B\n")
        r = self.run_cli(("F", fix), apply=True, allow=["F:a.txt"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.repo / "a.txt").read_text(), "A\n")

    def test_accidental_reset_stops_and_copies_nothing(self):
        fix = self.tree("fix")
        g(fix, "checkout", "--", "a.txt")
        self.write(fix, ODD, "Z\n")
        for apply in (False, True):
            r = self.run_cli(("G1", fix), apply=apply)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("STOP", r.stdout)
            self.assertIn("G1: reverts a.txt to HEAD", r.stdout)
            self.assertNotIn("copied", r.stdout)
            self.assertEqual((self.repo / "a.txt").read_text(), "B\n")
            self.assertEqual((self.repo / ODD).read_text(encoding="utf-8"), "Y\n")

    def test_allowed_revert_is_copied_and_marked(self):
        fix = self.tree("fix")
        g(fix, "checkout", "--", "a.txt")
        self.write(fix, ODD, "Z\n")
        r = self.run_cli(("G1", fix), apply=True, allow=["G1:a.txt"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"G1 +a\.txt .*revert")
        self.assertNotRegex(r.stdout, r"G1 +" + ODD + r" .*revert")
        self.assertEqual((self.repo / "a.txt").read_text(), "A\n")
        self.assertEqual((self.repo / ODD).read_text(encoding="utf-8"), "Z\n")

    def test_allow_revert_for_another_label_does_not_allow(self):
        fix = self.tree("fix")
        g(fix, "checkout", "--", "a.txt")
        r = self.run_cli(("G1", fix), apply=True, allow=["G2:a.txt"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("G1: reverts a.txt to HEAD", r.stdout)
        self.assertEqual((self.repo / "a.txt").read_text(), "B\n")

    def test_fix_deleting_snapshot_new_file_is_reported_and_stops(self):
        fix = self.tree("fix")
        (fix / "new.txt").unlink()
        self.assertEqual(merge_back.tree_changes(fix, self.snap), ([], ["new.txt"]))
        r = self.run_cli(("F", fix), apply=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("STOP", r.stdout)
        self.assertIn("F: deletes new.txt", r.stdout)
        self.assertTrue((self.repo / "new.txt").exists())

    def test_new_file_in_fix_tree_is_found(self):
        fix = self.tree("fix")
        self.write(fix, "pkg/added.go", "package pkg\n")
        self.assertEqual(merge_back.tree_changes(fix, self.snap), (["pkg/added.go"], []))

    def test_fix_undoing_snapshot_rename_reports_both_paths(self):
        g(self.snap, "mv", "b.txt", "c.txt")
        g(self.repo, "mv", "b.txt", "c.txt")
        fix = self.tree("fix")
        self.assertEqual(merge_back.tree_changes(fix, self.snap), (["b.txt"], ["c.txt"]))

    def test_two_trees_changing_same_file_stop(self):
        f1, f2 = self.tree("f1"), self.tree("f2")
        self.write(f1, "a.txt", "C1\n")
        self.write(f2, "a.txt", "C2\n")
        r = self.run_cli(("G1", f1), ("G2", f2), apply=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("overlap: a.txt changed by G1 and G2", r.stdout)
        self.assertEqual((self.repo / "a.txt").read_text(), "B\n")

    def test_main_checkout_differs_from_snapshot_stops(self):
        fix = self.tree("fix")
        self.write(fix, "a.txt", "C\n")
        self.write(self.repo, "a.txt", "someone else\n")
        r = self.run_cli(("F", fix), apply=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("main differs from snapshot: a.txt (F)", r.stdout)
        self.assertEqual((self.repo / "a.txt").read_text(), "someone else\n")

    def test_head_mismatch_stops_before_anything(self):
        fix = self.tree("fix")
        self.write(fix, "a.txt", "C\n")
        g(fix, "commit", "-qam", "moved HEAD")
        r = self.run_cli(("F", fix), apply=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("HEAD differs", r.stdout)
        self.assertIn("F", r.stdout)
        self.assertEqual((self.repo / "a.txt").read_text(), "B\n")

    def test_path_with_space_and_chinese_is_found_and_applied(self):
        fix = self.tree("fix")
        self.write(fix, ODD, "Z\n")
        self.assertEqual(merge_back.tree_changes(fix, self.snap), ([ODD], []))
        r = self.run_cli(("F", fix), apply=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.repo / ODD).read_text(encoding="utf-8"), "Z\n")

    def test_tmp_and_skip_parts_are_ignored(self):
        fix = self.tree("fix")
        self.write(fix, "tmp/scratch.txt", "s\n")
        self.write(fix, "web/node_modules/x.js", "x\n")
        self.assertEqual(merge_back.tree_changes(fix, self.snap), ([], []))


if __name__ == "__main__":
    unittest.main()
