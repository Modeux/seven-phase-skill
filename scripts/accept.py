# -*- coding: utf-8 -*-
"""Run the runnable commands of ACCEPTANCE.md 待驗 rows and print them next to each row's 判準; never edits the file.

Usage:
  python3 accept.py                             # due rows: what would run, what needs a human (dry run)
  python3 accept.py --run                       # ...and run them; outputs in <repo>/tmp/accept/<today>/
  python3 accept.py --row 14 19 --run --timeout 300
  python3 accept.py --all --today 2026-12-31    # every row, as of that day
  python3 accept.py show <slug>                 # the slug's docs(prps) commits
  python3 accept.py show <slug> PROGRESS.md     # that record file from the newest commit that has it

Rows (numbered 1… in 待驗 table order) are due when the 到期 cell's last date ≤ today (no date at all also
counts as due, like board.py); event when the cell names an event and its 最晚 date is still ahead
(listed in one line: 事件：人判是否已發生); later when it is a plain date still ahead.
  --due (default)  due rows in full, event rows one line each; --all every row in full; --row N… these rows
  --run            run the runnable spans (dry run without it); --out DIR (default <repo>/tmp/accept/<today>/)
                   gets row-<N>-<k>.out per span (stdout+stderr), the terminal shows rc and 5 lines
  --timeout SEC    per span (default 120); a timeout is rc=124
  --slug S         (repeatable) fill placeholders per slug; a row whose 指令 has one is printed and run once
                   per slug (`[N] slug=S …`, out file row-<N>-<S>-<k>.out), other rows once. <slug> → S;
                   <任務>／<任務目錄>／<task> → .claude/PRPs/plans/S; <…hash…>／<…commit…> → the newest
                   docs(prps) commit of S whose tree has the path after `:` in the same span (no path: the
                   newest one; none found: 需人判 找不到 commit). Spans still holding <…> stay 需填參數.
  --file           ACCEPTANCE.md (default: the one next to this scripts/ dir)
  --repo           where non-skill commands run (default: main checkout of cwd)

A code span in the 指令 cell is runnable when it starts with python3/grep/git/find/wc/ls/bash, has no <…>
placeholder and no 手動. `python3 scripts/…`, `python3 retros/…` and `python3 ~/.claude/skills/seven-phase/…`
resolve into the skill repo (missing file: 腳本不存在). Spans with rm, push, commit, --apply or --deployed
are never run (有副作用，需人跑). Everything else — blocked spans and prose such as 手動、讀、問 owner — is
printed under 需人判 verbatim. Every span runs with cwd=repo through bash, stdin closed.

First line: `ACCEPT due=<n> event=<n> later=<n> ran=<n> failed=<n>` (counts over the whole table; failed =
rc≠0, and grep without a match is rc 1 — read it against the 判準).
show: first line `SHOW <slug> commits=<n>`; with a file name stdout is the file only, the commit goes to stderr.

Exit: 0 ok (failed commands included), 1 show found nothing, 2 bad input (no 待驗 table, unknown row, bad date).
"""
import argparse
import contextlib
import datetime
import io
import pathlib
import re
import shlex
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _mdtable import pick_col, tables  # noqa: E402
from _usage import main_checkout  # noqa: E402

SKILL = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_FILE = SKILL / "ACCEPTANCE.md"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
RUN_PROGRAMS = {"python3", "grep", "git", "find", "wc", "ls", "bash"}
PLACEHOLDER = re.compile(r"<[^<>\s][^<>]{0,40}>")
SIDE_EFFECT = re.compile(r"(?<![\w-])(rm|push|commit)(?![\w-])|--apply\b|--deployed\b")
TASK_PH = re.compile(r"<(?:任務|任務目錄|task)>")
HASH_PH = re.compile(r"<[^<>]*(?:hash|commit)[^<>]*>")
PLAN_DIR = ".claude/PRPs/plans/{slug}"
GREP_VALUE_OPTS ={"-e", "-f", "-m", "-A", "-B", "-C"}
HUMAN_WORDS =("手動", "讀 ", "問 owner", "transcript")
SKILL_PREFIXES = ("scripts/", "retros/")
SKILL_HOME = "~/.claude/skills/seven-phase/"
OUT_LINES = 5
TIMEOUT_RC = 124


class InputError(Exception):
    pass


def code_spans(text):
    """[(is_span, text)] in order; a span opens with a run of n backticks and closes at the next run of
    exactly n (CommonMark), so '```mermaid' inside a single-tick span stays inside it."""
    out, i, plain = [], 0, ""
    while i < len(text):
        if text[i] != "`":
            plain += text[i]
            i += 1
            continue
        n = len(text[i:]) - len(text[i:].lstrip("`"))
        j = i + n
        close = None
        for m in re.finditer(r"`+", text[j:]):
            if len(m.group(0)) == n:
                close = j + m.start()
                break
        if close is None:
            plain += text[i:j]
            i = j
            continue
        if plain:
            out.append((False, plain))
            plain = ""
        out.append((True, text[j:close].strip()))
        i = close + n
    if plain:
        out.append((False, plain))
    return out


def resolve_python(span):
    """(command, None) with a skill script path made absolute, or (None, reason) when the script is missing."""
    try:
        argv = shlex.split(span)
    except ValueError:
        return span, None
    if len(argv) < 2 or argv[1].startswith("-"):
        return span, None
    script = argv[1]
    if script.startswith(SKILL_HOME):
        path = SKILL / script[len(SKILL_HOME):]
    elif script.startswith(SKILL_PREFIXES):
        path = SKILL / script
    else:
        path = pathlib.Path(script).expanduser()
        if not path.is_absolute():
            return span, None
    if not path.is_file():
        return None, f"腳本不存在：{path}"
    head = span.split(None, 2)
    return " ".join([head[0], shlex.quote(str(path))] + head[2:]), None


def classify_span(span):
    """('run', command) | ('human', reason) | ('text', None) for a span that is not a command at all."""
    if not span or span.split(None, 1)[0] not in RUN_PROGRAMS:
        return ("text", None)
    if "手動" in span:
        return ("human", "手動")
    ph = PLACEHOLDER.findall(span)
    if ph:
        return ("human", "需填參數 " + " ".join(dict.fromkeys(ph)))
    if SIDE_EFFECT.search(span):
        return ("human", "有副作用，需人跑")
    if span.startswith("python3 "):
        cmd, why = resolve_python(span)
        if cmd is None:
            return ("human", why)
        return ("run", cmd)
    if span.startswith("grep ") and grep_without_file(span):
        return ("human", "grep 沒有檔案參數，是手動步驟的片段")
    return ("run", span)


def grep_without_file(span):
    """True for a lone `grep [opts] PATTERN`: run with stdin closed it reports 0 matches, which reads as a
    result. A grep fed by a pipe or given -r / files is a real command."""
    if "|" in span:
        return False
    try:
        argv = shlex.split(span)[1:]
    except ValueError:
        return False
    operands, has_e, i = [], False, 0
    while i < len(argv):
        t = argv[i]
        name = t.split("=", 1)[0]
        if t in GREP_VALUE_OPTS or name in ("--regexp", "--file"):
            has_e = has_e or name in ("-e", "-f", "--regexp", "--file")
            i += 1 if "=" in t else 2
            continue
        if t.startswith("--"):
            if name in ("--recursive", "--dereference-recursive"):
                return False
        elif t.startswith("-") and len(t) > 1:
            if "r" in t[1:] or "R" in t[1:]:
                return False
        else:
            operands.append(t)
        i += 1
    return len(operands) <= (0 if has_e else 1)


class Slugs:
    """Fills one task's placeholders into a span: <slug>, <任務>-like task dirs, <… hash／commit>."""

    def __init__(self, repo):
        self.repo = repo
        self.cache = {}

    def commits(self, slug):
        if slug not in self.cache:
            self.cache[slug] = docs_commits(self.repo, slug)
        return self.cache[slug]

    def fill(self, span, slug):
        """(span with placeholders filled, None) or (span, reason) when a hash could not be resolved."""
        span = TASK_PH.sub(PLAN_DIR.format(slug=slug), span.replace("<slug>", slug))
        problem = None

        def commit_for(m):
            nonlocal problem
            path = re.match(r":([^\s'\"`]+)", span[m.end():])
            if path and "<" in path.group(1):
                return m.group(0)  # INVARIANT: an unfilled path stays a placeholder → 需填參數, not a guess.
            hits = self.commits(slug)
            if path:
                h = newest_with(self.repo, hits, path.group(1))
            else:
                h = hits[0].split(" ", 1)[0] if hits else None
            if h is None:
                problem = f"找不到 commit（{slug} 的 docs(prps) commit 裡沒有 {path.group(1) if path else '任何一筆'}）"
                return m.group(0)
            return h

        span = HASH_PH.sub(commit_for, span)
        return span, problem


def needs_slug(cmd_cell):
    return "<slug>" in cmd_cell or bool(TASK_PH.search(cmd_cell)) or bool(HASH_PH.search(cmd_cell))


def plan_row(cmd_cell, fill=None):
    """(runnable commands, 需人判 lines) of one 指令 cell; fill(span) → (span, problem) substitutes a slug."""
    runs, human, segments, cur = [], [], [], []
    for is_span, text in code_spans(cmd_cell):
        if is_span:
            cur.append((True, text))
            continue
        parts = re.split(r"[；;]", text)
        for k, part in enumerate(parts):
            if k:
                segments.append(cur)
                cur = []
            cur.append((False, part))
    segments.append(cur)
    for seg in segments:
        blocked, residual = [], ""
        for is_span, text in seg:
            if not is_span:
                residual += text
                continue
            problem = None
            if fill is not None:
                text, problem = fill(text)
            kind, info = classify_span(text)
            if problem and kind != "text":
                kind, info = "human", problem
            if kind == "run":
                runs.append(info)
            elif kind == "human":
                blocked.append((text, info))
            else:
                residual += text
        original = "".join(f"`{t}`" if s else t for s, t in seg).strip(" ，,、")
        plain = re.sub(r"[\s，,、。：:（）()]+", "", residual)
        if len(plain) >= 3 or any(w in residual for w in HUMAN_WORDS):
            human.append(original)
            human.extend(f"  ^ {reason}" for _, reason in blocked)
        else:
            human.extend(f"`{span}`（{reason}）" for span, reason in blocked)
    return runs, human


def status_of(due_text, today):
    dates = DATE_RE.findall(due_text)
    if not dates:
        return "due"
    try:
        last = datetime.date.fromisoformat(dates[-1])
    except ValueError:
        raise InputError(f"到期 {due_text!r}: bad date {dates[-1]}") from None
    if last <= today:
        return "due"
    return "later" if DATE_RE.fullmatch(due_text.strip()) else "event"


def read_pending(path):
    text = pathlib.Path(path).read_text(encoding="utf-8")
    for t in tables(text):
        if t.headings and t.headings[-1][1].startswith("待驗"):
            cols = {k: pick_col(t.header, k) for k in ("到期", "改動", "指令", "判準", "出處")}
            missing = [k for k, v in cols.items() if v is None]
            if missing:
                raise InputError(f"{path}: 待驗 table has no column {'、'.join(missing)}")
            return [{k: (row[i] if i < len(row) else "") for k, i in cols.items()} | {"line": n}
                    for n, row in t.rows]
    raise InputError(f"{path}: no 待驗 table")


def run_span(cmd, repo, timeout, out_file):
    try:
        p = subprocess.run(cmd, shell=True, executable="/bin/bash", cwd=str(repo), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        rc, data = p.returncode, p.stdout
    except subprocess.TimeoutExpired as e:
        rc, data = TIMEOUT_RC, (e.stdout or b"") + f"\n[accept] timeout after {timeout}s\n".encode()
    out_file.write_bytes(data)
    return rc, data.decode("utf-8", "replace")


def print_row(n, row, status, a, out_dir, counts, slugs=None):
    if slugs is not None and a.slug and needs_slug(row["指令"]):
        for slug in a.slug:
            print_one(n, row, status, a, out_dir, counts, slug, lambda s, slug=slug: slugs.fill(s, slug))
    else:
        print_one(n, row, status, a, out_dir, counts, None, None)


def print_one(n, row, status, a, out_dir, counts, slug, fill):
    tag = f"slug={slug} " if slug else ""
    print(f"[{n}] {tag}到期 {row['到期']} | 改動 {row['改動'][:40]}")
    print(f"  狀態 {status}" + ("（事件：人判是否已發生）" if status == "event" else ""))
    runs, human = plan_row(row["指令"], fill)
    for k, cmd in enumerate(runs, 1):
        if not a.run:
            print(f"  ${k} (未跑) {cmd}")
            continue
        out_file = out_dir / (f"row-{n}-{slug}-{k}.out" if slug else f"row-{n}-{k}.out")
        print(f"accept: row {n} {tag}${k} running", file=sys.stderr)
        rc, text = run_span(cmd, a.repo, a.timeout, out_file)
        counts["ran"] += 1
        counts["failed"] += rc != 0
        print(f"  ${k} {cmd}")
        print(f"    rc={rc}{' timeout' if rc == TIMEOUT_RC else ''} -> {out_file}")
        for line in text.splitlines()[:OUT_LINES]:
            print(f"    | {line[:200]}")
    if human:
        print("  需人判:")
        for h in human:
            print(f"    - {h}")
    print(f"  判準: {row['判準']}")
    print(f"  出處: {row['出處']}")


def cmd_rows(a):
    try:
        today = datetime.date.fromisoformat(a.today) if a.today else datetime.date.today()
    except ValueError:
        raise InputError(f"--today {a.today!r} is not YYYY-MM-DD") from None
    rows = read_pending(a.file)
    states = [status_of(r["到期"], today) for r in rows]
    if a.row:
        bad = [n for n in a.row if not 1 <= n <= len(rows)]
        if bad:
            raise InputError(f"--row {' '.join(map(str, bad))}: 待驗 has rows 1–{len(rows)}")
    a.repo = pathlib.Path(a.repo or main_checkout()).resolve()
    out_dir = pathlib.Path(a.out) if a.out else a.repo / "tmp" / "accept" / today.isoformat()
    if a.run:
        out_dir.mkdir(parents=True, exist_ok=True)
    counts = {"ran": 0, "failed": 0}
    slugs = Slugs(a.repo)
    # WHY: the first line carries ran/failed, known only after every span ran; the body is buffered.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for n, (row, st) in enumerate(zip(rows, states), 1):
            if a.row:
                full = n in a.row
            elif a.all:
                full = True
            else:
                full = st == "due"
            if full:
                print_row(n, row, st, a, out_dir, counts, slugs)
            elif not a.row and not a.all and st == "event":
                print(f"[{n}] 到期 {row['到期']} | 改動 {row['改動'][:40]}")
                print(f"  狀態 event（事件：人判是否已發生；--row {n} 看指令）")
    print(f"ACCEPT due={states.count('due')} event={states.count('event')} later={states.count('later')} "
          f"ran={counts['ran']} failed={counts['failed']}")
    sys.stdout.write(buf.getvalue())
    return 0


def git(repo, *args, check=True):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise InputError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p


def docs_commits(repo, slug):
    """`<hash> <date> <subject>` of the slug's docs(prps) commits, newest first; [] outside a git repo."""
    p = git(repo, "log", "--format=%h %ad %s", "--date=short", "--fixed-strings", f"--grep={slug}", check=False)
    return [ln for ln in p.stdout.splitlines() if "docs(prps)" in ln.split(" ", 2)[-1]] if p.returncode == 0 else []


def newest_with(repo, commits, rel):
    """Hash of the newest commit whose tree has `rel`, or None.

    WHY: the newest docs(prps) commit is often the one that deleted the task dir; `show` and the hash
    placeholder both take the newest commit that still has the file instead of failing on it."""
    for ln in commits:
        h = ln.split(" ", 1)[0]
        if git(repo, "cat-file", "-e", f"{h}:{rel}", check=False).returncode == 0:
            return h
    return None


def cmd_show(a):
    repo = pathlib.Path(a.repo or main_checkout()).resolve()
    git(repo, "rev-parse", "--git-dir")
    commits = docs_commits(repo, a.slug)
    if a.name is None or not commits:
        print(f"SHOW {a.slug} commits={len(commits)}")
        for ln in commits:
            print(ln)
        return 0 if commits else 1
    rel = f"{PLAN_DIR.format(slug=a.slug)}/{a.name}"
    h = newest_with(repo, commits, rel)
    if h is not None:
        print(f"SHOW {a.slug} {h}:{rel}", file=sys.stderr)
        sys.stdout.write(git(repo, "show", f"{h}:{rel}").stdout)
        return 0
    newest = commits[0].split(" ", 1)[0]
    files = [ln for c in commits for ln in
             git(repo, "ls-tree", "-r", "--name-only", c.split(" ", 1)[0], "--", f".claude/PRPs/plans/{a.slug}/")
             .stdout.splitlines()]
    print(f"SHOW {a.slug} commits={len(commits)} missing {rel}")
    print(f"files under .claude/PRPs/plans/{a.slug}/ in these commits (newest {newest}):")
    for f in dict.fromkeys(files):
        print(f"  {f}")
    return 1


def parse_args(argv):
    if argv and argv[0] == "show":
        ap = argparse.ArgumentParser(prog="accept.py show", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
        ap.add_argument("slug")
        ap.add_argument("name", nargs="?", default=None)
        ap.add_argument("--repo", default=None)
        a = ap.parse_args(argv[1:])
        a.cmd = "show"
        return a
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=str(DEFAULT_FILE))
    ap.add_argument("--repo", default=None)
    ap.add_argument("--today", default=None)
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--due", action="store_true")
    sel.add_argument("--all", action="store_true")
    sel.add_argument("--row", type=int, nargs="+", default=None)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--slug", action="append", default=None)
    a = ap.parse_args(argv)
    a.cmd = "rows"
    return a


def main(argv=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return cmd_show(a) if a.cmd == "show" else cmd_rows(a)
    except (InputError, OSError) as e:
        print(f"accept: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
