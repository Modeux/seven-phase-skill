# -*- coding: utf-8 -*-
"""Shared lane bookkeeping for a review dir (stdlib only): which lanes were expected, per-lane status files.

Usage (library):
  from _lanes import expected_lanes, read_status, write_status
  expected_lanes("tmp/<slug>/review")      # {"go": "tasks", "codex": "ext-log", ...}
  write_status(path, lane="codex", rc=0, ok=True)

Status files are `lane-<name>.status.json` next to `lane-<name>.md`; writers are run_external.py
(external lanes) and wf_reports.py (Workflow lanes), the reader is reviewcov.py.
"""
import datetime
import json
import pathlib


def lane_name(name):
    return name.removeprefix("lane-")


def status_path(review_dir, name):
    return pathlib.Path(review_dir) / f"lane-{lane_name(name)}.status.json"


def expected_lanes(review_dir):
    d = pathlib.Path(review_dir)
    found = {}
    for p in sorted((d / "tasks").glob("*.md")):
        found.setdefault(p.stem, "tasks")
    for p in sorted(d.glob("ext-*.log")):
        found.setdefault(p.name.removeprefix("ext-").removesuffix(".log"), "ext-log")
    for p in sorted(d.glob("lane-*.status.json")):
        found.setdefault(lane_name(p.name.removesuffix(".status.json")), "status")
    return found


def read_status(review_dir, name):
    p = status_path(review_dir, name)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        # WHY: an unreadable status file must not read as "no status" (that would let an existing
        # report through as ok); surface it as a failure instead.
        return {"rc": -1, "ok": False, "error": f"unreadable status file: {e}"}
    return data if isinstance(data, dict) else {"rc": -1, "ok": False, "error": "status file is not an object"}


def write_status(path, **fields):
    fields["finished"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(fields, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p
