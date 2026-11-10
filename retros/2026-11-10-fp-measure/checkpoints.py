# -*- coding: utf-8 -*-
"""What the owner says after each phase checkpoint, and when the machines run.

  python3 checkpoints.py --days 35 [--samples 10]         # run from the repo root

checkpoint = a main-session assistant text holding `/compact <slug> 第 N 階段已完成` (assets/checkpoint.md).
reply      = the next owner text in that session before another checkpoint; `go` when it is <= 24 chars or
             opens with 繼續／继续／下一階段／好／ok／commit／可以／進, else `steer` (owner adds direction or a question).
cleared    = a session whose first owner text is `繼續 <slug> 第 N 階段…` (the /clear handoff line) — a go for N-1.
Hour histogram = 5-minute bins with any main-session assistant message, by local hour.
"""
import argparse
import collections
import glob
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts"))
sys.path.insert(0, HERE)
from _usage import project_dirs  # noqa: E402
from leadtime import epoch, owner_text, records  # noqa: E402

CP = re.compile(r"/compact\s+(\S+)\s*第\s*([1-7])\s*階段")
HANDOFF = re.compile(r"^繼續\s+(\S+)\s*第\s*([1-7])\s*階段")
GO = re.compile(r"^(繼續|继续|下一階段|下一阶段|好|ok|OK|Ok|commit|可以|進|go|Go|yes|是|對)")


def assistant_text(r):
    c = (r.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=35)
    ap.add_argument("--samples", type=int, default=10)
    ap.add_argument("--tz", type=float, default=8.0)
    a = ap.parse_args()
    since = time.time() - a.days * 86400
    stat = collections.defaultdict(lambda: collections.Counter())
    gaps = collections.defaultdict(list)
    lens = collections.defaultdict(list)
    samples = collections.defaultdict(list)
    hours = collections.Counter()
    seen_bins = set()
    cps, hands = [], []
    for root in project_dirs(None, True):
        for jsonl in glob.glob(f"{root}/*.jsonl"):
            if os.path.getmtime(jsonl) < since:
                continue
            pending, first_owner = None, True
            for r in records(jsonl):
                ts = epoch(r["timestamp"])
                if r.get("type") == "assistant":
                    if ts >= since:
                        b = int(ts // 300)
                        if b not in seen_bins:
                            seen_bins.add(b)
                            hours[int(((ts + a.tz * 3600) % 86400) // 3600)] += 1
                    m = CP.search(assistant_text(r))
                    if m and ts >= since:
                        if pending:
                            stat[pending[0]]["no_reply"] += 1
                        pending = (int(m.group(2)), ts)
                        cps.append((m.group(1), int(m.group(2)), ts))
                        stat[pending[0]]["checkpoints"] += 1
                elif r.get("type") == "user":
                    t = owner_text(r)
                    if t is None:
                        continue
                    if first_owner:
                        first_owner = False
                        h = HANDOFF.match(t)
                        if h and ts >= since:
                            stat[int(h.group(2)) - 1]["cleared_go"] += 1
                            hands.append((h.group(1), int(h.group(2)), ts))
                    if pending and ts >= since:
                        n, t0 = pending
                        kind = "go" if len(t) <= 24 or GO.match(t) else "steer"
                        stat[n][kind] += 1
                        gaps[n].append(ts - t0)
                        lens[n].append(len(t))
                        if kind == "steer" and len(samples[n]) < a.samples:
                            samples[n].append(t.replace("\n", " ")[:90])
                        pending = None
            if pending:
                stat[pending[0]]["session_end"] += 1

    print(f"{'phase':>5}{'ckpt':>6}{'go':>5}{'steer':>7}{'clear-go':>9}{'no reply':>9}{'sess end':>9}{'steer %':>8}{'gap med min':>12}{'len med':>8}")
    for n in sorted(stat):
        s = stat[n]
        replied = s["go"] + s["steer"]
        print(f"{n:>5}{s['checkpoints']:>6}{s['go']:>5}{s['steer']:>7}{s['cleared_go']:>9}{s['no_reply']:>9}{s['session_end']:>9}"
              f"{(100 * s['steer'] / replied if replied else 0):>7.0f}%"
              f"{(statistics.median(gaps[n]) / 60 if gaps[n] else 0):>12.1f}{(statistics.median(lens[n]) if lens[n] else 0):>8.0f}")
    for n in sorted(samples):
        print(f"\n-- steer samples after phase {n}")
        for t in samples[n]:
            print("   " + t)
    # WHY: after /clear the go arrives in another session, so the wait is matched across sessions by slug and phase.
    print("\ncheckpoint N -> handoff `繼續 <slug> 第 N+1 階段` in a later session (wait before the next phase starts):")
    print(f"{'phase':>5}{'n':>5}{'med min':>9}{'p75 min':>9}{'>2h':>5}{'>8h':>5}{'sum h':>8}")
    for n in range(1, 7):
        w = []
        for slug, ph, t1 in hands:
            if ph != n + 1:
                continue
            prev = [t0 for s0, p0, t0 in cps if s0 == slug and p0 == n and t0 <= t1]
            if prev:
                w.append(t1 - max(prev))
        if w:
            w.sort()
            print(f"{n:>5}{len(w):>5}{statistics.median(w) / 60:>9.1f}{w[int(.75 * (len(w) - 1))] / 60:>9.1f}"
                  f"{sum(x > 7200 for x in w):>5}{sum(x > 28800 for x in w):>5}{sum(w) / 3600:>8.1f}")
    tot = sum(hours.values()) or 1
    print("\nmain-session active 5-min bins by local hour (share of all):")
    print("  " + "  ".join(f"{h:02d}:{100 * hours[h] / tot:.0f}%" for h in range(24)))
    night = sum(hours[h] for h in (0, 1, 2, 3, 4, 5, 6, 7))
    print(f"  00-08 share {100 * night / tot:.0f}%   total main-active {tot * 300 / 3600:.0f} h")


if __name__ == "__main__":
    main()
