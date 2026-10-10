# -*- coding: utf-8 -*-
"""Shared helpers for the seven-phase scripts (stdlib only)."""
import hashlib
import pathlib
import re
import subprocess

DEFAULT_EXCLUDES = (".claude/PRPs/plans/", "tmp/")
# WHY: scratch and task-record dirs move during every review round (mutation logs, PROGRESS
# edits), so tree checks never treat changes under them as a lane writing outside the change.
SCRATCH_PREFIXES = ("tmp/", ".claude/PRPs/plans/")

_TEST_RE = re.compile(r"(_test\.go$|(^|/)tests?/|(^|/)test_[^/]*\.py$|_test\.py$|\.spec\.[jt]sx?$|\.test\.[jt]sx?$)")
_DOC_RE = re.compile(r"(\.md$|(^|/)docs/|openapi[^/]*\.ya?ml$)")


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=check).stdout


def toplevel(path="."):
    return pathlib.Path(git(path, "rev-parse", "--show-toplevel").strip())


def classify(path):
    """'test' | 'doc' | 'prod' — tests win over docs (a tests/README is still test-side)."""
    if _TEST_RE.search(path):
        return "test"
    if _DOC_RE.search(path):
        return "doc"
    return "prod"


def status_entries(root):
    """`git status` vs HEAD as (status, path, orig_path_or_None); untracked included, ignored not."""
    # WHY: -z output is unquoted (core.quotePath would otherwise C-quote non-ASCII paths) and
    # renames come as 'XY <to>\0<from>\0' instead of an ambiguous ' -> ' separator.
    raw = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "-z", "--untracked-files=all"],
                         capture_output=True, check=True).stdout.decode("utf-8", "surrogateescape")
    tokens = raw.split("\0")
    out, i = [], 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if not entry:
            continue
        status, path, orig = entry[:2], entry[3:], None
        if "R" in status or "C" in status:
            orig = tokens[i]
            i += 1
        out.append((status, path, orig))
    return out


def dirty_paths(root):
    """Every path `git status` touches, rename origins included."""
    out = set()
    for _, path, orig in status_entries(root):
        out.add(path)
        if orig:
            out.add(orig)
    return out


def changed_paths(root, excludes=DEFAULT_EXCLUDES):
    """Uncommitted changes vs HEAD as (status, path); untracked files included, ignored files not.

    A rename origin is reported as ('D ', origin) so patches and baselines record its removal."""
    excludes = tuple(excludes)
    entries = status_entries(root)
    listed = {path for _, path, _ in entries}
    out = []
    for status, path, orig in entries:
        if not path.startswith(excludes):
            out.append((status, path))
        if "R" in status and orig not in listed and not orig.startswith(excludes):
            listed.add(orig)
            out.append(("D ", orig))
    return out


def sha256_file(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def file_digest(path):
    """Baseline digest of one file: its sha256, or 'DELETED' when it does not exist."""
    return sha256_file(path) if pathlib.Path(path).exists() else "DELETED"


def read_baseline(baseline):
    """baseline.sha256 lines: '<sha>  <path>' or 'DELETED  <path>'."""
    entries = []
    for line in pathlib.Path(baseline).read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, path = line.split("  ", 1)
            entries.append((digest, path.strip()))
    return entries


def verify_tree(tree, baseline):
    """Return a list of mismatch strings for `tree` against `baseline`."""
    bad = []
    for digest, path in read_baseline(baseline):
        f = pathlib.Path(tree) / path
        if digest == "DELETED":
            if f.exists():
                bad.append(f"should be deleted: {path}")
        elif not f.exists():
            bad.append(f"missing: {path}")
        elif sha256_file(f) != digest:
            bad.append(f"differs: {path}")
    return bad


def is_worktree_root(path):
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return r.returncode == 0 and pathlib.Path(r.stdout.strip()).resolve() == pathlib.Path(path).resolve()


def outside_changes(tree, baseline, outside=None):
    """Warnings for dirty paths in `tree` that are neither in `baseline` nor under SCRATCH_PREFIXES.

    A path recorded in `outside` (mkpatch's outside.sha256) with an unchanged digest is a
    pre-existing change and stays silent. Returns None when `tree` is not the root of a git
    work tree (the check needs `git status`)."""
    if not is_worktree_root(tree):
        return None
    in_change = {path for _, path in read_baseline(baseline)}
    recorded = {path: digest for digest, path in read_baseline(outside)} if outside else {}
    warnings = []
    for path in sorted(dirty_paths(tree)):
        if path in in_change or path.startswith(SCRATCH_PREFIXES):
            continue
        if path not in recorded:
            warnings.append(f"outside the change: {path}")
        elif file_digest(pathlib.Path(tree) / path) != recorded[path]:
            warnings.append(f"pre-existing change modified: {path}")
    return warnings
