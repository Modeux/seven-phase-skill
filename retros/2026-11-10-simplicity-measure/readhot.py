#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What agents read into context and what they write: tool_result characters attributed to files
(Read by file_path; Bash by the code/doc paths named in the command), by agent group and artifact kind,
plus characters written (Edit new_string, Write content). Last N days, main checkout + worktrees.
  python3 readhot.py --days 31 > readhot.out
"""
import argparse
import collections
import datetime
import glob
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, "/Users/huntwu/.claude/skills/seven-phase/scripts")
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _usage import agent_files, project_dirs  # noqa: E402
from mass import kind  # noqa: E402

PATH_RE = re.compile(r"[\w./@\-\[\]+]+\.(?:go|ts|html|css|scss|py|sql|md|ya?ml|json|mjs|js|sh|out|log|txt|patch)\b")
TOP = re.compile(r".*/(backend-golang|angular-workspace|ai-service)/")
REVIEW = {"lane-reviewer", "pr-test-analyzer", "comment-analyzer", "security-reviewer", "database-reviewer",
          "silent-failure-hunter", "typescript-reviewer", "go-reviewer", "python-reviewer", "a11y-architect",
          "code-reviewer", "fastapi-reviewer"}
EXPLORE = {"code-explorer", "planner", "code-architect", "architect", "Explore", "Plan"}


def group(agent_type):
    if agent_type is None:
        return "main"
    if agent_type == "tdd-guide":
        return "tdd-guide"
    if agent_type in REVIEW:
        return "review-lanes"
    if agent_type == "converge-verifier":
        return "converge-verifier"
    if agent_type == "ui-lane-reviewer":
        return "ui-lane-reviewer"
    if agent_type == "doc-slice":
        return "doc-slice"
    if agent_type in EXPLORE:
        return "explore-plan"
    return "other-agents"


def rel(p):
    p = p.strip("'\"")
    m = TOP.match(p)
    if m:
        return p[m.end() - len(m.group(1)) - 1:]
    if "/.claude/skills/seven-phase/" in p:
        return "skill:" + p.split("/.claude/skills/seven-phase/", 1)[1]
    for marker in ("/.claude/rules/", "/.claude/PRPs/"):
        if marker in p:
            return marker[1:] + p.rsplit(marker, 1)[1]
    if p.endswith("CLAUDE.md"):
        return "CLAUDE.md(root)" if "/" not in p.rstrip("/").split("deepweave/")[-1] else p.split("deepweave/")[-1]
    if "/tmp/" in p or p.startswith("tmp/"):
        return "scratch:" + p.rsplit("tmp/", 1)[1].split("/", 1)[0]
    if "/scratchpad/" in p:
        return "scratch:scratchpad"
    return "other:" + p.rsplit("/", 1)[-1]


def kind_of(r, raw=None):
    """Artifact kind from the raw path token (so a bare `service.go` after `cd` still counts as prod)."""
    p = (raw or r).strip("'\"")
    if "/.claude/skills/seven-phase/" in p or r.startswith("skill:"):
        return "skill"
    if p.endswith(".patch"):
        return "patch(diff under review)"
    if re.search(r"\.(out|log|txt)$", p):
        return "logs/test-output"
    scratch = "/tmp/" in p or p.startswith("tmp/") or "/scratchpad/" in p or r.startswith("scratch:")
    if scratch and p.endswith(".md"):
        return "briefs/reports(tmp md)"
    if p.endswith(".json"):
        return "json"
    name = r[6:] if r.startswith("other:") else r
    if r.startswith("scratch:"):
        name = p.rsplit("/", 1)[-1]
    return kind(name) or "generated"


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def text_len(c):
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return sum(len((x.get("text") or "")) if isinstance(x, dict) else len(str(x)) for x in c)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=31)
    a = ap.parse_args()
    since = time.time() - a.days * 86400
    read_kind = collections.defaultdict(collections.Counter)      # group -> kind -> chars
    read_file = collections.defaultdict(collections.Counter)      # group -> file -> chars
    file_agents = collections.defaultdict(set)                    # file -> agent instances
    file_calls = collections.Counter()
    wrote = collections.defaultdict(collections.Counter)          # group -> kind -> chars
    inst = collections.defaultdict(list)                          # group -> [(read_chars, distinct_prod_files, wrote_chars)]
    unattributed = collections.Counter()
    for proj in project_dirs(None, all_worktrees=True, cwd="/Users/huntwu/Desktop/deepweave/deepweave"):
        items = [(str(af.path), af.meta.get("agentType") or "?") for af in agent_files(proj)]
        items += [(p, None) for p in sorted(glob.glob(f"{proj}/*.jsonl"))]
        for p, atype in items:
            if pathlib.Path(p).stat().st_mtime < since:
                continue
            g = group(atype)
            pending = {}
            i_read = i_wrote = 0
            i_files = set()
            for line in open(p, encoding="utf-8", errors="replace"):
                if '"timestamp"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(e, dict) or "timestamp" not in e:
                    continue
                c = (e.get("message") or {}).get("content")
                if not isinstance(c, list):
                    continue
                if ts(e["timestamp"]) < since:
                    continue
                if e.get("type") == "assistant":
                    for b in c:
                        if not isinstance(b, dict) or b.get("type") != "tool_use":
                            continue
                        name, inp = b.get("name"), b.get("input") or {}
                        if name == "Read":
                            fp = inp.get("file_path", "")
                            pending[b.get("id")] = [(rel(fp), fp)]
                        elif name == "Bash":
                            paths = list(dict.fromkeys(m.group(0) for m in PATH_RE.finditer(inp.get("command", ""))))
                            pending[b.get("id")] = [(rel(x), x) for x in paths[:6]]
                        elif name in ("Edit", "Write"):
                            fp = inp.get("file_path", "")
                            n = len(inp.get("new_string") or inp.get("content") or "")
                            wrote[g][kind_of(rel(fp), fp)] += n
                            i_wrote += n
                elif e.get("type") == "user":
                    for b in c:
                        if not isinstance(b, dict) or b.get("type") != "tool_result":
                            continue
                        files = pending.pop(b.get("tool_use_id"), None)
                        if files is None:
                            continue
                        n = text_len(b.get("content"))
                        i_read += n
                        if not files:
                            unattributed[g] += n
                            continue
                        share = n / len(files)
                        for r, raw in files:
                            k = kind_of(r, raw)
                            read_kind[g][k] += share
                            read_file[g][r] += share
                            file_agents[r].add(p)
                            file_calls[r] += 1
                            if k == "prod":
                                i_files.add(r)
            if i_read or i_wrote:
                inst[g].append((i_read, len(i_files), i_wrote))
    print(f"last {a.days:g} days; chars/4 ≈ tokens")
    print(f"\n{'group':20} {'n':>5} {'read M':>8} {'no-file M':>9} {'wrote M':>8} {'read:write':>10} "
          f"{'med read k':>10} {'med prod files':>14} {'med wrote k':>11}")
    for g in sorted(inst, key=lambda x: -sum(r for r, _, _ in inst[x])):
        rows = inst[g]
        rd = sum(r for r, _, _ in rows)
        wr = sum(w for _, _, w in rows)
        med = lambda xs: sorted(xs)[len(xs) // 2]
        print(f"{g:20} {len(rows):5} {rd / 1e6:8.1f} {unattributed[g] / 1e6:9.1f} {wr / 1e6:8.2f} {rd / (wr or 1):10.0f} "
              f"{med([r for r, _, _ in rows]) / 1e3:10.0f} {med([f for _, f, _ in rows]):14} {med([w for _, _, w in rows]) / 1e3:11.1f}")
    print("\nread chars by artifact kind (share within group):")
    for g in sorted(read_kind, key=lambda x: -sum(read_kind[x].values())):
        tot = sum(read_kind[g].values())
        print(f"  {g:20} " + "  ".join(f"{k} {v / tot * 100:.0f}%" for k, v in read_kind[g].most_common(9)))
    print("\nwritten chars by artifact kind (share within group):")
    for g in sorted(wrote, key=lambda x: -sum(wrote[x].values())):
        tot = sum(wrote[g].values()) or 1
        print(f"  {g:20} {tot / 1e6:6.2f} M  " + "  ".join(f"{k} {v / tot * 100:.0f}%" for k, v in wrote[g].most_common(8)))
    allf = collections.Counter()
    for g in read_file:
        if g != "main":
            for f, v in read_file[g].items():
                allf[f] += v
    print("\ntop 40 files by chars read into subagent contexts (M chars, agent instances, calls):")
    for f, v in allf.most_common(40):
        print(f"  {v / 1e6:6.2f} {len(file_agents[f]):5} {file_calls[f]:6}  {f}")
    prod = [(f, v) for f, v in allf.items() if kind_of(f) == "prod"]
    prod.sort(key=lambda kv: -kv[1])
    tot_prod = sum(v for _, v in prod) or 1
    cum = 0
    for i, (_, v) in enumerate(prod, 1):
        cum += v
        if cum >= tot_prod * .5:
            print(f"\nprod files read by subagents: {len(prod)}; top {i} files carry 50% of prod chars read "
                  f"({tot_prod / 1e6:.1f} M total)")
            break
    print("top 25 prod files by chars read into subagent contexts:")
    for f, v in prod[:25]:
        print(f"  {v / 1e6:6.2f} {len(file_agents[f]):5} {file_calls[f]:6}  {f}")


if __name__ == "__main__":
    main()
