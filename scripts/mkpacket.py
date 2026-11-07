# -*- coding: utf-8 -*-
"""Build the codex／grok／agy external-review packets from one manifest; excerpts come mechanically from the
PLAN file matrix, agy is trimmed down a fixed ladder to the CLI cap, and the header count is checked.

Usage:
  python3 mkpacket.py tmp/<slug>/plan-review/manifest.txt              # write packet-*.md next to it
  python3 mkpacket.py tmp/<slug>/review/manifest.txt --out tmp/<slug>/review
  python3 mkpacket.py tmp/<slug>/plan-review/manifest.txt --check      # build and check, write nothing

Options:
  --out DIR   where packet-*.md go (default: the manifest's directory)
  --check     run every check and print the verdict, write no file

Manifest: one `key: value` per line; a line starting with `#` is a comment and so is ` # …` after a value.
Paths are absolute, or relative to the manifest directory (snapshot, brief, diff-*) or to the snapshot
(plan, spec, appendix, matrix, rule).
  kind: plan-review | review
  snapshot: wt-snap
  brief: brief.md                       # filled task brief, opens all three packets verbatim
  plan: tmp/review-input/PLAN.md
  spec: tmp/review-input/SPEC.md        # plan-review
  appendix: tmp/review-input/plan-appendix   # optional; every *.md in it, in full
  matrix: tmp/review-input/PLAN.md      # or plan-appendix/matrix.md (plan-review)
  rule: .claude/rules/x.md § <heading>  # repeatable; without `§ …` the whole file
  diff-src: change-src.patch            # review: non-test diff (inlined for grok and agy)
  diff-docs: change-docs.patch          # review: docs diff (inlined for agy, path for codex／grok)
  diff-tests: change-tests.patch        # review: test diff (path for codex／grok)
  role: <one paragraph>                 # optional `## 你的角色` for codex
  agy-split: <label>=<prefix>,<prefix>  # repeatable; agy becomes packet-agy-<label>.md per label
  agy-drop-spec: <heading>              # repeatable; SPEC section left out of agy
  agy-drop-plan: <heading>              # repeatable; PLAN section left out of agy

Matrix: every table whose header has 檔案 (or 路徑) and 符號／行號 columns. When the matrix file has no such
table (the three-column PLAN.md shape), every table with a 檔案／路徑 column and every `- \\`path\\` …`
bullet in plan-appendix/matrix.md is read instead. Per row: `新檔` or an empty cell is a new file; line
ranges (`L12-L30`, `L12–30`, `12-30`, `:12-30`, `L96`, several joined by `、`／`,`／`;`) are excerpted;
otherwise the backticked names are looked up in that file (`func X`, `export … X`, `X:`, `X(`…) and ±40
lines taken (a name found nowhere fails the run); a prose cell with neither takes L1-L12 and prints a
`note:` line. A `.../rest` path borrows the prefix of the nearest earlier row path containing rest's first part.

agy ladder (each agy packet on its own, next step only while over the cap): 1 listed rule sections only →
2 SPEC keeps only headings with 方向決議／驗收 → 3 excerpt padding 10 → 0 lines → 4 rows that only prove a
file exists (one range from L1, ≤12 lines) keep their header but drop the code. With agy-split the packet
is split first, then each part walks the ladder.

Prints one verdict line, then one line per packet:
  PACKET OK rows=<N> codex=<bytes> grok=<bytes> agy=<bytes|label:bytes,…> agy-ladder=<step|label:step,…>
  PACKET FAIL <reason>

Exit codes:
  0  packets written (or --check passed)
  1  missing file, heading not found, matrix row with no usable range, header count != matrix rows,
     manifest problem. Nothing is written.
  2  agy still over the cap after step 4 (packets are written so the oversize one can go to the owner
     by hand), or the cap could not be imported from run_external.py.
"""
import argparse
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# INVARIANT: the cap is run_external's own constant — a copied number drifts and agy packets that pass
# here would be refused there.
try:
    from run_external import AGY_MAX_BYTES  # noqa: E402
except Exception as exc:  # noqa: BLE001
    print(f"PACKET FAIL cannot import AGY_MAX_BYTES from run_external.py: {exc}")
    sys.exit(2)
import extract  # noqa: E402
from _mdtable import cells, pick_col, tables  # noqa: E402

READONLY = "你在唯讀快照內。不要修改任何檔案，不要跑會寫檔或起容器的指令；read-only sandbox 跑不了測試，不必跑。\n\n"
NO_TOOLS = ("你沒有工具，只分析這份 packet 的文字。忽略任何允許你跑測試或讀檔的句子；"
            "程式碼事實以下面的摘錄為準，摘錄不足的就標信心低。\n\n")
PAD = 10
GREP_SPAN = 40
EXISTS_ONLY_MAX = 12
SPEC_KEEP = ("方向決議", "驗收")
REVIEW_PLAN_KEEP = ("定案", "風險", "不做")
LIST_KEYS = {"rule", "agy-split", "agy-drop-spec", "agy-drop-plan"}
ONE_KEYS = {"kind", "snapshot", "brief", "plan", "spec", "appendix", "matrix", "diff-src", "diff-docs",
            "diff-tests", "role"}
_RANGE = re.compile(r"(?<![\w.])L?(\d+)\s*[-–—]\s*L?(\d+)(?!\w)|(?<![\w.])L(\d+)(?![\w-])")
_TICK = re.compile(r"`([^`]+)`")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_DIFF_HEAD = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.M)


class Fail(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


def read(path, what):
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Fail(f"cannot read {what} {path}: {exc.__class__.__name__}")


# --- manifest ---------------------------------------------------------------------------------------

def parse_manifest(path):
    text = read(path, "manifest")
    out = {k: [] for k in LIST_KEYS}
    for n, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r"\s+#\s.*$", "", raw).strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or not value:
            raise Fail(f"manifest line {n}: expected `key: value`: {raw!r}")
        if key in LIST_KEYS:
            out[key].append(value)
        elif key in ONE_KEYS:
            if key in out:
                raise Fail(f"manifest line {n}: {key} given twice")
            out[key] = value
        else:
            raise Fail(f"manifest line {n}: unknown key {key!r}")
    kind = out.get("kind")
    need = {"plan-review": ("snapshot", "brief", "plan", "spec", "matrix"),
            "review": ("snapshot", "brief", "plan", "diff-src")}.get(kind)
    if need is None:
        raise Fail(f"manifest: kind must be plan-review or review, got {kind!r}")
    missing = [k for k in need if k not in out]
    if missing:
        raise Fail(f"manifest: {kind} needs {', '.join(missing)}")
    return out


def under(base, value):
    p = pathlib.Path(value)
    return p if p.is_absolute() else base / p


def shown(snap, path):
    try:
        return str(path.relative_to(snap))
    except ValueError:
        return str(path)


# --- sections ---------------------------------------------------------------------------------------

def find_heading(lines, heads, sel, where):
    """Index into heads: exact heading text first, else the one heading containing sel."""
    exact = [k for k, h in enumerate(heads) if h[2] == sel]
    hits = exact or [k for k, h in enumerate(heads) if sel in h[2]]
    if len(hits) != 1:
        found = "; ".join(f"L{heads[k][0] + 1} {heads[k][2]}" for k in hits) or "none"
        raise Fail(f"heading {sel!r} in {where}: {len(hits)} matches ({found})")
    return hits[0]


def rule_text(snap, spec):
    rel, sep, heading = (s.strip() for s in spec.partition("§"))
    text = read(under(snap, rel), "rule file")
    if not sep:
        return f"### {rel}\n{text.rstrip()}\n"
    lines = text.splitlines()
    heads = extract.headings(lines)
    start, end = extract.section(lines, heads, find_heading(lines, heads, heading, rel))
    return f"### {rel} § {heading}\n" + "\n".join(lines[start:end]).rstrip() + "\n"


def drop_sections(text, names, what, notes):
    for name in names:
        lines = text.splitlines()
        heads = extract.headings(lines)
        try:
            k = find_heading(lines, heads, name, what)
        except Fail as exc:
            # WHY: a missing drop target only costs size, which the ladder still guards; say so, don't abort.
            notes.append(f"note: {exc} — nothing dropped")
            continue
        start, end = extract.section(lines, heads, k)
        text = "\n".join(lines[:start] + lines[end:]) + "\n"
    return text


def keep_sections(text, words):
    """The title line plus every top-most section whose heading contains one of words."""
    lines = text.splitlines()
    heads = extract.headings(lines)
    kept, upto = [], -1
    if heads and heads[0][1] == 1 and heads[0][0] == 0:
        kept.append(lines[0])
    for k, (start, _, title) in enumerate(heads):
        if start < upto or not any(w in title for w in words):
            continue
        s, e = extract.section(lines, heads, k)
        if s == 0 and heads[k][1] == 1:
            continue
        kept.extend([""] + lines[s:e])
        upto = e
    return "\n".join(kept).strip("\n") + "\n"


def drop_hit_column(text):
    """Rule-table 命中 cells are long path lists the matrix already gives; agy only pays bytes for them."""
    out, col = [], None
    for line in text.splitlines():
        if line.startswith("<!-- rulesfor:"):
            continue
        if line.lstrip().startswith("|"):
            row = cells(line)
            if col is None and "命中" in [c.strip("*` ") for c in row]:
                col = [c.strip("*` ") for c in row].index("命中")
            if col is not None and col < len(row):
                line = "| " + " | ".join(row[:col] + row[col + 1:]) + " |"
        else:
            col = None
        out.append(line)
    return "\n".join(out) + "\n"


# --- matrix -----------------------------------------------------------------------------------------

def cell_path(cell):
    m = _TICK.search(cell)
    return (m.group(1) if m else cell).strip()


def matrix_rows(snap, mf, appendix):
    """(where, path, symbol-cell) per matrix row, in document order."""
    text = read(mf, "matrix")
    rows = []
    # WHY: the appendix matrix mixes tables and `- path` bullets per slice; reading only its tables
    # would silently drop the bullet slices from the excerpts.
    in_appendix = mf.name == "matrix.md" or (appendix is not None and mf.parent == appendix)
    for t in [] if in_appendix else tables(text):
        pc = pick_col(t.header, "檔案")
        pc = pick_col(t.header, "路徑") if pc is None else pc
        sc = pick_col(t.header, "符號")
        if pc is not None and sc is not None:
            rows += [(f"{shown(snap, mf)}:{n}", cell_path(r[pc]), r[sc] if sc < len(r) else "") for n, r in t.rows]
    if rows:
        return rows
    if not in_appendix and appendix is not None:
        mf = appendix / "matrix.md"
        text = read(mf, "appendix matrix")
    for t in tables(text):
        pc = pick_col(t.header, "檔案")
        pc = pick_col(t.header, "路徑") if pc is None else pc
        if pc is None:
            continue
        sc = pick_col(t.header, "符號")
        sc = pick_col(t.header, "行號") if sc is None else sc
        for n, r in t.rows:
            sym = r[sc] if sc is not None and sc < len(r) else " ".join(c for i, c in enumerate(r) if i != pc)
            rows.append((f"{shown(snap, mf)}:{n}", cell_path(r[pc]), sym))
    fence = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"\s*[-*]\s+`([^`]+)`(.*)$", line)
        if m and ("/" in m.group(1) or "." in m.group(1)):
            rows.append((f"{shown(snap, mf)}:{n}", m.group(1).strip(), m.group(2)))
    rows.sort(key=lambda r: int(r[0].rsplit(":", 1)[1]))
    return rows


def resolve_ellipsis(path, earlier):
    for lead in ("...", "…"):
        if path.startswith(lead + "/"):
            rest = path[len(lead) + 1:]
            first = rest.split("/", 1)[0]
            for prev in reversed(earlier):
                idx = ("/" + prev).find("/" + first + "/")
                if idx >= 0:
                    return prev[:idx] + rest
            raise Fail(f"{path}: no earlier row path contains /{first}/ to borrow the prefix from")
    return path


def definition_line(lines, name):
    pats = [rf"^\s*func\s+(\([^)]*\)\s*)?{re.escape(name)}\b",
            rf"^\s*export\b.*\b{re.escape(name)}\b",
            rf"^\s*(async\s+)?(def|class|type|interface|function|const|let|var)\s+{re.escape(name)}\b",
            rf"^\s*{re.escape(name)}\s*[:=]",
            rf"\b{re.escape(name)}\s*\("]
    for pat in pats:
        rx = re.compile(pat)
        hit = next((i for i, line in enumerate(lines, 1) if rx.search(line)), None)
        if hit:
            return hit
    return None


def plan_rows(snap, raw_rows, notes):
    """[(path, ranges or None for a new file)], every failure collected so one run names them all."""
    out, errors, earlier = [], [], []
    for where, rawpath, sym in raw_rows:
        try:
            path = resolve_ellipsis(rawpath, earlier)
        except Fail as exc:
            errors.append(f"{where}: {exc}")
            continue
        earlier.append(path)
        ranges = []
        for m in _RANGE.finditer(sym):
            a, b = (int(m.group(1)), int(m.group(2))) if m.group(1) else (int(m.group(3)), int(m.group(3)))
            ranges.append((min(a, b), max(a, b)))
        is_new = not sym.strip() or ("新檔" in sym and not ranges)
        if is_new:
            out.append((path, None))
            continue
        try:
            lines = (snap / path).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            errors.append(f"{where}: cannot read {path} in the snapshot")
            continue
        names = [n for n in (t.strip().rstrip("()") for t in _TICK.findall(sym)) if _IDENT.fullmatch(n)]
        if not ranges and not names:
            # WHY: prose cells (加表, 受閘區塊, 新規則…) name nothing to look up; the row still has to prove
            # its file exists, and ladder step 4 is what drops these first. A named-but-missing symbol is
            # different: it means the PLAN is stale, so that stays a failure.
            notes.append(f"note: {where} {path}: no range or name, L1-L{EXISTS_ONLY_MAX} only ({sym.strip()!r})")
            ranges = [(1, min(EXISTS_ONLY_MAX, max(1, len(lines))))]
        if not ranges:
            for name in names:
                hit = definition_line(lines, name)
                if hit:
                    ranges.append((max(1, hit - GREP_SPAN), min(len(lines), hit + GREP_SPAN)))
            if not ranges:
                errors.append(f"{where}: {path}: no line range and no definition found for "
                              f"{', '.join(names)} in {sym.strip()!r}")
                continue
        stale = [f"L{a}-L{b}" for a, b in ranges if a > len(lines)]
        if stale:
            errors.append(f"{where}: {path} has {len(lines)} lines; range {', '.join(stale)} is past the end")
            continue
        out.append((path, sorted(ranges)))
    if errors:
        raise Fail(f"{len(errors)} matrix row(s) unusable:\n" + "\n".join(errors))
    return out


def fence_for(body):
    longest = max((len(m) for m in re.findall(r"`{3,}", body)), default=2)
    return "`" * max(3, longest + 1)


def excerpt(snap, path, ranges, pad, drop_exists_only):
    if ranges is None:
        return f"### {path} (new file in this PLAN, no code yet)\n"
    lines = (snap / path).read_text(encoding="utf-8").splitlines()
    merged = []
    for a, b in ranges:
        a, b = max(1, a - pad), min(len(lines), b + pad)
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    label = ", ".join(f"L{a}-L{b}" for a, b in merged)
    a0, b0 = ranges[0]
    if drop_exists_only and len(ranges) == 1 and a0 == 1 and b0 - a0 + 1 <= EXISTS_ONLY_MAX:
        return f"### {path}:L{a0}-L{b0}\n(code dropped to fit the agy cap: this row only shows the file exists)\n"
    parts = [f"### {path}:{label}\n"]
    for a, b in merged:
        body = "\n".join(lines[a - 1:b])
        if len(merged) > 1:
            parts.append(f"L{a}-L{b}:\n")
        fence = fence_for(body)
        parts.append(f"{fence}\n{body}\n{fence}\n")
    return "".join(parts)


def count_headers(text):
    """`### ` lines outside fences of the excerpt block; a fence closes only on a run at least as long."""
    count, fence = 0, None
    for line in text.splitlines():
        m = re.match(r"^(`{3,})", line)
        if fence is None and m:
            fence = m.group(1)
        elif fence is not None:
            if m and len(m.group(1)) >= len(fence) and not line.strip("`"):
                fence = None
        elif line.startswith("### "):
            count += 1
    return count


# --- diffs ------------------------------------------------------------------------------------------

def diff_chunks(text):
    starts = [m.start() for m in _DIFF_HEAD.finditer(text)]
    if not starts:
        return [("", text)] if text.strip() else []
    head = [("", text[:starts[0]])] if text[:starts[0]].strip() else []
    return head + [(_DIFF_HEAD.match(text, s).group(2), text[s:e])
                   for s, e in zip(starts, starts[1:] + [len(text)])]


def fenced_diff(text):
    fence = fence_for(text)
    return f"{fence}diff\n{text.rstrip()}\n{fence}\n"


# --- packets ----------------------------------------------------------------------------------------

def nbytes(text):
    return len(text.encode("utf-8"))


def parse_splits(values):
    splits = []
    for value in values:
        label, sep, prefixes = value.partition("=")
        label = label.strip()
        plist = [p.strip() for p in prefixes.split(",") if p.strip()]
        if not sep or not re.fullmatch(r"[\w.-]+", label) or not plist:
            raise Fail(f"agy-split {value!r}: expected <label>=<prefix>,<prefix>")
        splits.append((label, plist))
    return splits


def build(m, mdir):
    snap = under(mdir, m["snapshot"])
    if not snap.is_dir():
        raise Fail(f"snapshot {snap} is not a directory")
    brief = read(under(mdir, m["brief"]), "brief").rstrip() + "\n"
    plan_path = under(snap, m["plan"])
    plan = read(plan_path, "plan")
    role = f"\n## 你的角色\n{m['role']}\n" if "role" in m else ""
    splits = parse_splits(m["agy-split"])
    notes = []
    if m["kind"] == "plan-review":
        return build_plan_review(m, snap, brief, plan_path, plan, role, splits, notes)
    return build_review(m, mdir, snap, brief, plan, role, splits, notes)


def build_plan_review(m, snap, brief, plan_path, plan, role, splits, notes):
    spec_path = under(snap, m["spec"])
    spec = read(spec_path, "spec")
    appendix = under(snap, m["appendix"]) if "appendix" in m else None
    if appendix is not None and not appendix.is_dir():
        raise Fail(f"appendix {appendix} is not a directory")
    app_files = sorted(appendix.glob("*.md")) if appendix else []
    rows = plan_rows(snap, matrix_rows(snap, under(snap, m["matrix"]), appendix), notes)
    if not rows:
        raise Fail(f"no matrix rows in {m['matrix']} (need a table with 檔案／路徑 and 符號／行號 columns)")
    rules = "\n".join(rule_text(snap, r) for r in m["rule"])

    listed = [shown(snap, plan_path), shown(snap, spec_path)] + ([shown(snap, appendix) + "/"] if appendix else [])
    codex = (READONLY + brief + role + "\n## 材料在哪\nPLAN／SPEC／plan-appendix 在工作目錄（快照）下，自己讀 repo，"
             "這份不附摘錄：\n" + "".join(f"- `{p}`\n" for p in listed))

    def excerpts(subset, pad, drop):
        return "\n".join(excerpt(snap, p, r, pad, drop) for p, r in subset)

    app_text = "".join(f"\n## {shown(snap, f)}\n{read(f, 'appendix file').rstrip()}\n" for f in app_files)
    app_block = "\n# plan-appendix\n" + app_text if app_files else ""
    grok_rows = excerpts(rows, PAD, False)
    grok = (READONLY + brief + "\n# PLAN 全文\n" + plan + "\n# SPEC 全文\n" + spec + app_block +
            f"\n# 程式碼摘錄（照矩陣機械取出，前後各多 {PAD} 行）\n" + grok_rows +
            "\n# 規則原文\n" + rules)
    checks = [("grok", grok_rows, len(rows))]

    agy_plan = drop_hit_column(drop_sections(plan, m["agy-drop-plan"], "PLAN", notes))
    agy_spec = drop_sections(spec, m["agy-drop-spec"], "SPEC", notes)
    spec_lean = keep_sections(agy_spec, SPEC_KEEP)

    def agy_text(scope, subset, step):
        spec_text = spec_lean if step >= 2 else agy_spec
        pad = 0 if step >= 3 else PAD
        block = excerpts(subset, pad, step >= 4)
        # WHY: references/external-review.md gives grok and agy the same self-contained packet, appendix
        # included; every split part keeps the whole appendix and the ladder never trims it (owner 11-07).
        return (NO_TOOLS + scope + brief + "\n# PLAN\n" + agy_plan + "\n# SPEC\n" + spec_text + app_block +
                f"\n# 程式碼摘錄（照矩陣機械取出，前後各多 {pad} 行）\n" + block + "\n# 規則原文\n" + rules), block

    parts = [("", "", rows)]
    if splits:
        parts, assigned = [], set()
        for label, prefixes in splits:
            subset = [r for r in rows if any(r[0].startswith(p) for p in prefixes)]
            assigned.update(id(r) for r in subset)
            parts.append((label, f"本份只審 {label}；其餘由另一份材料審。\n\n", subset))
        left = [r[0] for r in rows if id(r) not in assigned]
        if left:
            notes.append(f"note: {len(left)} matrix row(s) in no agy-split part: {', '.join(left)}")
    agy = []
    for label, scope, subset in parts:
        (text, block), step = ladder(lambda s: agy_text(scope, subset, s), (1, 2, 3, 4))
        agy.append((label, text, step))
        checks.append((f"agy-{label}" if label else "agy", block, len(subset)))
    return codex, grok, agy, checks, len(rows), notes


def build_review(m, mdir, snap, brief, plan, role, splits, notes):
    diffs = {k: under(mdir, m[k]) for k in ("diff-src", "diff-docs", "diff-tests") if k in m}
    text = {k: read(p, k) for k, p in diffs.items()}
    where = {k: shown(snap, p) for k, p in diffs.items()}
    lines = plan.splitlines()
    heads = extract.headings(lines)
    for word in REVIEW_PLAN_KEEP:
        if not any(word in h[2] for h in heads):
            raise Fail(f"PLAN has no heading containing {word!r} (agy review gets 定案／風險／不做 sections)")
    plan_three = keep_sections(plan, REVIEW_PLAN_KEEP)

    codex = (READONLY + brief + role + "\n## 材料在哪\ndiff 不內聯，檔案在：\n" +
             "".join(f"- {k}: `{where[k]}`\n" for k in diffs))
    grok = (READONLY + brief + "\n# 非測試碼 diff\n" + fenced_diff(text["diff-src"]) +
            "".join(f"\n{label}：`{where[k]}`（自己讀）\n" for k, label in
                    (("diff-tests", "測試 diff"), ("diff-docs", "文件 diff")) if k in diffs))

    def agy_text(scope, src, docs):
        return (NO_TOOLS + scope + brief + "\n# PLAN（定案／風險／不做清單）\n" + plan_three +
                "\n# 非測試碼 diff\n" + fenced_diff(src) + ("\n# 文件 diff\n" + fenced_diff(docs) if docs else ""))

    if not splits:
        parts = [("", "", text["diff-src"], text.get("diff-docs", ""))]
    else:
        parts, left = [], []
        chunks = {k: diff_chunks(text[k]) for k in ("diff-src", "diff-docs") if k in text}
        for label, prefixes in splits:
            pick = {k: "".join(body for path, body in c if any(path.startswith(p) for p in prefixes))
                    for k, c in chunks.items()}
            parts.append((label, f"本份只審 {label}；其餘由另一份材料審。\n\n", pick["diff-src"],
                          pick.get("diff-docs", "")))
        for k, c in chunks.items():
            left += [f"{k}:{path or '(preamble)'}" for path, _ in c
                     if not any(path.startswith(p) for _, ps in splits for p in ps)]
        if left:
            notes.append(f"note: {len(left)} diff chunk(s) in no agy-split part: {', '.join(left)}")
    # WHY: a review packet has no SPEC or excerpts to trim, so its ladder is step 1 only; split is the way out.
    agy = [(label, agy_text(scope, src, docs), 1) for label, scope, src, docs in parts]
    return codex, grok, agy, [], 0, notes


def ladder(make, steps):
    """(make(step), step) for the first step whose packet fits; the last step when none does."""
    for step in steps:
        made = make(step)
        if nbytes(made[0]) <= agy_cap():
            return made, step
    return made, steps[-1]


def agy_cap():
    # WHY: tests shrink the cap through the environment so the real constant stays the only number in code.
    override = os.environ.get("MKPACKET_AGY_MAX")
    if not override:
        return AGY_MAX_BYTES
    if not override.isdigit():
        raise Fail(f"MKPACKET_AGY_MAX is not a byte count: {override!r}")
    return int(override)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest")
    ap.add_argument("--out", help="output directory (default: the manifest's directory)")
    ap.add_argument("--check", action="store_true", help="check only, write nothing")
    args = ap.parse_args()
    mpath = pathlib.Path(args.manifest).resolve()
    try:
        cap = agy_cap()
        codex, grok, agy, checks, nrows, notes = build(parse_manifest(mpath), mpath.parent)
        bad = [f"{name} has {count_headers(block)} excerpt headers for {want} matrix rows"
               for name, block, want in checks if count_headers(block) != want]
        if bad:
            raise Fail("; ".join(bad))
    except Fail as exc:
        print(f"PACKET FAIL {exc}")
        return exc.code
    split = any(label for label, _, _ in agy)
    files = [("packet-codex.md", codex), ("packet-grok.md", grok)]
    files += [(f"packet-agy-{label}.md" if label else "packet-agy.md", text) for label, text, _ in agy]
    over = [(label, nbytes(text)) for label, text, _ in agy if nbytes(text) > cap]
    agy_bytes = ",".join(f"{label}:{nbytes(t)}" for label, t, _ in agy) if split else str(nbytes(agy[0][1]))
    agy_steps = ",".join(f"{label}:{s}" for label, _, s in agy) if split else str(agy[0][2])
    if over:
        what = ", ".join(f"{'agy-' + label if label else 'agy'} {size}" for label, size in over)
        hint = "交 owner 手動跑或記沒跑" if split else "依片拆（agy-split）或記沒跑"
        print(f"PACKET FAIL agy 裁不下：{what} bytes > {cap}，{hint}")
    else:
        print(f"PACKET OK rows={nrows} codex={nbytes(codex)} grok={nbytes(grok)} agy={agy_bytes} "
              f"agy-ladder={agy_steps}")
    for note in notes:
        print(note)
    out = pathlib.Path(args.out).resolve() if args.out else mpath.parent
    if args.check:
        print("check only: nothing written")
    else:
        try:
            out.mkdir(parents=True, exist_ok=True)
            for name, text in files:
                (out / name).write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"PACKET FAIL cannot write to {out}: {exc}")
            return 1
    for name, text in files:
        print(f"{nbytes(text):>8}  {out / name}{'' if not args.check else '  (not written)'}")
    return 2 if over else 0


if __name__ == "__main__":
    sys.exit(main())
