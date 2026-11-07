#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Where tokens and minutes leak between the work: tool calls per turn, idle gaps, cold cache rewrites,
repeated reads — main sessions and subagents, last N days, main checkout + worktrees.

  python3 turns.py --days 14                      # one row per group (main / each agent type)
  python3 turns.py --days 14 --top 12             # longer lists of stalls and cold rewrites
  python3 turns.py --agents <id1>,<id2>           # one row per agent run (A/B of a dispatch rule)
  python3 turns.py --session <sid>                # one main session and its agents

Columns: API messages, $ (API-price equivalent; 1h writes at 2×), context median／p90, tool calls per
message, share of messages with exactly one call, "batch%" = single read-only calls (Read／Grep／Glob／
LSP) that directly follow another single read-only call (an upper bound on turns a batch would remove;
a Bash `sed -n` read does not count), repeated Reads of the same file+range inside one cache epoch (no
compaction between), model-side stalls (tool_result → next message ≥10 min: nothing ran, nobody was
asked), tool-side stalls (tool_use → tool_result ≥10 min: AskUserQuestion open, hung command), and
cold rewrites (a message whose cache write is ≥50k tokens and ≥50% of its context) by cause: owner
wait ≥ TTL, a tool call ≥ TTL, a model stall ≥ TTL, first message (startup), other (compact／clear／
edit). TTL is read per message: the main session writes 1h caches, every subagent 5m (`5m%`).

Reading it: cost ≈ turns × context, so the lever is fewer messages or a smaller context, not tool
latency (Read 0.07 s, hooks ≤1 s). A tool_use line's timestamp is when the model started streaming
that block, so an Edit's "tool time" is mostly generation of old／new strings — wallclock.py's Edit／
Write buckets read the same way.
"""
import argparse
import collections
import datetime
import glob
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
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
        self.tools = collections.Counter()
        self.calls = 0
        self.batchable = 0
        self.dup_reads = 0
        self.dup_chars = 0
        self.model_stalls = []
        self.tool_stalls = []
        self.cold = collections.Counter()
        self.cold_n = collections.Counter()
        self.cold_rows = []
        self.runs = 0
        self.ttl = collections.Counter()
        self.owner_waits = []
        self.reseen = 0


def scan(path, label, g, since):
    """Walk one transcript in file order."""
    g.runs += 1
    prev_user_ts = None
    prev_user_kind = None
    pending = {}
    cur = None
    prev_ctx = None
    prev_single_ro = False
    seen_reads = set()
    first_msg = True
    last_stall_before = 0.0
    dup_pending = set()
    prev_asst_end = None
    last_wait_kind = None
    last_tool_wait = 0.0
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
        if prev_ctx and prev_ctx > 80_000 and ctx < 0.5 * prev_ctx:
            seen_reads = set()
        prev_ctx = ctx
        first_msg = False
        last_stall_before = 0.0
        cur = None

    # WHY: one API message is written as several assistant lines sharing message.id, and a tool call
    # runs while the next block is still streaming, so its lines can sit on both sides of a tool_result
    # and the id can reappear after a rewrite; pass 1 merges usage and tool names per id like
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
                    tools_by_id[mid].extend(b.get("name") or "?" for b in m["content"] if b.get("type") == "tool_use")
    for e in events:
        t = ts(e["timestamp"])
        typ = e.get("type")
        m = e.get("message") or {}
        content = m.get("content")
        if typ == "assistant":
            mid = e["_mid"]
            tokens = tuple(tok_by_id[mid])
            if mid in seen_ids and (cur is None or cur["id"] != mid):
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
    ap.add_argument("--agents", help="comma-separated agent ids: one row per run, window ignored")
    ap.add_argument("--session", help="one main session id: that session and its agents")
    a = ap.parse_args()
    since = 0 if a.agents else time.time() - a.days * 86400
    wanted = set(a.agents.split(",")) if a.agents else None
    groups = collections.defaultdict(Group)
    for proj in project_dirs(a.project_dir, all_worktrees=a.project_dir is None):
        if wanted is None:
            for p in sorted(glob.glob(f"{proj}/{a.session or '*'}*.jsonl")):
                if pathlib.Path(p).stat().st_mtime < since:
                    continue
                scan(p, "main " + pathlib.Path(p).stem[:8], groups["main"], since)
        for af in agent_files(proj, a.session):
            if wanted is not None:
                if af.agent_id not in wanted:
                    continue
                scan(af.path, af.label[:40], groups[f"{af.meta.get('agentType')} {af.agent_id[:8]} {af.label[:28]}"], 0)
                continue
            if af.path.stat().st_mtime < since:
                continue
            typ = (af.meta.get("agentType") or "?") + (" [wf]" if af.is_workflow else "")
            scan(af.path, f"{typ} {af.session[:8]} {af.label[:40]}", groups[typ], since)

    print("per agent run" if wanted else f"last {a.days:g} days")
    print(f"{'group':44} {'runs':>4} {'msgs':>6} {'$':>7} {'ctx med':>8} {'p90':>6} {'call/msg':>8} {'1-call%':>7} "
          f"{'batch%':>6} {'dupRd':>5} {'mStall':>6} {'h':>5} {'tStall':>6} {'h':>5} "
          f"{'cold$':>6} {'owner$':>6} {'tool$':>5} {'stall$':>6} {'other$':>6} {'5m%':>4}")
    for name, g in sorted(groups.items(), key=lambda kv: -kv[1].cost):
        if g.msgs == 0:
            continue
        ms_h = sum(s for s, _ in g.model_stalls) / 3600
        ts_h = sum(s for s, _, _ in g.tool_stalls) / 3600
        print(f"{name[:44]:44} {g.runs:4} {g.msgs:6} {g.cost:7.0f} {med(g.ctx)/1000:7.0f}k {med(g.ctx, .9)/1000:5.0f}k "
              f"{g.calls/g.msgs:8.2f} {g.tools['1']/g.msgs*100:6.0f}% {g.batchable/g.msgs*100:5.0f}% "
              f"{g.dup_reads:5} {len(g.model_stalls):6} {ms_h:5.1f} {len(g.tool_stalls):6} {ts_h:5.1f} "
              f"{sum(g.cold.values()):6.0f} {g.cold['owner wait≥ttl']:6.0f} {g.cold['tool call≥ttl']:5.0f} "
              f"{g.cold['model stall≥ttl']:6.0f} {g.cold['other'] + g.cold['startup']:6.0f} {g.ttl['5m']/max(g.msgs,1)*100:3.0f}%")
    tot = sum(g.cost for g in groups.values())
    print(f"total ${tot:.0f}; unpriced msgs {sum(g.unpriced for g in groups.values())}")
    gm = groups.get("main")
    if gm and gm.msgs:
        print("\nmain $ by context bucket: " + "  ".join(f"{k} ${v:.0f} ({v/gm.cost*100:.0f}%)" for k, v in
              sorted(gm.cost_by_ctx.items(), key=lambda kv: -kv[1])))
        if gm.owner_waits:
            ow = sorted(gm.owner_waits)
            print(f"main owner waits (prompt after assistant): n={len(ow)} median {med(ow)/60:.1f} min  "
                  f"≥1h: {sum(1 for x in ow if x >= TTL)}  hours in ≥1h waits: {sum(x for x in ow if x >= TTL)/3600:.0f}")
    if wanted:
        return 0
    allm = sorted(((s, l) for g in groups.values() for s, l in g.model_stalls), reverse=True)[:a.top]
    print("\nlongest model-side stalls (tool_result → next message):")
    for s, l in allm:
        print(f"  {s/3600:6.1f} h  {l}")
    allt = sorted(((s, l, n) for g in groups.values() for s, l, n in g.tool_stalls), reverse=True)[:a.top]
    print("\nlongest tool-side stalls (tool_use → tool_result):")
    for s, l, n in allt:
        print(f"  {s/3600:6.1f} h  {n:28} {l}")
    allc = sorted(((w, c, x, l) for g in groups.values() for w, c, x, l in g.cold_rows), reverse=True)[:a.top]
    print("\nbiggest cold rewrites:")
    for w, c, x, l in allc:
        print(f"  ${w:5.2f}  {c:16} ctx {x/1000:4.0f}k  {l}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
