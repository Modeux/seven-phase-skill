# -*- coding: utf-8 -*-
"""Verify one or more trees (main checkout, snapshot, mutation tree...) against a sha256 baseline.

Run before and after a review round: the main checkout must match the baseline afterwards
(proof that no lane wrote to it), and a mutation tree must match again after mutations are reverted.

Usage: python3 checktree.py --baseline tmp/<slug>/review/baseline.sha256 <tree> [<tree> ...]
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _common import read_baseline, verify_tree  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("trees", nargs="+")
    a = ap.parse_args()
    n = len(read_baseline(a.baseline))
    failed = False
    for t in a.trees:
        bad = verify_tree(t, a.baseline)
        print(f"{t}: checked {n}, mismatches {len(bad)}")
        for b in bad[:20]:
            print("   ", b)
        failed |= bool(bad)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
