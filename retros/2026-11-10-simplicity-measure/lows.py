#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What LOW findings are: the 18 dedupe maps' L rows by topic, how many touch an important surface
(money, permissions, minors/18+, data loss, availability), and what the convergence tables did with them.
  python3 lows.py > lows.out
"""
import collections
import glob
import random
import re

R = "/Users/huntwu/Desktop/deepweave/deepweave"
IMPORTANT = re.compile(r"18\+|18禁|未成年|成年|nsfw|minor|扣點|扣費|扣款|退款|退費|鑽石|點數|credit|diamond|餘額|付費|訂閱|訂單|金額|權限|擁有者|越權|授權|登入|token|jwt|洩漏|外洩|遺失|丟失|資料遺失|刪除使用者|斷路器|逾時|timeout|連線池|全站", re.I)
COMMENT = re.compile(r"註解|comment|文件|CLAUDE\.md|rules/|openapi 描述|說明|措辭|WHY|INVARIANT|SPEC|PLAN|過時|漂移", re.I)
TEST = re.compile(r"測試|斷言|spec\b|_test\.go|\.spec\.ts|突變|mutation|pin|fixture|守不住|空集合", re.I)
A11Y = re.compile(r"aria|焦點|鍵盤|無障礙|a11y|對比|讀屏|screen reader|tab 順序", re.I)

rows = []
verdict = {}
for f in sorted(glob.glob(f"{R}/tmp/retro-1110-fp/reviews/*.md")):
    slug = f.rsplit("/", 1)[-1][:-3]
    seen = set()
    for line in open(f, encoding="utf-8"):
        m = re.match(r"\| *(L\d+) *\|", line)
        if not m:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        key = (slug, m.group(1))
        if re.match(r"(CRITICAL|HIGH|MEDIUM|LOW)", cells[1]):
            if key not in seen:
                seen.add(key)
                rows.append((slug, m.group(1), cells[2], cells[3] if len(cells) > 3 else "", cells[4] if len(cells) > 4 else ""))
        else:
            verdict.setdefault(key, " | ".join(cells[1:5]))

n = len(rows)
cat = collections.Counter()
imp_rows = []
for slug, lid, text, where, src in rows:
    s = src.lower()
    only_comment = bool(re.search(r"comment|doc", s)) and not re.search(r"go|ts|typescript|python|security|silent|spec|impact|timing|codex|grok|agy|db|database|ui|a11y|test", re.sub(r"comment[\w\-]*|doc[\w\-]*", "", s))
    if IMPORTANT.search(text):
        cat["touches an important surface (keyword)"] += 1
        imp_rows.append((slug, lid, text))
    if only_comment or (COMMENT.search(text) and not TEST.search(text) and not IMPORTANT.search(text)):
        cat["comment / doc wording"] += 1
    elif TEST.search(text) or re.search(r"test", s):
        cat["test quality"] += 1
    elif A11Y.search(text):
        cat["a11y / UI detail"] += 1
    else:
        cat["code behaviour / other"] += 1
print(f"L rows in maps: {n}")
for k, v in cat.most_common():
    print(f"  {k}: {v} ({v / n * 100:.0f}%)")

disp = collections.Counter()
imp_disp = collections.Counter()
impkeys = {(s, l) for s, l, _ in imp_rows}
for key, v in verdict.items():
    d = "fixed" if re.search(r"修|fixed|已補|已改|片", v) and not re.search(r"不修|won.t|backlog|BACKLOG|記錄", v) else \
        "backlog/record" if re.search(r"backlog|BACKLOG|記錄|不修|won.t|接受|accept", v) else \
        "false positive" if re.search(r"false positive", v) else "other"
    disp[d] += 1
    if key in impkeys:
        imp_disp[d] += 1
print(f"verdict rows found for L ids: {len(verdict)}  -> " + ", ".join(f"{k} {v}" for k, v in disp.most_common()))
print(f"of the important-surface L rows: " + ", ".join(f"{k} {v}" for k, v in imp_disp.most_common()))
random.seed(11)
print("\nsample: 14 L rows that touch an important surface")
for slug, lid, text in random.sample(imp_rows, min(14, len(imp_rows))):
    print(f"  [{slug} {lid}] {text[:230]}")
print("\nsample: 14 other L rows")
rest = [(s, l, t) for s, l, t, _, _ in rows if (s, l) not in impkeys]
for slug, lid, text in random.sample(rest, min(14, len(rest))):
    print(f"  [{slug} {lid}] {text[:200]}")
