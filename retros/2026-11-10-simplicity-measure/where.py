#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Where review findings land: rows of the 18 dedupe maps (R = MEDIUM+, L = LOW) by the kind of file they
point at and by the size of the prod file, against where the changed lines went (09-22~11-10).
  python3 where.py > where.out
"""
import collections
import glob
import re
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from extra import git  # noqa: E402
from mass import R, kind, norm  # noqa: E402
from shape import blobs  # noqa: E402

PATH = re.compile(r"(?:backend-golang|angular-workspace|ai-service|docs|\.claude)/[\w./@\-\[\]]+\.(?:go|ts|html|css|scss|py|sql|md|ya?ml)")
loc = {p: len(t.split("\n")) for p, (k, t) in blobs("dev").items() if k == "prod"}

rows = collections.Counter()
by_kind = {"R": collections.Counter(), "L": collections.Counter()}
by_size = {"R": collections.Counter(), "L": collections.Counter()}
src = {"R": collections.Counter(), "L": collections.Counter()}
hot = collections.Counter()
for f in sorted(glob.glob(f"{R}/tmp/retro-1110-fp/reviews/*.md")):
    seen = set()
    for line in open(f, encoding="utf-8"):
        m = re.match(r"\| *([RL])(\d+) *\|", line)
        if not m:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or (m.group(1) + m.group(2)) in seen:
            continue
        if not re.match(r"(CRITICAL|HIGH|MEDIUM|LOW)", cells[1]):
            continue                                   # verdict tables reuse the ids; only map rows carry a severity here
        seen.add(m.group(1) + m.group(2))
        t = m.group(1)
        rows[t] += 1
        paths = list(dict.fromkeys(PATH.findall(cells[3])))
        kinds = [kind(p) or "generated" for p in paths]
        first = kinds[0] if kinds else "(no path)"
        by_kind[t][first] += 1
        prod = [p for p in paths if kind(p) == "prod" and p in loc]
        if prod:
            big = max(loc[p] for p in prod)
            by_size[t][">2000" if big > 2000 else ">1000" if big > 1000 else ">500" if big > 500 else "<=500"] += 1
            for p in prod:
                if loc[p] > 1000:
                    hot[p] += 1
        s = cells[4].lower()
        lanes = set(re.findall(r"[a-z][a-z0-9\-]+(?=#|\(|,|、|$| )", s))
        is_c = bool(re.search(r"comment|doc", s))
        is_t = bool(re.search(r"\btest|pr-test|mutation", s))
        only = lambda pat: bool(lanes) and all(re.search(pat, x) for x in lanes)
        src[t]["any comment/doc lane"] += is_c
        src[t]["only comment/doc lanes"] += only(r"comment|doc")
        src[t]["any test lane"] += is_t
        src[t]["only test lanes"] += only(r"test|mutation")

print(f"map rows: R (MEDIUM+) {rows['R']}, L (LOW) {rows['L']}")
for t in ("R", "L"):
    n = rows[t] or 1
    print(f"[{t}] first location kind: " + ", ".join(f"{k} {v} ({v / n * 100:.0f}%)" for k, v in by_kind[t].most_common()))
    print(f"[{t}] source lanes: " + ", ".join(f"{k} {v} ({v / n * 100:.0f}%)" for k, v in src[t].most_common()))
    tot = sum(by_size[t].values()) or 1
    print(f"[{t}] rows citing a prod file, by the largest cited file's size at HEAD: "
          + ", ".join(f"{k} {by_size[t][k]} ({by_size[t][k] / tot * 100:.0f}%)" for k in ("<=500", ">500", ">1000", ">2000")))

added = collections.Counter()
for line in git("log", "dev", "--no-merges", "--since=2026-09-22T00:00:00", "--until=2026-11-11T00:00:00",
                "--numstat", "-M", "--format=").splitlines():
    parts = line.split("\t", 2)
    if len(parts) == 3 and parts[0] != "-":
        p = norm(parts[2])
        if p in loc:
            added[p] += int(parts[0]) + int(parts[1])
tot = sum(added.values()) or 1
b = collections.Counter()
for p, n in added.items():
    b[">2000" if loc[p] > 2000 else ">1000" if loc[p] > 1000 else ">500" if loc[p] > 500 else "<=500"] += n
print("changed prod lines 09-22~11-10 by file size at HEAD: " + ", ".join(f"{k} {b[k] / tot * 100:.0f}%" for k in ("<=500", ">500", ">1000", ">2000")))
allp = sum(loc.values())
print("prod LOC at HEAD by file size: " + ", ".join(
    f"{name} {sum(n for n in loc.values() if lo < n <= hi) / allp * 100:.0f}%"
    for name, lo, hi in (("<=500", 0, 500), (">500", 500, 1000), (">1000", 1000, 2000), (">2000", 2000, 10**9))))
print("files >1000 lines most cited by findings:")
for p, n in hot.most_common(12):
    print(f"  {n:4} {loc[p]:6}  {p}")
