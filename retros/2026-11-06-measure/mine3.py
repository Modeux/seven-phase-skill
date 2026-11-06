#!/usr/bin/env python3
"""Third pass: all main-session tools, agent spans, error signatures."""
import calendar, glob, json, os, re, sys, time

sys.path.insert(0, os.path.expanduser("~/.claude/skills/seven-phase/scripts"))
from _usage import project_dirs, agent_files  # noqa: E402

DAYS = float(sys.argv[1]) if len(sys.argv) > 1 else 14
OUT = sys.argv[2]
since = time.time() - DAYS * 86400
SCRIPT_EXT = re.compile(r"\.(py|mjs|cjs|js|sh|bash|sql)$")
PRODUCT = re.compile(r"/(angular-workspace|backend-golang|ai-service|db/migrations|db/queries)/")
INLINE = re.compile(r"(<<\s*['\"]?\w+|python3?\s+(-I\s+)?-c\b|python3?\s+-\s|node\s+(-e|--input-type|-\s)|node\s+<<|bash\s+-c\b)")
ERR = re.compile(r"[^\n\"\\]{0,60}(Traceback \(most recent|SyntaxError|ModuleNotFoundError|NameError|TypeError:|ReferenceError|command not found|No such file|Cannot find module|could not determine executable|Permission denied|blocked|被擋|hook error|unexpected EOF|exit code [1-9]|Exit code [1-9]|no such table|does not exist|connection refused|ECONNREFUSED|address already in use|Timeout \d+ms exceeded|timed out)[^\n\"\\]{0,60}")


def ep(s):
    try:
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) + (int(s[20:23]) / 1000 if s[20:23].isdigit() else 0)
    except Exception:
        return None


def scan(path, sid, aid, ty, desc, out):
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    last_user = None
    start, end, ntools = {}, {}, {}
    rows, by_tool = [], {}
    first_t = last_t = None
    with fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(r, dict) or r.get("type") not in ("assistant", "user"):
                continue
            if aid is None and r.get("isSidechain"):
                continue
            t = ep(r.get("timestamp") or "")
            if t is None:
                continue
            if first_t is None:
                first_t = t
            if last_t is not None and t - last_t > 1800 and aid is not None and first_t >= since:
                out.write(json.dumps(dict(kind="span", sid=sid, aid=aid, ty=ty, desc=desc, s=first_t, e=last_t), ensure_ascii=False) + "\n")
                first_t = t
            last_t = t
            m = r.get("message") or {}
            c = m.get("content")
            if r["type"] == "user":
                last_user = t
                for b in c if isinstance(c, list) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        x = by_tool.get(b.get("tool_use_id"))
                        if x is not None:
                            s = json.dumps(b.get("content"), ensure_ascii=False)
                            mm = ERR.search(s[:8000])
                            x["err"] = bool(b.get("is_error")) or bool(mm)
                            x["esig"] = (mm.group(0) if mm else (s[:100] if b.get("is_error") else ""))[:130]
                            x["rlen"] = len(s)
                            x["dur"] = t - x["t"]
                continue
            mid = m.get("id")
            start.setdefault(mid, last_user)
            end[mid] = t
            if t < since:
                continue
            for b in c if isinstance(c, list) else []:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                ntools[mid] = ntools.get(mid, 0) + 1
                n = b.get("name")
                i = b.get("input") or {}
                base = dict(sid=sid, aid=aid, ty=ty, t=t, mid=mid, tool=n, err=None, esig="", rlen=0, dur=0, path="", text="")
                x = None
                if n in ("Write", "Edit"):
                    fp = i.get("file_path") or ""
                    s = i.get("content") if n == "Write" else i.get("new_string")
                    if s is not None and SCRIPT_EXT.search(fp) and not PRODUCT.search(fp) and "/.claude/skills/" not in fp and "/.claude/hooks/" not in fp:
                        x = dict(base, kind="write" if n == "Write" else "edit", path=fp, text=s)
                    else:
                        x = dict(base, kind="fileop", path=fp, text="", chars=len(s or ""))
                elif n == "Bash":
                    cmd = i.get("command") or ""
                    x = dict(base, kind="inline" if INLINE.search(cmd) and len(cmd) > 150 else "cmd", text=cmd)
                elif n and n.startswith("mcp__") and "run_code" in n:
                    x = dict(base, kind="mcpcode", text=i.get("code") or i.get("function") or "")
                elif aid is None:
                    x = dict(base, kind="dispatch" if n in ("Agent", "Task", "Workflow", "SendMessage") else "othertool", path=i.get("file_path") or "", text=(i.get("subagent_type") or i.get("description") or "")[:80])
                if x is not None:
                    rows.append(x)
                    by_tool[b.get("id")] = x
    if aid is not None and first_t is not None and first_t >= since:
        out.write(json.dumps(dict(kind="span", sid=sid, aid=aid, ty=ty, desc=desc, s=first_t, e=last_t), ensure_ascii=False) + "\n")
    for x in rows:
        mid = x["mid"]
        s, e = start.get(mid), end.get(mid)
        x["gen"] = (e - s) / max(1, ntools.get(mid, 1)) if s and e and 0 <= e - s < 1800 else 0
        del x["mid"]
        out.write(json.dumps(x, ensure_ascii=False) + "\n")


with open(OUT, "w") as out:
    for root in project_dirs(None, True):
        for jsonl in glob.glob(f"{root}/*.jsonl"):
            try:
                if os.path.getmtime(jsonl) < since:
                    continue
            except OSError:
                continue
            sid = os.path.basename(jsonl)[:-6]
            scan(jsonl, sid, None, "主session", "", out)
            for af in agent_files(root, sid):
                scan(af.path, sid, af.path.stem, af.meta.get("agentType") or "general-purpose", af.meta.get("description", ""), out)
print("ok")
