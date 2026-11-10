#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replay of a size ratchet over 10-10~11-10 commits on dev: how often would it have fired?
Rule A: a prod .go/.ts/.py file already >1000 lines gains >50 net lines in one commit.
Rule B: a Go function ends >150 lines having been <=150 (or new), or grows >20 lines when already >150.
  python3 ratchet_probe.py > ratchet_probe.out
"""
import collections
import re
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from mass import R, kind, norm  # noqa: E402


def git(*args, check=True):
    return subprocess.run(["git", "-C", R, *args], capture_output=True, text=True, check=check, errors="replace").stdout


def funcs(text):
    out = {}
    start = None
    name = ""
    for i, line in enumerate(text.split("\n")):
        if start is None:
            if line.startswith("func ") and not (line.rstrip().endswith("}") and "{" in line):
                start = i
                m = re.match(r"func (\([^)]*\) )?([\w.]+)", line)
                name = (m.group(1) or "") + m.group(2) if m else line[:60]
        elif line.startswith("}"):
            out[name] = i - start + 1
            start = None
    return out


log = git("log", "dev", "--no-merges", "--since=2026-10-10T00:00:00", "--until=2026-11-11T00:00:00",
          "--numstat", "-M", "--format=@@%H\t%ad\t%s", "--date=short")
commits = []
cur = None
for line in log.splitlines():
    if line.startswith("@@"):
        sha, date, subj = line[2:].split("\t", 2)
        cur = {"sha": sha, "date": date, "subj": subj, "files": []}
        commits.append(cur)
        continue
    parts = line.split("\t", 2)
    if len(parts) == 3 and parts[0] != "-":
        path = norm(parts[2])
        if kind(path) == "prod" and path.rsplit(".", 1)[-1] in ("go", "ts", "py"):
            cur["files"].append((path, int(parts[0]), int(parts[1])))

fired_a = []
fired_b = []
touch = 0
for c in commits:
    if not c["files"]:
        continue
    touch += 1
    a_hits, b_hits = [], []
    for path, add, dele in c["files"]:
        net = add - dele
        before = git("show", f"{c['sha']}^:{path}", check=False)
        n_before = before.count("\n")
        if n_before > 1000 and net > 50:
            a_hits.append((path, n_before, net))
        if path.endswith(".go") and add >= 15:
            after = git("show", f"{c['sha']}:{path}", check=False)
            fb, fa = funcs(before), funcs(after)
            for name, n in fa.items():
                old = fb.get(name, 0)
                if (n > 150 and old <= 150) or (old > 150 and n - old > 20):
                    b_hits.append((path, name, old, n))
    if a_hits:
        fired_a.append((c, a_hits))
    if b_hits:
        fired_b.append((c, b_hits))

print(f"commits touching prod .go/.ts/.py: {touch}")
print(f"rule A (file >1000 lines gains >50 net): fired in {len(fired_a)} commits, "
      f"{sum(len(h) for _, h in fired_a)} file-hits, {sum(x[2] for _, h in fired_a for x in h)} net lines landed in such files")
files = collections.Counter()
for _, h in fired_a:
    for path, _, net in h:
        files[path] += net
for path, net in files.most_common(15):
    print(f"   +{net:5}  {path}")
print(f"rule B (Go function crosses 150 lines, or >150 grows by >20): fired in {len(fired_b)} commits, "
      f"{sum(len(h) for _, h in fired_b)} function-hits")
fn = collections.Counter()
for _, h in fired_b:
    for path, name, old, n in h:
        fn[(path.replace("backend-golang/", ""), name)] += n - old
for (path, name), d in fn.most_common(15):
    print(f"   +{d:5}  {path}  {name}")
both = {c["sha"] for c, _ in fired_a} | {c["sha"] for c, _ in fired_b}
print(f"commits where either rule fires: {len(both)} of {touch}")
