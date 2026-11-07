#!/usr/bin/env python3
"""Main-session Agent dispatches in the last N days: prompt sizes, boilerplate share, repeated lines."""
import collections, glob, json, os, re, statistics, sys, time, calendar

sys.path.insert(0, os.path.expanduser("~/.claude/skills/seven-phase/scripts"))
from _usage import project_dirs  # noqa: E402

DAYS = float(sys.argv[1]) if len(sys.argv) > 1 else 14
since = time.time() - DAYS * 86400
NORM = [(re.compile(r"/[\w.\-@+/]+"), "<P>"), (re.compile(r"'[^']{0,120}'"), "'S'"), (re.compile(r'"[^"]{0,120}"'), '"S"'), (re.compile(r"`[^`]{0,120}`"), "`S`"), (re.compile(r"\b\d+\b"), "N")]


def norm(l):
    l = l.strip()
    for rx, s in NORM:
        l = rx.sub(s, l)
    return l


def ep(s):
    try:
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


disp = []  # (sid, type, len, lines)
for root in project_dirs(None, True):
    for jsonl in glob.glob(f"{root}/*.jsonl"):
        try:
            if os.path.getmtime(jsonl) < since:
                continue
        except OSError:
            continue
        sid = os.path.basename(jsonl)[:-6]
        with open(jsonl, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict) or r.get("type") != "assistant" or r.get("isSidechain"):
                    continue
                t = ep(r.get("timestamp") or "")
                if t is None or t < since:
                    continue
                c = (r.get("message") or {}).get("content")
                for b in c if isinstance(c, list) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Agent", "Task"):
                        i = b.get("input") or {}
                        p = i.get("prompt") or ""
                        disp.append((sid, i.get("subagent_type") or "?", len(p), [norm(x) for x in p.splitlines() if len(x.strip()) >= 20], t))

print(f"dispatches {len(disp)} sessions {len({d[0] for d in disp})}")
sizes = sorted(d[2] for d in disp)
if sizes:
    print(f"prompt chars: median {statistics.median(sizes):.0f} p90 {sizes[int(len(sizes)*0.9)]} max {sizes[-1]} total KB {sum(sizes)/1000:.0f}")
by = collections.defaultdict(list)
for d in disp:
    by[d[1]].append(d[2])
print("per type: n / median chars")
for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
    print(f"  {k:22} {len(v):4} {statistics.median(v):8.0f}")

# boilerplate: normalized lines appearing in >=3 dispatches from >=2 sessions
occ = collections.defaultdict(set)   # line -> set of dispatch idx
sess = collections.defaultdict(set)
for idx, d in enumerate(disp):
    for l in set(d[3]):
        occ[l].add(idx)
        sess[l].add(d[0])
boiler = {l for l, s in occ.items() if len(s) >= 3 and len(sess[l]) >= 2}
tot_lines = sum(len(d[3]) for d in disp)
boiler_lines = sum(1 for d in disp for l in d[3] if l in boiler)
tot_chars = sum(len(l) for d in disp for l in d[3])
boiler_chars = sum(len(l) for d in disp for l in d[3] if l in boiler)
print(f"\nboilerplate lines (>=3 dispatches, >=2 sessions): {boiler_lines}/{tot_lines} lines = {100*boiler_lines/max(1,tot_lines):.0f}%, chars {100*boiler_chars/max(1,tot_chars):.0f}%")
print("top repeated lines (dispatches / sessions):")
for l, s in sorted(occ.items(), key=lambda kv: -len(kv[1]))[:40]:
    if len(s) >= 3:
        print(f"  {len(s):4} {len(sess[l]):3}  {l[:110]}")
