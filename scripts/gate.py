# -*- coding: utf-8 -*-
"""Run a test gate (Go, Angular, ai-service) and report one verdict nothing can misread as green.

Usage:
  python3 gate.py go --tree T [--pkg P ...] [--run NAME ...] [--integ] [--race] [--all] [--timeout D]
                     [--overlay F] [--tags-off] [--build|--vet [--tags T]] [--max-wait S]
  python3 gate.py fe --tree T --project frontend|admin [--include PATH ...] [--timeout D]
  python3 gate.py fe-build --tree T --project frontend|admin [--timeout D]
  python3 gate.py ai --tree T --repo R [--kind pytest|ruff|mypy] [PATH ...] [--timeout D]
  python3 gate.py clean-docker [--apply]
Shared: --out FILE (default <T>/tmp/gate/<mode>-<time>.out), --expect red|green, --known-red FILE, --no-known-red,
        --label TEXT

go: runs in <T>/backend-golang without GOROOT. --run repeats; `|` inside one --run is a tool error. --integ adds
    -tags integration and a dockerslot (-timeout 1500s); --all implies it, takes every slot (--max-wait 1800,
    -timeout 2400s, ./...). --tags T passes -tags T to --build or --vet only, to compile files behind other tags.
    --pkg accepts ./internal/x, internal/x, backend-golang/internal/x or an absolute path.
fe: --include accepts absolute, repo-relative or workspace-relative paths, or a glob under projects/.
ai: always a throwaway container; never host uv/pytest. --repo owns the shared Linux ai-service/.venv.
clean-docker: lists testcontainers that are dead/exited or older than 50 minutes; --apply removes them.

Known-red list: `fe` reads <T>/angular-workspace/known-red-<project>.txt and `go --integ/--all` reads
<T>/backend-golang/known-red-integration.txt when they exist; --known-red FILE replaces it, --no-known-red turns
it off; giving both is a tool error. A line is one test's exact full name; `TestX/*` (go only) is TestX and every
subtest under it; `unhandled <N> <spec> :: <message>` is exactly N uncaught errors whose whole message is that text
from that spec. `#` starts a comment line. A listed test may pass or fail; any other red stays red.

Verdicts: GREEN | RED | RED-KNOWN | BUILD-FAIL | NO-TESTS | INCOMPLETE | SLOT-TIMEOUT.
RED means an assertion failed: compile errors are BUILD-FAIL; panics, timeouts, kills, tests that started
without ending and unhandled errors off the list are INCOMPLETE. A --run name that matched nothing or only skipped
tests, or an explicit --pkg with zero tests, is NO-TESTS even when something else passed or every red is on the list.

Exit: 0 matches the expectation (default green; RED-KNOWN counts as green, --expect red needs a new RED);
      1 does not match; 2 tool error; 3 NO-TESTS; 4 INCOMPLETE; 75 dockerslot gave up waiting.

First stdout line: GATE <mode> <verdict> pass=N fail=N skip=N <sec>s out=<path>. Full text goes to --out and
machine-readable results to <out>.summary.json; `packages` there is {"./internal/x": tests run (pass + fail, no skip)}
for go test runs, a missing key meaning 0, and null for every other mode.

The command line refuses a tree with an unrecovered mutate.py run (exit 2). Import: gate.run_gate(argv) returns the
summary dict, raises ToolError and does not make that check; gate.pending_mutation(tree) is that check.
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import shlex
import signal
import subprocess
import sys
import time

EXIT_MISMATCH = 1
EXIT_TOOL = 2
EXIT_NO_TESTS = 3
EXIT_INCOMPLETE = 4
EXIT_SLOT = 75

DOCKERSLOT = pathlib.Path(__file__).resolve().parent / "dockerslot.py"
AI_IMAGE = "ghcr.io/astral-sh/uv:python3.12-bookworm"
TC_LABEL = "org.testcontainers"
MUTATE_POINTER = "mutate-journal"
MUTATE_SCRIPT = pathlib.Path(__file__).resolve().parent / "mutate.py"
# WHY: older than the longest gate timeout (2400 s), so such a container cannot belong to a live run.
ORPHAN_AGE_S = 50 * 60
WALL_DEFAULT_S = 1200
TERM_GRACE_S = 5
MAX_LISTED = 20
MAX_STDOUT_LINES = 30
MSG_LINE_WIDTH = 160

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
DURATION_RE = re.compile(r"^(\d+(\.\d+)?(ns|us|ms|s|m|h))+$")
GO_FATAL_RE = re.compile(r"^(panic: |signal: |fatal error: )")
GO_TIMED_OUT_RE = re.compile(r"^panic: test timed out")
GO_RUNNING_RE = re.compile(r"^\t\t(\S+) \(\S+\)$")
GO_BUILD_FAIL_RE = re.compile(r"^FAIL\s+\S+ \[(build|setup) failed\]")
GO_LOG_LINE_RE = re.compile(r"^\s+\S+\.go:\d+:")
GO_FRAME_RE = re.compile(r"^\s*(=== |--- |PASS$|FAIL$)")
GO_EMPTY_LOG_RE = re.compile(r"^\s*\S+\.go:\d+:\s*$")
GO_MODULE_RE = re.compile(r"""^module\s+"?([^\s"]+)"?""", re.M)
FE_FAIL_RE = re.compile(r"^\s*FAIL\s+(?:\S+\s+)?(\S+\.(?:ts|tsx|js|mjs)\b.*)$")
FE_SUITE_RE = re.compile(r"^(.*) \[ (.*) \]$")
FE_RUN_RE = re.compile(r"^\s*RUN\s+v\d")
FE_RULE_RE = re.compile(r"^\s*⎯")
FE_UNHANDLED_HEAD_RE = re.compile(r"^\s*⎯+ Unhandled Errors ⎯+\s*$")
FE_UNHANDLED_BLOCK_RE = re.compile(r"^\s*⎯+ \S.*? ⎯+\s*$")
FE_ORIGIN_RE = re.compile(r'This error originated in "([^"]+)" test file')
FE_UNHANDLED_LABEL = "vitest unhandled errors: "
KNOWN_UNHANDLED_RE = re.compile(r"^unhandled\s+([1-9]\d*)\s+(\S+)\s+::\s+(\S.*)$")
COUNT_RE = re.compile(r"(\d+) (failed|passed|skipped|todo|errors?|deselected|xfailed|xpassed)\b")
PYTEST_END_RE = re.compile(r"\bin \d+(\.\d+)?s\b|no tests ran")
RUFF_RE = re.compile(r"^(\S+?:\d+:\d+): ([A-Z]+\d+) (.*)$")
MYPY_RE = re.compile(r"^(\S+?:\d+): error: (.*)$")

_stop_reason = None


class ToolError(Exception):
    """The gate itself could not run; says nothing about the code under test."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ToolError(message)


def _build_parser():
    ap = _Parser(prog="gate.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode")

    def mode(name, tree=True):
        p = sub.add_parser(name)
        p.add_argument("--tree", required=tree)
        p.add_argument("--out")
        p.add_argument("--expect", choices=("green", "red"), default="green")
        p.add_argument("--known-red", dest="known_red")
        p.add_argument("--no-known-red", dest="no_known_red", action="store_true")
        p.add_argument("--label")
        return p

    go = mode("go")
    go.add_argument("--pkg", action="append", default=[])
    go.add_argument("--run", action="append", default=[])
    go.add_argument("--integ", action="store_true")
    go.add_argument("--race", action="store_true")
    go.add_argument("--all", action="store_true")
    go.add_argument("--timeout")
    go.add_argument("--overlay")
    go.add_argument("--tags-off", dest="tags_off", action="store_true")
    go.add_argument("--tags")
    go.add_argument("--build", action="store_true")
    go.add_argument("--vet", action="store_true")
    go.add_argument("--max-wait", dest="max_wait")
    for name in ("fe", "fe-build"):
        p = mode(name)
        p.add_argument("--project", required=True, choices=("frontend", "admin"))
        p.add_argument("--timeout")
        if name == "fe":
            p.add_argument("--include", action="append", default=[])
    ai = mode("ai")
    ai.add_argument("--repo", required=True)
    ai.add_argument("--kind", choices=("pytest", "ruff", "mypy"), default="pytest")
    ai.add_argument("--timeout")
    ai.add_argument("paths", nargs="*")
    clean = mode("clean-docker", tree=False)
    clean.add_argument("--apply", action="store_true")
    return ap


def parse_args(argv):
    ns = _build_parser().parse_args(argv)
    if not ns.mode:
        raise ToolError("missing mode: go, fe, fe-build, ai or clean-docker")
    return ns


def request_stop(reason):
    """Stop the current gate and any later one in this process; safe from a signal handler."""
    # INVARIANT: sticky and only polled, never raised. A signal landing before the run loop starts must
    # still stop it, and mutate.py relies on no exception ever crossing its restore path.
    global _stop_reason
    _stop_reason = reason


def _on_signal(signum, _frame):
    request_stop(f"interrupted by {signal.Signals(signum).name}")


def _child_env(cwd):
    env = {k: v for k, v in os.environ.items() if k != "GOROOT"}
    # WHY: the go tool trusts $PWD to resolve relative paths; a stale value from the caller's shell
    # would make -overlay keys miss on a tree reached through a symlink.
    env["PWD"] = str(cwd)
    return env


def _signal_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _execute(argv, cwd, raw_path, wall_s):
    """Run argv in its own process group with output captured to raw_path; return (rc, stop_reason)."""
    with open(raw_path, "wb") as sink:
        try:
            proc = subprocess.Popen(argv, cwd=str(cwd), env=_child_env(cwd), stdout=sink, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            raise ToolError(f"cannot start {argv[0]}: {e}")
    deadline = time.monotonic() + wall_s if wall_s else None
    stop, term_at = None, None
    try:
        while proc.poll() is None:
            time.sleep(0.05)
            now = time.monotonic()
            if stop is None:
                if _stop_reason:
                    stop = _stop_reason
                elif deadline and now >= deadline:
                    stop = f"wall-clock timeout after {wall_s:g}s"
            if stop and term_at is None:
                term_at = now
                _signal_group(proc.pid, signal.SIGTERM)
            elif term_at is not None and now - term_at >= TERM_GRACE_S:
                _signal_group(proc.pid, signal.SIGKILL)
    finally:
        # INVARIANT: no test worker may outlive its gate; a mutation restore assumes nothing still reads the tree.
        _signal_group(proc.pid, signal.SIGKILL)
    return proc.returncode, stop


def _died_of_signal(rc):
    return rc < 0 or rc >= 128


class Result:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.failures = []
        self.passed_names = set()
        self.seen_names = set()
        self.incomplete = []
        self.reasons = []
        self.no_tests = []
        self.build = []
        self.notes = []
        self.text = ""
        self.needs_tests = True
        self.run_unmatched = []
        self.unhandled_total = 0
        self.unhandled = []
        self.known_unhandled = 0
        self.packages = None


def _short(line):
    line = re.sub(r"\s+", " ", line).strip()
    return line if len(line) <= MSG_LINE_WIDTH else line[:MSG_LINE_WIDTH] + "…"


def _go_failure_lines(entries):
    """Pick the assertion text out of a test's output, skipping the log noise integration tests print first."""
    # WHY: go1.25+ tags t.Error output; the regex is the fallback for older toolchains.
    chosen = [t for kind, t in entries if kind in ("error", "error-continue")]
    if not chosen:
        lines = [t for kind, t in entries if kind != "frame" and not GO_FRAME_RE.match(t)]
        for i, t in enumerate(lines):
            if GO_LOG_LINE_RE.match(t) or "Error Trace:" in t:
                chosen = lines[i:]
                break
        else:
            chosen = lines
    return [t for t in chosen if t.strip() and not GO_EMPTY_LOG_RE.match(t) and "Error Trace:" not in t]


def _pattern_hits(pattern, names):
    parts = pattern.split("/")
    try:
        regexes = [re.compile(p) for p in parts]
    except re.error:
        # WHY: Go's RE2 accepts syntax Python rejects; unverifiable must not turn into a false NO-TESTS.
        return True
    for name in names:
        segs = name.split("/")
        if len(segs) >= len(parts) and all(r.search(s) for r, s in zip(regexes, segs)):
            return True
    return False


def _go_own_failure(entries):
    """Whether a parent test's body printed a failure of its own, beyond the rollup of its subtests."""
    # WHY: a bare t.Fail is invisible and t.Log looks the same as t.Error before go1.25, so a logged note counts too.
    # Over-reporting costs a list line; under-reporting hides a new failure behind a listed subtest.
    return any(kind in ("error", "error-continue") or (kind != "frame" and GO_LOG_LINE_RE.match(text))
               for kind, text in entries)


def _ancestors(name):
    parts = name.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def _names_that_ran(ran, seen):
    """Drop parents whose subtests were all skipped: they pass without having checked anything."""
    parents = {a for n in seen for a in _ancestors(n)}
    leaves = ran - parents
    return ran & (leaves | {a for n in leaves for a in _ancestors(n)})


def _run_pattern_problem(pattern, seen, ran, skipped):
    if not _pattern_hits(pattern, seen):
        return "matched no test"
    if not _pattern_hits(pattern, ran):
        return "只命中被略過的測試"
    # WHY: `--run TestTarget` also matches TestTargetB by substring; a pass there says nothing about the test named.
    named = "/".join(seg.lstrip("^").rstrip("$") for seg in pattern.split("/"))
    if named in skipped and named not in ran:
        return f"點名的 {named} 被略過，沒有跑"
    return None


def _package_key(import_path, module):
    if module and import_path == module:
        return "."
    if module and import_path.startswith(module + "/"):
        return "./" + import_path[len(module) + 1:]
    # INVARIANT: an unmappable package keeps its import path, so a reader asking for ./internal/x gets 0, not coverage.
    return import_path


def parse_go_stream(path, rc, requested=(), run_patterns=(), module=None):
    res = Result()
    pkgs, tests, raw_lines, build_lines = {}, {}, [], []
    build_failed = False
    timed_out = False
    hung = []

    def pkg_of(name):
        return pkgs.setdefault(name, {"out": [], "result": None, "lone_fail": False, "build": False, "fatal": None})

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                ev = json.loads(line)
                if not isinstance(ev, dict) or "Action" not in ev:
                    raise ValueError
            except ValueError:
                raw_lines.append(line.rstrip("\n"))
                continue
            action = ev["Action"]
            if action == "build-output":
                build_lines.append(ev.get("Output", "").rstrip("\n"))
                continue
            if action == "build-fail":
                build_failed = True
                continue
            name, test = ev.get("Package", ""), ev.get("Test")
            pkg = pkg_of(name)
            if action == "output":
                text = ev.get("Output", "")
                stripped = text.rstrip("\n")
                pkg["out"].append(text)
                if GO_FATAL_RE.match(stripped) and pkg["fatal"] is None:
                    pkg["fatal"] = stripped
                if GO_TIMED_OUT_RE.match(stripped):
                    timed_out = True
                running = GO_RUNNING_RE.match(stripped)
                if running and timed_out and running.group(1) not in hung:
                    hung.append(running.group(1))
                if test:
                    tests.setdefault((name, test), {"state": "run", "lines": []})["lines"].append((ev.get("OutputType", ""), stripped))
                else:
                    if stripped == "FAIL":
                        pkg["lone_fail"] = True
                    if GO_BUILD_FAIL_RE.match(stripped):
                        pkg["build"] = True
            elif action == "run":
                tests.setdefault((name, test), {"state": "run", "lines": []})
            elif action in ("pass", "fail", "skip"):
                if test:
                    tests.setdefault((name, test), {"state": "run", "lines": []})["state"] = action
                else:
                    pkg["result"] = action
                    if ev.get("FailedBuild"):
                        pkg["build"] = True

    any_build = build_failed or any(p["build"] for p in pkgs.values())
    if any_build:
        res.build = build_lines + [x for x in raw_lines if not x.startswith(("go: downloading", "dockerslot:"))]
    elif not pkgs and rc != 0:
        if _died_of_signal(rc) or not raw_lines:
            res.reasons.append(f"go exited rc={rc} with no test events")
            res.incomplete.append(f"go process (rc={rc})")
        else:
            res.build = raw_lines

    by_pkg_ran = {}
    failed_keys = {k for k, t in tests.items() if t["state"] == "fail"}
    for (name, test), t in tests.items():
        res.seen_names.add(test)
        state = t["state"]
        if state == "pass":
            by_pkg_ran[name] = by_pkg_ran.get(name, 0) + 1
            res.passed += 1
            res.passed_names.add(test)
        elif state == "skip":
            res.skipped += 1
        elif state == "run":
            res.incomplete.append(f"{name}: {test}")

    for key, t in tests.items():
        if t["state"] != "fail":
            continue
        name, test = key
        # INVARIANT: a parent fails whenever a subtest does, so a bare rollup does not count; otherwise --known-red
        # would have to list rollups and `fail=` would not match the failures an agent has to look at.
        if any(n == name and other.startswith(test + "/") for n, other in failed_keys) and not _go_own_failure(t["lines"]):
            continue
        by_pkg_ran[name] = by_pkg_ran.get(name, 0) + 1
        res.failed += 1
        res.failures.append({"name": test, "pkg": name, "lines": _go_failure_lines(t["lines"])[:8]})

    for name, pkg in pkgs.items():
        ran_fail = any(n == name for n, _ in failed_keys)
        if pkg["build"]:
            continue
        if pkg["result"] is None:
            res.incomplete.append(f"{name}: package never finished")
        elif pkg["result"] == "fail" and (not pkg["lone_fail"] or not ran_fail):
            # INVARIANT: the testing package prints a bare FAIL only after every test ran. Without it the
            # binary died early (panic, os.Exit, kill), and tests after that point never started, so the
            # visible failures understate the damage. Log text like "panic:" is deliberately not the signal:
            # recover-path tests print it on green runs.
            res.incomplete.append(f"{name}: test binary did not finish; remaining tests never ran")
            if pkg["fatal"]:
                res.reasons.append(pkg["fatal"])
    if timed_out and not res.incomplete:
        res.incomplete.append("test timed out")
        res.reasons.append("panic: test timed out")
    if hung:
        # WHY: most unfinished tests at a timeout are t.Parallel ones still paused; only these were running.
        res.reasons.append("running at the timeout: " + ", ".join(hung[:5]))

    if rc != 0 and not res.failed and not res.incomplete and not res.build:
        res.incomplete.append(f"go exited rc={rc} but the stream shows no failure")

    for req in requested:
        tail = req.rstrip("/")
        if tail.endswith("...") or tail in (".", "./"):
            continue
        tail = tail[2:] if tail.startswith("./") else tail
        matches = [n for n in pkgs if n == tail or n.endswith("/" + tail)]
        if not any(by_pkg_ran.get(n) for n in matches):
            res.no_tests.append(f"{req}: 0 tests ran in this package")
    skipped_names = {test for (_, test), t in tests.items() if t["state"] == "skip"}
    ran_names = {test for (_, test), t in tests.items() if t["state"] != "skip"}
    counted = _names_that_ran(ran_names, res.seen_names)
    for pattern in run_patterns:
        problem = _run_pattern_problem(pattern, res.seen_names, counted, skipped_names)
        if problem:
            res.run_unmatched.append(pattern)
            res.no_tests.append(f"--run {pattern}: {problem}")
    res.packages = {_package_key(n, module): by_pkg_ran.get(n, 0) for n in pkgs if n}

    sections = []
    for name, pkg in pkgs.items():
        sections.append(f"=== package {name} ===\n" + "".join(pkg["out"]))
    if build_lines or raw_lines:
        sections.append("=== build / non-JSON output ===\n" + "\n".join(build_lines + raw_lines) + "\n")
    res.text = "\n".join(sections)
    return res


def _read_clean(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return ANSI_RE.sub("", f.read()).replace("\r", "")


def _reject_npx_failure(text):
    if "could not determine executable" in text:
        raise ToolError("npx could not find ng: run `pnpm install` in angular-workspace/")


def _fe_build_errors(lines):
    start = next((i for i, x in enumerate(lines) if "[ERROR]" in x), None)
    if start is None:
        return [x for x in lines if x.strip()][-10:]
    return [x for x in lines[start:] if x.strip()][:12]


def _fe_unhandled(lines):
    start = next((i for i, x in enumerate(lines) if FE_UNHANDLED_HEAD_RE.match(x)), None)
    if start is None:
        return []
    blocks = []
    for line in lines[start + 1:]:
        if re.match(r"^\s*Test Files\s", line):
            break
        if FE_UNHANDLED_BLOCK_RE.match(line):
            blocks.append({"message": None, "origin": None})
        elif blocks and blocks[-1]["message"] is None and line.strip():
            blocks[-1]["message"] = line.strip()
        elif blocks and blocks[-1]["origin"] is None:
            origin = FE_ORIGIN_RE.search(line)
            if origin:
                blocks[-1]["origin"] = origin.group(1)
    return blocks


def parse_fe(path, rc, expected_files=0):
    res = Result()
    res.text = text = _read_clean(path)
    lines = text.splitlines()
    # INVARIANT: engine messages only count before vitest starts. After that the text is the code under
    # test logging, and a logged "[ERROR]" must not turn a real result into BUILD-FAIL or NO-TESTS.
    run_at = next((i for i, x in enumerate(lines) if FE_RUN_RE.match(x)), len(lines))
    before_run = lines[:run_at]
    _reject_npx_failure("\n".join(before_run))
    no_tests = next((x for x in before_run if "No tests found" in x), None)
    if no_tests:
        res.no_tests.append(no_tests)
        return res
    if any("Application bundle generation failed" in x or "[ERROR]" in x for x in before_run):
        res.build = _fe_build_errors(before_run)
        return res

    pending, block, suites = [], None, []
    for line in lines:
        m = FE_FAIL_RE.match(line)
        if m:
            name = m.group(1).strip()
            # WHY: vitest prints a suite-level failure as "<path> [ <path> ]"; requiring both halves to
            # match keeps a test merely titled "x [ y ]" counted as the assertion failure it is.
            suite = FE_SUITE_RE.match(name)
            if suite and suite.group(1) == suite.group(2):
                entry = {"name": name, "lines": []}
                suites.append(entry)
            else:
                entry = {"name": name, "pkg": "", "lines": []}
                res.failures.append(entry)
            pending.append(entry)
            block = pending
            continue
        if block is None:
            continue
        if FE_RULE_RE.match(line):
            block, pending = None, []
            continue
        if line.strip():
            pending = []
            for entry in block:
                if len(entry["lines"]) < 6:
                    entry["lines"].append(line)

    tests_line = next((x for x in reversed(lines) if re.match(r"^\s*Tests\s", x)), None)
    files_line = next((x for x in reversed(lines) if re.match(r"^\s*Test Files\s", x)), None)
    errors_line = next((x for x in reversed(lines) if re.match(r"^\s*Errors\s+\d", x)), None)
    if tests_line is None:
        if _died_of_signal(rc) or rc == 0 or "RUN" in text:
            res.incomplete.append(f"no vitest summary line (rc={rc})")
        else:
            res.build = [x for x in lines if x.strip()][-10:]
        return res

    counts = {k: int(n) for n, k in COUNT_RE.findall(tests_line)}
    res.passed = counts.get("passed", 0)
    res.failed = counts.get("failed", 0)
    res.skipped = counts.get("skipped", 0) + counts.get("todo", 0)
    if res.failed != len(res.failures):
        res.notes.append(f"summary says {res.failed} failed but {len(res.failures)} names were parsed; see out")
    for s in suites:
        res.incomplete.append(s["name"])
        res.reasons.extend(_short(x) for x in s["lines"][:2])
    if errors_line:
        res.incomplete.append(FE_UNHANDLED_LABEL + errors_line.strip())
        res.reasons.extend(_short(x) for x in lines if re.match(r"^\s*\w*Error: ", x))
        res.unhandled_total = int(re.search(r"\d+", errors_line).group())
        res.unhandled = _fe_unhandled(lines)
    if files_line and expected_files:
        m = re.search(r"\((\d+)\)\s*$", files_line)
        if m and int(m.group(1)) < expected_files:
            res.incomplete.append(f"{expected_files} spec files included but vitest ran {m.group(1)}")
    if rc != 0 and not res.failed and not res.incomplete:
        res.incomplete.append(f"ng test exited rc={rc} with an all-passed summary")
    return res


def _reject_docker_failure(text, rc):
    first = text.strip().splitlines()[0] if text.strip() else f"rc={rc}"
    # INVARIANT: only the first line; test output may quote these words and a real failure must stay RED.
    if rc in (125, 126, 127) or first.startswith("docker:") or "Cannot connect to the Docker daemon" in first:
        raise ToolError("container did not start: " + _short(first))


def parse_pytest(path, rc):
    res = Result()
    res.text = text = _read_clean(path)
    _reject_docker_failure(text, rc)
    lines = text.splitlines()
    if rc == 4:
        raise ToolError("pytest usage error: " + _short(lines[0] if lines else ""))
    if rc == 5:
        res.no_tests.append(next((x for x in reversed(lines) if x.strip()), "no tests collected"))
        return res
    if rc == 2 and "during collection" in text:
        res.build = [x for x in lines if x.startswith(("E ", "ERROR "))][:12]
        return res
    end = next((x for x in reversed(lines) if PYTEST_END_RE.search(x)), None)
    if end is None or _died_of_signal(rc):
        res.incomplete.append(f"pytest did not reach its summary line (rc={rc})")
        return res
    counts = {k: int(n) for n, k in COUNT_RE.findall(end)}
    res.passed = counts.get("passed", 0) + counts.get("xpassed", 0)
    res.failed = counts.get("failed", 0)
    res.skipped = counts.get("skipped", 0) + counts.get("xfailed", 0)
    errors = counts.get("error", 0) + counts.get("errors", 0)
    for line in lines:
        m = re.match(r"^(FAILED|ERROR) (\S+)(?: - (.*))?$", line)
        if not m:
            continue
        if m.group(1) == "FAILED":
            res.failures.append({"name": m.group(2), "pkg": "", "lines": [m.group(3) or ""]})
            res.seen_names.add(m.group(2))
        else:
            res.incomplete.append(m.group(2))
            res.reasons.append(_short(line))
    if errors and not res.incomplete:
        res.incomplete.append(f"{errors} pytest errors outside test bodies")
    if rc not in (0, 1) and not res.incomplete:
        res.incomplete.append(f"pytest exited rc={rc}")
    return res


def parse_lint(path, rc, kind):
    res = Result()
    res.needs_tests = False
    res.text = text = _read_clean(path)
    _reject_docker_failure(text, rc)
    lines = text.splitlines()
    ok_marker, rx = ("All checks passed", RUFF_RE) if kind == "ruff" else ("Success:", MYPY_RE)
    if rc == 0:
        if kind == "ruff" and "No Python files found" in text:
            res.needs_tests = True
            res.no_tests.append("ruff found no Python files")
        elif ok_marker not in text:
            res.incomplete.append(f"{kind} exited 0 without its success line")
        return res
    if rc == 1:
        for line in lines:
            m = rx.match(line)
            if m:
                res.failures.append({"name": line.split(" ", 1)[0].rstrip(":"), "pkg": "", "lines": [line]})
        res.failed = len(res.failures)
        if not res.failed:
            res.incomplete.append(f"{kind} exited 1 with no parsable finding")
        return res
    if _died_of_signal(rc):
        res.incomplete.append(f"{kind} killed (rc={rc})")
        return res
    raise ToolError(f"{kind} exited rc={rc}: " + _short(lines[0] if lines else ""))


def parse_plain_build(path, rc, tool):
    res = Result()
    res.needs_tests = False
    res.text = text = _read_clean(path)
    if tool == "ng":
        _reject_npx_failure(text)
    if _died_of_signal(rc):
        res.incomplete.append(f"{tool} killed (rc={rc})")
    elif rc != 0:
        lines = text.splitlines()
        res.build = _fe_build_errors(lines) if tool == "ng" else [x for x in lines if x.strip()][:12]
        if not res.build:
            res.build = [f"{tool} exited rc={rc} with no output"]
    return res


def _inside(root, path, label):
    rel = os.path.relpath(os.path.realpath(path), root)
    if rel == ".." or rel.startswith(".." + os.sep):
        raise ToolError(f"{label} is outside {root}: {path}")
    return rel


def _norm_pkg(backend, tree, pkg):
    wildcard = pkg.rstrip("/").endswith("...")
    base = pkg.rstrip("/")
    if wildcard:
        base = base[:-3].rstrip("/")
    if os.path.isabs(base):
        base = _inside(backend, base, "--pkg")
    else:
        base = base[2:] if base.startswith("./") else base
        if base.startswith("backend-golang/") or base == "backend-golang":
            base = base[len("backend-golang"):].lstrip("/")
    if base in ("", "."):
        return "./..." if wildcard else "."
    if not (pathlib.Path(backend) / base).is_dir():
        raise ToolError(f"--pkg {pkg}: no such directory under {backend}")
    return f"./{base}/..." if wildcard else f"./{base}"


def _resolve_tree(ns):
    if not ns.tree:
        raise ToolError("--tree is required")
    tree = os.path.realpath(ns.tree)
    if not os.path.isdir(tree):
        raise ToolError(f"--tree: no such directory: {ns.tree}")
    return tree


def _duration_seconds(text):
    if text is None:
        return WALL_DEFAULT_S
    if re.match(r"^\d+(\.\d+)?$", text):
        return float(text)
    if not DURATION_RE.match(text):
        raise ToolError(f"--timeout: not a duration: {text}")
    unit = {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1, "m": 60, "h": 3600}
    return sum(float(n) * unit[u] for n, _, u in re.findall(r"(\d+(\.\d+)?)(ns|us|ms|s|m|h)", text))


def go_argv(ns, tree):
    """The full command for go mode, dockerslot wrapper included."""
    backend = os.path.join(tree, "backend-golang")
    integ = ns.integ or ns.all
    if ns.build:
        cmd = ["go", "build", "-o", os.devnull]
    elif ns.vet:
        cmd = ["go", "vet"]
    else:
        cmd = ["go", "test", "-json", "-count=1"]
    if integ:
        cmd += ["-tags", "integration"]
    elif ns.tags:
        cmd += ["-tags", ns.tags]
    elif ns.tags_off:
        cmd += ["-tags="]
    if not (ns.build or ns.vet):
        timeout = ns.timeout or ("2400s" if ns.all else "1500s" if integ else None)
        if timeout:
            cmd += ["-timeout", timeout]
        if ns.race:
            cmd += ["-race"]
        if ns.run:
            cmd += ["-run", "|".join(ns.run)]
    if ns.overlay:
        cmd += ["-overlay", ns.overlay]
    cmd += [_norm_pkg(backend, tree, p) for p in ns.pkg] or ["./..."]
    if not integ or ns.build or ns.vet:
        return cmd
    slot = [sys.executable, str(DOCKERSLOT)]
    if ns.all:
        slot += ["--all", "--max-wait", ns.max_wait or "1800"]
    elif ns.max_wait:
        slot += ["--max-wait", ns.max_wait]
    return slot + ["--"] + cmd


def _validate_go(ns, tree):
    if ns.build and ns.vet:
        raise ToolError("--build and --vet are mutually exclusive")
    if (ns.build or ns.vet) and (ns.run or ns.race or ns.timeout):
        raise ToolError("--run/--race/--timeout only apply to test runs")
    if (ns.integ or ns.all) and ns.tags_off:
        raise ToolError("--tags-off contradicts --integ/--all")
    if ns.tags is not None:
        if not (ns.build or ns.vet):
            raise ToolError("--tags only applies to --build or --vet")
        if not ns.tags.strip():
            raise ToolError("--tags needs a value")
        if ns.integ or ns.all or ns.tags_off:
            raise ToolError("--tags contradicts --integ/--all/--tags-off")
    piped = next((r for r in ns.run if "|" in r), None)
    if piped is not None:
        # WHY: the unmatched check reads one --run as one name, so a typo on either side of `|` would pass unseen.
        raise ToolError(f"--run {piped}: `|` is not allowed; repeat the flag instead: --run A --run B")
    if ns.integ and not ns.all and not ns.pkg and not (ns.build or ns.vet):
        raise ToolError("--integ without --pkg would run every package on one slot; use --all or name packages")
    if ns.timeout and not DURATION_RE.match(ns.timeout):
        raise ToolError(f"--timeout: not a Go duration: {ns.timeout}")
    if ns.overlay:
        if not os.path.isfile(ns.overlay):
            raise ToolError(f"--overlay: no such file: {ns.overlay}")
        ns.overlay = os.path.abspath(ns.overlay)
    if not os.path.isdir(os.path.join(tree, "backend-golang")):
        raise ToolError(f"{tree} has no backend-golang/")


def _go_module(backend):
    try:
        text = pathlib.Path(backend, "go.mod").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = GO_MODULE_RE.search(text)
    return m.group(1) if m else None


def _norm_includes(ws, includes):
    """Return (normalized patterns, number of literal spec files)."""
    out, files = [], 0
    for inc in includes:
        if os.path.isabs(inc):
            rel = _inside(ws, inc, "--include")
        else:
            rel = inc[len("angular-workspace/"):] if inc.startswith("angular-workspace/") else inc
            rel = rel[2:] if rel.startswith("./") else rel
        if any(c in rel for c in "*?["):
            pattern = rel[len("projects/"):] if rel.startswith("projects/") else rel
            if next(pathlib.Path(ws, "projects").glob(pattern), None) is None:
                raise ToolError(f"--include {inc}: glob matches nothing under {ws}/projects")
        else:
            target = pathlib.Path(ws, rel)
            if not target.exists():
                raise ToolError(f"--include {inc}: no such path (looked for {target})")
            if target.is_file():
                files += 1
        out.append(rel)
    return out, files


def _fe_workspace(ns, tree):
    ws = os.path.join(tree, "angular-workspace")
    if not os.path.isdir(ws):
        raise ToolError(f"{tree} has no angular-workspace/")
    if not os.path.isdir(os.path.join(ws, "node_modules")):
        raise ToolError(f"{ws} has no node_modules; run: cd {ws} && pnpm install")
    return ws


def _norm_ai_paths(ai_dir, paths):
    out = []
    for raw in paths:
        path, sep, node = raw.partition("::")
        if os.path.isabs(path):
            rel = _inside(ai_dir, path, "PATH")
        else:
            rel = path[len("ai-service/"):] if path.startswith("ai-service/") else path
            rel = rel[2:] if rel.startswith("./") else rel
        if not os.path.exists(os.path.join(ai_dir, rel)):
            raise ToolError(f"{raw}: no such path under {ai_dir}")
        out.append(rel + sep + node)
    return out


def _check_venv(repo):
    venv = os.path.join(repo, "ai-service", ".venv")
    cfg = os.path.join(venv, "pyvenv.cfg")
    if not os.path.isfile(cfg):
        raise ToolError(f"{venv} is not a venv (no pyvenv.cfg); --repo must own the shared ai-service/.venv")
    with open(cfg, encoding="utf-8", errors="replace") as f:
        home = next((x for x in f if x.startswith("home")), "")
    if "/Users/" in home or "macos" in home or "darwin" in home:
        raise ToolError(
            f"{cfg} is a macOS venv ({home.strip()}): containers cannot run it, something ran uv or pytest on the host. "
            f"Ask the owner to rebuild it inside a container (uv sync --frozen in {AI_IMAGE}), or pass --repo with a Linux venv")
    return venv


def _ai_argv(ns, tree, repo):
    ai_dir = os.path.join(tree, "ai-service")
    if not os.path.isdir(ai_dir):
        raise ToolError(f"{tree} has no ai-service/")
    venv = _check_venv(repo)
    paths = _norm_ai_paths(ai_dir, ns.paths)
    # WHY: the tree is mounted read-only, so docker cannot create the nested mount point itself. It is
    # an empty gitignored directory; never touch it when the tree owns the real venv.
    if tree != repo:
        os.makedirs(os.path.join(ai_dir, ".venv"), exist_ok=True)
    tool = {
        "pytest": ["-m", "pytest", "-q", "-p", "no:cacheprovider"] + paths,
        # WHY: concise output is one finding per line; the default multi-line form has nothing stable to count.
        "ruff": ["-m", "ruff", "check", "--no-cache", "--output-format=concise"] + (paths or ["app", "tests"]),
        "mypy": ["-m", "mypy"] + (paths or ["app"]),
    }[ns.kind]
    return ["docker", "run", "--rm", "--network", "none",
            "-v", f"{ai_dir}:/app:ro", "-v", f"{venv}:/app/.venv:ro", "-w", "/app",
            "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "LANGFUSE_TRACING_ENABLED=false",
            "-e", "APP_ENV=development", "-e", "MYPY_CACHE_DIR=/tmp/mypy", AI_IMAGE, ".venv/bin/python"] + tool


def _load_known_red(path):
    """Return {"names": [...], "unhandled": [{"count", "origin", "message"}, ...]}, or None without a list."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            entries = [x.strip() for x in f if x.strip() and not x.lstrip().startswith("#")]
    except OSError as e:
        raise ToolError(f"--known-red: {e}")
    if not entries:
        raise ToolError(f"--known-red {path} lists no test")
    known = {"names": [], "unhandled": []}
    for entry in entries:
        if not entry.startswith("unhandled "):
            known["names"].append(entry)
            continue
        m = KNOWN_UNHANDLED_RE.match(entry)
        if not m:
            raise ToolError(f"--known-red {path}: want `unhandled <N> <spec> :: <message>`, got: {entry}")
        known["unhandled"].append({"count": int(m.group(1)), "origin": m.group(2), "message": m.group(3)})
    return known


def _tree_known_red(ns, tree):
    """(list path, whether it came from the tree) for this run."""
    if ns.known_red:
        return ns.known_red, False
    if ns.no_known_red:
        return None, False
    if ns.mode == "fe":
        path = os.path.join(tree, "angular-workspace", f"known-red-{ns.project}.txt")
    elif ns.mode == "go" and (ns.integ or ns.all) and not (ns.build or ns.vet or ns.tags_off):
        path = os.path.join(tree, "backend-golang", "known-red-integration.txt")
    else:
        return None, False
    # WHY: agents forget a flag on a healthy tree and then spend a turn reading a baseline as a regression.
    return (path, True) if os.path.exists(path) else (None, False)


def _settle_unhandled(res, entries):
    """Clear the not-green state only for an exact match; return listed entries that did not occur."""
    if not res.unhandled_total:
        return [f"unhandled {e['count']} {e['origin']} :: {e['message']}" for e in entries]
    if not entries:
        return []
    problems = []
    if len(res.unhandled) != res.unhandled_total:
        problems.append(f"vitest counted {res.unhandled_total} unhandled errors but {len(res.unhandled)} were parsed")
    rest = list(res.unhandled)
    for e in entries:
        hits = [b for b in rest if b["origin"] == e["origin"] and b["message"] and e["message"].strip() == b["message"].strip()]
        rest = [b for b in rest if not any(b is h for h in hits)]
        if len(hits) != e["count"]:
            problems.append(f"list allows exactly {e['count']} of `{e['message']}` from {e['origin']}, got {len(hits)}")
    problems += [f"not on the list: {b['message']} (from {b['origin']})" for b in rest]
    if problems:
        # INVARIANT: more, fewer or another origin all need a look, so the list is never partly applied.
        res.reasons = problems + res.reasons
        return []
    res.incomplete = [x for x in res.incomplete if not x.startswith(FE_UNHANDLED_LABEL)]
    listed = {_short(b["message"]) for b in res.unhandled}
    res.reasons = [x for x in res.reasons if x not in listed]
    res.known_unhandled = res.unhandled_total
    return []


def _entry_covers(entry, name, star):
    if entry == name:
        return True
    # INVARIANT: absorbing a subtree must be written out. Implicit prefixes let a listed parent swallow a brand-new
    # subtest. `star` is go only: in fe and ai names a `/` is a directory, and `projects/*` would swallow everything.
    if star and entry.endswith("/*"):
        return name == entry[:-2] or name.startswith(entry[:-1])
    return False


def _split_known(res, names, star):
    failed = [f["name"] for f in res.failures]

    def covered(pool, entry):
        return any(_entry_covers(entry, x, star) for x in pool)

    known = [n for n in failed if any(_entry_covers(e, n, star) for e in names)]
    report = {"new": [n for n in failed if n not in known], "existing": known, "absent": [], "passed": []}
    for entry in names:
        if covered(failed, entry):
            continue
        if covered(res.passed_names, entry):
            report["passed"].append(entry)
        elif not covered(res.seen_names, entry):
            report["absent"].append(entry)
    return report


def _verdict(res, known):
    if res.build:
        return "BUILD-FAIL"
    if res.incomplete:
        return "INCOMPLETE"
    if res.failures or res.failed:
        if known is not None and not known["new"] and res.failed == len(res.failures):
            # INVARIANT: the list only speaks for tests that ran; it cannot vouch for a package or --run that ran none.
            return "NO-TESTS" if res.no_tests or res.run_unmatched else "RED-KNOWN"
        return "RED"
    if res.needs_tests and (res.no_tests or res.passed == 0):
        return "NO-TESTS"
    return "RED-KNOWN" if res.known_unhandled else "GREEN"


def _exit_code(verdict, expect):
    if verdict == "NO-TESTS":
        return EXIT_NO_TESTS
    if verdict == "INCOMPLETE":
        return EXIT_INCOMPLETE
    if verdict == "SLOT-TIMEOUT":
        return EXIT_SLOT
    if expect == "red":
        return 0 if verdict == "RED" else EXIT_MISMATCH
    return 0 if verdict in ("GREEN", "RED-KNOWN") else EXIT_MISMATCH


def _default_out(tree, mode):
    d = pathlib.Path(tree, "tmp", "gate")
    stem = f"{mode}-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    p = d / f"{stem}.out"
    n = 0
    while p.exists():
        n += 1
        p = d / f"{stem}-{n}.out"
    return p


def _docker_rows():
    fmt = "\t".join(["{{.ID}}", "{{.State}}", "{{.CreatedAt}}", '{{.Label "%s"}}' % TC_LABEL, "{{.Names}}"])
    try:
        r = subprocess.run(["docker", "ps", "-a", "--filter", f"label={TC_LABEL}=true", "--format", fmt],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ToolError(f"docker ps failed: {e}")
    if r.returncode != 0:
        raise ToolError(f"docker ps failed: {_short((r.stderr or r.stdout).strip())}")
    rows, now = [], time.time()
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) != 5:
            raise ToolError(f"unexpected docker ps row: {_short(line)}")
        cid, state, created, label, name = cols
        try:
            when = datetime.datetime.strptime(" ".join(created.split(" ")[:3]), "%Y-%m-%d %H:%M:%S %z").timestamp()
        except ValueError:
            raise ToolError(f"unparsable CreatedAt in docker ps row: {_short(line)}")
        # INVARIANT: re-check the label; removing an unlabelled container would hit the dev stack or another session's services.
        if label == "true":
            rows.append({"id": cid, "state": state, "age_s": now - when, "name": name})
    return rows


def _docker_rm(ids):
    if not ids:
        return
    try:
        r = subprocess.run(["docker", "rm", "-f"] + ids, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ToolError(f"docker rm failed: {e}")
    if r.returncode != 0:
        raise ToolError(f"docker rm failed: {_short((r.stderr or r.stdout).strip())}")


def _clean_finished_containers():
    """Return (removed count, error text); never raises, a cleanup hiccup must not change a test verdict."""
    try:
        ids = [r["id"] for r in _docker_rows() if r["state"] in ("dead", "exited")]
        _docker_rm(ids)
        return len(ids), None
    except ToolError as e:
        return 0, str(e)


def clean_docker(ns):
    rows = _docker_rows()
    orphans = [r for r in rows if r["state"] in ("dead", "exited") or r["age_s"] > ORPHAN_AGE_S]
    if ns.apply:
        _docker_rm([r["id"] for r in orphans])
    first = f"GATE clean-docker OK found={len(orphans)} removed={len(orphans) if ns.apply else 0} kept={len(rows) - len(orphans)}"
    lines = [first] + [f"- {r['id']} {r['state']} age={int(r['age_s'] // 60)}m {r['name']}" for r in orphans[:MAX_LISTED]]
    if len(orphans) > MAX_LISTED:
        lines.append(f"... {len(orphans)} total, first {MAX_LISTED} listed")
    if orphans and not ns.apply:
        lines.append("dry run: pass --apply to remove them")
    if ns.out:
        pathlib.Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(ns.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def _prepare(ns):
    """Validate arguments and return (mode label, argv, cwd, parser, wall seconds)."""
    tree = _resolve_tree(ns)
    if ns.known_red and ns.no_known_red:
        raise ToolError("--known-red and --no-known-red contradict each other")
    if ns.mode == "go":
        _validate_go(ns, tree)
        argv = go_argv(ns, tree)
        cwd = os.path.join(tree, "backend-golang")
        if ns.build or ns.vet:
            kind = "go-build" if ns.build else "go-vet"
            return kind, argv, cwd, lambda p, rc: parse_plain_build(p, rc, "go"), None
        requested = [_norm_pkg(cwd, tree, p) for p in ns.pkg]
        module = _go_module(cwd)
        return "go", argv, cwd, lambda p, rc: parse_go_stream(p, rc, requested, ns.run, module), None
    if ns.mode == "fe":
        ws = _fe_workspace(ns, tree)
        includes, files = _norm_includes(ws, ns.include)
        argv = ["npx", "ng", "test", ns.project, "--watch=false"]
        for inc in includes:
            argv += ["--include", inc]
        return "fe", argv, ws, lambda p, rc: parse_fe(p, rc, files), _duration_seconds(ns.timeout)
    if ns.mode == "fe-build":
        ws = _fe_workspace(ns, tree)
        argv = ["npx", "ng", "build", ns.project]
        return "fe-build", argv, ws, lambda p, rc: parse_plain_build(p, rc, "ng"), _duration_seconds(ns.timeout)
    repo = os.path.realpath(ns.repo)
    argv = [sys.executable, str(DOCKERSLOT), "--"] + _ai_argv(ns, tree, repo)
    if ns.kind == "pytest":
        return "ai", argv, tree, parse_pytest, _duration_seconds(ns.timeout)
    return f"ai-{ns.kind}", argv, tree, lambda p, rc: parse_lint(p, rc, ns.kind), _duration_seconds(ns.timeout)


def _write_outputs(out, summary, res, stop):
    head = [_first_line(summary), f"cmd: {summary['cmd']}", f"tree: {summary['tree']}"]
    if stop:
        head.append(f"stopped: {stop}")
    body = []
    for f in res.failures:
        body.append(f"--- FAILED {f['name']}" + (f" ({f['pkg']})" if f.get("pkg") else ""))
        body += f["lines"]
    if res.build:
        body.append("--- BUILD ERRORS")
        body += res.build
    if res.incomplete:
        body.append("--- DID NOT FINISH")
        body += res.incomplete + res.reasons
    if res.no_tests:
        body.append("--- NO TESTS")
        body += res.no_tests
    text = "\n".join(head + body) + "\n\n=== full output ===\n" + res.text
    pathlib.Path(out).write_text(text, encoding="utf-8")
    pathlib.Path(str(out) + ".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")


def _first_line(s):
    return f"GATE {s['mode']} {s['verdict']} pass={s['pass']} fail={s['fail']} skip={s['skip']} {s['seconds']:g}s out={s['out']}"


def mutate_pointer(tree):
    """Where mutate.py records its journal for this tree: per worktree, so siblings do not block each other."""
    try:
        r = subprocess.run(["git", "-C", str(tree), "rev-parse", "--absolute-git-dir"], capture_output=True, text=True)
    except OSError:
        return None
    # WHY: mutate.py refuses a tree git cannot read, so outside a readable git tree no mutation can be pending.
    if r.returncode != 0:
        return None
    return pathlib.Path(os.path.realpath(r.stdout.strip()), MUTATE_POINTER)


def pending_mutation(tree):
    """The journal path of an unrecovered mutate.py run on this tree, or None. The path may no longer exist."""
    pointer = mutate_pointer(tree)
    if pointer is None or not pointer.exists():
        return None
    return pathlib.Path(pointer.read_text(encoding="utf-8", errors="replace").strip() or pointer)


def _refuse_pending_mutation(tree):
    pending = pending_mutation(tree)
    if pending is not None:
        raise ToolError(f"{tree} may still hold a mutant from an unfinished mutate.py run ({pending}), so a result here "
                        f"would describe the mutant. If no mutate is running: python3 {MUTATE_SCRIPT} --tree {tree} --recover")


def run_gate(argv, check_pending=False):
    """Run one gate from gate.py-style argv and return the summary dict. Raises ToolError."""
    ns = parse_args(argv)
    if ns.mode == "clean-docker":
        raise ToolError("run_gate does not run clean-docker")
    summary_path = pathlib.Path(str(ns.out) + ".summary.json") if ns.out else None
    # INVARIANT: a summary only ever describes the latest run at this --out. recv.py and mutate.py read it,
    # so a crash or tool error must not leave an earlier GREEN there to be believed.
    if summary_path and summary_path.exists():
        summary_path.unlink()
    try:
        if check_pending:
            _refuse_pending_mutation(ns.tree)
        return _run_gate(ns)
    except ToolError as e:
        if summary_path:
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps({"mode": ns.mode, "tree": ns.tree, "verdict": "TOOL-ERROR", "exit": EXIT_TOOL,
                                                "error": str(e), "out": str(ns.out), "label": ns.label}, ensure_ascii=False, indent=1), encoding="utf-8")
        raise


def _run_gate(ns):
    _load_known_red(ns.known_red)
    mode, cmd, cwd, parse, wall = _prepare(ns)
    tree = os.path.realpath(ns.tree)
    known_file, from_tree = _tree_known_red(ns, tree)
    known_list = _load_known_red(known_file)
    out = pathlib.Path(ns.out) if ns.out else _default_out(tree, mode)
    out = pathlib.Path(os.path.abspath(out))
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = pathlib.Path(str(out) + ".raw")

    t0 = time.monotonic()
    rc, stop = _execute(cmd, cwd, raw, wall)
    seconds = round(time.monotonic() - t0, 1)

    slot_gave_up = False
    if rc == EXIT_SLOT:
        with open(raw, encoding="utf-8", errors="replace") as f:
            slot_gave_up = "dockerslot: gave up" in f.read()
    if slot_gave_up:
        res = Result()
        res.text = raw.read_text(encoding="utf-8", errors="replace")
        res.reasons = [x for x in res.text.splitlines() if x.strip()][:5]
        verdict, known = "SLOT-TIMEOUT", None
    else:
        try:
            res = parse(str(raw), rc)
        except ToolError:
            # WHY: keep what the engine printed; the raw file is about to be removed on every other path.
            raw.replace(out)
            raise
        if stop:
            res.incomplete.insert(0, stop)
        known = None
        if known_list:
            unhandled_absent = _settle_unhandled(res, known_list["unhandled"])
            known = _split_known(res, known_list["names"], star=ns.mode == "go")
            known["unhandled"] = res.known_unhandled
            known["unhandled_absent"] = unhandled_absent
            # WHY: a narrow run never reaches most of the tree's list, so "absent" would be noise on every slice.
            if from_tree and (getattr(ns, "include", None) or (ns.mode == "go" and not ns.all)):
                known["absent"], known["unhandled_absent"] = [], []
        verdict = _verdict(res, known)

    cleaned, clean_err = None, None
    if ns.mode == "go" and (ns.integ or ns.all) and not (ns.build or ns.vet) and not slot_gave_up:
        cleaned, clean_err = _clean_finished_containers()

    summary = {
        "mode": mode, "tree": tree, "verdict": verdict, "exit": _exit_code(verdict, ns.expect),
        "expect": ns.expect, "pass": res.passed, "fail": res.failed, "skip": res.skipped,
        "failed": [f["name"] for f in res.failures],
        "failures": res.failures,
        "incomplete": res.incomplete, "reasons": res.reasons, "no_tests": res.no_tests, "build": res.build,
        "notes": res.notes, "run_unmatched": res.run_unmatched, "known_red": known, "known_red_file": known_file,
        "packages": res.packages,
        "docker_cleaned": cleaned, "docker_clean_error": clean_err,
        "out": str(out), "seconds": seconds, "cmd": " ".join(shlex.quote(c) for c in cmd), "label": ns.label,
    }
    _write_outputs(out, summary, res, stop)
    raw.unlink()
    return summary


def _names_line(title, names, limit=8):
    shown = ", ".join(names[:limit])
    more = f" ... {len(names)} total" if len(names) > limit else ""
    return f"{title} {len(names)}: {shown}{more}"


def _failure_lines(failures, per_failure):
    lines = []
    for f in failures[:MAX_LISTED]:
        lines.append(f"- {f['name']}")
        lines += [f"    {_short(x)}" for x in f["lines"][:per_failure]]
    if len(failures) > MAX_LISTED:
        lines.append(f"... {len(failures)} failures total, first {MAX_LISTED} listed")
    return lines


def render(s):
    """The concise stdout for one summary; first line is the verdict line."""
    lines = [_first_line(s)]
    if s["label"]:
        lines.append(f"label: {s['label']}")
    if s["docker_cleaned"] is not None:
        lines.append(f"docker: cleaned {s['docker_cleaned']} dead/exited testcontainers")
    if s["docker_clean_error"]:
        lines.append(f"docker: clean failed: {_short(s['docker_clean_error'])}")
    lines += [_short(x) for x in s["build"][:12]]
    if s["incomplete"]:
        # WHY: entries repeat the long import path; the test name alone is what the reader acts on.
        lines.append(_short(_names_line("did not finish", [x.split(": ", 1)[-1] if x.split(": ", 1)[-1].startswith("Test") else x for x in s["incomplete"]], limit=5)))
        if all(x.startswith(FE_UNHANDLED_LABEL) for x in s["incomplete"]):
            lines.append("every test ran, but errors escaped them and can hide a false green; they are not on the list")
        else:
            lines.append("tests after the stopping point never ran; their number is unknown")
        lines += [_short(x) for x in s["reasons"][:4]]
    elif s["reasons"]:
        lines += [_short(x) for x in s["reasons"][:4]]
    if s["verdict"] == "NO-TESTS":
        lines += [_short(x) for x in s["no_tests"][:6]]
    elif s["run_unmatched"]:
        lines.append(_names_line("--run matched nothing:", s["run_unmatched"]))
    lines += [_short(x) for x in s["notes"]]
    known = s["known_red"]
    if s["known_red_file"]:
        lines.append(f"known-red: {s['known_red_file']}")
    if known:
        if known["unhandled"]:
            lines.append(f"既存 unhandled: {known['unhandled']}")
        if known["unhandled_absent"]:
            lines.append(_names_line("名單內但這次沒出現", known["unhandled_absent"]))
        if known["new"] or s["fail"]:
            lines.append(_names_line("新增紅", known["new"]))
            lines.append(_names_line("既存紅", known["existing"]))
        if known["absent"]:
            lines.append(_names_line("名單內但這次沒出現", known["absent"]))
        if known["passed"]:
            lines.append(_names_line("名單內且這次通過", known["passed"]))
    failures = [f for f in s["failures"] if not known or f["name"] in known["new"]]
    per_failure = 3 if len(failures) <= 4 else 1 if len(failures) <= 12 else 0
    budget = MAX_STDOUT_LINES - len(lines)
    tail = _failure_lines(failures, per_failure)
    if len(tail) > budget:
        tail = tail[:budget - 1] + [f"... {len(failures)} failures total, see out"]
    return lines + tail


def main(argv=None):
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _on_signal)
    args = sys.argv[1:] if argv is None else argv
    try:
        ns = parse_args(args)
        if ns.mode == "clean-docker":
            print("\n".join(clean_docker(ns)))
            return 0
        summary = run_gate(args, check_pending=True)
    except ToolError as e:
        print(f"GATE TOOL-ERROR: {e}")
        return EXIT_TOOL
    print("\n".join(render(summary)))
    return summary["exit"]


if __name__ == "__main__":
    sys.exit(main())
