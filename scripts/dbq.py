# -*- coding: utf-8 -*-
"""Query and seed the local deepweave Postgres container: read-only by default, the shared DB cannot be written by
accident, SQL errors exit non-zero with a schema hint, and long results are cut only with the total and a full file.

Usage:
  python3 dbq.py [--db NAME] [--container NAME] [--raw] [--limit N] [--out FILE] "SELECT ..."
  python3 dbq.py [--db NAME] --file checks.sql
  python3 dbq.py --schema TABLE              # columns, types, NOT NULL, defaults, keys, FKs, checks, triggers
  python3 dbq.py --tables PATTERN            # substring, or * and ? wildcards
  python3 dbq.py --write --db NAME --file seed.sql
  python3 dbq.py --clone NAME [--tree PATH]  # create + mem0 stub + migrate-up + dev seed, as walk_env.sh does
  python3 dbq.py --drop NAME                 # only a database --clone made, only with no live connection
  python3 dbq.py --list-dbs
  python3 dbq.py --fixture NAME[,NAME]|all --db NAME
  python3 dbq.py --list-fixtures

Defaults: container deepweave-postgres, database deepweave, at most 50 rows per result (--limit 0 for all).
--tree defaults to the git toplevel of the current directory.

Exit codes:
  0  success (a query with zero rows is a success; --tables and --schema with no match are not)
  1  SQL error or wrong content: bad SQL, missing column or table, fixture post-check mismatch, clone name taken
  2  tool or parameter error: bad arguments, Docker or container missing, no such database, no usable tree
  3  refused by a safety rule: read-only violation (by Postgres or by the guard), shared-DB write, prod-looking
     target, remote Docker daemon, a database dbq did not create
"""
import argparse
import dataclasses
import difflib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

EX_OK, EX_CONTENT, EX_TOOL, EX_REFUSED = 0, 1, 2, 3

SHARED_DB = "deepweave"
DEFAULT_CONTAINER = "deepweave-postgres"
DEFAULT_LIMIT = 50
CLONE_PREFIX = "deepweave_"
CLONE_MARK = "dbq-clone"
FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"
FIXTURE_PASSWORD = "deepweave-demo-123"
LOG_DIR = pathlib.Path(tempfile.gettempdir()) / "dbq"
FIELD_SEP = "\x1f"

PROD_RE = re.compile(r"(?:^|[-_.])(?:prod|production|prd|live)(?:[-_.]|$)", re.I)
IDENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
CLONE_RE = re.compile(r"^deepweave_[a-z0-9_]{1,50}$")
TABLE_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?$")
PATTERN_RE = re.compile(r"^[\w%*?]+$")

# INVARIANT: default-deny. Anything not listed can leave the database (\connect), the session (\!), the container's
# file system (\o, \g file) or run a query the guard never saw (\gexec, \i), so a new psql command stays refused.
ALLOWED_META = re.compile(
    r"^\\(echo|qecho|warn|pset|set|unset|if|elif|else|endif|timing|x|t|a|f|C|d\w*|l|conninfo|encoding|gset|g|gx)$")
NO_ARG_META = {"\\g", "\\gx"}

# INVARIANT: both lists are a net against accidents, not a sandbox against hostile SQL; the role is a superuser and
# creating a read-only role would change the shared cluster. Read-only's real enforcement is the session setting.
ALWAYS_REFUSED = [
    (re.compile(r"\b(?:create|drop|alter)\s+(?:database|role|user|tablespace)\b|\balter\s+system\b", re.I),
     "cluster-level DDL"),
    (re.compile(r"\bcopy\b[^;]*\bprogram\b", re.I), "COPY ... PROGRAM runs a shell in the container"),
    (re.compile(r"\b(?:dblink\w*|postgres_fdw)\b", re.I), "dblink/postgres_fdw reach other hosts"),
]
READ_ONLY_REFUSED = [
    (re.compile(r"\b(?:default_)?transaction_read_only\b", re.I), "turning read-only off"),
    (re.compile(r"\bread\s+write\b", re.I), "READ WRITE transaction"),
    (re.compile(r"\bsession_authorization\b|\bset\s+(?:local\s+|session\s+)?role\b", re.I), "changing role"),
    (re.compile(r"\bpg_(?:terminate|cancel)_backend\b|\bpg_reload_conf\b", re.I), "affecting other sessions"),
]

SEP_RE = re.compile(r"^-+(?:\+-+)*$")
FOOT_RE = re.compile(r"^\((\d+) rows?\)$")
PG_ALIAS_COLUMN = re.compile(r'column (\w+)\.(\w+) does not exist')
PG_OF_RELATION = re.compile(r'column "(\w+)" of relation "(\w+)" does not exist')
PG_BARE_COLUMN = re.compile(r'column "(\w+)" does not exist')
PG_RELATION = re.compile(r'relation "([\w.]+)" does not exist')
TABLE_REF = re.compile(r"\b(?:from|join|update|into)\s+(?:only\s+)?(?:\w+\.)?(\w+)(?:\s+(?:as\s+)?(\w+))?", re.I)


class ToolError(Exception):
    """Exit 2."""


class Refused(Exception):
    """Exit 3."""


class ContentError(Exception):
    """Exit 1."""


@dataclasses.dataclass
class SqlParts:
    no_comments: str
    code: str
    metas: list


def split_sql(text):
    """Lex as psql does: comments dropped; `code` also drops quoted strings but keeps dollar bodies, because DDL in a
    DO block is still DDL; a backslash only starts a metacommand outside strings, comments and dollar bodies."""
    no_comments, code, metas = [], [], []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if two == "--":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif two == "/*":
            depth, i = 1, i + 2
            while i < n and depth:
                if text[i:i + 2] == "/*":
                    depth, i = depth + 1, i + 2
                elif text[i:i + 2] == "*/":
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            no_comments.append(" ")
        elif c == "'" or c == '"':
            escapes = c == "'" and i > 0 and text[i - 1] in "eE"
            j = i + 1
            while j < n:
                if escapes and text[j] == "\\":
                    j += 2
                elif text[j] == c:
                    if text[j + 1:j + 2] == c:
                        j += 2
                    else:
                        break
                else:
                    j += 1
            no_comments.append(text[i:j + 1])
            code.append(c + c)
            i = j + 1
        elif c == "$":
            m = re.match(r"\$(?:[A-Za-z_]\w*)?\$", text[i:])
            end = text.find(m.group(0), i + m.end()) if m else -1
            if m and end >= 0:
                stop = end + m.end()
                no_comments.append(text[i:stop])
                code.append(text[i:stop])
                i = stop
            else:
                no_comments.append(c)
                code.append(c)
                i += 1
        elif c == "\\":
            j = text.find("\n", i)
            j = n if j < 0 else j
            metas.append(text[i:j].strip())
            i = j
        else:
            no_comments.append(c)
            code.append(c)
            i += 1
    return SqlParts("".join(no_comments), "".join(code), metas)


def scan_sql(text, write=False):
    """Reasons to refuse this SQL; empty means it may run."""
    parts = split_sql(text)
    reasons = []
    for meta in parts.metas:
        pieces = meta.split()
        name = pieces[0] if pieces else "\\"
        if not ALLOWED_META.match(name):
            reasons.append(f"psql metacommand {name}")
        elif name in NO_ARG_META and len(pieces) > 1:
            reasons.append(f"{name} with a target")
        elif "`" in meta:
            reasons.append("backquote shell expansion in a metacommand")
    for rx, why in ALWAYS_REFUSED:
        if rx.search(parts.code):
            reasons.append(why)
    if not write:
        for rx, why in READ_ONLY_REFUSED:
            if rx.search(parts.no_comments):
                reasons.append(why)
    return reasons


@dataclasses.dataclass
class Truncated:
    text: str
    truncated: bool
    total_rows: object
    shown_rows: object
    total_lines: int


def _wraps(line):
    # WHY: aligned format marks a cell continuing on the next line with + in the slot before the column border; a
    # literal trailing + in a padded cell has a space there, so only the last column can miscount, by one row.
    return "+|" in line or line.endswith("+")


def _find_block(lines, sep_at):
    """Index of the footer closing the block whose separator is at sep_at, when the row count agrees with it."""
    rows, prev_wrapped = 0, False
    for j in range(sep_at + 1, len(lines)):
        m = FOOT_RE.match(lines[j])
        if m and int(m.group(1)) == rows:
            return j, rows
        if not prev_wrapped:
            rows += 1
        prev_wrapped = _wraps(lines[j])
    return None, 0


def truncate_output(text, limit):
    """Cut each result block to `limit` logical rows and each run of other lines to `limit`; the footers survive."""
    lines = text.split("\n")
    trailing_newline = bool(lines) and lines[-1] == ""
    if trailing_newline:
        lines = lines[:-1]
    total_lines = len(lines)
    # WHY a run, not a running total: a checks.sql with dozens of short SELECTs has a header and a blank line per
    # result, and those are not long output. Footerless output (tuples_only, csv) is one long run, so it is still cut.
    kept, total_rows, shown_rows, plain, dropped, cut, i, saw_block = [], 0, 0, 0, 0, False, 0, False
    while i < len(lines):
        line = lines[i]
        foot, rows = (_find_block(lines, i) if SEP_RE.match(line) else (None, 0))
        if foot is None:
            if limit and plain >= limit:
                cut = True
                dropped += 1
            else:
                kept.append(line)
                plain += 1
            i += 1
            continue
        saw_block = True
        total_rows += rows
        if dropped:
            # WHY: the line before a separator is this block's own header, and it was dropped with the run.
            kept.append(f"... {dropped - 1} more lines not shown")
            kept.append(lines[i - 1])
        plain, dropped = 0, 0
        kept.append(line)
        logical, prev_wrapped = 0, False
        for j in range(i + 1, foot):
            if not prev_wrapped:
                logical += 1
            prev_wrapped = _wraps(lines[j])
            if not limit or logical <= limit:
                kept.append(lines[j])
        shown = min(rows, limit) if limit else rows
        shown_rows += shown
        if shown < rows:
            cut = True
            kept.append(f"... {rows - shown} more rows not shown")
        kept.append(lines[foot])
        i = foot + 1
    if dropped and saw_block:
        kept.append(f"... {dropped} more lines not shown")
    if not cut:
        return Truncated(text, False, total_rows if saw_block else None, shown_rows if saw_block else None, total_lines)
    return Truncated("\n".join(kept) + "\n", True, total_rows if saw_block else None,
                     shown_rows if saw_block else None, total_lines)


def describe_cut(t, limit):
    if t.total_rows is not None and t.shown_rows < t.total_rows:
        return f"showing {t.shown_rows} of {t.total_rows} rows"
    return f"showing part of {t.total_lines} lines, at most {limit} in a row"


def parse_missing(stderr):
    """What a does-not-exist error names, or None."""
    m = PG_OF_RELATION.search(stderr)
    if m:
        return {"kind": "column", "name": m.group(1), "qualifier": None, "table": m.group(2)}
    m = PG_ALIAS_COLUMN.search(stderr)
    if m:
        return {"kind": "column", "name": m.group(2), "qualifier": m.group(1), "table": None}
    m = PG_BARE_COLUMN.search(stderr)
    if m:
        return {"kind": "column", "name": m.group(1), "qualifier": None, "table": None}
    m = PG_RELATION.search(stderr)
    if m:
        return {"kind": "relation", "name": m.group(1).split(".")[-1], "qualifier": None, "table": None}
    return None


def candidate_tables(sql, qualifier):
    """Tables the SQL reads or writes, narrowed to the alias or name the error qualified the column with."""
    out = []
    for m in TABLE_REF.finditer(split_sql(sql).code):
        table, alias = m.group(1), m.group(2)
        if qualifier is not None and qualifier.lower() not in (table.lower(), (alias or "").lower()):
            continue
        if table not in out:
            out.append(table)
    return out


def is_read_only_refusal(stderr):
    return "read-only transaction" in stderr or "read-write mode inside a read-only" in stderr


def is_missing_database(stderr):
    return bool(re.search(r'FATAL:\s+database "[^"]+" does not exist', stderr))


@dataclasses.dataclass
class Fixture:
    name: str
    meta: dict
    credits: dict
    requires: list
    sql: str


def _parse_fixture(path):
    text = path.read_text(encoding="utf-8")
    meta = {}
    for line in text.splitlines():
        m = re.match(r"^-- (\w+): ?(.*)$", line)
        if not m:
            break
        meta[m.group(1)] = m.group(2).strip()
    required = {"fixture", "kind", "id", "state"} | ({"email", "adult_status", "credits"}
                                                     if meta.get("kind") == "account" else {"is_nsfw"})
    if not required <= set(meta) or meta["fixture"] != path.stem:
        raise ToolError(f"fixture {path.name}: header must carry {sorted(required)} and match the file name")
    credits = {}
    for pair in meta.get("credits", "").split():
        currency, _, amount = pair.partition("=")
        if not amount.isdigit():
            raise ToolError(f"fixture {path.name}: bad credits entry {pair!r}")
        credits[currency] = int(amount)
    return Fixture(path.stem, meta, credits, meta.get("requires", "").split(), text)


def load_fixtures():
    paths = sorted(p for p in FIXTURE_DIR.glob("*.sql") if not p.name.startswith("_"))
    if not paths:
        raise ToolError(f"no fixtures in {FIXTURE_DIR}")
    return {p.stem: _parse_fixture(p) for p in paths}


def resolve_fixtures(spec, fixtures):
    """Names to apply, each after what it requires."""
    names = sorted(fixtures) if spec == "all" else [s.strip() for s in spec.split(",") if s.strip()]
    if not names:
        raise ToolError("--fixture: no fixture named; see --list-fixtures")
    order = []

    def add(name):
        if name not in fixtures:
            raise ToolError(f"--fixture: unknown fixture {name!r}; have {', '.join(sorted(fixtures))}")
        if name in order:
            return
        for dep in fixtures[name].requires:
            add(dep)
        order.append(name)

    for name in names:
        add(name)
    return order


def list_fixtures():
    fixtures = load_fixtures()
    print(f"{len(fixtures)} fixtures; every account logs in with password {FIXTURE_PASSWORD} (walk.signIn default)")
    for f in fixtures.values():
        if f.meta["kind"] == "account":
            credits = " ".join(f"{c}={a}" for c, a in f.credits.items()) or "no credits"
            print(f"  {f.name:<13}{f.meta['email']:<30}{f.meta['adult_status']:<9}{credits:<40}{f.meta['state']}")
        else:
            print(f"  {f.name:<13}{f.meta['id']:<39}is_nsfw={f.meta['is_nsfw']:<6}{f.meta['state']}")


def assert_local_docker(env_host):
    # INVARIANT: only the local daemon. The tool has no host, port or URL parameter and reads no DATABASE_URL, so
    # DOCKER_HOST and the docker context are the only remaining ways to reach another machine; a remote daemon's
    # "deepweave-postgres" looks exactly like the local one.
    if env_host and not env_host.startswith("unix://"):
        raise Refused(f"DOCKER_HOST={env_host} is not a local socket; dbq only talks to the local Docker daemon")
    if shutil.which("docker") is None:
        raise ToolError("docker not found on PATH")
    p = subprocess.run(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                       capture_output=True, text=True)
    host = p.stdout.strip()
    if p.returncode != 0:
        raise ToolError(f"docker context inspect failed: {p.stderr.strip()}")
    if not host.startswith("unix://"):
        raise Refused(f"docker context points at {host}; dbq only talks to the local Docker daemon")


class Pg:
    def __init__(self, container):
        self.container = container
        running = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", container],
                                 capture_output=True, text=True)
        if running.returncode != 0 or running.stdout.strip() != "true":
            raise ToolError(f"container {container} is not running (docker ps)")
        self.user = self._env("POSTGRES_USER")

    def _env(self, name):
        p = subprocess.run(["docker", "exec", self.container, "printenv", name], capture_output=True, text=True)
        if p.returncode != 0 or not p.stdout.strip():
            raise ToolError(f"container {self.container} has no {name}")
        return p.stdout.strip()

    def migrate_url(self, db):
        # INVARIANT: always localhost, built from the container's own published port and credentials; DATABASE_URL
        # from .env is never read, because it could point anywhere.
        p = subprocess.run(["docker", "port", self.container, "5432/tcp"], capture_output=True, text=True)
        m = re.search(r":(\d+)\s*$", p.stdout.splitlines()[0]) if p.returncode == 0 and p.stdout.strip() else None
        if not m:
            raise ToolError(f"container {self.container} publishes no 5432 port; migrate-up needs one")
        return f"postgresql://{self.user}:{self._env('POSTGRES_PASSWORD')}@localhost:{m.group(1)}/{db}?sslmode=disable"

    def psql(self, db, sql, write=False, raw=False, separator="|", quiet=False):
        cmd = ["docker", "exec", "-i", "-e", "PGCLIENTENCODING=UTF8"]
        if not write:
            # INVARIANT: read-only is enforced by Postgres for this whole session, not by reading the SQL; any
            # statement that writes fails with 25006 whatever text the guard let through.
            cmd += ["-e", "PGOPTIONS=-c default_transaction_read_only=on"]
        # INVARIANT: -h is the container's own socket, so a PGHOST in the container's env cannot redirect psql.
        cmd += [self.container, "psql", "-X", "-h", "/var/run/postgresql", "-v", "ON_ERROR_STOP=1",
                "-P", "pager=off", "-U", self.user, "-d", db]
        if raw:
            cmd += ["-At", "-F", separator]
        if raw or quiet:
            cmd += ["-q"]
        return subprocess.run(cmd, input=sql, capture_output=True, encoding="utf-8", errors="replace")

    def rows(self, db, sql, write=False):
        p = self.psql(db, sql, write=write, raw=True, separator=FIELD_SEP)
        if p.returncode != 0:
            if is_missing_database(p.stderr):
                raise ToolError(f"database {db} does not exist; see --list-dbs")
            raise ContentError(p.stderr.strip() or f"psql exit {p.returncode}")
        return [line.split(FIELD_SEP) for line in p.stdout.splitlines()]


def _columns(pg, db, table):
    return [r[0] for r in pg.rows(db, f"""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = '{table}' ORDER BY ordinal_position""")]


def _all_tables(pg, db):
    return [r[0] for r in pg.rows(db, """
        SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY 1""")]


def missing_hint(pg, db, sql, stderr):
    """Lines that turn a does-not-exist error into the real names, so the next attempt is not another guess."""
    missing = parse_missing(stderr)
    if not missing:
        return []
    if missing["kind"] == "relation":
        close = difflib.get_close_matches(missing["name"], _all_tables(pg, db), n=5, cutoff=0.4)
        return [f"hint: --tables {missing['name'][:4]}; closest tables: {', '.join(close) or '(none)'}"]
    tables = [missing["table"]] if missing["table"] else candidate_tables(sql, missing["qualifier"])
    lines = []
    for table in tables[:4]:
        cols = _columns(pg, db, table)
        if cols:
            close = difflib.get_close_matches(missing["name"], cols, n=5, cutoff=0.4) or cols[:12]
            lines.append(f"hint: --schema {table}; closest columns: {', '.join(close)}")
    return lines or ["hint: --schema <table> lists a table's real columns"]


def write_out(path, text):
    pathlib.Path(path).write_text(text, encoding="utf-8")


def auto_out_path():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="result-", suffix=".out", dir=str(LOG_DIR))
    os.close(fd)
    return name


def run_query(pg, args, db, sql):
    limit = DEFAULT_LIMIT if args.limit is None else args.limit
    mode = "write" if args.write else "read-only"
    p = pg.psql(db, sql, write=args.write, raw=args.raw)
    if args.out:
        write_out(args.out, p.stdout + p.stderr)
    if p.returncode != 0:
        return report_sql_failure(pg, db, sql, p, args.write)
    if p.stderr.strip():
        print(p.stderr.rstrip(), file=sys.stderr)
    if args.raw:
        return emit_raw(p.stdout, limit, args.out)
    t = truncate_output(p.stdout, limit)
    if t.truncated:
        full = args.out or auto_out_path()
        if not args.out:
            write_out(full, p.stdout)
        print(f"dbq ok db={db} {mode} TRUNCATED: {describe_cut(t, limit)}; full: {full}")
    else:
        rows = "no result table" if t.total_rows is None else f"rows={t.total_rows}"
        print(f"dbq ok db={db} {mode} {rows}" + (f"; full: {args.out}" if args.out else ""))
    sys.stdout.write(t.text)
    return EX_OK


def emit_raw(stdout, limit, out):
    lines = stdout.splitlines()
    if limit and len(lines) > limit:
        full = out or auto_out_path()
        if not out:
            write_out(full, stdout)
        sys.stdout.write("\n".join(lines[:limit]) + "\n")
        print(f"dbq TRUNCATED: showing the first {limit} of {len(lines)} lines; full: {full}", file=sys.stderr)
    else:
        sys.stdout.write(stdout)
    return EX_OK


def report_sql_failure(pg, db, sql, p, write):
    if is_missing_database(p.stderr):
        raise ToolError(f"database {db} does not exist in {pg.container}; see --list-dbs")
    if p.returncode != 3:
        raise ToolError(f"psql exit {p.returncode}: {p.stderr.strip()}")
    if p.stdout.strip():
        sys.stdout.write(p.stdout)
    if is_read_only_refusal(p.stderr):
        print(p.stderr.rstrip(), file=sys.stderr)
        print(f"dbq REFUSED db={db}: read-only by default. To write, use --write on your own clone (--clone).",
              file=sys.stderr)
        return EX_REFUSED
    print(p.stderr.rstrip(), file=sys.stderr)
    for line in missing_hint(pg, db, sql, p.stderr):
        print(line, file=sys.stderr)
    note = " Statements before the error may already be committed." if write else ""
    print(f"dbq FAILED db={db}: SQL error, stopped at the first one.{note}", file=sys.stderr)
    return EX_CONTENT


def run_schema(pg, args, db):
    table = args.schema
    found = pg.rows(db, f"SELECT to_regclass('{table}')::oid::text")
    oid = found[0][0] if found and found[0] else ""
    if not oid:
        close = difflib.get_close_matches(table.split(".")[-1], _all_tables(pg, db), n=5, cutoff=0.4)
        raise ContentError(f"no table {table} in {db}; closest: {', '.join(close) or '(none)'}")
    cols = pg.rows(db, f"""
        SELECT a.attname, format_type(a.atttypid, a.atttypmod), CASE WHEN a.attnotnull THEN 'NOT NULL' ELSE '' END,
               coalesce(pg_get_expr(d.adbin, d.adrelid), '')
        FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = {oid} AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum""")
    constraints = pg.rows(db, f"""
        SELECT contype::text, conname, pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = {oid} AND contype IN ('p', 'u', 'f', 'c', 'x') ORDER BY contype, conname""")
    triggers = pg.rows(db, f"""
        SELECT t.tgname, p.proname FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = {oid} AND NOT t.tgisinternal ORDER BY t.tgname""")
    inbound = pg.rows(db, f"SELECT count(*)::text FROM pg_constraint WHERE confrelid = {oid} AND contype = 'f'")[0][0]
    widths = [max(len(c[i]) for c in cols) for i in range(3)]
    out = [f"dbq ok db={db} table={table} columns={len(cols)} constraints={len(constraints)} "
           f"triggers={len(triggers)} inbound_fks={inbound}"]
    for c in cols:
        out.append(f"  {c[0]:<{widths[0]}}  {c[1]:<{widths[1]}}  {c[2]:<{widths[2]}}  {c[3]}".rstrip())
    labels = {"p": "PK", "u": "UNIQUE", "f": "FK", "c": "CHECK", "x": "EXCLUDE"}
    for kind, name, definition in constraints:
        out.append(f"{labels[kind]} {name}: {definition}")
    for name, func in triggers:
        out.append(f"TRIGGER {name}: {func}()")
    text = "\n".join(out) + "\n"
    if args.out:
        write_out(args.out, text)
    sys.stdout.write(text)
    return EX_OK


def run_tables(pg, args, db):
    # WHY the escape: table names are full of underscores, and a bare one in LIKE matches any character.
    pattern = args.tables.replace("_", "\\_").replace("*", "%").replace("?", "_")
    if not re.search(r"[%*?]", args.tables):
        pattern = f"%{pattern}%"
    rows = pg.rows(db, f"""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name ILIKE '{pattern}' ORDER BY 1""")
    if not rows:
        close = difflib.get_close_matches(args.tables, _all_tables(pg, db), n=5, cutoff=0.4)
        raise ContentError(f"no table matches {args.tables} in {db}; closest: {', '.join(close) or '(none)'}")
    body = "".join(r[0] + "\n" for r in rows)
    limit = DEFAULT_LIMIT if args.limit is None else args.limit
    t = truncate_output(body, limit)
    head = f"dbq ok db={db} tables={len(rows)}"
    if args.out:
        write_out(args.out, body)
    if t.truncated:
        full = args.out or auto_out_path()
        if not args.out:
            write_out(full, body)
        head += f" TRUNCATED: showing {limit}; full: {full}"
    print(head)
    sys.stdout.write(t.text)
    return EX_OK


def run_list_dbs(pg):
    rows = pg.rows("postgres", """
        SELECT d.datname, pg_size_pretty(pg_database_size(d.oid)),
               (SELECT count(*) FROM pg_stat_activity a WHERE a.datname = d.datname)::text,
               coalesce(shobj_description(d.oid, 'pg_database'), '')
        FROM pg_database d WHERE NOT d.datistemplate ORDER BY 1""")
    print(f"dbq ok container={pg.container} databases={len(rows)}")
    width = max(len(r[0]) for r in rows)
    for name, size, conns, comment in rows:
        origin = comment if comment.startswith(CLONE_MARK) else "not made by dbq"
        print(f"  {name:<{width}}  {size:>8}  conns={conns:<3}  {origin}")
    return EX_OK


def _comment(pg, db, comment):
    p = pg.psql("postgres", f"COMMENT ON DATABASE {db} IS '{comment}';", write=True)
    if p.returncode != 0:
        raise ToolError(f"cannot mark {db}: {p.stderr.strip()}")


def resolve_tree(tree):
    if tree:
        root = pathlib.Path(tree).resolve()
    else:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        if p.returncode != 0:
            raise ToolError("--clone: not inside a git tree; pass --tree <deepweave checkout>")
        root = pathlib.Path(p.stdout.strip())
    for needed in ("justfile", "db/migrations", "db/seeds/dev_seed.sql"):
        if not (root / needed).exists():
            raise ToolError(f"--clone: {root} has no {needed}; pass --tree <deepweave checkout>")
    if shutil.which("just") is None:
        raise ToolError("--clone: just not found on PATH")
    return root


def _abandon_clone(pg, name, log, why):
    # WHY: a half-built clone would hold the name and look usable; the log is the evidence, not the database.
    pg.psql("postgres", f"DROP DATABASE IF EXISTS {name};", write=True)
    raise ContentError(f"--clone {name}: {why}; database removed; log: {log}")


def run_clone(pg, args):
    name = args.clone
    tree = resolve_tree(args.tree)
    if pg.rows("postgres", f"SELECT 1 FROM pg_database WHERE datname = '{name}'"):
        raise ContentError(f"database {name} already exists; pick another name, or --drop it if it is yours")
    started = time.time()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"clone-{name}.log"
    # WHY: from zero like walk_env.sh rather than TEMPLATE deepweave: template creation fails while the shared DB has
    # connections (the dev gateway always does), and it would also miss this tree's uncommitted migrations.
    created = pg.psql("postgres", f"CREATE DATABASE {name};", write=True)
    if created.returncode != 0:
        raise ToolError(f"CREATE DATABASE {name}: {created.stderr.strip()}")
    _comment(pg, name, f"{CLONE_MARK}:pending")
    with open(log, "w", encoding="utf-8") as f:
        # WHY: public.mem0 belongs to the AI service, yet migrations index it; same stub as walk_env.sh and
        # internal/testutil/testutil.go, so a from-zero migrate passes.
        stub = pg.psql(name, "CREATE EXTENSION IF NOT EXISTS vector; CREATE TABLE IF NOT EXISTS public.mem0 "
                             "(id UUID PRIMARY KEY, vector vector(1536), payload JSONB);", write=True)
        f.write(stub.stdout + stub.stderr)
        if stub.returncode != 0:
            f.flush()
            _abandon_clone(pg, name, log, "mem0 stub failed")
        f.flush()
        env = {**_clean_env(), "MIGRATE_DATABASE_URL": pg.migrate_url(name)}
        mig = subprocess.run(["just", "--justfile", str(tree / "justfile"), "--working-directory", str(tree),
                              "migrate-up"], cwd=str(tree), env=env, stdout=f, stderr=subprocess.STDOUT)
        if mig.returncode != 0:
            _abandon_clone(pg, name, log, f"migrate-up exit {mig.returncode}")
        seed_dir = f"/tmp/dbq-seeds-{name}"
        subprocess.run(["docker", "exec", pg.container, "rm", "-rf", seed_dir], capture_output=True)
        cp = subprocess.run(["docker", "cp", f"{tree}/db/seeds/.", f"{pg.container}:{seed_dir}"], capture_output=True,
                            text=True)
        if cp.returncode != 0:
            _abandon_clone(pg, name, log, f"docker cp seeds failed: {cp.stderr.strip()}")
        seed = subprocess.run(["docker", "exec", pg.container, "psql", "-X", "-q", "-h", "/var/run/postgresql",
                               "-v", "ON_ERROR_STOP=1", "-U", pg.user, "-d", name, "-f", f"{seed_dir}/dev_seed.sql"],
                              stdout=f, stderr=subprocess.STDOUT)
        subprocess.run(["docker", "exec", pg.container, "rm", "-rf", seed_dir], capture_output=True)
        if seed.returncode != 0:
            _abandon_clone(pg, name, log, f"dev seed exit {seed.returncode}")
    head = subprocess.run(["git", "-C", str(tree), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    _comment(pg, name, f"{CLONE_MARK}:ready tree={tree.name} head={head.stdout.strip() or 'unknown'}")
    goose = pg.rows(name, "SELECT max(version_id)::text FROM goose_db_version")[0][0]
    print(f"dbq ok cloned {name} in {pg.container} in {time.time() - started:.0f}s: "
          f"migrated and dev-seeded from {tree} (goose version {goose})")
    print(f"log: {log}")
    print(f"next: --fixture all --db {name}; --drop {name} when done")
    return EX_OK


def _clean_env():
    env = dict(os.environ)
    # INVARIANT: migrate-up must see only MIGRATE_DATABASE_URL; its fallback is DATABASE_URL.
    env.pop("DATABASE_URL", None)
    return env


def run_drop(pg, args):
    name = args.drop
    found = pg.rows("postgres", f"""
        SELECT coalesce(shobj_description(oid, 'pg_database'), '') || ' ' FROM pg_database WHERE datname = '{name}'""")
    if not found:
        raise ContentError(f"database {name} does not exist; see --list-dbs")
    if not found[0][0].startswith(CLONE_MARK):
        # INVARIANT: other sessions keep walk and eval databases in this container under the same prefix.
        raise Refused(f"{name} was not created by dbq --clone, so it may be another session's; not dropped")
    conns = pg.rows("postgres", f"""
        SELECT count(*)::text, coalesce(string_agg(DISTINCT application_name, ','), '')
        FROM pg_stat_activity WHERE datname = '{name}'""")[0]
    if conns[0] != "0":
        raise ContentError(f"{name} has {conns[0]} live connection(s) ({conns[1]}); stop them first (a walk gateway?)")
    p = pg.psql("postgres", f"DROP DATABASE {name};", write=True)
    if p.returncode != 0:
        raise ContentError(f"DROP DATABASE {name}: {p.stderr.strip()}")
    print(f"dbq ok dropped {name}")
    return EX_OK


def _check_account(pg, db, f):
    problems = []
    fid = f.meta["id"]
    row = pg.rows(db, f"""
        SELECT u.email, coalesce(s.adult_status, 'unknown'), u.is_active::text
        FROM users u LEFT JOIN community_user_settings s ON s.user_id = u.id WHERE u.id = '{fid}'""")
    if not row:
        return [f"{f.name}: account {fid} is missing"], {}
    email, adult, active = row[0]
    if email != f.meta["email"]:
        problems.append(f"{f.name}: email is {email}, header says {f.meta['email']}")
    if adult != f.meta["adult_status"]:
        problems.append(f"{f.name}: adult_status is {adult}, header says {f.meta['adult_status']}")
    if active != "true":
        problems.append(f"{f.name}: account is not active")
    granted = {r[0]: int(r[1]) for r in pg.rows(db, f"""
        SELECT currency_type, sum(amount)::text FROM point_transactions
        WHERE user_id = '{fid}' AND idempotency_key LIKE 'fixture:{f.name}:%' GROUP BY 1""")}
    if granted != f.credits:
        problems.append(f"{f.name}: fixture grants in the ledger are {granted}, header says {f.credits}")
    balances = {r[0]: (int(r[1]), int(r[2])) for r in pg.rows(db, f"""
        SELECT b.currency_type, b.balance::text, coalesce((SELECT sum(t.amount) FROM point_transactions t
            WHERE t.user_id = b.user_id AND t.currency_type = b.currency_type), 0)::text
        FROM user_currency_balances b WHERE b.user_id = '{fid}'""")}
    for currency, (balance, ledger) in balances.items():
        if balance != ledger:
            problems.append(f"{f.name}: {currency} balance {balance} but ledger sums to {ledger}")
    return problems, {c: v[0] for c, v in balances.items()}


def _check_character(pg, db, f):
    row = pg.rows(db, f"SELECT is_nsfw::text, is_active::text, visibility FROM characters WHERE id = '{f.meta['id']}'")
    if not row:
        return [f"{f.name}: character {f.meta['id']} is missing"]
    problems = []
    if row[0][0] != f.meta["is_nsfw"]:
        problems.append(f"{f.name}: is_nsfw is {row[0][0]}, header says {f.meta['is_nsfw']}; "
                        "it cannot change after creation, drop this database")
    if row[0][1] != "true" or row[0][2] != "public":
        problems.append(f"{f.name}: expected active and public, found active={row[0][1]} visibility={row[0][2]}")
    return problems


def run_fixture(pg, args, db):
    fixtures = load_fixtures()
    order = resolve_fixtures(args.fixture, fixtures)
    prelude = (FIXTURE_DIR / "_prelude.sql").read_text(encoding="utf-8")
    sql = "BEGIN;\n" + prelude + "\n" + "\n".join(fixtures[n].sql for n in order) + "\nCOMMIT;\n"
    p = pg.psql(db, sql, write=True, quiet=True)
    if p.returncode != 0:
        if is_missing_database(p.stderr):
            raise ToolError(f"database {db} does not exist; --clone it first")
        print(p.stderr.rstrip(), file=sys.stderr)
        for line in missing_hint(pg, db, sql, p.stderr):
            print(line, file=sys.stderr)
        print(f"dbq FAILED db={db}: fixture SQL no longer fits this schema; rolled back, nothing seeded. "
              "Fix scripts/fixtures against --schema.", file=sys.stderr)
        return EX_CONTENT
    problems, lines = [], []
    for name in order:
        f = fixtures[name]
        if f.meta["kind"] == "account":
            found, balances = _check_account(pg, db, f)
            problems += found
            spent = "".join(f" (granted {a})" for c, a in f.credits.items() if balances.get(c, 0) != a)
            shown = " ".join(f"{c}={b}" for c, b in sorted(balances.items()) if b) or "no credits"
            lines.append(f"  {name:<13}{f.meta['email']:<30}{f.meta['adult_status']:<9}{shown}{spent}")
        else:
            problems += _check_character(pg, db, f)
            lines.append(f"  {name:<13}{f.meta['id']:<39}is_nsfw={f.meta['is_nsfw']}")
    if problems:
        print(f"dbq FAILED db={db}: fixtures applied but the post-check does not match", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return EX_CONTENT
    print(f"dbq ok db={db} fixtures={len(order)}; password {FIXTURE_PASSWORD}")
    print("\n".join(lines))
    return EX_OK


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"dbq: {message}", file=sys.stderr)
        sys.exit(EX_TOOL)


def build_parser():
    # INVARIANT: no host, port or URL option, and allow_abbrev off so a prefix cannot resolve to something else.
    p = Parser(description="Query and seed the local deepweave Postgres container.", allow_abbrev=False)
    p.add_argument("sql", nargs="?")
    p.add_argument("--file")
    p.add_argument("--schema")
    p.add_argument("--tables")
    p.add_argument("--clone")
    p.add_argument("--drop")
    p.add_argument("--list-dbs", action="store_true")
    p.add_argument("--fixture")
    p.add_argument("--list-fixtures", action="store_true")
    p.add_argument("--db")
    p.add_argument("--container", default=DEFAULT_CONTAINER)
    p.add_argument("--write", action="store_true")
    p.add_argument("--allow-shared-write", action="store_true")
    p.add_argument("--raw", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--out")
    p.add_argument("--tree")
    return p


def validate(args):
    """Everything decidable without Docker, so a refusal never depends on a daemon being up."""
    actions = [a for a, on in (("sql", args.sql is not None), ("file", args.file), ("schema", args.schema),
                               ("tables", args.tables), ("clone", args.clone), ("drop", args.drop),
                               ("list_dbs", args.list_dbs), ("fixture", args.fixture)) if on]
    if len(actions) > 1:
        raise ToolError(f"pick one action, got {', '.join(actions)}")
    if not actions:
        raise ToolError("no action: give SQL, --file, --schema, --tables, --clone, --drop, --list-dbs, "
                        "--fixture or --list-fixtures")
    action = actions[0]
    if args.limit is not None and args.limit < 0:
        raise ToolError("--limit must be >= 0")
    if action == "fixture" and (args.write or args.allow_shared_write):
        raise Refused("--fixture has no override: it only ever applies to a clone named deepweave_*")
    if (args.write or args.allow_shared_write) and action not in ("sql", "file"):
        raise ToolError("--write only applies to SQL or --file")
    if args.allow_shared_write and not args.write:
        raise ToolError("--allow-shared-write needs --write")
    if (args.raw or args.limit is not None) and action not in ("sql", "file") and not (
            action == "tables" and not args.raw):
        raise ToolError("--raw and --limit only apply to queries")
    if args.out and action not in ("sql", "file", "schema", "tables"):
        raise ToolError("--out only applies to queries, --schema and --tables")
    if args.tree and action != "clone":
        raise ToolError("--tree only applies to --clone")
    if args.db and action in ("clone", "drop", "list_dbs"):
        raise ToolError(f"--db does not apply to --{action.replace('_', '-')}; the name is its argument")
    if args.out and not pathlib.Path(args.out).resolve().parent.is_dir():
        raise ToolError(f"--out: no directory {pathlib.Path(args.out).resolve().parent}")
    if args.container and not IDENT_RE.match(args.container):
        raise ToolError(f"--container: bad name {args.container!r}")
    if PROD_RE.search(args.container):
        raise Refused(f"container {args.container} looks like production")
    db = args.db or SHARED_DB
    if not IDENT_RE.match(db):
        raise ToolError(f"--db: bad name {db!r}")
    if PROD_RE.search(db):
        raise Refused(f"database {db} looks like production")
    if action in ("clone", "drop"):
        name = args.clone or args.drop
        if not CLONE_RE.match(name):
            raise Refused(f"--{action}: {name!r} is not allowed; the name must match {CLONE_RE.pattern} "
                          f"(the shared {SHARED_DB} is never a target)")
        if PROD_RE.search(name):
            raise Refused(f"database {name} looks like production")
    if action == "fixture":
        load = load_fixtures()
        if not CLONE_RE.match(db):
            raise Refused(f"--fixture only applies to a clone named deepweave_*, not {db}")
        resolve_fixtures(args.fixture, load)
    if action == "schema" and not TABLE_RE.match(args.schema):
        raise ToolError(f"--schema: bad table name {args.schema!r}")
    if action == "tables" and not PATTERN_RE.match(args.tables):
        raise ToolError(f"--tables: bad pattern {args.tables!r}; letters, digits, _, %, *, ?")
    if args.write and action in ("sql", "file"):
        if db.lower() == SHARED_DB:
            if not args.allow_shared_write:
                raise Refused(f"--write on the shared {SHARED_DB} is refused; --clone your own and write there")
        elif not CLONE_RE.match(db):
            raise Refused(f"--write only applies to a clone named deepweave_*, not {db}")
    sql = None
    if action == "file":
        path = pathlib.Path(args.file)
        if not path.is_file():
            raise ToolError(f"--file: no file {path}")
        sql = path.read_text(encoding="utf-8")
    elif action == "sql":
        sql = args.sql
    if sql is not None:
        if not sql.strip():
            raise ToolError("the SQL is empty")
        reasons = scan_sql(sql, write=args.write)
        if reasons:
            raise Refused("SQL refused: " + "; ".join(reasons) +
                          (". Reads run read-only; write on your own clone with --write." if not args.write else "."))
    return action, db, sql


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.list_fixtures and not args.fixture:
            if args.sql or args.file or args.schema or args.tables or args.clone or args.drop or args.list_dbs:
                raise ToolError("--list-fixtures takes no other action")
            list_fixtures()
            return EX_OK
        action, db, sql = validate(args)
        assert_local_docker(os.environ.get("DOCKER_HOST"))
        pg = Pg(args.container)
        if args.allow_shared_write:
            print(f"dbq WARNING: writing to the shared {SHARED_DB}, which other sessions use", file=sys.stderr)
        if action in ("sql", "file"):
            return run_query(pg, args, db, sql)
        if action == "schema":
            return run_schema(pg, args, db)
        if action == "tables":
            return run_tables(pg, args, db)
        if action == "list_dbs":
            return run_list_dbs(pg)
        if action == "clone":
            return run_clone(pg, args)
        if action == "drop":
            return run_drop(pg, args)
        code = run_fixture(pg, args, db)
        if args.list_fixtures:
            list_fixtures()
        return code
    except Refused as e:
        print(f"dbq REFUSED: {e}", file=sys.stderr)
        return EX_REFUSED
    except ContentError as e:
        print(f"dbq FAILED: {e}", file=sys.stderr)
        return EX_CONTENT
    except ToolError as e:
        print(f"dbq ERROR: {e}", file=sys.stderr)
        return EX_TOOL


if __name__ == "__main__":
    sys.exit(main())
