#!/usr/bin/env python3
"""Main-session-only view of mine3 output: command shapes, one-off scripts, checkpoint ritual, retries, task-doc reads."""
import collections, json, re, statistics, sys

items = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
main = sorted([i for i in items if i["kind"] != "span" and i["ty"] == "主session"], key=lambda x: x["t"])
print(f"main-session tool rows {len(main)} sessions {len({i['sid'] for i in main})}")

NORM = [(re.compile(r"/[\w.\-@+/]+"), "<P>"), (re.compile(r"'[^']{0,120}'"), "'S'"), (re.compile(r'"[^"]{0,120}"'), '"S"'), (re.compile(r"`[^`]{0,120}`"), "`S`"), (re.compile(r"\b\d+\b"), "N")]
SCRIPT = re.compile(r"/([\w\-]+\.(?:py|mjs|sh))\b")


def norm(l):
    l = l.strip()
    for rx, s in NORM:
        l = rx.sub(s, l)
    return l


def shape(cmd):
    """coarse key: first program + script basename (if any) + first flag."""
    c = re.sub(r"^\s*(?:[A-Z_]+=\S+\s+)*", "", cmd.strip())
    c = re.sub(r"^(?:cd \S+\s*(?:&&|;)\s*)", "", c)
    first = c.split("\n")[0]
    prog = first.split()[0] if first.split() else ""
    m = SCRIPT.search(first)
    scr = m.group(1) if m else ""
    flag = re.search(r"\s(--?[a-zA-Z][\w-]*)", first)
    sub = ""
    if prog in ("git", "docker", "just", "go", "npx", "pnpm", "graphify"):
        toks = first.split()
        sub = toks[1] if len(toks) > 1 and not toks[1].startswith("-") else ""
    return " ".join(x for x in (prog, sub, scr, flag.group(1) if flag and not scr else "") if x)


# 1. command shapes
cmds = [i for i in main if i["kind"] == "cmd"]
sh = collections.Counter()
sh_s = collections.defaultdict(set)
sh_err = collections.Counter()
for i in cmds:
    k = shape(i["text"])
    sh[k] += 1
    sh_s[k].add(i["sid"])
    if i.get("err"):
        sh_err[k] += 1
print(f"\n== main-session Bash cmd rows {len(cmds)}; top shapes (n / sessions / err%)")
for k, v in sh.most_common(60):
    print(f"  {v:5} {len(sh_s[k]):3} {100*sh_err[k]/v:4.0f}%  {k[:80]}")

# 1b. exact-ish repeated multi-session lines (normalized full command, single-line cmds only)
full = collections.Counter()
full_s = collections.defaultdict(set)
for i in cmds:
    if "\n" in i["text"].strip() or len(i["text"]) > 300:
        continue
    k = norm(i["text"])
    full[k] += 1
    full_s[k].add(i["sid"])
print("\n== repeated single-line commands (normalized) seen in >=3 sessions")
for k, v in full.most_common(200):
    if len(full_s[k]) >= 3:
        print(f"  {v:4} {len(full_s[k]):3}  {k[:120]}")

# 2. one-off scripts by main session
scripts = [i for i in main if i["kind"] in ("inline", "write", "edit")]
print(f"\n== main-session one-off scripts {len(scripts)} KB {sum(len(i['text']) for i in scripts)/1000:.0f} gen h {sum(i.get('gen') or 0 for i in scripts)/3600:.1f}")
kw = {"transcript": r"\.jsonl|\.claude/projects", "md 改寫": r"PROGRESS|REVIEW|PLAN|SPEC|BACKLOG|ACCEPTANCE", "git": r"\bgit\b", "json": r"json\.load", "status/report": r"status\.json|report", "DB": r"psql|SELECT ", "cost": r"usage|cache_read", "curl/api": r"curl|/api/", "browser": r"playwright|page\."}
kc = collections.Counter()
for i in scripts:
    for k, rx in kw.items():
        if re.search(rx, i["text"]):
            kc[k] += 1
print("  keyword hits:", dict(kc))
paths = collections.Counter()
for i in scripts:
    p = i.get("path") or ""
    if p:
        paths[re.sub(r"/[0-9a-f-]{36}/", "/<sid>/", p)] += 1
print("  written script paths (top 15):")
for p, v in paths.most_common(15):
    print(f"    {v:3} {p[-100:]}")

# 3. checkpoint ritual: window of 25 min before each board.py run (not --docsize/--hook)
bp = [i for i in cmds if "board.py" in i["text"] and "--docsize" not in i["text"] and "--hook" not in i["text"]]
print(f"\n== board.py runs (checkpoint proxy) {len(bp)} in {len({i['sid'] for i in bp})} sessions; planview runs {sum(1 for i in cmds if 'planview.py' in i['text'])}")
win_n, win_gen, win_tools = [], [], collections.Counter()
by_sid = collections.defaultdict(list)
for i in main:
    by_sid[i["sid"]].append(i)
for b in bp:
    rows = [i for i in by_sid[b["sid"]] if b["t"] - 1500 <= i["t"] < b["t"]]
    win_n.append(len(rows))
    win_gen.append(sum(i.get("gen") or 0 for i in rows))
    for i in rows:
        key = i["tool"]
        if i["kind"] == "fileop" and re.search(r"PROGRESS", i.get("path") or ""):
            key = "Edit/Write PROGRESS"
        elif i["kind"] == "fileop":
            key = "Edit/Write other"
        win_tools[key] += 1
if win_n:
    print(f"  tool calls in 25 min before board.py: median {statistics.median(win_n)} p75 {sorted(win_n)[int(len(win_n)*0.75)]}; gen min median {statistics.median(win_gen)/60:.1f}")
    print("  tools in those windows:", win_tools.most_common(10))
pe = [i for i in main if i["kind"] == "fileop" and re.search(r"PROGRESS\.md$", i.get("path") or "")]
print(f"  Edit/Write PROGRESS.md: {len(pe)} rows, chars {sum(i.get('chars') or 0 for i in pe)/1000:.0f}K, sessions {len({i['sid'] for i in pe})}")

# 4. errors & immediate retries
errs = [i for i in main if i.get("err")]
retry = 0
for sid, rows in by_sid.items():
    for a, b in zip(rows, rows[1:]):
        if a.get("err") and b["tool"] == a["tool"] and b["t"] - a["t"] <= 240:
            retry += 1
print(f"\n== main-session errors {len(errs)} ({100*len(errs)/max(1,len(main)):.0f}%), immediate same-tool retries {retry}")
sig = collections.Counter()
for i in errs:
    sig[norm(i.get("esig") or "")[:90]] += 1
for k, v in sig.most_common(20):
    print(f"  {v:4} {k}")

# 5. task-doc reads
reads = [i for i in main if i["tool"] == "Read"]
td = [i for i in reads if re.search(r"(PROGRESS|REVIEW|PLAN|SPEC|RESEARCH|BACKLOG|ROADMAP)[^/]*\.md$", i.get("path") or "")]
rr = collections.Counter((i["sid"], i.get("path")) for i in td)
print(f"\n== Read: total {len(reads)} result MB {sum(i.get('rlen') or 0 for i in reads)/1e6:.1f}; task-doc reads {len(td)} MB {sum(i.get('rlen') or 0 for i in td)/1e6:.1f}; same doc re-read in same session: {sum(v-1 for v in rr.values() if v>1)}")
rep = [i for i in reads if re.search(r"report|-brief|status\.json|\.out$", i.get("path") or "")]
print(f"  agent report/brief/out reads {len(rep)} MB {sum(i.get('rlen') or 0 for i in rep)/1e6:.1f}")
skill = [i for i in reads if "/.claude/skills/" in (i.get("path") or "")]
print(f"  skill-file reads {len(skill)} MB {sum(i.get('rlen') or 0 for i in skill)/1e6:.1f}")
sk = collections.Counter(re.sub(r".*/\.claude/skills/", "", i.get("path") or "") for i in skill)
for k, v in sk.most_common(12):
    print(f"    {v:3} {k}")
