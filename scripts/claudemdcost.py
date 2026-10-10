# -*- coding: utf-8 -*-
"""What on-demand instruction files cost: nested CLAUDE.md and path-scoped rules loaded mid-run.

Usage:
  python3 claudemdcost.py                 # last 21 days, project = cwd
  python3 claudemdcost.py --days 7 --top 20

These files arrive as `nested_memory` attachments the first time a run reads a file under their
directory, then ride in context until the run ends or compacts — so their cost is
size × remaining turns × cache-read price, plus one cache write. Counted once per file per run
(re-counted after a compact boundary, which drops them). Worktree copies under tmp/ or
.claude/worktrees/ are folded onto the repo-relative path, so a file's row covers every copy.

Token sizes are estimated (CJK char ≈ 1 token, other chars ≈ 1/3.5); a 10-05 check against the
cache write on the attaching turn matched within the noise of the other content in that turn.
"""
import argparse
import collections
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _usage import CACHE_1H_MULT, PRICES, agent_files, project_dirs  # noqa: E402

_CJK = re.compile(r"[　-鿿＀-￯]")
_WT = re.compile(r"^(?:tmp/(?:[^/]+/)*?wt-[^/]+|\.claude/worktrees/[^/]+)/")


def est_tokens(text):
    cjk = len(_CJK.findall(text))
    return cjk + (len(text) - cjk) / 3.5


def repo_rel(path):
    rel = re.sub(r"^.*?/deepweave/deepweave/", "", path)
    inner = rel
    while _WT.match(inner):  # a review snapshot can sit inside a session worktree
        inner = _WT.sub("", inner, count=1)
    return inner, inner != rel


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project-dir", default=None)
    ap.add_argument("--days", type=float, default=21)
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    root = project_dirs(a.project_dir)[0]
    cutoff = time.time() - a.days * 86400

    cost, size, runs, in_wt = collections.Counter(), {}, collections.Counter(), collections.Counter()
    # WHY: main sessions write 1h cache (promptCacheTtl "1h"), subagents and Workflow agents 5m.
    files = [(f, True) for f in glob.glob(f"{root}/*.jsonl")] + [(str(af.path), False) for af in agent_files(root)]
    for f, is_main in files:
        if os.path.getmtime(f) < cutoff:
            continue
        turn, seen, active, events, bounds, price = 0, set(), set(), [], [], PRICES["claude-opus-5-5"]
        for line in open(f, encoding="utf-8"):
            if '"compact_boundary"' in line:
                bounds.append(turn)
                active = set()
            if '"nested_memory"' in line:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                att = r.get("attachment") or {}
                if att.get("type") != "nested_memory" or att.get("path") in active:
                    continue
                active.add(att.get("path"))
                body = att.get("content")
                text = body.get("content", "") if isinstance(body, dict) else str(body)
                events.append((turn, att.get("path", ""), est_tokens(text)))
            elif '"assistant"' in line:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = r.get("message") or {}
                if r.get("type") == "assistant" and m.get("id") not in seen:
                    seen.add(m.get("id"))
                    turn += 1
                    price = PRICES.get(m.get("model") or "", price)
        for t, path, n in events:
            end = next((b for b in bounds if b > t), turn)
            rel, wt = repo_rel(path)
            write = price[0] * CACHE_1H_MULT if is_main else price[1]
            cost[rel] += (n * max(end - t, 0) * price[2] + n * write) / 1e6
            size[rel] = max(size.get(rel, 0), n)
            runs[rel] += 1
            in_wt[rel] += wt
    total = sum(cost.values())
    print(f"on-demand instruction files, last {a.days:g} days: ${total:.0f}")
    print(f"{'file':78}{'~tok':>7}{'runs':>6}{'in wt':>7}{'$':>8}")
    for rel, c in cost.most_common(a.top):
        print(f"{rel[:78]:78}{size[rel]:>7.0f}{runs[rel]:>6}{in_wt[rel]:>7}{c:>8.0f}")


if __name__ == "__main__":
    main()
