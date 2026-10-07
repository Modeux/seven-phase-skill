#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-task progress board for seven-phase task dirs (stdlib only).

Scans .claude/PRPs/plans/*/PROGRESS.md in the main checkout and every
branch-attached worktree, picks one copy per task, computes git facts live,
writes <main checkout>/tmp/progress/board.html and prints a short summary.

  board.py                        # write HTML, print summary
  board.py --hook session-start   # write HTML, emit SessionStart hook JSON

PROGRESS.md frontmatter is the only hand-written part (checkpoint step 1):
  ---
  status: owner
  next: 一句話：下一步是什麼、誰要動
  worktree: chat-video
  ---
status ∈ owner | doing | blocked | deploy | done; worktree and phase are optional
(phase overrides the value derived from the stage table). Phase dots, commit/push
state, umbrella children and age are derived, never written.
"""
import datetime
import html
import json
import pathlib
import re
import subprocess
import sys
import time

PLANS = ".claude/PRPs/plans"
OUT = "tmp/progress/board.html"

STATUSES = (  # key, column label, blurb, colour
    ("owner", "等你", "拍板／說繼續／commit／push", "#e5484d"),
    ("doing", "做到一半", "session 中斷，從 PROGRESS「下一步」接", "#f5a524"),
    ("blocked", "卡住", "等外部：團隊討論、別的任務先合", "#8e8c99"),
    ("deploy", "等部署驗收", "已 commit，剩部署與驗收", "#30a46c"),
    ("unmarked", "未標記", "沒有 frontmatter，下次 checkpoint 補", "#6e56cf"),
    ("done", "完成", "", "#889096"),
)
STATUS_KEYS = {s[0] for s in STATUSES}

FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
# WHY: stage rows look like "| 5 TDD 實作 | ✅ …"; requiring a label after the digit keeps
# ROADMAP-style tables ("| 1 | ①a …") inside a PROGRESS from overwriting the stage table.
ROW_RE = re.compile(r"^\|\s*([1-7])\s+[^|\s][^|]*\|\s*([^|]*)\|")
NEXT_RE = re.compile(r"^## 下一步[^\n]*\n(.*?)(?=^## |\Z)", re.S | re.M)


def _git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def worktrees(cwd):
    entries, cur = [], {}
    for line in _git(cwd, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if cur:
                entries.append(cur)
            cur = {}
            continue
        key, _, val = line.partition(" ")
        cur[key] = val or True
    trees = []
    for i, w in enumerate(entries):
        path = pathlib.Path(w["worktree"])
        branch = str(w.get("branch", "")).removeprefix("refs/heads/")
        if w.get("bare") or w.get("prunable") or not path.is_dir():
            continue
        # WHY: detached trees are review/impl snapshots holding frozen copies of PROGRESS.
        if i and not branch:
            continue
        trees.append({"path": path, "branch": branch, "main": i == 0,
                      "name": "主 checkout" if i == 0 else path.name})
    return trees


def frontmatter(text):
    m = FM_RE.match(text)
    fm = {}
    for line in (m.group(1).splitlines() if m else ()):
        key, sep, val = line.partition(":")
        if sep and key.strip():
            fm[key.strip()] = val.strip().strip("\"'")
    return fm


def stage_done(cell):
    # WHY: status cells are free prose across tasks ("✅ 10-04", "**完成（…）**", "包 1 完成；包 2 完成",
    # "沿用傘狀任務"); a frontmatter `phase:` overrides when this guess is wrong.
    cell = cell.replace("**", "").strip()
    return cell.startswith("✅") or bool(re.search(r"(?<!未)完成|沿用|共用", cell))


def stage_table(text):
    done, seen = {}, False
    for line in text.splitlines():
        m = ROW_RE.match(line)
        if m:
            done[int(m.group(1))] = stage_done(m.group(2))
            seen = True
        elif seen:
            break
    return done


def clean(s):
    s = re.sub(r"\*\*|`", "", s)
    return re.sub(r"\s+", " ", s).strip()


def first_next(text):
    m = NEXT_RE.search(text)
    for line in (m.group(1).splitlines() if m else ()):
        s = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
        if s:
            return clean(s)
    return ""


def dirty_slugs(tree):
    out = set()
    for line in _git(tree, "status", "--porcelain", "--untracked-files=all", "--", PLANS).splitlines():
        parts = line[3:].split(" -> ")[-1].strip('"').split("/")
        if len(parts) > 3:
            out.add(parts[3])
    return out


def commit_ts(tree):
    ts, cur = {}, 0
    for line in _git(tree, "log", "--format=@%ct", "--name-only", "--", PLANS).splitlines():
        if line.startswith("@"):
            cur = int(line[1:])
        elif line and line not in ts:
            ts[line] = cur
    return ts


def pick(copies):
    pool = [c for c in copies if c["text"]] or copies
    for c in pool:
        owner = c["fm"].get("worktree")
        for d in pool:
            if owner and owner in (d["tree"]["name"], d["tree"]["branch"]):
                return d
    return max(pool, key=lambda c: (c["dirty"], c["ts"], c["tree"]["main"]))


def git_state(slug, commits, base_set, up_set, base):
    pat = re.compile(rf"(?<![A-Za-z0-9-]){re.escape(slug)}(?![A-Za-z0-9-])")
    subjects = {}
    for h, s in commits:
        if pat.search(s):
            subjects.setdefault(s, set()).add(h)
    # WHY: pull.rebase=true and keep/* backups re-create the same commit under new hashes;
    # judging per subject stops those duplicates from reading as unmerged work.
    if not subjects:
        return "none", "未 commit"
    if up_set and all(hs & up_set for hs in subjects.values()):
        return "pushed", "已 push"
    if all(hs & base_set for hs in subjects.values()):
        return "local", "已 commit · 未 push"
    return "branch", f"有 commit 未合進 {base}"


def age(ts):
    if not ts:
        return "?"
    days = int((time.time() - ts) // 86400)
    return "今天" if days <= 0 else f"{days} 天前"


def collect(cwd):
    trees = worktrees(cwd)
    # INVARIANT: the SessionStart hook is user-level (fires in every project); without task dirs,
    # return None so nothing gets written into repos that don't use this workflow.
    if not trees or not any((t["path"] / PLANS).is_dir() for t in trees):
        return None
    main = trees[0]["path"]
    base = trees[0]["branch"] or "HEAD"
    upstream = _git(main, "rev-parse", "--abbrev-ref", f"{base}@{{u}}").strip()
    behind, ahead = (_git(main, "rev-list", "--left-right", "--count", f"{upstream}...{base}").split() or ["0", "0"]) if upstream else ("0", "0")
    base_set = set(_git(main, "rev-list", base).split())
    up_set = set(_git(main, "rev-list", upstream).split()) if upstream else set()
    log = _git(main, "log", "--exclude=keep/*", "--branches", "--remotes", "--format=%H\t%s")
    commits = [tuple(line.split("\t", 1)) for line in log.splitlines() if "\t" in line]

    copies = {}
    for t in trees:
        t["ahead"] = _git(main, "rev-list", "--count", f"{base}..{t['branch']}").strip() if not t["main"] and t["branch"] else ""
        t["changes"] = sum(1 for line in _git(t["path"], "status", "--porcelain").splitlines() if line)
        root = t["path"] / PLANS
        if not root.is_dir():
            continue
        dirty, ts = dirty_slugs(t["path"]), commit_ts(t["path"])
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            prog = d / "PROGRESS.md"
            text = prog.read_text(encoding="utf-8") if prog.is_file() else ""
            is_dirty = d.name in dirty
            stamp = prog.stat().st_mtime if is_dirty and prog.is_file() else ts.get(f"{PLANS}/{d.name}/PROGRESS.md", 0)
            copies.setdefault(d.name, []).append({"tree": t, "dir": d, "path": prog, "text": text,
                                                  "fm": frontmatter(text), "dirty": is_dirty, "ts": stamp})

    tasks, copies_text = {}, {}
    for slug, cs in copies.items():
        c = pick(cs)
        copies_text[slug] = c["text"]
        fm, done = c["fm"], stage_table(c["text"])
        lean = "分流：精簡路" in c["text"]
        if fm.get("phase"):
            phase = fm["phase"]
        elif done:
            phase = f"{sum(done.values())}/7"
        else:
            phase = "精簡路" if lean else ("無 PROGRESS" if not c["text"] else "階段?")
        roadmap = c["dir"] / "ROADMAP.md"
        tasks[slug] = {
            "slug": slug, "fm": fm, "done": done, "lean": lean, "phase": phase,
            "status": fm.get("status") if fm.get("status") in STATUS_KEYS else "unmarked",
            "next": clean(fm.get("next", "")) or first_next(c["text"]),
            "tree": "" if c["tree"]["main"] else c["tree"]["name"],
            "path": c["path"], "dirty": c["dirty"], "ts": c["ts"], "age": age(c["ts"]),
            "git": git_state(slug, commits, base_set, up_set, base),
            "children": re.findall(r"`([a-z0-9][a-z0-9-]+)`", roadmap.read_text(encoding="utf-8")) if roadmap.is_file() else [],
        }
    claims = {}
    for t in tasks.values():
        for s in dict.fromkeys(t["children"]):
            if s in tasks and s != t["slug"]:
                claims.setdefault(s, []).append(t["slug"])
    for t in tasks.values():
        t["children"] = []
    for s, umbrellas in claims.items():
        # WHY: ROADMAPs cross-reference other umbrellas' children ("不屬於本包"); the umbrella a
        # child names first in its own PROGRESS (its header line) is its real parent.
        text = copies_text[s]
        parent = min(umbrellas, key=lambda u: text.find(u) if u in text else len(text))
        tasks[s]["parent"] = parent
        tasks[parent]["children"].append(s)
    for t in tasks.values():
        if t["children"]:
            fin = sum(1 for s in t["children"] if tasks[s]["status"] in ("deploy", "done")
                      or (len(tasks[s]["done"]) == 7 and all(tasks[s]["done"].values())))
            t["phase"] = f"傘狀 {fin}/{len(t['children'])}"
            # WHY: umbrella records ride along in their children's docs(prps) commits, so a slug
            # search over subjects says nothing about them; the dirty chip still shows unsaved docs.
            t["git"] = None

    return {"main": main, "base": base, "upstream": upstream, "ahead": ahead, "behind": behind,
            "trees": trees, "when": datetime.datetime.now().strftime("%m-%d %H:%M"),
            "tasks": sorted(tasks.values(), key=lambda t: -t["ts"])}


def clip(s, n):
    return s if len(s) <= n else s[: n - 1] + "…"


def render_text(b, out):
    wts = "、".join(f"{t['name']}（領先 {b['base']} {t['ahead'] or 0}、{t['changes']} 個未 commit 改動）"
                   for t in b["trees"] if not t["main"])
    lines = [f"進度看板 {b['when']} · {b['base']} 領先 {b['upstream'] or '上游'} {b['ahead']}、落後 {b['behind']}"
             + (f" · worktree：{wts}" if wts else ""), f"HTML：{out}"]
    for key, label, _, _ in STATUSES:
        group = [t for t in b["tasks"] if t["status"] == key]
        if not group:
            continue
        if key == "done":
            lines.append(f"■ 完成 {len(group)}")
            continue
        # WHY: this text is injected into every session's context; deploy-stage tasks need no
        # action from the agent, so they get one line of slugs and the details stay in the HTML.
        if key == "deploy":
            lines.append(f"■ {label} {len(group)}：" + "、".join(t["slug"] for t in group))
            continue
        lines.append(f"■ {label} {len(group)}")
        for t in group:
            tree = f" [{t['tree']}]" if t["tree"] else ""
            git = f" · {t['git'][1]}" if t["git"] else ""
            lines.append(f"  - {t['slug']}{tree} {t['phase']}{git} · {t['age']}：{clip(t['next'], 80)}")
    return "\n".join(lines)


def headline(b):
    counts = [f"{label} {n}" for key, label, _, _ in STATUSES
              if key != "done" and (n := sum(1 for t in b["tasks"] if t["status"] == key))]
    return "進度看板：" + (" · ".join(counts) or "沒有進行中的任務") + f" → {OUT}"


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--mute:#667085;--line:#e4e7ec}
@media (prefers-color-scheme:dark){:root{--bg:#14161a;--card:#1d2026;--fg:#e6e8ec;--mute:#98a2b3;--line:#2c3038}}
*{box-sizing:border-box}
body{margin:0;font:14px/1.55 -apple-system,"PingFang TC","Noto Sans TC",sans-serif;background:var(--bg);color:var(--fg)}
.top{padding:16px 24px;border-bottom:1px solid var(--line)}
.top h1{font-size:18px;margin:0 0 4px}
.meta{color:var(--mute);font-size:12px;display:flex;flex-wrap:wrap;gap:6px 14px}
.board{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px;padding:16px 24px;align-items:start}
.col>h2{font-size:14px;margin:0 0 2px;display:flex;align-items:center;gap:8px}
.col>h2 i{width:10px;height:10px;border-radius:50%;background:var(--c);display:inline-block}
.col>p{margin:0 0 10px;color:var(--mute);font-size:12px}
.card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--c);border-radius:8px;padding:10px 12px;margin-bottom:10px}
.card h3{font-size:14px;margin:0;display:flex;flex-wrap:wrap;gap:6px;align-items:baseline}
.card h3 a{color:var(--fg);text-decoration:none}.card h3 a:hover{text-decoration:underline}
.dots{display:flex;gap:4px;align-items:center;margin:6px 0 2px;font-size:12px;color:var(--mute)}
.dot{width:11px;height:11px;border-radius:50%;border:1.5px solid var(--c)}
.dot.on{background:var(--c)}.dot.cur{outline:2px solid var(--c);outline-offset:1px}
.next{margin:6px 0;display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;overflow:hidden}
.chips{display:flex;flex-wrap:wrap;gap:6px;font-size:12px;color:var(--mute)}
.chip{border:1px solid var(--line);border-radius:999px;padding:0 8px;white-space:nowrap}
.chip.warn{border-color:#f5a524;color:#b4690e}.chip.ok{border-color:#30a46c;color:#218358}
.kids{margin:8px 0 0;padding:0;list-style:none;font-size:12px}
.kids li{display:flex;gap:6px;align-items:center}.kids i{width:8px;height:8px;border-radius:50%;background:var(--k)}
details>summary{cursor:pointer;color:var(--mute);font-size:13px;margin-bottom:8px}
"""


def _colour(key):
    return next(s[3] for s in STATUSES if s[0] == key)


def render_card(t, tasks):
    e = html.escape
    dots = ""
    if t["done"] and not t["children"]:
        cur = next((n for n in range(1, 8) if not t["done"].get(n)), 0)
        dots = "".join(f'<span class="dot{" on" if t["done"].get(n) else ""}{" cur" if n == cur else ""}" title="第 {n} 階段"></span>'
                       for n in range(1, 8))
    chips = [f'<span class="chip">{e(t["age"])}更新</span>']
    if t["git"]:
        git_cls = {"pushed": "ok", "local": "warn", "branch": "warn"}.get(t["git"][0], "")
        chips.insert(0, f'<span class="chip {git_cls}">{e(t["git"][1])}</span>')
    if t["dirty"]:
        chips.append('<span class="chip warn">記錄未 commit</span>')
    if t.get("parent"):
        chips.append(f'<span class="chip">↳ {e(t["parent"])}</span>')
    kids = ""
    if t["children"]:
        kids = '<ul class="kids">' + "".join(
            f'<li><i style="--k:{_colour(tasks[s]["status"])}"></i>{e(s)} <span class="chip">{e(tasks[s]["phase"])}</span></li>'
            for s in t["children"]) + "</ul>"
    tree = f'<span class="chip">{e(t["tree"])}</span>' if t["tree"] else ""
    return (f'<article class="card" style="--c:{_colour(t["status"])}">'
            f'<h3><a href="{e(t["path"].as_uri())}">{e(t["slug"])}</a>{tree}</h3>'
            f'<div class="dots">{dots}<span>{e(t["phase"])}</span></div>'
            f'<p class="next" title="{e(t["next"])}">{e(t["next"]) or "（沒有下一步）"}</p>'
            f'<div class="chips">{"".join(chips)}</div>{kids}</article>')


def render_html(b):
    e = html.escape
    tasks = {t["slug"]: t for t in b["tasks"]}
    cols = []
    for key, label, blurb, colour in STATUSES:
        group = [t for t in b["tasks"] if t["status"] == key]
        if not group and key in ("doing", "unmarked", "done"):
            continue
        cards = "".join(render_card(t, tasks) for t in group) or '<p class="meta">（無）</p>'
        if key == "done":
            cards = f"<details><summary>展開 {len(group)} 個</summary>{cards}</details>"
        cols.append(f'<section class="col" style="--c:{colour}"><h2><i></i>{e(label)} {len(group)}</h2>'
                    f'<p>{e(blurb)}</p>{cards}</section>')
    trees = "".join(f'<span>{e(t["name"])}：{e(t["branch"])}，領先 {e(b["base"])} {e(t["ahead"] or "0")}，{t["changes"]} 個未 commit 改動</span>'
                    for t in b["trees"] if not t["main"])
    return (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta http-equiv="refresh" content="60"><title>進度看板</title><style>{CSS}</style></head><body>'
            f'<div class="top"><h1>進度看板</h1><div class="meta"><span>產生於 {e(b["when"])}（每 60 秒重讀檔案；內容在 session 開始與 checkpoint 時重算）</span>'
            f'<span>{e(b["base"])} 領先 {e(b["upstream"] or "上游")} {e(b["ahead"])}、落後 {e(b["behind"])}</span>{trees}</div></div>'
            f'<main class="board">{"".join(cols)}</main></body></html>')


def main(argv):
    hook = "--hook" in argv
    try:
        b = collect(pathlib.Path.cwd())
        if b is None:
            if not hook:
                print(f"board.py: no {PLANS}/ in this repo or its worktrees", file=sys.stderr)
            return 0 if hook else 1
        out = b["main"] / OUT
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(b), encoding="utf-8")
        text = render_text(b, out)
    except Exception:
        # INVARIANT: a SessionStart hook must never block or break a session.
        if hook:
            return 0
        raise
    if hook:
        context = text + "\n續接任務：owner 說「繼續 <slug>」→ 載入 seven-phase skill，讀該任務 PROGRESS「下一步」（標了 [worktree] 的在該 worktree 的 .claude/PRPs/plans/<slug>/）。"
        print(json.dumps({"systemMessage": headline(b),
                          "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}},
                         ensure_ascii=False))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
