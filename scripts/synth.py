#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase-6 synthesis dispatch with no hand work: land Workflow reports, take stock of every lane, build the
findings index, write the doc-slice task file from assets/synth-brief.md, print the dispatch line.

Usage:
  python3 synth.py --review-dir tmp/<slug>/review --review-md <REVIEW.md> [--journal <run>/journal.jsonl]
                   [--section TEXT] [--ack-manual LANE ...] [--waive LANE=REASON ...] [--note TEXT ...]
                   [--extra "PATH 一句為什麼要讀" ...] [--out <review-dir>/synth-task.md] [--slug NAME]

Steps: (1) --journal: `wf_reports.py --journal ... --out-dir <review-dir>`; (2) every expected lane
(`tasks/*.md`, `ext-*.log`, `lane-*.status.json`) is ok / failed / missing / pending (an `ext-*.log` whose CLI has
not written its status yet); (3) `reviewcov.py <review-dir> --index <review-dir>/findings-index.md` with the
--waive／--ack-manual flags; (4) the task file; (5) one line to paste into the Agent call.

Exit: 0 task file written; 1 an internal lane is missing or failed and not waived; 3 an external lane is still
running (wait for its background notification, or --waive); 2 bad arguments or a tool that would not run.
On 1／2／3 nothing is written and a task file from an earlier run is removed, so a stale one cannot be dispatched.
WHY a script: 11-07, 16 reviews — internal lanes done to synthesis started median 6.2 min, 170 min in all, 165 of
them the main session reading reports and hand-writing this task file; the file's content is the same every time.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _lanes import expected_lanes, lane_name, read_status  # noqa: E402
from reviewcov import _PASS, classify  # noqa: E402

TEMPLATE = HERE.parent / "assets" / "synth-brief.md"


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def lane_rows(review_dir, waived, acked):
    """[(lane, via, state, detail, counts)] — state is reviewcov.classify's (ok, ok-zero, acked, waived pass;
    failed, missing, empty, stale, mixed, count-mismatch, unformatted do not) plus `pending`: an external lane
    whose CLI has written neither status nor report yet. INVARIANT: same classifier as the lane gate, so a lane
    this script dispatches on is one reviewcov will accept."""
    baseline = review_dir / "baseline.sha256"
    baseline_mtime = baseline.stat().st_mtime if baseline.exists() else None
    rows = []
    for name, via in expected_lanes(review_dir).items():
        st = read_status(review_dir, name)
        state, detail, _heads = classify(review_dir, name, waived, acked, baseline_mtime)
        if state == "missing" and via == "ext-log" and st is None:
            state, detail = "pending", "ext-*.log 存在、status 與報告都還沒寫（CLI 還在跑）"
        counts = (st or {}).get("counts") if isinstance((st or {}).get("counts"), dict) else None
        rows.append((name, via, state, detail, counts))
    return rows


def md_table(rows):
    label = {"ok": "ok", "ok-zero": "ok（0 條）", "acked": "ok（手讀認可，來源寫 lane 名）", "waived": "免"}
    out = ["  | lane | 來源 | 狀態 | 自報條數 |", "  |---|---|---|---|"]
    for name, via, state, detail, counts in rows:
        c = "／".join(f"{k[0].upper()}{v}" for k, v in counts.items()) if counts else "—"
        d = f"{label[state]}：{detail}" if state == "waived" else label.get(state, state)
        out.append(f"  | {name} | {'內審' if via == 'tasks' else '外審'} | {d} | {c} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--review-dir", required=True)
    ap.add_argument("--review-md", required=True)
    ap.add_argument("--journal")
    ap.add_argument("--section", help="round heading text, e.g. 第二輪; default 第 6 階段")
    ap.add_argument("--ack-manual", action="append", default=[], metavar="LANE")
    ap.add_argument("--waive", action="append", default=[], metavar="LANE=REASON")
    ap.add_argument("--note", action="append", default=[], metavar="TEXT")
    ap.add_argument("--extra", action="append", default=[], metavar="PATH WHY")
    ap.add_argument("--out")
    ap.add_argument("--slug")
    a = ap.parse_args()

    review_dir = pathlib.Path(a.review_dir).resolve()
    review_md = pathlib.Path(a.review_md).resolve()
    out = pathlib.Path(a.out).resolve() if a.out else review_dir / "synth-task.md"
    if not review_dir.is_dir():
        print(f"synth: no such review dir {review_dir}", file=sys.stderr)
        return 2
    waived = {}
    for w in a.waive:
        if "=" not in w:
            print(f"synth: --waive wants LANE=REASON, got {w!r}", file=sys.stderr)
            return 2
        k, v = w.split("=", 1)
        waived[lane_name(k.strip())] = v.strip()
    acked = {lane_name(x) for x in a.ack_manual}
    slug = a.slug or (review_dir.parent.name if review_dir.name == "review" else review_dir.name)
    round_title = a.section or "第 6 階段"

    def abort(code, why):
        if out.exists():
            out.unlink()
            print(f"synth: removed stale {out}")
        print(f"synth: not dispatchable — {why}")
        return code

    if a.journal:
        rc, text = run([sys.executable, str(HERE / "wf_reports.py"), "--journal", a.journal, "--out-dir", str(review_dir)])
        print("\n".join(text.splitlines()[-6:]))
        if rc != 0:
            print(f"synth: wf_reports exit {rc} (lanes listed above are missing until they land)")

    rows = lane_rows(review_dir, waived, acked)
    if not rows:
        return abort(2, f"no lanes found under {review_dir} (tasks/*.md, ext-*.log, lane-*.status.json)")
    for name, _via, state, detail, _counts in rows:
        print(f"  {state:8} {name:14} {detail}")
    pending = [n for n, _, s, _, _ in rows if s == "pending"]
    bad = [f"{n}({s})" for n, _, s, _, _ in rows if s not in _PASS and s != "pending"]
    if bad:
        return abort(1, f"lane not accepted by the gate: {', '.join(bad)} (rerun it, --ack-manual an unformatted report, or --waive LANE=REASON)")
    if pending:
        return abort(3, f"external lane still running: {', '.join(pending)} (wait for its background notification, then rerun; or --waive)")

    index_md = review_dir / "findings-index.md"
    flags = [f"--waive={k}={v}" for k, v in waived.items()] + [f"--ack-manual={x}" for x in sorted(acked)]
    rc, text = run([sys.executable, str(HERE / "reviewcov.py"), str(review_dir), "--index", str(index_md), *flags])
    print("\n".join(text.splitlines()[-4:]))
    index_json = index_md.with_suffix(".json")
    if not index_json.is_file():
        return abort(2, f"reviewcov --index wrote no {index_json.name} (exit {rc})")
    items = json.loads(index_json.read_text(encoding="utf-8"))
    n_ids = sum(1 for i in items if i.get("id"))

    extras = []
    for cand, why in ((review_dir / "brief.md", "共用任務書：重點面定義、已知既存紅、預先登記、不做清單出處"),
                      (review_dir / "plan-core.md", "PLAN 核心摘錄（不做清單、owner 拍板）"),
                      (review_dir / "wt-snap" / "tmp" / "review-input" / "excerpts-core.md", "PLAN／SPEC 逐字摘錄"),
                      (review_dir / "wt-snap", "快照（核對某條位置時讀，唯讀）")):
        if cand.exists():
            extras.append(f"- {why}：`{cand}`")
    for x in a.extra:
        p, _, why = x.partition(" ")
        extras.append(f"- {why or '主 session 指定'}：`{p}`")
    section_flag = f" --section {a.section}" if a.section else ""
    reviewcov_cmd = f"python3 {HERE / 'reviewcov.py'} {review_dir} {review_md}{section_flag}" + "".join(f" {f}" for f in flags)
    fill = {
        "slug": slug, "round_title": round_title, "review_dir": str(review_dir), "review_md": str(review_md),
        "review_md_state": "已有檔，本輪另開一節、不動前幾輪" if review_md.exists() else "新檔",
        "n_lanes": str(len(rows)), "lanes_table": md_table(rows), "index_md": str(index_md), "index_json": str(index_json),
        "n_ids": str(n_ids), "extra_inputs": "\n".join(extras), "notes": "\n".join(f"  - {n}" for n in a.note) or "  - 無",
        "reviewcov_cmd": reviewcov_cmd,
    }
    text = TEMPLATE.read_text(encoding="utf-8")
    missing = sorted(set(re.findall(r"\{\{(\w+)\}\}", text)) - set(fill))
    if missing:
        print(f"synth: template placeholders without a value: {missing}", file=sys.stderr)
        return 2
    text = re.sub(r"\{\{(\w+)\}\}", lambda m: fill[m.group(1)], text)
    out.write_text(text, encoding="utf-8")
    states = {s: sum(1 for r in rows if r[2] == s) for s in ("ok", "acked", "waived")}
    print(f"synth: {out} — lanes ok {states['ok']}, acked {states['acked']}, waived {states['waived']}; {n_ids} finding ids")
    print(f'派工：Agent(subagent_type="doc-slice", description="合成 {slug} REVIEW", prompt=<{out} 全文>)')
    return 0


if __name__ == "__main__":
    sys.exit(main())
