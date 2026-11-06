# -*- coding: utf-8 -*-
"""Prove tests catch bugs: apply each mutation, run its gate, restore, and report KILLED/SURVIVED/INVALID.

Usage:
  python3 mutate.py --tree T --manifest M.txt --out RUN.out [--only NAME ...] [--allow-main] [--no-baseline]
  python3 mutate.py --tree T --recover [--force] [--out RUN.out]

Manifest (or .json: {"test": "...", "mutations": [{"name","desc","test","files":[{"path","old","new"}]}]}):
  test: fe --project frontend --include projects/frontend/src/app/x.spec.ts     # default for the file
  === M-a what this breaks
  test: go --pkg ./internal/foo --run TestX                                     # optional override
  file: angular-workspace/projects/frontend/src/app/x.ts
  <<<<<<< old
  original text (may span lines)
  =======
  replacement
  >>>>>>> new
`test:` is gate.py arguments without --tree/--out/--expect/--label/--help: go test, fe or ai pytest only, since
--build, --vet, ruff and mypy run no tests. A mutation may have several file blocks; paths are relative to the tree.
Use .json when the text contains a bare ======= line.

Result: RED = KILLED; GREEN or RED-KNOWN = SURVIVED; BUILD-FAIL, NO-TESTS, INCOMPLETE, a tool error, an `old`
that does not occur exactly once, a file edited after planning, a pytest failure naming SyntaxError, IndentationError,
ImportError or ModuleNotFoundError, or a baseline that is not green = INVALID (a mutant that did not compile or load
killed nothing; a test already red cannot prove a kill). Each distinct `test:` runs once unmutated first;
--no-baseline skips that.

Go files under a go test use -overlay: the tree is never written, so this works on any tree and alongside other
overlay runs. Everything else is written into the tree, run, and restored, which is refused on the main checkout
without --allow-main. A run holding the tree (shared for overlay-only, exclusive otherwise) makes a conflicting one
a tool error at once; it never waits.

If a run is SIGKILLed, the next run and `gate.py` on that tree refuse to start until `--recover` restores the originals
from <out>.journal/ and verifies their sha. --recover will not overwrite a file someone else edited; --force does.
It refuses a journal written for another tree, even with --force. If the journal itself is gone nothing can be
restored: --force only clears the pointer that blocks new runs, and any mutant stays in the tree.

Exit: 0 all KILLED; 1 some SURVIVED; 2 some INVALID, or a tool error, or interrupted, or a restore that failed.
First stdout line: MUTATE KILLED=N SURVIVED=N INVALID=N exit=N out=<path>. --out holds per-mutation evidence;
<out>.d/ the full gate outputs; <out>.summary.json the results.
"""
import argparse
import collections
import fcntl
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gate  # noqa: E402
from gate import ToolError  # noqa: E402

EXIT_SURVIVED = 1
EXIT_INVALID = 2
MUTABLE_MODES = ("go", "fe", "ai")
FORBIDDEN_TEST_FLAGS = ("--tree", "--out", "--expect", "--label")
EVIDENCE_CAP = 256 * 1024
MAX_ROWS = 25
LOCK_NAME = "mutate.lock"
LOAD_ERROR_RE = re.compile(r"\b(SyntaxError|IndentationError|ImportError|ModuleNotFoundError)\b")
TEMP_PREFIX = ".mutate-tmp-"
# WHY: macOS Docker Desktop bind mounts can serve new content with the old length right after a host write;
# a mutant truncated that way fails as a syntax error, which would read as INVALID rather than as the real result.
BIND_SETTLE_DEFAULT_S = 3

Hunk = collections.namedtuple("Hunk", "path old new")
Mutation = collections.namedtuple("Mutation", "name desc test hunks")


class RestoreConflict(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ToolError(message)


def _asks_for_help(arg):
    name = arg.split("=", 1)[0]
    return arg == "-h" or (name.startswith("--") and len(name) > 2 and "--help".startswith(name))


def _check_test(argv, where):
    if not argv or argv[0] not in MUTABLE_MODES:
        raise ToolError(f"{where}: test must start with one of {MUTABLE_MODES}, got: {' '.join(argv)}")
    for a in argv:
        if a.split("=", 1)[0] in FORBIDDEN_TEST_FLAGS:
            raise ToolError(f"{where}: test must not set {a}; mutate.py owns it")
        # WHY: argparse would print usage and exit 0 from inside the parse below, with no MUTATE line at all.
        if _asks_for_help(a):
            raise ToolError(f"{where}: test asks for help instead of running: {a}")
    ns = gate.parse_args(argv + ["--tree", "/unused"])
    if ns.mode == "go" and (ns.build or ns.vet):
        raise ToolError(f"{where}: --build/--vet run no tests, so they cannot kill a mutant")
    if ns.mode == "ai" and ns.kind != "pytest":
        raise ToolError(f"{where}: --kind {ns.kind} runs no tests, so a lint finding would read as a killed mutant")


def _parse_text(text):
    default, items, cur, path = None, [], None, None
    lines = text.splitlines()
    i = 0

    def fail(msg):
        raise ToolError(f"manifest line {i + 1}: {msg}")

    while i < len(lines):
        line = lines[i]
        if line.startswith("=== "):
            parts = line[4:].strip().split(None, 1)
            if not parts:
                fail("mutation has no name")
            cur = {"name": parts[0], "desc": parts[1] if len(parts) > 1 else "", "test": None, "files": []}
            items.append(cur)
            path = None
        elif line.startswith("test:"):
            value = shlex.split(line[5:])
            if cur is None:
                default = value
            else:
                cur["test"] = value
        elif line.startswith("file:"):
            if cur is None:
                fail("file: before the first === mutation")
            path = line[5:].strip()
        elif line.strip() == "<<<<<<< old":
            if cur is None or not path:
                fail("old/new block without file:")
            old, new, seen_sep = [], [], False
            i += 1
            while i < len(lines) and lines[i].strip() != ">>>>>>> new":
                if lines[i] == "=======":
                    if seen_sep:
                        fail("second ======= in a block is ambiguous; use the .json form")
                    seen_sep = True
                else:
                    (new if seen_sep else old).append(lines[i])
                i += 1
            if i >= len(lines):
                fail("block never closed with >>>>>>> new")
            if not seen_sep:
                fail("block has no ======= separator")
            cur["files"].append({"path": path, "old": "\n".join(old), "new": "\n".join(new)})
        elif line.strip() and not line.lstrip().startswith("#"):
            fail(f"unexpected text: {line[:80]}")
        i += 1
    return default, items


def _parse_json(text):
    try:
        data = json.loads(text)
        return data.get("test"), data["mutations"]
    except (ValueError, KeyError, AttributeError) as e:
        raise ToolError(f"bad manifest JSON: {e}")


def load_manifest(path):
    """Parse a manifest into Mutations, validating everything that can be checked without the tree."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise ToolError(f"cannot read manifest: {e}")
    default, items = _parse_json(text) if str(path).endswith(".json") else _parse_text(text)
    if isinstance(default, str):
        default = shlex.split(default)
    if not items:
        raise ToolError(f"manifest {path} has no mutations")
    out, names = [], set()
    for item in items:
        name = item.get("name")
        if not name or name in names:
            raise ToolError(f"mutation name missing or duplicated: {name}")
        names.add(name)
        test = item.get("test") or default
        if isinstance(test, str):
            test = shlex.split(test)
        if not test:
            raise ToolError(f"{name}: no test: line and no default")
        _check_test(test, name)
        files = item.get("files") or []
        if not files:
            raise ToolError(f"{name}: no file blocks")
        hunks = [Hunk(f["path"], f["old"], f["new"]) for f in files]
        out.append(Mutation(name, item.get("desc", ""), test, hunks))
    return out


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _sha_file(path):
    try:
        return _sha(pathlib.Path(path).read_bytes())
    except OSError:
        return None


def _atomic_write(path, data, mode=None):
    path = pathlib.Path(path)
    fd, tmp = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode if mode is not None else 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Tree:
    def __init__(self, path):
        self.root = os.path.realpath(path)
        if not os.path.isdir(self.root):
            raise ToolError(f"--tree: no such directory: {path}")
        r = subprocess.run(["git", "-C", self.root, "rev-parse", "--show-toplevel", "--git-dir", "--git-common-dir"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise ToolError(f"--tree {path} is not a git tree: {r.stderr.strip()}")
        top, git_dir, common = r.stdout.splitlines()
        if os.path.realpath(top) != self.root:
            raise ToolError(f"--tree {path} is not the top of its git tree ({top})")
        self.git_dir = os.path.realpath(os.path.join(self.root, git_dir))
        self.common_dir = os.path.realpath(os.path.join(self.root, common))
        self.is_main = self.git_dir == self.common_dir
        self.pointer = gate.mutate_pointer(self.root)
        self.lock_path = pathlib.Path(self.git_dir, LOCK_NAME)
        self.lock_fd = None

    def rel(self, p):
        rel = os.path.relpath(os.path.realpath(os.path.join(self.root, p)), self.root)
        if rel == ".." or rel.startswith(".." + os.sep):
            raise ToolError(f"file is outside the tree: {p}")
        return rel

    def lock(self, shared=False):
        """Take the per-worktree lock without waiting; a crashed holder releases it by dying."""
        # INVARIANT: overlay runs read the whole tree, so one sharing a tree with an in-tree run can be killed by that
        # run's mutant and report a kill it did not cause. Shared lets overlay runs coexist; exclusive excludes both.
        try:
            self.lock_fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as e:
            raise ToolError(f"cannot open {self.lock_path}: {e}")
        try:
            fcntl.flock(self.lock_fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.lock_fd)
            self.lock_fd = None
            raise ToolError(f"another mutate.py is running on {self.root} in a way that conflicts with this run; wait for it to finish")

    def active_journal(self, out=None):
        """The leftover journal, if any. The pointer finds it even when this run names a different --out."""
        pending = gate.pending_mutation(self.root)
        if pending is not None:
            return pending
        if out is not None:
            own = pathlib.Path(str(out) + ".journal")
            if own.exists():
                return own
        return None


def _write_journal(tree, journal, entries):
    # INVARIANT: originals, journal and pointer are durable before the first tree write, so a SIGKILL at
    # any later instruction leaves --recover everything it needs. Reordering this loses the original.
    files = journal / "files"
    files.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, e in enumerate(entries):
        backup = f"files/{i}"
        _atomic_write(journal / backup, e["orig"])
        rows.append({"path": e["rel"], "sha_orig": _sha(e["orig"]), "sha_mut": _sha(e["mut"]), "backup": backup, "mode": e["mode"]})
    doc = {"tree": tree.root, "pid": os.getpid(), "started": time.strftime("%Y-%m-%d %H:%M:%S"), "entries": rows}
    _atomic_write(journal / "journal.json", json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8"))
    _atomic_write(tree.pointer, str(journal).encode("utf-8"))


def _restore(tree, journal, force=False):
    """Restore from the journal and verify; returns (restored, already clean). Raises RestoreConflict."""
    doc = json.loads((journal / "journal.json").read_text(encoding="utf-8"))
    restored, clean, conflicts = 0, 0, []
    for e in doc["entries"]:
        path = pathlib.Path(tree.root, e["path"])
        current = _sha_file(path)
        if current == e["sha_orig"]:
            clean += 1
            continue
        if current != e["sha_mut"] and not force:
            conflicts.append(e["path"])
            continue
        _atomic_write(path, (journal / e["backup"]).read_bytes(), e["mode"])
        if _sha_file(path) != e["sha_orig"]:
            raise RestoreConflict(f"restore of {e['path']} did not verify")
        restored += 1
    if conflicts:
        raise RestoreConflict("edited by someone else since the mutation, not overwritten: " + ", ".join(conflicts))
    # WHY: a kill between mkstemp and os.replace strands the temp file next to its target, in the tree.
    for parent in {pathlib.Path(tree.root, e["path"]).parent for e in doc["entries"]}:
        for stray in parent.glob(TEMP_PREFIX + "*"):
            stray.unlink()
    shutil.rmtree(journal)
    if tree.pointer.exists():
        tree.pointer.unlink()
    return restored, clean


def _load_journal(journal):
    """The journal document, or None when it is missing or unreadable and so cannot drive a restore."""
    try:
        doc = json.loads((journal / "journal.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("entries"), list) else None


def _lost_journal_text(tree, journal):
    return (f"原檔備份不見了（{journal}），樹可能還是突變，自己用 `git -C {tree.root} status` 查；"
            f"查完用 `python3 mutate.py --tree {tree.root} --recover --force` 解開：不會還原任何檔案、會刪掉殘留的 journal，突變若還在就留在樹上")


def _clear_lost_journal(tree, journal, force):
    if not force:
        raise ToolError(_lost_journal_text(tree, journal))
    if tree.pointer.exists():
        tree.pointer.unlink()
    if journal.exists():
        shutil.rmtree(journal)
    print(f"MUTATE RECOVER CLEARED nothing restored, the backup is gone: {journal}. "
          f"The tree may still hold a mutant; check it with `git -C {tree.root} status`")
    return 0


def recover(args):
    tree = Tree(args.tree)
    tree.lock()
    journal = tree.active_journal(args.out)
    if journal is None:
        raise ToolError(f"nothing to recover in {tree.root}: no mutate journal")
    doc = _load_journal(journal)
    if doc is None:
        return _clear_lost_journal(tree, journal, args.force)
    owner = doc.get("tree")
    # INVARIANT: checked before any restore and regardless of --force. This tree's files match another tree's
    # originals, so the journal would be deleted as "already clean" while its own tree stays mutated with no backup.
    if not owner or os.path.realpath(owner) != tree.root:
        raise ToolError(f"{journal} belongs to {owner}, not {tree.root}; run --recover with --tree {owner}")
    try:
        restored, clean = _restore(tree, journal, force=args.force)
    except RestoreConflict as e:
        print(f"MUTATE RECOVER FAILED: {e}\noriginals are in {journal}/files; pass --recover --force to overwrite with them")
        return EXIT_INVALID
    except OSError as e:
        left = [x["path"] for x in doc["entries"] if _sha_file(os.path.join(tree.root, x["path"])) != x["sha_orig"]]
        print(f"MUTATE RECOVER FAILED: {e}\nnot restored: {', '.join(left) or 'none, but the journal was not removed'}\noriginals are in {journal}/files")
        return EXIT_INVALID
    print(f"MUTATE RECOVER OK restored={restored} already_clean={clean} journal removed: {journal}")
    return 0


def _plan_contents(tree, m):
    """Return ([{rel, orig, mut, mode}], None), or (None, reason) when the mutation is INVALID."""
    by_path, order = {}, []
    for h in m.hunks:
        rel = tree.rel(h.path)
        if rel not in by_path:
            path = pathlib.Path(tree.root, rel)
            try:
                data = path.read_bytes()
            except OSError as e:
                return None, f"cannot read {rel}: {e.strerror}"
            by_path[rel] = {"rel": rel, "orig": data, "mut": data, "mode": path.stat().st_mode & 0o7777}
            order.append(rel)
        if not h.old:
            return None, f"{rel}: empty old text"
        if h.old == h.new:
            return None, f"{rel}: old and new are identical"
        old = h.old.encode("utf-8")
        count = by_path[rel]["mut"].count(old)
        if count != 1:
            return None, f"{rel}: old text found {count} times, need exactly 1"
        by_path[rel]["mut"] = by_path[rel]["mut"].replace(old, h.new.encode("utf-8"))
    return [by_path[r] for r in order], None


def _split_overlay_arg(test):
    rest, overlay, i = [], None, 0
    while i < len(test):
        if test[i] == "--overlay" and i + 1 < len(test):
            overlay = test[i + 1]
            i += 2
        elif test[i].startswith("--overlay="):
            overlay = test[i].split("=", 1)[1]
            i += 1
        else:
            rest.append(test[i])
            i += 1
    return rest, overlay


def _use_overlay(m):
    return m.test[0] == "go" and all(h.path.endswith(".go") for h in m.hunks)


def _pytest_load_error(s):
    """The line showing a pytest failure was the mutant failing to load, or None."""
    if s["mode"] != "ai":
        return None
    # WHY: pytest cuts the summary message to the terminal width, so the traceback's E lines have to be read too.
    lines = [x for f in s["failures"] for x in f["lines"]]
    lines += [x for x in pathlib.Path(s["out"]).read_text(encoding="utf-8", errors="replace").splitlines() if x.startswith("E ")]
    # WHY: tests import lazily (function-local imports, conftest), so a mutant that cannot parse fails inside a test
    # instead of at collection, and would otherwise count as killed by a test that never reached the code.
    return next((gate._short(x) for x in lines if LOAD_ERROR_RE.search(x)), None)


class Runner:
    def __init__(self, args, tree, out):
        self.args = args
        self.tree = tree
        self.out = out
        self.evidence = pathlib.Path(str(out) + ".d")
        self.baselines = {}
        self.rows = []
        self.stop = None
        self.fatal = None

    def run_test(self, test, out_name):
        self.evidence.mkdir(parents=True, exist_ok=True)
        argv = list(test) + ["--tree", self.tree.root, "--out", str(self.evidence / f"{out_name}.out")]
        return gate.run_gate(argv)

    def baseline(self, test):
        key = tuple(test)
        if key not in self.baselines:
            try:
                s = self.run_test(test, f"baseline-{len(self.baselines)}")
                ok = s["verdict"] in ("GREEN", "RED-KNOWN")
                self.baselines[key] = (ok, f"baseline {s['verdict']} before any mutation: {s['out']}")
            except ToolError as e:
                self.baselines[key] = (False, f"baseline tool error: {e}")
        return self.baselines[key]

    def result_from(self, s):
        verdict = s["verdict"]
        if verdict == "RED":
            load_error = _pytest_load_error(s)
            if load_error:
                return "INVALID", [], f"gate RED, but the mutant did not load: {load_error}"
            return "KILLED", s["failed"], ""
        if verdict in ("GREEN", "RED-KNOWN"):
            return "SURVIVED", [], f"gate {verdict}"
        build = [x for x in s["build"] if not x.startswith("#")]
        detail = (build + s["incomplete"] + s["no_tests"] + [""])[0]
        return "INVALID", [], f"gate {verdict}: {detail}"

    def overlay_run(self, m, entries):
        test, extra = _split_overlay_arg(m.test)
        replace = {}
        if extra:
            try:
                replace = json.loads(pathlib.Path(extra).read_text(encoding="utf-8"))["Replace"]
            except (OSError, ValueError, KeyError) as e:
                return "INVALID", [], f"cannot read test overlay {extra}: {e}", None
        work = self.evidence / "overlay" / m.name
        work.mkdir(parents=True, exist_ok=True)
        for i, e in enumerate(entries):
            key = os.path.join(self.tree.root, e["rel"])
            if key in replace:
                return "INVALID", [], f"test overlay already replaces {e['rel']}", None
            copy = work / f"{i}-{os.path.basename(e['rel'])}"
            copy.write_bytes(e["mut"])
            replace[key] = str(copy)
        overlay = work / "overlay.json"
        overlay.write_text(json.dumps({"Replace": replace}, ensure_ascii=False), encoding="utf-8")
        try:
            s = self.run_test(test + ["--overlay", str(overlay)], m.name)
        except ToolError as err:
            return "INVALID", [], f"tool error: {err}", None
        result, killers, reason = self.result_from(s)
        changed = self.changed_since_plan(entries)
        if changed:
            return "INVALID", [], "file changed during the run, result unusable: " + ", ".join(changed), s
        return result, killers, reason, s

    def changed_since_plan(self, entries):
        return [e["rel"] for e in entries if _sha_file(os.path.join(self.tree.root, e["rel"])) != _sha(e["orig"])]

    def in_tree_run(self, m, entries):
        # INVARIANT: the baseline ran since planning. Writing now would test stale content, and the restore would put
        # that stale content back over whatever the other writer did.
        moved = self.changed_since_plan(entries)
        if moved:
            return "INVALID", [], "changed after planning; nothing written, the edit is kept: " + ", ".join(moved), None
        journal = pathlib.Path(str(self.out) + ".journal")
        try:
            _write_journal(self.tree, journal, entries)
        except OSError as err:
            # WHY: the pointer is written last, so a failure here means the tree was never touched.
            shutil.rmtree(journal, ignore_errors=True)
            return "INVALID", [], f"{err}: could not write the journal", None
        s, outcome = None, None
        try:
            for e in entries:
                _atomic_write(os.path.join(self.tree.root, e["rel"]), e["mut"], e["mode"])
            if m.test[0] == "ai":
                os.sync()
                time.sleep(float(os.environ.get("MUTATE_BIND_SETTLE_S", BIND_SETTLE_DEFAULT_S)))
            try:
                s = self.run_test(m.test, m.name)
                outcome = self.result_from(s)
            except ToolError as err:
                outcome = ("INVALID", [], f"tool error: {err}")
        except OSError as err:
            outcome = ("INVALID", [], f"{err}: could not apply or evaluate the mutant")
        finally:
            # INVARIANT: every path out of a mutated tree goes through here. Handlers only set a flag
            # (see _on_signal), so no signal can raise between the write above and this restore.
            try:
                _restore(self.tree, journal)
            except RestoreConflict as err:
                self.fatal = f"{err}; originals in {journal}/files, use --recover --force after checking"
                outcome = ("INVALID", [], str(err))
            except (OSError, ValueError, KeyError) as err:
                left = ", ".join(self.changed_since_plan(entries)) or "none, but the journal was not removed"
                self.fatal = f"{err}; not restored: {left}; originals in {journal}/files, fix the cause and run --recover"
                outcome = ("INVALID", [], self.fatal)
        return outcome + (s,)

    def process(self, m):
        row = {"name": m.name, "desc": m.desc, "test": " ".join(shlex.quote(x) for x in m.test), "hunks": m.hunks,
               "result": "INVALID", "killed_by": [], "reason": "", "gate_verdict": None, "gate_out": None, "shas": []}
        if self.stop or self.fatal:
            row["reason"] = f"not run: {self.stop or 'earlier restore failed'}"
            return row
        entries, reason = _plan_contents(self.tree, m)
        if reason:
            row["reason"] = reason
            return row
        if not self.args.no_baseline:
            ok, why = self.baseline(m.test)
            if not ok:
                row["reason"] = why
                return row
        if self.stop:
            row["reason"] = f"not run: {self.stop}"
            return row
        run = self.overlay_run if _use_overlay(m) else self.in_tree_run
        result, killers, reason, s = run(m, entries)
        row.update(result=result, killed_by=killers, reason=reason)
        if s:
            row.update(gate_verdict=s["verdict"], gate_out=s["out"])
        row["shas"] = [(e["rel"], _sha(e["orig"]), _sha_file(os.path.join(self.tree.root, e["rel"]))) for e in entries]
        if self.stop and result == "INVALID":
            row["reason"] = f"interrupted: {self.stop}"
        return row


def _counts(rows):
    return {k: sum(r["result"] == k for r in rows) for k in ("KILLED", "SURVIVED", "INVALID")}


def _exit_code(rows, runner):
    c = _counts(rows)
    if c["INVALID"] or runner.stop or runner.fatal:
        return EXIT_INVALID
    return EXIT_SURVIVED if c["SURVIVED"] else 0


def _first_line(rows, code, out):
    c = _counts(rows)
    return f"MUTATE KILLED={c['KILLED']} SURVIVED={c['SURVIVED']} INVALID={c['INVALID']} exit={code} out={out}"


def _tail(text, width=90):
    return text if len(text) <= width else "…" + text[-width:]


def _row_line(r):
    if r["result"] == "KILLED":
        detail = "; ".join(_tail(x) for x in r["killed_by"][:2])
        if len(r["killed_by"]) > 2:
            detail += f" (+{len(r['killed_by']) - 2})"
    else:
        detail = _tail(r["reason"], 140)
    return f"{r['name']} | {r['result']} | {detail}"


def _table(rows):
    # WHY: problems first, because a long run must not push the rows that need action past the line cap.
    ordered = sorted(rows, key=lambda r: {"INVALID": 0, "SURVIVED": 1, "KILLED": 2}[r["result"]])
    lines = [_row_line(r) for r in ordered[:MAX_ROWS]]
    if len(ordered) > MAX_ROWS:
        lines.append(f"... {len(ordered)} mutations total, first {MAX_ROWS} listed")
    return lines


def _evidence_section(r):
    lines = [f"=== {r['name']} {r['desc']}".rstrip(), f"result: {r['result']}", f"test: {r['test']}"]
    if r["killed_by"]:
        lines.append("killed by: " + ", ".join(r["killed_by"]))
    if r["reason"]:
        lines.append(f"reason: {r['reason']}")
    if r["gate_verdict"]:
        lines.append(f"gate verdict: {r['gate_verdict']}")
    for rel, before, after in r["shas"]:
        lines.append(f"sha {rel}: before={before} after={after}")
    for h in r["hunks"]:
        lines += [f"file: {h.path}", "<<<<<<< old", h.old, "=======", h.new, ">>>>>>> new"]
    if r["gate_out"]:
        p = pathlib.Path(r["gate_out"])
        data = p.read_bytes() if p.exists() else b""
        lines.append(f"--- gate output ({p})")
        lines.append(data[:EVIDENCE_CAP].decode("utf-8", "replace"))
        if len(data) > EVIDENCE_CAP:
            lines.append(f"... cut at {EVIDENCE_CAP} bytes of {len(data)}; the rest is in {p}")
    return "\n".join(lines)


def _write_evidence(out, rows, runner):
    code = _exit_code(rows, runner)
    body = [_first_line(rows, code, out)] + _table(rows)
    if runner.fatal:
        body.append(f"RESTORE FAILED: {runner.fatal}")
    text = "\n".join(body) + "\n\n" + "\n\n".join(_evidence_section(r) for r in rows) + "\n"
    pathlib.Path(out).write_text(text, encoding="utf-8")
    c = _counts(rows)
    summary = {
        "killed": c["KILLED"], "survived": c["SURVIVED"], "invalid": c["INVALID"], "exit": code, "out": str(out),
        "tree": runner.tree.root, "interrupted": runner.stop, "restore_error": runner.fatal,
        "mutations": [{k: r[k] for k in ("name", "desc", "test", "result", "killed_by", "reason", "gate_verdict", "gate_out")} for r in rows],
    }
    pathlib.Path(str(out) + ".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return code


def run(args):
    if not args.manifest or not args.out:
        raise ToolError("--manifest and --out are required unless --recover")
    tree = Tree(args.tree)
    mutations = load_manifest(args.manifest)
    if args.only:
        known = {m.name for m in mutations}
        missing = [n for n in args.only if n not in known]
        if missing:
            raise ToolError(f"--only names not in the manifest: {', '.join(missing)}")
        mutations = [m for m in mutations if m.name in args.only]
    for m in mutations:
        for h in m.hunks:
            tree.rel(h.path)
    out = pathlib.Path(os.path.abspath(args.out))
    writes_tree = any(not _use_overlay(m) for m in mutations)
    if writes_tree and tree.is_main and not args.allow_main:
        raise ToolError(f"{tree.root} is the main checkout; mutations that write the tree need --allow-main")
    # INVARIANT: lock before the leftover check. A run that dies between the two would otherwise leave a mutant this
    # one never saw, and a live run's pointer must read as "wait", not as "recover".
    tree.lock(shared=not writes_tree)
    leftover = tree.active_journal(out)
    if leftover is not None:
        if _load_journal(leftover) is None:
            raise ToolError(_lost_journal_text(tree, leftover))
        raise ToolError(f"{leftover} is left from an unfinished run, so the tree may still be mutated. "
                        f"If no other mutate is running: python3 mutate.py --tree {tree.root} --recover")
    out.parent.mkdir(parents=True, exist_ok=True)
    # INVARIANT: a summary only describes the latest run at this --out; a kill must not leave an earlier one to be believed.
    stale = pathlib.Path(str(out) + ".summary.json")
    if stale.exists():
        stale.unlink()

    runner = Runner(args, tree, out)

    def on_signal(signum, _frame):
        # INVARIANT: only set flags here. Raising would let a signal land between the mutation and its
        # restore; the gate's poll loop notices the stop request and returns normally instead.
        runner.stop = f"interrupted by {signal.Signals(signum).name}"
        gate.request_stop(runner.stop)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_signal)

    rows = []
    for m in mutations:
        rows.append(runner.process(m))
        _write_evidence(out, rows, runner)
    code = _write_evidence(out, rows, runner)
    print(_first_line(rows, code, out))
    print("\n".join(_table(rows)))
    if runner.stop:
        print(runner.stop)
    if runner.fatal:
        print(f"RESTORE FAILED: {runner.fatal}")
    return code


def main(argv=None):
    ap = _Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", required=True)
    ap.add_argument("--manifest")
    ap.add_argument("--out")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--allow-main", dest="allow_main", action="store_true")
    ap.add_argument("--no-baseline", dest="no_baseline", action="store_true")
    ap.add_argument("--recover", action="store_true")
    ap.add_argument("--force", action="store_true", help="with --recover: overwrite files edited since the mutation")
    try:
        args = ap.parse_args(argv)
        return recover(args) if args.recover else run(args)
    except ToolError as e:
        print(f"MUTATE TOOL-ERROR: {e}")
        return EXIT_INVALID
    except Exception as e:
        # INVARIANT: an uncaught traceback exits 1, which callers read as "some SURVIVED".
        traceback.print_exc()
        print(f"MUTATE TOOL-ERROR: {type(e).__name__}: {e}")
        return EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
