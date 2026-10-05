# -*- coding: utf-8 -*-
"""What each review lane bought: findings, unique findings, confirmed, and what the lane cost.

Usage:
  python3 laneyield.py <slug> [<slug> ...]       # reads .claude/PRPs/plans/<slug>/REVIEW*.md from cwd's repo
  python3 laneyield.py --review path/to/REVIEW.md --slug <slug>
  python3 laneyield.py <slug> --project-dir ~/.claude/projects/<dir>

Reads the dedup map rows (`| R12 | … | <sources> |`, `| L3 | … | <sources> |`): the last cell lists the
lanes that reported the finding, e.g. `spec(M)、impact(M)、go(L) — 3` or `codex、ts（M）；spec（L）— 8`.
Rows under a heading containing 收斂裁決 are read as verdicts (`| R1 | confirmed… |`).
Lane cost = the subagent whose dispatch prompt names `tmp/<slug>/review/lane-<name>.md` (else the one that
wrote it); external CLIs (codex／grok／agy) are not in transcripts and show "-".

unique = only this lane reported it. confirmed = verdict text starts with confirmed／成立／owner (a
finding the owner had to rule on counts as worth raising); false positive／won't fix do not count.
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
from agentcost import project_dir_for_cwd, scan  # noqa: E402
from _common import toplevel  # noqa: E402

_ROW = re.compile(r"^\|\s*((?:R|L)\d+(?:[-.]\d+)?)\s*\|(.*)\|\s*$")
_LANE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_PAREN = re.compile(r"[（(][^（）()]*[）)]")
_STOP = {"vs", "and", "or", "see", "main", "session"}
_COUNT = re.compile(r"(?:—|-{1,2}|（|\()\s*\d+\s*(?:）|\))?\s*$")


def lanes_of(cell):
    cell = _COUNT.sub("", cell.strip())
    cell = _PAREN.sub("", cell)
    out = []
    for part in re.split(r"[、；;,，/]|\s+", cell):
        m = _LANE.fullmatch(part.strip().strip("*"))
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


def parse_review(path):
    """Return (findings {id: (severity_class, [lanes])}, verdicts {id: text})."""
    findings, verdicts, verdict_level = {}, {}, None
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            if "收斂裁決" in line:
                verdict_level = level
            elif verdict_level is not None and level <= verdict_level:
                verdict_level = None
            continue
        m = _ROW.match(line)
        if not m:
            continue
        rid, cells = m.group(1), [c.strip() for c in m.group(2).split("|")]
        if verdict_level is not None:
            verdicts.setdefault(rid, cells[0] if cells else "")
        elif rid not in findings and cells:
            findings[rid] = ("R" if rid.startswith("R") else "L", lanes_of(sources_cell(cells)))
    return findings, verdicts


def worth_raising(verdict):
    v = verdict.lower()
    if re.match(r"\s*(false|fp|won.?t|不修|誤報|不成立)", v):
        return False
    return bool(re.match(r"\s*(confirmed|成立|owner|部分)", v))


def lane_costs(project_dir, slug):
    """{lane: $}. The dispatch prompt names the lane's report path (also for lanes without Write,
    whose report the main session lands); a prompt naming several lane files falls back to the file
    the agent itself wrote."""
    costs = {}
    pat = re.compile(rf"tmp/{re.escape(slug)}/review/lane-([a-z0-9-]+)\.md")
    for jsonl in glob.glob(f"{project_dir}/*/subagents/*.jsonl"):
        named, wrote = set(), None
        with open(jsonl, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if slug not in line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = r.get("message") or {}
                if r.get("type") == "user" and not named:
                    content = m.get("content")
                    text = content if isinstance(content, str) else " ".join(
                        c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")
                    named = set(pat.findall(text))
                for c in m.get("content") or [] if isinstance(m.get("content"), list) else []:
                    if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Write":
                        w = pat.search((c.get("input") or {}).get("file_path", ""))
                        wrote = wrote or (w and w.group(1))
        lane = next(iter(named)) if len(named) == 1 else wrote
        if lane:
            costs[lane] = costs.get(lane, 0.0) + scan(jsonl)[0]
    return costs


def report(slug, review_paths, project_dir):
    stats = collections.defaultdict(lambda: collections.Counter())
    for path in review_paths:
        findings, verdicts = parse_review(path)
        for rid, (cls, lanes) in findings.items():
            raised = worth_raising(verdicts[rid]) if rid in verdicts else None
            for lane in lanes:
                s = stats[lane]
                s[cls] += 1
                if len(lanes) == 1:
                    s["u" + cls] += 1
                    if cls == "R" and raised:
                        s["uRok"] += 1
                if cls == "R" and raised:
                    s["Rok"] += 1
    costs = lane_costs(project_dir, slug)
    print(f"== {slug}  ({', '.join(os.path.basename(p) for p in review_paths)})")
    print(f"{'lane':18}{'R':>4}{'R ok':>6}{'uniqR':>7}{'uniqR ok':>10}{'L':>5}{'uniqL':>7}{'$':>8}{'$/uniqR ok':>12}")
    for lane, s in sorted(stats.items(), key=lambda kv: (-kv[1]["uRok"], -kv[1]["uR"], -kv[1]["R"])):
        cost = costs.get(lane)
        per = f"{cost / s['uRok']:.2f}" if cost and s["uRok"] else "-"
        print(f"{lane[:18]:18}{s['R']:>4}{s['Rok']:>6}{s['uR']:>7}{s['uRok']:>10}{s['L']:>5}{s['uL']:>7}"
              f"{(f'{cost:.2f}' if cost else '-'):>8}{per:>12}")
    unmatched = sorted(set(costs) - set(stats))
    if unmatched:
        print(f"   lanes with a report but no finding credited: {', '.join(unmatched)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--review", default=None, help="explicit REVIEW.md path (use with one --slug)")
    ap.add_argument("--slug", default=None)
    ap.add_argument("--project-dir", default=None)
    a = ap.parse_args()
    project_dir = pathlib.Path(os.path.expanduser(a.project_dir)) if a.project_dir else project_dir_for_cwd()
    if a.review:
        report(a.slug or pathlib.Path(a.review).parent.name, [a.review], project_dir)
        return
    repo = toplevel()
    for slug in a.slugs:
        paths = sorted(glob.glob(f"{repo}/.claude/PRPs/plans/{slug}/REVIEW*.md"))
        if not paths:
            print(f"== {slug}: no REVIEW*.md")
            continue
        report(slug, paths, project_dir)


if __name__ == "__main__":
    main()
