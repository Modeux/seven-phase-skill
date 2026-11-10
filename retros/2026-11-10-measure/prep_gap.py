#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minutes from the owner's last typed message to each review-wave Workflow launch (script holds `lane-`).

  python3 prep_gap.py [--since 2026-10-20]

Rows whose owner message is 「繼續 … 第 6 階段」 (or the package name after a phase-5 checkpoint) are the
phase-6 dispatch latency; phase-7 converge Workflows also match `lane-` — skip them when reading.
WHY: baseline for 「等走一趟時先備第 6 階段」 (11-10): 16 phase-6 dispatches 10-20～11-06, median 7.7 min.
"""
import argparse
import datetime
import glob
import json
import os


def ts(t):
    return datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-10-20")
    a = ap.parse_args()
    since = datetime.datetime.fromisoformat(a.since).timestamp()
    root = os.path.expanduser("~/.claude/projects")
    files = glob.glob(root + "/-Users-huntwu-Desktop-deepweave-deepweave*/*.jsonl")
    for f in sorted((p for p in files if os.path.getmtime(p) >= since), key=os.path.getmtime):
        last_user = None
        for line in open(f, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") == "user" and not r.get("isSidechain"):
                c = r["message"]["content"]
                # INVARIANT: typed owner text only — tool results are lists, harness notices start with `<`.
                if isinstance(c, str) and not c.startswith("<"):
                    last_user = (r["timestamp"], c[:30].replace("\n", " "))
            if r.get("type") != "assistant" or not last_user:
                continue
            for b in r["message"].get("content", []):
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Workflow":
                    if "lane-" in json.dumps(b.get("input", {}), ensure_ascii=False):
                        d = (ts(r["timestamp"]) - ts(last_user[0])).total_seconds() / 60
                        print(f"{r['timestamp'][:16]} {d:6.1f} min  after owner: {last_user[1]!r}  {os.path.basename(f)[:8]}")


if __name__ == "__main__":
    main()
