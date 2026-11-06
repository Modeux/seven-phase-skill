# -*- coding: utf-8 -*-
"""Copy what each fix/implementation tree changed (relative to the frozen snapshot) back into the main checkout.

Changes are found from the union of the tree's and the snapshot's `git status`, so a fix that restores
HEAD content or deletes a file new in the snapshot is still seen.

Safety checks before anything is copied:
  - every tree must sit at the snapshot's HEAD (otherwise the status union is incomplete -> stop);
  - a file may be changed by only ONE tree (overlap = the writer matrix was wrong -> stop, fix the plan);
  - the main checkout's copy must still equal the snapshot's (someone else edited it -> stop, merge by hand);
  - a file the snapshot changed but a tree has back at HEAD ('<label>: reverts <path> to HEAD') stops
    unless named by --allow-revert LABEL:PATH (an accidental `git checkout -- f` / stash looks the same
    as an intended revert); allowed reverts are copied and marked '[revert to HEAD]' in the file list;
  - with --only-files F (one path per line), a candidate outside F or a line of F that is not a candidate stops.
Deletions inside a tree are reported, never applied.

Usage:
  python3 merge_back.py --snapshot tmp/<slug>/review/wt-snap --tree G1=<path> --tree P=<path>        # dry run
  python3 merge_back.py ... --allow-revert G1:path/to/file.go --only-files checked.txt --apply

Exit: 0 dry run clean or copied; 1 STOP, nothing copied (also an uncaught OSError midway, without the STOP: line);
      2 bad arguments, including an --only-files list that cannot be read or names nothing.
"""
import argparse
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _common import dirty_paths, git, toplevel  # noqa: E402

SKIP_PARTS = {".mut", "node_modules", ".venv", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def tree_changes(tree, snapshot):
    """(changed, deleted) paths where `tree` differs from `snapshot`; both must be detached at the same HEAD."""
    tree, snapshot = pathlib.Path(tree), pathlib.Path(snapshot)
    changed, deleted = [], []
    # INVARIANT: candidates are the union of BOTH trees' status vs their shared HEAD. The fix tree's
    # status alone misses a fix that restores HEAD content or deletes a file new in the snapshot;
    # the union is complete only while HEADs are equal (main() enforces that first).
    for path in sorted(dirty_paths(tree) | dirty_paths(snapshot)):
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


def reverts_to_head(tree, snap_dirty, changed):
    """Changed paths the snapshot had dirty but `tree` has back at HEAD content."""
    # WHY: an intended revert and an accidental `git checkout -- f` / stash in a fix tree look
    # identical by content, and copying either silently undoes the reviewed change in main.
    tree_dirty = dirty_paths(tree)
    return [p for p in changed if p in snap_dirty and p not in tree_dirty]


def head_of(tree):
    return git(tree, "rev-parse", "HEAD", check=False).strip() or f"<not a git tree: {tree}>"


def stop(problems):
    print("\nSTOP:")
    for p in problems:
        print("  ", p)
    sys.exit(1)


def read_only_files(ap, path):
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="surrogateescape")
    except OSError as e:
        ap.error(f"--only-files: {e}")
    # WHY: split on \n only; dirty_paths hands back raw names, and splitlines() would break one on \f or \x85.
    listed = {x for x in text.split("\n") if x}
    if not listed:
        # INVARIANT: an empty list is a caller bug. Reading it as "nothing to copy" would turn a lost list into success.
        ap.error(f"--only-files {path} names no file")
    return listed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--tree", action="append", required=True, help="LABEL=path (repeatable)")
    ap.add_argument("--root", default=None, help="main checkout (default: git toplevel of cwd)")
    ap.add_argument("--allow-revert", action="append", default=[], metavar="LABEL:PATH",
                    help="accept that tree LABEL reverts PATH to HEAD (repeatable)")
    ap.add_argument("--only-files", metavar="F", help="the exact candidates the caller checked, one path per line")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    only_files = read_only_files(ap, a.only_files) if a.only_files is not None else None
    root = pathlib.Path(a.root) if a.root else toplevel()

    def resolve(p):
        p = pathlib.Path(p)
        return p if p.is_absolute() else root / p

    snapshot = resolve(a.snapshot)
    trees = []
    for spec in a.tree:
        label, _, p = spec.partition("=")
        trees.append((label, resolve(p)))

    snap_head = head_of(snapshot)
    heads = [(label, t, head_of(t)) for label, t in trees]
    moved = [f"HEAD differs: {label} {h[:10]} vs snapshot {snap_head[:10]} ({t})"
             for label, t, h in heads if h != snap_head]
    if moved:
        stop(moved)

    allowed = set()
    for spec in a.allow_revert:
        label, _, path = spec.partition(":")
        allowed.add((label, path))

    owner, reverted, problems = {}, set(), []
    snap_dirty = dirty_paths(snapshot)
    for label, tree in trees:
        changed, deleted = tree_changes(tree, snapshot)
        for d in deleted:
            problems.append(f"{label}: deletes {d} (apply by hand)")
        for f in reverts_to_head(tree, snap_dirty, changed):
            reverted.add(f)
            if (label, f) not in allowed:
                problems.append(f"{label}: reverts {f} to HEAD (pass --allow-revert {label}:{f} if intended)")
        for f in changed:
            if f in owner:
                problems.append(f"overlap: {f} changed by {owner[f][0]} and {label}")
            owner[f] = (label, tree)
    for f, (label, _) in owner.items():
        m, s = root / f, snapshot / f
        if s.exists() and (not m.exists() or m.read_bytes() != s.read_bytes()):
            problems.append(f"main differs from snapshot: {f} ({label})")
        if not s.exists() and m.exists():
            problems.append(f"main already has new file: {f} ({label})")
    if only_files is not None:
        # INVARIANT: candidates are recomputed here, after the caller's checks; only this comparison stops a file that
        # appeared in between from being copied unchecked.
        problems += [f"not in --only-files: {f}" for f in sorted(set(owner) - only_files)]
        problems += [f"in --only-files but not a candidate: {f}" for f in sorted(only_files - set(owner))]

    for f in sorted(owner):
        print(f"{owner[f][0]:<6} {f}" + ("  [revert to HEAD]" if f in reverted else ""))
    print(f"{len(owner)} files")
    for label, path in sorted(allowed):
        if path not in reverted or owner.get(path, ("",))[0] != label:
            print(f"note: unused --allow-revert {label}:{path}")
    if problems:
        stop(problems)
    if not a.apply:
        print("dry run - re-run with --apply to copy")
        return
    for f, (_, tree) in owner.items():
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tree / f, root / f)
    print("copied.")


if __name__ == "__main__":
    main()
