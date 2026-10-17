#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wall-clock of subagents per agent type: median／p90／max／total minutes and where the time goes
(model turns vs tool calls, tool calls bucketed by kind). For retros and schedule changes.

  python3 wallclock.py                                   # last 14 days, main checkout + worktrees
  python3 wallclock.py --days 1.5 --detail ui-lane-reviewer converge-verifier
  python3 wallclock.py --project-dir ~/.claude/projects/<dir>

Wall＝first to last transcript timestamp of the agent, so a run that waited on Docker or a hung
command counts the wait. Workflow agents group as `<type>(wf)`.
"""
import argparse
import collections
import datetime
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _usage import agent_files, project_dirs  # noqa: E402


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def bucket(name, inp):
    if name == "Bash":
        c = (inp or {}).get("command", "")
        for k, pat in [("bash:sleep/wait", r"\bsleep\b|until |wait_for|while "),
                       ("bash:ng-serve/build", r"ng (serve|build)|pnpm|npm run|npx ng"),
                       ("bash:ng-test", r"ng test|vitest"),
                       ("bash:go-test", r"go test"),
                       ("bash:playwright", r"playwright|mcp-server"),
                       ("bash:curl", r"\bcurl\b"),
                       ("bash:docker/just", r"docker|just "),
                       ("bash:git", r"\bgit\b")]:
            if re.search(pat, c):
                return k
        return "bash:other"
    if name.startswith("mcp__") and "playwright" in name:
        return "browser:" + name.split("__")[-1].replace("browser_", "")
    return name


def collect(dirs, since):
    rows = collections.defaultdict(list)
    toolsum = collections.defaultdict(collections.Counter)
    for proj in dirs:
        for a in agent_files(proj):
            if a.path.stat().st_mtime < since:
                continue
            typ = (a.meta.get("agentType") or "?") + ("(wf)" if a.is_workflow else "")
            events = []
            for line in a.path.open(encoding="utf-8"):
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "timestamp" in e:
                    events.append(e)
            if len(events) < 2:
                continue
            t0, t1 = ts(events[0]["timestamp"]), ts(events[-1]["timestamp"])
            if t0 < since:
                continue
            pending, tool_t, turns = {}, collections.Counter(), 0
            for e in events:
                c = (e.get("message") or {}).get("content")
                if e.get("type") == "assistant" and isinstance(c, list):
                    for b in c:
                        if b.get("type") == "tool_use":
                            pending[b["id"]] = (ts(e["timestamp"]), bucket(b["name"], b.get("input")))
                    turns += 1
                elif e.get("type") == "user" and isinstance(c, list):
                    for b in c:
                        if b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                            st, k = pending.pop(b["tool_use_id"])
                            tool_t[k] += max(ts(e["timestamp"]) - st, 0)
            rows[typ].append((t1 - t0, sum(tool_t.values()), turns, a.label[:50], a.path.parent.parent.name[:8]))
            toolsum[typ].update(tool_t)
    return rows, toolsum


def med(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--project-dir")
    ap.add_argument("--detail", nargs="*", default=["ui-lane-reviewer", "converge-verifier", "converge-verifier(wf)"],
                    help="agent types to break down by tool kind and longest runs")
    a = ap.parse_args()
    rows, toolsum = collect(project_dirs(a.project_dir, all_worktrees=a.project_dir is None), time.time() - a.days * 86400)

    print(f"last {a.days:g} days; minutes")
    print(f"{'agentType':30} {'n':>4} {'median':>7} {'p90':>6} {'max':>6} {'sum':>7} {'tool%':>6}")
    for typ, rs in sorted(rows.items(), key=lambda kv: -sum(r[0] for r in kv[1])):
        walls = sorted(r[0] for r in rs)
        tot = sum(walls)
        tp = sum(r[1] for r in rs) / tot * 100 if tot else 0
        print(f"{typ:30} {len(rs):4} {med(walls)/60:7.1f} {walls[int(len(walls) * 0.9)]/60:6.1f} "
              f"{walls[-1]/60:6.1f} {tot/60:7.0f} {tp:5.0f}%")
    for typ in a.detail:
        if typ not in rows:
            continue
        tot = sum(r[0] for r in rows[typ])
        print(f"\n== {typ}: tool time share of wall ({tot/60:.0f} min total)")
        for k, v in toolsum[typ].most_common(14):
            print(f"  {k:32} {v/60:7.1f} min  {v/tot*100:5.1f}%")
        print("  longest runs:")
        for r in sorted(rows[typ], reverse=True)[:6]:
            print(f"   {r[0]/60:6.1f} min  turns={r[2]:3}  tool={r[1]/r[0]*100:3.0f}%  {r[4]} {r[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
