#!/usr/bin/env python3
"""Land the full reports a Workflow fan-out returned through its schema, one file per agent.

Workflow subagents cannot write report files, so review / verification lanes return the report
text in a schema field (default `report_markdown`) and the script returns only summaries. This
reads the run's journal.jsonl (each agent's validated return value), maps agentId -> label from
the "started" events, and writes <out-dir>/<label>.md.

Usage:
  python3 wf_reports.py --journal <transcript dir>/journal.jsonl --out-dir tmp/<slug>/review
  python3 wf_reports.py --journal ... --out-dir ... --field report_markdown --only lane-go lane-ts
Labels become file names as-is (label 'lane-go' -> lane-go.md). Existing files are overwritten.
Exit 1 when a started agent has no result or its result lacks the field (listed on stderr).
"""
import argparse
import json
import os
import sys


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--journal", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--field", default="report_markdown")
    ap.add_argument("--only", nargs="*", help="labels to write (default: every started agent)")
    a = ap.parse_args()

    labels, results = {}, {}
    with open(a.journal, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            if ev.get("type") == "started" and ev.get("agentId"):
                labels[ev["agentId"]] = ev.get("label") or ev["agentId"]
            elif ev.get("type") == "result" and ev.get("agentId"):
                results[ev["agentId"]] = ev.get("result")

    os.makedirs(a.out_dir, exist_ok=True)
    problems = []
    for aid, label in labels.items():
        if a.only and label not in a.only:
            continue
        res = results.get(aid)
        if res is None:
            problems.append("%s (%s): no result yet / agent died" % (label, aid))
            continue
        text = res.get(a.field) if isinstance(res, dict) else None
        if not isinstance(text, str) or not text.strip():
            problems.append("%s (%s): result has no %r" % (label, aid, a.field))
            continue
        path = os.path.join(a.out_dir, label + ".md")
        with open(path, "w", encoding="utf-8") as out:
            out.write(text if text.endswith("\n") else text + "\n")
        print("%-24s %8d B  %s" % (label, len(text.encode("utf-8")), path))
    for p in problems:
        print("MISSING " + p, file=sys.stderr)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
