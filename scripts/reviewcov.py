# -*- coding: utf-8 -*-
"""Check that every expected review lane reported, and that the merged dedup map accounts for every finding.

Usage:
  python3 reviewcov.py <review dir> <REVIEW.md>       # e.g. tmp/<slug>/review .claude/PRPs/plans/<slug>/REVIEW.md
  python3 reviewcov.py <review dir>                   # lane gate only (no map check yet)
  python3 reviewcov.py <review dir> --list            # informational, always exit 0: lane table + finding ids
  python3 reviewcov.py <review dir> <REVIEW.md> --expect codex --waive agy="packet too big, covered by grok" \
      --ack-manual grok
  python3 reviewcov.py <review dir> --index <review dir>/findings-index.md   # + .json next to it
  python3 reviewcov.py <review dir> <REVIEW.md> --section 包 2a     # doc holding several review rounds

Expected lanes: tasks/<name>.md stems, ext-<name>.log, lane-<name>.status.json, plus --expect NAME.
With none of these the lane set is unknown and the check fails. Names work with or without `lane-`.

Each lane is classified, first match wins:
  waived      --waive NAME=REASON
  failed      status file says rc != 0 or ok false (even when a lane-<name>.md exists: it is an older run's)
  missing     no lane-<name>.md
  empty       blank report
  stale       report older than baseline.sha256 (written when the review snapshot was frozen)
  mixed       `### [SEVERITY]` headings plus other severity-tagged title lines (`## [HIGH]`, `**[HIGH]**`,
              `- 【HIGH】`, `### F7 [HIGH]`, a heading glued after prose): those findings get no id
              (--ack-manual after merging them by hand; --index lists the unnumbered lines)
  count-mismatch  the Workflow schema's `counts` (kept in the status file) differ from the headings, are not
              integers, or are above 0 for a report with no headings
  ok          has `### [SEVERITY…] title` headings (review-brief format; `[LOW｜需要 owner 拍板]` counts)
  ok-zero     no severity tag anywhere ([HIGH], [HIGH｜…], 【HIGH】, [MED]), and a heading containing 已查證無問題
  acked       --ack-manual NAME (an unformatted or mixed report the main session merged by hand)
  unformatted anything else (incl. findings as `## [HIGH]`, `### 1. [HIGH]`, `**[HIGH]**`)
Pass (exit 0), except under --list: every lane is ok / ok-zero / acked / waived, the expected
lane set is known, and (when REVIEW.md is given) no finding id is missing, no map id repeats, and no 來源
id points at a finding that does not exist (`go#9` when go has 8, `go#0`, `spec#1` when spec has none,
`comment#1` when the lane is `comments`). Map tables without 宣稱嚴重度／位置 columns, R／L ids whose claimed
severity contradicts the series, and findings without a 位置 line print WARN and do not fail.

Finding ids: every `### [SEVERITY…] title` heading becomes `<name>#<n>`, numbered in file order. An id is
accounted for only in a table cell under a header cell containing 來源 (the map's source column; tables
without one, and tables under a 收斂裁決 heading, count for nothing), or anywhere inside a section whose
heading title starts with 未收錄 (intake-gate exclusions), up to the next heading of the same or higher
level. Mentions in prose, 依據 cells or result tables do not count. Fenced code blocks are ignored.
--section TEXT reads only the section under the first heading containing TEXT.
--index writes the lane × severity count table and one row per finding (id, severity＋bracket tag, title,
位置) as Markdown, and the same rows as JSON: the map's checklist, generated instead of retyped.
"""
import argparse
import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _lanes import SEV_TAG, expected_lanes, lane_name, read_status, report_findings, stray_findings  # noqa: E402
from _mdtable import cells, map_tables, pick_col, row_ids, unfenced  # noqa: E402

_PAREN = re.compile(r"[（(][^（）()]*[）)]")
_SEV_WORD = re.compile(r"CRITICAL|HIGH|MEDIUM|LOW", re.I)

_MD_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
# INVARIANT: same tag shapes as _lanes.SEV_TAG, so a qualifier or 【】 can never make a report look finding-free.
_SEV_TOKEN = re.compile(SEV_TAG, re.I)
_SEP_CELL = re.compile(r":?-+:?")
# WHY: ASCII lookbehind — `\w` matches CJK, so `go#2和ts#1` hid ts#1 (laneyield uses the same boundary).
_SRC_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])([a-z][a-z0-9]*(?:-[a-z0-9]+)*)#(\d+)(?!\d)")
_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
_PASS = ("ok", "ok-zero", "acked", "waived")
_ENUMERATED = ("ok", "ok-zero", "mixed", "count-mismatch")


def _count_mismatch(st, heads):
    counts = (st or {}).get("counts")
    if counts is None:
        return None
    if not isinstance(counts, dict):
        return f"counts is not an object: {counts!r}"[:90]
    got = collections.Counter(h["severity"] for h in heads)
    diff = []
    for sev in _SEVERITIES:
        claimed = counts.get(sev.lower(), counts.get(sev, 0))
        if not isinstance(claimed, int) or isinstance(claimed, bool):
            diff.append(f"{sev} counts {claimed!r} is not an integer")
        elif claimed != got[sev]:
            diff.append(f"{sev} counts {claimed} vs headings {got[sev]}")
    return "; ".join(diff) or None


def classify(review_dir, name, waived, acked, baseline_mtime):
    """-> (state, detail, [{n, severity, title, location}, ...])."""
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
    heads = report_findings(text)
    if name in acked:
        return "acked", f"{len(heads)} findings + merged by hand", heads
    mismatch = _count_mismatch(st, heads)
    if heads:
        stray = stray_findings(text)
        if stray:
            # WHY: one `## [HIGH]` among proper headings used to pass as ok with that finding silently unnumbered.
            return "mixed", f"{len(heads)} findings + {len(stray)} other-shaped [SEVERITY] lines (line {stray[0]})", heads
        if mismatch:
            return "count-mismatch", mismatch, heads
        return "ok", f"{len(heads)} findings", heads
    # WHY: every report ends with 已查證無問題, so a report whose findings use another heading shape
    # (`## [HIGH]`, `### 1. [HIGH]`, `**[HIGH]**`, `【HIGH】`) would otherwise pass as ok-zero with its ids dropped.
    if not _SEV_TOKEN.search(text) and any((m := _MD_HEAD.match(x)) and "已查證無問題" in m.group(2) for x in lines):
        if mismatch:
            # WHY: the schema says this lane found something the headings do not show (e.g. `### H1 …`).
            return "count-mismatch", mismatch, []
        return "ok-zero", "0 findings", []
    return "unformatted", "no [SEVERITY] / 已查證無問題 heading", []


def section_text(text, needle):
    """Lines under the first heading containing `needle`, up to the next heading of the same or higher level."""
    out, level = [], None
    for line in unfenced(text).splitlines():
        m = _MD_HEAD.match(line)
        if m and level is not None and len(m.group(1)) <= level:
            break
        if m and level is None and needle in m.group(2):
            level = len(m.group(1))
            continue
        if level is not None:
            out.append(line)
    return "\n".join(out) if level is not None else None


_NUMBERING = re.compile(r"^(?:§?\d+(?:\.\d+)*[.)、]?\s*)")


def accounting_text(text):
    """Text of 來源 cells and 未收錄 sections: where a finding id counts as merged.

    INVARIANT: same exclusions as _mdtable.map_tables — tables under a 收斂裁決 heading never account for an
    id (their 依據（來源報告） cells only mention it), and a section counts as 未收錄 only when its title starts
    with 未收錄 after the numbering (`## 地圖（0 未收錄）` is not one)."""
    keep, excl_level, verdict_level, src_cols = [], None, None, None
    for line in unfenced(text).splitlines():
        is_row = line.strip().startswith("|")
        m = _MD_HEAD.match(line)
        if m:
            level, title = len(m.group(1)), _NUMBERING.sub("", m.group(2).strip())
            if excl_level is not None and level <= excl_level:
                excl_level = None
            if verdict_level is not None and level <= verdict_level:
                verdict_level = None
            if verdict_level is None and "收斂裁決" in title:
                verdict_level = level
            if excl_level is None and verdict_level is None and title.startswith("未收錄"):
                excl_level = level
                continue
        if excl_level is not None:
            keep.append(line)
            src_cols = None
            continue
        if not is_row or verdict_level is not None:
            src_cols = None
            continue
        row = cells(line)
        if src_cols is None:
            # WHY: only the map's 來源 column says "this finding was merged here"; a verdict table's
            # 依據 cell or a results table that merely mentions an id does not account for it.
            src_cols = [i for i, c in enumerate(row) if "來源" in c]
            continue
        if all(_SEP_CELL.fullmatch(c) for c in row):
            continue
        keep.extend(row[i] for i in src_cols if i < len(row))
    return "\n".join(keep)


def write_index(path, rows, heads_by_lane, strays_by_lane=None):
    """Markdown checklist (lane × severity counts, one row per finding) plus the same rows as JSON.
    Severity lines in other shapes (acked／mixed lanes) are listed unnumbered so they still reach the map."""
    def esc(s):
        return s.replace("|", "\\|").replace("\n", " ")
    strays_by_lane = strays_by_lane or {}
    md = ["# 審查發現索引（reviewcov.py --index 產生，勿手改）", "",
          "| lane | 狀態 | CRITICAL | HIGH | MEDIUM | LOW | 合計 | 未編號 |", "|---|---|---|---|---|---|---|---|"]
    items = []
    for name, state, _, _ in rows:
        heads = heads_by_lane.get(name, [])
        c = collections.Counter(h["severity"] for h in heads)
        md.append(f"| {name} | {state} | " + " | ".join(str(c[s]) for s in _SEVERITIES)
                  + f" | {len(heads)} | {len(strays_by_lane.get(name, []))} |")
        items.extend({"id": f"{name}#{h['n']}", "lane": name, **h} for h in heads)
    md += ["", "| id | 嚴重度 | 標題 | 位置 |", "|---|---|---|---|"]
    md += [f"| {i['id']} | {i['severity']}{('｜' + esc(i['tag'])) if i.get('tag') else ''} | {esc(i['title'])} | "
           f"{esc(i['location']) or '（缺）'} |" for i in items]
    loose = [(name, n, text_) for name, lst in strays_by_lane.items() for n, text_ in lst]
    if loose:
        md += ["", "## 未編號的 [SEVERITY] 行（主 session 人工併入地圖）", "", "| lane | 行 | 原文 |", "|---|---|---|"]
        md += [f"| {name} | {n} | {esc(text_[:160])} |" for name, n, text_ in loose]
    # WHY: `--index x.json` used to write the md and then overwrite it with the json.
    p = pathlib.Path(path).with_suffix(".md")
    j = p.with_suffix(".json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(md) + "\n", encoding="utf-8")
    j.write_text(json.dumps(
        items + [{"id": None, "lane": name, "line": n, "text": text_} for name, n, text_ in loose],
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    no_loc = sum(1 for i in items if not i["location"])
    print(f"index: {len(items)} findings ({no_loc} without 位置), {len(loose)} unnumbered lines -> {p} (+ {j.name})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("review_dir")
    ap.add_argument("review_md", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--index", metavar="PATH.md", help="write the findings checklist (+ PATH.json)")
    ap.add_argument("--section", metavar="TEXT", help="check only the section under the heading containing TEXT")
    ap.add_argument("--expect", action="append", default=[], metavar="NAME", help="lane that must have reported")
    ap.add_argument("--waive", action="append", default=[], metavar="NAME=REASON", help="lane excused, with reason")
    ap.add_argument("--ack-manual", action="append", default=[], metavar="NAME",
                    help="unformatted or mixed lane report the main session merged by hand")
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

    rows, findings, heads_by_lane, state_of, strays_by_lane = [], {}, {}, {}, {}
    for name in sorted(set(expected) | present | set(waived)):
        state, detail, heads = classify(d, name, waived, acked, baseline_mtime)
        rows.append((name, state, expected.get(name, "report only"), detail))
        heads_by_lane[name], state_of[name] = heads, state
        for h in heads:
            findings[f"{name}#{h['n']}"] = (h["severity"], h["title"])
        report_path = d / f"lane-{name}.md"
        if state in ("acked", "mixed", "unformatted") and report_path.exists():
            lines = report_path.read_text(encoding="utf-8", errors="replace").splitlines()
            strays_by_lane[name] = [(n, lines[n - 1].strip()) for n in stray_findings("\n".join(lines))]

    print(f"{'lane':16} {'state':14} {'expected via':12} detail")
    for name, state, via, detail in rows:
        print(f"{name:16} {state:14} {via:12} {detail[:90]}")
    fails = [f"{name} {state}" for name, state, _, _ in rows if state not in _PASS]
    if not expected:
        fails.insert(0, "lane coverage unknown (no tasks/, ext-*.log, *.status.json or --expect)")
        print("lane coverage unknown: no tasks/, ext-*.log, lane-*.status.json or --expect - "
              "a lane that never reported would be invisible")
    if a.index:
        write_index(a.index, rows, heads_by_lane, strays_by_lane)

    if a.list:
        for fid, (sev, title) in findings.items():
            print(f"{fid:24} {sev:8} {title[:100]}")
        return
    if not a.review_md:
        if fails:
            print("FAIL: " + "; ".join(fails))
        sys.exit(1 if fails else 0)

    doc = pathlib.Path(a.review_md).read_text(encoding="utf-8")
    if a.section:
        doc = section_text(doc, a.section)
        if doc is None:
            print(f"FAIL: no heading containing {a.section!r} in {a.review_md}")
            sys.exit(1)
    text = accounting_text(doc)
    # WHY: `go#1` must not be satisfied by `go#12`, nor by `algo#1`.
    missing = [fid for fid in findings if not re.search(rf"(?<![A-Za-z0-9_-]){re.escape(fid)}(?!\d)", text)]
    print(f"{len(findings) - len(missing)}/{len(findings)} lane findings accounted for in {a.review_md}"
          " (來源 cells and 未收錄 sections only)")
    for sev in _SEVERITIES:
        for fid in missing:
            if findings[fid][0] == sev:
                print(f"  missing {sev:8} {fid:24} {findings[fid][1][:90]}")
    if missing:
        fails.append(f"{len(missing)} finding ids missing from the map")

    seen = collections.defaultdict(list)
    warns = []
    for t, _, rows_ in map_tables(doc):
        missing_cols = [c for c in ("宣稱嚴重度", "位置") if pick_col(t.header, c) is None]
        if missing_cols:
            warns.append(f"map table at line {rows_[0][0]} has no {'／'.join(missing_cols)} column")
        sevcol = pick_col(t.header, "宣稱嚴重度")
        for lineno, row in rows_:
            for rid in row_ids(row[0], any_prefix=True):
                seen[rid].append(lineno)
                claimed = _SEV_WORD.findall(_PAREN.sub("", row[sevcol])) if sevcol is not None and sevcol < len(row) else []
                if claimed and rid[0] in "RL" and rid[1:2].isdigit() and (claimed[0].upper() == "LOW") != (rid[0] == "L"):
                    warns.append(f"{rid} is in the {'L' if rid[0] == 'L' else 'R'} series but claims {claimed[0].upper()} "
                                 f"(line {lineno})")
    dups = {rid: lines for rid, lines in seen.items() if len(lines) > 1}
    for rid, lines in dups.items():
        print(f"  duplicate map id {rid} at lines {', '.join(map(str, lines))}")
    if dups:
        fails.append(f"{len(dups)} map ids repeat (several review rounds in one doc → --section)")

    # INVARIANT: acked／waived／unformatted lanes have no trustworthy numbering, so their ids are not checked.
    dangling = []
    for lane, n in dict.fromkeys(_SRC_TOKEN.findall(text)):
        if lane not in state_of:
            dangling.append(f"{lane}#{n} (no such lane)")
        elif state_of[lane] in _ENUMERATED and not 1 <= int(n) <= len(heads_by_lane[lane]):
            dangling.append(f"{lane}#{n} ({lane} has {len(heads_by_lane[lane])})")
    for x in dangling:
        print(f"  dangling source {x}")
    if dangling:
        fails.append(f"{len(dangling)} 來源 ids point at no finding")
    no_loc = {name: sum(1 for h in hs if not h["location"]) for name, hs in heads_by_lane.items()}
    warns += [f"{name}: {n} finding(s) without a `- 位置：` line" for name, n in no_loc.items() if n]
    for w in warns:
        # WHY: required-field gaps are flagged, not failed — the map is still usable and the main session decides.
        print(f"  WARN {w}")
    if fails:
        print("FAIL: " + "; ".join(fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
