# -*- coding: utf-8 -*-
"""Count or list tool calls in Claude Code transcripts (main session and subagents) — one row per tool_use.

Usage:
  python3 tx.py count --days 14 --by tool                                  # what tools ran, main + subagents
  python3 tx.py count --since 2026-11-05 --agent main --tool Bash --grep 'graphify query'
  python3 tx.py count --all-worktrees --result-grep 'generated-file-guard:' --by session
  python3 tx.py list  --agent tdd-guide --result-grep 'py-edit-guard' --limit 20
  python3 tx.py list  --tool Agent --grep '^ui-lane-reviewer' --jsonl

Filters (shared by count and list):
  --days N | --since YYYY-MM-DD   window start (default --days 14; --since is local midnight); files whose
                                  mtime is before it are not opened
  --all-worktrees                 also the `.claude/worktrees/*` and `tmp/*` worktrees' transcript dirs
                                  (default: only the main checkout's)
  --agent main|<agentType> …      only the main session (`main`) and/or those subagent types; default all
  --tool Bash,Read,…              tool names, exact
  --grep REGEX                    on the call's main input: Bash command, file_path (Read/Edit/Write…),
                                  Agent subagent_type + prompt, anything else as JSON
  --result-grep REGEX             on the paired tool_result text plus hook output attached to that call
                                  (PreToolUse block messages land in the result, PostToolUse
                                  additionalContext such as `ts-lint:` in a hook attachment)
count:  --by session|agent|tool|day|file (default tool); columns rows, sessions, subagent instances, key;
        last line `total`.
list:   one line per call `<local time> <sid[:8]> <main|agentType> <tool> <summary ≤120>`, oldest first;
        --limit N keeps the newest N (default 200, 0 = all); --jsonl prints full input (≤4000 chars) and
        result (≤500 chars) per call, and the TX header goes to stderr.
Location (tests, other machines): --checkout PATH (default: main checkout of cwd), --projects-root DIR.

First stdout line: `TX <count|list> <n> rows sessions=<s> agents=<a> files=<f> bad_lines=<b>`.
Unreadable files are skipped with one stderr line each; malformed JSON lines are counted in bad_lines.

Exit: 0 ok (zero rows included), 2 bad arguments.
"""
import argparse
import calendar
import collections
import datetime
import json
import os
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _usage import agent_files, main_checkout, project_dirs  # noqa: E402

SUMMARY_MAX = 120
JSONL_INPUT_MAX = 4000
JSONL_RESULT_MAX = 500
FILE_KEYS = ("file_path", "notebook_path")
BY_KEYS = ("session", "agent", "tool", "day", "file")

Call = collections.namedtuple("Call", "ts session agent agent_id tool input")


def _since_epoch(a):
    if a.since:
        try:
            return time.mktime(datetime.date.fromisoformat(a.since).timetuple())
        except ValueError:
            raise SystemExit(f"tx: --since {a.since!r} is not YYYY-MM-DD") from None
    return time.time() - a.days * 86400


def _iso_utc(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch))


def _local(ts, fmt):
    try:
        epoch = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return ts[:16]
    return time.strftime(fmt, time.localtime(epoch))


def main_field(tool, inp):
    """The input text --grep searches and the list summary starts from."""
    if not isinstance(inp, dict):
        return json.dumps(inp, ensure_ascii=False)
    if tool == "Bash" and isinstance(inp.get("command"), str):
        return inp["command"]
    for k in FILE_KEYS:
        if isinstance(inp.get(k), str):
            return inp[k]
    if tool in ("Agent", "Task") and (inp.get("subagent_type") or inp.get("prompt")):
        return f"{inp.get('subagent_type') or ''}\n{inp.get('prompt') or ''}"
    return json.dumps(inp, ensure_ascii=False)


def summary(tool, inp):
    if isinstance(inp, dict) and tool in ("Agent", "Task") and inp.get("subagent_type"):
        text = inp["subagent_type"]
    else:
        text = main_field(tool, inp)
    return " ".join(text.split())[:SUMMARY_MAX]


def file_key(inp):
    if isinstance(inp, dict):
        for k in FILE_KEYS:
            if isinstance(inp.get(k), str):
                return inp[k]
    return "-"


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text") if isinstance(b, dict) and isinstance(b.get("text"), str)
                 else (b if isinstance(b, str) else json.dumps(b, ensure_ascii=False)) for b in content]
        return "\n".join(p for p in parts if p)
    return "" if content is None else json.dumps(content, ensure_ascii=False)


class Scan:
    def __init__(self, a):
        self.a = a
        self.since_epoch = _since_epoch(a)
        self.since_iso = _iso_utc(self.since_epoch)
        self.tools = {t.strip() for t in (a.tool or "").split(",") if t.strip()}
        self.agents = {x.strip() for v in (a.agent or []) for x in v.split(",") if x.strip()}
        self.grep = re.compile(a.grep) if a.grep else None
        self.rgrep = re.compile(a.result_grep) if a.result_grep else None
        # WHY: Read/Bash results can be megabytes; hold them only when a filter or --jsonl reads them.
        self.result_cap = None if self.rgrep else (JSONL_RESULT_MAX if getattr(a, "jsonl", False) else 0)
        self.rows = []
        self.files = 0
        self.bad_lines = 0

    def wants(self, agent):
        return not self.agents or agent in self.agents

    def run(self, dirs):
        for d in dirs:
            for main in sorted(d.glob("*.jsonl")):
                if not self._fresh(main):
                    continue
                sid = main.stem
                if self.wants("main"):
                    self.scan(main, sid, "main", None)
                # WHY: a subagent's result is appended to the main file when it finishes, so a session whose
                # main file is older than the window has no subagent activity inside it either.
                for af in agent_files(d, sid):
                    agent = af.meta.get("agentType") or "general-purpose"
                    if self.wants(agent) and self._fresh(af.path):
                        self.scan(af.path, sid, agent, af.agent_id)

    def _fresh(self, path):
        try:
            return os.path.getmtime(path) >= self.since_epoch
        except OSError as e:
            print(f"tx: skip {path}: {e}", file=sys.stderr)
            return False

    def scan(self, path, sid, agent, agent_id):
        is_main = agent_id is None
        pending = {}
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"tx: skip {path}: {e}", file=sys.stderr)
            return
        self.files += 1
        with fh:
            for line in fh:
                # WHY: most lines are text, thinking or reminders; parsing only lines that can carry a call,
                # its result or a hook attachment keeps a 14-day scan of large transcripts cheap.
                if '"tool_use"' not in line and '"tool_result"' not in line and '"toolUseID"' not in line:
                    if line.strip() and not line.rstrip().endswith("}"):
                        self.bad_lines += 1
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    self.bad_lines += 1
                    continue
                if not isinstance(r, dict):
                    continue
                # INVARIANT: main-session lines with isSidechain are subagent echoes counted from the
                # subagent file; every line of a subagent file has isSidechain and must be kept.
                if is_main and r.get("isSidechain"):
                    continue
                kind = r.get("type")
                if kind == "assistant":
                    self._calls(r, sid, agent, agent_id, pending)
                elif kind == "user":
                    content = (r.get("message") or {}).get("content")
                    for b in content if isinstance(content, list) else []:
                        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                            row = pending[b["tool_use_id"]]
                            if self.result_cap is None or len(row["result"]) < self.result_cap:
                                row["result"] = (row["result"] + _text(b.get("content")))[:self.result_cap]
                            row["is_error"] = row["is_error"] or bool(b.get("is_error"))
                elif kind == "attachment":
                    att = r.get("attachment")
                    if isinstance(att, dict) and str(att.get("type", "")).startswith("hook_") \
                            and att.get("toolUseID") in pending:
                        row = pending[att["toolUseID"]]
                        if self.result_cap is None or len(row["hooks"]) < self.result_cap:
                            row["hooks"] = (row["hooks"] + "\n".join(
                                _text(att.get(k)) for k in ("content", "stdout", "stderr") if att.get(k))
                                + "\n")[:self.result_cap]
        for row in pending.values():
            if self.rgrep is None or self.rgrep.search(row["result"]) or self.rgrep.search(row["hooks"]):
                self.rows.append(row)

    def _calls(self, r, sid, agent, agent_id, pending):
        ts = r.get("timestamp") or ""
        if not ts or ts[:19] < self.since_iso:
            return
        content = (r.get("message") or {}).get("content")
        for b in content if isinstance(content, list) else []:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                continue
            tid, tool, inp = b.get("id"), b.get("name") or "?", b.get("input")
            if tid in pending or (self.tools and tool not in self.tools):
                continue
            if self.grep and not self.grep.search(main_field(tool, inp)):
                continue
            pending[tid or ("no-id", len(pending))] = {
                "call": Call(ts, sid, agent, agent_id, tool, inp), "result": "", "hooks": "", "is_error": False}


def key_of(by, c):
    if by == "session":
        return c.session[:8]
    if by == "agent":
        return c.agent
    if by == "tool":
        return c.tool
    if by == "day":
        return _local(c.ts, "%Y-%m-%d")
    return file_key(c.input)


def spread(calls):
    """(sessions, subagent instances) touched by the calls."""
    return (len({c.session for c in calls}),
            len({(c.session, c.agent_id) for c in calls if c.agent_id is not None}))


def header(cmd, calls, scan):
    s, a = spread(calls)
    return f"TX {cmd} {len(calls)} rows sessions={s} agents={a} files={scan.files} bad_lines={scan.bad_lines}"


def print_count(calls, scan, by):
    print(header("count", calls, scan))
    groups = collections.defaultdict(list)
    for c in calls:
        groups[key_of(by, c)].append(c)
    print(f"{'rows':>6} {'sess':>5} {'agts':>5}  {by}")
    for k, cs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        s, a = spread(cs)
        print(f"{len(cs):>6} {s:>5} {a:>5}  {k}")
    s, a = spread(calls)
    print(f"{len(calls):>6} {s:>5} {a:>5}  total")


def print_list(rows, scan, limit, as_jsonl):
    calls = [r["call"] for r in rows]
    head = header("list", calls, scan)
    shown = rows[-limit:] if limit else rows
    if as_jsonl:
        print(head + f" shown={len(shown)}", file=sys.stderr)
        for r in shown:
            c = r["call"]
            inp = c.input
            if len(json.dumps(inp, ensure_ascii=False)) > JSONL_INPUT_MAX:
                inp = json.dumps(inp, ensure_ascii=False)[:JSONL_INPUT_MAX]
            print(json.dumps({"time": c.ts, "session": c.session, "agent": c.agent, "agent_id": c.agent_id,
                              "tool": c.tool, "input": inp, "is_error": r["is_error"],
                              "result": r["result"][:JSONL_RESULT_MAX], "hooks": r["hooks"][:JSONL_RESULT_MAX]},
                             ensure_ascii=False))
        return
    print(head + f" shown={len(shown)}")
    for r in shown:
        c = r["call"]
        print(f"{_local(c.ts, '%Y-%m-%d %H:%M')} {c.session[:8]} {c.agent} {c.tool} {summary(c.tool, c.input)}")
    if len(shown) < len(rows):
        print(f"... {len(rows) - len(shown)} older rows not shown (--limit 0 for all)")


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("count", "list"):
        p = sub.add_parser(name, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        win = p.add_mutually_exclusive_group()
        win.add_argument("--days", type=float, default=14)
        win.add_argument("--since", default=None)
        p.add_argument("--all-worktrees", action="store_true")
        p.add_argument("--agent", nargs="+", action="extend", default=None)
        p.add_argument("--tool", default=None)
        p.add_argument("--grep", default=None)
        p.add_argument("--result-grep", default=None)
        p.add_argument("--checkout", default=None)
        p.add_argument("--projects-root", default=None)
        if name == "count":
            p.add_argument("--by", choices=BY_KEYS, default="tool")
        else:
            p.add_argument("--limit", type=int, default=200)
            p.add_argument("--jsonl", action="store_true")
    a = ap.parse_args(argv)
    for flag in ("grep", "result_grep"):
        try:
            re.compile(getattr(a, flag) or "")
        except re.error as e:
            ap.error(f"--{flag.replace('_', '-')}: {e}")
    return a


def main(argv=None):
    a = parse_args(argv)
    checkout = a.checkout or main_checkout()
    # INVARIANT: the default is the main checkout's transcript dir even when run from inside a worktree;
    # project_dirs(all_worktrees=False) alone would read cwd's dir instead.
    dirs = project_dirs(None, a.all_worktrees, cwd=checkout, checkout=checkout, projects_root=a.projects_root)
    scan = Scan(a)
    scan.run([pathlib.Path(d) for d in dirs if pathlib.Path(d).is_dir()])
    rows = sorted(scan.rows, key=lambda r: r["call"].ts)
    if scan.bad_lines:
        print(f"tx: {scan.bad_lines} malformed JSON lines skipped", file=sys.stderr)
    if a.cmd == "count":
        print_count([r["call"] for r in rows], scan, a.by)
    else:
        print_list(rows, scan, max(a.limit, 0), a.jsonl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
