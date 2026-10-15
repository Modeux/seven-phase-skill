# -*- coding: utf-8 -*-
"""What each review lane bought: findings, unique findings, valid ones, and what the lane cost.

Usage:
  python3 laneyield.py <slug> [<slug> ...]       # reads .claude/PRPs/plans/<slug>/REVIEW*.md from cwd's repo
                                                 # (lean tasks without one: that slug's PROGRESS.md §審查)
  python3 laneyield.py --review path/to/REVIEW.md --slug <slug>
  python3 laneyield.py --review path/to/PROGRESS.md --slug <slug>
  python3 laneyield.py <slug> --project-dir ~/.claude/projects/<dir>

Columns are found by header name, never by position (exact name, else prefix, else substring:
`嚴重度（宣稱→裁決）` is a severity column, not 裁決).
REVIEW*.md: dedup map = tables whose header has a 來源 column and whose rows start with ids (under a
heading containing 地圖 when the doc has one; there any capital prefix is an id — N1, NL3 — else only R/L).
Lanes come from the 來源 cell only: `<lane>#<n>` ids when the cell has any (plus bare words naming a lane
of the review dir: an acked lane's hand-merged finding), else lane words (`spec(M)、impact(M) — 3`, older
maps). Ids other than R/L take their class from the 宣稱嚴重度 cell. Tables under a heading containing
收斂裁決 with a 裁決 column are verdicts (`| R1 | confirmed… |`, `| L2、L4 | … |`, `| R6 title | … |`); a
最終嚴重度 column there sets the class (parentheses are notes: `LOW（原 HIGH）` → L; after an arrow, the
last state). Several rounds in one doc (one 地圖 each) keep their own ids (`R1@2`); a verdict table joins
the round sharing the longest heading-path prefix. Pre-table list maps (`**R1【HIGH】…**` ＋ `- 來源：`) are
read when their 來源 lines use <lane>#<n>. Fenced code blocks are ignored; `\\|` does not split a cell.
PROGRESS.md (lean tasks): rows of a table under a 審查 heading whose header has both 來源 and 裁決.

Data problems are printed under the table, never dropped silently: no map found, a row whose 來源 names
no lane, a lane word that is not a lane, duplicate map ids, a map-like table outside the 地圖 heading,
no verdict table at all, a verdict for an id the map lacks, R verdicts unrecognized／pending／missing.
--strict exits 1 on any of them; --no-cost skips the transcript scan.

Lane cost: a Workflow agent labelled `lane-<x>` whose dispatch names the slug is lane x. An ordinary
subagent is the lane whose `tmp/<slug>/review/lane-<name>.md` its dispatch prompt names (else the one it
wrote). Searches the main checkout and all its worktrees unless --project-dir is given; external CLIs
(codex／grok／agy) are not in transcripts and show "-".

unique = only this lane reported it. Verdicts (parentheses dropped, then the part after the last →, then
the leading word, else the first clause): valid = confirmed／成立／部分／won't fix／不修／收／owner (won't fix
is "valid, not fixed in this package"; a finding the owner had to rule on counts as worth raising);
refuted = false positive／fp／誤報／不成立／不收; unver = R findings still unverified／未驗證; pending =
待驗證／待裁決／需拍板／修片確認…; unver and pending are neither valid nor refuted.
"""
import argparse
import collections
import glob
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _usage import agent_files, cost_of, project_dirs, quality_line, read_messages  # noqa: E402
from _common import toplevel  # noqa: E402
from _lanes import expected_lanes, lane_name  # noqa: E402
from _mdtable import id_prefix, map_tables, pick_col, row_ids, tables, unfenced  # noqa: E402

_LANE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
# WHY: ASCII lookbehind — `\w` matches CJK, so `go#2和ts#1` used to drop ts.
_SRC_ID = re.compile(r"(?<![A-Za-z0-9_-])([a-z][a-z0-9]*(?:-[a-z0-9]+)*)#\d+")
_SRC_ID_FULL = re.compile(r"(?<![A-Za-z0-9_-])[a-z][a-z0-9]*(?:-[a-z0-9]+)*#\d+")
_PAREN = re.compile(r"[（(][^（）()]*[）)]")
_STOP = {"vs", "and", "or", "see", "main", "session"}
_COUNT = re.compile(r"(?:—|-{1,2}|（|\()\s*\d+\s*(?:）|\))?\s*$")
_SEVERITY = re.compile(r"CRITICAL|HIGH|MEDIUM|LOW", re.IGNORECASE)
# INVARIANT: pending and negative forms are tried before positive ones (待驗證＋owner, 不收 vs 收, 未成立 vs 成立).
_PENDING = re.compile(r"(?:pending|tbd|待驗證|待裁決|待拍板|需\s*(?:owner\s*)?拍板|待\s*owner|needs?\s+owner"
                      r"|待確認|修片確認|不確定)")
_REFUTED = re.compile(r"(?:false\s*positive|fp\b|誤報|(?:不|未|沒|無法|不算)成立|不收|未收)")
_UNVERIFIED = re.compile(r"(?:unverified|未驗證)")
_VALID = re.compile(r"(?:confirmed|成立|部分|won.?t\s*fix|不修|待修|收|owner)")
_VERDICT_ORDER = (("pending", _PENDING), ("refuted", _REFUTED), ("unverified", _UNVERIFIED), ("valid", _VALID))


def lanes_of(cell):
    cell = _COUNT.sub("", cell.strip())
    cell = _PAREN.sub("", cell)
    out = []
    for part in re.split(r"[、；;,，/]|\s+", cell):
        # WHY: older maps wrote sources as <lane>:U1; credit the lane.
        m = _LANE.fullmatch(re.sub(r"(?:#\d+|:[A-Za-z]*\d+)$", "", part.strip().strip("*")))
        if m and m.group(0) not in out and m.group(0) not in _STOP:
            out.append(m.group(0))
    return out


def source_lanes(cell, known=()):
    """(lanes, by_id, dropped). With `<lane>#<n>` ids in the cell, the lanes are those ids plus bare words
    naming a known lane (an acked lane's hand-merged finding has no id: `grok(HIGH)`); other bare words are
    `dropped` for the caller to report. Without ids: lane words (older maps), by_id False."""
    ids = list(dict.fromkeys(_SRC_ID.findall(cell)))
    if not ids:
        return lanes_of(cell), False, []
    bare = [w for w in lanes_of(_SRC_ID_FULL.sub(" ", cell)) if w not in ids]
    return ids + [w for w in bare if w in known], True, [w for w in bare if w not in known]


def severity_class(text):
    """R／L of a severity cell: parentheses are notes (`LOW（原 HIGH）` → L), an arrow gives the state after it
    (`HIGH／MEDIUM→LOW` → L), otherwise the first severity word; None when there is none."""
    t = _PAREN.sub("", text or "")
    t = t.rsplit("→", 1)[1] if "→" in t and _SEVERITY.search(t.rsplit("→", 1)[1]) else t
    found = _SEVERITY.findall(t) or _SEVERITY.findall(text or "")
    if not found:
        return None
    return "L" if found[0].upper() == "LOW" else "R"


def _leading_class(v):
    for cls, pat in _VERDICT_ORDER:
        if pat.match(v):
            return cls
    return None


def verdict_class(verdict):
    """'valid' | 'refuted' | 'unverified' | 'pending' | None (unrecognized)."""
    v = _PAREN.sub("", (verdict or "").strip().strip("*`")).strip().strip("*`").strip().lower()
    # WHY: 需拍板→owner 拍板 records a transition, so the state after the last arrow wins when it is a
    # verdict word; owner 拍板：「a」→「b」 has an arrow inside the ruling, so it falls back to the whole cell.
    after = v.rsplit("→", 1)[1].strip().strip("*`").strip() if "→" in v else ""
    found = (after and _leading_class(after)) or _leading_class(v)
    if found:
        return found
    clause = re.split(r"[；;。，,：:＋+]", v, maxsplit=1)[0]
    for cls, pat in _VERDICT_ORDER:
        if pat.search(clause):
            return cls
    return None


def _cell(cells, i):
    return cells[i] if i is not None and i < len(cells) else ""


def _scope(headings, word, include=False):
    """Heading titles down to the first heading containing `word` (that one too when include=True): the
    review round a map／verdict table belongs to. Maps include their 地圖 heading because the round name
    can live in it (`## 包 2 去重地圖`)."""
    out = []
    for _, title in headings:
        if word in title:
            return tuple(out + [title]) if include else tuple(out)
        out.append(title)
    return tuple(out)


def _common(a, b):
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n


_LIST_HEAD = re.compile(r"^(?:\*\*|#{2,6}\s+)((?:R|L)\d+)\s*[【\[]")
_LIST_SRC = re.compile(r"^\s*[-*]\s*來源\s*[：:]\s*(.*)$")
_MD_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")


def _list_map(text, issues, known):
    """Pre-table maps (before 10-05): `**R1【HIGH】title**` blocks under a 地圖 heading, each with a
    `- 來源：` line. Read only when the 來源 lines use <lane>#<n> ids; prose sources are not guessed at."""
    entries, map_level, seen, cur = [], None, {}, None
    for lineno, line in enumerate(unfenced(text).splitlines(), 1):
        h, head = _MD_HEAD.match(line), _LIST_HEAD.match(line)
        if h and not head:
            level = len(h.group(1))
            if "地圖" in h.group(2):
                map_level = level
            elif map_level is not None and level <= map_level:
                map_level = None
            cur = None
            continue
        if map_level is None:
            continue
        if head:
            rid = head.group(1)
            if rid in seen:
                issues.append(("error", f"duplicate map id {rid} (line {lineno}; first at line {seen[rid]})"))
                cur = None
                continue
            seen[rid] = lineno
            cur = {"scope": (), "rid": rid, "line": lineno, "src": None}
            entries.append(cur)
        elif cur is not None and cur["src"] is None and (m := _LIST_SRC.match(line)):
            cur["src"] = m.group(1)
    for e in entries:
        e["lanes"], e["by_id"], e["dropped"] = source_lanes(e.pop("src") or "", known)
        e["cls"] = "R" if e["rid"].startswith("R") else "L"
    if entries and not any(e["by_id"] for e in entries):
        issues.append(("error", f"list-style map ({len(entries)} items) has no <lane>#<n> sources — lane stats unavailable"))
        return []
    for e in entries:
        if not e["by_id"]:
            issues.append(("error", f"{e['rid']}: 來源 line has no <lane>#<n> ids (line {e['line']})"))
            e["lanes"] = []
    return entries


def parse_review(path, issues=None, known=()):
    """Return (findings {id: (severity_class, [lanes])}, verdicts {id: text}); problems go to `issues`.
    `known` = lane names from the review dir (lets a bare lane word next to <lane>#<n> ids count).

    A doc holding several review rounds (one 地圖 per package) numbers each from R1; ids repeated across
    rounds are returned as `R1@2` (2nd round), and each verdict table attaches to the round whose heading
    path shares the longest prefix with its own."""
    issues = [] if issues is None else issues
    text = pathlib.Path(path).read_text(encoding="utf-8")
    entries, vrows = [], []
    for t in tables(text):
        if any("收斂裁決" in h for _, h in t.headings):
            vcol = pick_col(t.header, "裁決", avoid=("嚴重度",))
            if vcol in (None, 0):
                continue
            scol = pick_col(t.header, "最終嚴重度")
            for _, cells in t.rows:
                for rid in row_ids(cells[0], any_prefix=True):
                    final = severity_class(_cell(cells, scol)) if scol is not None else None
                    vrows.append((_scope(t.headings, "收斂裁決"), rid, _cell(cells, vcol), final))
    discarded = []
    maps = map_tables(text, discarded)
    for t, _, rows in discarded:
        if pick_col(t.header, "宣稱嚴重度") is not None:
            issues.append(("error", f"table at line {rows[0][0]} looks like a map (來源＋宣稱嚴重度) but is not under a "
                                    "地圖 heading — its findings are not counted"))
    under_map = all(any("地圖" in h for _, h in t.headings) for t, _, _ in maps)
    # WHY: two passes — a bare word counts as a lane only if some 來源 cell (or the review dir) names it by id.
    vocab = set(known) | {x for _, scol, rows in maps for _, cells in rows for x in _SRC_ID.findall(_cell(cells, scol))}
    seen = {}
    for t, scol, rows in maps:
        scope = _scope(t.headings, "地圖", include=True) if under_map else ()
        sevcol = pick_col(t.header, "宣稱嚴重度")
        sevcol = pick_col(t.header, "嚴重度") if sevcol is None else sevcol
        for lineno, cells in rows:
            lanes, by_id, dropped = source_lanes(_cell(cells, scol), vocab)
            if not by_id and vocab - set(known):
                dropped = [x for x in lanes if x not in vocab]
                lanes = [x for x in lanes if x in vocab]
            for rid in row_ids(cells[0], any_prefix=under_map):
                if (scope, rid) in seen:
                    issues.append(("error", f"duplicate map id {rid} (line {lineno}; first at line {seen[scope, rid]})"))
                    continue
                seen[scope, rid] = lineno
                cls = {"R": "R", "L": "L"}.get(id_prefix(rid)) or severity_class(_cell(cells, sevcol))
                if cls is None:
                    issues.append(("error", f"{rid}: neither an R/L id nor a severity cell — counted as R (line {lineno})"))
                entries.append({"scope": scope, "rid": rid, "line": lineno, "lanes": lanes, "cls": cls or "R"})
                if dropped:
                    # WHY: once a map uses <lane>#<n>, an unknown bare word in a 來源 cell is a note (job, rollback…).
                    issues.append(("error", f"{rid}: 來源 words {', '.join(dropped)} are not lane ids (line {lineno})"))
                if not lanes:
                    issues.append(("error", f"{rid}: 來源 cell names no lane (line {lineno})"))
    if not entries:
        entries = _list_map(text, issues, known)
    if not entries and not any("list-style map" in msg for _, msg in issues):
        issues.append(("error", "no dedup map (table with a 來源 column and R/L id rows) — lane stats unavailable"))
    rounds = list(dict.fromkeys(e["scope"] for e in entries))
    owners = collections.defaultdict(list)
    for e in entries:
        owners[e["rid"]].append(e)
    for e in entries:
        e["key"] = e["rid"] if len(owners[e["rid"]]) == 1 else f"{e['rid']}@{rounds.index(e['scope']) + 1}"
    findings = {e["key"]: (e["cls"], e["lanes"]) for e in entries}
    prefixes = {id_prefix(e["rid"]) for e in entries}
    verdicts = {}
    for vscope, rid, text_, final in vrows:
        cands = owners.get(rid, [])
        if not cands:
            # WHY: a verdict for an id the map lacks means a lost map row or a typo; X1… cross-findings are skipped.
            if id_prefix(rid) in prefixes:
                issues.append(("error", f"verdict for {rid} has no map row"))
            continue
        if len(cands) > 1:
            # INVARIANT: a verdict table belongs to the round sharing the longest heading-path prefix; a tie is an error.
            best = max(_common(vscope, e["scope"]) for e in cands)
            cands = [e for e in cands if _common(vscope, e["scope"]) == best]
            if len(cands) != 1:
                issues.append(("error", f"verdict for {rid} under {' / '.join(vscope)[:60]} matches no single review round"))
                continue
        key = cands[0]["key"]
        if key in verdicts:
            continue
        verdicts[key] = text_
        if final and key in findings:
            findings[key] = (final, findings[key][1])
    return findings, verdicts


def parse_progress(path, issues=None, known=()):
    """A lean task's PROGRESS.md 審查 table: one finding per row (its id, else P1…); same shape as parse_review."""
    issues = [] if issues is None else issues
    findings, verdicts = {}, {}
    text = pathlib.Path(path).read_text(encoding="utf-8")
    for t in tables(text):
        if not any("審查" in h for _, h in t.headings):
            continue
        cols = {"來源": pick_col(t.header, "來源"), "裁決": pick_col(t.header, "裁決", avoid=("嚴重度",)),
                "嚴重度": pick_col(t.header, "最終嚴重度")}
        if cols["嚴重度"] is None:
            cols["嚴重度"] = pick_col(t.header, "嚴重度")
        if cols["來源"] is None or cols["裁決"] is None:
            continue
        for lineno, cells in t.rows:
            rid = (row_ids(cells[0], any_prefix=True) or [f"P{len(findings) + 1}"])[0]
            if rid in findings:
                issues.append(("error", f"duplicate review row id {rid} (line {lineno})"))
                continue
            lanes, _, dropped = source_lanes(_cell(cells, cols["來源"]), known)
            if dropped:
                issues.append(("error", f"{rid}: 來源 words {', '.join(dropped)} are not lane ids (line {lineno})"))
            if not lanes:
                issues.append(("error", f"{rid}: 來源 cell names no lane (line {lineno})"))
            # WHY: a blank／— final severity on an L row falls back to the id prefix, not to R.
            cls = severity_class(_cell(cells, cols["嚴重度"])) or {"R": "R", "L": "L"}.get(id_prefix(rid)) or "R"
            findings[rid] = (cls, lanes)
            verdicts[rid] = _cell(cells, cols["裁決"])
    if not findings:
        issues.append(("error", "no 審查 table with 來源 and 裁決 columns — lane stats unavailable"))
    return findings, verdicts


def parse(path, issues=None, known=()):
    if pathlib.Path(path).name.startswith("PROGRESS"):
        return parse_progress(path, issues, known)
    return parse_review(path, issues, known)


def verdict_issues(findings, verdicts, issues):
    """R findings whose verdict is unrecognized, pending or missing; no verdict at all is one error."""
    if not verdicts:
        if any(cls == "R" for cls, _ in findings.values()):
            # WHY: a 收斂裁決 heading typo or a 判定 column used to leave every lane at R ok 0 with no warning.
            issues.append(("error", "no verdicts (no table with a 裁決 column under a 收斂裁決 heading) — R ok is 0"))
        return
    buckets = collections.defaultdict(list)
    for rid, (cls, _) in findings.items():
        if cls != "R":
            continue
        if rid not in verdicts:
            buckets["missing"].append(rid)
        else:
            vc = verdict_class(verdicts[rid])
            if vc in (None, "pending"):
                buckets["unrecognized" if vc is None else "pending"].append(
                    f"{rid}「{verdicts[rid][:24]}」" if vc is None else rid)
    for kind, ids in buckets.items():
        issues.append(("error", f"R verdict {kind}: {len(ids)} — {', '.join(ids[:8])}{' …' if len(ids) > 8 else ''}"))


def review_paths(repo, slug):
    """REVIEW*.md of the slug, else its PROGRESS.md (lean tasks have no REVIEW doc), else []."""
    plans = pathlib.Path(repo) / ".claude" / "PRPs" / "plans" / slug
    paths = sorted(glob.glob(f"{glob.escape(str(plans))}/REVIEW*.md"))
    if paths:
        return paths
    return [str(plans / "PROGRESS.md")] if (plans / "PROGRESS.md").is_file() else []


def _text(content):
    if isinstance(content, str):
        return content
    return " ".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def _dispatch_text(jsonl):
    """User turns before the agent's first reply (a Workflow agent gets the relayed request, then the task)."""
    parts = []
    with open(jsonl, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("type") == "assistant":
                break
            if r.get("type") == "user":
                parts.append(_text((r.get("message") or {}).get("content")))
    return " ".join(parts)


def _named_lane(jsonl, slug, pat):
    """The dispatch prompt names the lane's report path (also for lanes without Write, whose report the
    main session lands); a prompt naming several lane files falls back to the file the agent itself wrote."""
    named, wrote = set(), None
    with open(jsonl, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if slug not in line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            m = r.get("message") or {}
            if r.get("type") == "user" and not named:
                named = set(pat.findall(_text(m.get("content"))))
            for c in m.get("content") if isinstance(m.get("content"), list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Write":
                    w = pat.search((c.get("input") or {}).get("file_path", ""))
                    wrote = wrote or (w and w.group(1))
    return next(iter(named)) if len(named) == 1 else wrote


def lane_costs(dirs, slug, quality=None):
    """{lane: $} over every subagent transcript in dirs."""
    costs = {}
    pat = re.compile(rf"tmp/{re.escape(slug)}/review/lane-([a-z0-9-]+)\.md")
    # WHY: a plain substring test credits `w3` with every `w3-recipe-ship-pins` lane.
    mentions = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(slug)}(?![A-Za-z0-9_-])")
    for d in dirs:
        for af in agent_files(d):
            if af.is_workflow:
                m = re.fullmatch(r"lane-([a-z0-9-]+)", af.label)
                lane = m.group(1) if m and mentions.search(_dispatch_text(af.path)) else None
            else:
                lane = _named_lane(af.path, slug, pat)
            if lane:
                costs[lane] = costs.get(lane, 0.0) + sum(cost_of(msg, quality) for msg in read_messages(af.path, quality=quality))
    return costs


def known_lanes(review_dir):
    """Lane names a review dir dispatched or received (tasks/, ext-*.log, status files, lane-*.md); None if no dir."""
    d = pathlib.Path(review_dir) if review_dir else None
    if not d or not d.is_dir():
        return None
    return set(expected_lanes(d)) | {lane_name(f.stem) for f in d.glob("lane-*.md")}


def collect(slug, paths, dirs, quality=None, issues=None, review_dir=None, with_cost=True):
    """Return ({lane: Counter(R, Rok, uR, uRok, unver, L, uL)}, {lane: $}); data problems go to `issues`."""
    issues = [] if issues is None else issues
    stats = collections.defaultdict(collections.Counter)
    known = known_lanes(review_dir)
    for path in paths:
        mine = []
        findings, verdicts = parse(path, mine, frozenset(known or ()))
        verdict_issues(findings, verdicts, mine)
        if known:
            stray = sorted({x for _, lanes in findings.values() for x in lanes} - known)
            if stray:
                mine.append(("error", f"lanes not in {review_dir}: {', '.join(stray)}"))
        issues.extend((sev, f"{os.path.basename(path)}: {msg}") for sev, msg in mine)
        for rid, (cls, lanes) in findings.items():
            verdict = verdict_class(verdicts.get(rid))
            for lane in lanes:
                s = stats[lane]
                s[cls] += 1
                if len(lanes) == 1:
                    s["u" + cls] += 1
                    if cls == "R" and verdict == "valid":
                        s["uRok"] += 1
                if cls == "R" and verdict == "valid":
                    s["Rok"] += 1
                if cls == "R" and verdict == "unverified":
                    s["unver"] += 1
    return dict(stats), (lane_costs(dirs, slug, quality) if with_cost else {})


def report(slug, paths, dirs, review_dir=None, with_cost=True):
    """Print the lane table and every data problem; return the number of problems."""
    quality, issues = {}, []
    stats, costs = collect(slug, paths, dirs, quality, issues, review_dir, with_cost)
    print(f"== {slug}  ({', '.join(os.path.basename(p) for p in paths)})")
    print(f"{'lane':18}{'R':>4}{'R ok':>6}{'uniqR':>7}{'uniqR ok':>10}{'unver':>7}{'L':>5}{'uniqL':>7}{'$':>8}{'$/uniqR ok':>12}")
    for lane, s in sorted(stats.items(), key=lambda kv: (-kv[1]["uRok"], -kv[1]["uR"], -kv[1]["R"])):
        cost = costs.get(lane)
        per = f"{cost / s['uRok']:.2f}" if cost and s["uRok"] else "-"
        print(f"{lane[:18]:18}{s['R']:>4}{s['Rok']:>6}{s['uR']:>7}{s['uRok']:>10}{s['unver']:>7}{s['L']:>5}{s['uL']:>7}"
              f"{(f'{cost:.2f}' if cost else '-'):>8}{per:>12}")
    unmatched = sorted(set(costs) - set(stats))
    if unmatched:
        print(f"   lanes with a report but no finding credited: {', '.join(unmatched)}")
    for sev, msg in issues:
        print(f"   DATA {sev}: {msg}")
    if with_cost:
        print(f"   {quality_line(quality)}")
    return len(issues)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--review", default=None, help="explicit REVIEW.md or PROGRESS.md path (use with one --slug)")
    ap.add_argument("--slug", default=None)
    ap.add_argument("--project-dir", default=None)
    ap.add_argument("--review-dir", default=None,
                    help="tmp/<slug>/review to check lane names against (default: that dir in the repo, if present)")
    ap.add_argument("--no-cost", action="store_true", help="parse and check only; skip the transcript scan")
    ap.add_argument("--strict", action="store_true", help="exit 1 when any data problem is printed")
    a = ap.parse_args()
    dirs = [] if a.no_cost else project_dirs(a.project_dir, all_worktrees=True)
    problems = 0
    if a.review:
        problems += report(a.slug or pathlib.Path(a.review).parent.name, [a.review], dirs, a.review_dir, not a.no_cost)
    else:
        repo = toplevel()
        for slug in a.slugs:
            paths = review_paths(repo, slug)
            if not paths:
                print(f"== {slug}: no REVIEW*.md or PROGRESS.md")
                problems += 1
                continue
            problems += report(slug, paths, dirs, a.review_dir or repo / "tmp" / slug / "review", not a.no_cost)
    sys.exit(1 if a.strict and problems else 0)


if __name__ == "__main__":
    main()
