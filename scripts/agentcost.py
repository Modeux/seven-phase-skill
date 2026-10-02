# -*- coding: utf-8 -*-
"""Subagent spend from Claude Code transcripts, for effort/agent A/B and periodic audits.

Usage:
  python3 agentcost.py                          # last 21 days, grouped by agent type, project = cwd
  python3 agentcost.py --days 7 --session <sid> # one main session's subagents
  python3 agentcost.py --agents <id1>,<id2>     # per-agent rows (the A/B view); ids from the
                                                # agent-<id>.jsonl file names or the Agent tool result
  python3 agentcost.py --project-dir ~/.claude/projects/<dir>

Dollars are API-price equivalents (cache write at the 5-minute rate), not a bill: on a subscription
they show relative weight. Context columns: first = first-turn context, last = final-turn context.
"""
import argparse
import collections
import glob
import json
import os
import pathlib
import statistics
import time

# WHY: per-MTok (input, cache write, cache read, output) from the claude-api skill model table cached
# 2026-09-25. Cache read is the same price on Opus 5.5 and Sonnet 5.5, so swapping models barely moves
# a context-heavy agent's cost — update this table before trusting cross-model comparisons.
PRICES = {
    "claude-opus-5-5": (4, 5, 0.2, 20),
    "claude-opus-5": (5, 6.25, 0.5, 25),
    "claude-fable-5-1": (10, 12.5, 0.25, 50),
    "claude-sonnet-5-5": (2, 2.5, 0.2, 10),
    "claude-sonnet-5": (2, 2.5, 0.2, 10),
    "claude-haiku-4-5-20251001": (1, 1.25, 0.1, 5),
}


def project_dir_for_cwd():
    return pathlib.Path.home() / ".claude" / "projects" / str(pathlib.Path.cwd()).replace("/", "-")


def scan(jsonl):
    seen, ctxs, cost, effort = set(), [], 0.0, collections.Counter()
    for line in open(jsonl, encoding="utf-8"):
        if '"assistant"' not in line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = r.get("message") or {}
        if r.get("type") != "assistant" or m.get("id") in seen:
            continue
        seen.add(m.get("id"))
        u = m.get("usage") or {}
        tokens = (u.get("input_tokens", 0), u.get("cache_creation_input_tokens", 0),
                  u.get("cache_read_input_tokens", 0), u.get("output_tokens", 0))
        price = PRICES.get(m.get("model") or "")
        if price:
            cost += sum(t * p for t, p in zip(tokens, price)) / 1e6
        ctxs.append(tokens[0] + tokens[1] + tokens[2])
        e = r.get("effort")
        if e:
            effort[e] += 1
    return cost, ctxs, effort


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project-dir", default=None)
    ap.add_argument("--days", type=float, default=21)
    ap.add_argument("--session", default=None, help="main session id (directory under the project dir)")
    ap.add_argument("--agents", default=None, help="comma-separated agent ids; prints one row per agent")
    a = ap.parse_args()
    root = pathlib.Path(os.path.expanduser(a.project_dir)) if a.project_dir else project_dir_for_cwd()
    cutoff = time.time() - a.days * 86400
    wanted = set(a.agents.split(",")) if a.agents else None
    pattern = f"{root}/{a.session or '*'}/subagents/*.meta.json"

    groups = collections.defaultdict(list)
    for meta in glob.glob(pattern):
        jsonl = meta[: -len(".meta.json")] + ".jsonl"
        agent_id = pathlib.Path(jsonl).stem.removeprefix("agent-")
        if not os.path.exists(jsonl) or (wanted is None and os.path.getmtime(jsonl) < cutoff):
            continue
        if wanted is not None and agent_id not in wanted:
            continue
        info = json.load(open(meta, encoding="utf-8"))
        cost, ctxs, effort = scan(jsonl)
        if not ctxs:
            continue
        key = f"{agent_id} {info.get('agentType')} {info.get('description', '')[:40]}" if wanted else info.get("agentType")
        groups[key].append((cost, ctxs, effort))

    total = sum(c for rows in groups.values() for c, _, _ in rows) or 1
    print(f"{'agent' if wanted else 'type':60}{'n':>4}{'$':>9}{'share':>7}{'$/n':>7}{'turns':>7}{'first k':>8}{'last k':>8}  effort")
    for key, rows in sorted(groups.items(), key=lambda kv: -sum(r[0] for r in kv[1])):
        cost = sum(r[0] for r in rows)
        effort = sum((r[2] for r in rows), collections.Counter())
        print(f"{key[:60]:60}{len(rows):>4}{cost:>9.2f}{100 * cost / total:>6.1f}%{cost / len(rows):>7.2f}"
              f"{statistics.median(len(r[1]) for r in rows):>7.0f}"
              f"{statistics.median(r[1][0] for r in rows) / 1e3:>8.0f}"
              f"{statistics.median(r[1][-1] for r in rows) / 1e3:>8.0f}  {dict(effort)}")
    print(f"total ${total:.2f} across {sum(len(r) for r in groups.values())} agents ({root})")


if __name__ == "__main__":
    main()
