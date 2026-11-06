#!/usr/bin/env python3
import collections, json, os, re, sys

items = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
tools = [i for i in items if i["kind"] != "span"]
spans = [i for i in items if i["kind"] == "span"]


def intent(i):
    t, p, k = i.get("text") or "", i.get("path") or "", i["kind"]
    if k == "fileop":
        if re.search(r"(PROGRESS|REVIEW|PLAN|SPEC|RESEARCH|BACKLOG)[^/]*\.md$", p):
            return "寫任務紀錄(Edit/Write)"
        if re.search(r"(brief|fixplan|task|tasks/)[^/]*(\.md)?$", p):
            return "寫任務包(Edit/Write)"
        if re.search(r"\.md$", p):
            return "寫其他 md"
        return "改產品碼/其他檔"
    if k in ("dispatch", "othertool"):
        if k == "dispatch":
            return "派工"
        if i["tool"] == "AskUserQuestion":
            return "問 owner"
        return "Read 等"
    if k == "mcpcode" or re.search(r"(^|/)step-[^/]*\.m?js$", p):
        return "瀏覽器腳本"
    if re.search(r"mutat|突變|mutant|-overlay|wt-mut", t + p, re.I):
        return "突變驗證"
    if k in ("inline", "write", "edit") and "replace(" in t and re.search(r"assert|count\(", t) and re.search(r"go test|ng test|dockerslot", t):
        return "突變驗證"
    if re.search(r"\.jsonl|\.claude/projects|cache_read_input", t):
        return "transcript 量測"
    if re.search(r"(^|\s|/)(go test|ng test|pytest|vitest|golangci-lint|ng build|go build|go vet|just test|just lint)\b|dockerslot", t):
        return "跑閘／測試"
    if re.search(r"(FAIL|Test Files|Tests  |EXIT=|--- FAIL|exit=)", t) and re.search(r"grep|tail|cat|sed", t) and k == "cmd":
        return "讀閘輸出"
    if re.search(r"extract\.py|build_brief|build_packet|fill_rules|rulesfor|mk_?brief|-brief\.md|tasks?/", t + p):
        return "組任務包"
    if k in ("inline", "write", "edit") and re.search(r"PROGRESS|REVIEW\.md|PLAN[^ ]*\.md|SPEC\.md|BACKLOG", t) and re.search(r"write_text|open\([^)]*['\"]w|re\.sub|\.replace\(|sed -i", t):
        return "腳本改任務紀錄"
    if re.search(r"merge_back|checktree|mkpatch|mkworktree|reviewcov|wf_reports|numstat", t):
        return "流程腳本(既有)"
    if re.search(r"board\.py", t):
        return "看板"
    if re.search(r"chromium|playwright|page\.(goto|click|locator|evaluate)|walk\.|run_kit|ng serve|walk_env", t):
        return "瀏覽器腳本"
    if re.search(r"psql|INSERT INTO|createdb|CREATE DATABASE|goose", t):
        return "DB 查詢／種資料"
    if re.search(r"blind|shuffle|judge|ids-[ab]\.json|dashscope|langfuse|chat/completions", t, re.I):
        return "探針／盲評"
    if re.search(r"\bcurl\b|/api/v\d", t):
        return "API 探針"
    if re.search(r"docker (ps|rm|stop|network|kill)|testcontainers", t):
        return "清容器"
    if re.search(r"git worktree|pnpm install|ln -s|node_modules|git apply|graphify-out|cp -[rR]", t):
        return "起環境／樹"
    if re.search(r"\bgit\b", t):
        return "git"
    if k == "cmd" and re.match(r"\s*(?:[A-Z_]+=\S+;?\s*)*(?:cd \S+ ?(?:&&|;) ?)?(sed -n|grep|rg|cat|head|tail|wc|ls|find|awk|cut|od|file|stat)\b", t):
        return "讀檔／搜尋"
    if k in ("inline", "write", "edit"):
        if re.search(r"\.md|markdown|json\.load|csv", t):
            return "解析報告／資料"
        if re.search(r"\.replace\(|re\.sub|write_text|open\([^)]*['\"]w|sed -i|perl -pi", t):
            return "批次改檔"
        return "其他腳本"
    return "其他指令"


NORM = [(re.compile(r"/[\w.\-@+/]+"), "<P>"), (re.compile(r"'[^']{0,120}'"), "'S'"), (re.compile(r'"[^"]{0,120}"'), '"S"'), (re.compile(r"`[^`]{0,120}`"), "`S`"), (re.compile(r"\b\d+\b"), "N")]


def norm(l):
    l = l.strip()
    for rx, s in NORM:
        l = rx.sub(s, l)
    return l


st = collections.defaultdict(lambda: dict(n=0, kb=0.0, gen=0.0, dur=0.0, err=0, rlen=0, ag=set(), ty=collections.Counter(), scripts=0, lines=0, dup=0))
seen = {}
for i in sorted(tools, key=lambda x: x["t"]):
    k = intent(i)
    i["intent"] = k
    s = st[k]
    who = i["aid"] or i["sid"]
    s["n"] += 1
    s["kb"] += len(i.get("text") or "") / 1000
    s["gen"] += i.get("gen") or 0
    s["dur"] += min(i.get("dur") or 0, 1800)
    s["err"] += 1 if i.get("err") else 0
    s["rlen"] += i.get("rlen") or 0
    s["ag"].add(who)
    s["ty"][i["ty"]] += 1
    if i["kind"] in ("inline", "write", "edit", "mcpcode"):
        s["scripts"] += 1
        for l in (i.get("text") or "").splitlines():
            if len(l.strip()) < 14:
                continue
            nk = norm(l)
            s["lines"] += 1
            if nk in seen and seen[nk] != who:
                s["dup"] += 1
            seen.setdefault(nk, who)
print("意圖                     次數  腳本  KB   生成h 執行h  失敗% 重複%  顆數  結果MB 誰")
for k, s in sorted(st.items(), key=lambda kv: -(kv[1]["gen"])):
    print(f"{k:22}{s['n']:6}{s['scripts']:6}{s['kb']:6.0f}{s['gen']/3600:7.1f}{s['dur']/3600:6.1f}{100*s['err']/max(1,s['n']):6.0f}{100*s['dup']/max(1,s['lines']):6.0f}{len(s['ag']):6}{s['rlen']/1e6:7.1f} {s['ty'].most_common(2)}")

print("\n== 錯誤簽名 top 30（正規化）")
sig = collections.Counter()
sig_a = collections.defaultdict(set)
for i in tools:
    e = i.get("esig") or ""
    if not e:
        continue
    k = norm(e)[:90]
    sig[k] += 1
    sig_a[k].add(i["aid"] or i["sid"])
for k, v in sig.most_common(30):
    print(f"{v:5} a={len(sig_a[k]):<4} {k}")

# Changeover contents: after a tdd wave ends until next tdd starts
by = collections.defaultdict(list)
for x in spans:
    if x["ty"] == "tdd-guide":
        by[x["sid"]].append(x)
main_by = collections.defaultdict(list)
for i in tools:
    if i["ty"] == "主session":
        main_by[i["sid"]].append(i)
win = collections.Counter()
winn = collections.Counter()
windur = collections.Counter()
nwin = 0
twin = 0.0
turns = []
for sid, arr in by.items():
    arr.sort(key=lambda x: x["s"])
    busy = None
    for a in arr:
        if busy is not None and a["s"] > busy and a["s"] - busy < 3 * 3600:
            ms = [i for i in main_by.get(sid, []) if busy <= i["t"] <= a["s"]]
            ms2 = [i for i in ms if i["intent"] != "問 owner"]
            nwin += 1
            twin += a["s"] - busy
            turns.append(len(ms))
            for i in ms2:
                win[i["intent"]] += i.get("gen") or 0
                winn[i["intent"]] += 1
                windur[i["intent"]] += min(i.get("dur") or 0, 900)
        busy = max(busy or 0, a["e"])
print(f"\n== 波間換線 n={nwin} 合計 {twin/3600:.1f}h，主 session 呼叫數中位 {sorted(turns)[len(turns)//2] if turns else 0}")
for k, v in sorted(win.items(), key=lambda kv: -(kv[1] + windur[kv[0]]))[:16]:
    print(f"  {k:22} 生成 {v/60:6.0f} 分 執行 {windur[k]/60:6.0f} 分  呼叫 {winn[k]}")

# tdd-guide / verifier self-work: intents by agent type
print("\n== 每種 agent 花在非產品腳本的（每顆平均：腳本數／KB／生成分鐘）")
per = collections.defaultdict(lambda: [set(), 0, 0.0, 0.0])
for i in tools:
    if i["kind"] in ("inline", "write", "edit", "mcpcode"):
        p = per[i["ty"]]
        p[0].add(i["aid"] or i["sid"])
        p[1] += 1
        p[2] += len(i.get("text") or "") / 1000
        p[3] += i.get("gen") or 0
tot_n = collections.Counter(x["ty"] for x in spans)
for k, p in sorted(per.items(), key=lambda kv: -kv[1][3])[:10]:
    n = tot_n.get(k) or len(p[0])
    print(f"  {k:22} 顆 {n:<4} 腳本/顆 {p[1]/n:5.1f}  KB/顆 {p[2]/n:6.1f}  生成分/顆 {p[3]/60/n:5.1f}  合計生成 {p[3]/3600:5.1f}h")
tg = sum(p[3] for p in per.values())
print(f"  全部非產品腳本生成合計 {tg/3600:.1f}h；KB {sum(p[2] for p in per.values()):.0f}")

# After walk_lib: adoption since 11-06
ui = [i for i in tools if i["ty"] == "ui-lane-reviewer"]
import calendar, time
t106 = calendar.timegm((2026, 11, 6, 2, 0, 0))
post = [i for i in ui if i["t"] >= t106 and i["kind"] in ("inline", "write", "edit", "mcpcode")]
print(f"\nwalk_lib 上線後 ui-lane 腳本 {len(post)} 支，提到 walk.: {sum(1 for i in post if 'walk.' in (i.get('text') or ''))}，走 walk_lib: {sum(1 for i in post if 'walk_lib' in (i.get('text') or ''))}")
# helper defs in ui-lane scripts
FN = re.compile(r"(?:async\s+)?function\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s*)?\(")
fn = collections.defaultdict(set)
for i in ui:
    for m in FN.finditer(i.get("text") or ""):
        fn[next(x for x in m.groups() if x)].add(i["aid"])
print("ui-lane 重定義 helper:", ", ".join(f"{k}({len(v)})" for k, v in sorted(fn.items(), key=lambda kv: -len(kv[1]))[:40]))
PAT = {"視窗切換": r"setViewportSize", "鍵盤/Tab": r"keyboard\.press\('Tab|keyboard\.press\(\"Tab|activeElement", "網路攔截": r"page\.on\('(request|response)|page\.route\(|waitForResponse", "console 錯誤收集": r"page\.on\('(console|pageerror)", "scroll 量測": r"scrollTop|scrollHeight|scrollBy|scrollTo", "getBoundingClientRect": r"getBoundingClientRect", "getComputedStyle": r"getComputedStyle", "login/token": r"auth/login|signIn|storageState|access_token", "elementFromPoint": r"elementFromPoint", "動畫/transition": r"getAnimations|transitionend|prefers-reduced", "等待 sleep": r"waitForTimeout"}
print("ui-lane 腳本常見片段（幾顆 agent／幾支）:")
for k, rx in PAT.items():
    hit = [i for i in ui if i["kind"] in ("inline", "write", "edit", "mcpcode") and re.search(rx, i.get("text") or "")]
    print(f"  {k:22} {len({i['aid'] for i in hit}):3} 顆 {len(hit):5} 支")
