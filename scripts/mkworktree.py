# -*- coding: utf-8 -*-
"""Create a detached worktree at HEAD with the frozen change applied, then verify it against the baseline.

Use for the read-only review snapshot, the mutation tree, and each private fix/implementation tree.
`isolation: "worktree"` on the Agent tool starts from the default branch and cannot see uncommitted
work, which is why these trees are built by hand.

Usage:
  python3 mkworktree.py --path tmp/<slug>/review/wt-snap --patch tmp/<slug>/review/change.patch \
      --baseline tmp/<slug>/review/baseline.sha256 [--mkdir ai-service/.venv] [--ref HEAD] [--root <repo>]
Remove later with: git worktree remove --force <path>
"""
import argparse
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _common import git, toplevel, verify_tree  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", required=True)
    ap.add_argument("--patch", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--mkdir", action="append", default=[], help="dirs to create inside the tree (e.g. mount points)")
    ap.add_argument("--root", default=None)
    a = ap.parse_args()
    root = pathlib.Path(a.root) if a.root else toplevel()
    path = pathlib.Path(a.path)
    path = path if path.is_absolute() else root / path
    patch = pathlib.Path(a.patch)
    patch = patch if patch.is_absolute() else root / patch
    baseline = pathlib.Path(a.baseline)
    baseline = baseline if baseline.is_absolute() else root / baseline

    ref = git(root, "rev-parse", a.ref).strip()
    git(root, "worktree", "add", "--detach", str(path), ref)
    if patch.stat().st_size:
        r = subprocess.run(["git", "-C", str(path), "apply", "--whitespace=nowarn", str(patch)],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"git apply failed:\n{r.stderr}")
    for d in a.mkdir:
        (path / d).mkdir(parents=True, exist_ok=True)
    bad = verify_tree(path, baseline)
    print(f"worktree {path} at {ref[:10]}; baseline mismatches: {len(bad)}")
    for b in bad[:20]:
        print("  ", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
