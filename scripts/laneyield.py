# -*- coding: utf-8 -*-
"""What each review lane bought: findings, unique findings, valid ones, and what the lane cost.

Usage:
  python3 laneyield.py <slug> [<slug> ...]       # reads .claude/PRPs/plans/<slug>/REVIEW*.md from cwd's repo
                                                 # (lean tasks without one: that slug's PROGRESS.md §審查)
  python3 laneyield.py --review path/to/REVIEW.md --slug <slug>
  python3 laneyield.py --review path/to/PROGRESS.md --slug <slug>
  python3 laneyield.py <slug> --project-dir ~/.claude/projects/<dir>

REVIEW*.md: reads the dedup map rows (`| R12 | … | <sources> |`, `| L3 | … | <sources> |`): the last cell
lists the lanes that reported the finding, e.g. `spec(M)、impact(M)、go(L) — 3` or `codex、ts（M）；spec（L）— 8`.
Rows under a heading containing 收斂裁決 are read as verdicts (`| R1 | confirmed… |`); when that table has
a 最終嚴重度 column it sets the class (CRITICAL／HIGH／MEDIUM → R, LOW → L), else the id prefix does.
PROGRESS.md (lean tasks): each row of the `## 審查` table is one finding, read by the header names
來源 (lanes), 嚴重度 (class) and 裁決.

Lane cost: a Workflow agent labelled `lane-<x>` whose dispatch names the slug is lane x. An ordinary
subagent is the lane whose `tmp/<slug>/review/lane-<name>.md` its dispatch prompt names (else the one it
wrote). Searches the main checkout and all its worktrees unless --project-dir is given; external CLIs
(codex／grok／agy) are not in transcripts and show "-".

unique = only this lane reported it. Verdicts: valid = confirmed／成立／部分／won't fix／不修／收／owner
(won't fix is "valid, not fixed in this package"; a finding the owner had to rule on counts as worth
raising); refuted = false positive／fp／誤報／不成立／不收; unver = R findings still unverified／未驗證,
neither valid nor refuted.
"""
import argparse
import collections
import glob
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _usage import agent_files, cost_of, project_dirs, quality_line, read_messages  # noqa: E402
from _common import toplevel  # noqa: E402

_ROW = re.compile(r"^\|\s*((?:R|L)\d+(?:[-.]\d+)?)\s*\|(.*)\|\s*$")
_SEPARATOR = re.compile(r"^\|[\s:|-]+$")
_LANE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_PAREN = re.compile(r"[（(][^（）()]*[）)]")
_STOP = {"vs", "and", "or", "see", "main", "session"}
_COUNT = re.compile(r"(?:—|-{1,2}|（|\()\s*\d+\s*(?:）|\))?\s*$")
_SEVERITY = re.compile(r"CRITICAL|HIGH|MEDIUM|LOW", re.IGNORECASE)
# INVARIANT: negative forms are tried before positive ones (不收 vs 收, 不成立 vs 成立).
_REFUTED = re.compile(r"(?:false\s*positive|fp\b|誤報|不成立|不收)")
_UNVERIFIED = re.compile(r"(?:unverified|未驗證)")
_VALID = re.compile(r"(?:confirmed|成立|部分|won.?t\s*fix|不修|收|owner)")


def lanes_of(cell):
    cell = _COUNT.sub("", cell.strip())
    cell = _PAREN.sub("", cell)
    out = []
    for part in re.split(r"[、；;,，/]|\s+", cell):
        # WHY: phases/6-review.md has sources written as <lane>#<n> (reviewcov ids) or <lane>:U1; credit the lane.
        m = _LANE.fullmatch(re.sub(r"(?:#\d+|:[A-Za-z]*\d+)$", "", part.strip().strip("*")))
        if m and m.group(0) not in out and m.group(0) not in _STOP:
            out.append(m.group(0))
    return out


def sources_cell(cells):
    """The lanes cell is the last cell, or the one before it when a trailing column (e.g. 要 owner) follows."""
    tail = [c for c in cells[-2:] if c]
    counted = [c for c in tail if _COUNT.search(c)]
    if counted:
        return counted[-1]
    plain = [c for c in tail if lanes_of(c) and not re.search(r"\.(go|ts|py|sql|md)\b|:\d", c)]
    return plain[-1] if plain else (cells[-1] if cells else "")


def severity_class(text):
    """R／L from the last severity word in a cell ("HIGH（降 LOW）" → L), None when there is none."""
    found = _SEVERITY.findall(text or "")
    if not found:
        return None
    return "L" if found[-1].upper() == "LOW" else "R"


def verdict_class(verdict):
    """'valid' | 'refuted' | 'unverified' | None, from the start of a verdict cell."""
    v = (verdict or "").strip().strip("*`").strip().lower()
    for cls, pat in (("refuted", _REFUTED), ("unverified", _UNVERIFIED), ("valid", _VALID)):
        if pat.match(v):
            return cls
    return None


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_review(path):
    """Return (findings {id: (severity_class, [lanes])}, verdicts {id: text})."""
    findings, verdicts, final, verdict_level = {}, {}, {}, None
    verdict_col, severity_col = 0, None
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            if "收斂裁決" in line:
                verdict_level, verdict_col, severity_col = level, 0, None
            elif verdict_level is not None and level <= verdict_level:
                verdict_level = None
            continue
        m = _ROW.match(line)
        if not m:
            if verdict_level is not None and line.startswith("|") and not _SEPARATOR.match(line.strip()):
                header = _cells(line)[1:]
                verdict_col = next((i for i, h in enumerate(header) if "裁決" in h), 0)
                severity_col = next((i for i, h in enumerate(header) if "最終嚴重度" in h), None)
            continue
        rid, cells = m.group(1), [c.strip() for c in m.group(2).split("|")]
        if verdict_level is not None:
            if rid not in verdicts:
                verdicts[rid] = cells[verdict_col] if verdict_col < len(cells) else ""
                if severity_col is not None and severity_col < len(cells):
                    final[rid] = severity_class(cells[severity_col])
        elif rid not in findings and cells:
            findings[rid] = ("R" if rid.startswith("R") else "L", lanes_of(sources_cell(cells)))
    for rid, cls in final.items():
        if rid in findings and cls:
            findings[rid] = (cls, findings[rid][1])
    return findings, verdicts


def parse_progress(path):
    """A lean task's PROGRESS.md `## 審查` table: one finding per row, ids P1…; same shape as parse_review."""
    findings, verdicts, section_level, cols = {}, {}, None, None
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            if section_level is not None and level <= section_level:
                section_level = None
            if section_level is None and "審查" in line:
                section_level, cols = level, None
            continue
        if section_level is None or not line.startswith("|") or _SEPARATOR.match(line.strip()):
            continue
        cells = _cells(line)
        if cols is None:
            if any("來源" in c for c in cells) and any("裁決" in c for c in cells):
                cols = {name: next((i for i, c in enumerate(cells) if name in c), None)
                        for name in ("來源", "嚴重度", "裁決")}
            continue
        get = lambda name: cells[cols[name]] if cols[name] is not None and cols[name] < len(cells) else ""  # noqa: E731
        rid = f"P{len(findings) + 1}"
        findings[rid] = (severity_class(get("嚴重度")) or "R", lanes_of(get("來源")))
        verdicts[rid] = get("裁決")
    return findings, verdicts


def parse(path):
    return parse_progress(path) if pathlib.Path(path).name.startswith("PROGRESS") else parse_review(path)


def review_paths(repo, slug):
    """REVIEW*.md of the slug, else its PROGRESS.md (lean tasks have no REVIEW doc), else []."""
    plans = pathlib.Path(repo) / ".claude" / "PRPs" / "plans" / slug
    paths = sorted(glob.glob(f"{glob.escape(str(plans))}/REVIEW*.md"))
    if paths:
        return paths
    return [str(plans / "PROGRESS.md")] if (plans / "PROGRESS.md").is_file() else []


def _text(content):
    if isinstance(content, str):
        return content
    return " ".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def _dispatch_text(jsonl):
    """User turns before the agent's first reply (a Workflow agent gets the relayed request, then the task)."""
    parts = []
    with open(jsonl, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("type") == "assistant":
                break
            if r.get("type") == "user":
                parts.append(_text((r.get("message") or {}).get("content")))
    return " ".join(parts)


def _named_lane(jsonl, slug, pat):
    """The dispatch prompt names the lane's report path (also for lanes without Write, whose report the
    main session lands); a prompt naming several lane files falls back to the file the agent itself wrote."""
    named, wrote = set(), None
    with open(jsonl, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if slug not in line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            m = r.get("message") or {}
            if r.get("type") == "user" and not named:
                named = set(pat.findall(_text(m.get("content"))))
            for c in m.get("content") if isinstance(m.get("content"), list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Write":
                    w = pat.search((c.get("input") or {}).get("file_path", ""))
                    wrote = wrote or (w and w.group(1))
    return next(iter(named)) if len(named) == 1 else wrote


def lane_costs(dirs, slug, quality=None):
    """{lane: $} over every subagent transcript in dirs."""
    costs = {}
    pat = re.compile(rf"tmp/{re.escape(slug)}/review/lane-([a-z0-9-]+)\.md")
    # WHY: a plain substring test credits `w3` with every `w3-recipe-ship-pins` lane.
    mentions = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(slug)}(?![A-Za-z0-9_-])")
    for d in dirs:
        for af in agent_files(d):
            if af.is_workflow:
                m = re.fullmatch(r"lane-([a-z0-9-]+)", af.label)
                lane = m.group(1) if m and mentions.search(_dispatch_text(af.path)) else None
            else:
                lane = _named_lane(af.path, slug, pat)
            if lane:
                costs[lane] = costs.get(lane, 0.0) + sum(cost_of(msg, quality) for msg in read_messages(af.path, quality=quality))
    return costs


def collect(slug, paths, dirs, quality=None):
    """Return ({lane: Counter(R, Rok, uR, uRok, unver, L, uL)}, {lane: $})."""
    stats = collections.defaultdict(collections.Counter)
    for path in paths:
        findings, verdicts = parse(path)
        for rid, (cls, lanes) in findings.items():
            verdict = verdict_class(verdicts.get(rid))
            for lane in lanes:
                s = stats[lane]
                s[cls] += 1
                if len(lanes) == 1:
                    s["u" + cls] += 1
                    if cls == "R" and verdict == "valid":
                        s["uRok"] += 1
                if cls == "R" and verdict == "valid":
                    s["Rok"] += 1
                if cls == "R" and verdict == "unverified":
                    s["unver"] += 1
    return dict(stats), lane_costs(dirs, slug, quality)


def report(slug, paths, dirs):
    quality = {}
    stats, costs = collect(slug, paths, dirs, quality)
    print(f"== {slug}  ({', '.join(os.path.basename(p) for p in paths)})")
    print(f"{'lane':18}{'R':>4}{'R ok':>6}{'uniqR':>7}{'uniqR ok':>10}{'unver':>7}{'L':>5}{'uniqL':>7}{'$':>8}{'$/uniqR ok':>12}")
    for lane, s in sorted(stats.items(), key=lambda kv: (-kv[1]["uRok"], -kv[1]["uR"], -kv[1]["R"])):
        cost = costs.get(lane)
        per = f"{cost / s['uRok']:.2f}" if cost and s["uRok"] else "-"
        print(f"{lane[:18]:18}{s['R']:>4}{s['Rok']:>6}{s['uR']:>7}{s['uRok']:>10}{s['unver']:>7}{s['L']:>5}{s['uL']:>7}"
              f"{(f'{cost:.2f}' if cost else '-'):>8}{per:>12}")
    unmatched = sorted(set(costs) - set(stats))
    if unmatched:
        print(f"   lanes with a report but no finding credited: {', '.join(unmatched)}")
    print(f"   {quality_line(quality)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--review", default=None, help="explicit REVIEW.md or PROGRESS.md path (use with one --slug)")
    ap.add_argument("--slug", default=None)
    ap.add_argument("--project-dir", default=None)
    a = ap.parse_args()
    dirs = project_dirs(a.project_dir, all_worktrees=True)
    if a.review:
        report(a.slug or pathlib.Path(a.review).parent.name, [a.review], dirs)
        return
    repo = toplevel()
    for slug in a.slugs:
        paths = review_paths(repo, slug)
        if not paths:
            print(f"== {slug}: no REVIEW*.md or PROGRESS.md")
            continue
        report(slug, paths, dirs)


if __name__ == "__main__":
    main()
