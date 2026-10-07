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
LEDGER = "tmp/progress/ledger.json"
BACKLOG = ".claude/PRPs/BACKLOG.md"
BACKLOG_GROUPS = (("重要", "#e5484d"), ("有感", "#f5a524"), ("其餘", "#889096"), ("已知限制", "#8e8c99"))

STATUSES = (  # key, column label, blurb, colour
    ("owner", "等你", "拍板／說繼續／commit／push", "#e5484d"),
    ("doing", "做到一半", "session 中斷，從 PROGRESS「下一步」接", "#f5a524"),
    ("blocked", "卡住", "等外部：團隊討論、別的任務先合", "#8e8c99"),
    ("deploy", "等部署驗收", "已 commit，剩部署與驗收", "#30a46c"),
    ("unmarked", "未標記", "沒有 frontmatter，下次 checkpoint 補", "#6e56cf"),
    ("done", "完成", "", "#889096"),
)
STATUS_KEYS = {s[0] for s in STATUSES}
GONE_LABEL, GONE_COLOUR = "目錄已刪・待部署", "#0091ff"

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


def commit_info(tree):
    """path -> (commit time, short hash) of the last commit that touched it."""
    info, cur = {}, (0, "")
    for line in _git(tree, "log", "--format=@%ct %h", "--name-only", "--", PLANS).splitlines():
        if line.startswith("@"):
            ct, _, h = line[1:].partition(" ")
            cur = (int(ct), h)
        elif line and line not in info:
            info[line] = cur
    return info


def sync_ledger(main, tasks, state_of, deployed=()):
    """Snapshot every live task that isn't done; a task whose dir is gone stays until marked deployed."""
    path = main / LEDGER
    try:
        led = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        led = {}
    now = time.time()
    for slug, t in tasks.items():
        if t["status"] == "done":
            led.pop(slug, None)
            continue
        prev = led.get(slug, {})
        led[slug] = {"status": t["status"], "next": t["next"], "phase": t["phase"], "parent": t.get("parent", ""),
                     "umbrella": bool(t["children"]), "tree": t["tree"], "rel": t["rel"],
                     "record": t["record"] or prev.get("record", ""), "section": t["section"],
                     "first_seen": prev.get("first_seen", now), "last_seen": now}
    removed = [s for s in list(led) if s not in tasks and ("all" in deployed or s in deployed)]
    for s in removed:
        del led[s]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(led, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    newest_first = sorted(led.items(), key=lambda kv: kv[1]["last_seen"], reverse=True)
    gone = [dict(e, slug=s, git=None if e["umbrella"] else state_of(s), age=age(e["last_seen"]))
            for s, e in newest_first if s not in tasks]
    return gone, removed


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


def read_backlog(main):
    """Open items (`- [ ]`) of BACKLOG.md grouped by `## ` heading; indented lines continue an item."""
    path = main / BACKLOG
    if not path.is_file():
        return None
    groups = {g: [] for g, _ in BACKLOG_GROUPS}
    cur = item = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            cur = next((g for g, _ in BACKLOG_GROUPS if line[3:].strip().startswith(g)), None)
            item = None
        elif line.startswith("- [ ]") and cur:
            item = {"raw": line[5:].strip()}
            groups[cur].append(item)
        elif item is not None and line.startswith("  ") and line.strip():
            item["raw"] += " " + line.strip()
        else:
            item = None
    for items in groups.values():
        for it in items:
            m = re.search(r"\*\*(.+?)\*\*", it["raw"])
            rest = it["raw"][m.end():] if m else it["raw"]
            body, _, status = rest.partition("｜狀態：")
            body, _, src = body.partition("出處：")
            it.update(title=m.group(1) if m else clip(clean(rest), 40), body=clean(body).lstrip("—-– ").strip(),
                      src=clean(src), status=clean(status) or "未開")
    return groups


def collect(cwd, deployed=()):
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
        dirty, info = dirty_slugs(t["path"]), commit_info(t["path"])
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            prog = d / "PROGRESS.md"
            rel = f"{PLANS}/{d.name}/PROGRESS.md"
            text = prog.read_text(encoding="utf-8") if prog.is_file() else ""
            is_dirty = d.name in dirty
            ct, record = info.get(rel, (0, ""))
            stamp = prog.stat().st_mtime if is_dirty and prog.is_file() else ct
            copies.setdefault(d.name, []).append({"tree": t, "dir": d, "path": prog, "rel": rel, "record": record,
                                                  "text": text, "fm": frontmatter(text), "dirty": is_dirty, "ts": stamp})

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
            "path": c["path"], "rel": c["rel"], "record": c["record"],
            "dirty": c["dirty"], "ts": c["ts"], "age": age(c["ts"]),
            "section": (m.group(1).strip()[:6000] if (m := NEXT_RE.search(c["text"])) else ""),
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

    # WHY: the owner deletes task dirs when pushing to dev, but deploys later in batches; the
    # ledger keeps each deleted task's deploy notes visible until the owner says it's deployed.
    gone, removed = sync_ledger(main, tasks, lambda s: git_state(s, commits, base_set, up_set, base), deployed)
    return {"main": main, "base": base, "upstream": upstream, "ahead": ahead, "behind": behind,
            "trees": trees, "when": datetime.datetime.now().strftime("%m-%d %H:%M"),
            "tasks": sorted(tasks.values(), key=lambda t: -t["ts"]), "gone": gone, "removed": removed,
            "still": [s for s in deployed if s in tasks], "backlog": read_backlog(main)}


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
    if b["gone"]:
        lines.append(f"■ {GONE_LABEL} {len(b['gone'])}：" + "、".join(g["slug"] for g in b["gone"]))
    if b["backlog"]:
        lines.append(f"■ Backlog（{BACKLOG}）：" + "・".join(f"{g} {len(b['backlog'][g])}" for g, _ in BACKLOG_GROUPS))
        # WHY: injected into every session; the top of 重要 (owner-ranked first) is enough to steer, the rest is in the HTML.
        top = b["backlog"]["重要"]
        lines += [f"  - [重要] {it['title']}｜{clip(it['status'], 30)}" for it in top[:8]]
        if len(top) > 8:
            lines.append(f"  - …重要另 {len(top) - 8} 條見 HTML／{BACKLOG}")
    return "\n".join(lines)


def headline(b):
    counts = [f"{label} {n}" for key, label, _, _ in STATUSES
              if key != "done" and (n := sum(1 for t in b["tasks"] if t["status"] == key))]
    if b["gone"]:
        counts.append(f"{GONE_LABEL} {len(b['gone'])}")
    if b["backlog"]:
        counts.append(f"backlog 重要 {len(b['backlog']['重要'])}")
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
code{font:12px ui-monospace,Menlo,monospace;word-break:break-all}
.sect{border-top:1px solid var(--line);margin-top:8px}
pre.sec{white-space:pre-wrap;font:12px/1.5 ui-monospace,Menlo,monospace;max-height:320px;overflow:auto;margin:0;padding:8px;border:1px solid var(--line);border-radius:6px}
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


def render_gone(g):
    e = html.escape
    label = next((s[1] for s in STATUSES if s[0] == g["status"]), g["status"])
    chips = [f'<span class="chip">刪前：{e(label)}</span>', f'<span class="chip">{e(g["age"])}最後看到</span>']
    if g["git"]:
        chips.insert(0, f'<span class="chip {"ok" if g["git"][0] == "pushed" else "warn"}">{e(g["git"][1])}</span>')
    if g["parent"]:
        chips.append(f'<span class="chip">↳ {e(g["parent"])}</span>')
    recover = (f'<p class="meta">全文：<code>git show {e(g["record"])}:{e(g["rel"])}</code></p>' if g["record"]
               else '<p class="meta">PROGRESS 從未 commit，只剩下面的快照</p>')
    section = (f'<details><summary>刪除前的「下一步」</summary><pre class="sec">{e(g["section"])}</pre></details>'
               if g["section"] else "")
    return (f'<article class="card" style="--c:{GONE_COLOUR}"><h3>{e(g["slug"])}</h3>'
            f'<div class="dots"><span>{e(g["phase"])}</span></div>'
            f'<p class="next" title="{e(g["next"])}">{e(g["next"]) or "（沒有下一步）"}</p>'
            f'<div class="chips">{"".join(chips)}</div>{recover}{section}</article>')


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
        if key == "deploy" and b["gone"]:
            cols.append(f'<section class="col" style="--c:{GONE_COLOUR}"><h2><i></i>{GONE_LABEL} {len(b["gone"])}</h2>'
                        f'<p>push 時刪了目錄、還沒部署；部署完跑 <code>board.py --deployed &lt;slug…|all&gt;</code> 清掉</p>'
                        + "".join(render_gone(g) for g in b["gone"]) + "</section>")
    trees = "".join(f'<span>{e(t["name"])}：{e(t["branch"])}，領先 {e(b["base"])} {e(t["ahead"] or "0")}，{t["changes"]} 個未 commit 改動</span>'
                    for t in b["trees"] if not t["main"])
    return (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta http-equiv="refresh" content="60"><title>進度看板</title><style>{CSS}</style></head><body>'
            f'<div class="top"><h1>進度看板</h1><div class="meta"><span>產生於 {e(b["when"])}（每 60 秒重讀檔案；內容在 session 開始與 checkpoint 時重算）</span>'
            f'<span>{e(b["base"])} 領先 {e(b["upstream"] or "上游")} {e(b["ahead"])}、落後 {e(b["behind"])}</span>{trees}</div></div>'
            f'<main class="board">{"".join(cols)}</main>{render_backlog(b["backlog"])}</body></html>')


def render_backlog(groups):
    if groups is None:
        return ""
    e = html.escape
    cols = []
    for g, colour in BACKLOG_GROUPS:
        cards = []
        for it in groups[g]:
            s = it["status"]
            cls = "ok" if s.startswith("任務") else ("warn" if s.startswith("未驗證") else "")
            src = f'<p class="meta">出處：<code>{e(it["src"])}</code></p>' if it["src"] else ""
            cards.append(f'<article class="card" style="--c:{colour}"><h3>{e(it["title"])}</h3>'
                         f'<p class="next" title="{e(it["body"])}">{e(it["body"])}</p>'
                         f'<div class="chips"><span class="chip {cls}">{e(s)}</span></div>{src}</article>')
        cols.append(f'<section class="col" style="--c:{colour}"><h2><i></i>{e(g)} {len(groups[g])}</h2>'
                    + ("".join(cards) or '<p class="meta">（無）</p>') + "</section>")
    return (f'<div class="top sect"><h1>Backlog</h1><div class="meta"><span>真相源 <code>{e(BACKLOG)}</code>：'
            f'不在任務目錄裡，push 刪目錄不會動到；要開任務、做完或改優先序，跟 Claude 說</span></div></div>'
            f'<main class="board">{"".join(cols)}</main>')


def main(argv):
    hook = "--hook" in argv
    deployed = argv[argv.index("--deployed") + 1:] if "--deployed" in argv else []
    try:
        b = collect(pathlib.Path.cwd(), deployed)
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
        if b["gone"]:
            context += f"\n{GONE_LABEL}的部署要點在 HTML 與 {LEDGER}；owner 說部署完了 → 先確認是哪幾個，再跑 board.py --deployed <slug…|all>。"
        print(json.dumps({"systemMessage": headline(b),
                          "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}},
                         ensure_ascii=False))
    else:
        print(text)
        if b["removed"]:
            print("已清掉（部署完）：" + "、".join(b["removed"]))
        if b["still"]:
            print("目錄還在、沒清：" + "、".join(b["still"]) + "（先刪目錄或把 status 改成 done）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
