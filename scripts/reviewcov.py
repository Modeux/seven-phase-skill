# -*- coding: utf-8 -*-
"""Check that every expected review lane reported, and that the merged dedup map accounts for every finding.

Usage:
  python3 reviewcov.py <review dir> <REVIEW.md>       # e.g. tmp/<slug>/review .claude/PRPs/plans/<slug>/REVIEW.md
  python3 reviewcov.py <review dir>                   # lane gate only (no map check yet)
  python3 reviewcov.py <review dir> --list            # informational, always exit 0: lane table + finding ids
  python3 reviewcov.py <review dir> <REVIEW.md> --expect codex --waive agy="packet too big, covered by grok" \
      --ack-manual grok

Expected lanes: tasks/<name>.md stems, ext-<name>.log, lane-<name>.status.json, plus --expect NAME.
With none of these the lane set is unknown and the check fails. Names work with or without `lane-`.

Each lane is classified, first match wins:
  waived      --waive NAME=REASON
  failed      status file says rc != 0 or ok false (even when a lane-<name>.md exists: it is an older run's)
  missing     no lane-<name>.md
  empty       blank report
  stale       report older than baseline.sha256 (written when the review snapshot was frozen)
  ok          has `### [SEVERITY] title` headings (review-brief format)
  ok-zero     no [SEVERITY] token anywhere, and a heading containing 已查證無問題
  acked       --ack-manual NAME (an unformatted report the main session merged by hand)
  unformatted anything else (incl. findings as `## [HIGH]`, `### 1. [HIGH]`, `**[HIGH]**`)
Pass (exit 0), except under --list: every lane is ok / ok-zero / acked / waived, the expected lane set
is known, and (when REVIEW.md is given) no finding id is missing.

Finding ids: every `### [SEVERITY] title` heading in an ok lane becomes `<name>#<n>`, numbered in file
order. An id is accounted for only in a table cell under a header cell containing 來源 (the map's
source column; tables without one count for nothing), or anywhere inside a section whose heading
contains 未收錄 (intake-gate exclusions), up to the next heading of the same or higher level.
Mentions in prose, 依據 cells or result tables do not count.
"""
import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _lanes import expected_lanes, lane_name, read_status  # noqa: E402

_HEAD = re.compile(r"^###\s*\[(CRITICAL|HIGH|MEDIUM|LOW)\]\s*(.*)$", re.I)
_MD_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
_SEV_TOKEN = re.compile(r"\[(CRITICAL|HIGH|MEDIUM|LOW)\]", re.I)
_SEP_CELL = re.compile(r":?-+:?")
_PASS = ("ok", "ok-zero", "acked", "waived")


def classify(review_dir, name, waived, acked, baseline_mtime):
    """-> (state, detail, [(severity, title), ...])."""
    if name in waived:
        return "waived", waived[name], []
    st = read_status(review_dir, name)
    if st is not None and (st.get("rc") != 0 or st.get("ok") is False):
        return "failed", f"status rc={st.get('rc')} {st.get('error') or ''}".strip(), []
    f = pathlib.Path(review_dir) / f"lane-{name}.md"
    if not f.exists():
        return "missing", "no report", []
    text = f.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return "empty", f"{f.stat().st_size} bytes", []
    if baseline_mtime is not None and f.stat().st_mtime < baseline_mtime:
        return "stale", "older than baseline.sha256", []
    lines = text.splitlines()
    heads = [(m.group(1).upper(), m.group(2).strip()) for m in (_HEAD.match(x) for x in lines) if m]
    if heads:
        return "ok", f"{len(heads)} findings", heads
    # WHY: every report ends with 已查證無問題, so a report whose findings use another heading shape
    # (`## [HIGH]`, `### 1. [HIGH]`, `**[HIGH]**`) would otherwise pass as ok-zero with its ids dropped.
    if not _SEV_TOKEN.search(text) and any((m := _MD_HEAD.match(x)) and "已查證無問題" in m.group(2) for x in lines):
        return "ok-zero", "0 findings", []
    if name in acked:
        return "acked", "merged by hand", []
    return "unformatted", "no [SEVERITY] / 已查證無問題 heading", []


def _cells(line):
    s = line.strip()[1:]
    return [c.strip() for c in (s[:-1] if s.endswith("|") else s).split("|")]


def accounting_text(text):
    keep, excl_level, src_cols = [], None, None
    for line in text.splitlines():
        is_row = line.strip().startswith("|")
        m = _MD_HEAD.match(line)
        if m:
            level = len(m.group(1))
            if excl_level is not None and level <= excl_level:
                excl_level = None
            if excl_level is None and "未收錄" in m.group(2):
                excl_level = level
                continue
        if excl_level is not None:
            keep.append(line)
            src_cols = None
            continue
        if not is_row:
            src_cols = None
            continue
        cells = _cells(line)
        if src_cols is None:
            # WHY: only the map's 來源 column says "this finding was merged here"; a verdict table's
            # 依據 cell or a results table that merely mentions an id does not account for it.
            src_cols = [i for i, c in enumerate(cells) if "來源" in c]
            continue
        if all(_SEP_CELL.fullmatch(c) for c in cells):
            continue
        keep.extend(cells[i] for i in src_cols if i < len(cells))
    return "\n".join(keep)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("review_dir")
    ap.add_argument("review_md", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--expect", action="append", default=[], metavar="NAME", help="lane that must have reported")
    ap.add_argument("--waive", action="append", default=[], metavar="NAME=REASON", help="lane excused, with reason")
    ap.add_argument("--ack-manual", action="append", default=[], metavar="NAME",
                    help="unformatted lane report the main session merged by hand")
    a = ap.parse_args()
    d = pathlib.Path(a.review_dir)

    waived = {}
    for w in a.waive:
        name, sep, reason = w.partition("=")
        if not sep or not reason.strip():
            ap.error(f"--waive needs NAME=REASON, got {w!r}")
        waived[lane_name(name.strip())] = reason.strip()
    acked = {lane_name(x) for x in a.ack_manual}
    expected = expected_lanes(d)
    for x in a.expect:
        expected.setdefault(lane_name(x), "--expect")
    present = {lane_name(f.stem) for f in d.glob("lane-*.md")}
    base = d / "baseline.sha256"
    baseline_mtime = base.stat().st_mtime if base.exists() else None

    rows, findings = [], {}
    for name in sorted(set(expected) | present | set(waived)):
        state, detail, heads = classify(d, name, waived, acked, baseline_mtime)
        rows.append((name, state, expected.get(name, "report only"), detail))
        if state == "ok":
            for n, (sev, title) in enumerate(heads, 1):
                findings[f"{name}#{n}"] = (sev, title)

    print(f"{'lane':16} {'state':12} {'expected via':12} detail")
    for name, state, via, detail in rows:
        print(f"{name:16} {state:12} {via:12} {detail[:90]}")
    fails = [f"{name} {state}" for name, state, _, _ in rows if state not in _PASS]
    if not expected:
        fails.insert(0, "lane coverage unknown (no tasks/, ext-*.log, *.status.json or --expect)")
        print("lane coverage unknown: no tasks/, ext-*.log, lane-*.status.json or --expect - "
              "a lane that never reported would be invisible")

    if a.list:
        for fid, (sev, title) in findings.items():
            print(f"{fid:24} {sev:8} {title[:100]}")
        return
    if not a.review_md:
        if fails:
            print("FAIL: " + "; ".join(fails))
        sys.exit(1 if fails else 0)

    text = accounting_text(pathlib.Path(a.review_md).read_text(encoding="utf-8"))
    # WHY: `go#1` must not be satisfied by `go#12`, nor by `algo#1`.
    missing = [fid for fid in findings if not re.search(rf"(?<![\w-]){re.escape(fid)}(?!\d)", text)]
    print(f"{len(findings) - len(missing)}/{len(findings)} lane findings accounted for in {a.review_md}"
          " (來源 cells and 未收錄 sections only)")
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        for fid in missing:
            if findings[fid][0] == sev:
                print(f"  missing {sev:8} {fid:24} {findings[fid][1][:90]}")
    if missing:
        fails.append(f"{len(missing)} finding ids missing from the map")
    if fails:
        print("FAIL: " + "; ".join(fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
