# -*- coding: utf-8 -*-
"""Shared helpers for the seven-phase scripts (stdlib only)."""
import hashlib
import pathlib
import re
import subprocess

DEFAULT_EXCLUDES = (".claude/PRPs/plans/", "tmp/")

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


def changed_paths(root, excludes=DEFAULT_EXCLUDES):
    """Uncommitted changes vs HEAD as (status, path); untracked files included, ignored files not."""
    out = []
    for line in git(root, "status", "--porcelain", "--untracked-files=all").splitlines():
        if not line:
            continue
        status, path = line[:2], line[3:]
        if " -> " in path:  # rename: take the new path
            path = path.split(" -> ", 1)[1]
        if path.startswith(tuple(excludes)):
            continue
        out.append((status, path))
    return out


def sha256_file(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def read_baseline(baseline):
    """baseline.sha256 lines: '<sha>  <path>' or 'DELETED  <path>'."""
    entries = []
    for line in pathlib.Path(baseline).read_text().splitlines():
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
