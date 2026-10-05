# -*- coding: utf-8 -*-
"""Spend per task (slug) and per task sub-directory, main session and subagents together.

Usage:
  python3 taskcost.py                       # last 21 days, one row per slug, project = cwd
  python3 taskcost.py --slug <slug>         # that slug split by tmp/<slug>/<dir> (≈ phase) and agent type
  python3 taskcost.py --days 7 --top 15
  python3 taskcost.py --project-dir ~/.claude/projects/<dir>

Attribution: a main-session turn is billed to the slug and sub-directory most recently named in a
tool call (file_path / command / Agent prompt matching `.claude/PRPs/plans/<slug>/` or
`tmp/<slug>/<dir>/`); turns before any slug appears go to "(none)". A subagent is billed whole to
the slug named most often in its own prompt and tool calls, falling back to the parent's slug at the
moment it was spawned. Sub-directory names are whatever the task used (research／plan／impl／
review／converge／gate…), so they read as phases without being guessed into one.

Dollars are API-price equivalents from agentcost.PRICES (cache write at the 5-minute rate).
"""
import argparse
import collections
import glob
import json
import os
import pathlib
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agentcost import PRICES, project_dir_for_cwd  # noqa: E402

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
    for c in content or []:
        if c.get("type") == "tool_use":
            i = c.get("input") or {}
            yield c.get("id"), " ".join(str(i.get(k, "")) for k in ("file_path", "command", "prompt", "path", "content"))


def turn_cost(msg):
    u = msg.get("usage") or {}
    price = PRICES.get(msg.get("model") or "")
    if not price:
        return 0.0
    tokens = (u.get("input_tokens", 0), u.get("cache_creation_input_tokens", 0),
              u.get("cache_read_input_tokens", 0), u.get("output_tokens", 0))
    return sum(t * p for t, p in zip(tokens, price)) / 1e6


def scan_main(jsonl, cutoff_iso):
    """Yield (slug, sub, cost) per assistant turn, plus {tool_use_id: (slug, sub)} at spawn time."""
    seen, cur, spawn_at, rows = set(), ("(none)", None), {}, []
    for line in open(jsonl, encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("type") != "assistant":
            continue
        m = r.get("message") or {}
        for tid, text in tool_texts(m.get("content")):
            for slug, sub in slugs_in(text):
                cur = (slug, sub or (cur[1] if cur[0] == slug else None))
            spawn_at[tid] = cur
        if m.get("id") in seen or (r.get("timestamp") or "") < cutoff_iso:
            continue
        seen.add(m.get("id"))
        rows.append((cur[0], cur[1], turn_cost(m)))
    return rows, spawn_at


def scan_sub(jsonl):
    seen, cost, votes = set(), 0.0, collections.Counter()
    for line in open(jsonl, encoding="utf-8"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = r.get("message") or {}
        if r.get("type") == "user" and isinstance(m.get("content"), str):
            votes.update(slugs_in(m["content"]))
        if r.get("type") != "assistant":
            continue
        for _, text in tool_texts(m.get("content")):
            votes.update(slugs_in(text))
        if m.get("id") not in seen:
            seen.add(m.get("id"))
            cost += turn_cost(m)
    return cost, votes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project-dir", default=None)
    ap.add_argument("--days", type=float, default=21)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    root = pathlib.Path(os.path.expanduser(a.project_dir)) if a.project_dir else project_dir_for_cwd()
    cutoff = time.time() - a.days * 86400
    cutoff_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(cutoff))

    main_cost = collections.defaultdict(float)                      # (slug, sub) -> $
    sub_cost = collections.defaultdict(float)                       # (slug, sub, agent type) -> $
    agents = collections.Counter()
    for jsonl in glob.glob(f"{root}/*.jsonl"):
        if os.path.getmtime(jsonl) < cutoff:
            continue
        rows, spawn_at = scan_main(jsonl, cutoff_iso)
        for slug, sub, c in rows:
            main_cost[(slug, sub)] += c
        for meta in glob.glob(f"{jsonl[:-6]}/subagents/*.meta.json"):
            sj = meta[: -len(".meta.json")] + ".jsonl"
            if not os.path.exists(sj) or os.path.getmtime(sj) < cutoff:
                continue
            info = json.load(open(meta, encoding="utf-8"))
            cost, votes = scan_sub(sj)
            if votes:
                slug = collections.Counter(s for s, _ in votes.elements()).most_common(1)[0][0]
                subs = collections.Counter(sub for s, sub in votes.elements() if s == slug and sub)
                key = (slug, subs.most_common(1)[0][0] if subs else None)
            else:
                key = spawn_at.get(info.get("toolUseId"), ("(none)", None))
            sub_cost[(*key, info.get("agentType") or "general-purpose")] += cost
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
        return

    per_slug = collections.defaultdict(lambda: [0.0, 0.0])
    for (slug, _), c in main_cost.items():
        per_slug[slug][0] += c
    for (slug, _, _), c in sub_cost.items():
        per_slug[slug][1] += c
    total = sum(m + s for m, s in per_slug.values()) or 1
    print(f"{'slug':44}{'main $':>9}{'sub $':>9}{'total':>9}{'share':>7}{'agents':>7}")
    for slug, (m, s) in sorted(per_slug.items(), key=lambda kv: -sum(kv[1]))[: a.top]:
        print(f"{slug[:44]:44}{m:>9.0f}{s:>9.0f}{m + s:>9.0f}{100 * (m + s) / total:>6.1f}%{agents[slug]:>7}")
    print(f"total ${total:.0f} over {a.days:g} days ({root})")


if __name__ == "__main__":
    main()
