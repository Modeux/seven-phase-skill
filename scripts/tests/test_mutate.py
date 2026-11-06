# -*- coding: utf-8 -*-
"""mutate: manifest parsing, verdict mapping, overlay vs in-tree, and the restore guarantee under real kills.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_mutate.py' -v
"""
import hashlib
import json
import os
import pathlib
import shutil
import signal
import stat
import subprocess
import sys
import textwrap
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _gate_fakes import GATE, MUTATE, FIXTURES, Harness, go_replay, text_replay  # noqa: E402

FE_REL = "angular-workspace/projects/frontend/src/app/x.ts"
GO_REL = "backend-golang/internal/logger/x.go"
SQL_REL = "backend-golang/internal/logger/q.sql"
PY_REL = "ai-service/app/x.py"
FE_TEST = "fe --project frontend --include projects/frontend/src/app/site-content.spec.ts"
GO_TEST = "go --pkg ./internal/logger"


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def wait_until(cond, seconds=15):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


class MutateHarness(Harness):
    def setUp(self):
        super().setUp()
        self.main = self.tmp / "main"
        self.main.mkdir()
        self.make_layout(self.main)
        (self.main / FE_REL).write_text("const v = 'ORIGINAL';\nconst w = 'twin';\nconst u = 'twin';\n", encoding="utf-8")
        (self.main / GO_REL).write_text("package logger\n\nconst V = \"ORIGINAL\"\n", encoding="utf-8")
        (self.main / SQL_REL).write_text("select 'ORIGINAL';\n", encoding="utf-8")
        (self.main / PY_REL).write_text("V = 'ORIGINAL'\n", encoding="utf-8")
        (self.main / ".gitignore").write_text("node_modules/\ntmp/\n", encoding="utf-8")
        self.git(self.main, "init", "-q", "-b", "main")
        self.git(self.main, "add", "-A")
        self.git(self.main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
        self.wt = self.tmp / "wt"
        self.git(self.main, "worktree", "add", "-q", "--detach", str(self.wt))
        self.make_layout(self.wt)
        self.fe_file = self.wt / FE_REL
        self.go_file = self.wt / GO_REL
        self.orig_fe = sha(self.fe_file)
        self.orig_go = sha(self.go_file)
        self.out = self.fresh_out()
        self.fake_fe_on("MUTANT", "fe_red")
        self.fake(go={"when": [{"path": str(self.go_file), "contains": "MUTANT", "use": go_replay("go_red")}], **go_replay("go_green")})

    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout

    def fake_fe_on(self, needle, fixture, tree=None, **extra):
        path = str((tree or self.wt) / FE_REL)
        self.fake(npx={"when": [{"path": path, "contains": needle, "use": {**text_replay(fixture), **extra}}], **text_replay("fe_green")})

    def manifest(self, body):
        p = self.fresh_out("m.txt")
        p.write_text(textwrap.dedent(body), encoding="utf-8")
        return p

    def fe_block(self, name="M-a", old="'ORIGINAL'", new="'MUTANT'", path=FE_REL, test=FE_TEST):
        return f"=== {name} flips the value\ntest: {test}\nfile: {path}\n<<<<<<< old\n{old}\n=======\n{new}\n>>>>>>> new\n"

    def go_block(self, name="G-a", old='"ORIGINAL"', new='"MUTANT"', path=GO_REL, test=GO_TEST):
        return f"=== {name} flips the go value\ntest: {test}\nfile: {path}\n<<<<<<< old\n{old}\n=======\n{new}\n>>>>>>> new\n"

    def mutate(self, manifest, *args, tree=None, out=None, timeout=60):
        r = self.run_tool(MUTATE, "--tree", tree or self.wt, "--manifest", manifest, "--out", out or self.out, *args, timeout=timeout)
        return r

    def spawn(self, manifest, *args, out=None):
        p = subprocess.Popen([sys.executable, str(MUTATE), "--tree", str(self.wt), "--manifest", str(manifest), "--out", str(out or self.out), *args],
                             env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(p.stdout.close)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        return p

    def kill_fake_children(self):
        for c in self.calls():
            if alive(c["pid"]):
                os.kill(c["pid"], signal.SIGKILL)

    def assert_clean_tree(self):
        self.assertEqual(self.git(self.wt, "status", "--porcelain"), "")


class ManifestTest(MutateHarness):
    def test_text_blocks_defaults_and_multifile(self):
        import mutate
        p = self.manifest(f"""\
            # comment
            test: {FE_TEST}

            === M-a first one
            file: a.ts
            <<<<<<< old
            line one
            line two
            =======
            new one
            >>>>>>> new
            file: b.ts
            <<<<<<< old
            x
            =======
            >>>>>>> new

            === M-b second
            test: {GO_TEST}
            file: c.go
            <<<<<<< old
            y
            =======
            z
            >>>>>>> new
            """)
        ms = mutate.load_manifest(p)
        self.assertEqual([m.name for m in ms], ["M-a", "M-b"])
        self.assertEqual(ms[0].desc, "first one")
        self.assertEqual(ms[0].test[0], "fe")
        self.assertEqual(ms[1].test, GO_TEST.split())
        self.assertEqual([(h.path, h.old, h.new) for h in ms[0].hunks],
                         [("a.ts", "line one\nline two", "new one"), ("b.ts", "x", "")])

    def test_json_form(self):
        import mutate
        p = self.fresh_out("m.json")
        p.write_text(json.dumps({"test": FE_TEST, "mutations": [
            {"name": "J-a", "desc": "d", "files": [{"path": "a.ts", "old": "=======\n", "new": "x"}]},
            {"name": "J-b", "test": GO_TEST.split(), "files": [{"path": "b.go", "old": "o", "new": "n"}]}]}), encoding="utf-8")
        ms = mutate.load_manifest(p)
        self.assertEqual(ms[0].hunks[0].old, "=======\n")
        self.assertEqual(ms[1].test, GO_TEST.split())

    def test_malformed_manifests_are_tool_errors_and_touch_nothing(self):
        bad = {
            "empty": "# nothing\n",
            "no test": "=== M-a d\nfile: a.ts\n<<<<<<< old\nx\n=======\ny\n>>>>>>> new\n",
            "no file": f"=== M-a d\ntest: {FE_TEST}\n",
            "unterminated": f"=== M-a d\ntest: {FE_TEST}\nfile: a.ts\n<<<<<<< old\nx\n",
            "no separator": f"=== M-a d\ntest: {FE_TEST}\nfile: a.ts\n<<<<<<< old\nx\n>>>>>>> new\n",
            "stray text": f"hello\n=== M-a d\ntest: {FE_TEST}\nfile: a.ts\n<<<<<<< old\nx\n=======\ny\n>>>>>>> new\n",
            "dup names": self.fe_block() + self.fe_block(),
            "ambiguous separator": f"=== M-a d\ntest: {FE_TEST}\nfile: a.ts\n<<<<<<< old\nx\n=======\ny\n=======\nz\n>>>>>>> new\n",
            "test mode not mutable": self.fe_block(test="fe-build --project frontend"),
            "go build not mutable": self.go_block(test="go --pkg ./internal/logger --build"),
            "test overrides tree": self.fe_block(test=f"fe --tree /x --project frontend"),
            "test overrides out": self.fe_block(test=f"{FE_TEST} --out /x"),
            "test overrides expect": self.fe_block(test=f"{FE_TEST} --expect red"),
            "bad gate args": self.fe_block(test="fe --project nosuch"),
            "file outside tree": self.fe_block(path="../main/" + FE_REL),
        }
        for label, body in bad.items():
            with self.subTest(label):
                p = self.fresh_out("m.txt")
                p.write_text(body, encoding="utf-8")
                r = self.mutate(p)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertEqual(sha(self.fe_file), self.orig_fe)
        self.assertEqual(self.calls(), [])

    def test_missing_manifest_and_tree_are_tool_errors(self):
        self.assertEqual(self.mutate(self.tmp / "nope.txt").returncode, 2)
        m = self.manifest(self.fe_block())
        self.assertEqual(self.mutate(m, tree=self.tmp / "nope").returncode, 2)


class VerdictMappingTest(MutateHarness):
    def test_red_is_killed_and_names_the_killer(self):
        r = self.mutate(self.manifest(self.fe_block()))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        first = r.stdout.splitlines()[0]
        self.assertIn("KILLED=1", first)
        self.assertIn("SURVIVED=0", first)
        self.assertIn("INVALID=0", first)
        self.assertIn(f"out={self.out}", first)
        self.assertRegex(r.stdout, r"M-a\s+\|?\s*KILLED")
        self.assertIn("fails one", r.stdout)
        self.assertEqual(sha(self.fe_file), self.orig_fe)
        self.assert_clean_tree()

    def test_green_is_survived(self):
        r = self.mutate(self.manifest(self.fe_block(old="'twin';\nconst u", new="'twin2';\nconst u")))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("SURVIVED=1", r.stdout.splitlines()[0])
        self.assertEqual(sha(self.fe_file), self.orig_fe)

    def test_non_red_outcomes_are_invalid_never_killed(self):
        for fixture in ("fe_compile_error", "fe_no_tests", "fe_all_skip", "fe_unhandled", "fe_collect_error"):
            with self.subTest(fixture):
                self.fake_fe_on("MUTANT", fixture)
                out = self.fresh_out()
                r = self.mutate(self.manifest(self.fe_block()), out=out)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("INVALID=1", r.stdout.splitlines()[0])
                self.assertIn("KILLED=0", r.stdout.splitlines()[0])
                self.assertEqual(sha(self.fe_file), self.orig_fe)

    def test_killed_child_is_invalid_and_the_file_is_restored(self):
        self.fake_fe_on("MUTANT", "fe_green", sleep=30)
        p = self.spawn(self.manifest(self.fe_block()))
        self.assertTrue(wait_until(lambda: "MUTANT" in self.fe_file.read_text(encoding="utf-8")))
        self.assertTrue(wait_until(lambda: len(self.calls("npx")) >= 2))
        os.kill(self.calls("npx")[-1]["pid"], signal.SIGKILL)
        out, _ = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 2, out)
        self.assertIn("INVALID=1", out.splitlines()[0])
        self.assertEqual(sha(self.fe_file), self.orig_fe)

    def test_only_known_reds_under_mutation_is_survived(self):
        k = self.fresh_out("k.txt")
        k.write_text("projects/frontend/src/app/zz-probe-fe_red.spec.ts > zz probe > fails one\n"
                     "projects/frontend/src/app/zz-probe-fe_red.spec.ts > zz probe > nested > fails two\n", encoding="utf-8")
        r = self.mutate(self.manifest(self.fe_block(test=f"{FE_TEST} --known-red {k}")))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("SURVIVED=1", r.stdout.splitlines()[0])

    def test_baseline_red_makes_a_kill_meaningless(self):
        self.fake(npx=text_replay("fe_red"))
        r = self.mutate(self.manifest(self.fe_block()))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("INVALID=1", r.stdout.splitlines()[0])
        self.assertIn("baseline", r.stdout.lower())
        self.assertEqual(len(self.calls("npx")), 1)

    def test_baseline_can_be_skipped_explicitly(self):
        self.fake(npx=text_replay("fe_red"))
        r = self.mutate(self.manifest(self.fe_block()), "--no-baseline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.calls("npx")), 1)

    def test_baseline_runs_once_per_distinct_test(self):
        body = self.fe_block("M-a") + self.fe_block("M-b", old="const w", new="const MUTANT")
        r = self.mutate(self.manifest(body))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.calls("npx")), 3)

    def test_exit_code_precedence_invalid_over_survived(self):
        body = (self.fe_block("M-a") + self.fe_block("M-b", old="const w", new="const w2")
                + self.fe_block("M-c", old="not in file", new="x"))
        r = self.mutate(self.manifest(body))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("KILLED=1 SURVIVED=1 INVALID=1", r.stdout.splitlines()[0])


class OldTextRuleTest(MutateHarness):
    def test_zero_or_many_matches_are_invalid_and_do_not_touch_the_file(self):
        for old in ("no such text", "'twin'"):
            with self.subTest(old):
                out = self.fresh_out()
                r = self.mutate(self.manifest(self.fe_block(old=old)), out=out)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("INVALID=1", r.stdout.splitlines()[0])
                self.assertEqual(sha(self.fe_file), self.orig_fe)
        self.assertLessEqual(len(self.calls("npx")), 0)

    def test_missing_file_and_noop_mutation_are_invalid(self):
        self.assertEqual(self.mutate(self.manifest(self.fe_block(path="angular-workspace/nope.ts"))).returncode, 2)
        self.assertEqual(self.mutate(self.manifest(self.fe_block(old="'ORIGINAL'", new="'ORIGINAL'")), out=self.fresh_out()).returncode, 2)

    def test_one_bad_file_in_a_multifile_mutation_touches_neither(self):
        body = self.fe_block() + f"file: {SQL_REL}\n<<<<<<< old\nnot there\n=======\nx\n>>>>>>> new\n"
        sql = self.wt / SQL_REL
        before = sha(sql)
        r = self.mutate(self.manifest(body))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(sha(self.fe_file), self.orig_fe)
        self.assertEqual(sha(sql), before)
        self.assertEqual(self.calls("npx"), [])

    def test_multi_file_mutation_applies_both_and_restores_both(self):
        sql = self.wt / SQL_REL
        before = sha(sql)
        self.fake(npx={"when": [{"path": str(sql), "contains": "SECOND", "use": {"when": [], **text_replay("fe_red")}}], **text_replay("fe_green")})
        body = self.fe_block() + f"file: {SQL_REL}\n<<<<<<< old\n'ORIGINAL'\n=======\n'SECOND'\n>>>>>>> new\n"
        r = self.mutate(self.manifest(body))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sha(self.fe_file), self.orig_fe)
        self.assertEqual(sha(sql), before)

    def test_absolute_path_inside_the_tree_is_accepted(self):
        r = self.mutate(self.manifest(self.fe_block(path=str(self.fe_file))))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_mode_and_non_utf8_bytes_survive_restore(self):
        raw = b"const v = 'ORIGINAL'; // \xff\xfe latin\n"
        self.fe_file.write_bytes(raw)
        self.fe_file.chmod(0o755)
        r = self.mutate(self.manifest(self.fe_block()))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.fe_file.read_bytes(), raw)
        self.assertTrue(stat.S_IMODE(self.fe_file.stat().st_mode) & stat.S_IXUSR)


class OverlayTest(MutateHarness):
    def test_go_mutation_uses_overlay_and_never_writes_the_tree(self):
        mtime = self.go_file.stat().st_mtime_ns
        r = self.mutate(self.manifest(self.go_block()))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.go_file.stat().st_mtime_ns, mtime)
        self.assert_clean_tree()
        argv = self.calls("go")[-1]["argv"]
        replace = json.load(open(argv[argv.index("-overlay") + 1], encoding="utf-8"))["Replace"]
        self.assertEqual(list(replace), [str(self.go_file)])
        self.assertIn("MUTANT", pathlib.Path(replace[str(self.go_file)]).read_text(encoding="utf-8"))
        self.assertFalse(pathlib.Path(str(self.out) + ".journal").exists())

    def test_overlay_survived(self):
        r = self.mutate(self.manifest(self.go_block(old="package logger", new="package logger // x")))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_overlay_works_on_the_main_checkout(self):
        self.fake(go={"when": [{"path": str(self.main / GO_REL), "contains": "MUTANT", "use": go_replay("go_red")}], **go_replay("go_green")})
        r = self.mutate(self.manifest(self.go_block()), tree=self.main)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_test_line_overlay_is_merged(self):
        probe = self.fresh_out("probe.go.src")
        probe.write_text("package logger\n", encoding="utf-8")
        ov = self.fresh_out("o.json")
        key = str(self.wt / "backend-golang/internal/logger/zz_test.go")
        ov.write_text(json.dumps({"Replace": {key: str(probe)}}), encoding="utf-8")
        r = self.mutate(self.manifest(self.go_block(test=f"{GO_TEST} --overlay {ov}")))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        argv = self.calls("go")[-1]["argv"]
        replace = json.load(open(argv[argv.index("-overlay") + 1], encoding="utf-8"))["Replace"]
        self.assertEqual(sorted(replace), sorted([key, str(self.go_file)]))

    def test_test_line_overlay_conflict_is_invalid(self):
        ov = self.fresh_out("o.json")
        ov.write_text(json.dumps({"Replace": {str(self.go_file): "/x"}}), encoding="utf-8")
        r = self.mutate(self.manifest(self.go_block(test=f"{GO_TEST} --overlay {ov}")))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_non_go_file_with_go_test_goes_in_tree(self):
        sql = self.wt / SQL_REL
        before = sha(sql)
        self.fake(go={"when": [{"path": str(sql), "contains": "MUTANT", "use": go_replay("go_red")}], **go_replay("go_green")})
        r = self.mutate(self.manifest(self.go_block(path=SQL_REL, old="'ORIGINAL'", new="'MUTANT'")))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("-overlay", self.calls("go")[-1]["argv"])
        self.assertEqual(sha(sql), before)

    def test_go_file_edited_during_the_run_is_invalid(self):
        self.fake(go={"when": [{"path": str(self.go_file), "contains": "MUTANT", "use": {**go_replay("go_red"), "sleep": 3}}], **go_replay("go_green")})
        p = self.spawn(self.manifest(self.go_block()))
        self.assertTrue(wait_until(lambda: len(self.calls("go")) >= 2))
        self.go_file.write_text("package logger\n\nconst V = \"HUMAN EDIT\"\n", encoding="utf-8")
        out, _ = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 2, out)
        self.assertIn("INVALID=1", out.splitlines()[0])
        self.assertIn("HUMAN EDIT", self.go_file.read_text(encoding="utf-8"))


@unittest.skipUnless(shutil.which("go"), "real go toolchain required")
class RealGoOverlayTest(MutateHarness):
    """INVARIANT coverage that a fake cannot give: the overlay keys must be what the real go tool resolves."""

    def setUp(self):
        super().setUp()
        self.env["PATH"] = os.environ["PATH"]
        be = self.wt / "backend-golang"
        (be / "go.mod").write_text("module m\n\ngo 1.23\n", encoding="utf-8")
        (be / "internal/logger/x_test.go").write_text(
            'package logger\n\nimport "testing"\n\nfunc TestV(t *testing.T) {\n\tif V != "ORIGINAL" {\n\t\tt.Fatalf("V=%s", V)\n\t}\n}\n', encoding="utf-8")
        self.git(self.wt, "add", "-A")
        self.git(self.wt, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "gomod")

    def test_killed_and_survived_without_touching_the_tree(self):
        body = (self.go_block("G-kill") + self.go_block("G-live", old="package logger", new="package logger // live")
                + self.go_block("G-nocompile", old='"ORIGINAL"', new='ORIGINAL'))
        r = self.mutate(self.manifest(body), timeout=180)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("KILLED=1 SURVIVED=1 INVALID=1", r.stdout.splitlines()[0])
        self.assertRegex(r.stdout, r"G-kill\s+\|?\s*KILLED")
        self.assertRegex(r.stdout, r"G-live\s+\|?\s*SURVIVED")
        self.assertRegex(r.stdout, r"G-nocompile\s+\|?\s*INVALID[^\n]*undefined: ORIGINAL")
        self.assert_clean_tree()

    def test_overlay_applies_when_the_tree_is_reached_through_a_symlink(self):
        link = self.tmp / "link-to-wt"
        link.symlink_to(self.wt)
        r = self.mutate(self.manifest(self.go_block()), tree=link, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_run_gate_green_red_timeout_on_a_real_module(self):
        self.assertEqual(self.gate("go", "--tree", self.wt, "--pkg", "./internal/logger", timeout=120).returncode, 0)
        r = self.gate("go", "--tree", self.wt, "--pkg", "./internal/logger", "--run", "NoSuch", timeout=120)
        self.assertEqual(r.returncode, 3, r.stdout)


class MainCheckoutTest(MutateHarness):
    def test_in_tree_mutation_on_main_is_refused_without_flag(self):
        self.fake_fe_on("MUTANT", "fe_red", tree=self.main)
        r = self.mutate(self.manifest(self.fe_block()), tree=self.main)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("--allow-main", r.stdout + r.stderr)
        self.assertEqual(self.calls(), [])

    def test_allow_main_runs_and_restores(self):
        self.fake_fe_on("MUTANT", "fe_red", tree=self.main)
        before = sha(self.main / FE_REL)
        r = self.mutate(self.manifest(self.fe_block()), "--allow-main", tree=self.main)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sha(self.main / FE_REL), before)

    def test_not_a_git_toplevel_is_a_tool_error(self):
        r = self.mutate(self.manifest(self.fe_block()), tree=self.tree)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        r = self.mutate(self.manifest(self.fe_block()), tree=self.wt / "backend-golang")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


class RestoreGuaranteeTest(MutateHarness):
    def sleeping_fe(self):
        self.fake_fe_on("MUTANT", "fe_green", sleep=60)

    def mutated(self):
        return "MUTANT" in self.fe_file.read_text(encoding="utf-8")

    def start_and_wait_mutated(self, *args, out=None):
        self.sleeping_fe()
        started = len(self.calls("npx"))
        p = self.spawn(self.manifest(self.fe_block()), *args, out=out)
        self.assertTrue(wait_until(self.mutated), "never mutated")
        # WHY: baseline plus the mutated run; killing before the child logs itself would orphan it past cleanup.
        self.assertTrue(wait_until(lambda: len(self.calls("npx")) >= started + 2), "test child never started")
        self.addCleanup(self.kill_fake_children)
        return p

    def test_journal_holds_the_original_before_the_tree_changes(self):
        p = self.start_and_wait_mutated()
        journal = pathlib.Path(str(self.out) + ".journal")
        data = json.loads((journal / "journal.json").read_text(encoding="utf-8"))
        entry = data["entries"][0]
        self.assertEqual(entry["path"], FE_REL)
        self.assertEqual(entry["sha_orig"], self.orig_fe)
        self.assertEqual(sha(journal / entry["backup"]), self.orig_fe)
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=30)

    def test_sigterm_and_sigint_restore_and_clear_the_journal(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(sig=sig):
                out = self.fresh_out()
                p = self.start_and_wait_mutated(out=out)
                self.assertTrue(wait_until(lambda: self.calls("npx") and alive(self.calls("npx")[-1]["pid"])))
                time.sleep(0.3)
                child = self.calls("npx")[-1]["pid"]
                p.send_signal(sig)
                text, _ = p.communicate(timeout=30)
                self.assertEqual(p.returncode, 2, text)
                self.assertEqual(sha(self.fe_file), self.orig_fe)
                self.assertFalse(pathlib.Path(str(out) + ".journal").exists())
                self.assertTrue(wait_until(lambda: not alive(child)), "test child survived")
                self.assert_clean_tree()
                # INVARIANT: a clean interrupt leaves nothing to recover, so the next run may start.
                self.fake_fe_on("MUTANT", "fe_red")
                self.assertEqual(self.mutate(self.manifest(self.fe_block()), out=self.fresh_out()).returncode, 0)

    def test_sigkill_leaves_a_journal_that_blocks_new_runs_until_recover(self):
        p = self.start_and_wait_mutated()
        p.kill()
        p.communicate(timeout=30)
        self.kill_fake_children()
        self.assertTrue(self.mutated())
        self.fake_fe_on("MUTANT", "fe_red")
        before_calls = len(self.calls())

        for label, r in (("same out", self.mutate(self.manifest(self.fe_block()))),
                         ("different out", self.mutate(self.manifest(self.fe_block()), out=self.fresh_out())),
                         ("overlay only", self.mutate(self.manifest(self.go_block()), out=self.fresh_out()))):
            with self.subTest(label):
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("--recover", r.stdout + r.stderr)
        self.assertEqual(len(self.calls()), before_calls)
        self.assertTrue(self.mutated())

        r = self.run_tool(MUTATE, "--tree", self.wt, "--recover")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sha(self.fe_file), self.orig_fe)
        self.assert_clean_tree()
        self.assertFalse(pathlib.Path(str(self.out) + ".journal").exists())
        self.assertEqual(self.mutate(self.manifest(self.fe_block()), out=self.fresh_out()).returncode, 0)

    def test_recover_removes_a_temp_file_stranded_by_a_kill_mid_write(self):
        p = self.start_and_wait_mutated()
        p.kill()
        p.communicate(timeout=30)
        self.kill_fake_children()
        stray = self.fe_file.parent / ".mutate-tmp-abc123"
        stray.write_text("half a write", encoding="utf-8")
        r = self.run_tool(MUTATE, "--tree", self.wt, "--recover")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(stray.exists())
        self.assert_clean_tree()

    def test_earlier_summary_at_the_same_out_does_not_outlive_a_kill(self):
        self.mutate(self.manifest(self.fe_block()))
        summary = pathlib.Path(str(self.out) + ".summary.json")
        self.assertTrue(summary.exists())
        p = self.start_and_wait_mutated()
        self.assertFalse(summary.exists())
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=30)

    def test_recover_with_nothing_to_do_is_an_error(self):
        r = self.run_tool(MUTATE, "--tree", self.wt, "--recover")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_recover_when_kill_landed_after_restore_is_a_noop_success(self):
        p = self.start_and_wait_mutated()
        p.kill()
        p.communicate(timeout=30)
        self.kill_fake_children()
        self.fe_file.write_bytes(subprocess.run(["git", "-C", str(self.wt), "show", f"HEAD:{FE_REL}"], capture_output=True, check=True).stdout)
        r = self.run_tool(MUTATE, "--tree", self.wt, "--recover")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sha(self.fe_file), self.orig_fe)

    def test_someone_else_edit_is_not_clobbered(self):
        p = self.start_and_wait_mutated()
        p.kill()
        p.communicate(timeout=30)
        self.kill_fake_children()
        self.fe_file.write_text("human edit\n", encoding="utf-8")
        r = self.run_tool(MUTATE, "--tree", self.wt, "--recover")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(self.fe_file.read_text(encoding="utf-8"), "human edit\n")
        self.assertIn("--force", r.stdout + r.stderr)
        r = self.run_tool(MUTATE, "--tree", self.wt, "--recover", "--force")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sha(self.fe_file), self.orig_fe)

    def test_edit_during_a_live_run_stops_and_keeps_the_journal(self):
        self.fake_fe_on("MUTANT", "fe_green", sleep=3)
        p = self.spawn(self.manifest(self.fe_block() + self.fe_block("M-b", old="const w", new="const w2")))
        self.assertTrue(wait_until(self.mutated))
        self.fe_file.write_text("human edit\n", encoding="utf-8")
        out, _ = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 2, out)
        self.assertEqual(self.fe_file.read_text(encoding="utf-8"), "human edit\n")
        self.assertTrue(pathlib.Path(str(self.out) + ".journal").exists())
        self.assertIn("--recover", out)

    def test_every_other_run_is_refused_while_a_mutation_is_live(self):
        p = self.start_and_wait_mutated()
        r = self.mutate(self.manifest(self.fe_block()), out=self.fresh_out())
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        # WHY: an overlay run reads the rest of the tree, which right now contains someone's mutant.
        r = self.mutate(self.manifest(self.go_block()), out=self.fresh_out())
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        r = self.run_tool(MUTATE, "--tree", self.wt, "--recover")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue(self.mutated(), "a live run's mutation was restored underneath it")
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=30)
        self.assertEqual(sha(self.fe_file), self.orig_fe)

    def test_interrupt_stops_remaining_mutations(self):
        self.sleeping_fe()
        body = self.fe_block("M-a") + self.fe_block("M-b", old="const w", new="const MUTANT")
        p = self.spawn(self.manifest(body))
        self.assertTrue(wait_until(self.mutated))
        time.sleep(0.3)
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=30)
        self.addCleanup(self.kill_fake_children)
        self.assertEqual(sha(self.fe_file), self.orig_fe)
        self.assertLessEqual(len(self.calls("npx")), 2)


    def killed_midrun(self, *args, out=None):
        p = self.start_and_wait_mutated(*args, out=out)
        p.kill()
        p.communicate(timeout=30)
        self.kill_fake_children()

    def test_killed_run_is_pending_and_the_gate_cli_refuses_with_the_recover_command(self):
        import gate
        gate_out = self.fresh_out()
        self.assertEqual(self.gate("go", "--tree", self.wt, "--pkg", "./internal/logger", "--out", gate_out).returncode, 0)
        self.assertIsNone(gate.pending_mutation(self.wt))
        self.killed_midrun()
        self.assertEqual(os.path.realpath(gate.pending_mutation(self.wt)), os.path.realpath(str(self.out) + ".journal"))
        calls = len(self.calls("go"))
        r = self.gate("go", "--tree", self.wt, "--pkg", "./internal/logger", "--out", gate_out)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(f"mutate.py --tree {self.wt} --recover", r.stdout)
        self.assertEqual(len(self.calls("go")), calls)
        s = json.loads(pathlib.Path(str(gate_out) + ".summary.json").read_text(encoding="utf-8"))
        self.assertEqual(s["verdict"], "TOOL-ERROR")
        self.assertEqual(self.run_tool(MUTATE, "--tree", self.wt, "--recover").returncode, 0)
        self.assertIsNone(gate.pending_mutation(self.wt))
        self.assertEqual(self.gate("go", "--tree", self.wt, "--pkg", "./internal/logger", "--out", gate_out).returncode, 0)

    def test_pending_mutation_belongs_to_one_worktree(self):
        import gate
        self.killed_midrun()
        self.assertIsNotNone(gate.pending_mutation(self.wt))
        self.assertIsNone(gate.pending_mutation(self.main))
        self.assertIsNone(gate.pending_mutation(self.tmp / "nope"))
        self.assertIsNone(gate.pending_mutation(self.tree))

    def test_run_gate_does_not_check_for_a_pending_mutation(self):
        import gate
        self.killed_midrun()
        env = dict(os.environ)
        os.environ.update(self.env)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(env)))
        s = gate.run_gate(["go", "--tree", str(self.wt), "--pkg", "./internal/logger", "--out", str(self.fresh_out())])
        self.assertEqual(s["verdict"], "GREEN")

    def test_recover_refuses_a_journal_that_belongs_to_another_tree(self):
        wt2 = self.tmp / "wt2"
        self.git(self.main, "worktree", "add", "-q", "--detach", str(wt2))
        self.killed_midrun()
        for extra in ((), ("--force",)):
            with self.subTest(extra):
                r = self.run_tool(MUTATE, "--tree", wt2, "--recover", "--out", self.out, *extra)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn(str(self.wt), r.stdout)
                self.assertTrue(pathlib.Path(str(self.out) + ".journal", "journal.json").exists())
                self.assertTrue(self.mutated())
        r = self.run_tool(MUTATE, "--tree", self.wt, "--recover")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sha(self.fe_file), self.orig_fe)

    def test_recover_through_a_symlink_to_the_same_tree_is_accepted(self):
        link = self.tmp / "link-to-wt"
        link.symlink_to(self.wt)
        self.killed_midrun()
        r = self.run_tool(MUTATE, "--tree", link, "--recover")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sha(self.fe_file), self.orig_fe)

    def test_lost_journal_says_so_and_force_clears_the_deadlock(self):
        import gate
        self.killed_midrun()
        shutil.rmtree(str(self.out) + ".journal")
        for label, r in (("recover", self.run_tool(MUTATE, "--tree", self.wt, "--recover")),
                         ("next run", self.mutate(self.manifest(self.fe_block()), out=self.fresh_out()))):
            with self.subTest(label):
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("原檔備份不見了", r.stdout)
                self.assertIn(f"git -C {self.wt} status", r.stdout)
                self.assertIn("--recover --force", r.stdout)
        self.assertTrue(self.mutated())
        self.assertIsNotNone(gate.pending_mutation(self.wt))

        r = self.run_tool(MUTATE, "--tree", self.wt, "--recover", "--force")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"git -C {self.wt} status", r.stdout)
        self.assertNotIn("RECOVER OK", r.stdout)
        self.assertTrue(self.mutated(), "--force has no backup to restore from")
        self.assertIsNone(gate.pending_mutation(self.wt))

    def test_a_live_run_says_wait_and_not_recover(self):
        p = self.start_and_wait_mutated()
        r = self.mutate(self.manifest(self.go_block()), out=self.fresh_out())
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("another mutate.py", r.stdout)
        self.assertNotIn("--recover", r.stdout)
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=30)

    def test_recover_compares_the_journal_tree_by_realpath(self):
        link = self.tmp / "link-to-wt"
        link.symlink_to(self.wt)
        self.killed_midrun()
        doc_path = pathlib.Path(str(self.out) + ".journal", "journal.json")
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        doc["tree"] = str(link)
        doc_path.write_text(json.dumps(doc), encoding="utf-8")
        r = self.run_tool(MUTATE, "--tree", self.wt, "--recover")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sha(self.fe_file), self.orig_fe)

    def test_unreadable_journal_is_treated_as_lost(self):
        self.killed_midrun()
        pathlib.Path(str(self.out) + ".journal", "journal.json").write_text("{not json", encoding="utf-8")
        r = self.run_tool(MUTATE, "--tree", self.wt, "--recover")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("原檔備份不見了", r.stdout)
        self.assertEqual(self.run_tool(MUTATE, "--tree", self.wt, "--recover", "--force").returncode, 0)

    def test_journal_corrupted_during_the_run_is_a_failed_restore(self):
        journal = pathlib.Path(str(self.out) + ".journal", "journal.json")
        self.fake(npx={"when": [{"path": str(self.fe_file), "contains": "MUTANT",
                                 "use": {**text_replay("fe_red"), "shell": ["sh", "-c", f"printf garbage > {journal}"]}}],
                       **text_replay("fe_green")})
        r = self.mutate(self.manifest(self.fe_block()))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertRegex(r.stdout.splitlines()[0], r"^MUTATE KILLED=0 SURVIVED=0 INVALID=1 exit=2 ")
        self.assertIn("RESTORE FAILED", r.stdout)
        self.assertIn(FE_REL, r.stdout)

    def test_failure_writing_the_journal_is_invalid_and_touches_nothing(self):
        git_dir = self.git(self.wt, "rev-parse", "--absolute-git-dir").strip()
        self.addCleanup(lambda: os.chmod(git_dir, 0o755))
        self.fake(npx={"shell": ["chmod", "555", git_dir], **text_replay("fe_green")})
        r = self.mutate(self.manifest(self.fe_block()))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("INVALID=1", r.stdout.splitlines()[0])
        self.assertIn("journal", r.stdout)
        self.assertFalse(pathlib.Path(str(self.out) + ".journal").exists())
        self.assertEqual(sha(self.fe_file), self.orig_fe)

    def test_partial_journal_from_a_kill_before_the_pointer_can_be_cleared(self):
        partial = pathlib.Path(str(self.out) + ".journal", "files")
        partial.mkdir(parents=True)
        r = self.mutate(self.manifest(self.fe_block()))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        r = self.run_tool(MUTATE, "--tree", self.wt, "--recover", "--out", self.out)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        r = self.run_tool(MUTATE, "--tree", self.wt, "--recover", "--out", self.out, "--force")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(partial.parent.exists())
        self.assertEqual(self.mutate(self.manifest(self.fe_block())).returncode, 0)

    def test_restore_oserror_is_a_mutate_line_exit_2_naming_the_file(self):
        parent = self.fe_file.parent
        self.addCleanup(lambda: os.chmod(parent, 0o755))
        self.fake(npx={"when": [{"path": str(self.fe_file), "contains": "MUTANT",
                                 "use": {**text_replay("fe_red"), "shell": ["chmod", "555", str(parent)]}}],
                       **text_replay("fe_green")})
        r = self.mutate(self.manifest(self.fe_block()))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertRegex(r.stdout.splitlines()[0], r"^MUTATE KILLED=0 SURVIVED=0 INVALID=1 exit=2 ")
        self.assertIn("RESTORE FAILED", r.stdout)
        self.assertIn(FE_REL, r.stdout)
        self.assertTrue(self.mutated())
        s = json.loads(pathlib.Path(str(self.out) + ".summary.json").read_text(encoding="utf-8"))
        self.assertIn(FE_REL, s["restore_error"])
        os.chmod(parent, 0o755)
        self.assertEqual(self.run_tool(MUTATE, "--tree", self.wt, "--recover").returncode, 0)
        self.assertEqual(sha(self.fe_file), self.orig_fe)

    def test_recover_oserror_names_the_file_and_is_not_a_traceback(self):
        parent = self.fe_file.parent
        self.killed_midrun()
        self.addCleanup(lambda: os.chmod(parent, 0o755))
        os.chmod(parent, 0o555)
        r = self.run_tool(MUTATE, "--tree", self.wt, "--recover")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("MUTATE RECOVER FAILED", r.stdout)
        self.assertIn(FE_REL, r.stdout)
        os.chmod(parent, 0o755)
        self.assertEqual(self.run_tool(MUTATE, "--tree", self.wt, "--recover").returncode, 0)

    def test_failure_writing_the_mutant_is_invalid_and_leaves_no_journal(self):
        parent = self.fe_file.parent
        self.addCleanup(lambda: os.chmod(parent, 0o755))
        self.fake(npx={"shell": ["chmod", "555", str(parent)], **text_replay("fe_green")})
        r = self.mutate(self.manifest(self.fe_block()))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("INVALID=1", r.stdout.splitlines()[0])
        self.assertFalse(pathlib.Path(str(self.out) + ".journal").exists())
        self.assertEqual(sha(self.fe_file), self.orig_fe)


class TreeLockTest(MutateHarness):
    def test_overlay_run_is_refused_during_an_in_tree_runs_baseline(self):
        self.fake_fe_on("MUTANT", "fe_red")
        self.fake(npx={**self.cfg["npx"], "sleep": 3})
        p = self.spawn(self.manifest(self.fe_block()))
        self.assertTrue(wait_until(lambda: self.calls("npx")), "in-tree run never started")
        self.assertNotIn("MUTANT", self.fe_file.read_text(encoding="utf-8"))
        r = self.mutate(self.manifest(self.go_block()), out=self.fresh_out())
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("another mutate.py", r.stdout)
        self.assertEqual(self.calls("go"), [])
        p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0)

    def test_in_tree_run_is_refused_while_an_overlay_run_is_live(self):
        self.fake(go={**self.cfg["go"], "sleep": 3})
        p = self.spawn(self.manifest(self.go_block()))
        self.assertTrue(wait_until(lambda: self.calls("go")), "overlay run never started")
        r = self.mutate(self.manifest(self.fe_block()), out=self.fresh_out())
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("another mutate.py", r.stdout)
        self.assertEqual(self.calls("npx"), [])
        self.assertEqual(sha(self.fe_file), self.orig_fe)
        p.communicate(timeout=30)

    def test_two_overlay_runs_share_the_tree(self):
        self.fake(go={**self.cfg["go"], "sleep": 2})
        p = self.spawn(self.manifest(self.go_block()))
        self.assertTrue(wait_until(lambda: self.calls("go")), "overlay run never started")
        r = self.mutate(self.manifest(self.go_block()), out=self.fresh_out())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0)


class EditBeforeMutationTest(MutateHarness):
    def appending(self, path):
        return {"shell": ["sh", "-c", f"printf '// OWNER EDIT\\n' >> {path}"], **text_replay("fe_green")}

    def test_edit_during_baseline_is_invalid_and_the_edit_survives(self):
        self.fake(npx=self.appending(self.fe_file))
        r = self.mutate(self.manifest(self.fe_block()))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("KILLED=0 SURVIVED=0 INVALID=1", r.stdout.splitlines()[0])
        self.assertIn(FE_REL, r.stdout)
        self.assertTrue(self.fe_file.read_text(encoding="utf-8").endswith("// OWNER EDIT\n"))
        self.assertEqual(len(self.calls("npx")), 1, "the mutant must not have been tested")
        self.assertFalse(pathlib.Path(str(self.out) + ".journal").exists())

    def test_edit_to_one_file_of_a_multifile_mutation_writes_neither(self):
        sql = self.wt / SQL_REL
        self.fake(npx=self.appending(sql))
        body = self.fe_block() + f"file: {SQL_REL}\n<<<<<<< old\n'ORIGINAL'\n=======\n'SECOND'\n>>>>>>> new\n"
        r = self.mutate(self.manifest(body))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(sha(self.fe_file), self.orig_fe)
        self.assertIn("// OWNER EDIT", sql.read_text(encoding="utf-8"))
        self.assertNotIn("SECOND", sql.read_text(encoding="utf-8"))


class PytestAndHelpRulesTest(MutateHarness):
    def py_block(self, test=None):
        test = test or f"ai --repo {self.repo}"
        return f"=== P-a d\ntest: {test}\nfile: {PY_REL}\n<<<<<<< old\n'ORIGINAL'\n=======\n'MUTANT'\n>>>>>>> new\n"

    def fake_pytest_on_mutant(self, text, rc=1):
        p = self.fresh_out("pytest.txt")
        p.write_text(text, encoding="utf-8")
        self.fake(docker={"run": {"when": [{"path": str(self.wt / PY_REL), "contains": "MUTANT", "use": {"file": str(p), "rc": rc}}],
                                  **text_replay("ai_green")}})

    def test_lint_kinds_cannot_kill_a_mutant(self):
        for flag in ("--kind mypy", "--kind ruff", "--kind=ruff"):
            with self.subTest(flag):
                r = self.mutate(self.manifest(self.py_block(f"ai --repo {self.repo} {flag}")))
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("MUTATE TOOL-ERROR", r.stdout)
        self.assertEqual(self.calls(), [])

    def test_a_mutant_that_does_not_load_is_invalid_not_killed(self):
        for name, message in (("SyntaxError", "invalid syntax"), ("IndentationError", "unexpected indent"),
                              ("ImportError", "cannot import name 'V'"), ("ModuleNotFoundError", "No module named 'app.x'")):
            with self.subTest(name):
                self.fake_pytest_on_mutant(f"F                                                  [100%]\n=== FAILURES ===\nE     {name}: {message}\n"
                                           f"=== short test summary info ===\nFAILED tests/test_config.py::test_lazy - {name}: {message}\n1 failed in 0.03s\n")
                out = self.fresh_out()
                r = self.mutate(self.manifest(self.py_block()), out=out)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("KILLED=0 SURVIVED=0 INVALID=1", r.stdout.splitlines()[0])
                self.assertIn(name, r.stdout)

    def test_a_load_error_cut_from_the_summary_line_still_counts(self):
        self.fake_pytest_on_mutant("F                                                  [100%]\n=== FAILURES ===\n"
                                   "E     ModuleNotFoundError: No module named 'app.x'\n=== short test summary info ===\n"
                                   "FAILED tests/test_config.py::test_lazy - Modu...\n1 failed in 0.03s\n")
        r = self.mutate(self.manifest(self.py_block()))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("INVALID=1", r.stdout.splitlines()[0])

    def test_an_assertion_failure_stays_killed(self):
        self.fake(docker={"run": {"when": [{"path": str(self.wt / PY_REL), "contains": "MUTANT", "use": text_replay("ai_red")}],
                                  **text_replay("ai_green")}})
        r = self.mutate(self.manifest(self.py_block()))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("KILLED=1", r.stdout.splitlines()[0])

    def test_load_error_words_in_a_go_failure_stay_killed(self):
        text = (FIXTURES / "go_red.jsonl").read_text(encoding="utf-8").replace("boom: want 1 got 2", "ImportError: want 1 got 2")
        self.assertIn("ImportError", text)
        self.fake(go={"when": [{"path": str(self.go_file), "contains": "MUTANT", "use": {"text": text, "rc": 1}}], **go_replay("go_green")})
        r = self.mutate(self.manifest(self.go_block()))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("KILLED=1", r.stdout.splitlines()[0])

    def test_help_in_a_test_line_is_refused_instead_of_exiting_zero(self):
        for flag in ("-h", "--help", "--he"):
            with self.subTest(flag):
                r = self.mutate(self.manifest(self.go_block(test=f"{GO_TEST} {flag}")))
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertTrue(r.stdout.startswith("MUTATE TOOL-ERROR"), r.stdout)
        self.assertEqual(self.calls(), [])

    def test_a_crash_is_exit_2_because_exit_1_means_survived(self):
        p = self.fresh_out("m.json")
        p.write_text(json.dumps({"test": FE_TEST, "mutations": [{"name": "J", "files": [{"path": "a.ts"}]}]}), encoding="utf-8")
        r = self.mutate(p)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("MUTATE TOOL-ERROR"), r.stdout)


class SelectionAndOutputTest(MutateHarness):
    def test_only_selects_and_unknown_name_is_a_tool_error(self):
        body = self.fe_block("M-a") + self.fe_block("M-b", old="not there", new="x")
        r = self.mutate(self.manifest(body), "--only", "M-a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("KILLED=1 SURVIVED=0 INVALID=0", r.stdout.splitlines()[0])
        r = self.mutate(self.manifest(body), "--only", "M-z", out=self.fresh_out())
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_out_file_is_full_evidence_per_mutation(self):
        self.mutate(self.manifest(self.fe_block() + self.fe_block("M-b", old="const w", new="const w2")))
        text = self.out.read_text(encoding="utf-8")
        for needle in ("M-a", "flips the value", "KILLED", "M-b", "SURVIVED", "'ORIGINAL'", "'MUTANT'",
                       FE_REL, "expected 1 to be 2", self.orig_fe, "--include"):
            self.assertIn(needle, text)

    def test_summary_json(self):
        self.mutate(self.manifest(self.fe_block() + self.fe_block("M-b", old="not there", new="x")))
        s = json.loads(pathlib.Path(str(self.out) + ".summary.json").read_text(encoding="utf-8"))
        self.assertEqual(s["killed"], 1)
        self.assertEqual(s["invalid"], 1)
        self.assertEqual(s["survived"], 0)
        self.assertEqual(s["exit"], 2)
        rows = {m["name"]: m for m in s["mutations"]}
        self.assertEqual(rows["M-a"]["result"], "KILLED")
        self.assertTrue(rows["M-a"]["killed_by"])
        self.assertEqual(rows["M-b"]["result"], "INVALID")

    def test_stdout_stays_short(self):
        body = "".join(self.fe_block(f"M-{i}", old="not there", new="x") for i in range(40))
        r = self.mutate(self.manifest(body))
        self.assertLessEqual(len(r.stdout.splitlines()), 30)
        self.assertIn("INVALID=40", r.stdout.splitlines()[0])

    def test_python_mutation_runs_in_tree_through_the_container(self):
        py = self.wt / PY_REL
        before = sha(py)
        self.fake(docker={"run": {"when": [{"path": str(py), "contains": "MUTANT", "use": text_replay("ai_red")}], **text_replay("ai_green")}})
        body = (f"=== P-a d\ntest: ai --repo {self.repo} tests/test_config.py\nfile: {PY_REL}\n"
                "<<<<<<< old\n'ORIGINAL'\n=======\n'MUTANT'\n>>>>>>> new\n")
        r = self.mutate(self.manifest(body))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sha(py), before)
        self.assertIn("test_bad", r.stdout)


if __name__ == "__main__":
    unittest.main()
