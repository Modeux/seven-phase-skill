# -*- coding: utf-8 -*-
"""gate lint: golangci-lint in docker, split on OOM until every package ran, known-red by path/linter/message prefix.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_gate_lint.py' -v
"""
import json
import pathlib
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _gate_fakes import FIXTURES, GATE, Harness, text_replay  # noqa: E402

# WHY: the shared fake picks one docker answer per subcommand; batching needs an answer per pattern list,
# so `run` is matched on the patterns after `--concurrency N` and the rest behaves like the shared fake.
LINT_FAKE = r'''#!%s
import json, os, subprocess, sys, time
argv = sys.argv[1:]
cfg = json.load(open(os.environ["FAKE_CFG"], encoding="utf-8"))
log = {"name": "docker", "argv": argv, "cwd": os.getcwd(), "pid": os.getpid()}
sub = argv[0] if argv else ""
c = {}
if sub == "run":
    pats = argv[argv.index("--concurrency") + 2:] if "--concurrency" in argv else []
    log["patterns"] = pats
    for rule in cfg.get("lint", []):
        if "has" not in rule or rule["has"] in pats:
            c = rule
            break
else:
    c = cfg.get("docker", {}).get(sub, {})
if c.get("shell"):
    log["shell"] = subprocess.run(c["shell"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(log) + "\n")
if c.get("file"):
    sys.stdout.write(open(c["file"], encoding="utf-8").read())
if c.get("text"):
    sys.stdout.write(c["text"])
sys.stdout.flush()
if c.get("sleep"):
    time.sleep(c["sleep"])
sys.exit(c.get("rc", 0))
''' % sys.executable

INTERNAL = ("logger", "tag", "a1", "a2", "a3", "a4", "a5", "a6")
EXPANDED = ["./cmd/..."] + [f"./internal/{d}/..." for d in sorted(INTERNAL)]
ERRCHECK = "internal/tag/handler.go:42:9: Error return value of `w.Write` is not checked (errcheck)"
UNUSED = "internal/logger/log.go:10:6: func `unusedHelper` is unused (unused)"
GREEN = text_replay("golint-green")
RED = text_replay("golint-red")
OOM = text_replay("golint-oom")


class LintHarness(Harness):
    def setUp(self):
        super().setUp()
        (self.bin / "docker").write_text(LINT_FAKE, encoding="utf-8")
        (self.bin / "docker").chmod(0o755)
        self.backend = self.tree / "backend-golang"
        for d in INTERNAL:
            (self.backend / "internal" / d).mkdir(parents=True, exist_ok=True)
        (self.backend / "cmd/server").mkdir(parents=True)
        # INVARIANT: not packages go would walk into with ./..., so they must not become batches.
        (self.backend / "internal/testdata").mkdir()
        (self.backend / "internal/.hidden").mkdir()
        (self.backend / "internal/README.md").write_text("x\n", encoding="utf-8")

    def lint(self, *args, rules=None):
        if rules is not None:
            self.fake(lint=rules)
        return self.gate("lint", "--tree", self.tree, *args)

    def runs(self):
        return [c for c in self.calls("docker") if c["argv"] and c["argv"][0] == "run"]

    def known(self, *lines, name="known.txt"):
        p = self.tmp / name
        p.write_text("".join(x + "\n" for x in lines), encoding="utf-8")
        return p


class LintVerdictTest(LintHarness):
    def test_zero_issues_on_a_full_run_is_green(self):
        r = self.lint(rules=[GREEN])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.first, r"^GATE lint GREEN issues=0 known=0 ignored=0 batches=1/1 incomplete=0 out=/")
        self.assertEqual([c["patterns"] for c in self.runs()], [["./..."]])

    def test_one_new_issue_is_red_and_listed_one_per_line(self):
        r = self.lint("--no-known-red", rules=[RED])
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.first, r"^GATE lint RED issues=2 known=0 ignored=0 batches=1/1 incomplete=0 out=/")
        lines = r.stdout.splitlines()
        self.assertIn("internal/tag/handler.go:42:9 Error return value of `w.Write` is not checked (errcheck)", lines)
        self.assertIn("internal/logger/log.go:10:6 func `unusedHelper` is unused (unused)", lines)
        s = self.summary_of(r)
        self.assertEqual(s["mode"], "lint")
        self.assertEqual(s["verdict"], "RED")
        self.assertEqual(s["exit"], 1)

    def test_expect_red_accepts_a_new_issue(self):
        self.assertEqual(self.lint("--expect", "red", rules=[RED]).returncode, 0)
        self.assertEqual(self.lint("--expect", "red", rules=[GREEN]).returncode, 1)

    def test_listed_issues_match_by_message_prefix_and_ignore_line_numbers(self):
        k = self.known("# baseline",
                       "",
                       "internal/tag/handler.go :: errcheck :: Error return value of `w.Write`",
                       "internal/logger/log.go :: unused :: func `unusedHelper` is unused")
        r = self.lint("--known-red", k, rules=[RED])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.first, r"^GATE lint RED-KNOWN issues=0 known=2 ")

    def test_list_needs_the_same_linter_and_path_and_a_real_prefix(self):
        for entry in ("internal/tag/handler.go :: govet :: Error return value",  # other linter
                      "internal/tag/other.go :: errcheck :: Error return value",  # other path
                      "internal/tag/handler.go :: errcheck :: return value"):  # mid-message, not a prefix
            with self.subTest(entry):
                k = self.known(entry, "internal/logger/log.go :: unused :: func")
                r = self.lint("--known-red", k, rules=[RED])
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertRegex(r.first, r"^GATE lint RED issues=1 known=1 ")
                self.assertIn("internal/tag/handler.go:42:9 Error return value", r.stdout)
                self.assertNotIn("internal/logger/log.go:10:6", r.stdout)

    def test_tree_list_is_read_by_default_and_a_missing_one_is_empty(self):
        r = self.lint(rules=[RED])
        self.assertIn(" RED issues=2 known=0 ", r.first)
        (self.backend / "known-red-golint.txt").write_text(
            "internal/tag/handler.go :: errcheck :: Error return\ninternal/logger/log.go :: unused :: func\n", encoding="utf-8")
        r = self.lint(rules=[RED])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" RED-KNOWN issues=0 known=2 ", r.first)
        r = self.lint("--no-known-red", rules=[RED])
        self.assertIn(" RED issues=2 known=0 ", r.first)

    def test_malformed_list_line_is_a_tool_error(self):
        k = self.known("internal/tag/handler.go errcheck Error return")
        r = self.lint("--known-red", k, rules=[RED])
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertEqual(self.runs(), [])

    def test_typecheck_issue_is_build_fail(self):
        r = self.lint(rules=[text_replay("golint-typecheck")])
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("GATE lint BUILD-FAIL ", r.first)
        self.assertIn("undefined: foo", r.stdout)
        self.assertEqual(self.lint("--expect", "red", rules=[text_replay("golint-typecheck")]).returncode, 1)

    def test_known_red_and_no_known_red_together_is_a_tool_error(self):
        k = self.known("internal/tag/handler.go :: errcheck :: Error")
        r = self.lint("--known-red", k, "--no-known-red", rules=[GREEN])
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("GATE TOOL-ERROR", r.first)
        self.assertEqual(self.runs(), [])

    def test_exit_1_without_a_parsable_issue_is_incomplete(self):
        r = self.lint(rules=[{"text": "something went sideways\n", "rc": 1}])
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn(" INCOMPLETE ", r.first)
        self.assertIn("./...", r.stdout)

    def test_other_engine_failure_is_incomplete_not_green(self):
        r = self.lint(rules=[{"text": "level=error msg=\"Running error: bad config\"\n", "rc": 3}])
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn(" INCOMPLETE ", r.first)
        self.assertIn("bad config", r.stdout)

    def test_unknown_lines_become_notes_and_do_not_crash(self):
        text = "WARN something odd\n" + (FIXTURES / "golint-red.txt").read_text(encoding="utf-8") + "garbage ::: line\n"
        r = self.lint("--no-known-red", rules=[{"text": text, "rc": 1}])
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" RED issues=2 ", r.first)
        self.assertTrue(any("WARN something odd" in n for n in self.summary_of(r)["notes"]))

    def test_docker_not_starting_is_a_tool_error(self):
        r = self.lint(rules=[{"text": "docker: Cannot connect to the Docker daemon at unix:///x.\n", "rc": 125}])
        self.assertEqual(r.returncode, 2, r.stdout)


class LintBatchTest(LintHarness):
    def test_oom_splits_into_four_batches_that_cover_every_package(self):
        r = self.lint("--no-known-red", rules=[dict(OOM, has="./..."), dict(RED, has="./internal/tag/..."), GREEN])
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.first, r"^GATE lint RED issues=2 known=0 ignored=0 batches=4/4 incomplete=0 ")
        got = [c["patterns"] for c in self.runs()]
        self.assertEqual(got[0], ["./..."])
        self.assertEqual(got[1:], [EXPANDED[0:3], EXPANDED[3:5], EXPANDED[5:7], EXPANDED[7:9]])
        out = pathlib.Path(r.first.rsplit("out=", 1)[1]).read_text(encoding="utf-8")
        headers = [x for x in out.splitlines() if x.startswith("=== batch ")]
        self.assertEqual(len(headers), 5, headers)
        self.assertIn(" ".join(EXPANDED[0:3]), headers[1])

    def test_all_batches_green_after_oom_is_green(self):
        r = self.lint(rules=[dict(OOM, has="./..."), GREEN])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.first, r"^GATE lint GREEN issues=0 known=0 ignored=0 batches=4/4 incomplete=0 ")

    def test_issues_printed_by_a_killed_run_are_not_counted(self):
        killed = {"text": ERRCHECK + "\nsignal: killed\n", "rc": 137}
        r = self.lint(rules=[dict(killed, has="./..."), GREEN])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" GREEN issues=0 ", r.first)

    def test_oom_text_without_137_also_splits(self):
        for marker in ("signal: killed", "Killed", "fatal error: runtime: cannot allocate memory"):
            with self.subTest(marker):
                r = self.lint(rules=[{"text": marker + "\n", "rc": 3, "has": "./..."}, GREEN])
                self.assertEqual(r.returncode, 0, r.stdout)
                self.assertIn("batches=4/4", r.first)

    def test_killed_inside_an_issue_message_is_not_oom(self):
        line = "internal/tag/handler.go:3:1: process Killed too early (godox)\n1 issues:\n* godox: 1\n"
        r = self.lint("--no-known-red", rules=[{"text": line, "rc": 1}])
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" RED issues=1 known=0 ignored=0 batches=1/1 ", r.first)
        self.assertEqual(len(self.runs()), 1)

    def test_a_single_pattern_that_still_ooms_is_incomplete(self):
        r = self.lint(rules=[dict(OOM, has="./..."), dict(OOM, has="./internal/tag/..."), GREEN])
        self.assertEqual(r.returncode, 4, r.stdout)
        # INVARIANT: [logger, tag] OOMs and halves; [logger] finishes, [tag] alone is the one leaf that never finished.
        self.assertRegex(r.first, r"^GATE lint INCOMPLETE issues=0 known=0 ignored=0 batches=4/5 incomplete=1 ")
        self.assertTrue(any(ln.startswith("did not finish: ./internal/tag/... ") for ln in r.stdout.splitlines()[2:]), r.stdout)
        self.assertEqual(self.summary_of(r)["incomplete"], ["./internal/tag/..."])
        got = [c["patterns"] for c in self.runs()]
        self.assertEqual(got[1:], [EXPANDED[0:3], EXPANDED[3:5], EXPANDED[5:7], EXPANDED[7:9],
                                   [EXPANDED[7]], [EXPANDED[8]]])

    def test_explicit_patterns_run_as_given_and_split_on_oom(self):
        r = self.lint("--pkg", "./internal/tag/...", "--pkg", "internal/logger", rules=[GREEN])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.runs()[-1]["patterns"], ["./internal/tag/...", "./internal/logger"])
        self.log_path.unlink()
        r = self.lint("--pkg", "./internal/tag/...", "--pkg", "internal/logger",
                      rules=[dict(OOM, has="./internal/tag/..."), GREEN])
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertEqual(self.summary_of(r)["incomplete"], ["./internal/tag/..."])
        self.assertIn(["./internal/logger"], [c["patterns"] for c in self.runs()])

    def test_missing_pkg_directory_is_a_tool_error(self):
        self.assertEqual(self.lint("--pkg", "./internal/nope/...", rules=[GREEN]).returncode, 2)

    def test_timeout_makes_that_batch_incomplete_without_splitting(self):
        r = self.lint("--timeout", "1s", rules=[{"text": "", "rc": 0, "sleep": 30}])
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn(" INCOMPLETE ", r.first)
        self.assertIn("incomplete=1", r.first)
        self.assertEqual(len(self.runs()), 1)
        self.assertIn("timeout", r.stdout)


class LintCommandTest(LintHarness):
    def test_exact_command_inside_one_dockerslot_slot(self):
        r = self.lint(rules=[dict(GREEN, shell=[sys.executable, str(GATE.parent / "dockerslot.py"), "--status"])])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        call = self.runs()[-1]
        self.assertEqual(call["argv"], [
            "run", "--rm", "-e", "GOMEMLIMIT=3000MiB", "-e", "GOGC=50",
            "-v", f"{self.tree}:/app", "-v", "deepweave-golangci-modcache:/go/pkg/mod",
            "-w", "/app/backend-golang", "golangci/golangci-lint:v2.14.0",
            "golangci-lint", "run", "--concurrency", "2", "./..."])
        self.assertIn("docker: 1/3 busy", call["shell"])
        cmd = self.summary_of(r)["cmd"]
        self.assertIn("dockerslot.py --max-wait 1800 -- docker run", cmd)
        self.assertNotIn("--all", cmd)

    def test_tuning_flags_reach_the_command(self):
        self.lint("--concurrency", "1", "--memlimit", "2GiB", "--gogc", "30", "--max-wait", "60",
                  "--image", "golangci/golangci-lint:v9", rules=[GREEN])
        a = self.runs()[-1]["argv"]
        self.assertIn("GOMEMLIMIT=2GiB", a)
        self.assertIn("GOGC=30", a)
        self.assertEqual(a[a.index("--concurrency") + 1], "1")
        self.assertIn("golangci/golangci-lint:v9", a)

    def test_bad_tuning_values_are_tool_errors(self):
        for args in (("--concurrency", "0"), ("--memlimit", "lots"), ("--gogc", "x"), ("--timeout", "soon")):
            with self.subTest(args):
                self.assertEqual(self.lint(*args, rules=[GREEN]).returncode, 2)

    def test_image_comes_from_the_tree_justfile(self):
        (self.tree / "justfile").write_text('set dotenv-load\ngolangci_lint_image := "golangci/golangci-lint:v7.7.7"\n', encoding="utf-8")
        self.lint(rules=[GREEN])
        self.assertIn("golangci/golangci-lint:v7.7.7", self.runs()[-1]["argv"])

    def test_image_falls_back_without_a_justfile_entry(self):
        (self.tree / "justfile").write_text("default:\n  echo hi\n", encoding="utf-8")
        self.lint(rules=[GREEN])
        self.assertIn("golangci/golangci-lint:v2.14.0", self.runs()[-1]["argv"])

    def test_slot_timeout_is_75_and_runs_nothing(self):
        holder = subprocess.Popen([sys.executable, str(GATE.parent / "dockerslot.py"), "--slots", "1", "--", sys.executable,
                                   "-c", "import time; time.sleep(30)"], env=self.env)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        deadline = time.time() + 10
        slot = self.tmp / "slots/docker-0.lock"
        while time.time() < deadline and not (slot.exists() and slot.read_text()):
            time.sleep(0.05)
        self.env["DOCKERSLOT_SLOTS"] = "1"
        r = self.lint("--max-wait", "0.2", rules=[GREEN])
        self.assertEqual(r.returncode, 75, r.stdout + r.stderr)
        self.assertIn("GATE lint SLOT-TIMEOUT ", r.first)
        self.assertEqual(self.runs(), [])


def count_issue_lines(name):
    """Independent count from the fixture, so a parser bug cannot agree with itself."""
    text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    return sum(1 for x in text.splitlines() if x.startswith("tmp/") and x.endswith(")") and ": " in x)


class LintIgnorePrefixTest(LintHarness):
    """golint-real is the real golangci-lint v2.14.0 run over the product repo: 45 issues, all in tmp/ probes."""
    REAL = text_replay("golint-real")

    def test_real_output_under_tmp_is_ignored_by_default(self):
        want = count_issue_lines("golint-real")
        self.assertEqual(want, 45)
        r = self.lint("--no-known-red", rules=[self.REAL])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.first, rf"^GATE lint GREEN issues=0 known=0 ignored={want} batches=1/1 incomplete=0 out=/")
        s = self.summary_of(r)
        self.assertEqual(s["lint"]["ignored"], want)
        self.assertEqual(len(s["lint"]["ignored_issues"]), want)
        self.assertEqual(s["lint"]["ignore_prefixes"], ["tmp/"])
        self.assertEqual(s["failed"], [])

    def test_no_ignore_counts_every_real_issue(self):
        r = self.lint("--no-known-red", "--no-ignore", rules=[self.REAL])
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.first, r"^GATE lint RED issues=45 known=0 ignored=0 batches=1/1 ")
        self.assertIn("tmp/assistprobe/main.go:532:17 Error return value of `out.Close` is not checked (errcheck)",
                      r.stdout.splitlines())

    def test_given_prefixes_replace_the_default_and_repeat(self):
        r = self.lint("--no-known-red", "--ignore-prefix", "internal/", rules=[self.REAL])
        self.assertIn(" RED issues=45 known=0 ignored=0 ", r.first)
        r = self.lint("--no-known-red", "--ignore-prefix", "internal/tag/", "--ignore-prefix", "./internal/logger/",
                      rules=[RED])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" GREEN issues=0 known=0 ignored=2 ", r.first)
        r = self.lint("--no-known-red", "--ignore-prefix", "internal/tag/", rules=[RED])
        self.assertIn(" RED issues=1 known=0 ignored=1 ", r.first)
        self.assertNotIn("internal/tag/handler.go:42:9", r.stdout)

    def test_ignored_issue_is_neither_new_nor_a_list_hit(self):
        k = self.known("internal/tag/handler.go :: errcheck :: Error return", "internal/logger/log.go :: unused :: func")
        r = self.lint("--known-red", k, "--ignore-prefix", "internal/tag/", rules=[RED])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" RED-KNOWN issues=0 known=1 ignored=1 ", r.first)

    def test_compile_error_under_an_ignored_prefix_is_not_build_fail(self):
        r = self.lint("--ignore-prefix", "internal/tag/", rules=[text_replay("golint-typecheck")])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" GREEN issues=0 known=0 ignored=1 ", r.first)

    def test_empty_prefix_and_contradiction_are_tool_errors(self):
        # INVARIANT: an empty prefix matches every path; it must never silently turn a whole run green.
        for args in (("--ignore-prefix", ""), ("--ignore-prefix", " "), ("--no-ignore", "--ignore-prefix", "tmp/")):
            with self.subTest(args):
                r = self.lint(*args, rules=[self.REAL])
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertEqual(self.runs(), [])


def ps_row(cid, state, minutes_ago, label="true"):
    created = time.strftime("%Y-%m-%d %H:%M:%S +0800 CST", time.gmtime(time.time() + 8 * 3600 - minutes_ago * 60))
    return f"{cid}\t{state}\t{created}\t{label}\tc\n"


class LintCleanDockerTest(LintHarness):
    def rows(self):
        return ps_row("exited1", "exited", 5) + ps_row("old1", "running", 90) + ps_row("young1", "running", 2)

    def removed(self):
        return sorted(a for c in self.calls("docker") if c["argv"][0] == "rm" for a in c["argv"][1:] if not a.startswith("-"))

    def test_flag_removes_orphans_before_the_run_and_reports_on_line_two(self):
        self.fake(docker={"ps": {"text": self.rows()}})
        r = self.lint("--clean-docker", rules=[GREEN])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[1], "clean-docker found=2 removed=2")
        self.assertEqual(self.removed(), ["exited1", "old1"])
        subs = [c["argv"][0] for c in self.calls("docker")]
        self.assertLess(subs.index("rm"), subs.index("run"))

    def test_without_the_flag_it_only_lists(self):
        self.fake(docker={"ps": {"text": self.rows()}})
        r = self.lint(rules=[GREEN])
        self.assertEqual(r.stdout.splitlines()[1], "clean-docker found=2 removed=0")
        self.assertEqual(self.removed(), [])


if __name__ == "__main__":
    unittest.main()
