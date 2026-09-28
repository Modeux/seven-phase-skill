# -*- coding: utf-8 -*-
"""Copy what each fix/implementation tree changed (relative to the frozen snapshot) back into the main checkout.

Safety checks before anything is copied:
  - a file may be changed by only ONE tree (overlap = the writer matrix was wrong -> stop, fix the plan);
  - the main checkout's copy must still equal the snapshot's (someone else edited it -> stop, merge by hand).
Deletions inside a tree are reported, never applied.

Usage:
  python3 merge_back.py --snapshot tmp/<slug>/review/wt-snap --tree G1=<path> --tree P=<path>        # dry run
  python3 merge_back.py ... --apply
"""
import argparse
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _common import git, toplevel  # noqa: E402

SKIP_PARTS = {".mut", "node_modules", ".venv", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def tree_changes(tree, snapshot):
    changed, deleted = [], []
    for line in git(tree, "status", "--porcelain", "--untracked-files=all").splitlines():
        if not line:
            continue
        path = line[3:].split(" -> ")[-1]
        parts = set(pathlib.PurePosixPath(path).parts)
        if parts & SKIP_PARTS or path.startswith("tmp/"):
            continue
        a, b = tree / path, snapshot / path
        if not a.exists():
            if b.exists():
                deleted.append(path)
            continue
        if not b.exists() or a.read_bytes() != b.read_bytes():
            changed.append(path)
    return changed, deleted


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--tree", action="append", required=True, help="LABEL=path (repeatable)")
    ap.add_argument("--root", default=None, help="main checkout (default: git toplevel of cwd)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    root = pathlib.Path(a.root) if a.root else toplevel()

    def resolve(p):
        p = pathlib.Path(p)
        return p if p.is_absolute() else root / p

    snapshot = resolve(a.snapshot)
    owner, problems = {}, []
    for spec in a.tree:
        label, _, p = spec.partition("=")
        changed, deleted = tree_changes(resolve(p), snapshot)
        for d in deleted:
            problems.append(f"{label}: deletes {d} (apply by hand)")
        for f in changed:
            if f in owner:
                problems.append(f"overlap: {f} changed by {owner[f][0]} and {label}")
            owner[f] = (label, resolve(p))
    for f, (label, _) in owner.items():
        m, s = root / f, snapshot / f
        if s.exists() and (not m.exists() or m.read_bytes() != s.read_bytes()):
            problems.append(f"main differs from snapshot: {f} ({label})")
        if not s.exists() and m.exists():
            problems.append(f"main already has new file: {f} ({label})")

    for f in sorted(owner):
        print(f"{owner[f][0]:<6} {f}")
    print(f"{len(owner)} files")
    if problems:
        print("\nSTOP:")
        for p in problems:
            print("  ", p)
        sys.exit(1)
    if not a.apply:
        print("dry run - re-run with --apply to copy")
        return
    for f, (_, tree) in owner.items():
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tree / f, root / f)
    print("copied.")


if __name__ == "__main__":
    main()
