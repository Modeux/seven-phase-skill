#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Real-browser passes (ui-lane-reviewer runs) per task, with gaps >10 min between transcript entries
counted as offline instead of wall.

  python3 walks.py [--since 2026-11-01]

Task = the slug most often named as `tmp/<slug>/` or `plans/<slug>/` in the run's first prompt. Workflow
lane-ui runs live elsewhere and are not counted here. A forked session keeps a copy of its subagents, so the
same run can print twice (same start, same minutes).
WHY offline trimming: 11-09 owner went offline mid-wave and three tdd-guide runs read 129 min; one 11-06 walk
read 632 min with 572 of them offline.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re


def ts(t):
    return datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-11-01")
    a = ap.parse_args()
    since = ts(a.since + "T00:00:00Z")
    root = os.path.expanduser("~/.claude/projects")
    rows = []
    for meta in glob.glob(root + "/-Users-huntwu-Desktop-deepweave-deepweave*/*/subagents/*.meta.json"):
        m = json.load(open(meta, encoding="utf-8"))
        if m.get("agentType") != "ui-lane-reviewer":
            continue
        f = meta.replace(".meta.json", ".jsonl")
        if not os.path.exists(f):
            continue
        stamps, first = [], ""
        for line in open(f, encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("timestamp"):
                stamps.append(ts(d["timestamp"]))
            if not first and d.get("type") == "user":
                c = d["message"]["content"]
                first = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
        if not stamps:
            continue
        stamps.sort()
        if stamps[0] < since:
            continue
        off = sum((b - x).total_seconds() for x, b in zip(stamps, stamps[1:]) if (b - x).total_seconds() > 600)
        wall = ((stamps[-1] - stamps[0]).total_seconds() - off) / 60
        slug = collections.Counter(re.findall(r"(?:tmp/|plans/)([a-z0-9][a-z0-9-]+)/", first)).most_common(1)
        rows.append((slug[0][0] if slug else "?", stamps[0], wall, off / 60, (m.get("description") or "")[:40]))
    by = collections.defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    for slug, rs in sorted(by.items(), key=lambda kv: -sum(r[2] for r in kv[1])):
        rs.sort(key=lambda r: r[1])
        print(f"{slug:32} passes={len(rs):2} active_min={sum(r[2] for r in rs):6.0f}  offline_min={sum(r[3] for r in rs):5.0f}")
        for r in rs:
            print(f"    {r[1]:%m-%d %H:%M} {r[2]:6.1f}  off={r[3]:5.0f}  {r[4]}")


if __name__ == "__main__":
    main()
