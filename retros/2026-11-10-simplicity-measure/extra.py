#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Four spot checks behind the simplicity retro:
  1. comment blocks added to prod code per period: how many start with WHY:/INVARIANT:, block length, a sample
  2. source-reading (pin) test files added since 09-21: names, to validate the heuristic by eye
  3. the longest Go functions at HEAD
  4. growth between the phase-6 review snapshot and the committed change, per task that still has its snapshot
  python3 extra.py > extra.out
"""
import collections
import pathlib
import random
import re
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from mass import MARK, PERIODS, R, kind  # noqa: E402
from shape import GO_AST, GO_READ, SRC_LIT, TS_READ, blobs, rev_before  # noqa: E402


def git(*args):
    return subprocess.run(["git", "-C", R, *args], capture_output=True, text=True, check=True, errors="replace").stdout


def is_comment(s, py):
    if py:
        return s.startswith("#")
    return s.startswith("//") or s.startswith("/*") or re.match(r"\*(\s|/|$)", s) is not None


def comment_blocks(since, until):
    out = git("log", "dev", "--no-merges", f"--since={since}", f"--until={until}", "-p", "-U0", "-M",
              "--format=@@%H", "--", "*.go", "*.ts", "*.py")
    blocks = []          # (n_lines, has_marker, first_line, path)
    cur = None
    k = None
    py = False
    path = None

    def close():
        nonlocal cur
        if cur:
            blocks.append(tuple(cur))
        cur = None

    for line in out.split("\n"):
        if line.startswith("+++ "):
            close()
            path = line[6:] if line.startswith("+++ b/") else None
            k = kind(path) if path else None
            py = bool(path and path.endswith(".py"))
            continue
        if line.startswith("@@") or line.startswith("diff "):
            close()
            continue
        if k != "prod" or not line.startswith("+"):
            close() if not line.startswith("+") else None
            continue
        s = line[1:].strip()
        if s and is_comment(s, py):
            if cur is None:
                cur = [0, False, s, path]
            cur[0] += 1
            if MARK.search(s):
                cur[1] = True
        else:
            close()
    close()
    return blocks


def part1():
    print("## 1. comment blocks added to prod .go/.ts/.py")
    for name, since, until in PERIODS:
        b = comment_blocks(since, until)
        n = len(b) or 1
        lines = sum(x[0] for x in b) or 1
        marked = [x for x in b if x[1]]
        ml = sum(x[0] for x in marked)
        sizes = sorted(x[0] for x in b)
        print(f"{name}: blocks {len(b)}, lines {lines}; blocks with WHY/INVARIANT {len(marked)} ({len(marked) / n * 100:.0f}%), "
              f"lines inside them {ml} ({ml / lines * 100:.0f}%); block length median {sizes[len(sizes) // 2] if sizes else 0}, "
              f"p90 {sizes[int(len(sizes) * .9)] if sizes else 0}, max {sizes[-1] if sizes else 0}; "
              f"marked block mean {ml / (len(marked) or 1):.1f} lines, unmarked mean "
              f"{(lines - ml) / ((len(b) - len(marked)) or 1):.1f}")
        if name.startswith("P2"):
            random.seed(7)
            unmarked = [x for x in b if not x[1]]
            print(f"  sample of 30 unmarked block first lines (of {len(unmarked)}):")
            for x in random.sample(unmarked, min(30, len(unmarked))):
                print(f"    [{x[0]}L] {x[2][:150]}   <- {x[3].rsplit('/', 1)[-1]}")
            print("  sample of 12 marked block first lines:")
            for x in random.sample(marked, min(12, len(marked))):
                print(f"    [{x[0]}L] {x[2][:170]}   <- {x[3].rsplit('/', 1)[-1]}")


def part2():
    print("\n## 2. source-reading test files at HEAD that did not exist at 09-21")
    old = blobs(rev_before("2026-09-22"))
    new = blobs("dev")
    rows = []
    for path, (k, text) in new.items():
        if k != "test":
            continue
        go = path.endswith(".go") and ((GO_READ.search(text) and SRC_LIT.search(text)) or GO_AST.search(text))
        ts = path.endswith(".ts") and TS_READ.search(text)
        if go or ts:
            rows.append((path not in old, len(text.split("\n")), path,
                         "ast" if path.endswith(".go") and GO_AST.search(text) else "read"))
    newrows = [r for r in rows if r[0]]
    print(f"at HEAD {len(rows)} files; new since 09-21: {len(newrows)} files, {sum(r[1] for r in newrows)} lines")
    names = collections.Counter()
    for _, _, path, _ in newrows:
        base = path.rsplit("/", 1)[-1]
        m = re.search(r"_(\w+?)_test\.go$|\.(\w+)\.spec\.ts$", base)
        tail = re.findall(r"(contract|pin|guard|wiring|openapi|parity|drift|audit|static|lint|integration|migration|schema|doc|comment|slug)", base)
        names[tail[-1] if tail else "(other)"] += 1
    print("  name keywords: " + ", ".join(f"{k} {v}" for k, v in names.most_common()))
    random.seed(3)
    for _, n, path, how in random.sample(newrows, min(25, len(newrows))):
        print(f"    {n:5} {how:4} {path}")


def part3():
    print("\n## 3. longest Go functions at HEAD (prod)")
    new = blobs("dev")
    rows = []
    for path, (k, text) in new.items():
        if k != "prod" or not path.endswith(".go"):
            continue
        start = None
        name = ""
        for i, line in enumerate(text.split("\n")):
            if start is None:
                if line.startswith("func ") and not (line.rstrip().endswith("}") and "{" in line):
                    start = i
                    name = line[:90]
            elif line.startswith("}"):
                rows.append((i - start + 1, path, name))
                start = None
    rows.sort(reverse=True)
    for n, path, name in rows[:18]:
        print(f"  {n:5}  {path.replace('backend-golang/', '')}  {name}")


def added(patch):
    tot = collections.Counter()
    k = None
    try:
        text = pathlib.Path(patch).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.split("\n"):
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            k = kind(path) if path else None
            continue
        if line.startswith("--- "):
            continue
        if k and line.startswith("+"):
            tot[k + "+"] += 1
        elif k and line.startswith("-"):
            tot[k + "-"] += 1
    return tot


def part4():
    print("\n## 4. phase-6 snapshot vs committed change (added lines; delta = what review + convergence added)")
    print(f"  {'task':28} {'snap prod+':>10} {'final prod+':>11} {'d%':>6} {'snap test+':>10} {'final test+':>11} {'d%':>6}  feat commit")
    tmp = pathlib.Path(R) / "tmp"
    sp = st = fp = ft = 0
    for d in sorted(tmp.iterdir()):
        rv = d / "review"
        if not (rv / "change.patch").exists() or (d / "review2").exists():
            continue
        snap = added(rv / "change.patch")
        log = git("log", "dev", "--format=%H\t%s", f"--grep=docs(prps): {d.name}", "--fixed-strings").strip().split("\n")
        log = [x for x in log if x]
        if not log:
            continue
        doc_sha = log[-1].split("\t")[0]
        par = git("log", "-1", "--format=%H\t%s", doc_sha + "^").strip().split("\t")
        if len(par) < 2 or not re.match(r"(feat|fix|perf|refactor)", par[1]):
            continue
        fin = collections.Counter()
        for line in git("show", "--numstat", "-M", "--format=", par[0]).split("\n"):
            parts = line.split("\t", 2)
            if len(parts) != 3 or parts[0] == "-":
                continue
            kk = kind(parts[2])
            if kk:
                fin[kk + "+"] += int(parts[0])
        a, b, c, e = snap["prod+"] + snap["sql+"], fin["prod+"] + fin["sql+"], snap["test+"], fin["test+"]
        if not a or not b:
            continue
        sp, fp, st, ft = sp + a, fp + b, st + c, ft + e
        print(f"  {d.name:28} {a:10} {b:11} {(b - a) / a * 100:6.0f} {c:10} {e:11} {((e - c) / c * 100 if c else 0):6.0f}  {par[1][:50]}")
    if sp:
        print(f"  {'TOTAL':28} {sp:10} {fp:11} {(fp - sp) / sp * 100:6.0f} {st:10} {ft:11} {((ft - st) / st * 100 if st else 0):6.0f}")


if __name__ == "__main__":
    part1()
    part2()
    part3()
    part4()
