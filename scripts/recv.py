# -*- coding: utf-8 -*-
"""Receive one slice: run the mechanical part of taking an agent's delivery into the main checkout.

Judgement stays with the main session (why the red run was red, deviations, a mutation of its own choosing,
grep for old wording): this tool lists those as undone and never prints them as passed.

Usage:
  python3 recv.py --slice S1 --tree T --snapshot SNAP --root MAIN --gate "go --pkg ./internal/x"
      [--gate "fe --project frontend"] (--scope PATH_OR_GLOB ... | --scope-file F)
      [--require FILE ...] [--allow-narrow "reason" | --allow-narrow-file F] [--allow-revert S1:path ...]
      [--allow-root-moved "reason" | --allow-root-moved-file F] [--out-dir D] [--progress PROGRESS.md] [--no-board] [--apply]

Without --apply it only checks: nothing is copied, PROGRESS is not written, the board does not run.
--apply needs --progress, which must already exist.

--gate: gate.py arguments without --tree/--out/--expect (recv owns them) and without --overlay (the gate must test
        the files being merged). `ai` without --repo uses --root. Quote it as one argument.
        A .go change also runs `go --vet --pkg ./...`, `go --vet --tags integration --pkg ./...` and, for a changed
        file behind another //go:build tag, `go --vet --tags <tag> --pkg <its package>`, before the gates named here.
        They cannot be turned off, count in gates=N and do not count as coverage.
--scope: a path covers itself and everything below; * and ? stop at /, ** crosses it; relative to the tree.
        A pattern admitting any path of some depth or extension (**/*.go, */*/*/*) is a tool error.
--require: relative to the tree unless absolute.
        A slice that edited its own known-red list is measured against the snapshot's copy of that list.
--allow-narrow: lets `go --run` and `fe --include` and `ai PATH` count as coverage, and releases the file rules and new
        skips below (each is named in the output). An uncovered .go, frontend source or ai-service .py stays refused.
        `go --integ --run` is allowed next to a whole-package gate, but alone it does not cover the package.
--allow-root-moved: lets through a --root whose HEAD differs from the snapshot's; the reason goes to the output and PROGRESS.
--out-dir: default <tree>/tmp/recv-<slice>; inside a tree it must be under that tree's tmp/.

Coverage, per changed file, first match wins:
  *.md, docs/**, .claude/**, tmp/**       nothing
  backend-golang/**/*.go                  a go test gate naming its package
  angular-workspace/projects/<p>/**       fe --project <p>
  ai-service/**                           ai (pytest)
  db/migrations/**, db/queries/**         at least one go --integ or --all
  backend-golang/go.mod, go.sum           go --pkg ./... or go --all
  angular-workspace package.json, pnpm-lock.yaml, angular.json, tsconfig*.json    fe --project frontend and admin
  backend-golang other, next to .go files, or under testdata beside them    a go test gate naming that package
  anything else                           nothing, but named in the output for judgement
A test file gaining a t.Skip, .skip(, .only(, xit(, xdescribe(, pytest.mark.skip, pytest.skip( or xfail line is refused too.

Named for judgement and never cut from stdout: files no rule covers, changed test files, what --allow-narrow released,
files only another platform compiles, and `這包沒有測試跑到：X` for a changed package where no go gate ran a test (a hint,
not a refusal: packages without tests exist).

Steps, stopping at the first failure:
  preflight (no unrecovered mutate.py on the tree: `mutate.py --tree T --recover`; no skip-worktree or assume-unchanged
  flag; --root at the snapshot's HEAD), require, preflight (merge_back dry run), scope, coverage,
  gate (only GREEN and RED-KNOWN pass), drift (file list and sha equal before and after the gates),
  merge (exactly the list the drift check passed), verify (main sha equals what the gates ran), progress, board.

Exit: 0 dry run passed or applied; 1 rejected at any step; 2 tool error, including anything failing after the merge
      (first line MERGED-BUT-FAILED:<step>); 75 dockerslot gave up waiting, retry.

First stdout line: RECV <slice> <DRY-RUN-OK|APPLIED|REJECTED:<step>|TOOL-ERROR|MERGED-BUT-FAILED:<step>>
files=N gates=<gates actually run> out=<path>. At most 30 lines; the whole story is in <out-dir>/recv-<slice>.out.
"""
import argparse
import collections
import datetime
import os
import pathlib
import re
import shlex
import signal
import subprocess
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import gate  # noqa: E402
import merge_back  # noqa: E402
from _common import dirty_paths, file_digest, is_worktree_root  # noqa: E402
from gate import ToolError  # noqa: E402

MERGE_BACK = SCRIPTS / "merge_back.py"
BOARD = SCRIPTS / "board.py"
BOARD_TIMEOUT_S = 180

PASSING_VERDICTS = ("GREEN", "RED-KNOWN")
RECV_OWNED_FLAGS = ("--tree", "--out", "--expect")
GATE_MODES = ("go", "fe", "fe-build", "ai")
SLICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
INTEG_TAG_RE = re.compile(r"(?m)^//go:build .*(?<!!)\bintegration\b")
GO_BUILD_LINE_RE = re.compile(r"^//go:build\s+(.*)$")
GO_TAG_RE = re.compile(r"(!?)\s*([A-Za-z0-9_.]+)")
GO_VERSION_TAG_RE = re.compile(r"^go\d+\.\d+$")
GOOS = frozenset("aix android darwin dragonfly freebsd hurd illumos ios js linux nacl netbsd openbsd plan9 solaris "
                 "wasip1 windows zos".split())
GOARCH = frozenset("386 amd64 arm arm64 loong64 mips mips64 mips64le mipsle ppc64 ppc64le riscv64 s390x sparc64 "
                   "wasm".split())
# WHY: `ignore` by convention marks `go run` scripts; setting it would pull every package main script of a directory
# into one package and refuse a slice for code nothing compiles.
GO_TOOLCHAIN_TAGS = frozenset(("cgo", "gc", "gccgo", "unix", "ignore"))
# WHY: go tooling ignores these directories, so a file there belongs to the enclosing package; naming the
# directory itself would ask for a gate that can only come back NO-TESTS.
GO_IGNORED_DIR_RE = re.compile(r"^(testdata$|_|\.)")
# WHY: a pattern can admit every path of one depth or one extension without matching any single sample, so the check
# has to try the whole grid. The name is deliberately no directory a slice would own.
MATCH_ALL_PROBES = tuple(
    "zzrecvprobe/" * depth + "zzrecvprobe" + ext
    for depth in range(10)
    for ext in ("", ".z", ".go", "_test.go", ".ts", ".spec.ts", ".py", ".md", ".sql", ".json", ".yaml", ".txt"))

NO_GATE_NEEDED_RE = re.compile(r"(\.md$|^docs/|^\.claude/|^tmp/)")
DB_FILE_RE = re.compile(r"^(backend-golang/)?db/(migrations|queries)/")
GO_MODULE_FILES = ("backend-golang/go.mod", "backend-golang/go.sum")
# WHY: read by the gate, compiled by nothing; recv already measures the slice against the snapshot's copy.
KNOWN_RED_FILE = "backend-golang/known-red-integration.txt"
FE_WORKSPACE_FILE_RE = re.compile(r"^angular-workspace/(package\.json|pnpm-lock\.yaml|angular\.json|tsconfig[^/]*\.json)$")
FE_PROJECTS = ("frontend", "admin")
TEST_FILE_RE = re.compile(r"(_test\.go|\.spec\.ts|(^|/)test_[^/]*\.py|_test\.py)$")
# WHY: any receiver for .Skip( because `tt.Skip` and `s.T().Skip` skip as well; \b so that exit( is no xit(.
NEW_SKIP_RE = re.compile(r"\.Skip(?:f|Now)?\(|\.skip(?:If)?\(|\.only\(|\bxit\(|\bxdescribe\(|\bpytest\.mark\.skip|"
                         r"\bpytest\.skip\(|\bpytest\.(?:mark\.)?xfail")

JUDGE_LABELS = ("red-run 紅的原因", "偏離裁決", "抽的突變", "改語義的舊字樣 grep", "主寫測試未動")

MAX_STDOUT_LINES = 30
MAX_CHANGED_LISTED = 20
MAX_GATE_LINES = 8
MAX_REASON_LINES = 20
MAX_NAMES = 10


class Rejected(Exception):
    def __init__(self, step, reasons, code=1):
        super().__init__(step)
        self.step, self.reasons, self.code = step, reasons, code


class PostMerge(Exception):
    def __init__(self, step, message):
        super().__init__(message)
        self.step = step


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ToolError(message)


class Run:
    """What the final report is rendered from; filled as the steps go."""

    def __init__(self):
        self.slice = "?"
        self.status = "TOOL-ERROR"
        self.out = None
        self.changed = []
        self.gates_run = 0
        self.gate_lines = []
        self.reasons = []
        self.hints = []
        # INVARIANT: these say a change may be untested, so unlike hints they are printed whatever the line budget.
        self.must_show = []
        self.tests_run = {}
        self.narrow_reason = None
        self.root_moved_reason = None
        self.detail = []
        self.skeleton = None

    def log(self, *lines):
        self.detail.extend(lines)


def _build_parser():
    ap = _Parser(prog="recv.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slice", required=True)
    ap.add_argument("--tree", required=True)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--root", required=True, help="main checkout; never defaulted, merge_back would pick the cwd's toplevel")
    ap.add_argument("--gate", action="append", default=[])
    ap.add_argument("--scope", action="append", default=[])
    ap.add_argument("--scope-file")
    ap.add_argument("--require", action="append", default=[])
    ap.add_argument("--allow-narrow")
    ap.add_argument("--allow-narrow-file")
    ap.add_argument("--allow-root-moved")
    ap.add_argument("--allow-root-moved-file")
    ap.add_argument("--allow-revert", action="append", default=[])
    ap.add_argument("--out-dir")
    ap.add_argument("--progress")
    ap.add_argument("--no-board", action="store_true")
    ap.add_argument("--apply", action="store_true")
    return ap


def _glob_regex(pat):
    out, i, n = [], 0, len(pat)
    while i < n:
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[" and pat.find("]", i + 2) > 0:
            end = pat.find("]", i + 2)
            body = pat[i + 1:end]
            out.append("(?!/)[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
            i = end + 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def compile_scope(raw, a):
    """A regex over tree-relative paths for one --scope entry. Raises ToolError."""
    text = raw.strip()
    if os.path.isabs(text):
        real = os.path.realpath(text)
        base = next((b for b in (a.tree, a.root) if real == b or real.startswith(b + os.sep)), None)
        if base is None:
            raise ToolError(f"--scope {raw}: outside the tree and the root")
        text = os.path.relpath(real, base) + ("/" if text.endswith("/") else "")
    while text.startswith("./"):
        text = text[2:]
    is_dir = text.endswith("/")
    text = text.rstrip("/")
    if text in ("", ".") or ".." in text.split("/"):
        raise ToolError(f"--scope {raw!r}: not a path inside the tree")
    try:
        if any(c in text for c in "*?["):
            rx = re.compile(_glob_regex(text + ("/**" if is_dir else "")))
        else:
            rx = re.compile(re.escape(text) + "(?:/.*)?")
    except re.error as e:
        raise ToolError(f"--scope {raw}: {e}")
    # INVARIANT: a scope that admits every path turns the scope step into a printed green nobody earned.
    if any(rx.fullmatch(probe) for probe in MATCH_ALL_PROBES):
        raise ToolError(f"--scope {raw}: matches everything of a depth or extension, name what this slice owns")
    return rx


def _load_scopes(a):
    if a.scope and a.scope_file:
        raise ToolError("--scope and --scope-file are alternatives")
    raws = a.scope
    if a.scope_file:
        try:
            lines = pathlib.Path(a.scope_file).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as e:
            raise ToolError(f"--scope-file: {e}")
        raws = [x.strip() for x in lines if x.strip() and not x.lstrip().startswith("#")]
        if not raws:
            raise ToolError(f"--scope-file {a.scope_file} names no path")
    if not raws:
        raise ToolError("--scope or --scope-file is required")
    return [(raw, compile_scope(raw, a)) for raw in raws]


def _flag_prefix_of(token, flag):
    # WHY: argparse accepts unambiguous abbreviations, so an exact-name check would let `--ou x` through.
    name = token.split("=", 1)[0]
    return name.startswith("--") and len(name) > 2 and flag.startswith(name)


class GateSpec:
    def __init__(self, raw, ns, argv, pkgs, hint=None):
        self.raw, self.ns, self.argv, self.pkgs, self.hint = raw, ns, argv, pkgs, hint
        self.automatic = False

    @property
    def runs_tests(self):
        if self.ns.mode == "go":
            return not (self.ns.build or self.ns.vet)
        return self.ns.mode == "fe" or (self.ns.mode == "ai" and self.ns.kind == "pytest")

    @property
    def narrow(self):
        """The refusal step 4 names; a named integration gate is allowed, it just proves no whole package."""
        if self.ns.mode == "go":
            return bool(self.ns.run) and not self.ns.integ
        if self.ns.mode == "fe":
            return bool(self.ns.include)
        return self.ns.mode == "ai" and self.runs_tests and bool(self.ns.paths)

    @property
    def partial(self):
        return self.narrow or (self.ns.mode == "go" and bool(self.ns.run))

    def go_covers(self, pkg, allow_partial):
        if self.ns.mode != "go" or not self.runs_tests or (self.partial and not allow_partial):
            return False
        return any(p == "./..." or p == pkg or (p.endswith("/...") and (pkg == p[:-4] or pkg.startswith(p[:-3])))
                   for p in self.pkgs)


def _pin_known_red(ns, argv, a):
    """Point the gate at the snapshot's known-red list when the slice edited its own; returns a hint or None."""
    if ns.known_red or ns.no_known_red:
        return None
    tree_list, _ = gate._tree_known_red(ns, a.tree)
    snap_list, _ = gate._tree_known_red(ns, a.snapshot)
    if not tree_list or (snap_list and pathlib.Path(tree_list).read_bytes() == pathlib.Path(snap_list).read_bytes()):
        return None
    # INVARIANT: gate.py reads the list from the tree under test, so a slice adding its own failure would turn its
    # RED into RED-KNOWN and get merged. The baseline it is measured against comes from before the slice.
    argv += ["--known-red", snap_list] if snap_list else ["--no-known-red"]
    return f"這片改了 known-red 名單，閘改用快照那份: {os.path.relpath(tree_list, a.tree)}"


def _build_gate(raw, i, a):
    try:
        tokens = shlex.split(raw)
    except ValueError as e:
        raise ToolError(f"--gate {raw}: {e}")
    for t in tokens:
        for flag in RECV_OWNED_FLAGS:
            if _flag_prefix_of(t, flag):
                raise ToolError(f"--gate {raw}: must not set {t}; recv owns {flag}")
        # WHY: argparse would print help and exit 0 from inside a run, which reads as a passed receive.
        if t == "-h" or _flag_prefix_of(t, "--help"):
            raise ToolError(f"--gate {raw}: asks for help instead of running")
    if not tokens or tokens[0] not in GATE_MODES:
        raise ToolError(f"--gate {raw!r}: must start with one of {', '.join(GATE_MODES)}")
    if tokens[0] == "ai" and not any(_flag_prefix_of(t, "--repo") for t in tokens):
        tokens += ["--repo", a.root]
    out = os.path.join(a.out_dir, f"gate-{a.slice}-{i}-{tokens[0]}.out")
    argv = tokens + ["--tree", a.tree, "--out", out]
    ns = gate.parse_args(argv)
    # INVARIANT: --overlay compiles replacement files, so its result says nothing about the files being merged.
    if getattr(ns, "overlay", None):
        raise ToolError(f"--gate {raw}: --overlay tests other files than the ones being merged")
    gate._prepare(ns)
    hint = _pin_known_red(ns, argv, a)
    pkgs = []
    if ns.mode == "go":
        backend = os.path.join(a.tree, "backend-golang")
        pkgs = [gate._norm_pkg(backend, a.tree, p) for p in ns.pkg] or ["./..."]
    return GateSpec(raw, ns, argv, pkgs, hint)


def _load_reason(flag, inline, path):
    if inline is not None and path:
        raise ToolError(f"{flag} and {flag}-file are alternatives")
    reason = inline
    if path:
        try:
            reason = pathlib.Path(path).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as e:
            raise ToolError(f"{flag}-file: {e}")
    if reason is not None and not reason.strip():
        raise ToolError(f"{flag} needs a reason")
    return reason


def _check_tree_args(a):
    for name in ("tree", "snapshot", "root"):
        value = getattr(a, name)
        if not is_worktree_root(value):
            raise ToolError(f"--{name}: not the top level of a git checkout: {value}")
    if len({a.tree, a.snapshot, a.root}) != 3:
        raise ToolError("--tree, --snapshot and --root must be three different checkouts")


def _resolve_out_dir(a):
    out = os.path.realpath(a.out_dir) if a.out_dir else os.path.join(a.tree, "tmp", f"recv-{a.slice}")
    for base in (a.tree, a.snapshot, a.root):
        inside = out == base or out.startswith(base + os.sep)
        # INVARIANT: outside tmp/ the output would show up as a change in the tree (false drift) or dirty main.
        if inside and not out.startswith(os.path.join(base, "tmp") + os.sep):
            raise ToolError(f"--out-dir {out}: inside {base} it must be under tmp/")
    return out


def _check_progress(a):
    if a.apply and not a.progress:
        raise ToolError("--apply needs --progress")
    if not a.progress:
        return
    if not os.path.isfile(a.progress):
        raise ToolError(f"--progress: no such file: {a.progress}")
    if not os.access(a.progress, os.W_OK):
        raise ToolError(f"--progress: not writable: {a.progress}")


def parse_cli(argv, r):
    a = _build_parser().parse_args(argv)
    if not SLICE_RE.match(a.slice):
        raise ToolError(f"--slice {a.slice!r}: letters, digits, . _ - only")
    r.slice = a.slice
    a.tree, a.snapshot, a.root = (os.path.realpath(x) for x in (a.tree, a.snapshot, a.root))
    _check_tree_args(a)
    _check_progress(a)
    a.out_dir = _resolve_out_dir(a)
    os.makedirs(a.out_dir, exist_ok=True)
    r.out = os.path.join(a.out_dir, f"recv-{a.slice}.out")
    for spec in a.allow_revert:
        label, sep, path = spec.partition(":")
        if not sep or not path or label != a.slice:
            raise ToolError(f"--allow-revert {spec}: must be {a.slice}:PATH")
    a.narrow_reason = _load_reason("--allow-narrow", a.allow_narrow, a.allow_narrow_file)
    a.root_moved_reason = _load_reason("--allow-root-moved", a.allow_root_moved, a.allow_root_moved_file)
    a.scopes = _load_scopes(a)
    if not a.gate:
        raise ToolError("--gate is required")
    a.gates = [_build_gate(raw, i + 1, a) for i, raw in enumerate(a.gate)]
    r.narrow_reason = a.narrow_reason
    r.hints += [g.hint for g in a.gates if g.hint]
    return a


def _index_flagged_files(tree):
    raw = subprocess.run(["git", "-C", tree, "ls-files", "-v", "-z"], capture_output=True,
                         check=True).stdout.decode("utf-8", "surrogateescape")
    # WHY: `S` is skip-worktree and a lowercase status letter is assume-unchanged (`s` is both), git's own encoding.
    return [x[2:] for x in raw.split("\0") if x and (x[0] == "S" or x[0].islower())]


def check_tree_state(a, r):
    problems = []
    pending = gate.pending_mutation(a.tree)
    if pending is not None:
        problems.append(f"片的樹可能還留著沒還原的突變（{pending}），閘測不到它、合回會帶進主線。"
                        f"確認沒有 mutate 在跑後還原: python3 {gate.MUTATE_SCRIPT} --tree {a.tree} --recover")
    # INVARIANT: git status skips a flagged file, so the gates would run a copy merge_back never sees or copies.
    problems += [f"skip-worktree 或 assume-unchanged 旗標（git status 看不到這檔的改動）: {p}"
                 for p in _index_flagged_files(a.tree)]
    root_head, snap_head = merge_back.head_of(a.root), merge_back.head_of(a.snapshot)
    # WHY: HEAD only. A working-tree difference in the root is what merging other slices of the same wave leaves
    # behind; merge_back checks the files this slice touches, and the combination of slices is the phase-end full gate's job.
    if root_head == snap_head:
        if a.root_moved_reason is not None:
            r.hints.append("--allow-root-moved 沒用到：--root 與 --snapshot 的 HEAD 相同")
    elif a.root_moved_reason is not None:
        r.root_moved_reason = a.root_moved_reason
    else:
        problems.append(f"主線動了，這份組合沒被閘過：--root HEAD {root_head[:10]} ≠ --snapshot HEAD {snap_head[:10]}。"
                        "重開快照重跑，或帶 --allow-root-moved 理由")
    if problems:
        raise Rejected("preflight", problems)


def check_require(a):
    bad = []
    for raw in a.require:
        p = pathlib.Path(raw) if os.path.isabs(raw) else pathlib.Path(a.tree, raw)
        if not p.exists():
            bad.append(f"missing deliverable: {raw}")
        elif not p.is_file():
            bad.append(f"deliverable is not a file: {raw}")
        elif not p.read_bytes().strip():
            bad.append(f"empty deliverable: {raw}")
    if bad:
        raise Rejected("require", bad)


def fingerprint(a):
    """{path: sha or DELETED} for what the tree differs from the snapshot; merge_back's own idea of the change."""
    changed, deleted = merge_back.tree_changes(a.tree, a.snapshot)
    return {p: file_digest(pathlib.Path(a.tree, p)) for p in changed + deleted}


def _merge_back(a, apply=False, only_files=None):
    cmd = [sys.executable, str(MERGE_BACK), "--snapshot", a.snapshot, "--tree", f"{a.slice}={a.tree}", "--root", a.root]
    for spec in a.allow_revert:
        cmd += ["--allow-revert", spec]
    if only_files:
        cmd += ["--only-files", only_files]
    if apply:
        cmd.append("--apply")
    return subprocess.run(cmd, cwd=a.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          encoding="utf-8", errors="replace")


def _stop_lines(text):
    lines = text.splitlines()
    return [x.strip() for x in lines[lines.index("STOP:") + 1:] if x.strip()] if "STOP:" in lines else None


def preflight(a, r):
    before = fingerprint(a)
    res = _merge_back(a)
    r.log("--- merge_back dry run", res.stdout)
    if res.returncode != 0:
        stop = _stop_lines(res.stdout)
        r.changed = sorted(before)
        if stop is None:
            raise ToolError(f"merge_back crashed: {res.stdout.strip()[-400:]}")
        raise Rejected("preflight", stop)
    r.hints += [x for x in res.stdout.splitlines() if x.startswith("note:")]
    r.changed = sorted(before)
    if not before:
        raise Rejected("preflight", ["no change between the tree and the snapshot; nothing to receive"])
    reverted = merge_back.reverts_to_head(a.tree, dirty_paths(a.snapshot), r.changed)
    if reverted:
        r.hints.append("改回 HEAD（--allow-revert 放行，確認是刻意的）: " + ", ".join(reverted))
    return before


def check_scope(a, r):
    bad = [p for p in r.changed if not any(rx.fullmatch(p) for _, rx in a.scopes)]
    if bad:
        raise Rejected("scope", [f"out of scope: {p}" for p in bad])
    idle = [raw for raw, rx in a.scopes if not any(rx.fullmatch(p) for p in r.changed)]
    if idle:
        r.hints.append("--scope 沒命中任何改動（該做沒做？）: " + ", ".join(idle))


def go_package(path):
    parts = path.split("/")[1:-1]
    for i, part in enumerate(parts):
        if GO_IGNORED_DIR_RE.match(part):
            parts = parts[:i]
            break
    return "./" + "/".join(parts) if parts else "."


def _integ_tagged(tree, path):
    try:
        return bool(INTEG_TAG_RE.search(pathlib.Path(tree, path).read_text(encoding="utf-8", errors="replace")[:4096]))
    except OSError:
        return False


def _is_backend_go(path):
    return path.startswith("backend-golang/") and path.endswith(".go")


def _file_need(a, p, allow):
    """(covered, what it needs, whether --allow-narrow excuses it) for a non-.go file; None when no gate answers for it."""
    parts = p.split("/")

    def fe_covers(project):
        return any(g.ns.mode == "fe" and g.ns.project == project and (allow or not g.narrow) for g in a.gates)

    if parts[:2] == ["angular-workspace", "projects"] and len(parts) > 3:
        return fe_covers(parts[2]), f"fe --project {parts[2]}", False
    if parts[0] == "ai-service":
        covered = any(g.ns.mode == "ai" and g.runs_tests and (allow or not g.narrow) for g in a.gates)
        return covered, "an ai gate", not p.endswith(".py")
    if DB_FILE_RE.match(p):
        return any(g.ns.mode == "go" and g.runs_tests and (g.ns.integ or g.ns.all) for g in a.gates), "go --integ or go --all", True
    if p in GO_MODULE_FILES:
        return any(g.go_covers("./...", allow) for g in a.gates), "go --pkg ./... or go --all", True
    if FE_WORKSPACE_FILE_RE.match(p):
        missing = [x for x in FE_PROJECTS if not fe_covers(x)]
        return not missing, " and ".join(f"fe --project {x}" for x in FE_PROJECTS), True
    if parts[0] == "backend-golang" and p != KNOWN_RED_FILE:
        pkg = go_package(p)
        if any(pathlib.Path(a.tree, "backend-golang", pkg).glob("*.go")):
            return any(g.go_covers(pkg, allow) for g in a.gates), f"a go gate with --pkg {pkg}", True
    return None


def _new_skip_lines(a, path):
    """`path:line: text` for skip lines the tree has and the snapshot does not. Stripped: moving one is not adding it."""
    def lines_of(root):
        try:
            return pathlib.Path(root, path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
    old = collections.Counter(x.strip() for x in lines_of(a.snapshot))
    found = []
    for n, line in enumerate(lines_of(a.tree), 1):
        text = line.strip()
        if old[text] > 0:
            old[text] -= 1
        elif NEW_SKIP_RE.search(text):
            found.append(f"{path}:{n}: {gate._short(text)}")
    return found


def check_coverage(a, r):
    allow = a.narrow_reason is not None
    problems, released, no_rule, integ_uncovered = [], [], [], []
    if not allow:
        problems += [f"narrow gate: {g.raw}; pass --allow-narrow REASON if that is really enough" for g in a.gates if g.narrow]
    for p in r.changed:
        if NO_GATE_NEEDED_RE.search(p):
            continue
        if _is_backend_go(p):
            pkg = go_package(p)
            if not any(g.go_covers(pkg, allow) for g in a.gates):
                problems.append(f"{p} needs a go gate with --pkg {pkg}, and not one limited by --run")
            elif _integ_tagged(a.tree, p) and not any(g.ns.integ or g.ns.all for g in a.gates if g.go_covers(pkg, True)):
                integ_uncovered.append(p)
            continue
        need = _file_need(a, p, allow)
        if need is None:
            no_rule.append(p)
        elif not need[0]:
            if need[2] and allow:
                released.append(f"{p} (needs {need[1]})")
            else:
                problems.append(f"{p} needs {need[1]}")
    test_files = [p for p in r.changed if TEST_FILE_RE.search(p)]
    skips = [x for p in test_files for x in _new_skip_lines(a, p)]
    if skips and not allow:
        problems += [f"新增的略過: {x}" for x in skips]
    r.must_show.append(f"改到的測試檔 {len(test_files)}" + (": " + ", ".join(test_files) if test_files else ""))
    if skips and allow:
        r.must_show.append("--allow-narrow 放行、新增的略過: " + "; ".join(skips))
    if released:
        r.must_show.append("--allow-narrow 放行、沒有對應的閘: " + ", ".join(released))
    if no_rule:
        r.must_show.append("無閘規則的改動（有沒有被測到要自己看）: " + ", ".join(no_rule))
    if problems:
        raise Rejected("coverage", problems)
    if integ_uncovered:
        r.hints.append("整合測試檔沒有 --integ 閘跑到: " + ", ".join(integ_uncovered))


def _build_constraint_tags(tree, path):
    """Tags a file's //go:build line asks for, negated ones left out; [] without a constraint before the package clause."""
    try:
        text = pathlib.Path(tree, path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        if line.startswith("package "):
            break
        m = GO_BUILD_LINE_RE.match(line)
        if m:
            return [name for negated, name in GO_TAG_RE.findall(m.group(1)) if not negated]
    return []


def _platform_only(path, tags):
    stem = path.rsplit("/", 1)[-1][:-len(".go")]
    if stem.endswith("_test"):
        stem = stem[:-len("_test")]
    suffix = stem.split("_")[1:]
    return bool(GOOS.union(GOARCH).intersection(tags)) or bool(suffix and suffix[-1] in GOOS.union(GOARCH))


def auto_gates(a, r):
    """F3 and F15: the compile checks no caller can forget: dependents of the change, and files behind build tags."""
    go_files = [p for p in r.changed if _is_backend_go(p)]
    if not go_files:
        return []
    raws, platform_only = ["go --vet --pkg ./...", "go --vet --tags integration --pkg ./..."], []
    for p in go_files:
        if any(GO_IGNORED_DIR_RE.match(x) for x in p.split("/")[1:-1]):
            continue
        tags = _build_constraint_tags(a.tree, p)
        if _platform_only(p, tags):
            platform_only.append(p)
        named = sorted({t for t in tags if not (t in GOOS or t in GOARCH or t in GO_TOOLCHAIN_TAGS or GO_VERSION_TAG_RE.match(t))})
        # INVARIANT: `a && b` builds under neither tag alone, so the joint gate is what actually compiles such a file.
        sets = [t for t in named if t != "integration"] + ([",".join(named)] if len(named) > 1 else [])
        raws += [f"go --vet --tags {t} --pkg {go_package(p)}" for t in sorted(sets)]
    if platform_only:
        r.must_show.append("平台限定的檔（閘只編譯本機平台，可能沒編到）: " + ", ".join(platform_only))
    specs = []
    for i, raw in enumerate(sorted(set(raws), key=raws.index), len(a.gates) + 1):
        spec = _build_gate(raw, i, a)
        spec.automatic = True
        specs.append(spec)
    return specs


def _names(title, names):
    more = f" ... {len(names)} total" if len(names) > MAX_NAMES else ""
    return f"{title} {len(names)}: {', '.join(names[:MAX_NAMES])}{more}"


def _gate_failure_lines(s):
    lines = []
    new = (s.get("known_red") or {}).get("new")
    if new:
        lines.append(_names("新增紅", new))
    elif s["failed"]:
        lines.append(_names("failed", s["failed"]))
    if s["incomplete"]:
        lines.append(_names("did not finish", s["incomplete"]))
    lines += [gate._short(x) for x in s["build"][:4] + s["no_tests"][:3] + s["reasons"][:3]]
    if s["run_unmatched"]:
        lines.append(_names("--run matched nothing", s["run_unmatched"]))
    return lines


def run_gates(specs, r):
    for spec in specs:
        summary = gate.run_gate(spec.argv)
        r.gates_run += 1
        rendered = gate.render(summary)
        r.gate_lines.append(f"自動 {spec.raw}: {rendered[0]}" if spec.automatic else rendered[0])
        r.log(f"--- gate: {spec.raw}", *rendered)
        for pkg, count in (summary.get("packages") or {}).items():
            r.tests_run[pkg] = r.tests_run.get(pkg, 0) + count
        verdict = summary["verdict"]
        if verdict not in PASSING_VERDICTS:
            raise Rejected("gate", [f"{spec.raw}: {verdict}"] + _gate_failure_lines(summary),
                           code=gate.EXIT_SLOT if verdict == "SLOT-TIMEOUT" else 1)
        if verdict == "RED-KNOWN":
            known = summary["failed"] + [f"unhandled {summary['known_red']['unhandled']}"] * bool(summary["known_red"]["unhandled"])
            r.hints.append(f"RED-KNOWN 放行（名單 {summary['known_red_file']} 要有出處）: {spec.raw}: " + ", ".join(known))


def note_untested_packages(r):
    missing = sorted(pkg for pkg in {go_package(p) for p in r.changed if _is_backend_go(p)} if not r.tests_run.get(pkg, 0))
    if missing:
        r.must_show.append("這包沒有測試跑到：" + ", ".join(missing))


def check_drift(a, before):
    after = fingerprint(a)
    moved = [p for p in sorted(set(before) | set(after)) if before.get(p) != after.get(p)]
    if moved:
        raise Rejected("drift", [f"changed while the gates ran: {p}" for p in moved])
    if gate._stop_reason:
        raise Rejected("interrupted", [gate._stop_reason])


def _write_file_list(a, r):
    path = pathlib.Path(a.out_dir, f"merge-files-{a.slice}.txt")
    path.write_text("".join(p + "\n" for p in r.changed), encoding="utf-8", errors="surrogateescape")
    return str(path)


def merge(a, r, before):
    # INVARIANT: merge_back lists the tree again, so without this list a file written after the drift check would land
    # in main past the scope step, the gates and verify_merge, which only looks at r.changed.
    res = _merge_back(a, apply=True, only_files=_write_file_list(a, r))
    r.log("--- merge_back --apply", res.stdout)
    if res.returncode == 0:
        return
    stop = _stop_lines(res.stdout)
    if stop is not None:
        raise Rejected("merge", stop)
    # WHY: merge_back copies file by file and an uncaught OSError also exits 1, the same code as its clean STOP;
    # only the main tree can say whether this is "nothing happened" or "half of it landed".
    copied = [p for p in r.changed if file_digest(pathlib.Path(a.root, p)) == before[p]
              and file_digest(pathlib.Path(a.snapshot, p)) != before[p]]
    tail = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
    if copied:
        raise PostMerge("merge", f"合回中途失敗，主樹已寫入 {len(copied)}/{len(r.changed)} 檔，其餘未寫（{tail}）；"
                                 f"已寫入: {', '.join(copied)}")
    raise ToolError(f"merge_back crashed before copying anything: {tail}")


def verify_merge(a, r, before):
    bad = [p for p in r.changed if file_digest(pathlib.Path(a.root, p)) != before[p]]
    if bad:
        raise RuntimeError("主樹內容不是閘跑過的版本: " + ", ".join(bad))


def _skeleton(r):
    lines = [f"\n### 收件 {r.slice}（recv.py {datetime.datetime.now():%Y-%m-%d %H:%M}）", "",
             f"- 改動檔數：{len(r.changed)}"]
    lines += [f"- 閘：{x}" for x in r.gate_lines]
    if r.narrow_reason is not None:
        lines.append(f"- --allow-narrow 理由：{r.narrow_reason}")
    if r.root_moved_reason is not None:
        lines.append(f"- --allow-root-moved 理由：{r.root_moved_reason}")
    lines.append(f"- 輸出：{r.out}")
    lines += [f"- 提示：{x}" for x in r.must_show + r.hints]
    lines += ["", "待主 session 判斷（留空＝還沒判斷）：", ""]
    lines += [f"- {x}：" for x in JUDGE_LABELS]
    return "\n".join(lines) + "\n"


def append_progress(a, r):
    path = pathlib.Path(a.progress)
    existing = path.read_bytes()
    with open(path, "a", encoding="utf-8") as f:
        if existing and not existing.endswith(b"\n"):
            f.write("\n")
        f.write(r.skeleton)


def run_board(a, r):
    res = subprocess.run([sys.executable, str(BOARD)], cwd=a.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         encoding="utf-8", errors="replace", timeout=BOARD_TIMEOUT_S)
    r.log("--- board", res.stdout)
    if res.returncode != 0:
        tail = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
        raise RuntimeError(f"exit {res.returncode}: {tail}")


def _after_merge(step, label, fn, *args):
    try:
        fn(*args)
    except Exception as e:
        raise PostMerge(step, f"已合回，但 {label} 失敗：{e}")


def receive(a, r):
    check_tree_state(a, r)
    check_require(a)
    before = preflight(a, r)
    check_scope(a, r)
    check_coverage(a, r)
    # WHY: the vets take seconds and the named gates can take minutes, so a slice that does not compile fails fast.
    run_gates(auto_gates(a, r) + a.gates, r)
    note_untested_packages(r)
    check_drift(a, before)
    if not a.apply:
        r.status = "DRY-RUN-OK"
        return
    merge(a, r, before)
    # WHY: compared against what the gates ran, not the tree as it is now; a write after the drift check is copied
    # by merge_back and would agree with a fresh read of the tree.
    _after_merge("verify", "sha 核對", verify_merge, a, r, before)
    r.skeleton = _skeleton(r)
    r.log("--- PROGRESS skeleton", r.skeleton)
    _after_merge("progress", "PROGRESS 寫入", append_progress, a, r)
    if not a.no_board:
        _after_merge("board", "board", run_board, a, r)
    r.status = "APPLIED"


def _capped(lines, limit, what):
    if len(lines) <= limit:
        return lines
    if limit < 1:
        return []
    return lines[:limit - 1] + [f"... {len(lines)} {what} total, see out"]


def _changed_lines(r, room):
    if not r.changed or room < 1:
        return []
    header = f"changed ({len(r.changed)}):"
    listed = min(MAX_CHANGED_LISTED, room - 1)
    if len(r.changed) <= listed:
        return [header] + [f"  {p}" for p in r.changed]
    if listed < 2:
        return [f"{header} see out"]
    return [header] + [f"  {p}" for p in r.changed[:listed - 1]] + [f"  ... {len(r.changed)} files total, see out"]


def render(r):
    first = f"RECV {r.slice} {r.status} files={len(r.changed)} gates={r.gates_run} out={r.out or '-'}"
    head = [first]
    passed = r.status in ("DRY-RUN-OK", "APPLIED")
    if r.status == "DRY-RUN-OK":
        head.append("未合回。機械檢查通過不等於收件通過，下列判斷還沒做。")
    elif r.status == "APPLIED":
        head.append("已合回。機械檢查通過不等於收件通過，下列判斷還沒做。")
    if r.narrow_reason is not None:
        head.append("--allow-narrow 理由：" + " ".join(r.narrow_reason.split("\n")))
    if r.root_moved_reason is not None:
        head.append("--allow-root-moved 理由：" + " ".join(r.root_moved_reason.split("\n")))
    judged = passed or r.status.startswith("MERGED-BUT-FAILED")
    if judged:
        judge = ["待主 session 判斷（未做）: " + "、".join(JUDGE_LABELS)]
    else:
        judge = ["待主 session 判斷: 被拒絕或工具錯誤，修好重收後再判斷"]
    judge += [f"  ! {x}" for x in r.must_show]
    # INVARIANT: the budget is spent in order of what a reader cannot get wrong: refusal reasons, then hints, then gate
    # lines, then the file list, which recv-<slice>.out always has in full.
    room = MAX_STDOUT_LINES - len(head) - len(judge)
    reasons = _capped(r.reasons, min(room, MAX_REASON_LINES), "reasons")
    room -= len(reasons)
    hints = [f"  ! {x}" for x in _capped(r.hints, room, "hints")] if judged else []
    room -= len(hints)
    gates = [f"gate {x}" for x in _capped(r.gate_lines, min(room, MAX_GATE_LINES), "gates")]
    room -= len(gates)
    return head + gates + reasons + _changed_lines(r, room) + judge + hints


def _write_detail(r, argv, lines):
    if not r.out:
        return None
    body = ["argv: " + " ".join(shlex.quote(str(x)) for x in argv), "", *lines, "", "--- reasons", *r.reasons,
            "--- changed", *r.changed, "--- hints", *r.must_show, *r.hints, "--- steps", *r.detail]
    try:
        pathlib.Path(r.out).write_text("\n".join(body) + "\n", encoding="utf-8", errors="backslashreplace")
    except OSError as e:
        return str(e)
    return None


def run_cli(argv):
    r = Run()
    code = 0
    try:
        a = parse_cli(argv, r)
        receive(a, r)
    except Rejected as e:
        r.status, r.reasons, code = f"REJECTED:{e.step}", list(e.reasons), e.code
    except PostMerge as e:
        r.status, r.reasons, code = f"MERGED-BUT-FAILED:{e.step}", [str(e)], 2
        if e.step == "progress":
            r.reasons.append("PROGRESS 骨架全文在 out，補貼上去")
    except Exception as e:  # noqa: BLE001
        # WHY: an uncaught exception would exit 1, which callers read as a clean rejection of the slice.
        r.status, r.reasons, code = "TOOL-ERROR", [f"{type(e).__name__}: {e}" if not isinstance(e, ToolError) else str(e)], 2
    lines = render(r)
    problem = _write_detail(r, argv, lines)
    if problem:
        lines.append(f"寫不進 out：{problem}")
        code = code or 2
    print("\n".join(lines))
    return code


def main(argv=None):
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, gate._on_signal)
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    return run_cli(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
