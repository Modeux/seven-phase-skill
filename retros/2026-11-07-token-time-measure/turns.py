#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Where tokens and minutes leak between the work: idle gaps, cold cache rewrites, tool calls per turn,
repeated reads — main sessions and subagents, last N days, main checkout + worktrees.

  python3 idle.py --days 14
  python3 idle.py --days 14 --top 12          # longest stalls and biggest cold rewrites, with labels

Per group (main / each agent type): API messages, $ (API-price equivalent, 1h writes at 2×), context
median／p90, tool calls per message, share of messages with exactly one call, "batchable" = single
read-only calls that directly follow another single read-only call (upper bound on turns a batch
would remove), repeated reads of the same file+range inside one cache epoch (no compaction between),
model-side stalls (tool_result → next assistant message ≥10 min: nothing ran, nobody was asked),
tool-side stalls (tool_use → tool_result ≥10 min: permission prompt, hung command), and cold rewrites
(a message whose cache write is ≥50k tokens and ≥50% of its context) split by cause: after a ≥60 min
stall (TTL expired), first message of the transcript (startup), other (compact／clear／edit).
"""
import argparse
import collections
import datetime
import glob
import json
import pathlib
import sys
import time

sys.path.insert(0, "/Users/huntwu/.claude/skills/seven-phase/scripts")
from _usage import PRICES, CACHE_1H_MULT, agent_files, project_dirs, split_tokens  # noqa: E402

READ_ONLY = {"Read", "Grep", "Glob", "LSP"}
STALL = 600
TTL = 3600
COLD_MIN = 50_000


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def price(model, tokens):
    p = PRICES.get(model or "")
    if p is None:
        return None, None
    inp, cw5m, cw1h, cr, out = tokens
    write = (cw5m * p[1] + cw1h * p[0] * CACHE_1H_MULT) / 1e6
    return (inp * p[0] + cr * p[2] + out * p[3]) / 1e6 + write, write


class Group:
    def __init__(self):
        self.msgs = 0
        self.cost = 0.0
        self.unpriced = 0
        self.ctx = []
        self.cost_by_ctx = collections.Counter()
        self.tools = collections.Counter()      # tool calls per message histogram key 0/1/2/3+
        self.calls = 0
        self.batchable = 0
        self.dup_reads = 0
        self.dup_chars = 0
        self.model_stalls = []                  # (secs, label)
        self.tool_stalls = []                   # (secs, label, tool)
        self.cold = collections.Counter()       # cause -> $
        self.cold_n = collections.Counter()
        self.cold_rows = []                     # ($, cause, ctx, label)
        self.runs = 0
        self.ttl = collections.Counter()
        self.owner_waits = []
        self.reseen = 0


def scan(path, label, g, since):
    """Walk one transcript in file order."""
    g.runs += 1
    prev_user_ts = None          # last tool_result / prompt timestamp
    prev_user_kind = None        # "result" | "prompt"
    pending = {}                 # tool_use id -> (ts, name)
    cur = None                   # current API message accumulator
    prev_ctx = None
    prev_single_ro = False
    seen_reads = set()
    first_msg = True
    last_stall_before = 0.0      # seconds of the stall right before the current message
    dup_pending = set()
    prev_asst_end = None         # last assistant line timestamp (owner wait = next prompt − this)
    last_wait_kind = None        # what the current message waited on: result | prompt
    last_tool_wait = 0.0         # longest tool_use→tool_result wait inside the previous turn
    max_tool_wait = 0.0
    seen_ids = set()

    def finish():
        nonlocal cur, prev_ctx, prev_single_ro, first_msg, seen_reads, last_stall_before
        if cur is None:
            return
        tokens = tuple(cur["tokens"])
        ctx = sum(tokens[:4])
        if cur["ts"] >= since:
            g.msgs += 1
            cost, write = price(cur["model"], tokens)
            if cost is None:
                g.unpriced += 1
                cost, write = 0.0, 0.0
            g.cost += cost
            g.ctx.append(ctx)
            b = "<100k" if ctx < 100_000 else "100-200k" if ctx < 200_000 else "200-400k" if ctx < 400_000 else ">=400k"
            g.cost_by_ctx[b] += cost
            n = len(cur["tools"])
            g.tools["3+" if n >= 3 else str(n)] += 1
            g.calls += n
            single_ro = n == 1 and cur["tools"][0] in READ_ONLY
            if single_ro and prev_single_ro:
                g.batchable += 1
            prev_single_ro = single_ro
            cw = tokens[1] + tokens[2]
            g.ttl["1h" if tokens[2] > tokens[1] else "5m"] += 1
            ttl = TTL if tokens[2] > tokens[1] else 300
            if cw >= COLD_MIN and ctx and cw >= 0.5 * ctx:
                cause = ("startup" if first_msg else
                         "owner wait≥ttl" if last_wait_kind == "prompt" and last_stall_before >= ttl else
                         "model stall≥ttl" if last_wait_kind == "result" and last_stall_before >= ttl else
                         "tool call≥ttl" if last_tool_wait >= ttl else "other")
                g.cold[cause] += write
                g.cold_n[cause] += 1
                g.cold_rows.append((write, cause, ctx, label))
        # epoch: compaction = context collapses
        if prev_ctx and prev_ctx > 80_000 and ctx < 0.5 * prev_ctx:
            seen_reads = set()
        prev_ctx = ctx
        first_msg = False
        last_stall_before = 0.0
        cur = None

    # WHY: one API message is written as several assistant lines sharing message.id, and the same id can
    # reappear after other lines (the harness rewrites it); pass 1 merges usage per id like
    # _usage.read_messages so pass 2 counts each message once, at its first line.
    tok_by_id = {}
    tools_by_id = collections.defaultdict(list)
    events = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh):
            if '"timestamp"' not in line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(e, dict) or "timestamp" not in e:
                continue
            events.append(e)
            if e.get("type") == "assistant":
                m = e.get("message") or {}
                mid = m.get("id") or ("no-id", n)
                e["_mid"] = mid
                tokens = split_tokens(m.get("usage"))
                old = tok_by_id.get(mid)
                tok_by_id[mid] = list(tokens) if old is None else [max(a, b) for a, b in zip(old, tokens)]
                if isinstance(m.get("content"), list):
                    # WHY: a tool call is executed while the next one is still streaming, so the blocks of one
                    # message land on separate lines with tool_results between them; the message's call
                    # count is the union over all its lines.
                    tools_by_id[mid].extend(b.get("name") or "?" for b in m["content"] if b.get("type") == "tool_use")
    if True:
        for e in events:
            t = ts(e["timestamp"])
            typ = e.get("type")
            m = e.get("message") or {}
            content = m.get("content")
            if typ == "assistant":
                mid = e["_mid"]
                tokens = tuple(tok_by_id[mid])
                if mid in seen_ids and (cur is None or cur["id"] != mid):
                    # a rewritten line of an already-counted message: only its tool_use blocks matter
                    g.reseen += 1
                    if isinstance(content, list):
                        for b in content:
                            if b.get("type") == "tool_use":
                                pending[b.get("id")] = (t, b.get("name") or "?")
                    prev_asst_end = t
                    continue
                if cur is None or cur["id"] != mid:
                    finish()
                    seen_ids.add(mid)
                    stall = 0.0
                    if prev_user_ts is not None and prev_user_kind == "result":
                        stall = t - prev_user_ts
                        if stall >= STALL and t >= since:
                            g.model_stalls.append((stall, label))
                    elif prev_user_ts is not None and prev_user_kind == "prompt" and prev_asst_end is not None:
                        stall = prev_user_ts - prev_asst_end
                        if t >= since:
                            g.owner_waits.append(stall)
                    cur = {"id": mid, "ts": t, "model": m.get("model"), "tokens": list(tokens),
                           "tools": list(tools_by_id.get(mid, []))}
                    last_stall_before = stall
                    last_wait_kind = prev_user_kind
                    last_tool_wait = max_tool_wait
                    max_tool_wait = 0.0
                else:
                    cur["model"] = cur["model"] or m.get("model")
                prev_asst_end = t
                if isinstance(content, list):
                    for b in content:
                        if b.get("type") == "tool_use":
                            name = b.get("name") or "?"
                            pending[b.get("id")] = (t, name)
                            if name == "Read":
                                inp = b.get("input") or {}
                                key = (inp.get("file_path"), inp.get("offset"), inp.get("limit"))
                                if key in seen_reads:
                                    dup_pending.add(b.get("id"))
                                    if t >= since:
                                        g.dup_reads += 1
                                seen_reads.add(key)
            elif typ == "user":
                finish()
                kind = "prompt"
                if isinstance(content, list):
                    for b in content:
                        if b.get("type") == "tool_result":
                            kind = "result"
                            st = pending.pop(b.get("tool_use_id"), None)
                            if st is not None:
                                d = t - st[0]
                                max_tool_wait = max(max_tool_wait, d)
                                if d >= STALL and t >= since:
                                    g.tool_stalls.append((d, label, st[1]))
                                if st[1] == "Read" and b.get("tool_use_id") in dup_pending:
                                    c = b.get("content")
                                    n = len(c) if isinstance(c, str) else sum(len(x.get("text") or "") for x in c if isinstance(x, dict))
                                    if t >= since:
                                        g.dup_chars += n
                                dup_pending.discard(b.get("tool_use_id"))
                prev_user_ts, prev_user_kind = t, kind
        finish()


def med(xs, q=0.5):
    xs = sorted(xs)
    return xs[min(int(len(xs) * q), len(xs) - 1)] if xs else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--project-dir")
    a = ap.parse_args()
    since = time.time() - a.days * 86400
    groups = collections.defaultdict(Group)
    for proj in project_dirs(a.project_dir, all_worktrees=a.project_dir is None):
        for p in sorted(glob.glob(f"{proj}/*.jsonl")):
            if pathlib.Path(p).stat().st_mtime < since:
                continue
            scan(p, "main " + pathlib.Path(p).stem[:8], groups["main"], since)
        for af in agent_files(proj):
            if af.path.stat().st_mtime < since:
                continue
            typ = (af.meta.get("agentType") or "?") + (" [wf]" if af.is_workflow else "")
            scan(af.path, f"{typ} {af.session[:8]} {af.label[:40]}", groups[typ], since)

    print(f"last {a.days:g} days")
    print(f"{'group':28} {'runs':>5} {'msgs':>6} {'$':>7} {'ctx med':>8} {'p90':>6} {'call/msg':>8} {'1-call%':>7} "
          f"{'batch%':>6} {'dupRead':>7} {'dupMch':>6} {'mStall':>6} {'h':>6} {'≥1h':>4} {'tStall':>6} {'h':>6} "
          f"{'cold$':>7} {'owner$':>6} {'tool$':>6} {'stall$':>6} {'start$':>6} {'other$':>6} {'5m%':>4} {'reseen':>6}")
    for name, g in sorted(groups.items(), key=lambda kv: -kv[1].cost):
        if g.msgs == 0:
            continue
        ms_h = sum(s for s, _ in g.model_stalls) / 3600
        ts_h = sum(s for s, _, _ in g.tool_stalls) / 3600
        print(f"{name[:28]:28} {g.runs:5} {g.msgs:6} {g.cost:7.0f} {med(g.ctx)/1000:7.0f}k {med(g.ctx, .9)/1000:5.0f}k "
              f"{g.calls/g.msgs:8.2f} {g.tools['1']/g.msgs*100:6.0f}% {g.batchable/g.msgs*100:5.0f}% "
              f"{g.dup_reads:7} {g.dup_chars/1e6:6.1f} {len(g.model_stalls):6} {ms_h:6.1f} "
              f"{sum(1 for s, _ in g.model_stalls if s >= TTL):4} {len(g.tool_stalls):6} {ts_h:6.1f} "
              f"{sum(g.cold.values()):7.0f} {g.cold['owner wait≥ttl']:6.0f} {g.cold['tool call≥ttl']:6.0f} "
              f"{g.cold['model stall≥ttl']:6.0f} {g.cold['startup']:6.0f} {g.cold['other']:6.0f} "
              f"{g.ttl['5m']/max(g.msgs,1)*100:3.0f}% {g.reseen:6}")
    tot = sum(g.cost for g in groups.values())
    print(f"total ${tot:.0f}; unpriced msgs {sum(g.unpriced for g in groups.values())}")
    gm = groups.get("main")
    if gm:
        print("\nmain $ by context bucket: " + "  ".join(f"{k} ${v:.0f} ({v/gm.cost*100:.0f}%)" for k, v in
              sorted(gm.cost_by_ctx.items(), key=lambda kv: -kv[1])))
    if gm and gm.owner_waits:
        ow = sorted(gm.owner_waits)
        print(f"main owner waits (prompt after assistant): n={len(ow)} median {med(ow)/60:.1f} min  "
              f"≥1h: {sum(1 for x in ow if x >= TTL)}  hours in ≥1h waits: {sum(x for x in ow if x >= TTL)/3600:.0f}")
    allm = sorted(((s, l) for g in groups.values() for s, l in g.model_stalls), reverse=True)[:a.top]
    print(f"\nlongest model-side stalls (tool_result → next message):")
    for s, l in allm:
        print(f"  {s/3600:6.1f} h  {l}")
    allt = sorted(((s, l, n) for g in groups.values() for s, l, n in g.tool_stalls), reverse=True)[:a.top]
    print(f"\nlongest tool-side stalls (tool_use → tool_result):")
    for s, l, n in allt:
        print(f"  {s/3600:6.1f} h  {n:28} {l}")
    allc = sorted(((w, c, x, l) for g in groups.values() for w, c, x, l in g.cold_rows), reverse=True)[:a.top]
    print(f"\nbiggest cold rewrites:")
    for w, c, x, l in allc:
        print(f"  ${w:5.2f}  {c:16} ctx {x/1000:4.0f}k  {l}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
