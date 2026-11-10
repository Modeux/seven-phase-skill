# -*- coding: utf-8 -*-
"""Lead time per task from transcripts: calendar span, machine-active hours, owner messages, owner-wait, WIP.

  python3 leadtime.py --days 35 [--min-hours 2] [--tz 8]      # run from the repo root (main checkout)

Attribution is taskcost.py's: a main-session record belongs to the slug most recently named in a tool call;
a subagent belongs whole to the slug it names most (else the slug current when it was spawned).
active h  = 5-minute bins holding at least one assistant API message (main or subagent) for that slug.
owner msg = human text turns in the main session (not tool results, not harness notices or command echoes).
wait      = gap between an owner message and the record just before it in that session, bucketed
            <10 min (reading／typing) · 10 min–2 h (away briefly) · >2 h (away／overnight).
WIP       = slugs with >= 30 active minutes on a local calendar day.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts"))
from _usage import agent_files, project_dirs  # noqa: E402
from taskcost import slugs_in, tool_texts  # noqa: E402

BIN = 300


def epoch(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def owner_text(r):
    if r.get("type") != "user" or r.get("isMeta") or r.get("isCompactSummary") or r.get("isSidechain"):
        return None
    c = (r.get("message") or {}).get("content")
    if isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return None
        c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(c, str):
        return None
    c = c.strip()
    if not c or c.startswith("<") or c.startswith("Caveat:") or c.startswith("[Request interrupted"):
        return None
    return c


def records(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and r.get("timestamp"):
                yield r


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=35)
    ap.add_argument("--min-hours", type=float, default=2.0)
    ap.add_argument("--tz", type=float, default=8.0)
    a = ap.parse_args()
    since = time.time() - a.days * 86400
    tz = a.tz * 3600

    bins = collections.defaultdict(set)        # slug -> {bin}
    main_bins = collections.defaultdict(set)
    span = {}                                  # slug -> [first, last]
    owner = collections.Counter()
    waits = collections.defaultdict(list)
    sessions = collections.defaultdict(set)
    all_waits = []

    def touch(slug, ts, is_main):
        b = int(ts // BIN)
        bins[slug].add(b)
        if is_main:
            main_bins[slug].add(b)
        s = span.setdefault(slug, [ts, ts])
        s[0], s[1] = min(s[0], ts), max(s[1], ts)

    for root in project_dirs(None, True):
        for jsonl in glob.glob(f"{root}/*.jsonl"):
            if os.path.getmtime(jsonl) < since:
                continue
            sid = os.path.basename(jsonl)[:-6]
            cur, spawn_at, prev_ts = "(none)", {}, None
            for r in records(jsonl):
                ts = epoch(r["timestamp"])
                if r.get("type") == "assistant":
                    for tid, text in tool_texts((r.get("message") or {}).get("content")):
                        for slug, _sub in slugs_in(text):
                            cur = slug
                        spawn_at[tid] = cur
                    if ts >= since:
                        touch(cur, ts, True)
                        sessions[cur].add(sid)
                    prev_ts = ts
                elif r.get("type") == "user":
                    if owner_text(r) is not None and ts >= since:
                        owner[cur] += 1
                        if prev_ts is not None:
                            waits[cur].append(ts - prev_ts)
                            all_waits.append(ts - prev_ts)
                    prev_ts = ts
            for af in agent_files(root, sid):
                votes, stamps = collections.Counter(), []
                for r in records(str(af.path)):
                    m = r.get("message") or {}
                    if r.get("type") == "user" and isinstance(m.get("content"), str):
                        votes.update(s for s, _ in slugs_in(m["content"]))
                    elif r.get("type") == "assistant":
                        for _, text in tool_texts(m.get("content")):
                            votes.update(s for s, _ in slugs_in(text))
                        stamps.append(epoch(r["timestamp"]))
                slug = votes.most_common(1)[0][0] if votes else spawn_at.get(af.meta.get("toolUseId"), "(none)")
                for ts in stamps:
                    if ts >= since:
                        touch(slug, ts, False)

    def h(n_bins):
        return n_bins * BIN / 3600

    def day(ts):
        return time.strftime("%m-%d", time.gmtime(ts + tz))

    rows = []
    for slug, bs in bins.items():
        if slug == "(none)" or h(len(bs)) < a.min_hours:
            continue
        w = waits[slug]
        rows.append((span[slug][0], slug, (span[slug][1] - span[slug][0]) / 86400, h(len(bs)), h(len(main_bins[slug])),
                     owner[slug], sum(x for x in w if x < 600) / 3600, sum(x for x in w if 600 <= x < 7200) / 3600,
                     sum(x for x in w if x >= 7200) / 3600, len(sessions[slug]),
                     len({day(b * BIN) for b in bs})))
    rows.sort()
    print(f"{'slug':34}{'first':>6}{'last':>6}{'span d':>7}{'days':>5}{'act h':>7}{'main h':>7}{'owner':>6}"
          f"{'w<10m':>7}{'w<2h':>6}{'w>2h':>7}{'sess':>5}")
    for first, slug, sp, act, mh, om, w1, w2, w3, ns, nd in rows:
        print(f"{slug[:34]:34}{day(first):>6}{day(span[slug][1]):>6}{sp:>7.1f}{nd:>5}{act:>7.1f}{mh:>7.1f}{om:>6}"
              f"{w1:>7.1f}{w2:>6.1f}{w3:>7.1f}{ns:>5}")
    if rows:
        med = statistics.median
        print(f"\ntasks >= {a.min_hours:g} active h: {len(rows)}  median span {med(r[2] for r in rows):.1f} d  "
              f"median active {med(r[3] for r in rows):.1f} h  median active-days {med(r[10] for r in rows):.0f}  "
              f"median owner msgs {med(r[5] for r in rows):.0f}")
        tot_act = sum(r[3] for r in rows)
        tot_span = sum(r[2] for r in rows) * 24
        print(f"sum active {tot_act:.0f} h over sum span {tot_span:.0f} h -> flow efficiency {100 * tot_act / tot_span:.0f}%")

    # WIP and concurrency
    per_day = collections.defaultdict(collections.Counter)
    by_bin = collections.defaultdict(set)
    for slug, bs in bins.items():
        if slug == "(none)":
            continue
        for b in bs:
            per_day[day(b * BIN)][slug] += 1
            by_bin[b].add(slug)
    wip = {d: sum(1 for n in c.values() if n * BIN >= 1800) for d, c in per_day.items()}
    if wip:
        vals = sorted(wip.values())
        print(f"\nWIP per day (slugs with >=30 active min): median {statistics.median(vals):.0f}  "
              f"p90 {vals[int(0.9 * (len(vals) - 1))]}  max {vals[-1]}  days {len(vals)}")
        print("  " + "  ".join(f"{d}:{wip[d]}" for d in sorted(wip)))
    conc = collections.Counter(min(len(s), 4) for s in by_bin.values())
    tot = sum(conc.values()) or 1
    print("active 5-min bins by number of tasks running: " +
          "  ".join(f"{k if k < 4 else '4+'}: {100 * v / tot:.0f}%" for k, v in sorted(conc.items())) +
          f"  (machine-active {tot * BIN / 3600:.0f} h in {a.days:g} d = {tot * BIN / 3600 / a.days:.1f} h/day)")
    if all_waits:
        aw = sorted(all_waits)
        print(f"owner gaps: n {len(aw)}  median {statistics.median(aw) / 60:.1f} min  "
              f"p75 {aw[int(.75 * (len(aw) - 1))] / 60:.1f}  p90 {aw[int(.9 * (len(aw) - 1))] / 60:.1f} min  "
              f"sum<10m {sum(x for x in aw if x < 600) / 3600:.0f} h  sum 10m-2h {sum(x for x in aw if 600 <= x < 7200) / 3600:.0f} h  "
              f"sum>2h {sum(x for x in aw if x >= 7200) / 3600:.0f} h")


if __name__ == "__main__":
    main()
