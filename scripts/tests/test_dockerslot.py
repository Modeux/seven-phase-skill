# -*- coding: utf-8 -*-
"""dockerslot: a full pool makes callers wait (not fail), --max-wait gives up with 75, --all takes
priority over newcomers, the command's exit code passes through, and leftover testcontainers are removed
before and after a command in the default pool.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_dockerslot.py' -v
"""
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "dockerslot.py"
# WHY a fake: the sweep would otherwise remove real leftovers from the machine running the tests.
FAKE_DOCKER = r'''#!%s
import json, os, pathlib, sys, time
d = pathlib.Path(os.environ["FAKE_DOCKER_DIR"])
with open(d / "docker.log", "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "t": time.time()}) + "\n")
if sys.argv[1] == "ps":
    if (d / "ps.fail").exists():
        print("Cannot connect to the Docker daemon")
        sys.exit(1)
    if (d / "ps.txt").exists():
        sys.stdout.write((d / "ps.txt").read_text(encoding="utf-8"))
''' % sys.executable


def tc_row(cid, state, minutes_ago):
    created = time.strftime("%Y-%m-%d %H:%M:%S +0800 CST", time.gmtime(time.time() + 8 * 3600 - minutes_ago * 60))
    return "\t".join([cid, state, created, "true", "c", "", "", ""]) + "\n"


class DockerSlotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        (self.dir / "bin").mkdir()
        fake = self.dir / "bin" / "docker"
        fake.write_text(FAKE_DOCKER, encoding="utf-8")
        fake.chmod(0o755)
        self.env = {**os.environ, "DOCKERSLOT_DIR": str(self.dir / "slots"), "FAKE_DOCKER_DIR": str(self.dir),
                    "PATH": f"{self.dir / 'bin'}{os.pathsep}{os.environ['PATH']}"}

    def docker_calls(self):
        log = self.dir / "docker.log"
        return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

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

    def test_leftovers_are_removed_before_and_after_the_command(self):
        (self.dir / "ps.txt").write_text(tc_row("exited1", "exited", 5) + tc_row("young1", "running", 2), encoding="utf-8")
        a = self.job("a", 0.5)
        _, err = a.communicate()
        self.assertEqual(a.returncode, 0, err)
        calls = self.docker_calls()
        self.assertEqual([c["argv"][0] for c in calls], ["ps", "rm", "ps", "rm"])
        self.assertEqual([c["argv"][-1] for c in calls if c["argv"][0] == "rm"], ["exited1", "exited1"])
        self.assertLess(calls[1]["t"], self.t("a", "start"))
        self.assertGreater(calls[2]["t"], self.t("a", "end"))
        self.assertIn("dockerslot: removed 1 leftover testcontainers (before the command)", err)
        self.assertIn("dockerslot: removed 1 leftover testcontainers (after the command)", err)

    def test_nothing_left_over_prints_nothing(self):
        a = self.job("a", 0)
        _, err = a.communicate()
        self.assertEqual([c["argv"][0] for c in self.docker_calls()], ["ps", "ps"])
        self.assertEqual(err, "")

    def test_no_sweep_and_other_pools_never_call_docker(self):
        for flags in (("--no-sweep",), ("--pool", "sqlc", "--slots", "1")):
            with self.subTest(flags):
                self.assertEqual(self.job("a", 0, *flags).wait(), 0)
                self.assertEqual(self.docker_calls(), [])

    def test_cleanup_failure_keeps_the_commands_exit_code(self):
        (self.dir / "ps.fail").write_text("", encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPT), "--", sys.executable, "-c", "import sys; sys.exit(3)"],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 3)
        self.assertIn("dockerslot: container cleanup failed after the command", r.stderr)

    def test_sweeps_after_a_command_stopped_by_a_forwarded_signal(self):
        a = self.job("a", 30)
        self.wait_for("a")
        a.send_signal(signal.SIGTERM)
        self.assertEqual(a.wait(timeout=10), 128 + signal.SIGTERM)
        self.assertEqual([c["argv"][0] for c in self.docker_calls()], ["ps", "ps"])
        self.assertFalse((self.dir / "a.end").exists())


if __name__ == "__main__":
    unittest.main()
