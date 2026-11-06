# -*- coding: utf-8 -*-
"""gate: every verdict row, exit code and command shape, replayed from real captured engine output.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_gate.py' -v
"""
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _gate_fakes import (FIXTURES, GATE, Harness, count_go_events, fixture_rc, go_replay, text_replay)  # noqa: E402

LOGGER = "./internal/logger"
FE_SPEC = "projects/frontend/src/app/site-content.spec.ts"


class GoVerdictTest(Harness):
    def go(self, fixture, *args):
        self.fake(go=go_replay(fixture))
        return self.gate("go", "--tree", self.tree, *(args or ("--pkg", LOGGER)))

    def test_green_counts_match_the_stream(self):
        r = self.go("go_green")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        want = count_go_events("go_green", "pass")
        self.assertGreater(want, 0)
        self.assertRegex(r.first, rf"^GATE go GREEN pass={want} fail=0 skip=0 \d+(\.\d+)?s out=/")

    def test_zero_tests_is_never_green(self):
        for fixture in ("go_no_tests", "go_no_test_files", "go_all_skip"):
            with self.subTest(fixture):
                r = self.go(fixture)
                self.assertEqual(r.returncode, 3, r.stdout)
                self.assertIn(" NO-TESTS ", r.first)

    def test_empty_output_with_zero_exit_is_no_tests(self):
        self.fake(go={"text": "", "rc": 0})
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER)
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn(" NO-TESTS ", r.first)

    def test_red_lists_leaf_failures_with_messages(self):
        r = self.go("go_red")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("GATE go RED pass=1 fail=2 skip=0", r.first)
        self.assertIn("TestZZFail", r.stdout)
        self.assertIn("boom: want 1 got 2", r.stdout)
        self.assertIn("TestZZSub/bad_one", r.stdout)
        self.assertIn("sub failed", r.stdout)
        s = self.summary_of(r)
        self.assertEqual(sorted(s["failed"]), ["TestZZFail", "TestZZSub/bad_one"])

    def test_red_message_starts_at_the_assertion_not_log_noise(self):
        r = self.go("go_testify_red")
        self.assertIn("Not equal", r.stdout)
        self.assertNotIn("noise before", r.stdout)

    def test_expect_red_accepts_only_assertion_failures(self):
        self.fake(go=go_replay("go_red"))
        self.assertEqual(self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--expect", "red").returncode, 0)
        self.fake(go=go_replay("go_green"))
        self.assertEqual(self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--expect", "red").returncode, 1)

    def test_build_failures(self):
        for fixture, needle in (("go_compile_error", "cannot use"),  # build-output events
                                ("go_raw_pre124_compile", "cannot use"),  # stderr text, JSON only for the FAIL
                                ("go_raw_bad_gomod", "unknown directive"),  # no JSON at all
                                ("go_bad_flag", "no Go files")):  # the real text: go reads the flag as a package
            with self.subTest(fixture):
                r = self.go(fixture)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn(" BUILD-FAIL ", r.first)
                self.assertIn(needle, r.stdout)

    def test_build_failure_is_not_red_under_expect_red(self):
        for fixture in ("go_compile_error", "go_raw_pre124_compile", "go_raw_bad_gomod"):
            with self.subTest(fixture):
                self.fake(go=go_replay(fixture))
                r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--expect", "red")
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn(" BUILD-FAIL ", r.first)

    def test_interrupted_runs_are_incomplete_never_red(self):
        for fixture, unfinished, needle in (
                ("go_timeout", "TestZZSlow", "test timed out"),
                ("go_panic", None, "kaboom"),
                ("go_goroutine_panic", "TestZZGo", "bg"),
                ("go_os_exit0_in_test", None, "os.Exit(0)"),
                ("go_os_exit1_in_test", "TestZZExit", None),
                ("go_testmain_exit", None, None),
                ("go_binary_killed", "TestZZKilled", "signal: killed")):
            for expect in ("green", "red"):
                with self.subTest(fixture=fixture, expect=expect):
                    self.fake(go=go_replay(fixture))
                    r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--expect", expect)
                    self.assertEqual(r.returncode, 4, r.stdout)
                    self.assertIn(" INCOMPLETE ", r.first)
                    s = self.summary_of(r)
                    self.assertTrue(s["incomplete"])
                    if unfinished:
                        self.assertIn(unfinished, r.stdout)
                        self.assertTrue(any(unfinished in x for x in s["incomplete"]), s["incomplete"])
                    if needle:
                        self.assertIn(needle, r.stdout)

    def test_timeout_names_the_test_that_was_actually_running(self):
        r = self.go("go_timeout")
        self.assertIn("running at the timeout: TestZZSlow", r.stdout)

    def test_process_killed_mid_stream_is_incomplete(self):
        head = "".join((FIXTURES / "go_green.jsonl").read_text(encoding="utf-8").splitlines(True)[:6])
        self.fake(go={"text": head, "kill_self": True})
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER)
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn(" INCOMPLETE ", r.first)

    def test_package_without_terminal_event_is_incomplete_even_with_exit_zero(self):
        head = "".join((FIXTURES / "go_green.jsonl").read_text(encoding="utf-8").splitlines(True)[:6])
        self.fake(go={"text": head, "rc": 0})
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER)
        self.assertEqual(r.returncode, 4, r.stdout)

    def test_killed_with_no_output_is_incomplete(self):
        self.fake(go={"kill_self": True})
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER)
        self.assertEqual(r.returncode, 4, r.stdout)

    def test_panic_text_in_logs_does_not_change_the_verdict(self):
        self.assertIn(" GREEN ", self.go("go_panic_text_in_pass").first)
        self.assertIn(" RED ", self.go("go_red_with_panic_log").first)

    def test_failure_inside_incomplete_run_is_still_incomplete(self):
        r = self.go("go_panic")
        self.assertNotIn(" RED ", r.first)

    def test_explicit_package_with_zero_tests_blocks_green(self):
        r = self.go("go_multi_partial", "--pkg", LOGGER, "--pkg", "./internal/tag", "--run", "TestZZOk")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn(" NO-TESTS ", r.first)
        self.assertIn("internal/tag", r.stdout)

    def test_wildcard_package_with_zero_tests_is_allowed(self):
        r = self.go("go_multi_partial", "--pkg", "./internal/...", "--run", "TestZZOk")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" GREEN ", r.first)

    def test_run_name_matching_nothing_blocks_green(self):
        r = self.go("go_multi_partial", "--pkg", "./internal/...", "--run", "TestZZOk", "--run", "TestTypo")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("TestTypo", r.stdout)
        self.assertEqual(self.summary_of(r)["run_unmatched"], ["TestTypo"])

    def test_subtest_pattern_needs_a_matching_subtest(self):
        r = self.go("go_red", "--pkg", LOGGER, "--run", "TestZZFail", "--run", "TestZZSub/nomatch")
        self.assertEqual(self.summary_of(r)["run_unmatched"], ["TestZZSub/nomatch"])
        r = self.go("go_red", "--pkg", LOGGER, "--run", "TestZZFail", "--run", "TestZZSub/bad_one")
        self.assertEqual(self.summary_of(r)["run_unmatched"], [])

    def test_exit_zero_cannot_override_a_failed_test(self):
        self.fake(go={"file": str(FIXTURES / "go_red.jsonl"), "rc": 0})
        self.assertEqual(self.gate("go", "--tree", self.tree, "--pkg", LOGGER).returncode, 1)

    def test_nonzero_exit_with_all_green_stream_is_not_green(self):
        self.fake(go={"file": str(FIXTURES / "go_green.jsonl"), "rc": 1})
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER)
        self.assertEqual(r.returncode, 4, r.stdout)

    def test_stdout_truncates_failures_and_states_the_total(self):
        lines = ['{"Action":"start","Package":"p"}']
        for i in range(25):
            lines.append(json.dumps({"Action": "run", "Package": "p", "Test": f"TestN{i}"}))
            lines.append(json.dumps({"Action": "output", "Package": "p", "Test": f"TestN{i}", "Output": "    a_test.go:1: nope\n"}))
            lines.append(json.dumps({"Action": "fail", "Package": "p", "Test": f"TestN{i}"}))
        lines.append(json.dumps({"Action": "output", "Package": "p", "Output": "FAIL\n"}))
        lines.append(json.dumps({"Action": "fail", "Package": "p"}))
        self.fake(go={"text": "\n".join(lines) + "\n", "rc": 1})
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER)
        self.assertEqual(r.returncode, 1)
        self.assertIn("fail=25", r.first)
        self.assertLessEqual(len(r.stdout.splitlines()), 30)
        self.assertIn("25", r.stdout.splitlines()[-1] + r.stdout.splitlines()[-2])
        self.assertIn("TestN19", r.stdout)
        self.assertNotIn("TestN20", r.stdout)
        self.assertEqual(len(self.summary_of(r)["failed"]), 25)


class KnownRedTest(Harness):
    def known(self, *names):
        p = self.tmp / "known.txt"
        p.write_text("\n".join(names) + "\n", encoding="utf-8")
        return p

    def run_with(self, fixture, *names, expect=None):
        self.fake(go=go_replay(fixture))
        args = ["go", "--tree", self.tree, "--pkg", LOGGER, "--known-red", self.known(*names)]
        if expect:
            args += ["--expect", expect]
        return self.gate(*args)

    def test_only_known_reds_is_red_known_and_passes_the_green_gate(self):
        r = self.run_with("go_red", "TestZZFail", "TestZZSub/bad_one")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" RED-KNOWN ", r.first)
        kr = self.summary_of(r)["known_red"]
        self.assertEqual(sorted(kr["existing"]), ["TestZZFail", "TestZZSub/bad_one"])
        self.assertEqual(kr["new"], [])

    def test_only_known_reds_does_not_satisfy_expect_red(self):
        r = self.run_with("go_red", "TestZZFail", "TestZZSub/bad_one", expect="red")
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_new_red_is_separated_from_existing(self):
        r = self.run_with("go_red", "TestZZFail")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" RED ", r.first)
        kr = self.summary_of(r)["known_red"]
        self.assertEqual(kr["new"], ["TestZZSub/bad_one"])
        self.assertEqual(kr["existing"], ["TestZZFail"])
        self.assertIn("新增紅", r.stdout)
        self.assertIn("既存紅", r.stdout)

    def test_star_covers_a_listed_parents_subtests(self):
        kr = self.summary_of(self.run_with("go_red", "TestZZSub/*"))["known_red"]
        self.assertEqual(kr["existing"], ["TestZZSub/bad_one"])
        self.assertEqual(kr["new"], ["TestZZFail"])

    def test_name_prefix_does_not_match(self):
        r = self.run_with("go_red", "TestZZ", "TestZZSu")
        self.assertIn(" RED ", r.first)
        self.assertEqual(sorted(self.summary_of(r)["known_red"]["new"]), ["TestZZFail", "TestZZSub/bad_one"])

    def test_listed_but_absent_and_listed_but_passed_are_reported(self):
        r = self.run_with("go_red", "TestZZFail", "TestZZSub/bad_one", "TestGone", "TestZZSub/ok")
        kr = self.summary_of(r)["known_red"]
        self.assertEqual(kr["absent"], ["TestGone"])
        self.assertEqual(kr["passed"], ["TestZZSub/ok"])
        self.assertIn("名單內但這次沒出現", r.stdout)
        self.assertIn("TestGone", r.stdout)

    def test_known_red_cannot_absorb_an_incomplete_run(self):
        r = self.run_with("go_timeout", "TestZZSlow")
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn(" INCOMPLETE ", r.first)

    def test_comments_and_blanks_are_ignored(self):
        p = self.tmp / "k.txt"
        p.write_text("# baseline\n\nTestZZFail\nTestZZSub/bad_one\n", encoding="utf-8")
        self.fake(go=go_replay("go_red"))
        self.assertEqual(self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--known-red", p).returncode, 0)

    def test_empty_or_missing_list_is_a_tool_error(self):
        self.fake(go=go_replay("go_red"))
        empty = self.tmp / "empty.txt"
        empty.write_text("# nothing\n", encoding="utf-8")
        self.assertEqual(self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--known-red", empty).returncode, 2)
        self.assertEqual(self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--known-red", self.tmp / "nope").returncode, 2)


class GoCommandTest(Harness):
    def argv(self):
        return self.calls("go")[-1]["argv"]

    def test_unit_command_shape_and_environment(self):
        self.fake(go=go_replay("go_green"))
        self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--run", "TestA", "--run", "TestB", "--race")
        call = self.calls("go")[-1]
        self.assertIsNone(call["goroot"])
        self.assertEqual(call["cwd"], str(self.tree / "backend-golang"))
        self.assertEqual(call["pwd"], str(self.tree / "backend-golang"))
        a = call["argv"]
        self.assertEqual(a[:2], ["test", "-json"])
        self.assertIn("-count=1", a)
        self.assertIn("-race", a)
        self.assertEqual(a[a.index("-run") + 1], "TestA|TestB")
        self.assertEqual(a[-1], LOGGER)
        self.assertNotIn("-tags", a)

    def test_package_forms_normalize(self):
        self.fake(go=go_replay("go_green"))
        for form in ("internal/logger", "./internal/logger", "backend-golang/internal/logger",
                     str(self.tree / "backend-golang/internal/logger")):
            with self.subTest(form):
                self.gate("go", "--tree", self.tree, "--pkg", form)
                self.assertEqual(self.argv()[-1], LOGGER)

    def test_tags_off_and_overlay_and_timeout_flags(self):
        self.fake(go=go_replay("go_green"))
        ov = self.tmp / "o.json"
        ov.write_text('{"Replace":{}}', encoding="utf-8")
        self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--tags-off", "--overlay", ov, "--timeout", "90s")
        a = self.argv()
        self.assertIn("-tags=", a)
        self.assertEqual(a[a.index("-overlay") + 1], str(ov))
        self.assertEqual(a[a.index("-timeout") + 1], "90s")

    def test_integ_adds_tag_default_timeout_and_takes_a_docker_slot(self):
        self.fake(go=dict(go_replay("go_green"), shell=[sys.executable, str(GATE.parent / "dockerslot.py"), "--status"]))
        r = self.gate("go", "--tree", self.tree, "--integ", "--pkg", LOGGER)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        call = self.calls("go")[-1]
        a = call["argv"]
        self.assertEqual(a[a.index("-tags") + 1], "integration")
        self.assertEqual(a[a.index("-timeout") + 1], "1500s")
        self.assertIn("-count=1", a)
        self.assertIn("1/3 busy", call["shell"])

    def test_all_holds_every_slot_with_long_timeout(self):
        self.fake(go=dict(go_replay("go_green"), shell=[sys.executable, str(GATE.parent / "dockerslot.py"), "--status"]))
        r = self.gate("go", "--tree", self.tree, "--all")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        call = self.calls("go")[-1]
        a = call["argv"]
        self.assertEqual(a[a.index("-timeout") + 1], "2400s")
        self.assertEqual(a[a.index("-tags") + 1], "integration")
        self.assertEqual(a[-1], "./...")
        self.assertIn("3/3 busy", call["shell"])

    def test_all_waits_up_to_1800s_for_slots(self):
        import gate
        ns = gate.parse_args(["go", "--tree", str(self.tree), "--all"])
        argv = gate.go_argv(ns, str(self.tree))
        i = argv.index("--max-wait")
        self.assertEqual(argv[i + 1], "1800")
        self.assertIn("--all", argv[:i + 2])

    def test_slot_timeout_passes_75_through(self):
        holder = subprocess.Popen([sys.executable, str(GATE.parent / "dockerslot.py"), "--slots", "1", "--", sys.executable, "-c", "import time; time.sleep(30)"],
                                  env=self.env)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        deadline = time.time() + 10
        slot = self.tmp / "slots/docker-0.lock"
        while time.time() < deadline and not (slot.exists() and slot.read_text()):
            time.sleep(0.05)
        self.env["DOCKERSLOT_SLOTS"] = "1"
        self.fake(go=go_replay("go_green"))
        r = self.gate("go", "--tree", self.tree, "--integ", "--pkg", LOGGER, "--max-wait", "0.2")
        self.assertEqual(r.returncode, 75, r.stdout + r.stderr)
        self.assertEqual(self.calls("go"), [])
        self.assertNotIn(" GREEN ", r.first)

    def test_exit_75_from_the_test_itself_is_not_a_slot_timeout(self):
        self.fake(go={"text": "", "rc": 75})
        r = self.gate("go", "--tree", self.tree, "--integ", "--pkg", LOGGER)
        self.assertEqual(r.returncode, 4, r.stdout)

    def test_build_and_vet_modes(self):
        self.fake(go={"text": "", "rc": 0})
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--build")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("GATE go-build GREEN", r.first)
        a = self.argv()
        self.assertEqual(a[0], "build")
        self.assertEqual(a[a.index("-o") + 1], os.devnull)
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--vet")
        self.assertIn("GATE go-vet GREEN", r.first)
        self.assertEqual(self.argv()[0], "vet")
        self.fake(go={"text": "# p\na.go:1:1: bad thing\n", "rc": 1})
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--vet")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("GATE go-vet BUILD-FAIL", r.first)
        self.assertIn("bad thing", r.stdout)

    def test_argument_errors_are_tool_errors_before_any_run(self):
        ov = self.tmp / "missing.json"
        cases = [
            ("go", "--tree", self.tmp / "nope"),
            ("go", "--tree", self.tree, "--pkg", "./internal/nope"),
            ("go", "--tree", self.tree, "--pkg", "./internal/nope/..."),
            ("go", "--tree", self.tree, "--integ"),
            ("go", "--tree", self.tree, "--integ", "--pkg", LOGGER, "--tags-off"),
            ("go", "--tree", self.tree, "--pkg", LOGGER, "--overlay", ov),
            ("go", "--tree", self.tree, "--pkg", LOGGER, "--build", "--vet"),
            ("go", "--tree", self.tree, "--pkg", LOGGER, "--build", "--run", "X"),
            ("go", "--tree", self.tree, "--pkg", LOGGER, "--timeout", "soon"),
            ("nosuchmode",),
            ("go",),
        ]
        for args in cases:
            with self.subTest(args=[str(a) for a in args[1:]]):
                r = self.gate(*args)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(self.calls("go"), [])

    def test_missing_go_binary_is_a_tool_error(self):
        (self.bin / "go").unlink()
        self.env["PATH"] = str(self.bin)
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_sigterm_kills_the_run_and_reports_incomplete(self):
        self.fake(go={"text": "", "sleep": 30})
        p = subprocess.Popen([sys.executable, str(GATE), "go", "--tree", str(self.tree), "--pkg", LOGGER],
                             env=self.env, stdout=subprocess.PIPE, text=True)
        self.addCleanup(p.stdout.close)
        deadline = time.time() + 10
        while time.time() < deadline and not self.calls("go"):
            time.sleep(0.05)
        p.send_signal(signal.SIGTERM)
        out, _ = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 4, out)
        self.assertIn(" INCOMPLETE ", out.splitlines()[0])
        child = self.calls("go")[-1]["pid"]
        deadline = time.time() + 10
        while time.time() < deadline and _alive(child):
            time.sleep(0.05)
        self.assertFalse(_alive(child))


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class OutputShapeTest(Harness):
    def test_default_out_goes_under_tree_tmp_gate(self):
        self.fake(go=go_replay("go_green"))
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER)
        out = r.first.rsplit("out=", 1)[1]
        self.assertTrue(out.startswith(str(self.tree / "tmp/gate/go-")), out)
        self.assertTrue(pathlib.Path(out).exists())

    def test_two_runs_do_not_share_a_default_out(self):
        self.fake(go=go_replay("go_green"))
        a = self.gate("go", "--tree", self.tree, "--pkg", LOGGER).first.rsplit("out=", 1)[1]
        b = self.gate("go", "--tree", self.tree, "--pkg", LOGGER).first.rsplit("out=", 1)[1]
        self.assertNotEqual(a, b)

    def test_out_file_keeps_the_failure_message_verbatim_as_text(self):
        self.fake(go=go_replay("go_red"))
        out = self.fresh_out()
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--out", out)
        self.assertEqual(r.first.rsplit("out=", 1)[1], str(out))
        text = out.read_text(encoding="utf-8")
        self.assertIn("zz_probe_test.go:15: boom: want 1 got 2", text)
        self.assertNotIn('"Action"', text)

    def test_summary_json_shape(self):
        self.fake(go=go_replay("go_red"))
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--label", "slice-a")
        s = self.summary_of(r)
        for key in ("mode", "tree", "verdict", "exit", "pass", "fail", "skip", "failed", "incomplete", "known_red", "out", "seconds", "cmd", "label"):
            self.assertIn(key, s)
        self.assertEqual(s["mode"], "go")
        self.assertEqual(s["verdict"], "RED")
        self.assertEqual(s["exit"], 1)
        self.assertEqual(s["fail"], 2)
        self.assertEqual(s["label"], "slice-a")
        self.assertEqual(s["tree"], str(self.tree))

    def test_import_api_returns_the_same_summary(self):
        import gate
        env = dict(os.environ)
        os.environ.update(self.env)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(env)))
        self.fake(go=go_replay("go_red"))
        s = gate.run_gate(["go", "--tree", str(self.tree), "--pkg", LOGGER, "--out", str(self.fresh_out())])
        self.assertEqual(s["verdict"], "RED")
        self.assertEqual(s["exit"], 1)
        with self.assertRaises(gate.ToolError):
            gate.run_gate(["go", "--tree", str(self.tmp / "nope")])

    def test_tool_error_replaces_an_earlier_green_summary_at_the_same_out(self):
        self.fake(go=go_replay("go_green"))
        out = self.fresh_out()
        self.assertEqual(self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--out", out).returncode, 0)
        r = self.gate("go", "--tree", self.tree, "--pkg", "./internal/nope", "--out", out)
        self.assertEqual(r.returncode, 2)
        s = json.loads(pathlib.Path(str(out) + ".summary.json").read_text(encoding="utf-8"))
        self.assertEqual(s["verdict"], "TOOL-ERROR")
        self.assertEqual(s["exit"], 2)
        self.assertIn("internal/nope", s["error"])

    def test_killed_gate_leaves_no_earlier_summary_behind(self):
        self.fake(go=go_replay("go_green"))
        out = self.fresh_out()
        self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--out", out)
        summary = pathlib.Path(str(out) + ".summary.json")
        self.assertTrue(summary.exists())
        self.fake(go={"text": "", "sleep": 30})
        p = subprocess.Popen([sys.executable, str(GATE), "go", "--tree", str(self.tree), "--pkg", LOGGER, "--out", str(out)],
                             env=self.env, stdout=subprocess.DEVNULL)
        deadline = time.time() + 10
        while time.time() < deadline and len(self.calls("go")) < 2:
            time.sleep(0.05)
        self.assertFalse(summary.exists())
        p.kill()
        p.wait()
        os.kill(self.calls("go")[-1]["pid"], signal.SIGKILL)

    def test_stale_raw_stream_file_is_removed_after_a_normal_run(self):
        self.fake(go=go_replay("go_green"))
        out = self.fresh_out()
        self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--out", out)
        self.assertFalse(pathlib.Path(str(out) + ".raw").exists())


class FeTest(Harness):
    def fe(self, fixture, *args):
        self.fake(npx=text_replay(fixture))
        return self.gate("fe", "--tree", self.tree, "--project", "frontend", *(args or ("--include", FE_SPEC)))

    def test_green_counts_and_command(self):
        r = self.fe("fe_green")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.first, r"^GATE fe GREEN pass=2 fail=0 skip=0 ")
        call = self.calls("npx")[-1]
        self.assertEqual(call["cwd"], str(self.tree / "angular-workspace"))
        self.assertEqual(call["argv"][:4], ["ng", "test", "frontend", "--watch=false"])
        self.assertEqual(call["argv"][call["argv"].index("--include") + 1], FE_SPEC)

    def test_include_forms_normalize_relative_to_the_workspace(self):
        self.fake(npx=text_replay("fe_green"))
        for form in (FE_SPEC, f"angular-workspace/{FE_SPEC}", str(self.tree / "angular-workspace" / FE_SPEC)):
            with self.subTest(form):
                r = self.gate("fe", "--tree", self.tree, "--project", "frontend", "--include", form)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                a = self.calls("npx")[-1]["argv"]
                self.assertEqual(a[a.index("--include") + 1], FE_SPEC)

    def test_include_glob_must_match_something(self):
        self.fake(npx=text_replay("fe_green"))
        self.assertEqual(self.gate("fe", "--tree", self.tree, "--project", "frontend", "--include", "**/site-content.spec.ts").returncode, 0)
        self.assertEqual(self.gate("fe", "--tree", self.tree, "--project", "frontend", "--include", "**/zz-nope.spec.ts").returncode, 2)

    def test_missing_include_is_a_tool_error_before_running(self):
        self.fake(npx=text_replay("fe_green"))
        r = self.gate("fe", "--tree", self.tree, "--project", "frontend", "--include", "projects/frontend/src/app/zz.spec.ts")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(self.calls("npx"), [])

    def test_missing_node_modules_is_a_tool_error_naming_the_fix(self):
        (self.tree / "angular-workspace/node_modules").rmdir()
        r = self.gate("fe", "--tree", self.tree, "--project", "frontend")
        self.assertEqual(r.returncode, 2)
        self.assertIn("pnpm install", r.stdout + r.stderr)
        self.assertEqual(self.calls("npx"), [])

    def test_unknown_project_is_a_tool_error(self):
        self.assertEqual(self.gate("fe", "--tree", self.tree, "--project", "nope").returncode, 2)

    def test_could_not_determine_executable_is_a_tool_error(self):
        self.fake(npx={"text": "npm error could not determine executable to run\n", "rc": 1})
        r = self.gate("fe", "--tree", self.tree, "--project", "frontend")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_red_names_and_messages_without_ansi(self):
        r = self.fe("fe_red")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("GATE fe RED pass=1 fail=2 skip=0", r.first)
        s = self.summary_of(r)
        self.assertEqual(sorted(s["failed"]), [
            "projects/frontend/src/app/zz-probe-fe_red.spec.ts > zz probe > fails one",
            "projects/frontend/src/app/zz-probe-fe_red.spec.ts > zz probe > nested > fails two"])
        self.assertIn("expected 1 to be 2", r.stdout)
        self.assertIn("kaboom two", r.stdout)
        self.assertNotIn("\x1b", r.stdout)
        self.assertNotIn("\x1b", pathlib.Path(s["out"]).read_text(encoding="utf-8"))

    def test_expect_red(self):
        self.fake(npx=text_replay("fe_red"))
        self.assertEqual(self.gate("fe", "--tree", self.tree, "--project", "frontend", "--expect", "red").returncode, 0)

    def test_compile_error_is_build_fail_and_not_red(self):
        r = self.fe("fe_compile_error", "--include", FE_SPEC, "--expect", "red")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" BUILD-FAIL ", r.first)
        self.assertIn("TS2322", r.stdout)

    def test_no_tests_forms(self):
        for fixture in ("fe_no_tests", "fe_all_skip"):
            with self.subTest(fixture):
                r = self.fe(fixture)
                self.assertEqual(r.returncode, 3, r.stdout)
                self.assertIn(" NO-TESTS ", r.first)

    def test_suite_that_failed_to_load_is_incomplete(self):
        for fixture in ("fe_collect_error", "fe_empty_spec"):
            with self.subTest(fixture):
                r = self.fe(fixture)
                self.assertEqual(r.returncode, 4, r.stdout)
                self.assertIn(" INCOMPLETE ", r.first)

    def test_unhandled_error_with_all_passed_is_incomplete(self):
        r = self.fe("fe_unhandled")
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn("late boom", r.stdout)

    def test_no_summary_line_is_incomplete_even_with_exit_zero(self):
        head = (FIXTURES / "fe_green.txt").read_text(encoding="utf-8").split("Test Files")[0]
        self.fake(npx={"text": head, "rc": 0})
        r = self.gate("fe", "--tree", self.tree, "--project", "frontend")
        self.assertEqual(r.returncode, 4, r.stdout)

    def test_killed_is_incomplete(self):
        self.fake(npx={"text": "Building...\n", "kill_self": True})
        self.assertEqual(self.gate("fe", "--tree", self.tree, "--project", "frontend").returncode, 4)

    def test_wall_clock_timeout_is_incomplete(self):
        self.fake(npx={"text": "Building...\n", "sleep": 30})
        r = self.gate("fe", "--tree", self.tree, "--project", "frontend", "--timeout", "1s")
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn("timeout", r.stdout.lower())

    def test_fewer_test_files_than_includes_is_incomplete(self):
        r = self.fe("fe_green", "--include", FE_SPEC, "--include", "projects/frontend/src/app/other.spec.ts")
        self.assertEqual(r.returncode, 4, r.stdout)

    def test_known_red_uses_the_full_name(self):
        k = self.tmp / "k.txt"
        k.write_text("projects/frontend/src/app/zz-probe-fe_red.spec.ts > zz probe > fails one\n"
                     "projects/frontend/src/app/zz-probe-fe_red.spec.ts > zz probe > nested > fails two\n", encoding="utf-8")
        self.fake(npx=text_replay("fe_red"))
        r = self.gate("fe", "--tree", self.tree, "--project", "frontend", "--known-red", k)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" RED-KNOWN ", r.first)

    def test_error_text_in_test_logs_is_not_a_build_failure(self):
        real = (FIXTURES / "fe_green.txt").read_text(encoding="utf-8")
        noisy = real.replace(" Test Files", "stderr | x.spec.ts > y\n[ERROR] logged by the code under test\nNo tests found matching nothing\n\n Test Files", 1)
        self.assertNotEqual(real, noisy)
        self.fake(npx={"text": noisy, "rc": 0})
        r = self.gate("fe", "--tree", self.tree, "--project", "frontend")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" GREEN ", r.first)

    def test_test_title_with_brackets_is_a_failure_not_a_suite_error(self):
        real = (FIXTURES / "fe_red.txt").read_text(encoding="utf-8")
        titled = real.replace("fails one", "fails one [ a b ]")
        self.assertNotEqual(real, titled)
        self.fake(npx={"text": titled, "rc": 1})
        r = self.gate("fe", "--tree", self.tree, "--project", "frontend")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" RED ", r.first)

    def test_fe_build(self):
        self.fake(npx={"text": "Application bundle generation complete.\n", "rc": 0})
        r = self.gate("fe-build", "--tree", self.tree, "--project", "admin")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("GATE fe-build GREEN", r.first)
        self.assertEqual(self.calls("npx")[-1]["argv"], ["ng", "build", "admin"])
        self.fake(npx=text_replay("fe_compile_error"))
        r = self.gate("fe-build", "--tree", self.tree, "--project", "admin")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" BUILD-FAIL ", r.first)
        self.assertIn("TS2322", r.stdout)
        self.fake(npx={"kill_self": True})
        self.assertEqual(self.gate("fe-build", "--tree", self.tree, "--project", "admin").returncode, 4)


class AiTest(Harness):
    def ai(self, fixture, *args):
        self.fake(docker={"run": text_replay(fixture)})
        return self.gate("ai", "--tree", self.tree, "--repo", self.repo, *args)

    def test_green_and_exact_container_command(self):
        r = self.ai("ai_green", "tests/test_config.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.first, r"^GATE ai GREEN pass=25 fail=0 skip=0 ")
        a = self.calls("docker")[-1]["argv"]
        self.assertEqual(a, [
            "run", "--rm", "--network", "none",
            "-v", f"{self.tree}/ai-service:/app:ro",
            "-v", f"{self.repo}/ai-service/.venv:/app/.venv:ro",
            "-w", "/app", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "LANGFUSE_TRACING_ENABLED=false",
            "-e", "APP_ENV=development", "-e", "MYPY_CACHE_DIR=/tmp/mypy",
            "ghcr.io/astral-sh/uv:python3.12-bookworm", ".venv/bin/python",
            "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_config.py"])

    def test_mount_point_is_created_in_the_tree(self):
        self.assertFalse((self.tree / "ai-service/.venv").exists())
        self.ai("ai_green", "tests/test_config.py")
        self.assertTrue((self.tree / "ai-service/.venv").is_dir())

    def test_path_forms_normalize_and_keep_the_node_id(self):
        self.fake(docker={"run": text_replay("ai_green")})
        for form, want in (("tests/test_config.py", "tests/test_config.py"),
                           ("ai-service/tests/test_config.py", "tests/test_config.py"),
                           (str(self.tree / "ai-service/tests/test_config.py"), "tests/test_config.py"),
                           ("tests/test_config.py::test_a", "tests/test_config.py::test_a")):
            with self.subTest(form):
                r = self.gate("ai", "--tree", self.tree, "--repo", self.repo, form)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(self.calls("docker")[-1]["argv"][-1], want)

    def test_missing_path_is_a_tool_error_before_running(self):
        r = self.ai("ai_green", "tests/test_zz.py")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(self.calls("docker"), [])

    def test_pytest_verdicts(self):
        for fixture, verdict, code, needle in (
                ("ai_no_tests", "NO-TESTS", 3, None),
                ("ai_all_skip", "NO-TESTS", 3, None),
                ("ai_red", "RED", 1, "test_zz_probe.py::test_bad"),
                ("ai_collect_error", "BUILD-FAIL", 1, "not_a_module_zz"),
                ("ai_fixture_error", "INCOMPLETE", 4, "fx")):
            with self.subTest(fixture):
                r = self.ai(fixture, "tests/test_config.py")
                self.assertEqual(r.returncode, code, r.stdout)
                self.assertIn(f" {verdict} ", r.first)
                if needle:
                    self.assertIn(needle, r.stdout)

    def test_red_counts(self):
        r = self.ai("ai_red", "tests/test_config.py")
        self.assertIn("pass=1 fail=1 skip=0", r.first)
        self.assertEqual(self.summary_of(r)["failed"], ["tests/test_zz_probe.py::test_bad"])

    def test_collect_error_is_not_red_under_expect_red(self):
        self.assertEqual(self.ai("ai_collect_error", "tests/test_config.py", "--expect", "red").returncode, 1)

    def test_container_start_failure_and_usage_errors_are_tool_errors(self):
        for fixture in ("ai_venv_macos", "ai_missing_path"):
            with self.subTest(fixture):
                r = self.ai(fixture, "tests/test_config.py")
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_macos_venv_is_refused_before_starting_a_container(self):
        (self.repo / "ai-service/.venv/pyvenv.cfg").write_text(
            "home = /Users/x/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin\n", encoding="utf-8")
        r = self.ai("ai_green", "tests/test_config.py")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("macOS", r.stdout + r.stderr)
        self.assertEqual(self.calls("docker"), [])

    def test_missing_venv_is_a_tool_error(self):
        (self.repo / "ai-service/.venv/pyvenv.cfg").unlink()
        self.assertEqual(self.ai("ai_green", "tests/test_config.py").returncode, 2)

    def test_repo_is_required(self):
        self.assertEqual(self.gate("ai", "--tree", self.tree).returncode, 2)

    def test_ruff(self):
        r = self.ai("ai_ruff_green", "--kind", "ruff", "tests/test_config.py")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("GATE ai-ruff GREEN", r.first)
        self.assertEqual(self.calls("docker")[-1]["argv"][-6:], ["-m", "ruff", "check", "--no-cache", "--output-format=concise", "tests/test_config.py"])
        r = self.ai("ai_ruff_red", "--kind", "ruff", "tests/test_config.py")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("GATE ai-ruff RED pass=0 fail=3", r.first)
        self.assertIn("F401", r.stdout)

    def test_ruff_default_targets(self):
        self.ai("ai_ruff_green", "--kind", "ruff")
        self.assertEqual(self.calls("docker")[-1]["argv"][-2:], ["app", "tests"])

    def test_mypy(self):
        r = self.ai("ai_mypy_green", "--kind", "mypy")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("GATE ai-mypy GREEN", r.first)
        self.assertEqual(self.calls("docker")[-1]["argv"][-3:], ["-m", "mypy", "app"])
        r = self.ai("ai_mypy_red", "--kind", "mypy")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("GATE ai-mypy RED pass=0 fail=2", r.first)
        self.assertIn("app/zz_probe.py:1", r.stdout)

    def test_docker_daemon_down_is_a_tool_error_even_with_exit_1(self):
        self.fake(docker={"run": {"text": "docker: Cannot connect to the Docker daemon at unix:///x. Is it running?\n", "rc": 1}})
        r = self.gate("ai", "--tree", self.tree, "--repo", self.repo)
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_daemon_text_in_test_output_does_not_hide_a_real_failure(self):
        real = (FIXTURES / "ai_red.txt").read_text(encoding="utf-8")
        self.fake(docker={"run": {"text": real.replace("E       assert 1 == 2", "E       Cannot connect to the Docker daemon"), "rc": 1}})
        r = self.gate("ai", "--tree", self.tree, "--repo", self.repo)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" RED ", r.first)

    def test_lint_exit_zero_without_its_success_line_is_incomplete(self):
        self.fake(docker={"run": {"text": "", "rc": 0}})
        for kind in ("ruff", "mypy"):
            with self.subTest(kind):
                self.assertEqual(self.gate("ai", "--tree", self.tree, "--repo", self.repo, "--kind", kind).returncode, 4)


def ps_row(cid, state, created, label="true", name="c"):
    return f"{cid}\t{state}\t{created}\t{label}\t{name}\n"


def stamp(minutes_ago):
    return time.strftime("%Y-%m-%d %H:%M:%S +0800 CST", time.gmtime(time.time() + 8 * 3600 - minutes_ago * 60))


class CleanDockerTest(Harness):
    def rows(self):
        return (ps_row("exited1", "exited", stamp(5)) + ps_row("dead1", "dead", stamp(5))
                + ps_row("runningyoung", "running", stamp(49)) + ps_row("runningold", "running", stamp(51))
                + ps_row("createdyoung", "created", stamp(1)))

    def removed(self):
        ids = []
        for c in self.calls("docker"):
            if c["argv"][0] == "rm":
                ids += [a for a in c["argv"][1:] if not a.startswith("-")]
        return sorted(ids)

    def test_dry_run_lists_and_removes_nothing(self):
        self.fake(docker={"ps": {"text": self.rows()}})
        r = self.gate("clean-docker")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for cid in ("exited1", "dead1", "runningold"):
            self.assertIn(cid, r.stdout)
        self.assertNotIn("runningyoung", r.stdout)
        self.assertNotIn("createdyoung", r.stdout)
        self.assertEqual(self.removed(), [])
        self.assertIn("found=3", r.first)
        self.assertIn("--apply", r.stdout)

    def test_apply_removes_only_orphans_and_counts(self):
        self.fake(docker={"ps": {"text": self.rows()}})
        r = self.gate("clean-docker", "--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.removed(), ["dead1", "exited1", "runningold"])
        self.assertIn("removed=3", r.first)

    def test_listing_is_filtered_by_the_label(self):
        self.fake(docker={"ps": {"text": ""}})
        r = self.gate("clean-docker")
        self.assertEqual(r.returncode, 0)
        a = self.calls("docker")[-1]["argv"]
        self.assertEqual(a[a.index("--filter") + 1], "label=org.testcontainers=true")
        self.assertIn("-a", a)
        self.assertIn("found=0", r.first)

    def test_row_without_the_label_is_never_touched(self):
        self.fake(docker={"ps": {"text": ps_row("devstack", "exited", stamp(500), label="")}})
        r = self.gate("clean-docker", "--apply")
        self.assertEqual(self.removed(), [])
        self.assertIn("removed=0", r.first)

    def test_unparsable_row_is_a_tool_error_not_silently_kept(self):
        self.fake(docker={"ps": {"text": "garbage row\n"}})
        self.assertEqual(self.gate("clean-docker").returncode, 2)
        self.fake(docker={"ps": {"text": ps_row("x", "running", "last tuesday")}})
        self.assertEqual(self.gate("clean-docker").returncode, 2)

    def test_docker_failure_is_a_tool_error(self):
        self.fake(docker={"ps": {"text": "Cannot connect to the Docker daemon\n", "rc": 1}})
        self.assertEqual(self.gate("clean-docker").returncode, 2)

    def test_rm_failure_is_a_tool_error(self):
        self.fake(docker={"ps": {"text": self.rows()}, "rm": {"text": "boom\n", "rc": 1}})
        self.assertEqual(self.gate("clean-docker", "--apply").returncode, 2)

    def test_integ_run_cleans_dead_and_exited_only_and_prints_count(self):
        self.fake(go=go_replay("go_green"), docker={"ps": {"text": self.rows()}})
        r = self.gate("go", "--tree", self.tree, "--integ", "--pkg", LOGGER)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.removed(), ["dead1", "exited1"])
        self.assertIn("2", [ln for ln in r.stdout.splitlines() if "docker" in ln.lower()][0])
        self.assertEqual(self.summary_of(r)["docker_cleaned"], 2)

    def test_integ_run_cleans_even_when_red(self):
        self.fake(go=go_replay("go_red"), docker={"ps": {"text": self.rows()}})
        self.gate("go", "--tree", self.tree, "--integ", "--pkg", LOGGER)
        self.assertEqual(self.removed(), ["dead1", "exited1"])

    def test_unit_run_never_touches_docker(self):
        self.fake(go=go_replay("go_green"), docker={"ps": {"text": self.rows()}})
        self.gate("go", "--tree", self.tree, "--pkg", LOGGER)
        self.assertEqual(self.calls("docker"), [])

    def test_docker_failure_after_a_run_does_not_change_the_verdict(self):
        self.fake(go=go_replay("go_green"), docker={"ps": {"text": "nope\n", "rc": 1}})
        r = self.gate("go", "--tree", self.tree, "--integ", "--pkg", LOGGER)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" GREEN ", r.first)
        self.assertIn("clean", r.stdout.lower())


ROUTES_SPEC = "projects/frontend/src/app/app.routes.spec.ts"
SERVER_NOW = "TypeError: Cannot read properties of null (reading 'server_now')"
CHARACTERS_TEST = ("projects/frontend/src/app/core/data/characters.spec.ts > CharactersService — the storage mock "
                   "does not outlive this file > hands the next spec file an un-mocked localStorage it has not written to")


class FeUnhandledBaselineTest(Harness):
    """fe_full_unhandled*: real whole-project frontend runs, 4 unhandled errors from app.routes.spec.ts."""

    def baseline(self, *lines):
        p = self.tmp / "baseline.txt"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def run_fixture(self, fixture, *args, text=None):
        self.fake(npx={"text": text, "rc": 1} if text is not None else text_replay(fixture))
        return self.gate("fe", "--tree", self.tree, "--project", "frontend", *args)

    def fixture_text(self, name):
        return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")

    def allow4(self):
        return f"unhandled 4 {ROUTES_SPEC} :: {SERVER_NOW}"

    def test_no_baseline_says_every_test_ran_not_that_tests_were_cut_off(self):
        r = self.run_fixture("fe_full_unhandled")
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn(" INCOMPLETE ", r.first)
        self.assertIn("every test ran", r.stdout)
        self.assertNotIn("never ran", r.stdout)
        self.assertIn("server_now", r.stdout)

    def test_a_cut_off_run_still_says_tests_never_ran(self):
        head = self.fixture_text("fe_full_unhandled").split(" Test Files")[0]
        r = self.run_fixture("fe_full_unhandled", text=head)
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn("never ran", r.stdout)

    def test_exactly_the_listed_unhandled_errors_is_red_known(self):
        r = self.run_fixture("fe_full_unhandled", "--known-red", self.baseline(self.allow4()))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" RED-KNOWN ", r.first)
        kr = self.summary_of(r)["known_red"]
        self.assertEqual(kr["unhandled"], 4)
        self.assertEqual(kr["unhandled_absent"], [])
        self.assertIn("既存 unhandled: 4", r.stdout)

    def test_listed_unhandled_and_listed_failure_is_red_known(self):
        r = self.run_fixture("fe_full_unhandled_red", "--known-red", self.baseline(self.allow4(), CHARACTERS_TEST))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" RED-KNOWN ", r.first)

    def test_listed_unhandled_cannot_absorb_a_new_failure(self):
        r = self.run_fixture("fe_full_unhandled_red", "--known-red", self.baseline(self.allow4()))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" RED ", r.first)
        self.assertEqual(self.summary_of(r)["known_red"]["new"], [CHARACTERS_TEST])

    def test_listed_failure_alone_cannot_absorb_unhandled_errors(self):
        r = self.run_fixture("fe_full_unhandled_red", "--known-red", self.baseline(CHARACTERS_TEST))
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn(" INCOMPLETE ", r.first)

    def test_more_errors_than_listed_is_incomplete(self):
        r = self.run_fixture("fe_full_unhandled", "--known-red",
                             self.baseline(f"unhandled 3 {ROUTES_SPEC} :: {SERVER_NOW}"))
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn("exactly 3", r.stdout)

    def test_fewer_errors_than_listed_is_incomplete(self):
        r = self.run_fixture("fe_full_unhandled", "--known-red",
                             self.baseline(f"unhandled 5 {ROUTES_SPEC} :: {SERVER_NOW}"))
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn("exactly 5", r.stdout)

    def test_same_count_from_another_spec_is_incomplete(self):
        text = self.fixture_text("fe_full_unhandled").replace(
            f'originated in "{ROUTES_SPEC}"', 'originated in "projects/frontend/src/app/other.spec.ts"', 1)
        r = self.run_fixture("fe_full_unhandled", "--known-red", self.baseline(self.allow4()), text=text)
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn("other.spec.ts", r.stdout)

    def test_same_count_with_another_message_is_incomplete(self):
        text = self.fixture_text("fe_full_unhandled").replace(SERVER_NOW, "TypeError: a different crash", 1)
        r = self.run_fixture("fe_full_unhandled", "--known-red", self.baseline(self.allow4()), text=text)
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn("a different crash", r.stdout)

    def test_summary_count_disagreeing_with_parsed_blocks_is_incomplete(self):
        text = re.sub(r"(Errors\s+(?:\x1b\[[0-9;]*m)*)4 errors", r"\g<1>5 errors", self.fixture_text("fe_full_unhandled"))
        self.assertNotEqual(text, self.fixture_text("fe_full_unhandled"))
        r = self.run_fixture("fe_full_unhandled", "--known-red", self.baseline(self.allow4()), text=text)
        self.assertEqual(r.returncode, 4, r.stdout)

    def test_listed_unhandled_never_satisfies_expect_red(self):
        r = self.run_fixture("fe_full_unhandled", "--known-red", self.baseline(self.allow4()), "--expect", "red")
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_zero_errors_with_a_listed_entry_is_green_and_reports_it_absent(self):
        r = self.gate_green_with(self.baseline(self.allow4()))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" GREEN ", r.first)
        self.assertIn("名單內但這次沒出現", r.stdout)

    def gate_green_with(self, baseline):
        self.fake(npx=text_replay("fe_green"))
        return self.gate("fe", "--tree", self.tree, "--project", "frontend", "--known-red", baseline)

    def test_malformed_unhandled_line_is_a_tool_error(self):
        for bad in ("unhandled x spec.ts :: boom", f"unhandled 4 {ROUTES_SPEC}", "unhandled 0 a.spec.ts :: boom"):
            with self.subTest(bad):
                r = self.run_fixture("fe_full_unhandled", "--known-red", self.baseline(bad))
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


class KnownRedAutoloadTest(Harness):
    def write_tree_list(self, rel, *lines):
        p = self.tree / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# why this is on the list\n" + "\n".join(lines) + "\n", encoding="utf-8")
        return p

    def test_whole_frontend_run_reads_the_list_in_the_tree_and_says_so(self):
        p = self.write_tree_list("angular-workspace/known-red-frontend.txt", f"unhandled 4 {ROUTES_SPEC} :: {SERVER_NOW}")
        self.fake(npx=text_replay("fe_full_unhandled"))
        r = self.gate("fe", "--tree", self.tree, "--project", "frontend")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" RED-KNOWN ", r.first)
        self.assertIn(f"known-red: {p}", r.stdout)
        self.assertEqual(self.summary_of(r)["known_red_file"], str(p))

    def test_each_project_only_reads_its_own_list(self):
        self.write_tree_list("angular-workspace/known-red-frontend.txt", f"unhandled 4 {ROUTES_SPEC} :: {SERVER_NOW}")
        self.fake(npx=text_replay("fe_full_unhandled"))
        r = self.gate("fe", "--tree", self.tree, "--project", "admin")
        self.assertEqual(r.returncode, 4, r.stdout)

    def test_no_known_red_flag_turns_the_list_off(self):
        self.write_tree_list("angular-workspace/known-red-frontend.txt", f"unhandled 4 {ROUTES_SPEC} :: {SERVER_NOW}")
        self.fake(npx=text_replay("fe_full_unhandled"))
        r = self.gate("fe", "--tree", self.tree, "--project", "frontend", "--no-known-red")
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIsNone(self.summary_of(r)["known_red_file"])

    def test_explicit_list_replaces_the_one_in_the_tree(self):
        self.write_tree_list("angular-workspace/known-red-frontend.txt", f"unhandled 4 {ROUTES_SPEC} :: {SERVER_NOW}")
        other = self.tmp / "other.txt"
        other.write_text("SomeOtherTest\n", encoding="utf-8")
        self.fake(npx=text_replay("fe_full_unhandled"))
        r = self.gate("fe", "--tree", self.tree, "--project", "frontend", "--known-red", other)
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertEqual(self.summary_of(r)["known_red_file"], str(other))

    def test_a_narrow_run_does_not_report_list_entries_as_absent(self):
        self.write_tree_list("angular-workspace/known-red-frontend.txt", f"unhandled 4 {ROUTES_SPEC} :: {SERVER_NOW}", CHARACTERS_TEST)
        self.fake(npx=text_replay("fe_green"))
        r = self.gate("fe", "--tree", self.tree, "--project", "frontend", "--include", FE_SPEC)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" GREEN ", r.first)
        self.assertNotIn("名單內但這次沒出現", r.stdout)

    def test_a_whole_run_reports_list_entries_as_absent(self):
        self.write_tree_list("angular-workspace/known-red-frontend.txt", CHARACTERS_TEST)
        self.fake(npx=text_replay("fe_green"))
        r = self.gate("fe", "--tree", self.tree, "--project", "frontend")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("名單內但這次沒出現", r.stdout)

    def test_go_integration_reads_the_backend_list(self):
        self.write_tree_list("backend-golang/known-red-integration.txt", "TestZZFail", "TestZZSub/bad_one")
        self.fake(go=go_replay("go_red"))
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--integ")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" RED-KNOWN ", r.first)

    def test_go_unit_run_never_reads_the_integration_list(self):
        self.write_tree_list("backend-golang/known-red-integration.txt", "TestZZFail", "TestZZSub/bad_one")
        self.fake(go=go_replay("go_red"))
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" RED ", r.first)

    def test_empty_list_in_the_tree_is_a_tool_error(self):
        self.write_tree_list("angular-workspace/known-red-frontend.txt")
        self.fake(npx=text_replay("fe_green"))
        r = self.gate("fe", "--tree", self.tree, "--project", "frontend")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


class FullNameKnownRedInGoTest(Harness):
    """A list line absorbs its exact full name; only an explicit `TestX/*` reaches further."""

    def run_with(self, fixture, *names, pkg=LOGGER):
        p = self.tmp / "known.txt"
        p.write_text("\n".join(names) + "\n", encoding="utf-8")
        self.fake(go=go_replay(fixture))
        return self.gate("go", "--tree", self.tree, "--pkg", pkg, "--known-red", p)

    def test_listed_parent_does_not_absorb_a_new_subtest(self):
        r = self.run_with("go_new_subtest", "TestParent")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" RED ", r.first)
        self.assertEqual(sorted(self.summary_of(r)["known_red"]["new"]), ["TestParent/new_regression", "TestParent/old"])

    def test_listed_subtest_leaves_its_sibling_red(self):
        r = self.run_with("go_new_subtest", "TestParent/old")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(self.summary_of(r)["known_red"]["new"], ["TestParent/new_regression"])

    def test_star_absorbs_every_subtest_when_written_out(self):
        r = self.run_with("go_new_subtest", "TestParent/*")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" RED-KNOWN ", r.first)
        self.assertEqual(sorted(self.summary_of(r)["known_red"]["existing"]), ["TestParent/new_regression", "TestParent/old"])

    def test_star_also_absorbs_the_parents_own_failure(self):
        r = self.run_with("go_parent_own_fail", "TestOwn/*", "TestLogged/*", "TestRollup/*")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("TestOwn", self.summary_of(r)["known_red"]["existing"])

    def test_star_does_not_reach_a_name_that_only_shares_the_prefix(self):
        r = self.run_with("go_new_subtest", "TestPar/*")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(self.summary_of(r)["known_red"]["existing"], [])

    def test_absent_and_passed_understand_the_star(self):
        r = self.run_with("go_red", "TestZZFail", "TestZZSub/*", "TestGone/*", "TestZZSub/ok/*")
        kr = self.summary_of(r)["known_red"]
        self.assertEqual(kr["absent"], ["TestGone/*"])
        self.assertEqual(kr["passed"], ["TestZZSub/ok/*"])
        self.assertEqual(kr["new"], [])

    def test_a_star_with_a_passing_and_a_failing_subtest_is_neither_passed_nor_absent(self):
        kr = self.summary_of(self.run_with("go_red", "TestZZFail", "TestZZSub/*"))["known_red"]
        self.assertEqual(kr["passed"], [])
        self.assertEqual(kr["absent"], [])


class FullNameKnownRedInFeTest(Harness):
    NAME = "projects/frontend/src/app/zz-probe-fe_red.spec.ts > zz probe > fails one"

    def run_with(self, *names):
        p = self.tmp / "known.txt"
        p.write_text("\n".join(names) + "\n", encoding="utf-8")
        self.fake(npx=text_replay("fe_red"))
        return self.gate("fe", "--tree", self.tree, "--project", "frontend", "--known-red", p)

    def test_a_path_prefix_absorbs_nothing(self):
        for line in ("projects", "projects/frontend/src/app", "projects/frontend/src/app/zz-probe-fe_red.spec.ts",
                     "projects/*", "projects/frontend/src/app/*"):
            with self.subTest(line):
                r = self.run_with(line)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn(" RED ", r.first)
                self.assertEqual(self.summary_of(r)["known_red"]["existing"], [])

    def test_one_full_name_absorbs_only_itself(self):
        r = self.run_with(self.NAME)
        self.assertEqual(r.returncode, 1, r.stdout)
        kr = self.summary_of(r)["known_red"]
        self.assertEqual(kr["existing"], [self.NAME])
        self.assertEqual(len(kr["new"]), 1)


class ZeroTestsBeatKnownRedTest(Harness):
    def known(self, *names):
        p = self.tmp / "known.txt"
        p.write_text("\n".join(names) + "\n", encoding="utf-8")
        return p

    def test_package_with_zero_tests_is_no_tests_even_when_every_red_is_listed(self):
        self.fake(go=go_replay("go_known_and_empty_pkg"))
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--pkg", "./internal/tag", "--known-red", self.known("TestListed"))
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn(" NO-TESTS ", r.first)
        self.assertIn("./internal/tag", r.stdout)

    def test_unmatched_run_is_no_tests_even_when_every_red_is_listed(self):
        self.fake(go=go_replay("go_known_run_partial"))
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--run", "TestListed", "--run", "TestMissingRegression",
                      "--known-red", self.known("TestListed"))
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn(" NO-TESTS ", r.first)
        self.assertEqual(self.summary_of(r)["run_unmatched"], ["TestMissingRegression"])

    def test_a_real_new_red_stays_red(self):
        self.fake(go=go_replay("go_known_and_empty_pkg"))
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--pkg", "./internal/tag", "--known-red", self.known("TestOther"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" RED ", r.first)

    def test_expect_red_does_not_turn_it_into_a_pass(self):
        self.fake(go=go_replay("go_known_and_empty_pkg"))
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--pkg", "./internal/tag", "--known-red", self.known("TestListed"),
                      "--expect", "red")
        self.assertEqual(r.returncode, 3, r.stdout)


class SkippedRunPatternTest(Harness):
    def go(self, fixture, *patterns):
        self.fake(go=go_replay(fixture))
        args = []
        for p in patterns:
            args += ["--run", p]
        return self.gate("go", "--tree", self.tree, "--pkg", LOGGER, *args)

    def test_pattern_that_only_hits_a_skipped_test_is_no_tests(self):
        for fixture, patterns, unmatched in (
                ("go_run_only_skip", ["^TestTarget$"], ["^TestTarget$"]),
                ("go_run_skip_and_plain", ["^TestTarget$", "^TestPlain$"], ["^TestTarget$"]),
                ("go_run_sub_skip", ["^TestMixed$/^a$"], ["^TestMixed$/^a$"])):
            with self.subTest(fixture):
                r = self.go(fixture, *patterns)
                self.assertEqual(r.returncode, 3, r.stdout)
                self.assertIn(" NO-TESTS ", r.first)
                self.assertIn("只命中被略過的測試", r.stdout)
                self.assertEqual(self.summary_of(r)["run_unmatched"], unmatched)

    def test_naming_a_skipped_test_does_not_count_because_a_longer_name_also_matched(self):
        r = self.go("go_run_skip_named", "TestTarget")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn(" NO-TESTS ", r.first)
        self.assertIn("TestTarget", self.summary_of(r)["no_tests"][0])

    def test_a_parent_whose_subtests_were_all_skipped_ran_nothing(self):
        r = self.go("go_run_hollow_parent", "^TestHollow$")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("只命中被略過的測試", r.stdout)

    def test_a_pattern_with_one_skipped_subtest_and_one_run_is_green(self):
        r = self.go("go_run_mixed_subs", "^TestMixed$")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.first, r" GREEN pass=2 fail=0 skip=1 ")

    def test_the_longer_name_itself_is_green(self):
        r = self.go("go_run_skip_named", "TestTargetB")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" GREEN ", r.first)

    def test_whole_package_gate_with_some_skips_is_unchanged(self):
        r = self.go("go_skip_partial_whole")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.first, r" GREEN pass=1 fail=0 skip=1 ")


class ParentOwnFailureTest(Harness):
    def go(self, *names, text=None):
        args = []
        if names:
            p = self.tmp / "known.txt"
            p.write_text("\n".join(names) + "\n", encoding="utf-8")
            args = ["--known-red", p]
        self.fake(go={"text": text, "rc": 1} if text is not None else go_replay("go_parent_own_fail"))
        return self.gate("go", "--tree", self.tree, "--pkg", LOGGER, *args)

    def test_a_listed_subtest_does_not_hide_the_parents_own_failure(self):
        r = self.go("TestOwn/old", "TestLogged", "TestLogged/old", "TestRollup/old")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" RED ", r.first)
        self.assertEqual(self.summary_of(r)["known_red"]["new"], ["TestOwn"])
        self.assertIn("the parent body failed on its own", r.stdout)

    def test_failed_names_keep_rollups_out_and_own_failures_in(self):
        s = self.summary_of(self.go())
        self.assertEqual(sorted(s["failed"]), ["TestLogged", "TestLogged/old", "TestOwn", "TestOwn/old", "TestRollup/old"])
        self.assertEqual(s["fail"], 5)

    def test_toolchains_without_output_types_still_see_it(self):
        rows = [json.loads(x) for x in (FIXTURES / "go_parent_own_fail.jsonl").read_text(encoding="utf-8").splitlines()]
        for row in rows:
            row.pop("OutputType", None)
        s = self.summary_of(self.go(text="\n".join(json.dumps(x) for x in rows) + "\n"))
        self.assertEqual(sorted(s["failed"]), ["TestLogged", "TestLogged/old", "TestOwn", "TestOwn/old", "TestRollup/old"])


    def test_a_tagged_failure_without_a_go_file_name_still_counts(self):
        text = (FIXTURES / "go_parent_own_fail.jsonl").read_text(encoding="utf-8").replace("z_test.go:4:", "<autogenerated>:1:")
        self.assertIn("<autogenerated>:1: the parent", text)
        self.assertIn("TestOwn", self.summary_of(self.go(text=text))["failed"])


class RunPipeTest(Harness):
    def test_pipe_in_run_is_a_tool_error_naming_the_repeatable_flag(self):
        self.fake(go=go_replay("go_green"))
        for args in (["--run", "TestA|TestB"], ["--run=TestA|TestB"], ["--run", "TestOK", "--run", "TestX/(a|b)"]):
            with self.subTest(args):
                out = self.fresh_out()
                r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--out", out, *args)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("GATE TOOL-ERROR", r.first)
                self.assertIn("--run A --run B", r.stdout)
                self.assertEqual(json.loads(pathlib.Path(str(out) + ".summary.json").read_text(encoding="utf-8"))["verdict"], "TOOL-ERROR")
        self.assertEqual(self.calls("go"), [])


class GoTagsTest(Harness):
    def test_tags_reach_vet_and_build(self):
        self.fake(go={"text": "", "rc": 0})
        for flag, sub in (("--vet", "vet"), ("--build", "build")):
            with self.subTest(flag):
                r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, flag, "--tags", "smoke")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                argv = self.calls("go")[-1]["argv"]
                self.assertEqual(argv[0], sub)
                self.assertEqual(argv[argv.index("-tags") + 1], "smoke")

    def test_other_combinations_are_tool_errors(self):
        self.fake(go={"text": "", "rc": 0})
        for args in (["--tags", "smoke"],
                     ["--tags", "smoke", "--integ"],
                     ["--tags", "smoke", "--all"],
                     ["--vet", "--tags", "smoke", "--integ"],
                     ["--build", "--tags", "smoke", "--tags-off"],
                     ["--vet", "--tags="]):
            with self.subTest(args):
                r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, *args)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("--tags", r.stdout)
        self.assertEqual(self.calls("go"), [])


class KnownRedFlagAndMessageTest(Harness):
    def baseline(self, line):
        p = self.tmp / "baseline.txt"
        p.write_text(line + "\n", encoding="utf-8")
        return p

    def test_both_known_red_flags_are_a_tool_error(self):
        p = self.baseline("TestZZFail")
        self.fake(go=go_replay("go_red"))
        out = self.fresh_out()
        r = self.gate("go", "--tree", self.tree, "--pkg", LOGGER, "--known-red", p, "--no-known-red", "--out", out)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("--no-known-red", r.stdout)
        self.assertEqual(self.calls("go"), [])
        self.assertTrue(pathlib.Path(str(out) + ".summary.json").exists())

    def test_unhandled_message_must_equal_the_whole_line(self):
        spec = "projects/frontend/src/app/zz-probe-fe_unhandled.spec.ts"
        self.fake(npx=text_replay("fe_unhandled"))
        for line, code in ((f"unhandled 1 {spec} :: Error", 4),
                           (f"unhandled 1 {spec} :: late boom", 4),
                           (f"unhandled 1 {spec} :: Error: late boom", 0)):
            with self.subTest(line):
                r = self.gate("fe", "--tree", self.tree, "--project", "frontend", "--known-red", self.baseline(line))
                self.assertEqual(r.returncode, code, r.stdout)


class PackagesSummaryTest(Harness):
    def go(self, fixture, *args):
        (self.tree / "backend-golang/go.mod").write_text("module deepweave/backend-golang\n\ngo 1.22\n", encoding="utf-8")
        self.fake(go=go_replay(fixture))
        return self.gate("go", "--tree", self.tree, *args)

    def test_counts_are_pass_plus_fail_per_package_relative_to_the_module(self):
        r = self.go("go_packages")
        s = self.summary_of(r)
        self.assertEqual(r.returncode, 0, r.stdout)
        got = s["packages"]
        self.assertEqual(got["."], 1)
        self.assertEqual(got["./internal/logger"], 4)
        for empty in ("./internal/tag", "./internal/skiponly", "./internal/tmain"):
            self.assertEqual(got.get(empty, 0), 0, empty)
        self.assertEqual(sum(got.values()), s["pass"] + s["fail"])

    def test_failures_count_and_rollups_do_not(self):
        s = self.summary_of(self.go("go_red", "--pkg", LOGGER))
        self.assertEqual(s["packages"], {"./internal/logger": s["pass"] + s["fail"]})

    def test_module_line_with_a_comment_is_understood(self):
        (self.tree / "backend-golang/go.mod").write_text('module "deepweave/backend-golang" // quoted\n', encoding="utf-8")
        self.fake(go=go_replay("go_red"))
        s = self.summary_of(self.gate("go", "--tree", self.tree, "--pkg", LOGGER))
        self.assertIn("./internal/logger", s["packages"])

    def test_without_a_module_line_no_relative_key_can_be_read_as_covered(self):
        self.fake(go=go_replay("go_red"))
        s = self.summary_of(self.gate("go", "--tree", self.tree, "--pkg", LOGGER))
        self.assertEqual(s["packages"].get("./internal/logger", 0), 0)

    def test_only_go_test_runs_fill_it(self):
        self.fake(go={"text": "", "rc": 0}, npx=text_replay("fe_green"))
        for args in (["go", "--tree", self.tree, "--pkg", LOGGER, "--build"],
                     ["go", "--tree", self.tree, "--pkg", LOGGER, "--vet"],
                     ["fe", "--tree", self.tree, "--project", "frontend"]):
            with self.subTest(args[-1]):
                self.assertIsNone(self.summary_of(self.gate(*args)).get("packages"))


@unittest.skipUnless(shutil.which("go"), "real go toolchain required")
class RealGoVerdictTest(Harness):
    """The same symptoms the review reproduced, against the real go tool instead of a replay."""

    def setUp(self):
        super().setUp()
        self.env["PATH"] = os.environ["PATH"]
        self.env.pop("GOROOT", None)
        self.write("go.mod", "module m\n\ngo 1.22\n")
        self.write("internal/logger/z_test.go", """package logger

import "testing"

func TestParent(t *testing.T) {
	t.Run("old", func(t *testing.T) { t.Fatal("known") })
	t.Run("new_regression", func(t *testing.T) { t.Fatal("new") })
}
func TestOwn(t *testing.T) {
	t.Error("own")
	t.Run("old", func(t *testing.T) { t.Fatal("known") })
}
func TestTarget(t *testing.T) { t.Skip("no") }
func TestTargetB(t *testing.T) {}
func TestPlain(t *testing.T) {}
""")
        self.write("internal/tag/tag.go", "package tag\n")

    def write(self, rel, text):
        p = self.tree / "backend-golang" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def go(self, *args, known=None):
        extra = []
        if known:
            k = self.fresh_out("known.txt")
            k.write_text(known + "\n", encoding="utf-8")
            extra = ["--known-red", k]
        return self.gate("go", "--tree", self.tree, *args, *extra, timeout=180)

    def test_listed_parent_does_not_absorb_a_new_subtest(self):
        r = self.go("--pkg", LOGGER, "--run", "TestParent", known="TestParent")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" RED ", r.first)

    def test_star_with_a_zero_test_package_is_no_tests(self):
        r = self.go("--pkg", LOGGER, "--pkg", "./internal/tag", "--run", "TestParent", known="TestParent/*")
        self.assertEqual(r.returncode, 3, r.stdout)

    def test_run_on_skipped_tests_is_no_tests(self):
        self.assertEqual(self.go("--pkg", LOGGER, "--run", "TestTarget").returncode, 3)
        self.assertEqual(self.go("--pkg", LOGGER, "--run", "^TestTarget$", "--run", "^TestPlain$").returncode, 3)

    def test_parent_with_its_own_failure_is_red(self):
        r = self.go("--pkg", LOGGER, "--run", "TestOwn", known="TestOwn/old")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(self.summary_of(r)["known_red"]["new"], ["TestOwn"])

    def test_packages_on_a_wildcard(self):
        s = self.summary_of(self.go("--pkg", "./internal/...", "--run", "TestPlain"))
        self.assertEqual(s["packages"]["./internal/logger"], 1)
        self.assertEqual(s["packages"].get("./internal/tag", 0), 0)

    def test_tags_make_vet_compile_a_tagged_file(self):
        self.write("internal/tag/smoke.go", "//go:build smoke\n\npackage tag\n\nconst X = !!!\n")
        self.assertEqual(self.go("--pkg", "./internal/tag", "--vet").returncode, 0)
        r = self.go("--pkg", "./internal/tag", "--vet", "--tags", "smoke")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" BUILD-FAIL ", r.first)
        r = self.go("--pkg", "./internal/tag", "--build", "--tags", "smoke")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(" BUILD-FAIL ", r.first)


class FixtureSanityTest(unittest.TestCase):
    def test_every_fixture_has_an_exit_code_and_no_host_paths(self):
        for p in FIXTURES.iterdir():
            if p.suffix == ".rc":
                continue
            fixture_rc(p.stem)
            self.assertNotIn("/Users/huntwu/Desktop", p.read_text(encoding="utf-8"), p.name)


if __name__ == "__main__":
    unittest.main()
