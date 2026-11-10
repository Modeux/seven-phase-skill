#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ui-lane-reviewer runs: where the wall-clock goes without double counting, and how much script work repeats.

  python3 uilane_audit.py                         # last 14 days, main checkout + worktrees (run from the repo root)
  python3 uilane_audit.py --since 2026-11-06      # only runs started on or after this date
  python3 uilane_audit.py --project-dir ~/.claude/projects/<dir>

Each API request splits into generation (last tool result → last block) and tool wait (last block → last tool
result), so parallel calls count once; wallclock.py sums per call and can pass 100% on batched runs.
Away spans (`_usage.away_spans`: retried requests with no response, sitting finished until resumed) are cut out
of wall, generation and wait; the `away` column shows them.
A run counts once by agent id: a forked session keeps a copy of its subagents' transcripts.
"Failed exec" is matched on output text, so it is an estimate.
"""
import argparse
import collections
import datetime
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _usage import agent_files, away_spans, overlap, project_dirs  # noqa: E402

SCRIPT = re.compile(r"\.(js|mjs|cjs|sh|py)$")
# INVARIANT: names agents gave the helpers walk_lib.mjs and walk-env.template.sh now provide; a kit's login.js and
# env.sh count too, because copying the template and setting values is not a Write of the whole file per fix.
INFRA = re.compile(r"(^|[-_.])(login|env|lib|helpers?|prelude|drive|build|run-kit|replay|waitrun)([-_.]|$)")
FAILED = re.compile(r"TimeoutError|Timeout \d+ms|waiting for locator|strict mode violation|SyntaxError|ReferenceError"
                    r"|TypeError|is not a function|is not defined|Cannot read prop|ERR_MODULE_NOT_FOUND"
                    r"|Cannot find module|Cannot use import|ECONNREFUSED|ERR_CONNECTION")
USES_LIB = re.compile(r"walk_lib\.mjs|async\s*\(\s*page\s*,\s*walk\s*\)")
PROBE = re.compile(r"\bnode\b|run_kit")
# A shell loop that sleeps or pgreps is the agent polling its own background job; the harness notifies on exit instead.
POLL = re.compile(r"\b(for|while|until)\b[^\n]*\b(sleep|pgrep)\b")


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def med(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0


def text_of(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    return " ".join(x.get("text", "") for x in c if isinstance(x, dict)) if isinstance(c, list) else ""


def source_of(name, inp):
    if name == "Write" and SCRIPT.search(inp.get("file_path", "")):
        return inp.get("content", ""), inp["file_path"]
    if name == "Edit" and SCRIPT.search(inp.get("file_path", "")):
        return inp.get("new_string", ""), inp["file_path"]
    if name == "Bash" and "<<" in inp.get("command", ""):
        return inp["command"], ""
    if "run_code" in name:
        return inp.get("code") or inp.get("function") or "", ""
    return None, ""


def analyze(agent):
    events = []
    for line in agent.path.open(encoding="utf-8"):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "timestamp" in e:
            events.append(e)
    if len(events) < 5:
        return None
    t0, t1 = ts(events[0]["timestamp"]), ts(events[-1]["timestamp"])
    spans = away_spans(events)
    reqs = collections.OrderedDict()
    tools, results = {}, {}
    run = collections.Counter(peak=0)
    seen_lines, written = collections.Counter(), collections.Counter()
    for e in events:
        m = e.get("message") or {}
        c = m.get("content")
        t = ts(e["timestamp"])
        if e.get("type") == "assistant" and isinstance(c, list):
            u = m.get("usage") or {}
            run["peak"] = max(run["peak"], sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")))
            r = reqs.setdefault(m.get("id") or e.get("uuid"), dict(last=t, ids=[], script=False))
            r["last"] = t
            for b in c:
                if b.get("type") != "tool_use":
                    continue
                inp = b.get("input") or {}
                r["ids"].append(b["id"])
                tools[b["id"]] = (b["name"], inp, t)
                if b["name"] == "Bash" and PROBE.search(inp.get("command", "")):
                    run["probes"] += 1
                    run["bg_probes"] += bool(inp.get("run_in_background"))
                src, path = source_of(b["name"], inp)
                if src is None:
                    continue
                r["script"] = True
                run["script_chars"] += len(src)
                run["lib"] += bool(USES_LIB.search(src))
                if path:
                    written[path] += 1
                    run["infra"] += bool(INFRA.search(path.rsplit("/", 1)[-1]))
                for ln in src.splitlines():
                    s = ln.strip()
                    if len(s) >= 12:
                        run["lines"] += 1
                        run["dup_lines"] += seen_lines[s] > 0
                        seen_lines[s] += 1
        elif e.get("type") == "user" and isinstance(c, list):
            for b in c:
                if not isinstance(b, dict) or b.get("type") != "tool_result" or b.get("tool_use_id") not in tools:
                    continue
                results[b["tool_use_id"]] = t
                name, inp, started = tools[b["tool_use_id"]]
                txt = text_of(b)
                if name == "Bash":
                    run["bash_chars"] += len(txt)
                    cmd = inp.get("command", "")
                    # Waits are per call from the turn's start, so a batch over-attributes; both are estimates.
                    if POLL.search(cmd):
                        run["poll_wait"] += max(t - started, 0)
                    elif PROBE.search(cmd) and not inp.get("run_in_background"):
                        run["fg_probe_wait"] += max(t - started, 0)
                if "run_code" in name or re.search(r"\bnode\b|run_kit", inp.get("command", "")):
                    run["execs"] += 1
                    if FAILED.search(txt) or b.get("is_error"):
                        run["failed_execs"] += 1
                        run["failed_wait"] += max(t - started, 0)
    gen = wait = script_gen = 0.0
    prev = t0
    for r in reqs.values():
        g = max(r["last"] - prev - overlap(spans, prev, r["last"]), 0)
        gen += g
        script_gen += g if r["script"] else 0
        ends = [results[i] for i in r["ids"] if i in results]
        end = max(ends) if ends else r["last"]
        wait += max(end - r["last"] - overlap(spans, r["last"], end), 0)
        prev = end
    away = sum(b - a for a, b, _k in spans)
    return dict(t0=t0, wall=max(t1 - t0 - away, 1), away=away, gen=gen, wait=wait, script_gen=script_gen, reqs=len(reqs), run=run,
                rewrites=sum(v - 1 for v in written.values()), desc=agent.meta.get("description") or "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--since", help="YYYY-MM-DD; overrides --days")
    ap.add_argument("--project-dir")
    a = ap.parse_args()
    since = datetime.datetime.fromisoformat(a.since).timestamp() if a.since else time.time() - a.days * 86400
    seen, rows = set(), []
    for proj in project_dirs(a.project_dir, all_worktrees=a.project_dir is None):
        for agent in agent_files(proj):
            if agent.meta.get("agentType") != "ui-lane-reviewer" or agent.agent_id in seen or agent.path.stat().st_mtime < since:
                continue
            seen.add(agent.agent_id)
            r = analyze(agent)
            if r and r["t0"] >= since:
                rows.append(r)
    rows.sort(key=lambda r: r["t0"])
    print("started      wall  away gen% wait% reqs scriptK dup% infra rewr lib failedWait% bashK peakK probes bg fgWait% poll%  description")
    for r in rows:
        c = r["run"]
        print(f"{datetime.datetime.fromtimestamp(r['t0']).strftime('%m-%d %H:%M')} {r['wall']/60:5.1f} {r['away']/60:5.1f} {r['gen']/r['wall']*100:4.0f} {r['wait']/r['wall']*100:5.0f} "
              f"{r['reqs']:4} {c['script_chars']/1000:7.0f} {c['dup_lines']/max(c['lines'],1)*100:4.0f} {c['infra']:5} {r['rewrites']:4} {'Y' if c['lib'] else '-':>3} "
              f"{c['failed_wait']/r['wall']*100:11.1f} {c['bash_chars']/1000:5.0f} {c['peak']/1000:5.0f} {c['probes']:6} {c['bg_probes']:2} "
              f"{c['fg_probe_wait']/r['wall']*100:7.0f} {c['poll_wait']/r['wall']*100:5.0f}  {r['desc'][:40]}")
    if not rows:
        return 0
    total = sum(r["wall"] for r in rows)
    lines = sum(r["run"]["lines"] for r in rows)
    print(f"\nruns {len(rows)}; wall median {med([r['wall'] for r in rows])/60:.1f} min, sum {total/60:.0f} min; "
          f"generation {sum(r['gen'] for r in rows)/total*100:.0f}%, tool wait {sum(r['wait'] for r in rows)/total*100:.0f}%; "
          f"away {sum(r['away'] for r in rows)/60:.0f} min excluded (_usage.away_spans)")
    print(f"generation on requests that write scripts: {sum(r['script_gen'] for r in rows)/total*100:.0f}% of wall; "
          f"repeated script lines: {sum(r['run']['dup_lines'] for r in rows)/max(lines,1)*100:.0f}%; "
          f"infra writes per run: {sum(r['run']['infra'] for r in rows)/len(rows):.1f}; rewrites per run: {sum(r['rewrites'] for r in rows)/len(rows):.1f}")
    print(f"failed exec: {sum(r['run']['failed_execs'] for r in rows)}/{sum(r['run']['execs'] for r in rows)} runs of scripts, "
          f"waiting {sum(r['run']['failed_wait'] for r in rows)/total*100:.1f}% of wall (estimate)")
    print(f"Bash output median {med([r['run']['bash_chars'] for r in rows])/1000:.0f}k chars; peak context median {med([r['run']['peak'] for r in rows])/1000:.0f}k; "
          f"runs using walk_lib: {sum(1 for r in rows if r['run']['lib'])}/{len(rows)}")
    probes = sum(r["run"]["probes"] for r in rows)
    print(f"probes (node/run_kit via Bash): {probes}, in background {sum(r['run']['bg_probes'] for r in rows)}; "
          f"foreground probe wait {sum(r['run']['fg_probe_wait'] for r in rows)/total*100:.1f}% of wall, "
          f"poll loops {sum(r['run']['poll_wait'] for r in rows)/total*100:.1f}% (estimates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
