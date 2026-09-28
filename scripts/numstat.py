# -*- coding: utf-8 -*-
"""Line counts of a change split into prod / test / doc, for the review-size tier.

Usage:
  python3 numstat.py                       # uncommitted changes (incl. untracked) vs HEAD, repo = cwd
  python3 numstat.py --base <sha>          # everything since <sha> (commits + working tree)
  python3 numstat.py --root <repo> --exclude some/dir/ --exclude tmp/
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _common import DEFAULT_EXCLUDES, classify, git, toplevel  # noqa: E402

SMALL_PROD_LINES = 1500


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="repo root (default: git toplevel of cwd)")
    ap.add_argument("--base", default="HEAD", help="compare the working tree against this commit")
    ap.add_argument("--exclude", action="append", help="path prefix to skip (repeatable); "
                    f"default {list(DEFAULT_EXCLUDES)}")
    a = ap.parse_args()
    root = pathlib.Path(a.root) if a.root else toplevel()
    excludes = tuple(a.exclude) if a.exclude else DEFAULT_EXCLUDES

    totals = {k: [0, 0, 0] for k in ("prod", "test", "doc")}  # files, added, deleted
    for line in git(root, "diff", "--numstat", a.base).splitlines():
        added, deleted, path = line.split("\t", 2)
        if path.startswith(excludes):
            continue
        t = totals[classify(path)]
        t[0] += 1
        t[1] += 0 if added == "-" else int(added)
        t[2] += 0 if deleted == "-" else int(deleted)
    for path in git(root, "ls-files", "--others", "--exclude-standard").splitlines():
        if not path or path.startswith(excludes):
            continue
        try:
            n = len((root / path).read_text(errors="replace").splitlines())
        except (IsADirectoryError, FileNotFoundError):
            continue
        t = totals[classify(path)]
        t[0] += 1
        t[1] += n

    print(f"{'kind':<5} {'files':>5} {'added':>7} {'deleted':>7}")
    for k, (f, ad, de) in totals.items():
        print(f"{k:<5} {f:>5} {ad:>7} {de:>7}")
    prod_lines = totals["prod"][1] + totals["prod"][2]
    tier = "small" if prod_lines <= SMALL_PROD_LINES else "large"
    print(f"\nprod lines changed: {prod_lines} -> size tier by lines: {tier} "
          f"(threshold {SMALL_PROD_LINES})")
    print("Reminder: money / permissions / cross-layer invariants make it 'large' regardless of lines.")


if __name__ == "__main__":
    main()
