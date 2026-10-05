# -*- coding: utf-8 -*-
"""Check that a merged dedup map accounts for every finding in every lane report.

Usage:
  python3 reviewcov.py <review dir> <REVIEW.md>       # e.g. tmp/<slug>/review .claude/PRPs/plans/<slug>/REVIEW.md
  python3 reviewcov.py <review dir> --list            # print the finding ids each lane report yields

Finding ids: every `### [SEVERITY] title` heading in `lane-<name>.md` (the review-brief report format)
becomes `<name>#<n>`, numbered in file order. The map is complete when each id appears in REVIEW.md —
in a map row's source cell or in an intake-gate section listing what was left out and why (any
heading containing 未收錄). Exit 1 on any missing id.

Lane files with no `### [SEVERITY]` heading are listed separately (external CLIs often ignore the
format) — the main session checks those by hand.
"""
import argparse
import pathlib
import re
import sys

_HEAD = re.compile(r"^###\s*\[(CRITICAL|HIGH|MEDIUM|LOW)\]\s*(.*)$", re.I)


def lane_findings(review_dir):
    out, unformatted = {}, []
    for f in sorted(pathlib.Path(review_dir).glob("lane-*.md")):
        name = f.stem.removeprefix("lane-")
        heads = [m for m in (_HEAD.match(line) for line in f.read_text(encoding="utf-8").splitlines()) if m]
        if not heads and f.stat().st_size:
            unformatted.append(name)
        for n, m in enumerate(heads, 1):
            out[f"{name}#{n}"] = (m.group(1).upper(), m.group(2).strip())
    return out, unformatted


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("review_dir")
    ap.add_argument("review_md", nargs="?")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    findings, unformatted = lane_findings(a.review_dir)
    if a.list or not a.review_md:
        for fid, (sev, title) in findings.items():
            print(f"{fid:24} {sev:8} {title[:100]}")
        if unformatted:
            print(f"no [SEVERITY] headings (check by hand): {', '.join(unformatted)}")
        return
    text = pathlib.Path(a.review_md).read_text(encoding="utf-8")
    # WHY: `go#1` must not be satisfied by `go#12`, nor by `algo#1`.
    missing = [fid for fid in findings if not re.search(rf"(?<![\w-]){re.escape(fid)}(?!\d)", text)]
    by_sev = {}
    for fid in missing:
        by_sev.setdefault(findings[fid][0], []).append(fid)
    print(f"{len(findings) - len(missing)}/{len(findings)} lane findings accounted for in {a.review_md}")
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        for fid in by_sev.get(sev, []):
            print(f"  missing {sev:8} {fid:24} {findings[fid][1][:90]}")
    if unformatted:
        print(f"no [SEVERITY] headings (check by hand): {', '.join(unformatted)}")
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
