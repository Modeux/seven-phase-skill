#!/usr/bin/env python3
"""Land the full reports a Workflow fan-out returned through its schema, one file per agent.

Workflow subagents cannot write report files, so review / verification lanes return the report
text in a schema field (default `report_markdown`) and the script returns only summaries. This
reads the run's journal.jsonl (each agent's validated return value), maps agentId -> label from
the "started" events, and writes <out-dir>/<label>.md plus <out-dir>/<label>.status.json
(read by reviewcov.py; a failed lane's status has rc 1 and any older <label>.md is left untouched;
a result's `counts` object, e.g. {"critical": 0, "high": 1, ...}, is kept in the status file).

Usage:
  python3 wf_reports.py --journal <transcript dir>/journal.jsonl --out-dir tmp/<slug>/review
  python3 wf_reports.py --journal ... --out-dir ... --field report_markdown --only lane-go lane-ts
Labels become file names as-is (label 'lane-go' -> lane-go.md). Existing files are overwritten.
When <out-dir>/tasks/ exists, every tasks/<x>.md whose label lane-<x> never started is MISSING too.
Exit 1 when a started agent has no result or its result lacks the field, a result has no started event,
an --only label is unknown, or a dispatched lane never started (listed on stderr).
"""
import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _lanes import write_status  # noqa: E402


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
    journal = str(pathlib.Path(a.journal).resolve())
    problems = []

    def status(label, aid, error=None, report=None, counts=None):
        extra = {"counts": counts} if isinstance(counts, dict) else {}
        write_status(os.path.join(a.out_dir, label + ".status.json"), source="workflow", journal=journal,
                     agent_id=aid, rc=0 if error is None else 1, ok=error is None, error=error, report=report, **extra)
        if error is not None:
            problems.append("%s (%s): %s" % (label, aid, error))

    for aid, label in labels.items():
        if a.only and label not in a.only:
            continue
        res = results.get(aid)
        if res is None:
            status(label, aid, "no result yet / agent died")
            continue
        text = res.get(a.field) if isinstance(res, dict) else None
        if not isinstance(text, str) or not text.strip():
            status(label, aid, "result has no %r" % a.field)
            continue
        path = os.path.join(a.out_dir, label + ".md")
        with open(path, "w", encoding="utf-8") as out:
            out.write(text if text.endswith("\n") else text + "\n")
        # WHY: the schema's `counts` is the lane's own tally; reviewcov compares it with the report's headings.
        status(label, aid, report=str(pathlib.Path(path).resolve()), counts=res.get("counts"))
        print("%-24s %8d B  %s" % (label, len(text.encode("utf-8")), path))

    started = set(labels.values())
    for aid in results:
        if aid not in labels:
            # WHY: a result without its "started" event has no label, so nothing would be written for it.
            problems.append("%s: result has no started event (no label; report not written)" % aid)
    for label in a.only or []:
        if label not in started and not pathlib.Path(a.out_dir, "tasks", label.removeprefix("lane-") + ".md").exists():
            problems.append("--only %s: no such label in the journal" % label)
    for task in sorted(pathlib.Path(a.out_dir, "tasks").glob("*.md")):
        label = "lane-" + task.stem
        if label in started or (a.only and label not in a.only):
            continue
        # WHY: a lane the Workflow never launched leaves no journal trace at all; without this it is
        # invisible, and an older lane-<x>.md from a previous round would pass as this round's report.
        status(label, None, "never started (tasks/%s exists)" % task.name)
    for p in problems:
        print("MISSING " + p, file=sys.stderr)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
