#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What fills the main session's context: tool_result characters by tool, the Bash commands whose output
is largest, Agent hand-back sizes, and the user's own prompt sizes — last N days, main sessions only.
  python3 mainio.py --days 14
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
from _usage import project_dirs  # noqa: E402


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def text_of(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join((x.get("text") or "") if isinstance(x, dict) else str(x) for x in c)
    return ""


def cmd_key(cmd):
    cmd = cmd.strip()
    cmd = re.sub(r"^(cd \S+ && |S=\S+; |export \S+; )+", "", cmd)
    first = cmd.split("\n")[0]
    m = re.match(r"(python3 \S+?([^/\s]+\.py)|just \S+|go test|npx ng \w+|git \w+|grep|rg|ls|cat|sed|find|curl|docker \w+|pnpm \S+|\S+)", first)
    return (m.group(2) if m and m.group(2) else (m.group(1) if m else first))[:40]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--agent", help="scan this subagent type's transcripts instead of the main sessions")
    a = ap.parse_args()
    since = time.time() - a.days * 86400
    from _usage import agent_files
    by_tool = collections.Counter()
    n_tool = collections.Counter()
    by_cmd = collections.Counter()
    n_cmd = collections.Counter()
    agent_sizes = []
    prompt_chars = 0
    n_prompts = 0
    asst_text = 0
    for proj in project_dirs(None, all_worktrees=True):
        if a.agent:
            paths = [str(af.path) for af in agent_files(proj) if af.meta.get("agentType") == a.agent]
        else:
            paths = sorted(glob.glob(f"{proj}/*.jsonl"))
        for p in paths:
            if pathlib.Path(p).stat().st_mtime < since:
                continue
            pending = {}
            for line in open(p, encoding="utf-8", errors="replace"):
                if '"timestamp"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(e, dict) or "timestamp" not in e:
                    continue
                t = ts(e["timestamp"])
                c = (e.get("message") or {}).get("content")
                if e.get("type") == "assistant" and isinstance(c, list):
                    for b in c:
                        if b.get("type") == "tool_use":
                            inp = b.get("input") or {}
                            pending[b.get("id")] = (b.get("name"), cmd_key(inp.get("command", "")) if b.get("name") == "Bash" else "")
                        elif b.get("type") == "text" and t >= since:
                            asst_text += len(b.get("text") or "")
                elif e.get("type") == "user":
                    if t < since:
                        continue
                    if isinstance(c, str):
                        prompt_chars += len(c)
                        n_prompts += 1
                    elif isinstance(c, list):
                        for b in c:
                            if b.get("type") == "tool_result":
                                name, key = pending.pop(b.get("tool_use_id"), ("?", ""))
                                n = len(text_of(b.get("content")))
                                by_tool[name] += n
                                n_tool[name] += 1
                                if name == "Bash":
                                    by_cmd[key] += n
                                    n_cmd[key] += 1
                                elif name == "Agent":
                                    agent_sizes.append(n)
                            elif b.get("type") == "text":
                                prompt_chars += len(b.get("text") or "")
                                n_prompts += 1
    tot = sum(by_tool.values())
    print(f"main sessions, last {a.days:g} days: tool_result {tot/1e6:.1f} M chars (≈{tot/4e6:.1f} M tokens); "
          f"assistant text {asst_text/1e6:.1f} M chars; prompts {n_prompts} / {prompt_chars/1e6:.2f} M chars")
    print(f"{'tool':22} {'calls':>7} {'M chars':>8} {'share':>6} {'avg chars':>9}")
    for name, n in by_tool.most_common(14):
        print(f"{name:22} {n_tool[name]:7} {n/1e6:8.2f} {n/tot*100:5.0f}% {n/n_tool[name]:9.0f}")
    print(f"\nBash output by command key (top 25):")
    for key, n in by_cmd.most_common(25):
        print(f"  {key:40} {n_cmd[key]:6} calls {n/1e6:6.2f} M chars  avg {n/n_cmd[key]:7.0f}")
    if agent_sizes:
        agent_sizes.sort()
        print(f"\nAgent hand-backs: n={len(agent_sizes)} median {agent_sizes[len(agent_sizes)//2]} chars  "
              f"p90 {agent_sizes[int(len(agent_sizes)*.9)]}  max {agent_sizes[-1]}  "
              f">3000 chars: {sum(1 for x in agent_sizes if x > 3000)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
