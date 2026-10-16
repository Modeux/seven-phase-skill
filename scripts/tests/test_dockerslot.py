# -*- coding: utf-8 -*-
"""dockerslot: a full pool makes callers wait (not fail), --max-wait gives up with 75, --all takes
priority over newcomers, and the command's exit code passes through.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_dockerslot.py' -v
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "dockerslot.py"


class DockerSlotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.env = {**os.environ, "DOCKERSLOT_DIR": str(self.dir / "slots")}

    def tearDown(self):
        self.tmp.cleanup()

    def job(self, name, hold_s, *flags):
        """Start a slot holder that records its start/end wall times as files <name>.start / <name>.end."""
        code = (f"import time,pathlib; d=pathlib.Path({str(self.dir)!r}); "
                f"(d/'{name}.start').write_text(repr(time.time())); time.sleep({hold_s}); "
                f"(d/'{name}.end').write_text(repr(time.time()))")
        p = subprocess.Popen([sys.executable, str(SCRIPT), *flags, "--", sys.executable, "-c", code],
                             env=self.env, stderr=subprocess.PIPE, text=True)
        assert p.stderr is not None
        self.addCleanup(p.stderr.close)
        return p

    def t(self, name, edge):
        return float((self.dir / f"{name}.{edge}").read_text())

    def wait_for(self, name, edge="start", timeout=10):
        end = time.time() + timeout
        while not (self.dir / f"{name}.{edge}").exists():
            self.assertLess(time.time(), end, f"{name}.{edge} never appeared")
            time.sleep(0.05)

    def test_exit_code_passes_through(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--slots", "1", "--", sys.executable, "-c",
                            "import sys; sys.exit(3)"], env=self.env)
        self.assertEqual(r.returncode, 3)

    def test_full_pool_waits_instead_of_failing(self):
        a = self.job("a", 1.5, "--slots", "1")
        self.wait_for("a")
        b = self.job("b", 0, "--slots", "1")
        self.assertEqual(a.wait(), 0)
        self.assertEqual(b.wait(), 0)
        self.assertGreaterEqual(self.t("b", "start"), self.t("a", "end"))

    def test_free_slots_run_concurrently(self):
        a = self.job("a", 1.5, "--slots", "2")
        self.wait_for("a")
        b = self.job("b", 0, "--slots", "2")
        for p in (a, b):
            self.assertEqual(p.wait(), 0)
        self.assertLess(self.t("b", "start"), self.t("a", "end"))

    def test_gives_up_with_75_and_names_holder(self):
        a = self.job("a", 3, "--slots", "1")
        self.wait_for("a")
        b = self.job("b", 0, "--slots", "1", "--max-wait", "1")
        _, err = b.communicate()
        self.assertEqual(b.returncode, 75)
        self.assertIn("gave up", err)
        self.assertIn(str(a.pid), err)
        self.assertFalse((self.dir / "b.start").exists())
        a.wait()

    def test_all_waits_for_holders_and_newcomers_queue_behind_it(self):
        a = self.job("a", 1.5, "--slots", "2")
        self.wait_for("a")
        full = self.job("full", 1.0, "--slots", "2", "--all")
        time.sleep(0.5)  # full is now waiting on a's slot while holding the gate
        c = self.job("c", 0, "--slots", "2")
        for p in (a, full, c):
            self.assertEqual(p.wait(), 0)
        self.assertGreaterEqual(self.t("full", "start"), self.t("a", "end"))
        self.assertGreaterEqual(self.t("c", "start"), self.t("full", "end"))

    def test_pools_are_independent(self):
        a = self.job("a", 1.5, "--slots", "1", "--pool", "sqlc")
        self.wait_for("a")
        b = self.job("b", 0, "--slots", "1")
        for p in (a, b):
            self.assertEqual(p.wait(), 0)
        self.assertLess(self.t("b", "start"), self.t("a", "end"))


if __name__ == "__main__":
    unittest.main()
