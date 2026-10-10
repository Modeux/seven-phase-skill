# -*- coding: utf-8 -*-
"""Shared transcript reading and pricing for agentcost／taskcost／laneyield／claudemdcost.

Usage (library):
  from _usage import project_dirs, agent_files, read_messages, cost_of, quality_line
  for d in project_dirs(all_worktrees=True):
      for a in agent_files(d):
          q = {}
          dollars = sum(cost_of(m, q) for m in read_messages(a.path, since=time.time() - 86400, quality=q))

Transcript layout: `<proj>/<sid>.jsonl` (main), `<proj>/<sid>/subagents/agent-<id>.jsonl` (ordinary
subagents) and `<proj>/<sid>/subagents/workflows/<wf_id>/agent-<id>.jsonl` (Workflow agents), each
with an `agent-<id>.meta.json` beside it.
"""
import collections
import glob
import json
import os
import pathlib
import re
import subprocess
import time

# WHY: per-MTok (input, cache write 5m, cache read, output) from the claude-api skill model table cached
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
# WHY: platform prompt-caching docs (checked 2026-10-10): a 5-minute cache write bills at 1.25× base
# input (the table's write column already equals 1.25 × input) and a 1-hour write at 2×. Main sessions
# write 1h (settings promptCacheTtl "1h"), Workflow agents 5m, so pricing every write at 5m undercounts
# the main session.
CACHE_1H_MULT = 2

PROJECTS = pathlib.Path.home() / ".claude" / "projects"

Message = collections.namedtuple("Message", "id model effort timestamp tokens")
AgentFile = collections.namedtuple("AgentFile", "path agent_id session meta is_workflow label")


def split_tokens(usage):
    """(input, cache write 5m, cache write 1h, cache read, output) from one `usage` dict."""
    u = usage or {}
    cw = u.get("cache_creation_input_tokens") or 0
    sub = u.get("cache_creation")
    cw1h = (sub.get("ephemeral_1h_input_tokens") or 0) if isinstance(sub, dict) else 0
    cw5m = (sub.get("ephemeral_5m_input_tokens") or 0) if isinstance(sub, dict) else 0
    # WHY: without the sub-object (older transcripts) all writes are 5m; any remainder the sub-object
    # does not explain is also billed as 5m rather than dropped.
    cw5m += max(cw - cw5m - cw1h, 0)
    return (u.get("input_tokens") or 0, cw5m, cw1h, u.get("cache_read_input_tokens") or 0, u.get("output_tokens") or 0)


def dollars(model, tokens):
    """API-price dollars for split tokens, or None when the model is not in PRICES."""
    p = PRICES.get(model or "")
    if p is None:
        return None
    inp, cw5m, cw1h, cr, out = tokens
    return (inp * p[0] + cw5m * p[1] + cw1h * p[0] * CACHE_1H_MULT + cr * p[2] + out * p[3]) / 1e6


def cost_of(msg, quality=None):
    """Dollars for a Message; an unpriced message with tokens adds to quality['unpriced'] and counts $0."""
    d = dollars(msg.model, msg.tokens)
    if d is None:
        if quality is not None and any(msg.tokens):
            quality["unpriced"] = quality.get("unpriced", 0) + 1
        return 0.0
    return d


def context_of(msg):
    return sum(msg.tokens[:4])


def quality_line(quality):
    """One line of data-quality counters, printed at the bottom of each report."""
    keys = ("unpriced", "no_usage", "no_message_id", "bad_json", "no_timestamp")
    return "data quality: " + "  ".join(f"{k} {quality.get(k, 0)}" for k in keys)


def encode_project_dir(path):
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def main_checkout(cwd=None):
    """The main checkout of the repo containing cwd (a worktree resolves to the repo it hangs off)."""
    try:
        out = subprocess.run(["git", "-C", str(cwd or os.getcwd()), "rev-parse", "--path-format=absolute",
                              "--git-common-dir"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return str(cwd or os.getcwd())
    return str(pathlib.Path(out).parent)


def project_dirs(project_dir=None, all_worktrees=False, cwd=None, checkout=None, projects_root=None):
    """Transcript dirs to scan: the explicit one, else cwd's, else (all_worktrees) the main checkout's
    plus its `.claude/worktrees/*` and `tmp/*` worktrees."""
    if project_dir:
        return [pathlib.Path(os.path.expanduser(str(project_dir)))]
    root = pathlib.Path(projects_root) if projects_root else PROJECTS
    if not all_worktrees:
        return [root / encode_project_dir(cwd or os.getcwd())]
    main = encode_project_dir(checkout or main_checkout(cwd))
    # WHY: a plain `<main>*` glob also picks up unrelated sibling checkouts such as `<main>-image`.
    found = [root / main] + sorted(pathlib.Path(p) for p in glob.glob(f"{root}/{main}--claude-worktrees-*")) \
        + sorted(pathlib.Path(p) for p in glob.glob(f"{root}/{main}-tmp-*"))
    return [d for d in found if d.is_dir()]


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch))


def read_messages(path, since=None, until=None, quality=None, on_line=None, all_lines=False):
    """Assistant API messages of one transcript file, in first-seen order, inside [since, until).

    One API message is written as several assistant lines sharing `message.id` whose usage can grow, so
    each token field is the max across its lines; model and effort are the first non-empty, timestamp
    the first line's. since/until are epoch seconds compared against record timestamps; file mtime only
    skips files untouched since `since`. on_line(record, message_or_None, first_line_of_message) is
    called for every parsed line, in or out of the window, so callers can follow file order; only
    lines mentioning "assistant" are parsed unless all_lines (user prompts are needed).
    """
    q = quality if quality is not None else {}
    if since is not None and os.path.getmtime(path) < since:
        return []
    since_iso = _iso(since) if since is not None else None
    until_iso = _iso(until) if until is not None else None
    merged = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh):
            if not all_lines and '"assistant"' not in line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                q["bad_json"] = q.get("bad_json", 0) + 1
                continue
            if not isinstance(r, dict) or r.get("type") != "assistant":
                if on_line is not None and isinstance(r, dict):
                    on_line(r, None, False)
                continue
            m = r.get("message") or {}
            mid = m.get("id")
            if not mid:
                q["no_message_id"] = q.get("no_message_id", 0) + 1
                mid = ("no-id", n)
            usage = m.get("usage")
            if not isinstance(usage, dict):
                q["no_usage"] = q.get("no_usage", 0) + 1
            tokens = split_tokens(usage)
            cur = merged.get(mid)
            first = cur is None
            if first:
                cur = merged[mid] = {"model": m.get("model"), "effort": r.get("effort"),
                                     "timestamp": r.get("timestamp"), "tokens": list(tokens)}
            else:
                cur["tokens"] = [max(a, b) for a, b in zip(cur["tokens"], tokens)]
                cur["model"] = cur["model"] or m.get("model")
                cur["effort"] = cur["effort"] or r.get("effort")
                cur["timestamp"] = cur["timestamp"] or r.get("timestamp")
            if on_line is not None:
                on_line(r, _message(mid, cur), first)
    out = []
    for mid, cur in merged.items():
        ts = cur["timestamp"]
        if (since_iso or until_iso) and not ts:
            q["no_timestamp"] = q.get("no_timestamp", 0) + 1
            continue
        if since_iso and ts < since_iso or until_iso and ts >= until_iso:
            continue
        out.append(_message(mid, cur))
    return out


def _message(mid, cur):
    return Message(mid if isinstance(mid, str) else None, cur["model"], cur["effort"], cur["timestamp"],
                   tuple(cur["tokens"]))


def agent_files(project_dir, session=None):
    """Every subagent transcript under one project dir (or one session), ordinary and Workflow."""
    found = []
    for p in sorted(glob.glob(f"{project_dir}/{glob.escape(session) if session else '*'}/subagents/**/agent-*.jsonl",
                              recursive=True)):
        path = pathlib.Path(p)
        parts = path.parts
        i = len(parts) - 1 - parts[::-1].index("subagents")
        rel = parts[i + 1:-1]
        try:
            meta = json.loads(path.with_name(path.stem + ".meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        found.append(AgentFile(path, path.stem.removeprefix("agent-"), parts[i - 1], meta,
                               bool(rel) and rel[0] == "workflows", meta.get("description") or ""))
    return found
