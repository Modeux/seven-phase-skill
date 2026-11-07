#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Latency (tool_use → tool_result) of cheap tools — Read／Grep／Glob and short Bash — per group, last N days.
A Read of a source file should take well under a second; anything above is the PreToolUse hooks.
  python3 reads.py --days 14
"""
import argparse
import collections
import datetime
import glob
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, "/Users/huntwu/.claude/skills/seven-phase/scripts")
from _usage import agent_files, project_dirs  # noqa: E402

CHEAP_BASH = re.compile(r"^\s*(ls|wc|cat|head|tail|echo|git (status|log|diff|show)|grep|rg|find|pwd|which|stat)\b")


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def scan(path, group, since, acc, total_calls):
    pending = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        if '"timestamp"' not in line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(e, dict) or "timestamp" not in e:
            continue
        t = ts(e["timestamp"])
        c = (e.get("message") or {}).get("content")
        if e.get("type") == "assistant" and isinstance(c, list):
            n_in_msg = sum(1 for b in c if b.get("type") == "tool_use")
            for b in c:
                if b.get("type") != "tool_use":
                    continue
                name = b.get("name")
                if name == "Bash":
                    if not CHEAP_BASH.match((b.get("input") or {}).get("command", "")):
                        continue
                    name = "Bash(cheap)"
                elif name not in ("Read", "Grep", "Glob"):
                    continue
                pending[b.get("id")] = (t, name, n_in_msg)
        elif e.get("type") == "user" and isinstance(c, list):
            for b in c:
                if b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                    st, name, n_in_msg = pending.pop(b.get("tool_use_id"))
                    if t >= since:
                        # WHY: parallel calls in one message share the same start time, so only solo calls
                        # measure one hook+tool round trip cleanly.
                        if n_in_msg == 1:
                            acc[(group, name)].append(t - st)
                        total_calls[(group, name)] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=14)
    a = ap.parse_args()
    since = time.time() - a.days * 86400
    acc = collections.defaultdict(list)
    total_calls = collections.Counter()
    for proj in project_dirs(None, all_worktrees=True):
        for p in sorted(glob.glob(f"{proj}/*.jsonl")):
            if pathlib.Path(p).stat().st_mtime < since:
                continue
            scan(p, "main", since, acc, total_calls)
        for af in agent_files(proj):
            if af.path.stat().st_mtime < since:
                continue
            scan(af.path, af.meta.get("agentType") or "?", since, acc, total_calls)
    print(f"{'group':20} {'tool':12} {'calls':>7} {'solo':>7} {'median s':>9} {'p90 s':>7} {'sum h(solo)':>11}")
    rows = []
    for (group, name), ds in acc.items():
        ds.sort()
        rows.append((sum(ds), group, name, total_calls[(group, name)], len(ds), ds[len(ds) // 2], ds[int(len(ds) * .9)]))
    for tot, group, name, calls, n, md, p90 in sorted(rows, reverse=True)[:30]:
        print(f"{group:20} {name:12} {calls:7} {n:7} {md:9.2f} {p90:7.2f} {tot/3600:11.1f}")
    by_tool = collections.defaultdict(list)
    for (group, name), ds in acc.items():
        by_tool[name].extend(ds)
    print("\nall groups:")
    for name, ds in by_tool.items():
        ds.sort()
        print(f"  {name:12} solo={len(ds):6} median {ds[len(ds)//2]:.2f}s  p90 {ds[int(len(ds)*.9)]:.2f}s  sum {sum(ds)/3600:.1f} h")
    return 0


if __name__ == "__main__":
    sys.exit(main())
