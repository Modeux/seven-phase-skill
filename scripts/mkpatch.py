# -*- coding: utf-8 -*-
"""Freeze the uncommitted change into patches + a sha256 baseline (review / fix snapshots start from these).

Writes into --out:
  change.patch        tracked diff vs HEAD + untracked new files (git diff --no-index)
  change-src.patch    non-test, non-doc files
  change-tests.patch  test files
  change-docs.patch   docs (.md, docs/, openapi)
  baseline.sha256     '<sha>  <path>' per changed file ('DELETED  <path>' for deletions; a rename
                      is its new path plus 'DELETED  <origin>')
  outside.sha256      same format, for every dirty path the --exclude prefixes kept OUT of the change
                      (minus the scratch prefixes tmp/ and .claude/PRPs/plans/); checktree.py treats
                      these as pre-existing and only warns if they change later
  files.txt           changed file list

Usage: python3 mkpatch.py --out tmp/<slug>/review [--root <repo>] [--exclude <prefix> ...]
Exclude every task dir / scratch dir that is NOT part of this change (defaults: .claude/PRPs/plans/, tmp/).
"""
import argparse
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _common import (DEFAULT_EXCLUDES, SCRATCH_PREFIXES, changed_paths, classify, dirty_paths,  # noqa: E402
                     file_digest, git, toplevel)


def untracked_patch(root, path):
    r = subprocess.run(["git", "-C", str(root), "diff", "--no-index", "--", "/dev/null", path],
                       capture_output=True, text=True)
    return r.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", default=None)
    ap.add_argument("--exclude", action="append")
    a = ap.parse_args()
    root = pathlib.Path(a.root) if a.root else toplevel()
    out = pathlib.Path(a.out)
    if not out.is_absolute():
        out = root / out
    out.mkdir(parents=True, exist_ok=True)
    excludes = tuple(a.exclude) if a.exclude else DEFAULT_EXCLUDES

    entries = changed_paths(root, excludes)
    tracked = [p for s, p in entries if s != "??"]
    untracked = [p for s, p in entries if s == "??"]
    groups = {"src": "prod", "tests": "test", "docs": "doc"}

    def build(paths_tracked, paths_untracked):
        text = git(root, "diff", "HEAD", "--", *paths_tracked) if paths_tracked else ""
        for p in paths_untracked:
            text += untracked_patch(root, p)
        return text

    (out / "change.patch").write_text(build(tracked, untracked))
    for suffix, kind in groups.items():
        (out / f"change-{suffix}.patch").write_text(build(
            [p for p in tracked if classify(p) == kind], [p for p in untracked if classify(p) == kind]))

    all_paths = sorted(tracked + untracked)
    outside = sorted(p for p in dirty_paths(root) - set(all_paths) if not p.startswith(SCRATCH_PREFIXES))

    def write_digests(name, paths):
        (out / name).write_text("".join(f"{file_digest(root / p)}  {p}\n" for p in paths), encoding="utf-8")

    write_digests("baseline.sha256", all_paths)
    write_digests("outside.sha256", outside)
    (out / "files.txt").write_text("\n".join(all_paths) + "\n", encoding="utf-8")

    print(f"tracked {len(tracked)}  untracked {len(untracked)}  HEAD {git(root, 'rev-parse', 'HEAD').strip()}")
    print(f"outside.sha256       {len(outside)} dirty paths outside the change (pre-existing; checktree warns if they change)")
    for n in ("change.patch", "change-src.patch", "change-tests.patch", "change-docs.patch"):
        print(f"{n:<20} {(out / n).stat().st_size:>9} bytes")
    print("Excluded prefixes:", list(excludes), "- check files.txt holds only this change's files.")


if __name__ == "__main__":
    main()
