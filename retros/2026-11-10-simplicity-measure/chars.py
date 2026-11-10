#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Comment share of prod .go/.ts/.py by characters at HEAD, and how many added comment blocks in P2
carry the word WHY/INVARIANT without the fixed `WHY:` prefix.
  python3 chars.py > chars.out
"""
import re
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from extra import git, is_comment  # noqa: E402
from mass import MARK, PERIODS, kind  # noqa: E402
from shape import blobs  # noqa: E402

c = {"prod": [0, 0], "test": [0, 0]}
for path, (k, text) in blobs("dev").items():
    if k not in c or path.rsplit(".", 1)[-1] not in ("go", "ts", "py"):
        continue
    py = path.endswith(".py")
    for line in text.split("\n"):
        s = line.strip()
        if s:
            c[k][0 if is_comment(s, py) else 1] += len(s)
for k, (com, code) in c.items():
    print(f"HEAD {k}: comment chars {com / 1e6:.2f} M, code chars {code / 1e6:.2f} M, comment share {com / (com + code) * 100:.1f}%")

_, since, until = PERIODS[2]
out = git("log", "dev", "--no-merges", f"--since={since}", f"--until={until}", "-p", "-U0", "-M", "--format=@@%H",
          "--", "*.go", "*.ts", "*.py")
blocks, cur, k, py = [], None, None, False
for line in out.split("\n"):
    if line.startswith("+++ "):
        if cur:
            blocks.append(cur)
        cur = None
        path = line[6:] if line.startswith("+++ b/") else None
        k = kind(path) if path else None
        py = bool(path and path.endswith(".py"))
        continue
    s = line[1:].strip() if line.startswith("+") else ""
    if k == "prod" and s and is_comment(s, py):
        cur = (cur or []) + [s]
    else:
        if cur:
            blocks.append(cur)
        cur = None
n = len(blocks)
strict = sum(1 for b in blocks if any(MARK.search(s) for s in b))
loose = sum(1 for b in blocks if not any(MARK.search(s) for s in b) and any(re.search(r"\b(WHY|INVARIANT)\b", s) for s in b))
task = sum(1 for b in blocks if any(re.search(r"\b(SPEC|PLAN|REVIEW) ?§|\b[PRLF]\d{1,3}\b[:：]|包 ?[A-Z0-9]|第 ?\d ?階段", s) for s in b))
lines = lambda sel: sum(len(b) for b in blocks if sel(b))
is_strict = lambda b: any(MARK.search(s) for s in b)
is_loose = lambda b: not is_strict(b) and any(re.search(r"\b(WHY|INVARIANT)\b", s) for s in b)
tot = sum(len(b) for b in blocks)
print(f"P2 added prod comment blocks {n} ({tot} lines): fixed prefix {strict} ({strict / n * 100:.0f}% blocks, {lines(is_strict) / tot * 100:.0f}% lines); "
      f"WHY/INVARIANT word without the prefix {loose} ({loose / n * 100:.0f}% blocks, {lines(is_loose) / tot * 100:.0f}% lines); "
      f"neither {n - strict - loose} ({(n - strict - loose) / n * 100:.0f}% blocks, {(tot - lines(is_strict) - lines(is_loose)) / tot * 100:.0f}% lines); "
      f"blocks citing a task code or SPEC/PLAN section {task} ({task / n * 100:.0f}%)")
