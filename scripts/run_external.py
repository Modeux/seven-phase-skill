# -*- coding: utf-8 -*-
"""Run one external review lane (codex / grok / agy) against a frozen snapshot. Run it in the background.

Usage:
  python3 run_external.py codex --packet <packet.md> --snapshot <wt-snap> --out <lane-codex.md> [--root <repo>]
  python3 run_external.py grok  --packet <packet.md> --snapshot <wt-snap> --out <lane-grok.md>
  python3 run_external.py agy   --packet <packet.md> --snapshot <wt-snap> --out <lane-agy.md>
A log is written next to --out as ext-<lane>.log, and a status file as <out stem>.status.json
(lane-codex.md -> lane-codex.status.json) that reviewcov.py reads. The CLI writes to <out>.partial;
only a zero-exit, non-blank report replaces <out>, so a failure never clobbers or impersonates a report.
Exit: 0 ok; the CLI's exit code when it failed; 3 when it exited 0 without a report; 2 agy packet too big.
Pitfalls: references/external-review.md.
"""
import argparse
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _lanes import write_status  # noqa: E402

CODEX_MODEL, CODEX_EFFORT = "gpt-6-sol", "xhigh"
GROK_MODEL = "grok-4.7"
AGY_MODEL, AGY_EFFORT = "gemini-3.8-flash", "high"
AGY_MAX_BYTES = 170_000


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lane", choices=["codex", "grok", "agy"])
    ap.add_argument("--packet", required=True)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", default=None, help="cwd for codex (repo root); default: snapshot's parent repo cwd")
    a = ap.parse_args()
    packet, snap, out = pathlib.Path(a.packet), pathlib.Path(a.snapshot), pathlib.Path(a.out)
    log_path = out.parent / f"ext-{a.lane}.log"
    partial = out.with_name(out.name + ".partial")
    status_file = out.with_suffix(".status.json")
    size = packet.stat().st_size
    base = dict(lane=a.lane, snapshot=str(snap.resolve()), packet=str(packet.resolve()), packet_bytes=size,
                log=str(log_path.resolve()))
    print(f"{a.lane}: packet {size} bytes")
    # WHY: a partial left by an earlier crash would otherwise be promoted as this run's report.
    partial.unlink(missing_ok=True)
    t0 = time.time()
    if a.lane == "codex":
        # WHY: codex writes -o only on success, so pointing it at <out> let the previous round's
        # report survive a failure and be read as current.
        with open(packet) as fin, open(log_path, "w") as log:
            r = subprocess.run(["codex", "exec", "-m", CODEX_MODEL, "-c", 'sandbox_mode="read-only"',
                                "-c", f"model_reasoning_effort={CODEX_EFFORT}", "-C", str(snap),
                                "--skip-git-repo-check", "-o", str(partial), "-"],
                               stdin=fin, stdout=log, stderr=subprocess.STDOUT, cwd=a.root or None)
    elif a.lane == "grok":
        with open(partial, "w") as fout, open(log_path, "w") as log:
            r = subprocess.run(["grok", "--prompt-file", str(packet), "--verbatim", "-m", GROK_MODEL,
                                "--output-format", "plain"], stdout=fout, stderr=log, cwd=str(snap))
    else:
        if size > AGY_MAX_BYTES:
            err = f"agy packet is {size} bytes (> {AGY_MAX_BYTES}); input truncates around 190KB - trim it"
            write_status(status_file, **base, rc=2, ok=False, secs=0, report=None, error=err)
            print(err, file=sys.stderr)
            sys.exit(2)
        with open(partial, "w") as fout, open(log_path, "w") as log:
            r = subprocess.run(["agy", "--model", AGY_MODEL, "--effort", AGY_EFFORT, "--print", packet.read_text()],
                               stdout=fout, stderr=log, cwd=str(snap))
    secs = int(time.time() - t0)
    rc = r.returncode
    has_text = partial.exists() and partial.read_text(errors="replace").strip() != ""
    ok = rc == 0 and has_text
    if ok:
        partial.replace(out)
    write_status(status_file, **base, rc=rc, ok=ok, secs=secs, report=str(out.resolve()) if ok else None)
    if ok:
        print(f"{a.lane} EXIT 0 after {secs}s; report {out} ({out.stat().st_size} bytes)")
        if "truncat" in out.read_text(errors="replace").lower():
            print("WARNING: report mentions truncation - check the packet size")
        return
    why = f"exit {rc}" if rc != 0 else "exit 0 but no report / blank report"
    stale = f"; {out} is from an earlier run, NOT this one" if out.exists() else ""
    kept = f"; partial output kept at {partial}" if partial.exists() else ""
    print(f"{a.lane} FAILED ({why}) after {secs}s; see {log_path}{kept}{stale}", file=sys.stderr)
    sys.exit(rc if rc != 0 else 3)


if __name__ == "__main__":
    main()
