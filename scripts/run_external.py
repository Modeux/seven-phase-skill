# -*- coding: utf-8 -*-
"""Run one external review lane (codex / grok / agy) against a frozen snapshot. Run it in the background.

Usage:
  python3 run_external.py codex --packet <packet.md> --snapshot <wt-snap> --out <lane-codex.md> [--root <repo>]
  python3 run_external.py grok  --packet <packet.md> --snapshot <wt-snap> --out <lane-grok.md>
  python3 run_external.py agy   --packet <packet.md> --snapshot <wt-snap> --out <lane-agy.md>
A log is written next to --out as ext-<lane>.log. Pitfalls: references/external-review.md.
"""
import argparse
import pathlib
import subprocess
import sys
import time

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
    size = packet.stat().st_size
    print(f"{a.lane}: packet {size} bytes")
    t0 = time.time()
    if a.lane == "codex":
        with open(packet) as fin, open(log_path, "w") as log:
            r = subprocess.run(["codex", "exec", "-m", CODEX_MODEL, "-c", 'sandbox_mode="read-only"',
                                "-c", f"model_reasoning_effort={CODEX_EFFORT}", "-C", str(snap),
                                "--skip-git-repo-check", "-o", str(out), "-"],
                               stdin=fin, stdout=log, stderr=subprocess.STDOUT, cwd=a.root or None)
    elif a.lane == "grok":
        with open(out, "w") as fout, open(log_path, "w") as log:
            r = subprocess.run(["grok", "--prompt-file", str(packet), "--verbatim", "-m", GROK_MODEL,
                                "--output-format", "plain"], stdout=fout, stderr=log, cwd=str(snap))
    else:
        if size > AGY_MAX_BYTES:
            sys.exit(f"agy packet is {size} bytes (> {AGY_MAX_BYTES}); input truncates around 190KB - trim it")
        with open(out, "w") as fout, open(log_path, "w") as log:
            r = subprocess.run(["agy", "--model", AGY_MODEL, "--effort", AGY_EFFORT, "--print", packet.read_text()],
                               stdout=fout, stderr=log, cwd=str(snap))
    secs = int(time.time() - t0)
    print(f"{a.lane} EXIT {r.returncode} after {secs}s; report {out} ({out.stat().st_size if out.exists() else 0} bytes)")
    if out.exists() and "truncat" in out.read_text(errors="replace").lower():
        print("WARNING: report mentions truncation - check the packet size")


if __name__ == "__main__":
    main()
