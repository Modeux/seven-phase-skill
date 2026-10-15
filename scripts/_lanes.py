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
import re

# INVARIANT: assets/review-brief.md tells every lane to title findings `### [SEVERITY] title` with a
# `- 位置：` line; reviewcov ids (<lane>#<n>) are the n-th such heading in file order.
# WHY: lanes write `### [LOW｜需要 owner 拍板] title`; without the qualifier arm those findings drop out of the
# numbering and every later <lane>#<n> in the map points at a different finding.
_SEV_BRACKET = r"\[\s*(CRITICAL|HIGH|MEDIUM|LOW)\b([^\]]*)\]"
_FINDING_HEAD = re.compile(rf"^###\s*{_SEV_BRACKET}\s*(.*)$", re.I)
# Any severity tag a lane might use: [HIGH], [HIGH｜…], [MEDIUM-HIGH], 【HIGH】, [MED], [CRIT].
SEV_TAG = r"(?:\[|【)\s*(?:CRITICAL|CRIT|HIGH|MEDIUM|MED|LOW)\b[^\]】]*(?:\]|】)"
_STRAY_HEAD = re.compile(
    rf"^\s*(?:#{{1,6}}\s*(?:\S+\s*)?|\d+[.)]\s*|[-*]\s*)?\*{{0,2}}{SEV_TAG}"     # `## [HIGH]`, `### F7 [HIGH]`, `- 【HIGH】`
    r"|^\s*#{1,6}\s*\**(?:CRITICAL|HIGH|MEDIUM|LOW)\s*[：:]"                     # `### HIGH：title`
    rf"|\S.*?#{{2,6}}\s*{SEV_TAG}", re.I)                                        # `…對不上。### [HIGH] title` (glued)
_LOCATION = re.compile(r"^\s*[-*]\s*\**(?:位置|location|file|檔案)\**\s*(?:[:：]\s*(.*))?$", re.I)
_ANY_HEAD = re.compile(r"^#{1,6}\s")


def report_findings(text):
    """[{n, severity, tag, title, location}] for each `### [SEVERITY…] title` heading, in file order; `tag` is
    what else the bracket said (`需要 owner 拍板`), kept so the index does not drop it. A `- 位置：` (or
    `- **位置**`, `- File:`) line with nothing after it takes its value from the next non-empty line."""
    out, open_, want = [], False, False
    for line in text.splitlines():
        m = _FINDING_HEAD.match(line)
        if m:
            out.append({"n": len(out) + 1, "severity": m.group(1).upper(), "tag": m.group(2).strip(" ｜|/／,，;；:：-"),
                        "title": m.group(3).strip(), "location": ""})
            open_, want = True, False
        elif _ANY_HEAD.match(line):
            open_ = want = False
        elif open_ and want and line.strip():
            out[-1]["location"], want = line.strip().lstrip("-* ").strip(), False
        elif open_ and not out[-1]["location"] and (loc := _LOCATION.match(line)):
            value = (loc.group(1) or "").strip()
            out[-1]["location"], want = value, not value
    return out


def stray_findings(text):
    """Line numbers of severity-tagged lines shaped like a finding title but not `### [SEVERITY]`
    (`## [HIGH]`, `### 1. [HIGH]`, `**[HIGH]**`, `- [HIGH]`): findings the id scheme cannot see."""
    return [i for i, line in enumerate(text.splitlines(), 1)
            if _STRAY_HEAD.match(line) and not _FINDING_HEAD.match(line)]


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
