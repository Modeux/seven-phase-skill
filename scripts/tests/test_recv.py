# -*- coding: utf-8 -*-
"""recv: nothing that is red, narrow, moving or out of scope reaches the main checkout.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_recv.py' -v
"""
import contextlib
import fcntl
import io
import os
import pathlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _gate_fakes import FAKE_SRC, MUTATE, SCRIPTS, Harness, go_replay, text_replay  # noqa: E402

import gate  # noqa: E402
import recv  # noqa: E402

RECV = SCRIPTS / "recv.py"
# WHY: recv adds its own `go vet` runs. They log under their own name, with config keyed by tag, so a fixture written
# for `go test` (a red replay exits 1) keeps meaning `go test` and each vet can fail on its own.
GO_FAKE_SRC = FAKE_SRC.replace('c = cfg.get(name, {})\n', '''tags = ""
if name == "go" and argv[:1] == ["vet"]:
    name = log["name"] = "go-vet"
    tags = log["tags"] = argv[argv.index("-tags") + 1] if "-tags" in argv else ""
c = cfg.get(name + ":" + tags, cfg.get(name, {}))
''')
assert "go-vet" in GO_FAKE_SRC
LOGGER_FILE = "backend-golang/internal/logger/logger.go"
TAG_FILE = "backend-golang/internal/tag/tag.go"
FE_FILE = "angular-workspace/projects/frontend/src/app/a.ts"
FE_SPEC = "angular-workspace/projects/frontend/src/app/site-content.spec.ts"
ADMIN_FILE = "angular-workspace/projects/admin/src/b.ts"
AI_FILE = "ai-service/app/c.py"
DOC_FILE = "docs/d.md"
LOGGER_GATE = "go --pkg ./internal/logger"
ALL_SCOPE = ("backend-golang/**", "angular-workspace/**", "ai-service/**", "docs/**")
JUDGE_LABELS = ("red-run 紅的原因", "偏離裁決", "抽的突變", "改語義的舊字樣 grep", "主寫測試未動")
GREEN_TEST = "TestNewHandler_RespectsLevel"


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          check=True, capture_output=True, text=True).stdout


class Base(Harness):
    def setUp(self):
        super().setUp()
        (self.bin / "go").write_text(GO_FAKE_SRC, encoding="utf-8")
        self.main = self.tmp / "main"
        self.main.mkdir()
        g(self.main, "init", "-q")
        files = {
            ".gitignore": "node_modules/\ntmp/\nai-service/.venv/\n",
            LOGGER_FILE: "package logger\n",
            "backend-golang/internal/logger/sub/sub.go": "package sub\n",
            "backend-golang/internal/logger/testdata/fix.go": "package fix\n",
            "backend-golang/internal/loggerx/x.go": "package loggerx\n",
            TAG_FILE: "package tag\n",
            "backend-golang/main.go": "package main\n",
            FE_FILE: "// a\n",
            FE_SPEC: "// spec\n",
            ADMIN_FILE: "// b\n",
            AI_FILE: "# c\n",
            "ai-service/tests/test_config.py": "# test\n",
            DOC_FILE: "doc\n",
            ".claude/PRPs/plans/t/PROGRESS.md": "---\nstatus: doing\n---\n# 紀錄\n",
        }
        for rel, text in files.items():
            self.write(self.main, rel, text)
        g(self.main, "add", ".")
        g(self.main, "commit", "-qm", "init")
        self.progress = self.main / ".claude/PRPs/plans/t/PROGRESS.md"
        self.snap = self.worktree("snap")
        self.slice = self.worktree("slice")
        self.out_dir = self.tmp / "out"
        self.fake(go=go_replay("go_green"))

    def worktree(self, name):
        path = self.tmp / name
        g(self.main, "worktree", "add", "-q", "--detach", str(path), "HEAD")
        (path / "angular-workspace/node_modules").mkdir(parents=True)
        return path

    @staticmethod
    def write(root, rel, text):
        p = pathlib.Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def change(self, rel=LOGGER_FILE, text="package logger // changed\n"):
        self.write(self.slice, rel, text)

    def args(self, *extra, gates=(LOGGER_GATE,), scope=ALL_SCOPE, apply=False, progress=None, no_board=True,
             out_dir=True):
        a = ["--slice", "S1", "--tree", self.slice, "--snapshot", self.snap, "--root", self.main]
        for x in gates:
            a += ["--gate", x]
        for x in scope:
            a += ["--scope", x]
        if out_dir:
            a += ["--out-dir", self.out_dir]
        if apply:
            a += ["--apply"]
        if progress:
            a += ["--progress", progress]
        if no_board:
            a += ["--no-board"]
        return [*a, *extra]

    def recv(self, *extra, script=RECV, **kw):
        r = self.run_tool(script, *self.args(*extra, **kw), timeout=90)
        r.first = r.stdout.splitlines()[0] if r.stdout.strip() else ""
        return r

    def apply(self, *extra, **kw):
        return self.recv(*extra, apply=True, progress=self.progress, **kw)

    def inproc(self, *extra, **kw):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(buf):
            rc = recv.run_cli([str(x) for x in self.args(*extra, **kw)])
        return rc, buf.getvalue()

    def main_state(self):
        state = {}
        for p in sorted(self.main.rglob("*")):
            if ".git" in p.relative_to(self.main).parts[:1] or not p.is_file():
                continue
            state[str(p.relative_to(self.main))] = p.read_bytes()
        return state, g(self.main, "status", "--porcelain", "--untracked-files=all", "--ignored")

    def assertRejected(self, r, step, code=1):
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        self.assertTrue(r.first.startswith(f"RECV S1 REJECTED:{step} "), r.stdout + r.stderr)

    def assertToolError(self, r):
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("TOOL-ERROR", r.first, r.stdout + r.stderr)

    def assertNothingMerged(self, before):
        self.assertEqual(self.main_state(), before)

    def detail(self):
        return (self.out_dir / "recv-S1.out").read_text(encoding="utf-8")

    def vets(self):
        return [(c["tags"], [x for x in c["argv"] if x.startswith(".")]) for c in self.calls("go-vet")]

    def reset_slice(self):
        g(self.slice, "checkout", "--", ".")
        g(self.slice, "clean", "-fdq")

    def in_all_trees(self, rel, text):
        for root in (self.main, self.snap, self.slice):
            self.write(root, rel, text)

    def pending_pointer(self):
        return pathlib.Path(g(self.slice, "rev-parse", "--absolute-git-dir").strip(), gate.MUTATE_POINTER)

    def tools_with_board(self, source):
        """A copy of the scripts whose board.py is a fake, so nothing touches the shared board."""
        d = self.tmp / "tools"
        d.mkdir(exist_ok=True)
        for name in ("recv.py", "gate.py", "merge_back.py", "_common.py", "dockerslot.py"):
            shutil.copy2(SCRIPTS / name, d / name)
        (d / "board.py").write_text(source, encoding="utf-8")
        return d / "recv.py"


class RequireTest(Base):
    def test_missing_deliverable_is_rejected_before_anything_else(self):
        self.change()
        before = self.main_state()
        r = self.apply("--require", "tmp/S1-red-run.out")
        self.assertRejected(r, "require")
        self.assertIn("S1-red-run.out", r.stdout)
        self.assertEqual(self.calls("go"), [])
        self.assertNothingMerged(before)

    def test_empty_deliverable_is_rejected(self):
        self.change()
        self.write(self.slice, "tmp/S1-red-run.out", "")
        r = self.recv("--require", "tmp/S1-red-run.out")
        self.assertRejected(r, "require")
        self.assertIn("S1-red-run.out", r.stdout)
        self.assertEqual(self.calls("go"), [])

    def test_blank_deliverable_counts_as_empty(self):
        self.change()
        self.write(self.slice, "tmp/S1-red-run.out", " \n\n")
        self.assertRejected(self.recv("--require", "tmp/S1-red-run.out"), "require")

    def test_directory_is_not_a_deliverable(self):
        self.change()
        (self.slice / "tmp/d").mkdir(parents=True)
        self.assertRejected(self.recv("--require", "tmp/d"), "require")

    def test_every_missing_deliverable_is_listed(self):
        self.change()
        r = self.recv("--require", "tmp/one.out", "--require", "tmp/two.out")
        self.assertIn("one.out", r.stdout)
        self.assertIn("two.out", r.stdout)

    def test_present_deliverable_passes_relative_to_the_tree_or_absolute(self):
        self.change()
        self.write(self.slice, "tmp/S1-red-run.out", "red\n")
        self.write(self.tmp, "elsewhere/x.out", "x\n")
        r = self.recv("--require", "tmp/S1-red-run.out", "--require", self.tmp / "elsewhere/x.out")
        self.assertEqual(r.returncode, 0, r.stdout)


class PreflightTest(Base):
    def test_head_mismatch_prints_merge_back_message_and_runs_no_gate(self):
        self.change()
        g(self.slice, "add", ".")
        g(self.slice, "commit", "-qm", "agent committed")
        r = self.recv()
        self.assertRejected(r, "preflight")
        self.assertIn("HEAD differs: S1", r.stdout)
        self.assertEqual(self.calls("go"), [])

    def test_main_differing_from_snapshot_stops_and_runs_no_gate(self):
        self.change()
        self.write(self.main, LOGGER_FILE, "someone else\n")
        r = self.recv()
        self.assertRejected(r, "preflight")
        self.assertIn(f"main differs from snapshot: {LOGGER_FILE} (S1)", r.stdout)
        self.assertEqual(self.calls("go"), [])

    def test_revert_to_head_stops_unless_allowed(self):
        for root in (self.main, self.snap):
            self.write(root, LOGGER_FILE, "reviewed change\n")
        r = self.recv()
        self.assertRejected(r, "preflight")
        self.assertIn(f"S1: reverts {LOGGER_FILE} to HEAD", r.stdout)
        self.assertEqual(self.calls("go"), [])

        r = self.recv("--allow-revert", f"S1:{LOGGER_FILE}")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("revert", r.stdout)
        self.assertIn(LOGGER_FILE, r.stdout)

    def test_deletion_in_the_tree_stops(self):
        self.write(self.main, "backend-golang/internal/tag/new.go", "package tag\n")
        self.write(self.snap, "backend-golang/internal/tag/new.go", "package tag\n")
        r = self.recv(gates=("go --pkg ./internal/tag",))
        self.assertRejected(r, "preflight")
        self.assertIn("deletes backend-golang/internal/tag/new.go", r.stdout)

    def test_no_changes_is_rejected_not_an_empty_success(self):
        r = self.recv()
        self.assertRejected(r, "preflight")
        self.assertIn("files=0", r.first)
        self.assertEqual(self.calls("go"), [])

    def test_gates_count_on_the_first_line_is_gates_actually_run(self):
        self.change()
        self.write(self.main, LOGGER_FILE, "someone else\n")
        self.assertIn(" gates=0 ", self.recv().first)


class ScopeTest(Base):
    def test_files_outside_scope_are_each_listed_and_no_gate_runs(self):
        self.change()
        self.change(TAG_FILE, "package tag // x\n")
        self.change(DOC_FILE, "changed\n")
        r = self.recv(scope=("backend-golang/internal/logger",),
                      gates=("go --pkg ./internal/logger --pkg ./internal/tag",))
        self.assertRejected(r, "scope")
        self.assertIn(TAG_FILE, r.stdout)
        self.assertIn(DOC_FILE, r.stdout)
        reasons = [x for x in r.stdout.splitlines() if "out of scope" in x]
        self.assertEqual(len(reasons), 2, r.stdout)
        self.assertEqual(self.calls("go"), [])

    def test_scope_is_required(self):
        self.change()
        self.assertToolError(self.recv(scope=()))

    def test_glob_star_does_not_cross_directories(self):
        self.change("backend-golang/internal/logger/sub/sub.go", "package sub // x\n")
        r = self.recv(scope=("backend-golang/internal/logger/*.go",), gates=("go --pkg ./internal/logger/...",))
        self.assertRejected(r, "scope")

    def test_double_star_crosses_directories(self):
        self.change("backend-golang/internal/logger/sub/sub.go", "package sub // x\n")
        r = self.recv(scope=("backend-golang/internal/**/*.go",), gates=("go --pkg ./internal/logger/...",))
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_directory_scope_covers_below_but_not_a_sibling_with_the_same_prefix(self):
        self.change("backend-golang/internal/loggerx/x.go", "package loggerx // x\n")
        r = self.recv(scope=("backend-golang/internal/logger",), gates=("go --pkg ./internal/loggerx",))
        self.assertRejected(r, "scope")
        self.change(LOGGER_FILE)
        r = self.recv(scope=("backend-golang/internal/logger/", "backend-golang/internal/loggerx/x.go"),
                      gates=("go --pkg ./internal/...",))
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_a_scope_matching_everything_is_a_tool_error(self):
        self.change()
        for scope in ("**", "**/*", ".", "./"):
            with self.subTest(scope=scope):
                self.assertToolError(self.recv(scope=(scope,)))

    def test_scope_escaping_the_tree_is_a_tool_error(self):
        self.change()
        self.assertToolError(self.recv(scope=("../other",)))
        self.assertToolError(self.recv(scope=("/etc/passwd",)))

    def test_absolute_scope_inside_the_tree_is_accepted(self):
        self.change()
        r = self.recv(scope=(str(self.slice / "backend-golang/internal/logger"),))
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_scope_file(self):
        self.change()
        f = self.tmp / "scope.txt"
        f.write_text("# 這片的範圍\n\nbackend-golang/internal/logger/**\n", encoding="utf-8")
        self.assertEqual(self.recv("--scope-file", f, scope=()).returncode, 0)
        f.write_text("backend-golang/internal/tag/**\n", encoding="utf-8")
        self.assertRejected(self.recv("--scope-file", f, scope=()), "scope")

    def test_scope_file_problems_are_tool_errors(self):
        self.change()
        f = self.tmp / "scope.txt"
        f.write_text("# only a comment\n", encoding="utf-8")
        self.assertToolError(self.recv("--scope-file", f, scope=()))
        self.assertToolError(self.recv("--scope-file", self.tmp / "missing.txt", scope=()))
        self.assertToolError(self.recv("--scope-file", f))

    def test_scope_that_matched_nothing_goes_to_the_judgement_list(self):
        self.change()
        r = self.recv(scope=("backend-golang/internal/logger/**", "backend-golang/internal/promised/**"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("backend-golang/internal/promised/**", r.stdout)


class CoverageTest(Base):
    def test_go_package_without_a_covering_gate_is_rejected(self):
        self.change()
        r = self.recv(gates=("go --pkg ./internal/tag",))
        self.assertRejected(r, "coverage")
        self.assertIn(LOGGER_FILE, r.stdout)
        self.assertIn("./internal/logger", r.stdout)
        self.assertEqual(self.calls("go"), [])

    def test_every_changed_package_needs_coverage(self):
        self.change()
        self.change(TAG_FILE, "package tag // x\n")
        r = self.recv(gates=("go --pkg ./internal/logger",))
        self.assertRejected(r, "coverage")
        self.assertIn(TAG_FILE, r.stdout)
        self.assertNotIn(f"{LOGGER_FILE} needs", r.stdout)

    def test_package_spellings_gate_accepts_all_cover(self):
        self.change()
        for spec in ("--pkg ./internal/logger", "--pkg internal/logger", "--pkg backend-golang/internal/logger",
                     f"--pkg {self.slice}/backend-golang/internal/logger",
                     "--pkg ./internal/logger/...", "--pkg ./internal/...", "--pkg ./...", ""):
            with self.subTest(spec=spec):
                self.assertEqual(self.recv(gates=(f"go {spec}".strip(),)).returncode, 0)

    def test_all_covers(self):
        self.change()
        self.assertEqual(self.recv(gates=("go --all",)).returncode, 0)

    def test_all_narrowed_by_pkg_covers_only_those_packages(self):
        self.change()
        self.assertRejected(self.recv(gates=("go --all --pkg ./internal/tag",)), "coverage")

    def test_a_parent_package_does_not_cover_a_subpackage_without_a_wildcard(self):
        self.change("backend-golang/internal/logger/sub/sub.go", "package sub // x\n")
        self.assertRejected(self.recv(), "coverage")

    def test_sibling_with_the_same_prefix_is_not_covered_by_a_wildcard(self):
        self.change("backend-golang/internal/loggerx/x.go", "package loggerx // x\n")
        self.assertRejected(self.recv(gates=("go --pkg ./internal/logger/...",)), "coverage")

    def test_root_package_needs_dot_or_wildcard(self):
        self.change("backend-golang/main.go", "package main // x\n")
        self.assertRejected(self.recv(), "coverage")
        self.fake(go=go_replay("go_green"))
        self.assertEqual(self.recv(gates=("go --pkg ./...",)).returncode, 0)

    def test_testdata_file_belongs_to_the_enclosing_package(self):
        self.change("backend-golang/internal/logger/testdata/fix.go", "package fix // x\n")
        self.assertEqual(self.recv().returncode, 0)
        self.assertRejected(self.recv(gates=("go --pkg ./internal/tag",)), "coverage")

    def test_build_and_vet_run_no_tests_so_they_do_not_cover(self):
        self.change()
        for flag in ("--build", "--vet"):
            with self.subTest(flag=flag):
                self.assertRejected(self.recv(gates=(f"go --pkg ./internal/logger {flag}",)), "coverage")

    def test_build_and_vet_are_fine_next_to_a_test_gate(self):
        self.change()
        self.fake(go={"text": "", "rc": 0})
        r = self.recv(gates=("go --pkg ./internal/logger --build",))
        self.assertRejected(r, "coverage")
        self.fake(go={"when": [{"path": str(self.slice / LOGGER_FILE), "contains": "changed",
                                "use": go_replay("go_green")}], "rc": 0})
        r = self.recv(gates=("go --pkg ./internal/logger", "go --pkg ./internal/logger --build"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("gates=4", r.first)

    def test_frontend_project_needs_its_own_gate(self):
        self.change(FE_FILE, "// changed\n")
        self.fake(npx=text_replay("fe_green"))
        r = self.recv(gates=("fe --project admin",))
        self.assertRejected(r, "coverage")
        self.assertIn(FE_FILE, r.stdout)
        self.assertIn("fe --project frontend", r.stdout)
        self.assertEqual(self.recv(gates=("fe --project frontend",)).returncode, 0)

    def test_admin_project_needs_its_own_gate(self):
        self.change(ADMIN_FILE, "// changed\n")
        self.fake(npx=text_replay("fe_green"))
        self.assertRejected(self.recv(gates=("fe --project frontend",)), "coverage")
        self.assertEqual(self.recv(gates=("fe --project admin",)).returncode, 0)

    def test_production_build_does_not_cover_the_project(self):
        self.change(FE_FILE, "// changed\n")
        self.assertRejected(self.recv(gates=("fe-build --project frontend",)), "coverage")

    def test_python_needs_an_ai_gate(self):
        self.change(AI_FILE, "# changed\n")
        r = self.recv()
        self.assertRejected(r, "coverage")
        self.assertIn(AI_FILE, r.stdout)

    def test_ai_gate_covers_and_defaults_repo_to_the_root(self):
        self.change(AI_FILE, "# changed\n")
        self.write(self.main, "ai-service/.venv/pyvenv.cfg", "home = /usr/local/bin\n")
        self.fake(docker={"run": text_replay("ai_green")})
        r = self.recv(gates=("ai",))
        self.assertEqual(r.returncode, 0, r.stdout)
        run = [c for c in self.calls("docker") if c["argv"][0] == "run"][0]
        self.assertIn(f"{self.main}/ai-service/.venv:/app/.venv:ro", run["argv"])

    def test_ai_lint_does_not_cover(self):
        self.change(AI_FILE, "# changed\n")
        self.write(self.main, "ai-service/.venv/pyvenv.cfg", "home = /usr/local/bin\n")
        self.assertRejected(self.recv(gates=("ai --kind ruff",)), "coverage")

    def test_file_with_no_gate_rule_passes_but_is_named_for_judgement(self):
        self.change()
        self.change("ops/deploy.sh", "x\n")
        self.change("scripts/run.sh", "x\n")
        r = self.recv(scope=(*ALL_SCOPE, "ops/**", "scripts/**"))
        self.assertEqual(r.returncode, 0, r.stdout)
        hint = [x for x in r.stdout.splitlines() if "無閘規則" in x]
        self.assertEqual(len(hint), 1, r.stdout)
        self.assertIn("ops/deploy.sh", hint[0])
        self.assertIn("scripts/run.sh", hint[0])

    def test_integration_tagged_file_with_no_integ_gate_is_named_for_judgement(self):
        self.change("backend-golang/internal/logger/x_integration_test.go",
                    "//go:build integration\n\npackage logger\n")
        r = self.recv()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("整合測試", r.stdout)
        self.assertIn("x_integration_test.go", r.stdout)

    def test_integration_tagged_file_with_an_integ_gate_raises_nothing(self):
        self.change("backend-golang/internal/logger/x_integration_test.go",
                    "//go:build integration\n\npackage logger\n")
        r = self.recv(gates=("go --pkg ./internal/logger --integ",))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("整合測試", r.stdout)


class NarrowTest(Base):
    def test_run_without_integ_is_rejected(self):
        self.change()
        r = self.recv(gates=(f"go --pkg ./internal/logger --run {GREEN_TEST}",))
        self.assertRejected(r, "coverage")
        self.assertIn("--allow-narrow", r.stdout)
        self.assertEqual(self.calls("go"), [])

    def test_run_is_rejected_even_next_to_a_full_gate(self):
        self.change()
        r = self.recv(gates=(LOGGER_GATE, f"go --pkg ./internal/logger --run {GREEN_TEST}"))
        self.assertRejected(r, "coverage")

    def test_include_is_rejected_even_next_to_a_full_gate(self):
        self.change(FE_FILE, "// changed\n")
        self.fake(npx=text_replay("fe_green"))
        r = self.recv(gates=("fe --project frontend", f"fe --project frontend --include {FE_SPEC}"))
        self.assertRejected(r, "coverage")
        self.assertEqual(self.calls("npx"), [])

    def test_allow_narrow_lets_run_through_and_records_the_reason(self):
        self.change()
        reason = "只跑這支因為整包要 40 分鐘"
        r = self.apply("--allow-narrow", reason, gates=(f"go --pkg ./internal/logger --run {GREEN_TEST}",))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(reason, r.stdout)
        self.assertIn(reason, self.detail())
        self.assertIn(reason, self.progress.read_text(encoding="utf-8"))

    def test_allow_narrow_from_a_file(self):
        self.change()
        reason = "有 | 這種字元的理由"
        f = self.tmp / "reason.txt"
        f.write_text(reason + "\n", encoding="utf-8")
        r = self.recv("--allow-narrow-file", f, gates=(f"go --pkg ./internal/logger --run {GREEN_TEST}",))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(reason, r.stdout)

    def test_blank_narrow_reason_is_a_tool_error(self):
        self.change()
        self.assertToolError(self.recv("--allow-narrow", "  ", gates=(f"go --pkg ./internal/logger --run {GREEN_TEST}",)))

    def test_allow_narrow_does_not_excuse_an_uncovered_package(self):
        self.change()
        r = self.recv("--allow-narrow", "x", gates=(f"go --pkg ./internal/tag --run {GREEN_TEST}",))
        self.assertRejected(r, "coverage")

    def test_include_is_rejected_and_allow_narrow_lets_it_through(self):
        self.change(FE_FILE, "// changed\n")
        self.fake(npx=text_replay("fe_green"))
        gates = (f"fe --project frontend --include {FE_SPEC}",)
        r = self.recv(gates=gates)
        self.assertRejected(r, "coverage")
        self.assertEqual(self.calls("npx"), [])
        r = self.recv("--allow-narrow", "只改這支 spec", gates=gates)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("只改這支 spec", r.stdout)

    def test_ai_paths_are_narrow_too(self):
        self.change(AI_FILE, "# changed\n")
        self.write(self.main, "ai-service/.venv/pyvenv.cfg", "home = /usr/local/bin\n")
        self.fake(docker={"run": text_replay("ai_green")})
        gates = ("ai tests/test_config.py",)
        self.assertRejected(self.recv(gates=gates), "coverage")
        self.assertEqual(self.recv("--allow-narrow", "x", gates=gates).returncode, 0)

    def test_named_integration_gate_alone_does_not_cover_the_package(self):
        # WHY: otherwise adding --integ is a one-word way around the --run refusal.
        self.change()
        r = self.recv(gates=(f"go --pkg ./internal/logger --integ --run {GREEN_TEST}",))
        self.assertRejected(r, "coverage")
        self.assertEqual(self.calls("go"), [])

    def test_named_integration_gate_next_to_a_full_gate_passes(self):
        self.change()
        r = self.recv(gates=(LOGGER_GATE, f"go --pkg ./internal/logger --integ --run {GREEN_TEST}"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(len(self.calls("go")), 2)


class GateArgumentTest(Base):
    def test_flags_recv_owns_or_that_break_the_invariant_are_tool_errors(self):
        self.change()
        overlay = self.tmp / "overlay.json"
        overlay.write_text("{}", encoding="utf-8")
        for bad in (f"{LOGGER_GATE} --tree /x", f"{LOGGER_GATE} --out /x", f"{LOGGER_GATE} --expect red",
                    f"{LOGGER_GATE} --tree=/x", f"{LOGGER_GATE} --out=/x", f"{LOGGER_GATE} --tre /x",
                    f"{LOGGER_GATE} --ou /x", f"{LOGGER_GATE} --exp red", f"{LOGGER_GATE} --overlay {overlay}",
                    f"{LOGGER_GATE} --help", "clean-docker", "", "bogus", "go --pkg ./internal/nope",
                    "fe --project nope", f"{LOGGER_GATE} 'unbalanced"):
            with self.subTest(gate=bad):
                self.assertToolError(self.recv(gates=(bad,)))
                self.assertEqual(self.calls("go"), [])

    def test_gate_is_required(self):
        self.change()
        self.assertToolError(self.recv(gates=()))

    def test_gate_validation_happens_before_the_first_gate_runs(self):
        self.change()
        r = self.recv(gates=(LOGGER_GATE, "fe --project frontend --include no/such.spec.ts"))
        self.assertToolError(r)
        self.assertEqual(self.calls("go"), [])


class VerdictTest(Base):
    def run_verdict(self, fixture, verdict):
        self.change()
        self.fake(go=go_replay(fixture))
        before = self.main_state()
        r = self.apply()
        self.assertRejected(r, "gate")
        self.assertIn(f"GATE go {verdict} ", r.stdout)
        self.assertNothingMerged(before)
        return r

    def test_red_is_not_merged_and_names_the_failed_tests(self):
        r = self.run_verdict("go_red", "RED")
        self.assertIn("TestZZFail", r.stdout)
        self.assertIn("TestZZSub/bad_one", r.stdout)

    def test_build_fail_is_not_merged(self):
        self.run_verdict("go_compile_error", "BUILD-FAIL")

    def test_no_tests_is_not_merged(self):
        self.run_verdict("go_no_tests", "NO-TESTS")

    def test_incomplete_is_not_merged(self):
        self.run_verdict("go_panic", "INCOMPLETE")

    def test_fe_red_is_not_merged(self):
        self.change(FE_FILE, "// changed\n")
        self.fake(npx=text_replay("fe_red"))
        before = self.main_state()
        self.assertRejected(self.apply(gates=("fe --project frontend",)), "gate")
        self.assertNothingMerged(before)

    def test_slot_timeout_is_not_merged_and_exits_75(self):
        self.change()
        self.env["DOCKERSLOT_SLOTS"] = "1"
        slots = self.tmp / "slots"
        slots.mkdir()
        fd = os.open(slots / "docker-0.lock", os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        before = self.main_state()
        r = self.apply(gates=("go --pkg ./internal/logger --integ --max-wait 0.2",))
        self.assertRejected(r, "gate", code=75)
        self.assertIn("GATE go SLOT-TIMEOUT ", r.stdout)
        self.assertEqual(self.calls("go"), [])
        self.assertNothingMerged(before)

    def test_red_known_passes_and_is_named_for_judgement(self):
        self.change()
        self.fake(go=go_replay("go_red"))
        known = self.tmp / "known.txt"
        known.write_text("TestZZFail\nTestZZSub/bad_one\n", encoding="utf-8")
        r = self.apply(gates=(f"go --pkg ./internal/logger --known-red {shlex.quote(str(known))}",))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("GATE go RED-KNOWN ", r.stdout)
        self.assertEqual((self.main / LOGGER_FILE).read_text(encoding="utf-8"),
                         (self.slice / LOGGER_FILE).read_text(encoding="utf-8"))
        hint = [x for x in r.stdout.splitlines() if "RED-KNOWN" in x and "GATE" not in x]
        self.assertTrue(hint, r.stdout)
        self.assertIn("TestZZFail", hint[0])

    def test_red_with_a_new_failure_beyond_the_known_list_is_not_merged(self):
        self.change()
        self.fake(go=go_replay("go_red"))
        known = self.tmp / "known.txt"
        known.write_text("TestZZFail\n", encoding="utf-8")
        before = self.main_state()
        r = self.apply(gates=(f"go --pkg ./internal/logger --known-red {shlex.quote(str(known))}",))
        self.assertRejected(r, "gate")
        self.assertIn("TestZZSub/bad_one", r.stdout)
        self.assertNothingMerged(before)

    def test_first_failing_gate_stops_the_later_ones(self):
        self.change()
        self.change(FE_FILE, "// changed\n")
        self.fake(go=go_replay("go_red"), npx=text_replay("fe_green"))
        r = self.recv(gates=(LOGGER_GATE, "fe --project frontend"))
        self.assertRejected(r, "gate")
        self.assertEqual(self.calls("npx"), [])
        self.assertIn(" gates=3 ", r.first)

    def test_gate_runs_against_the_slice_tree_with_recv_owned_out(self):
        self.change()
        r = self.recv()
        self.assertEqual(self.calls("go")[0]["cwd"], str(self.slice / "backend-golang"))
        out = pathlib.Path(re.search(r"GATE go GREEN .* out=(\S+)", r.stdout).group(1))
        self.assertEqual(out.parent, self.out_dir)
        self.assertTrue(out.exists())

    def test_every_gate_gets_its_own_line_and_its_own_output(self):
        self.change()
        r = self.recv(gates=(LOGGER_GATE, "go --pkg ./internal/logger --race"))
        outs = re.findall(r"GATE go GREEN .* out=(\S+)", r.stdout)
        self.assertEqual(len(set(outs)), 2, r.stdout)

    def test_tool_error_during_a_gate_exits_2_not_rejected(self):
        self.change()
        self.env["PATH"] = str(self.tmp / "nowhere") + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
        before = self.main_state()
        r = self.apply()
        self.assertToolError(r)
        self.assertNothingMerged(before)


class KnownRedPinTest(Base):
    """A slice cannot excuse its own failure by editing the list the gate reads from its tree."""
    LIST = "backend-golang/known-red-integration.txt"
    INTEG = "go --pkg ./internal/logger --integ"
    NAMES = "TestZZFail\nTestZZSub/bad_one\n"

    def setUp(self):
        super().setUp()
        self.change()
        self.fake(go=go_replay("go_red"))

    def in_baseline(self, text):
        for root in (self.main, self.snap, self.slice):
            self.write(root, self.LIST, text)

    def test_list_added_by_the_slice_does_not_excuse_its_red(self):
        self.change(self.LIST, self.NAMES)
        before = self.main_state()
        r = self.apply(gates=(self.INTEG,))
        self.assertRejected(r, "gate")
        self.assertIn("GATE go RED ", r.stdout)
        self.assertNothingMerged(before)

    def test_names_added_to_an_existing_list_do_not_excuse_a_red(self):
        self.in_baseline("TestZZFail\n")
        self.change(self.LIST, self.NAMES)
        before = self.main_state()
        r = self.apply(gates=(self.INTEG,))
        self.assertRejected(r, "gate")
        self.assertIn("TestZZSub/bad_one", r.stdout)
        self.assertNothingMerged(before)

    def test_unchanged_baseline_list_still_gives_red_known(self):
        self.in_baseline(self.NAMES)
        r = self.apply(gates=(self.INTEG,))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("GATE go RED-KNOWN ", r.stdout)

    def test_slice_shrinking_the_list_is_measured_against_the_old_list_and_named(self):
        self.in_baseline(self.NAMES + "TestGone\n")
        self.change(self.LIST, self.NAMES)
        r = self.recv(gates=(self.INTEG,))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("改了 known-red 名單", r.stdout)
        self.assertIn(self.LIST, r.stdout)

    def test_explicit_known_red_is_the_callers_decision(self):
        known = self.tmp / "known.txt"
        known.write_text(self.NAMES, encoding="utf-8")
        self.change(self.LIST, "TestZZFail\n")
        r = self.recv(gates=(f"{self.INTEG} --known-red {shlex.quote(str(known))}",))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(str(known), r.stdout)


class DriftTest(Base):
    def test_file_changed_during_the_gate_is_caught_and_not_merged(self):
        self.change()
        target = self.slice / LOGGER_FILE
        self.fake(go={**go_replay("go_green"), "shell": ["sh", "-c", f"echo 'package logger // late' > {target}"]})
        before = self.main_state()
        r = self.apply()
        self.assertRejected(r, "drift")
        self.assertIn(LOGGER_FILE, r.stdout)
        self.assertNothingMerged(before)

    def test_file_created_during_the_gate_is_caught(self):
        self.change()
        target = self.slice / "backend-golang/internal/logger/late.go"
        self.fake(go={**go_replay("go_green"), "shell": ["sh", "-c", f"echo 'package logger' > {target}"]})
        before = self.main_state()
        r = self.apply()
        self.assertRejected(r, "drift")
        self.assertIn("late.go", r.stdout)
        self.assertNothingMerged(before)

    def test_file_removed_during_the_gate_is_caught(self):
        self.change()
        self.change("backend-golang/internal/logger/extra.go", "package logger\n")
        target = self.slice / "backend-golang/internal/logger/extra.go"
        self.fake(go={**go_replay("go_green"), "shell": ["rm", str(target)]})
        r = self.recv()
        self.assertRejected(r, "drift")
        self.assertIn("extra.go", r.stdout)

    def test_drift_also_stops_a_dry_run_from_reading_ok(self):
        self.change()
        target = self.slice / LOGGER_FILE
        self.fake(go={**go_replay("go_green"), "shell": ["sh", "-c", f"echo x > {target}"]})
        self.assertRejected(self.recv(), "drift")

    def test_writes_under_tmp_are_not_drift(self):
        self.change()
        target = self.slice / "tmp/log.txt"
        self.fake(go={**go_replay("go_green"), "shell": ["sh", "-c", f"mkdir -p {target.parent} && echo x > {target}"]})
        self.assertEqual(self.recv().returncode, 0)

    def test_interrupt_after_the_last_gate_does_not_merge(self):
        self.change()
        before = self.main_state()
        real = gate.run_gate
        self.addCleanup(setattr, gate, "_stop_reason", None)

        def run_then_signal(argv):
            s = real(argv)
            if "--vet" not in argv:
                gate.request_stop("interrupted by SIGTERM")
            return s

        with mock.patch.object(gate, "run_gate", run_then_signal):
            rc, out = self.inproc(apply=True, progress=self.progress)
        self.assertEqual(rc, 1, out)
        self.assertIn("REJECTED:interrupted", out.splitlines()[0])
        self.assertNothingMerged(before)

    def test_sigterm_during_a_gate_stops_the_worker_and_merges_nothing(self):
        self.change()
        self.fake(go={**go_replay("go_green"), "sleep": 30})
        before = self.main_state()
        p = subprocess.Popen([sys.executable, str(RECV), *map(str, self.args(apply=True, progress=self.progress))],
                             env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 20
        while time.time() < deadline and not self.calls("go"):
            time.sleep(0.05)
        self.assertTrue(self.calls("go"))
        p.send_signal(signal.SIGTERM)
        out, _ = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("REJECTED:gate", out.splitlines()[0])
        self.assertIn("INCOMPLETE", out)
        self.assertNothingMerged(before)
        pid = self.calls("go")[0]["pid"]
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


class DryRunTest(Base):
    def test_dry_run_writes_nothing_to_main_or_progress_and_says_so(self):
        self.change()
        before = self.main_state()
        r = self.recv(progress=self.progress)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.first, r"^RECV S1 DRY-RUN-OK files=1 gates=3 out=\S+recv-S1\.out$")
        self.assertIn("未合回", r.stdout)
        self.assertEqual(len(self.calls("go")), 1)
        self.assertNothingMerged(before)

    def test_dry_run_with_the_default_out_dir_writes_nothing_to_main(self):
        self.change()
        before = self.main_state()
        r = self.recv(out_dir=False)
        self.assertEqual(r.returncode, 0, r.stdout)
        out = r.first.rsplit("out=", 1)[1]
        self.assertTrue(pathlib.Path(out).is_file())
        self.assertTrue(out.startswith(str(self.slice / "tmp")), out)
        self.assertNothingMerged(before)

    def test_dry_run_lists_what_only_the_main_session_can_judge_as_undone(self):
        self.change()
        r = self.recv()
        lines = r.stdout.splitlines()
        at = next(i for i, x in enumerate(lines) if x.startswith("待主 session 判斷"))
        for label in JUDGE_LABELS:
            self.assertTrue(any(label in x for x in lines[at:]), label)

    def test_rejected_run_does_not_print_ok_words(self):
        self.change()
        self.fake(go=go_replay("go_red"))
        r = self.recv()
        self.assertNotIn("DRY-RUN-OK", r.stdout)
        self.assertNotIn("APPLIED", r.stdout)

    def test_dry_run_does_not_run_the_board(self):
        self.change()
        marker = self.tmp / "board-ran"
        script = self.tools_with_board(f"open({str(marker)!r}, 'w').close()\n")
        r = self.recv(script=script, no_board=False)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(marker.exists())


class ApplyTest(Base):
    def test_apply_copies_and_appends_the_skeleton(self):
        self.change()
        old = self.progress.read_text(encoding="utf-8")
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.first, r"^RECV S1 APPLIED files=1 gates=3 out=\S+$")
        self.assertEqual((self.main / LOGGER_FILE).read_bytes(), (self.slice / LOGGER_FILE).read_bytes())
        text = self.progress.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(old))
        block = text[len(old):]
        self.assertIn("S1", block)
        self.assertIn("改動檔數：1", block)
        self.assertRegex(block, r"GATE go GREEN pass=5 ")
        self.assertIn(str(self.out_dir / "recv-S1.out"), block)

    def test_applied_still_says_the_judgement_is_not_done(self):
        self.change()
        r = self.apply()
        self.assertIn("待主 session 判斷", r.stdout)
        for label in JUDGE_LABELS:
            self.assertIn(label, r.stdout)

    def test_skeleton_judgement_lines_are_empty(self):
        self.change()
        self.write(self.slice, "tmp/S1-red-run.out", "red for the right reason\n")
        self.apply("--require", "tmp/S1-red-run.out")
        text = self.progress.read_text(encoding="utf-8")
        for label in JUDGE_LABELS:
            self.assertRegex(text, rf"(?m)^- {re.escape(label)}：$")
        self.assertNotIn("red for the right reason", text)

    def test_skeleton_has_no_markdown_table_the_board_could_take_for_the_stage_table(self):
        self.change()
        self.apply()
        self.assertNotRegex(self.progress.read_text(encoding="utf-8"), r"(?m)^\s*\|")

    def test_apply_requires_progress(self):
        self.change()
        before = self.main_state()
        self.assertToolError(self.recv(apply=True))
        self.assertEqual(self.calls("go"), [])
        self.assertNothingMerged(before)

    def test_missing_or_unwritable_progress_stops_before_the_gates(self):
        self.change()
        before = self.main_state()
        self.assertToolError(self.recv(apply=True, progress=self.tmp / "no/such/PROGRESS.md"))
        self.assertToolError(self.recv(apply=True, progress=self.tmp / "absent.md"))
        self.progress.chmod(0o444)
        self.addCleanup(self.progress.chmod, 0o644)
        self.assertToolError(self.recv(apply=True, progress=self.progress))
        self.assertEqual(self.calls("go"), [])
        self.progress.chmod(0o644)
        self.assertNothingMerged(before)

    def test_second_apply_is_rejected_by_merge_back(self):
        self.change()
        self.assertEqual(self.apply().returncode, 0)
        self.assertRejected(self.apply(), "preflight")

    def test_main_changed_during_the_gate_is_rejected_and_keeps_the_other_edit(self):
        self.change()
        target = self.main / LOGGER_FILE
        self.fake(go={**go_replay("go_green"), "shell": ["sh", "-c", f"echo other > {target}"]})
        old = self.progress.read_text(encoding="utf-8")
        r = self.apply()
        self.assertRejected(r, "merge")
        self.assertIn("main differs from snapshot", r.stdout)
        self.assertEqual(target.read_text(encoding="utf-8"), "other\n")
        self.assertEqual(self.progress.read_text(encoding="utf-8"), old)

    def test_allow_revert_with_a_label_other_than_the_slice_is_a_tool_error(self):
        self.change()
        self.assertToolError(self.recv("--allow-revert", f"S2:{LOGGER_FILE}"))
        self.assertToolError(self.recv("--allow-revert", "no-colon"))

    def test_allowed_revert_is_copied_and_named_for_judgement(self):
        for root in (self.main, self.snap):
            self.write(root, LOGGER_FILE, "reviewed change\n")
        r = self.apply("--allow-revert", f"S1:{LOGGER_FILE}")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.main / LOGGER_FILE).read_text(encoding="utf-8"), "package logger\n")
        self.assertRegex(r.stdout, rf"改回 HEAD.*{re.escape(LOGGER_FILE)}")
        self.assertRegex(self.progress.read_text(encoding="utf-8"), rf"改回 HEAD.*{re.escape(LOGGER_FILE)}")

    def test_unused_allow_revert_note_reaches_the_reader(self):
        self.change()
        r = self.recv("--allow-revert", f"S1:{TAG_FILE}")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(f"unused --allow-revert S1:{TAG_FILE}", r.stdout)


class AfterMergeTest(Base):
    def test_progress_failure_after_merge_exits_2_and_says_merged(self):
        self.change()
        self.addCleanup(self.progress.chmod, 0o644)
        self.fake(go={**go_replay("go_green"), "shell": ["chmod", "444", str(self.progress)]})
        r = self.apply()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("MERGED-BUT-FAILED:progress", r.first)
        self.assertIn("已合回，但 PROGRESS", r.stdout)
        self.assertEqual((self.main / LOGGER_FILE).read_bytes(), (self.slice / LOGGER_FILE).read_bytes())
        self.assertIn("改動檔數：1", self.detail())

    def test_board_failure_after_merge_exits_2_and_says_merged(self):
        self.change()
        script = self.tools_with_board("import sys\nprint('board exploded')\nsys.exit(1)\n")
        r = self.apply(script=script, no_board=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("MERGED-BUT-FAILED:board", r.first)
        self.assertIn("已合回，但 board", r.stdout)
        self.assertEqual((self.main / LOGGER_FILE).read_bytes(), (self.slice / LOGGER_FILE).read_bytes())
        self.assertIn("改動檔數：1", self.progress.read_text(encoding="utf-8"))
        self.assertIn("board exploded", self.detail())

    def test_board_runs_from_the_scripts_directory_in_the_root_after_apply(self):
        self.change()
        marker = self.tmp / "board-cwd"
        script = self.tools_with_board(f"import os\nopen({str(marker)!r}, 'w').write(os.getcwd())\n")
        r = self.apply(script=script, no_board=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), str(self.main))

    def test_no_board_skips_it(self):
        self.change()
        marker = self.tmp / "board-ran"
        script = self.tools_with_board(f"open({str(marker)!r}, 'w').close()\n")
        r = self.apply(script=script, no_board=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(marker.exists())

    def test_tree_written_between_gates_and_copy_fails_the_sha_check_after_merge(self):
        self.change()
        real = recv._merge_back

        def write_then_merge(a, *rest, **kw):
            if kw.get("apply"):
                self.change(text="package logger // written after the gates\n")
            return real(a, *rest, **kw)

        old = self.progress.read_text(encoding="utf-8")
        with mock.patch.object(recv, "_merge_back", write_then_merge):
            rc, out = self.inproc(apply=True, progress=self.progress)
        self.assertEqual(rc, 2, out)
        self.assertIn("MERGED-BUT-FAILED:verify", out.splitlines()[0])
        self.assertIn("已合回，但 sha", out)
        self.assertIn(LOGGER_FILE, out)
        self.assertEqual(self.progress.read_text(encoding="utf-8"), old)

    def test_merge_back_crash_midway_is_not_reported_as_a_clean_rejection(self):
        self.change()
        self.change(TAG_FILE, "package tag // x\n")
        target = self.main / TAG_FILE
        target.chmod(0o444)
        self.addCleanup(target.chmod, 0o644)
        r = self.apply(gates=("go --pkg ./internal/...",))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("MERGED-BUT-FAILED:merge", r.first)
        self.assertIn("1/2", r.stdout)
        self.assertIn(LOGGER_FILE, r.stdout)
        self.assertNotIn("已合回，但", r.stdout)

    def test_merge_back_crash_before_any_copy_is_a_tool_error(self):
        self.change()
        target = self.main / LOGGER_FILE
        target.chmod(0o444)
        self.addCleanup(target.chmod, 0o644)
        before = self.main_state()
        r = self.apply()
        self.assertToolError(r)
        self.assertNothingMerged(before)


class OutputTest(Base):
    def test_long_change_list_is_truncated_with_the_total_and_stays_within_30_lines(self):
        for i in range(40):
            self.change(f"backend-golang/internal/logger/f{i:02}.go", "package logger\n")
        r = self.recv()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertLessEqual(len(r.stdout.splitlines()), 30, r.stdout)
        self.assertIn("files=40", r.first)
        self.assertIn("40", r.stdout.split("\n", 1)[1])
        self.assertIn("f00.go", r.stdout)
        self.assertNotIn("f39.go", r.stdout)
        for i in range(40):
            self.assertIn(f"f{i:02}.go", self.detail())

    def test_twenty_files_are_listed_in_full(self):
        for i in range(20):
            self.change(f"backend-golang/internal/logger/f{i:02}.go", "package logger\n")
        r = self.recv()
        self.assertIn("f19.go", r.stdout)
        self.assertLessEqual(len(r.stdout.splitlines()), 30, r.stdout)

    def test_many_rejection_reasons_stay_within_30_lines_and_the_detail_has_them_all(self):
        for i in range(40):
            self.change(f"backend-golang/internal/tag/f{i:02}.go", "package tag\n")
        r = self.recv(scope=("docs/**",))
        self.assertRejected(r, "scope")
        self.assertLessEqual(len(r.stdout.splitlines()), 30, r.stdout)
        for i in range(40):
            self.assertIn(f"f{i:02}.go", self.detail())

    def test_many_gates_with_failures_stay_within_30_lines(self):
        self.change()
        self.fake(go=go_replay("go_green"))
        gates = [f"go --pkg ./internal/logger --label g{i}" for i in range(20)]
        r = self.recv(gates=gates)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("gates=22", r.first)
        self.assertLessEqual(len(r.stdout.splitlines()), 30, r.stdout)
        for i in range(1, 21):
            self.assertIn(f"gate-S1-{i}-go.out", self.detail())

    def test_non_ascii_path_survives_to_the_list_and_the_merge(self):
        self.change("docs/中文 檔.md", "x\n")
        self.change()
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("docs/中文 檔.md", r.stdout)
        self.assertTrue((self.main / "docs/中文 檔.md").exists())


class ToolErrorTest(Base):
    def test_root_is_required(self):
        self.change()
        args = [str(x) for x in self.args()]
        i = args.index("--root")
        del args[i:i + 2]
        # WHY: from inside a checkout, where a cwd fallback would have something to resolve to and still merge.
        r = subprocess.run([sys.executable, str(RECV), *args, "--apply", "--progress", str(self.progress)], env=self.env,
                           capture_output=True, text=True, timeout=90, cwd=self.main)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("--root", r.stdout)
        self.assertEqual(g(self.main, "status", "--porcelain"), "")

    def test_root_must_be_a_checkout_toplevel(self):
        self.change()
        args = [str(x) for x in self.args()]
        args[args.index("--root") + 1] = str(self.main / "docs")
        self.assertToolError(self.fresh(args))

    def test_tree_must_exist(self):
        args = [str(x) for x in self.args()]
        args[args.index("--tree") + 1] = str(self.tmp / "gone")
        self.assertToolError(self.fresh(args))

    def test_root_equal_to_tree_is_a_tool_error(self):
        self.change()
        args = [str(x) for x in self.args()]
        args[args.index("--root") + 1] = str(self.slice)
        self.assertToolError(self.fresh(args))

    def test_tree_equal_to_snapshot_is_a_tool_error(self):
        self.change()
        args = [str(x) for x in self.args()]
        args[args.index("--snapshot") + 1] = str(self.slice)
        self.assertToolError(self.fresh(args))

    def test_slice_name_cannot_escape_the_out_dir(self):
        args = [str(x) for x in self.args()]
        args[args.index("--slice") + 1] = "../S1"
        self.assertToolError(self.fresh(args))

    def test_out_dir_that_would_dirty_a_tree_is_a_tool_error(self):
        self.change()
        before = self.main_state()
        args = [str(x) for x in self.args()]
        args[args.index("--out-dir") + 1] = str(self.main / "docs/out")
        self.assertToolError(self.fresh(args))
        args[args.index("--out-dir") + 1] = str(self.slice / "backend-golang/out")
        self.assertToolError(self.fresh(args))
        self.assertNothingMerged(before)

    def test_out_dir_under_a_tmp_dir_of_the_root_is_accepted(self):
        self.change()
        out = self.main / "tmp/recv-out"
        args = [str(x) for x in self.args()]
        args[args.index("--out-dir") + 1] = str(out)
        r = self.fresh(args)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue((out / "recv-S1.out").is_file())

    def test_internal_crash_exits_2_not_1(self):
        self.change()
        with mock.patch.object(recv, "check_scope", side_effect=RuntimeError("boom")):
            rc, out = self.inproc()
        self.assertEqual(rc, 2, out)
        self.assertIn("TOOL-ERROR", out.splitlines()[0])
        self.assertIn("boom", out)

    def test_crash_after_merge_says_merged(self):
        self.change()
        with mock.patch.object(recv, "append_progress", side_effect=RuntimeError("boom")):
            rc, out = self.inproc(apply=True, progress=self.progress)
        self.assertEqual(rc, 2, out)
        self.assertIn("MERGED-BUT-FAILED:progress", out.splitlines()[0])
        self.assertIn("已合回", out)

    def fresh(self, args):
        r = self.run_tool(RECV, *args, timeout=90)
        r.first = r.stdout.splitlines()[0] if r.stdout.strip() else ""
        return r


class PendingMutationTest(Base):
    """F1: a tree mutate.py never restored holds a mutant the gates cannot see."""

    def test_pending_mutation_is_refused_before_any_other_step(self):
        self.change()
        self.pending_pointer().write_text(str(self.tmp / "journal"), encoding="utf-8")
        before = self.main_state()
        r = self.apply("--require", "tmp/never-delivered.out")
        self.assertRejected(r, "preflight")
        self.assertIn(f"mutate.py --tree {self.slice} --recover", r.stdout)
        self.assertNotIn("never-delivered", r.stdout)
        self.assertEqual(self.calls(), [])
        self.assertNothingMerged(before)

    def test_mutate_killed_midway_is_not_merged_and_recover_clears_the_way(self):
        self.change(FE_FILE, "// a ORIGINAL\n")
        manifest = self.tmp / "m.txt"
        manifest.write_text(f"=== M x\ntest: fe --project frontend\nfile: {FE_FILE}\n<<<<<<< old\nORIGINAL\n=======\n"
                            "MUTANT\n>>>>>>> new\n", encoding="utf-8")
        self.fake(npx={"sleep": 30, **text_replay("fe_green")})
        p = subprocess.Popen([sys.executable, str(MUTATE), "--tree", str(self.slice), "--manifest", str(manifest),
                              "--out", str(self.tmp / "mut.out"), "--no-baseline"], env=self.env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 20
        while time.time() < deadline and not self.calls("npx"):
            time.sleep(0.05)
        self.assertIn("MUTANT", (self.slice / FE_FILE).read_text(encoding="utf-8"))
        p.kill()
        p.wait()
        for call in self.calls():
            with contextlib.suppress(OSError):
                os.kill(call["pid"], signal.SIGKILL)
        self.fake(npx=text_replay("fe_green"))
        before = self.main_state()

        r = self.apply(gates=("fe --project frontend",))
        self.assertRejected(r, "preflight")
        self.assertNothingMerged(before)

        rec = self.run_tool(MUTATE, "--tree", self.slice, "--recover")
        self.assertEqual(rec.returncode, 0, rec.stdout + rec.stderr)
        r = self.apply(gates=("fe --project frontend",))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.main / FE_FILE).read_text(encoding="utf-8"), "// a ORIGINAL\n")


class AutoVetTest(Base):
    """F3, F15: any .go change also has to compile for the packages depending on it and behind every build tag."""
    BOTH = [("", ["./..."]), ("integration", ["./..."])]

    def tagged(self, constraint, rel="backend-golang/internal/logger/probe.go"):
        self.change(rel, f"//go:build {constraint}\n\npackage logger\n")

    def test_go_change_runs_both_repo_wide_vets_and_counts_them(self):
        self.change()
        r = self.recv()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.vets(), self.BOTH)
        self.assertIn(" gates=3 ", r.first)
        self.assertEqual(len([x for x in r.stdout.splitlines() if x.startswith("gate ")]), 3, r.stdout)

    def test_a_change_without_go_files_runs_no_vet(self):
        self.change(FE_FILE, "// changed\n")
        self.fake(npx=text_replay("fe_green"))
        r = self.recv(gates=("fe --project frontend",))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.vets(), [])
        self.assertIn(" gates=1 ", r.first)

    def test_a_failing_vet_is_rejected_at_the_gate_step_and_merges_nothing(self):
        for key in ("go-vet", "go-vet:integration"):
            with self.subTest(key=key):
                self.reset_slice()
                self.cfg.pop("go-vet", None)
                self.cfg.pop("go-vet:integration", None)
                self.change()
                self.fake(**{key: {"text": "internal/high/high.go:3:23: not enough arguments\n", "rc": 1}})
                before = self.main_state()
                r = self.apply()
                self.assertRejected(r, "gate")
                self.assertIn("GATE go-vet BUILD-FAIL", r.stdout)
                self.assertIn("not enough arguments", r.stdout)
                self.assertNothingMerged(before)

    def test_a_vet_failure_stops_before_the_callers_gates(self):
        self.change()
        self.fake(**{"go-vet": {"text": "boom\n", "rc": 1}})
        self.assertRejected(self.recv(), "gate")
        self.assertEqual(self.calls("go"), [])

    def test_vets_are_recorded_in_the_skeleton(self):
        self.change()
        self.assertEqual(self.apply().returncode, 0)
        text = self.progress.read_text(encoding="utf-8")
        self.assertIn("go --vet --pkg ./...", text)
        self.assertIn("go --vet --tags integration --pkg ./...", text)

    def test_another_build_tag_gets_a_vet_on_that_package(self):
        self.tagged("smoke")
        self.fake(**{"go-vet:smoke": {"text": "probe.go:3:1: syntax error\n", "rc": 1}})
        before = self.main_state()
        r = self.apply()
        self.assertRejected(r, "gate")
        self.assertIn("syntax error", r.stdout)
        self.assertEqual(self.vets(), self.BOTH + [("smoke", ["./internal/logger"])])
        self.assertNothingMerged(before)

    def test_each_tag_of_a_conjunction_and_the_conjunction_itself_are_vetted(self):
        # WHY: `a && b` compiles under neither tag alone, so one gate per tag would never touch this file.
        self.tagged("videoprobe && videoprobe_s1")
        self.assertEqual(self.recv().returncode, 0)
        self.assertEqual(self.vets(), self.BOTH + [("videoprobe", ["./internal/logger"]),
                                                   ("videoprobe,videoprobe_s1", ["./internal/logger"]),
                                                   ("videoprobe_s1", ["./internal/logger"])])

    def test_integration_next_to_another_tag_is_vetted_together(self):
        self.tagged("integration && smoke")
        self.assertEqual(self.recv().returncode, 0)
        self.assertEqual(self.vets(), self.BOTH + [("integration,smoke", ["./internal/logger"]),
                                                   ("smoke", ["./internal/logger"])])

    def test_negated_platform_and_convention_tags_get_no_vet(self):
        for constraint in ("!smoke", "linux", "amd64", "go1.22", "cgo", "unix", "ignore", "integration",
                           "!smoke && linux && go1.22"):
            with self.subTest(constraint=constraint):
                self.reset_slice()
                self.log_path.unlink(missing_ok=True)
                self.tagged(constraint)
                self.assertEqual(self.recv().returncode, 0)
                self.assertEqual(self.vets(), self.BOTH)

    def test_a_tag_on_a_second_package_is_vetted_there(self):
        self.tagged("smoke")
        self.tagged("videoprobe", rel="backend-golang/internal/tag/probe.go")
        self.assertEqual(self.recv(gates=("go --pkg ./internal/...",)).returncode, 0)
        self.assertEqual(self.vets(), self.BOTH + [("smoke", ["./internal/logger"]),
                                                   ("videoprobe", ["./internal/tag"])])

    def test_a_build_line_after_the_package_clause_is_not_a_constraint(self):
        self.change("backend-golang/internal/logger/probe.go", "package logger\n\n//go:build smoke\n")
        self.assertEqual(self.recv().returncode, 0)
        self.assertEqual(self.vets(), self.BOTH)

    def test_a_tagged_file_go_ignores_gets_no_vet(self):
        self.change("backend-golang/internal/logger/testdata/fix.go", "//go:build smoke\n\npackage fix\n")
        self.assertEqual(self.recv().returncode, 0)
        self.assertEqual(self.vets(), self.BOTH)

    def test_a_file_for_another_platform_is_named_because_no_vet_compiles_it(self):
        for rel, text in (("backend-golang/internal/logger/x_linux.go", "package logger\n"),
                          ("backend-golang/internal/logger/x_windows_amd64_test.go", "package logger\n"),
                          ("backend-golang/internal/logger/y.go", "//go:build linux\n\npackage logger\n")):
            with self.subTest(rel=rel):
                self.reset_slice()
                self.change(rel, text)
                r = self.recv()
                self.assertEqual(r.returncode, 0, r.stdout)
                self.assertRegex(r.stdout, rf"平台限定.*{re.escape(rel)}")

    def test_a_plain_file_is_not_named_as_platform_only(self):
        self.change("backend-golang/internal/logger/linux.go", "package logger\n")
        self.assertNotIn("平台限定", self.recv().stdout)


class RootMovedTest(Base):
    """F4: a commit on main after the snapshot means this combination never went through a gate."""

    def move_root(self):
        self.write(self.main, TAG_FILE, "package tag // moved\n")
        g(self.main, "add", ".")
        g(self.main, "commit", "-qm", "moved")

    def test_moved_root_is_refused_before_any_gate(self):
        self.change()
        self.move_root()
        before = self.main_state()
        r = self.apply()
        self.assertRejected(r, "preflight")
        self.assertIn("主線動了", r.stdout)
        self.assertIn("沒被閘過", r.stdout)
        self.assertIn("--allow-root-moved", r.stdout)
        self.assertEqual(self.calls(), [])
        self.assertNothingMerged(before)

    def test_a_reason_lets_it_through_and_is_recorded(self):
        self.change()
        self.move_root()
        reason = "主線只動了不相干的文件"
        r = self.apply("--allow-root-moved", reason)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("APPLIED", r.first)
        self.assertIn(reason, r.stdout)
        self.assertIn(reason, self.detail())
        self.assertIn(reason, self.progress.read_text(encoding="utf-8"))

    def test_reason_from_a_file(self):
        self.change()
        self.move_root()
        f = self.tmp / "why.txt"
        f.write_text("有 | 的理由\n", encoding="utf-8")
        r = self.recv("--allow-root-moved-file", f)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("有 | 的理由", r.stdout)

    def test_blank_reason_or_both_forms_are_tool_errors(self):
        self.change()
        self.move_root()
        f = self.tmp / "why.txt"
        f.write_text("x\n", encoding="utf-8")
        self.assertToolError(self.recv("--allow-root-moved", "  "))
        self.assertToolError(self.recv("--allow-root-moved", "x", "--allow-root-moved-file", f))
        self.assertToolError(self.recv("--allow-root-moved-file", self.tmp / "missing.txt"))

    def test_uncommitted_difference_in_the_root_is_not_a_moved_root(self):
        self.change()
        self.write(self.main, TAG_FILE, "package tag // another slice merged back\n")
        r = self.recv()
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_unused_reason_is_named_and_not_recorded_as_the_reason(self):
        self.change()
        r = self.apply("--allow-root-moved", "沒動也寫理由")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("--allow-root-moved 沒用到", r.stdout)
        self.assertNotIn("--allow-root-moved 理由", self.progress.read_text(encoding="utf-8"))


class SkipWorktreeTest(Base):
    """F18: an index flag hides a weakened file from the status union, so the gate and main disagree."""

    def assert_refused(self, *flags):
        self.change()
        self.change(TAG_FILE, "package tag // weakened\n")
        for flag in flags:
            g(self.slice, "update-index", flag, TAG_FILE)
        self.addCleanup(self.clear_flags)
        before = self.main_state()
        r = self.apply()
        self.assertRejected(r, "preflight")
        self.assertIn(TAG_FILE, r.stdout)
        self.assertEqual(self.calls(), [])
        self.assertNothingMerged(before)

    def clear_flags(self):
        g(self.slice, "update-index", "--no-skip-worktree", "--no-assume-unchanged", TAG_FILE)

    def test_skip_worktree_is_refused(self):
        self.assert_refused("--skip-worktree")

    def test_assume_unchanged_is_refused(self):
        self.assert_refused("--assume-unchanged")

    def test_both_flags_together_are_refused(self):
        self.assert_refused("--skip-worktree", "--assume-unchanged")


class ScopeProbeTest(Base):
    """F25: scopes that admit every file of some depth or extension are no scope."""

    def test_scopes_covering_every_path_of_a_depth_or_extension_are_tool_errors(self):
        self.change()
        for scope in ("**/*.go", "*/*/*/*", "*/*", "*", "*.go", "**/*_test.go", "**/*.spec.ts", "**/*.py", "**/*.md",
                      "*/*/*/*.go", "[a-z]*/**", "*/**", "*/*/*/*/*/*"):
            with self.subTest(scope=scope):
                self.assertToolError(self.recv(scope=(scope,)))

    def test_rooted_globs_are_still_scopes(self):
        self.change()
        for scope in ("backend-golang/**/*.go", "backend-golang/*/*/*", "backend-golang/internal/*/logger.go",
                      "*/internal/logger/logger.go"):
            with self.subTest(scope=scope):
                self.assertEqual(self.recv(scope=(scope,)).returncode, 0)


class CoverageRulesTest(Base):
    """F5: files that shape many packages need a gate that reaches them."""
    # (path, gates that are not enough, gate sets that are)
    RULES = (
        ("backend-golang/go.mod", (LOGGER_GATE, "go --pkg ./internal/...", "go --pkg ./... --vet"),
         (("go --pkg ./...",), ("go --all",))),
        ("backend-golang/go.sum", (LOGGER_GATE,), (("go --pkg ./...",),)),
        ("backend-golang/internal/tag/tmpl.txt", (LOGGER_GATE, "go --pkg ./internal/tag --vet"),
         (("go --pkg ./internal/...",),)),
        ("backend-golang/internal/logger/testdata/case.json", ("go --pkg ./internal/tag --vet",), ((LOGGER_GATE,),)),
        ("db/migrations/1_x.sql", (LOGGER_GATE, "go --pkg ./internal/logger --vet"),
         (("go --integ --pkg ./internal/logger",), ("go --all",))),
        ("db/migrations/sub/1_x.sql", (LOGGER_GATE,), (("go --integ --pkg ./internal/logger",),)),
        ("db/queries/x.sql", (LOGGER_GATE,), (("go --integ --pkg ./internal/logger",),)),
        ("backend-golang/db/queries/x.sql", (LOGGER_GATE,), (("go --integ --pkg ./internal/logger",),)),
        ("angular-workspace/package.json", ("fe --project frontend", "fe --project admin", LOGGER_GATE),
         (("fe --project frontend", "fe --project admin"),)),
        ("angular-workspace/pnpm-lock.yaml", ("fe --project frontend",), (("fe --project frontend", "fe --project admin"),)),
        ("angular-workspace/angular.json", ("fe --project admin",), (("fe --project frontend", "fe --project admin"),)),
        ("angular-workspace/tsconfig.json", ("fe --project admin",), (("fe --project frontend", "fe --project admin"),)),
        ("angular-workspace/tsconfig.app.json", ("fe --project admin",), (("fe --project frontend", "fe --project admin"),)),
        ("ai-service/pyproject.toml", (LOGGER_GATE, "ai --kind ruff"), (("ai",),)),
        ("ai-service/uv.lock", (LOGGER_GATE,), (("ai",),)),
        ("ai-service/prompts/a.txt", (LOGGER_GATE,), (("ai",),)),
    )

    def setUp(self):
        super().setUp()
        self.fake(npx=text_replay("fe_green"), docker={"run": text_replay("ai_green")})
        self.write(self.main, "ai-service/.venv/pyvenv.cfg", "home = /usr/local/bin\n")

    def test_each_rule_refuses_without_its_gate_and_accepts_with_it(self):
        for path, short, enough in self.RULES:
            for gate_arg in short:
                with self.subTest(path=path, gate=gate_arg):
                    self.reset_slice()
                    self.change(path, "changed\n")
                    r = self.recv(gates=(gate_arg,), scope=(path,))
                    self.assertRejected(r, "coverage")
                    self.assertIn(path, r.stdout)
            for gates in enough:
                with self.subTest(path=path, gates=gates):
                    self.reset_slice()
                    self.change(path, "changed\n")
                    r = self.recv(gates=gates, scope=(path,))
                    self.assertEqual(r.returncode, 0, r.stdout)

    def test_allow_narrow_releases_each_rule_and_names_the_file(self):
        for path, short, _ in self.RULES:
            with self.subTest(path=path):
                self.reset_slice()
                self.change(path, "changed\n")
                r = self.recv("--allow-narrow", "看過了", gates=(short[0],), scope=(path,))
                self.assertEqual(r.returncode, 0, r.stdout)
                self.assertRegex(r.stdout, rf"--allow-narrow 放行.*{re.escape(path)}")

    def test_go_mod_needs_every_package_not_just_the_root_one(self):
        # WHY: this fixture has .go files at the module root, so without its own rule go.mod would pass as a root-package file.
        self.change("backend-golang/go.mod", "changed\n")
        r = self.recv(gates=("go --pkg .",), scope=("backend-golang/go.mod",))
        self.assertRejected(r, "coverage")
        self.assertIn("backend-golang/go.mod needs go --pkg ./...", r.stdout)

    def test_fe_include_does_not_satisfy_a_workspace_file(self):
        path = "angular-workspace/package.json"
        self.change(path, "changed\n")
        r = self.recv("--allow-narrow", "x", gates=(f"fe --project frontend --include {FE_SPEC}", "fe --project admin"),
                      scope=(path,))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.reset_slice()
        self.change(path, "changed\n")
        self.assertRejected(self.recv(gates=(f"fe --project frontend --include {FE_SPEC}", "fe --project admin"),
                                      scope=(path,)), "coverage")

    def test_python_source_stays_refused_even_with_allow_narrow(self):
        self.change(AI_FILE, "# changed\n")
        self.assertRejected(self.recv("--allow-narrow", "x"), "coverage")

    def test_documents_need_no_gate_and_no_hint(self):
        for path in ("docs/x.txt", "README.md", "ai-service/README.md", "backend-golang/internal/tag/NOTES.md",
                     ".claude/rules/a.txt", "angular-workspace/projects/frontend/src/a.md"):
            with self.subTest(path=path):
                self.reset_slice()
                self.change(path, "changed\n")
                r = self.recv(scope=(path,))
                self.assertEqual(r.returncode, 0, r.stdout)
                self.assertNotIn("無閘規則", r.stdout)

    def test_other_files_pass_and_are_named(self):
        for path in ("ops/deploy.sh", "scripts/a.py", "justfile", "ops/Dockerfile", "angular-workspace/eslint.config.js",
                     "backend-golang/internal/empty/notes.txt", "backend-golang/known-red-integration.txt"):
            with self.subTest(path=path):
                self.reset_slice()
                self.change(path, "changed\n")
                r = self.recv(scope=(path,))
                self.assertEqual(r.returncode, 0, r.stdout)
                self.assertRegex(r.stdout, rf"無閘規則.*{re.escape(path)}")

    def test_named_files_survive_a_crowded_report(self):
        for i in range(40):
            self.change(f"ops/f{i:02}.sh", "x\n")
        self.change()
        extras = [x for i in range(6) for x in ("--allow-revert", f"S1:unused{i}")]
        r = self.recv(*extras, gates=[f"{LOGGER_GATE} --label g{i}" for i in range(20)], scope=("ops/**", "backend-golang/**"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertLessEqual(len(r.stdout.splitlines()), 30, r.stdout)
        hint = [x for x in r.stdout.splitlines() if "無閘規則" in x]
        self.assertEqual(len(hint), 1, r.stdout)
        self.assertIn("ops/f39.sh", hint[0])

    def test_named_files_still_show_when_the_slice_is_refused(self):
        self.change("ops/deploy.sh", "x\n")
        self.change()
        r = self.recv(gates=("go --pkg ./internal/tag --vet",), scope=("ops/**", "backend-golang/**"))
        self.assertRejected(r, "coverage")
        self.assertRegex(r.stdout, r"無閘規則.*ops/deploy\.sh")

    def test_every_reason_survives_a_crowded_refusal(self):
        for i in range(15):
            self.change(f"backend-golang/internal/p{i:02}/a.go", f"package p{i:02}\n")
        self.change("backend-golang/go.mod", "changed\n")
        r = self.recv(scope=("backend-golang/**",))
        self.assertRejected(r, "coverage")
        self.assertLessEqual(len(r.stdout.splitlines()), 30, r.stdout)
        self.assertIn("backend-golang/go.mod needs", r.stdout)
        self.assertIn("internal/p14/a.go needs", r.stdout)


class UntestedPackageTest(Base):
    """F10: covering a package by name is not running a test in it. A hint, because such packages exist."""
    MODULE = "module deepweave/backend-golang\n\ngo 1.22\n"

    def setUp(self):
        super().setUp()
        self.in_all_trees("backend-golang/go.mod", self.MODULE)

    def test_changed_package_nothing_ran_in_is_named_and_passes(self):
        self.change(TAG_FILE, "package tag // x\n")
        r = self.recv(gates=("go --pkg ./internal/...",))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("這包沒有測試跑到：./internal/tag", r.stdout)

    def test_package_with_tests_is_not_named(self):
        self.change()
        self.change(TAG_FILE, "package tag // x\n")
        r = self.recv(gates=("go --pkg ./internal/...",))
        hint = [x for x in r.stdout.splitlines() if "這包沒有測試跑到" in x]
        self.assertEqual(len(hint), 1, r.stdout)
        self.assertNotIn("./internal/logger", hint[0])

    def test_package_with_tests_raises_nothing(self):
        self.change()
        self.assertNotIn("這包沒有測試跑到", self.recv().stdout)

    def test_a_vet_gate_never_counts_as_a_test(self):
        self.change(TAG_FILE, "package tag // x\n")
        r = self.recv(gates=("go --pkg ./internal/...", "go --pkg ./internal/tag --build"))
        self.assertIn("這包沒有測試跑到：./internal/tag", r.stdout)

    def test_a_second_gate_that_runs_the_package_clears_it(self):
        self.change()
        real = gate.run_gate

        def fake_counts(argv):
            s = real(argv)
            if "--race" in argv:
                s["packages"] = {"./internal/logger": 3}
            elif s["packages"] is not None:
                s["packages"] = {"./internal/logger": 0}
            return s

        with mock.patch.object(gate, "run_gate", fake_counts):
            rc, out = self.inproc(gates=(LOGGER_GATE, f"{LOGGER_GATE} --race"))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("這包沒有測試跑到", out)

    def test_a_later_gate_that_ran_nothing_does_not_undo_an_earlier_one(self):
        self.change()
        real = gate.run_gate

        def fake_counts(argv):
            s = real(argv)
            if s["packages"] is not None:
                s["packages"] = {"./internal/logger": 0 if "--race" in argv else 3}
            return s

        with mock.patch.object(gate, "run_gate", fake_counts):
            rc, out = self.inproc(gates=(LOGGER_GATE, f"{LOGGER_GATE} --race"))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("這包沒有測試跑到", out)

    def test_it_survives_a_crowded_report_and_reaches_the_skeleton(self):
        for i in range(40):
            self.change(f"backend-golang/internal/tag/f{i:02}.go", "package tag\n")
        extras = [x for i in range(6) for x in ("--allow-revert", f"S1:unused{i}")]
        r = self.apply(*extras, gates=[f"go --pkg ./internal/... --label g{i}" for i in range(20)])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertLessEqual(len(r.stdout.splitlines()), 30, r.stdout)
        self.assertIn("這包沒有測試跑到：./internal/tag", r.stdout)
        self.assertIn("這包沒有測試跑到：./internal/tag", self.progress.read_text(encoding="utf-8"))

    def test_an_unmappable_module_reads_as_not_run(self):
        self.in_all_trees("backend-golang/go.mod", "garbage\n")
        self.change()
        self.assertIn("這包沒有測試跑到：./internal/logger", self.recv().stdout)


class MergeListTest(Base):
    """F16: what gets copied is the list the drift check passed, not a fresh look at the tree."""

    def test_file_appearing_after_the_drift_check_stops_the_merge(self):
        self.change()
        real = recv.check_drift

        def racing(a, before):
            real(a, before)
            self.write(self.slice, "secrets/out_of_scope.txt", "landed\n")

        before = self.main_state()
        old = self.progress.read_text(encoding="utf-8")
        with mock.patch.object(recv, "check_drift", racing):
            rc, out = self.inproc(apply=True, progress=self.progress)
        self.assertEqual(rc, 1, out)
        self.assertIn("REJECTED:merge", out.splitlines()[0])
        self.assertIn("secrets/out_of_scope.txt", out)
        self.assertFalse((self.main / "secrets/out_of_scope.txt").exists())
        self.assertNothingMerged(before)
        self.assertEqual(self.progress.read_text(encoding="utf-8"), old)

    def test_the_dry_run_does_not_need_a_list(self):
        self.change()
        self.assertEqual(self.recv().returncode, 0)


class TestWashingTest(Base):
    """F17: a slice can hide its breakage by skipping the tests that catch it."""
    GO_TEST = "backend-golang/internal/logger/a_test.go"
    SPEC = "angular-workspace/projects/frontend/src/app/x.spec.ts"
    PY_TEST = "ai-service/tests/test_x.py"
    PY_TEST2 = "ai-service/app/x_test.py"

    def test_every_new_skip_is_refused_with_file_and_line(self):
        for path, line in ((self.GO_TEST, '\tt.Skip("later")'), (self.GO_TEST, '\tt.Skipf("later %d", 1)'),
                           (self.GO_TEST, "\tt.SkipNow()"), (self.SPEC, "  it.skip('x', () => {});"),
                           (self.SPEC, "  describe.skip('x', () => {});"), (self.SPEC, "  it.only('x', () => {});"),
                           (self.SPEC, "  describe.only('x', () => {});"), (self.SPEC, "  xit('x', () => {});"),
                           (self.SPEC, "  xdescribe('x', () => {});"), (self.SPEC, "  it.skipIf(true)('x', () => {});"),
                           (self.PY_TEST, '@pytest.mark.skip(reason="x")'), (self.PY_TEST, '@pytest.mark.skipif(True, reason="x")'),
                           (self.PY_TEST, '    pytest.skip("x")'), (self.PY_TEST2, '    pytest.skip("x")'),
                           (self.PY_TEST, '@pytest.mark.xfail(reason="x")')):
            with self.subTest(path=path, line=line):
                self.reset_slice()
                self.change()
                self.change(path, f"first\n{line}\n")
                before = self.main_state()
                r = self.apply()
                self.assertRejected(r, "coverage")
                self.assertIn(f"{path}:2: {line.strip()}", r.stdout)
                self.assertNothingMerged(before)

    def test_it_is_refused_with_the_gate_otherwise_covering_the_change(self):
        self.change(self.GO_TEST, 'package logger\nfunc TestA(t *testing.T) { t.Skip("later") }\n')
        r = self.recv()
        self.assertRejected(r, "coverage")
        self.assertEqual(self.calls("go"), [])

    def test_a_skip_already_in_the_snapshot_is_not_new(self):
        self.in_all_trees(self.GO_TEST, 'package logger\n\tt.Skip("old")\n')
        self.change(self.GO_TEST, 'package logger\n// more\n\tt.Skip("old")\n')
        self.assertEqual(self.recv().returncode, 0)

    def test_moving_or_reindenting_a_skip_is_not_new(self):
        self.in_all_trees(self.GO_TEST, 'package logger\n\tt.Skip("old")\n// tail\n')
        self.change(self.GO_TEST, 'package logger\n// tail\n\t\tt.Skip("old")\n')
        self.assertEqual(self.recv().returncode, 0)

    def test_a_second_copy_of_an_existing_skip_is_new(self):
        self.in_all_trees(self.GO_TEST, 'package logger\n\tt.Skip("old")\n')
        self.change(self.GO_TEST, 'package logger\n\tt.Skip("old")\n\tt.Skip("old")\n')
        r = self.recv()
        self.assertRejected(r, "coverage")
        self.assertIn(f"{self.GO_TEST}:3:", r.stdout)

    def test_look_alikes_and_non_test_files_are_not_skips(self):
        for path, text in ((self.SPEC, "process.exit(1);\n"), (self.SPEC, "commit(x);\n"),
                           ("backend-golang/internal/logger/a.go", 'package logger\n// t.Skip("x")\n'),
                           ("angular-workspace/projects/frontend/src/app/x.ts", "it.skip('x');\n")):
            with self.subTest(path=path, text=text):
                self.reset_slice()
                self.change()
                self.change(path, text)
                r = self.recv(gates=(LOGGER_GATE, "fe --project frontend"))
                self.assertNotEqual(r.first.split()[2], "REJECTED:coverage", r.stdout)

    def test_allow_narrow_releases_it_and_names_the_lines(self):
        self.change(self.GO_TEST, 'package logger\n\tt.Skip("x")\n')
        r = self.apply("--allow-narrow", "暫時略過，看過了")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.stdout, rf'新增的略過.*{re.escape(self.GO_TEST)}:2: t\.Skip\("x"\)')
        self.assertRegex(self.progress.read_text(encoding="utf-8"), rf"新增的略過.*{re.escape(self.GO_TEST)}")

    def test_changed_test_files_are_listed(self):
        self.change()
        self.change(self.GO_TEST, "package logger\n")
        r = self.recv()
        self.assertEqual(r.returncode, 0, r.stdout)
        hint = [x for x in r.stdout.splitlines() if "改到的測試檔" in x]
        self.assertEqual(len(hint), 1, r.stdout)
        self.assertIn("改到的測試檔 1", hint[0])
        self.assertIn(self.GO_TEST, hint[0])
        self.assertNotIn(LOGGER_FILE, hint[0])

    def test_no_changed_test_file_is_said_out_loud(self):
        self.change()
        self.assertIn("改到的測試檔 0", self.recv().stdout)


@unittest.skipUnless(shutil.which("go"), "needs the real go tool")
class RealGoTest(Base):
    """The vets above, against the go tool itself: a fake cannot prove what `go vet -tags` compiles."""

    def setUp(self):
        super().setUp()
        (self.bin / "go").unlink()
        files = {
            "backend-golang/go.mod": "module x\n\ngo 1.22\n",
            "backend-golang/main.go": "package main\n\nfunc main() {}\n",
            "backend-golang/internal/low/low.go": "package low\n\nfunc F() int { return 1 }\n",
            "backend-golang/internal/low/low_test.go": 'package low\n\nimport "testing"\n\nfunc TestF(t *testing.T) {\n\tif F() != 1 {\n\t\tt.Fatal("x")\n\t}\n}\n',
            "backend-golang/internal/high/high.go": 'package high\n\nimport "x/internal/low"\n\nfunc G() int { return low.F() }\n',
            "backend-golang/internal/high/high_test.go": 'package high\n\nimport "testing"\n\nfunc TestG(t *testing.T) {\n\tif G() != 1 {\n\t\tt.Fatal("x")\n\t}\n}\n',
            "backend-golang/internal/notest/n.go": "package notest\n\nfunc N() int { return 1 }\n",
        }
        for rel, text in files.items():
            self.write(self.main, rel, text)
        g(self.main, "add", ".")
        g(self.main, "commit", "-qm", "module")
        head = g(self.main, "rev-parse", "HEAD").strip()
        for tree in (self.snap, self.slice):
            g(tree, "checkout", "-q", "--detach", head)

    LOW_GATE = "go --pkg ./internal/low"

    def test_a_change_that_breaks_a_dependent_package_is_refused(self):
        self.change("backend-golang/internal/low/low.go", "package low\n\nfunc F(x int) int { return x }\n")
        self.change("backend-golang/internal/low/low_test.go",
                    'package low\n\nimport "testing"\n\nfunc TestF(t *testing.T) {\n\tif F(1) != 1 {\n\t\tt.Fatal("x")\n\t}\n}\n')
        before = self.main_state()
        r = self.apply(gates=(self.LOW_GATE,))
        self.assertRejected(r, "gate")
        self.assertIn("not enough arguments", r.stdout)
        self.assertNothingMerged(before)

    def test_a_broken_file_behind_a_tag_is_refused(self):
        for tag in ("smoke", "integration"):
            with self.subTest(tag=tag):
                self.reset_slice()
                self.change("backend-golang/internal/low/probe.go", f"//go:build {tag}\n\npackage low\n\nconst S = !!!broken\n")
                before = self.main_state()
                r = self.apply(gates=(self.LOW_GATE,))
                self.assertRejected(r, "gate")
                self.assertIn("broken", r.stdout)
                self.assertNothingMerged(before)

    def test_a_broken_file_behind_a_conjunction_is_refused(self):
        self.change("backend-golang/internal/low/probe.go",
                    "//go:build videoprobe && videoprobe_s1\n\npackage low\n\nconst S = !!!broken\n")
        r = self.recv(gates=(self.LOW_GATE,))
        self.assertRejected(r, "gate")
        self.assertIn("broken", r.stdout)

    def test_a_clean_change_passes_all_the_vets_and_says_what_ran(self):
        self.change("backend-golang/internal/low/low.go", "package low\n\nfunc F() int { return 1 } // ok\n")
        self.change("backend-golang/internal/notest/n.go", "package notest\n\nfunc N() int { return 2 }\n")
        r = self.apply(gates=("go --pkg ./internal/...",))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(" gates=3 ", r.first)
        self.assertIn("這包沒有測試跑到：./internal/notest", r.stdout)
        self.assertNotIn("./internal/low,", r.stdout.split("這包沒有測試跑到：")[1].splitlines()[0])


class DocstringTest(unittest.TestCase):
    def test_docstring_names_every_exit_code(self):
        doc = recv.__doc__
        for code in ("0 ", "1 ", "2 ", "75 "):
            self.assertIn(code, doc)
        self.assertIn("Usage", doc)

    def test_docstring_names_the_new_refusals(self):
        for text in ("--allow-root-moved", "--recover", "skip-worktree", "go --vet"):
            self.assertIn(text, recv.__doc__)


if __name__ == "__main__":
    unittest.main()
