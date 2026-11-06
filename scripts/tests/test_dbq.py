# -*- coding: utf-8 -*-
"""dbq: reads cannot write (Postgres itself refuses), the shared DB cannot be written or fixtured, SQL errors exit
non-zero with a schema hint, long results are cut only with the total and the full file, fixtures reach balances
only through the ledger trigger and converge on rerun.

Engine classes run against the real local deepweave-postgres on databases named deepweave_dbq_ut_<pid>*; they
fail, not skip, when the container is missing, unless DBQ_SKIP_ENGINE=1 says so out loud. A green run with engine
tests skipped would read as "read-only is pinned" when it is not. The one test that touches the shared DB sends a
zero-row UPDATE, so even a broken guard changes nothing there. DBQ_TEST_TREE picks the tree that --clone migrates
from (default: the main checkout, whose migrations are only read).

Usage: python3 -m unittest discover -s scripts/tests -p 'test_dbq.py' -v
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import dbq  # noqa: E402

SCRIPT = SCRIPTS / "dbq.py"
REAL = pathlib.Path(__file__).resolve().parent / "fixtures_dbq"
MAIN = pathlib.Path("/Users/huntwu/Desktop/deepweave/deepweave")
TREE = pathlib.Path(os.environ.get("DBQ_TEST_TREE") or MAIN)
PG = "deepweave-postgres"
ENGINE = os.environ.get("DBQ_SKIP_ENGINE") != "1"
LIGHT = f"deepweave_dbq_ut_{os.getpid()}"
FULL = f"deepweave_dbq_ut_full_{os.getpid()}"
ACCOUNTS = {"creator", "minor", "adult", "unknown", "zero"}


def run(*args, env=None, cwd=None):
    full_env = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True,
                          env=full_env, cwd=cwd, timeout=900)


def admin(db, sql):
    """Test setup that must not depend on the tool under test."""
    p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-U", "deepweave",
                        "-d", db], input=sql, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise AssertionError(f"admin sql failed: {p.stderr}")
    return p.stdout.strip()


def create_empty(db):
    admin("postgres", f"CREATE DATABASE {db};")


def drop_db(db):
    admin("postgres", f"DROP DATABASE IF EXISTS {db} WITH (FORCE);")


def out(p):
    return p.stdout + p.stderr


class ScanTest(unittest.TestCase):
    """The guard reads SQL as psql does: strings and comments are not commands, dollar bodies are not metacommands."""

    def refused(self, sql, write=False):
        return dbq.scan_sql(sql, write=write)

    def test_ordinary_sql_passes_including_backslashes_inside_strings(self):
        for sql in ("SELECT 1;",
                    r"SELECT phone ~ '^\+[1-9]\d{6}$' FROM users;",
                    "SELECT 1 -- \\connect prod\n;",
                    "/* \\! rm -rf / */ SELECT 1;",
                    r"DO $$ BEGIN PERFORM 'x' ~ '\d'; END $$;",
                    "\\set creator '\\'01a175ea\\''\nSELECT :creator;",
                    "\\pset format unaligned\n\\echo hello\n\\if true\n\\endif\nSELECT 1;",
                    "SELECT 1 \\gset\n",
                    "\\d users\n"):
            self.assertEqual(self.refused(sql), [], sql)

    def test_metacommands_that_leave_the_database_are_refused_in_both_modes(self):
        for sql in ("\\connect deepweave\nUPDATE users SET role = 'x';", "\\c deepweave", "\\! curl evil",
                    "\\i /etc/passwd", "\\ir other.sql", "\\o /tmp/x", "\\copy users to '/tmp/x'",
                    "SELECT 1 \\g /tmp/x\n", "SELECT 'x' \\gexec\n", "\\set x `whoami`\n", "\\setenv PGHOST prod\n"):
            self.assertTrue(self.refused(sql, write=False), sql)
            self.assertTrue(self.refused(sql, write=True), sql)

    def test_cluster_and_remote_reach_is_refused_in_both_modes(self):
        for sql in ("DROP DATABASE deepweave;", "ALTER SYSTEM SET x = 1;", "CREATE ROLE evil;",
                    "COPY users TO PROGRAM 'curl evil';", "SELECT dblink('host=prod', 'select 1');",
                    "CREATE EXTENSION postgres_fdw;"):
            self.assertTrue(self.refused(sql, write=False), sql)
            self.assertTrue(self.refused(sql, write=True), sql)

    def test_turning_read_only_off_is_refused_only_in_read_only_mode(self):
        for sql in ("SET default_transaction_read_only = off; UPDATE users SET role = 'x';",
                    "BEGIN READ WRITE; UPDATE users SET role = 'x'; COMMIT;",
                    "START TRANSACTION READ\n WRITE;",
                    "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE;",
                    "SET transaction_read_only = off;",
                    "SELECT set_config('default_transaction_read_only', 'off', false);",
                    "DO $$ BEGIN PERFORM set_config('transaction_read_only', 'off', true); END $$;",
                    "SELECT pg_terminate_backend(1);"):
            self.assertTrue(self.refused(sql, write=False), sql)
        self.assertEqual(self.refused("BEGIN READ WRITE; COMMIT;", write=True), [])


class TruncateTest(unittest.TestCase):
    def setUp(self):
        self.real = (REAL / "aligned_multi.txt").read_text(encoding="utf-8")

    def test_real_multi_result_output_cuts_only_the_long_block_and_counts_a_wrapped_row_once(self):
        t = dbq.truncate_output(self.real, 50)
        self.assertTrue(t.truncated)
        self.assertEqual(t.total_rows, 61)
        self.assertEqual(t.shown_rows, 51)
        lines = t.text.splitlines()
        self.assertIn(" 50 | ", "\n".join(lines))
        self.assertNotIn(" 51 | ", "\n".join(lines))
        self.assertIn("(60 rows)", t.text, "the block's total must survive the cut")
        self.assertIn("marker", t.text, "output after the long block is kept")
        self.assertIn("(1 row)", t.text)
        self.assertIn("(0 rows)", t.text)

    def test_within_limit_is_passed_through_byte_for_byte(self):
        t = dbq.truncate_output(self.real, 60)
        self.assertFalse(t.truncated)
        self.assertEqual(t.text, self.real)
        self.assertEqual(t.total_rows, 61)

    def test_zero_limit_means_no_limit(self):
        t = dbq.truncate_output(self.real, 0)
        self.assertFalse(t.truncated)
        self.assertEqual(t.text, self.real)

    def test_output_without_footers_is_still_cut_by_lines(self):
        # \pset tuples_only on / format csv appear in real kits: no footer to read, so no row total to trust.
        text = "".join(f"{i}\n" for i in range(200))
        t = dbq.truncate_output(text, 50)
        self.assertTrue(t.truncated)
        self.assertEqual(len(t.text.splitlines()), 50)
        self.assertIsNone(t.total_rows, "no footer means no row total, not a guessed one")
        self.assertEqual(t.total_lines, 200)

    def test_a_checks_file_with_many_short_results_is_not_cut(self):
        # Real checks.sql files run dozens of short SELECTs; their headers and blank lines are not "long output".
        block = " n \n---\n 1\n(1 row)\n\n"
        text = "\\echo x\n" + block * 40
        t = dbq.truncate_output(text, 50)
        self.assertFalse(t.truncated)
        self.assertEqual(t.text, text)
        self.assertEqual(t.total_rows, 40)

    def test_long_plain_output_between_results_is_marked_and_not_reported_as_all_rows_shown(self):
        block = " n \n---\n 1\n(1 row)\n\n"
        text = block + "".join(f"line {i}\n" for i in range(80)) + block
        t = dbq.truncate_output(text, 50)
        self.assertTrue(t.truncated)
        self.assertRegex(t.text, r"\.\.\. \d+ more lines not shown")
        self.assertEqual(t.text.count("(1 row)"), 2, "the result after the cut must still show")
        self.assertIn("lines", dbq.describe_cut(t, 50))
        self.assertNotIn("2 of 2", dbq.describe_cut(t, 50))

    def test_the_result_after_a_plain_cut_keeps_its_own_header_and_nothing_else_moves(self):
        first, second = " first \n---\n 1\n(1 row)\n\n", " second \n--------\n 2\n(1 row)\n\n"
        text = first + "".join(f"line {i}\n" for i in range(80)) + second
        t = dbq.truncate_output(text, 50)
        lines = t.text.splitlines()
        self.assertIn(" second ", lines)
        at = lines.index(" second ")
        self.assertRegex(lines[at - 1], r"^\.\.\. \d+ more lines not shown$")
        self.assertEqual(lines[at + 1], "--------")
        self.assertEqual(sum(1 for line in lines if line.startswith("line ")), len(set(
            line for line in lines if line.startswith("line "))), "no kept line may appear twice")

    def test_cut_rows_are_described_as_rows(self):
        t = dbq.truncate_output((REAL / "aligned_multi.txt").read_text(encoding="utf-8"), 50)
        self.assertIn("51 of 61 rows", dbq.describe_cut(t, 50))


class MissingParseTest(unittest.TestCase):
    def err(self, name):
        return (REAL / name).read_text(encoding="utf-8")

    def test_real_errors(self):
        self.assertEqual(dbq.parse_missing(self.err("err_alias_column.txt")),
                         {"kind": "column", "name": "currency", "qualifier": "pt", "table": None})
        self.assertEqual(dbq.parse_missing(self.err("err_bare_column.txt")),
                         {"kind": "column", "name": "nope", "qualifier": None, "table": None})
        self.assertEqual(dbq.parse_missing(self.err("err_of_relation.txt")),
                         {"kind": "column", "name": "nope", "qualifier": None, "table": "characters"})
        self.assertEqual(dbq.parse_missing(self.err("err_relation.txt")),
                         {"kind": "relation", "name": "charactrs", "qualifier": None, "table": None})
        self.assertIsNone(dbq.parse_missing(self.err("err_readonly.txt")))

    def test_candidate_tables_resolve_the_alias_the_error_names(self):
        sql = "SELECT pt.currency FROM point_transactions pt JOIN users AS u ON u.id = pt.user_id"
        self.assertEqual(dbq.candidate_tables(sql, "pt"), ["point_transactions"])
        self.assertEqual(dbq.candidate_tables(sql, "u"), ["users"])
        self.assertEqual(dbq.candidate_tables(sql, "users"), ["users"])
        self.assertEqual(dbq.candidate_tables(sql, None), ["point_transactions", "users"])
        self.assertEqual(dbq.candidate_tables("UPDATE public.users SET x = 1", None), ["users"])

    def test_error_classes_are_told_apart(self):
        self.assertTrue(dbq.is_read_only_refusal(self.err("err_readonly.txt")))
        self.assertFalse(dbq.is_read_only_refusal(self.err("err_bare_column.txt")))
        self.assertTrue(dbq.is_missing_database(self.err("err_nodb.txt")))
        self.assertFalse(dbq.is_missing_database(self.err("err_relation.txt")))


class FixtureFilesTest(unittest.TestCase):
    def setUp(self):
        self.fx = dbq.load_fixtures()

    def test_the_roles_walks_need_all_exist_with_fixed_identity(self):
        self.assertTrue(ACCOUNTS <= set(self.fx), sorted(self.fx))
        self.assertTrue({"char_general", "char_nsfw"} <= set(self.fx), sorted(self.fx))
        ids = [f.meta["id"] for f in self.fx.values()]
        self.assertEqual(len(ids), len(set(ids)))
        for name in ACCOUNTS:
            self.assertRegex(self.fx[name].meta["email"], r"^fx-[a-z]+@deepweave\.local$")

    def test_no_fixture_writes_a_balance_directly(self):
        for path in [*dbq.FIXTURE_DIR.glob("*.sql")]:
            sql = dbq.split_sql(path.read_text(encoding="utf-8")).code.lower()
            self.assertNotRegex(sql, r"user_currency_balances", path.name)
            self.assertNotRegex(sql, r"update\s+\w*balance", path.name)
            self.assertNotRegex(sql, r"set\s+balance", path.name)

    def test_no_daily_point_grants(self):
        # WHY: the daily sweep resets non-subscribers' daily_point to 0, so a listed daily balance would go stale.
        for f in self.fx.values():
            self.assertNotIn("daily_point", f.credits, f.name)

    def test_credits_are_a_ledger_grant_in_the_sql_not_only_a_header_claim(self):
        for f in self.fx.values():
            for currency, amount in f.credits.items():
                self.assertRegex(f.sql, rf"fx_grant\(\s*'{f.meta['id']}',\s*'{currency}',\s*{amount},")

    def test_zero_account_has_no_credits(self):
        self.assertEqual(self.fx["zero"].credits, {})
        self.assertNotIn("fx_grant(", self.fx["zero"].sql)

    def test_password_hash_is_the_dev_seed_hash_and_walk_password_matches_its_header(self):
        prelude = (dbq.FIXTURE_DIR / "_prelude.sql").read_text(encoding="utf-8")
        hashes = set(re.findall(r"\$argon2id\$[^']+", prelude))
        self.assertEqual(len(hashes), 1)
        seed = (MAIN / "db/seeds/dev_seed.sql").read_text(encoding="utf-8")
        self.assertIn(hashes.pop(), seed)
        self.assertRegex(seed[:200], r"deepweave-demo-123")
        self.assertIn("password: 'deepweave-demo-123'", (SCRIPTS / "walk_lib.mjs").read_text(encoding="utf-8"))

    def test_dependencies_come_first_and_all_expands(self):
        self.assertEqual(dbq.resolve_fixtures("char_nsfw", self.fx), ["creator", "char_nsfw"])
        order = dbq.resolve_fixtures("all", self.fx)
        self.assertEqual(sorted(order), sorted(self.fx))
        self.assertLess(order.index("creator"), order.index("char_general"))
        with self.assertRaises(dbq.ToolError):
            dbq.resolve_fixtures("nope", self.fx)
        with self.assertRaises(dbq.ToolError):
            dbq.resolve_fixtures("", self.fx)


class CliRefusalTest(unittest.TestCase):
    """Refusals happen before anything reaches Docker, so no docker on PATH must not change the code."""

    NO_DOCKER = {"PATH": "/nonexistent"}

    def assert_refused(self, *args, code=dbq.EX_REFUSED):
        p = run(*args, env=self.NO_DOCKER)
        self.assertEqual(p.returncode, code, out(p))
        self.assertTrue(out(p).strip(), "a refusal must say why")
        return p

    def test_write_to_shared_db_is_refused_by_the_tool(self):
        self.assert_refused("--write", "--db", "deepweave", "UPDATE users SET role = 'x'")
        self.assert_refused("--write", "UPDATE users SET role = 'x'")

    def test_write_is_refused_on_anything_that_is_not_a_clone_name(self):
        self.assert_refused("--write", "--db", "postgres", "SELECT 1")
        self.assert_refused("--write", "--db", "template1", "SELECT 1")

    def test_fixture_on_shared_db_is_refused_even_with_the_override(self):
        self.assert_refused("--fixture", "all", "--db", "deepweave")
        self.assert_refused("--fixture", "all", "--db", "deepweave", "--allow-shared-write")
        self.assert_refused("--fixture", "all", "--db", "DEEPWEAVE")
        self.assert_refused("--fixture", "all", "--db", "postgres")
        self.assert_refused("--fixture", "all")

    def test_drop_and_clone_name_rules(self):
        self.assert_refused("--drop", "deepweave")
        self.assert_refused("--drop", "postgres")
        self.assert_refused("--clone", "deepweave")
        self.assert_refused("--clone", "scratch")
        self.assert_refused("--clone", "deepweave_x; DROP DATABASE deepweave")
        self.assert_refused("--drop", "deepweave_x; DROP DATABASE deepweave")

    def test_prod_looking_targets_are_refused(self):
        self.assert_refused("--container", "deepweave-prod-postgres", "SELECT 1")
        self.assert_refused("--container", "postgres_production", "SELECT 1")
        self.assert_refused("--db", "deepweave_prod", "SELECT 1")
        self.assert_refused("--clone", "deepweave_prod")

    def test_there_is_no_way_to_name_a_host_or_url(self):
        for flag in ("--host", "--port", "--url", "--database-url", "--dsn"):
            p = run(flag, "prod.example.com", "SELECT 1", env=self.NO_DOCKER)
            self.assertEqual(p.returncode, dbq.EX_TOOL, flag)

    def test_docker_pointed_at_a_remote_daemon_is_refused(self):
        for host in ("tcp://prod.example.com:2376", "ssh://root@prod.example.com"):
            p = run("SELECT 1", env={"DOCKER_HOST": host})
            self.assertEqual(p.returncode, dbq.EX_REFUSED, out(p))
            self.assertIn("DOCKER_HOST", out(p))

    def test_sql_guard_exit_code(self):
        self.assert_refused("--db", "deepweave_x", "SET default_transaction_read_only = off")
        self.assert_refused("--write", "--db", "deepweave_x", "\\connect deepweave")

    def test_parameter_errors_exit_2(self):
        for args in ([], ["SELECT 1", "--file", "x.sql"], ["--file", "/nonexistent/x.sql"], ["   "],
                     ["--schema", "users", "--write", "--db", "deepweave_x"],
                     ["--allow-shared-write", "SELECT 1"], ["--db", "bad name", "SELECT 1"],
                     ["--schema", "users; DROP TABLE x"], ["--tables", "a; b"],
                     ["--fixture", "nope", "--db", "deepweave_x"], ["--limit", "-1", "SELECT 1"]):
            p = run(*args, env=self.NO_DOCKER)
            self.assertEqual(p.returncode, dbq.EX_TOOL, f"{args}: {out(p)}")

    def test_docker_missing_is_a_tool_error_not_a_sql_error(self):
        p = run("SELECT 1", env=self.NO_DOCKER)
        self.assertEqual(p.returncode, dbq.EX_TOOL, out(p))

    def test_list_fixtures_needs_no_database_and_names_every_account(self):
        p = run("--list-fixtures", env=self.NO_DOCKER)
        self.assertEqual(p.returncode, 0, out(p))
        for email in ("fx-creator@", "fx-minor@", "fx-adult@", "fx-unknown@", "fx-zero@"):
            self.assertIn(email, p.stdout)
        self.assertIn("permanent_point=", p.stdout)
        self.assertIn("deepweave-demo-123", p.stdout)


@unittest.skipUnless(ENGINE, "DBQ_SKIP_ENGINE=1")
class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_empty(LIGHT)
        cls.addClassCleanup(drop_db, LIGHT)
        admin(LIGHT, """
            CREATE TABLE parent (id INT PRIMARY KEY);
            CREATE TABLE widgets (id INT PRIMARY KEY, widget_name TEXT NOT NULL DEFAULT 'x',
                                  widget_size INT CHECK (widget_size > 0),
                                  parent_id INT REFERENCES parent(id) ON DELETE CASCADE);
            CREATE FUNCTION noop() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;
            CREATE TRIGGER trg_widgets_noop BEFORE INSERT ON widgets FOR EACH ROW EXECUTE FUNCTION noop();
            INSERT INTO widgets (id) SELECT g FROM generate_series(1, 60) g;
        """)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)

    def test_read_defaults_to_the_shared_db_and_opens_with_a_conclusion(self):
        p = run("SELECT current_database() AS db")
        self.assertEqual(p.returncode, 0, out(p))
        self.assertIn("deepweave", p.stdout)
        first = p.stdout.splitlines()[0]
        self.assertIn("ok", first)
        self.assertIn("read-only", first)
        self.assertIn("rows=1", first)

    def test_env_urls_cannot_redirect_the_tool(self):
        p = run("SELECT current_database() AS db",
                env={"DATABASE_URL": "postgresql://x:y@prod.example.com:5432/prod",
                     "PGHOST": "prod.example.com", "MIGRATE_DATABASE_URL": "postgresql://prod.example.com/prod"})
        self.assertEqual(p.returncode, 0, out(p))
        self.assertIn("deepweave", p.stdout)

    def test_read_only_update_on_the_shared_db_is_rejected_by_postgres(self):
        p = run("UPDATE users SET updated_at = updated_at WHERE false")
        self.assertEqual(p.returncode, dbq.EX_REFUSED, out(p))
        self.assertIn("cannot execute UPDATE in a read-only transaction", out(p))

    def test_read_only_mode_on_a_clone_leaves_data_unchanged(self):
        p = run("--db", LIGHT, "INSERT INTO parent VALUES (1)")
        self.assertEqual(p.returncode, dbq.EX_REFUSED, out(p))
        self.assertIn("read-only transaction", out(p))
        self.assertEqual(admin(LIGHT, "SELECT count(*) FROM parent"), "0")
        p = run("--db", LIGHT, "CREATE TABLE sneaky (id INT)")
        self.assertEqual(p.returncode, dbq.EX_REFUSED, out(p))
        self.assertEqual(admin(LIGHT, "SELECT to_regclass('sneaky') IS NULL"), "t")

    def test_write_mode_writes(self):
        p = run("--write", "--db", LIGHT, "INSERT INTO parent VALUES (42) ON CONFLICT DO NOTHING")
        self.assertEqual(p.returncode, 0, out(p))
        self.assertEqual(admin(LIGHT, "SELECT count(*) FROM parent WHERE id = 42"), "1")
        self.assertIn("write", p.stdout.splitlines()[0])

    def test_sql_error_exits_1_and_stops_the_rest_of_the_file(self):
        f = pathlib.Path(self.tmp.name) / "stop.sql"
        f.write_text("INSERT INTO parent VALUES (7) ON CONFLICT DO NOTHING;\nSELECT syntax error here;\n"
                     "INSERT INTO parent VALUES (8) ON CONFLICT DO NOTHING;\n", encoding="utf-8")
        p = run("--write", "--db", LIGHT, "--file", f)
        self.assertEqual(p.returncode, dbq.EX_CONTENT, out(p))
        self.assertIn("syntax error", out(p))
        self.assertEqual(admin(LIGHT, "SELECT count(*) FROM parent WHERE id = 8"), "0")

    def test_mistyped_column_hints_schema_and_the_closest_real_columns(self):
        p = run("--db", LIGHT, "SELECT w.widget_nam FROM widgets w")
        self.assertEqual(p.returncode, dbq.EX_CONTENT, out(p))
        self.assertIn("--schema widgets", out(p))
        self.assertIn("widget_name", out(p))
        p = run("--db", LIGHT, "SELECT widgt_size FROM widgets")
        self.assertEqual(p.returncode, dbq.EX_CONTENT, out(p))
        self.assertIn("widget_size", out(p))
        p = run("--db", LIGHT, "--write", "UPDATE widgets SET widget_nam = 'x' WHERE false")
        self.assertEqual(p.returncode, dbq.EX_CONTENT, out(p))
        self.assertIn("widget_name", out(p))

    def test_real_guess_from_the_transcripts_gets_the_right_answer(self):
        p = run("SELECT pt.currency FROM point_transactions pt LIMIT 1")
        self.assertEqual(p.returncode, dbq.EX_CONTENT, out(p))
        self.assertIn("--schema point_transactions", out(p))
        self.assertIn("currency_type", out(p))

    def test_mistyped_table_hints_the_closest_tables(self):
        p = run("--db", LIGHT, "SELECT * FROM widgts")
        self.assertEqual(p.returncode, dbq.EX_CONTENT, out(p))
        self.assertIn("widgets", out(p))
        self.assertIn("--tables", out(p))

    def test_over_limit_reports_the_total_and_writes_the_full_result(self):
        target = pathlib.Path(self.tmp.name) / "full.out"
        p = run("--db", LIGHT, "--out", target, "SELECT id FROM widgets ORDER BY id")
        self.assertEqual(p.returncode, 0, out(p))
        first = p.stdout.splitlines()[0]
        self.assertIn("TRUNCATED", first)
        self.assertIn("50", first)
        self.assertIn("60", first)
        self.assertIn(str(target), p.stdout)
        self.assertLess(len(p.stdout.splitlines()), 60)
        full = target.read_text(encoding="utf-8")
        self.assertIn(" 60\n", full)
        self.assertIn("(60 rows)", full)

    def test_over_limit_without_out_still_writes_a_file_and_names_it(self):
        p = run("--db", LIGHT, "SELECT id FROM widgets ORDER BY id")
        self.assertEqual(p.returncode, 0, out(p))
        m = re.search(r"full: (\S+)", p.stdout)
        self.assertTrue(m, p.stdout)
        path = pathlib.Path(m.group(1))
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        self.assertIn("(60 rows)", path.read_text(encoding="utf-8"))

    def test_limit_zero_prints_everything(self):
        p = run("--db", LIGHT, "--limit", "0", "SELECT id FROM widgets ORDER BY id")
        self.assertIn(" 60\n", p.stdout)
        self.assertNotIn("TRUNCATED", p.stdout)

    def test_raw_is_pipe_separated_without_a_conclusion_line(self):
        p = run("--db", LIGHT, "--raw", "SELECT id, widget_name FROM widgets WHERE id <= 2 ORDER BY id")
        self.assertEqual(p.returncode, 0, out(p))
        self.assertEqual(p.stdout, "1|x\n2|x\n")

    def test_raw_over_limit_warns_on_stderr_and_keeps_stdout_parseable(self):
        target = pathlib.Path(self.tmp.name) / "raw.out"
        p = run("--db", LIGHT, "--raw", "--out", target, "SELECT id FROM widgets ORDER BY id")
        self.assertEqual(p.returncode, 0, out(p))
        self.assertEqual(len(p.stdout.splitlines()), 50)
        self.assertIn("TRUNCATED", p.stderr)
        self.assertEqual(len(target.read_text(encoding="utf-8").splitlines()), 60)

    def test_file_runs(self):
        f = pathlib.Path(self.tmp.name) / "checks.sql"
        f.write_text("\\echo first\nSELECT count(*) AS n FROM widgets;\n", encoding="utf-8")
        p = run("--db", LIGHT, "--file", f)
        self.assertEqual(p.returncode, 0, out(p))
        self.assertIn("first", p.stdout)
        self.assertIn(" 60\n", p.stdout)

    def test_schema_shows_what_a_query_author_guesses_wrong(self):
        p = run("--db", LIGHT, "--schema", "widgets")
        self.assertEqual(p.returncode, 0, out(p))
        for needle in ("widget_name", "text", "NOT NULL", "'x'", "parent_id", "parent(id)", "CASCADE",
                       "trg_widgets_noop", "widget_size > 0"):
            self.assertIn(needle, p.stdout, needle)

    def test_schema_of_a_missing_table_fails_with_the_closest(self):
        p = run("--db", LIGHT, "--schema", "widgts")
        self.assertEqual(p.returncode, dbq.EX_CONTENT, out(p))
        self.assertIn("widgets", out(p))

    def test_tables(self):
        p = run("--db", LIGHT, "--tables", "widg")
        self.assertEqual(p.returncode, 0, out(p))
        self.assertIn("widgets", p.stdout)
        self.assertNotIn("parent", p.stdout.split("\n", 1)[1])
        p = run("--db", LIGHT, "--tables", "no_such_thing")
        self.assertEqual(p.returncode, dbq.EX_CONTENT, out(p))

    def test_missing_database_is_a_parameter_error_with_a_pointer(self):
        p = run("--db", "deepweave_no_such_db", "SELECT 1")
        self.assertEqual(p.returncode, dbq.EX_TOOL, out(p))
        self.assertIn("--list-dbs", out(p))

    def test_shared_write_override_warns_out_loud(self):
        p = run("--write", "--allow-shared-write", "--db", "deepweave", "SELECT 1 AS one")
        self.assertEqual(p.returncode, 0, out(p))
        self.assertIn("WARNING", p.stderr)

    def test_fixture_on_a_missing_database_is_a_parameter_error(self):
        p = run("--fixture", "zero", "--db", "deepweave_no_such_db")
        self.assertEqual(p.returncode, dbq.EX_TOOL, out(p))

    def test_statements_without_rows_do_not_claim_a_row_count(self):
        p = run("--write", "--db", LIGHT, "INSERT INTO parent VALUES (43) ON CONFLICT DO NOTHING")
        self.assertEqual(p.returncode, 0, out(p))
        self.assertNotIn("rows=?", p.stdout)

    def test_missing_container_is_a_parameter_error(self):
        p = run("--container", "no-such-container", "SELECT 1")
        self.assertEqual(p.returncode, dbq.EX_TOOL, out(p))

    def test_list_dbs_marks_ownership(self):
        p = run("--list-dbs")
        self.assertEqual(p.returncode, 0, out(p))
        self.assertIn(LIGHT, p.stdout)
        self.assertIn("deepweave", p.stdout)

    def test_drop_refuses_a_database_dbq_did_not_create(self):
        p = run("--drop", LIGHT)
        self.assertEqual(p.returncode, dbq.EX_REFUSED, out(p))
        self.assertIn(LIGHT, admin("postgres", f"SELECT datname FROM pg_database WHERE datname = '{LIGHT}'"))

    def test_drop_of_a_missing_database_fails(self):
        p = run("--drop", "deepweave_no_such_db")
        self.assertEqual(p.returncode, dbq.EX_CONTENT, out(p))

    def test_clone_refuses_an_existing_name_and_leaves_it(self):
        p = run("--clone", LIGHT, "--tree", TREE)
        self.assertEqual(p.returncode, dbq.EX_CONTENT, out(p))
        self.assertEqual(admin(LIGHT, "SELECT count(*) FROM widgets"), "60")

    def test_clone_without_a_usable_tree_is_a_tool_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = run("--clone", LIGHT + "_x", "--tree", d)
        self.assertEqual(p.returncode, dbq.EX_TOOL, out(p))
        self.assertEqual(admin("postgres", f"SELECT count(*) FROM pg_database WHERE datname = '{LIGHT}_x'"), "0")


@unittest.skipUnless(ENGINE, "DBQ_SKIP_ENGINE=1")
class FullCloneTest(unittest.TestCase):
    """One from-zero clone: fixtures apply, converge, reach balances through the trigger, and fail loudly on drift."""

    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(drop_db, FULL)
        cls.clone = run("--clone", FULL, "--tree", TREE)
        if cls.clone.returncode != 0:
            raise AssertionError(f"clone failed: {out(cls.clone)}")
        cls.first = run("--fixture", "all", "--db", FULL)

    def ledger(self):
        return admin(FULL, """
            SELECT count(*), coalesce(sum(amount), 0) FROM point_transactions t JOIN users u ON u.id = t.user_id
            WHERE u.email LIKE 'fx-%'""")

    def balance(self, email):
        rows = admin(FULL, f"""
            SELECT b.currency_type, b.balance FROM user_currency_balances b JOIN users u ON u.id = b.user_id
            WHERE u.email = '{email}' AND b.balance <> 0 ORDER BY 1""")
        return dict(r.split("|") for r in rows.splitlines())

    def test_a_clone_is_migrated_seeded_and_marked(self):
        self.assertEqual(admin(FULL, "SELECT count(*) FROM users WHERE email = 'creator.demo@deepweave.local'"), "1")
        self.assertEqual(admin(FULL, "SELECT to_regclass('public.mem0') IS NOT NULL"), "t")
        self.assertEqual(admin(FULL, "SELECT to_regclass('public.river_job') IS NOT NULL"), "t")
        self.assertIn("goose", self.clone.stdout)
        self.assertEqual(admin("postgres", f"SELECT shobj_description(oid, 'pg_database') FROM pg_database "
                                           f"WHERE datname = '{FULL}'").split()[0], "dbq-clone:ready")

    def test_first_apply_succeeds_and_reports_each_fixture(self):
        self.assertEqual(self.first.returncode, 0, out(self.first))
        for name in ("creator", "minor", "adult", "unknown", "zero", "char_general", "char_nsfw"):
            self.assertIn(name, self.first.stdout)

    def test_balances_equal_the_header_and_the_ledger_sum(self):
        fx = dbq.load_fixtures()
        for name in ACCOUNTS:
            email = fx[name].meta["email"]
            expected = {c: str(a) for c, a in fx[name].credits.items()}
            self.assertEqual(self.balance(email), expected, name)
        drift = admin(FULL, """
            SELECT count(*) FROM user_currency_balances b JOIN users u ON u.id = b.user_id
            WHERE u.email LIKE 'fx-%' AND b.balance <> (
                SELECT coalesce(sum(t.amount), 0) FROM point_transactions t
                WHERE t.user_id = b.user_id AND t.currency_type = b.currency_type)""")
        self.assertEqual(drift, "0", "balance must be what the trigger summed from the ledger")

    def test_the_balance_guard_is_live_so_only_the_ledger_could_have_written_them(self):
        p = subprocess.run(["docker", "exec", "-i", PG, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "deepweave", "-d", FULL],
                           input="UPDATE user_currency_balances SET balance = balance + 1;\n",
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)

    def test_second_apply_is_a_no_op_for_the_ledger(self):
        before = (self.ledger(), self.balance("fx-adult@deepweave.local"))
        p = run("--fixture", "all", "--db", FULL)
        self.assertEqual(p.returncode, 0, out(p))
        self.assertEqual((self.ledger(), self.balance("fx-adult@deepweave.local")), before)

    def test_spending_survives_a_rerun_and_the_rerun_says_so(self):
        admin(FULL, """
            INSERT INTO point_transactions (user_id, currency_type, tx_type, amount, description, idempotency_key)
            SELECT id, 'diamond', 'consumption', -7, 'test spend', 'test:spend:' || id FROM users
            WHERE email = 'fx-minor@deepweave.local'
              AND NOT EXISTS (SELECT 1 FROM point_transactions WHERE idempotency_key = 'test:spend:' || users.id);""")
        fx = dbq.load_fixtures()["minor"]
        p = run("--fixture", "minor", "--db", FULL)
        self.assertEqual(p.returncode, 0, out(p))
        self.assertEqual(self.balance("fx-minor@deepweave.local")["diamond"], str(fx.credits["diamond"] - 7))
        self.assertIn(f"diamond={fx.credits['diamond'] - 7}", p.stdout)

    def test_states_landed(self):
        rows = dict(r.split("|", 1) for r in admin(FULL, """
            SELECT u.email, coalesce(s.adult_status, 'no-row') || ',' || u.email_verified || ',' || u.phone_verified
            FROM users u LEFT JOIN community_user_settings s ON s.user_id = u.id
            WHERE u.email LIKE 'fx-%'""").splitlines())
        self.assertEqual(rows["fx-creator@deepweave.local"], "adult,true,true")
        self.assertEqual(rows["fx-minor@deepweave.local"], "minor,true,true")
        self.assertEqual(rows["fx-adult@deepweave.local"], "adult,true,true")
        self.assertEqual(rows["fx-unknown@deepweave.local"], "unknown,true,true")
        self.assertEqual(rows["fx-zero@deepweave.local"], "adult,true,true")

    def test_a_walk_that_flipped_a_state_gets_it_back(self):
        admin(FULL, """UPDATE community_user_settings SET adult_status = 'minor', adult_answered_at = now()
                       WHERE user_id = (SELECT id FROM users WHERE email = 'fx-zero@deepweave.local');""")
        p = run("--fixture", "zero", "--db", FULL)
        self.assertEqual(p.returncode, 0, out(p))
        self.assertEqual(admin(FULL, """SELECT adult_status FROM community_user_settings WHERE user_id =
                                        (SELECT id FROM users WHERE email = 'fx-zero@deepweave.local')"""), "adult")

    def test_character_nsfw_flags_and_rerun_does_not_touch_the_immutable_column(self):
        fx = dbq.load_fixtures()
        self.assertEqual(admin(FULL, f"SELECT is_nsfw FROM characters WHERE id = '{fx['char_nsfw'].meta['id']}'"), "t")
        self.assertEqual(admin(FULL, f"SELECT is_nsfw FROM characters WHERE id = '{fx['char_general'].meta['id']}'"), "f")
        p = run("--fixture", "char_nsfw,char_general", "--db", FULL)
        self.assertEqual(p.returncode, 0, out(p))

    def scratch_copy(self, suffix):
        name = FULL + suffix
        admin("postgres", f"CREATE DATABASE {name} TEMPLATE {FULL};")
        self.addCleanup(drop_db, name)
        return name

    def test_schema_drift_fails_loudly_and_seeds_nothing(self):
        db = self.scratch_copy("_drift")
        admin(db, """ALTER TABLE community_user_settings RENAME COLUMN adult_status TO adult_state;
                     UPDATE users SET display_name = 'tampered' WHERE email = 'fx-unknown@deepweave.local';""")
        p = run("--fixture", "unknown", "--db", db)
        self.assertEqual(p.returncode, dbq.EX_CONTENT, out(p))
        self.assertIn("adult_status", out(p))
        self.assertIn("nothing seeded", out(p))
        self.assertEqual(admin(db, "SELECT display_name FROM users WHERE email = 'fx-unknown@deepweave.local'"),
                         "tampered", "the rollback must cover the user upsert that ran before the failing statement")

    def test_a_grant_the_header_does_not_list_fails_the_post_check(self):
        db = self.scratch_copy("_stray")
        admin(db, """
            INSERT INTO point_transactions (user_id, currency_type, tx_type, amount, description, reference_type, idempotency_key)
            SELECT id, 'permanent_point', 'admin_adjustment', 1, 'stray', 'admin_adjustment', 'fixture:creator:stray'
            FROM users WHERE email = 'fx-creator@deepweave.local';""")
        p = run("--fixture", "creator", "--db", db)
        self.assertEqual(p.returncode, dbq.EX_CONTENT, out(p))
        self.assertIn("permanent_point", out(p))

    def test_a_failure_in_a_later_fixture_rolls_back_the_earlier_ones(self):
        db = self.scratch_copy("_later")
        admin(db, """ALTER TABLE characters RENAME COLUMN tagline TO tagline_renamed;
                     UPDATE users SET display_name = 'tampered' WHERE email = 'fx-creator@deepweave.local';""")
        p = run("--fixture", "char_general", "--db", db)
        self.assertEqual(p.returncode, dbq.EX_CONTENT, out(p))
        self.assertIn("tagline", out(p))
        self.assertEqual(admin(db, "SELECT display_name FROM users WHERE email = 'fx-creator@deepweave.local'"),
                         "tampered", "creator ran first and must roll back with the character that failed")

    def test_clone_tree_defaults_to_the_git_toplevel_of_the_cwd_and_a_real_clone_can_be_dropped(self):
        name = FULL + "_cwd"
        self.addCleanup(drop_db, name)
        p = run("--clone", name, cwd=TREE / "db")
        self.assertEqual(p.returncode, 0, out(p))
        self.assertIn(str(TREE.resolve()), p.stdout)
        p = run("--drop", name)
        self.assertEqual(p.returncode, 0, out(p))
        self.assertEqual(admin("postgres", f"SELECT count(*) FROM pg_database WHERE datname = '{name}'"), "0")

    def test_drop_refuses_while_connected_then_succeeds(self):
        name = FULL + "_d"
        admin("postgres", f"CREATE DATABASE {name} TEMPLATE {FULL};")
        self.addCleanup(drop_db, name)
        admin("postgres", f"COMMENT ON DATABASE {name} IS 'dbq-clone:ready test';")
        holder = subprocess.Popen(["docker", "exec", "-i", PG, "psql", "-X", "-U", "deepweave", "-d", name,
                                   "-c", "SELECT pg_sleep(6)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(holder.wait)
        deadline = 0
        while admin("postgres", f"SELECT count(*) FROM pg_stat_activity WHERE datname = '{name}'") == "0" and deadline < 50:
            deadline += 1
        p = run("--drop", name)
        self.assertEqual(p.returncode, dbq.EX_CONTENT, out(p))
        self.assertIn("connection", out(p).lower())
        holder.wait()
        p = run("--drop", name)
        self.assertEqual(p.returncode, 0, out(p))
        self.assertEqual(admin("postgres", f"SELECT count(*) FROM pg_database WHERE datname = '{name}'"), "0")

    def test_shared_db_carries_no_fixture_account(self):
        self.assertEqual(admin("deepweave", "SELECT count(*) FROM users WHERE email LIKE 'fx-%'"), "0")


if __name__ == "__main__":
    unittest.main()
