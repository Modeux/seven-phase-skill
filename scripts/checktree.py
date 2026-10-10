# -*- coding: utf-8 -*-
"""Verify one or more trees (main checkout, snapshot, mutation tree...) against a sha256 baseline.

What a pass proves, and what it does not:
  - every file in baseline.sha256 is verified exactly (content, or still deleted); a mismatch exits 1;
  - other dirty paths in the tree (per `git status`) are listed as WARNINGS: 'outside the change'
    for a path nobody recorded, 'pre-existing change modified' for a path recorded in
    outside.sha256 whose content moved since mkpatch ran. Warnings exit 0 unless --strict;
  - NOT covered: anything under tmp/ and .claude/PRPs/plans/ (scratch / task records), gitignored
    files, and trees that are not the root of a git work tree (the extra check is skipped).

Run before and after a review round: the main checkout should match the baseline afterwards, and a
mutation tree must match again after mutations are reverted.

Usage: python3 checktree.py --baseline tmp/<slug>/review/baseline.sha256 [--outside PATH] [--strict] <tree> [<tree> ...]
  --outside  pre-existing dirty paths (default: outside.sha256 next to the baseline, if present)
  --strict   exit 1 on warnings too
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _common import outside_changes, read_baseline, verify_tree  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--outside", default=None)
    # WHY: warnings do not fail by default because the main checkout is shared with concurrent
    # sessions and the owner (it routinely carries another task's uncommitted files); a hard
    # failure there would cry wolf on every round.
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("trees", nargs="+")
    a = ap.parse_args()
    outside = a.outside
    if outside is None:
        default = pathlib.Path(a.baseline).parent / "outside.sha256"
        outside = default if default.exists() else None
    n = len(read_baseline(a.baseline))
    failed = False
    for t in a.trees:
        bad = verify_tree(t, a.baseline)
        print(f"{t}: checked {n}, mismatches {len(bad)}")
        for b in bad[:20]:
            print("   ", b)
        failed |= bool(bad)
        extra = outside_changes(t, a.baseline, outside)
        if extra is None:
            print("    extra check skipped: not the root of a git work tree")
            continue
        for w in extra[:50]:
            print("    warning:", w)
        if len(extra) > 50:
            print(f"    ... {len(extra) - 50} more warnings")
        failed |= a.strict and bool(extra)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
