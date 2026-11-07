#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Edit／Write tool latency (tool_use → tool_result) by file extension and by group, last N days.
Hooks run inside that window (PreToolUse guards, PostToolUse ts-lint), so a slow extension = a slow hook.
  python3 edits.py --days 14
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


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def scan(path, group, since, acc, hooks):
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
            for b in c:
                if b.get("type") == "tool_use" and b.get("name") in ("Edit", "Write", "MultiEdit"):
                    fp = (b.get("input") or {}).get("file_path") or ""
                    ext = pathlib.Path(fp).suffix or "(none)"
                    if "/tmp/" in fp or "/scratchpad" in fp:
                        ext += "@tmp"
                    pending[b.get("id")] = (t, b.get("name"), ext)
        elif e.get("type") == "user" and isinstance(c, list):
            for b in c:
                if b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                    st, name, ext = pending.pop(b.get("tool_use_id"))
                    if t < since:
                        continue
                    acc[(group, name, ext)].append(t - st)
                    txt = b.get("content")
                    txt = txt if isinstance(txt, str) else "".join(x.get("text") or "" for x in txt if isinstance(x, dict))
                    for h in ("ts-lint", "py-edit-guard", "generated-file-guard", "worktree-guard"):
                        if h in txt:
                            hooks[(group, h)] += 1
        elif e.get("type") == "attachment":
            txt = json.dumps(e.get("attachment") or {})[:4000]
            if "ts-lint" in txt:
                hooks[(group, "ts-lint@attach")] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=14)
    a = ap.parse_args()
    since = time.time() - a.days * 86400
    acc = collections.defaultdict(list)
    hooks = collections.Counter()
    for proj in project_dirs(None, all_worktrees=True):
        for p in sorted(glob.glob(f"{proj}/*.jsonl")):
            if pathlib.Path(p).stat().st_mtime < since:
                continue
            scan(p, "main", since, acc, hooks)
        for af in agent_files(proj):
            if af.path.stat().st_mtime < since:
                continue
            scan(af.path, af.meta.get("agentType") or "?", since, acc, hooks)
    rows = []
    for (group, name, ext), ds in acc.items():
        ds.sort()
        rows.append((sum(ds), group, name, ext, len(ds), ds[len(ds) // 2], ds[int(len(ds) * .9)], ds[-1]))
    print(f"{'group':20} {'tool':6} {'ext':14} {'n':>6} {'sum min':>8} {'median s':>9} {'p90 s':>7} {'max s':>7}")
    for tot, group, name, ext, n, md, p90, mx in sorted(rows, reverse=True)[:40]:
        print(f"{group:20} {name:6} {ext:14} {n:6} {tot/60:8.1f} {md:9.1f} {p90:7.1f} {mx:7.0f}")
    print("\nhook mentions in Edit/Write results:")
    for (g, h), n in sorted(hooks.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  {g:20} {h:22} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
