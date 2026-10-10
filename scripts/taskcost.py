# -*- coding: utf-8 -*-
"""Spend per task (slug) and per task sub-directory, main session and subagents together.

Usage:
  python3 taskcost.py                       # last 21 days, one row per slug, project = cwd
  python3 taskcost.py --all-worktrees       # main checkout + its .claude/worktrees/* and tmp/* worktrees
  python3 taskcost.py --slug <slug>         # that slug split by tmp/<slug>/<dir> (≈ phase) and agent type
  python3 taskcost.py --days 7 --top 15
  python3 taskcost.py --project-dir ~/.claude/projects/<dir>

Attribution: a main-session API message is billed to the slug and sub-directory most recently named
in a tool call (file_path / command / Agent prompt matching `.claude/PRPs/plans/<slug>/` or
`tmp/<slug>/<dir>/`) as of the message's first transcript line; messages before any slug appears go
to "(none)". A subagent (ordinary or Workflow) is billed whole to the slug named most often in its own
prompt and tool calls, falling back to the parent's slug at the moment it was spawned. Sub-directory
names are whatever the task used (research／plan／impl／review／converge／gate…), so they read as
phases without being guessed into one.

Dollars are API-price equivalents from _usage.PRICES (cache writes priced at their 5m／1h rate); the
window applies per message timestamp.
"""
import argparse
import collections
import glob
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _usage import agent_files, cost_of, project_dirs, quality_line, read_messages  # noqa: E402

# WHY: `/private/tmp/claude-<uid>/…` is the harness scratch dir, and `tmp/review-input/` is the copy
# packed into review snapshots — neither is a task.
_SLUG_RE = re.compile(r"(?:\.claude/PRPs/plans|(?<!private/)(?<![\w.-])tmp)/([a-z0-9][a-z0-9._-]*)/(?:([A-Za-z0-9_-]+)/)?")
_NOT_SLUGS = {"review-input"}


def slugs_in(text):
    for m in _SLUG_RE.finditer(text or ""):
        slug = m.group(1)
        if slug in _NOT_SLUGS or slug.startswith("claude-"):
            continue
        sub = None if "PRPs/plans" in m.group(0) else m.group(2)
        yield slug, sub


def tool_texts(content):
    for c in content if isinstance(content, list) else []:
        if isinstance(c, dict) and c.get("type") == "tool_use":
            i = c.get("input") or {}
            yield c.get("id"), " ".join(str(i.get(k, "")) for k in ("file_path", "command", "prompt", "path", "content"))


def scan_main(jsonl, since, quality):
    """Return ([(slug, sub, $)] per in-window message, {tool_use_id: (slug, sub)} at spawn time)."""
    state = {"cur": ("(none)", None)}
    at_first, spawn_at = {}, {}

    def on_line(r, msg, first):
        if msg is None:
            return
        for tid, text in tool_texts((r.get("message") or {}).get("content")):
            for slug, sub in slugs_in(text):
                cur = state["cur"]
                state["cur"] = (slug, sub or (cur[1] if cur[0] == slug else None))
            spawn_at[tid] = state["cur"]
        if first:
            at_first[msg.id] = state["cur"]

    msgs = read_messages(jsonl, since=since, quality=quality, on_line=on_line)
    rows = [(*at_first.get(m.id, ("(none)", None)), cost_of(m, quality)) for m in msgs]
    return rows, spawn_at


def scan_sub(jsonl, since, quality):
    votes = collections.Counter()

    def on_line(r, msg, first):
        m = r.get("message") or {}
        if r.get("type") == "user" and isinstance(m.get("content"), str):
            votes.update(slugs_in(m["content"]))
        elif msg is not None:
            for _, text in tool_texts(m.get("content")):
                votes.update(slugs_in(text))

    msgs = read_messages(jsonl, since=since, quality=quality, on_line=on_line, all_lines=True)
    return sum(cost_of(m, quality) for m in msgs), votes, bool(msgs)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project-dir", default=None)
    ap.add_argument("--all-worktrees", action="store_true", help="scan the main checkout and all its worktrees")
    ap.add_argument("--days", type=float, default=21)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    dirs = project_dirs(a.project_dir, a.all_worktrees)
    since = time.time() - a.days * 86400

    main_cost = collections.defaultdict(float)                      # (slug, sub) -> $
    sub_cost = collections.defaultdict(float)                       # (slug, sub, agent type) -> $
    agents, quality = collections.Counter(), {}
    for root in dirs:
        for jsonl in glob.glob(f"{root}/*.jsonl"):
            if os.path.getmtime(jsonl) < since:
                continue
            rows, spawn_at = scan_main(jsonl, since, quality)
            for slug, sub, c in rows:
                main_cost[(slug, sub)] += c
            for af in agent_files(root, os.path.basename(jsonl)[:-6]):
                cost, votes, any_msgs = scan_sub(af.path, since, quality)
                if not any_msgs:
                    continue
                if votes:
                    slug = collections.Counter(s for s, _ in votes.elements()).most_common(1)[0][0]
                    subs = collections.Counter(sub for s, sub in votes.elements() if s == slug and sub)
                    key = (slug, subs.most_common(1)[0][0] if subs else None)
                else:
                    key = spawn_at.get(af.meta.get("toolUseId"), ("(none)", None))
                sub_cost[(*key, af.meta.get("agentType") or "general-purpose")] += cost
                agents[key[0]] += 1

    if a.slug:
        m_dir, s_dir = collections.Counter(), collections.Counter()
        types = collections.defaultdict(collections.Counter)
        for (slug, sub), c in main_cost.items():
            if slug == a.slug:
                m_dir[sub or "-"] += c
        for (slug, sub, ty), c in sub_cost.items():
            if slug == a.slug:
                s_dir[sub or "-"] += c
                types[sub or "-"][ty] += c
        print(f"{a.slug}: main ${sum(m_dir.values()):.0f}  sub ${sum(s_dir.values()):.0f}  agents {agents[a.slug]}")
        print(f"{'dir':14}{'main $':>9}{'sub $':>9}  top agent types")
        for sub in sorted(set(m_dir) | set(s_dir), key=lambda d: -(m_dir[d] + s_dir[d])):
            top = ", ".join(f"{t} {c:.0f}" for t, c in types[sub].most_common(4))
            print(f"{sub:14}{m_dir[sub]:>9.0f}{s_dir[sub]:>9.0f}  {top}")
        print(quality_line(quality))
        return

    per_slug = collections.defaultdict(lambda: [0.0, 0.0])
    for (slug, _), c in main_cost.items():
        per_slug[slug][0] += c
    for (slug, _, _), c in sub_cost.items():
        per_slug[slug][1] += c
    total = sum(m + s for m, s in per_slug.values())
    share_base = total or 1
    print(f"{'slug':44}{'main $':>9}{'sub $':>9}{'total':>9}{'share':>7}{'agents':>7}")
    for slug, (m, s) in sorted(per_slug.items(), key=lambda kv: -sum(kv[1]))[: a.top]:
        print(f"{slug[:44]:44}{m:>9.0f}{s:>9.0f}{m + s:>9.0f}{100 * (m + s) / share_base:>6.1f}%{agents[slug]:>7}")
    where = str(dirs[0]) if len(dirs) == 1 else f"{len(dirs)} project dirs"
    print(f"total ${total:.0f} over {a.days:g} days ({where})")
    print(quality_line(quality))


if __name__ == "__main__":
    main()
