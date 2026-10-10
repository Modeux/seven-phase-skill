# -*- coding: utf-8 -*-
"""Subagent spend from Claude Code transcripts, for effort/agent A/B and periodic audits.

Usage:
  python3 agentcost.py                          # last 21 days, grouped by agent type, project = cwd
  python3 agentcost.py --all-worktrees          # main checkout + its .claude/worktrees/* and tmp/* worktrees
  python3 agentcost.py --days 7 --session <sid> # one main session's subagents
  python3 agentcost.py --agents <id1>,<id2>     # per-agent rows (the A/B view); ids from the
                                                # agent-<id>.jsonl file names or the Agent tool result
  python3 agentcost.py --project-dir ~/.claude/projects/<dir>

Covers ordinary subagents and Workflow agents (`subagents/workflows/<wf>/`); Workflow agents group as
`<type> [wf]`. Dollars are API-price equivalents (cache writes priced at their 5m／1h rate), not a bill:
on a subscription they show relative weight. The window applies per message timestamp (--agents
ignores it). Context columns: first = first-turn context, last = final-turn context.
"""
import argparse
import collections
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _usage import PRICES, agent_files, context_of, cost_of, project_dirs, quality_line, read_messages  # noqa: E402,F401


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project-dir", default=None)
    ap.add_argument("--all-worktrees", action="store_true", help="scan the main checkout and all its worktrees")
    ap.add_argument("--days", type=float, default=21)
    ap.add_argument("--session", default=None, help="main session id (directory under the project dir)")
    ap.add_argument("--agents", default=None, help="comma-separated agent ids; prints one row per agent")
    a = ap.parse_args()
    dirs = project_dirs(a.project_dir, a.all_worktrees)
    wanted = set(a.agents.split(",")) if a.agents else None
    since = None if wanted else time.time() - a.days * 86400

    groups, quality = collections.defaultdict(list), {}
    for d in dirs:
        for af in agent_files(d, a.session):
            if wanted is not None and af.agent_id not in wanted:
                continue
            msgs = read_messages(af.path, since=since, quality=quality)
            if not msgs:
                continue
            cost = sum(cost_of(m, quality) for m in msgs)
            effort = collections.Counter(m.effort for m in msgs if m.effort)
            kind = f"{af.meta.get('agentType')}{' [wf]' if af.is_workflow else ''}"
            key = f"{af.agent_id} {kind} {af.label[:40]}" if wanted else kind
            groups[key].append((cost, [context_of(m) for m in msgs], effort))

    total = sum(c for rows in groups.values() for c, _, _ in rows)
    share_base = total or 1
    print(f"{'agent' if wanted else 'type':60}{'n':>4}{'$':>9}{'share':>7}{'$/n':>7}{'turns':>7}{'first k':>8}{'last k':>8}  effort")
    for key, rows in sorted(groups.items(), key=lambda kv: -sum(r[0] for r in kv[1])):
        cost = sum(r[0] for r in rows)
        effort = sum((r[2] for r in rows), collections.Counter())
        print(f"{key[:60]:60}{len(rows):>4}{cost:>9.2f}{100 * cost / share_base:>6.1f}%{cost / len(rows):>7.2f}"
              f"{statistics.median(len(r[1]) for r in rows):>7.0f}"
              f"{statistics.median(r[1][0] for r in rows) / 1e3:>8.0f}"
              f"{statistics.median(r[1][-1] for r in rows) / 1e3:>8.0f}  {dict(effort)}")
    where = str(dirs[0]) if len(dirs) == 1 else f"{len(dirs)} project dirs"
    print(f"total ${total:.2f} across {sum(len(r) for r in groups.values())} agents ({where})")
    print(quality_line(quality))


if __name__ == "__main__":
    main()
