# -*- coding: utf-8 -*-
"""Where review findings end up: per REVIEW.md, verdict rows by final severity and disposition.

  python3 verdicts.py <dir with <slug>.md REVIEW copies>

Reads tables under a heading containing 裁決 whose header has a 裁決 column; rows whose first cell starts with
R／L ids. A grouped row (`L2、L4`) counts once per id. Disposition is keyword-bucketed from the 處置 cell
(falls back to the whole row): backlog · accept(不修／接受／維持／已知限制) · fixed(anything else on a confirmed row).
Verdict: fp(false positive／不成立／駁回／撤回) · confirmed(the rest). Rough by design: prints unread files.
"""
import collections
import glob
import os
import re
import sys

ID = re.compile(r"\b([RL])\d+[a-z]?\b")
SEV = re.compile(r"CRITICAL|HIGH|MEDIUM|LOW|嚴重|高|中|低")
SEVMAP = {"CRITICAL": "H", "HIGH": "H", "嚴重": "H", "高": "H", "MEDIUM": "M", "中": "M", "LOW": "L", "低": "L"}


def cells(line):
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def main():
    tot = collections.Counter()
    print(f"{'task':28}{'rows':>5}{'fp':>4}{'conf':>5}  {'H':>3}{'M':>4}{'L':>4}  {'fixed':>6}{'backlog':>8}{'accept':>7}")
    for path in sorted(glob.glob(os.path.join(sys.argv[1], "*.md"))):
        in_sec, hdr, c = False, None, collections.Counter()
        for line in open(path, encoding="utf-8", errors="replace"):
            if line.startswith("#"):
                in_sec, hdr = "裁決" in line, None
                continue
            if not in_sec or not line.lstrip().startswith("|"):
                continue
            row = cells(line)
            if hdr is None:
                if any("裁決" in x for x in row):
                    hdr = row
                continue
            if set(row[0]) <= set("-: "):
                continue
            ids = ID.findall(row[0])
            if not ids:
                continue
            col = {name: i for i, name in enumerate(hdr)}
            vi = next((i for n, i in col.items() if n.startswith("裁決")), 1)
            si = next((i for n, i in col.items() if "最終嚴重度" in n), next((i for n, i in col.items() if "嚴重度" in n), None))
            di = next((i for n, i in col.items() if "處置" in n), None)
            verdict = row[vi] if vi < len(row) else ""
            sev_cell = re.sub(r"[（(].*?[)）]", "", row[si]) if si is not None and si < len(row) else ""
            m = SEV.findall(sev_cell)
            sev = SEVMAP[m[-1]] if m else ids[0]
            sev = {"R": "M"}.get(sev, sev)
            disp = row[di] if di is not None and di < len(row) else " ".join(row)
            n = len(ids)
            c["rows"] += n
            if re.search(r"false positive|不成立|駁回|撤回|rejected|誤報", verdict, re.I):
                c["fp"] += n
                continue
            c["conf"] += n
            c[sev] += n
            if re.search(r"BACKLOG|backlog", disp):
                kind = "backlog"
            elif re.search(r"不修|接受|維持|已知限制|不處理|不做|won't fix", disp + verdict):
                kind = "accept"
            else:
                kind = "fixed"
            c[kind] += n
            c[sev + ":" + kind] += n
        name = os.path.basename(path)[:-3]
        if not c["rows"]:
            print(f"{name:28}  (no verdict rows read)")
            continue
        tot.update(c)
        print(f"{name:28}{c['rows']:>5}{c['fp']:>4}{c['conf']:>5}  {c['H']:>3}{c['M']:>4}{c['L']:>4}  {c['fixed']:>6}{c['backlog']:>8}{c['accept']:>7}")
    print("severity x disposition: " + "  ".join(f"{k}={tot[k]}" for k in sorted(tot) if ":" in k))
    print(f"{'TOTAL':28}{tot['rows']:>5}{tot['fp']:>4}{tot['conf']:>5}  {tot['H']:>3}{tot['M']:>4}{tot['L']:>4}  {tot['fixed']:>6}{tot['backlog']:>8}{tot['accept']:>7}")


if __name__ == "__main__":
    main()
