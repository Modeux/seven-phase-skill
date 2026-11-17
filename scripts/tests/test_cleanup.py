# -*- coding: utf-8 -*-
"""cleanup: what a task left behind is found, removed in a safe order, and never more than the task's own.

Real git trees, real processes and real sockets; only docker is a fake, driven by a JSON state file.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_cleanup.py' -v
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "cleanup.py"
if str(SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT.parent))
SLUG = "demo-task"
FAKE_DOCKER = r'''#!%s
import json, os, pathlib, sys
d = pathlib.Path(os.environ["FAKE_DOCKER_DIR"])
state = json.loads((d / "state.json").read_text(encoding="utf-8"))
argv = sys.argv[1:]
entry = {"argv": argv}
def done(rc=0, out=""):
    with open(d / "docker.log", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    sys.stdout.write(out)
    sys.exit(rc)
if argv[0] == "ps":
    done(out=state.get("ps", ""))
if argv[0] == "rm":
    done()
if argv[0] == "exec":
    if state.get("pg_down"):
        done(1, "Error response from daemon: No such container: deepweave-postgres\n")
    if argv[2] == "printenv":
        done(out="deepweave\n")
    if argv[2] == "psql":
        sql = argv[-1]
        done(out="".join(f"{n}|{c}\n" for n, c in state["dbs"].items() if f"'{n}'" in sql))
    if argv[2] == "dropdb":
        name = argv[-1]
        watch = state.get("watch_pid")
        if watch:
            import subprocess
            stat = subprocess.run(["ps", "-o", "stat=", "-p", str(watch)], capture_output=True, text=True).stdout.strip()
            entry["watched_alive"] = bool(stat) and not stat.startswith("Z")
        state["dbs"].pop(name, None)
        (d / "state.json").write_text(json.dumps(state), encoding="utf-8")
        done()
done(2, "fake docker: unexpected " + " ".join(argv) + "\n")
''' % sys.executable
SLEEP = "import time; time.sleep(120)"


def tc_row(cid, state, minutes_ago):
    created = time.strftime("%Y-%m-%d %H:%M:%S +0800 CST", time.gmtime(time.time() + 8 * 3600 - minutes_ago * 60))
    return "\t".join([cid, state, created, "true", "c", "", "", ""]) + "\n"


class CleanupTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_obj.cleanup)
        self.tmp = pathlib.Path(os.path.realpath(self.tmp_obj.name))
        self.root = self.tmp / "repo"
        self.root.mkdir()
        self.git("init", "-q", "-b", "dev")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        (self.root / "a.txt").write_text("one\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("tmp/\n", encoding="utf-8")
        self.git("add", "a.txt", ".gitignore")
        self.git("commit", "-q", "-m", "init")
        self.task = self.root / "tmp" / SLUG
        self.task.mkdir(parents=True)
        (self.tmp / "bin").mkdir()
        fake = self.tmp / "bin" / "docker"
        fake.write_text(FAKE_DOCKER, encoding="utf-8")
        fake.chmod(0o755)
        self.state = {"ps": "", "dbs": {}}
        self.save_state()
        self.env = {**os.environ, "FAKE_DOCKER_DIR": str(self.tmp),
                    "PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}"}
        self.procs = []
        self.addCleanup(self.reap)

    def reap(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            p.stdout.close()

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.root), *args], capture_output=True, text=True, check=True).stdout

    def save_state(self):
        (self.tmp / "state.json").write_text(json.dumps(self.state), encoding="utf-8")

    def docker_calls(self):
        log = self.tmp / "docker.log"
        return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    def cleanup(self, *args, slug=SLUG):
        target = ["--slug", slug] if slug else []
        r = subprocess.run([sys.executable, str(SCRIPT), *target, "--root", str(self.root), *args], env=self.env,
                           capture_output=True, text=True, timeout=120)
        r.first = r.stdout.splitlines()[0] if r.stdout.strip() else ""
        return r

    def spawn(self, code=SLEEP, cwd=None):
        p = subprocess.Popen([sys.executable, "-c", code], cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, text=True)
        self.procs.append(p)
        return p

    def pidfile(self, name, pid, age_s=0):
        f = self.task / "walk" / "pids" / f"{name}.pid"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"{pid}\n", encoding="utf-8")
        if age_s:
            os.utime(f, (time.time() - age_s, time.time() - age_s))
        return f

    def env_log(self, text, rnd="r1"):
        f = self.task / "walk" / rnd / "run" / "env.log"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        return f

    def worktree(self, rel, parent=None):
        path = (parent or self.task) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        self.git("worktree", "add", "-q", "--detach", str(path), "HEAD")
        return path

    def gone(self, p, timeout=5):
        end = time.time() + timeout
        while p.poll() is None and time.time() < end:
            time.sleep(0.05)
        return p.poll() is not None

    def test_nothing_left_is_ok(self):
        r = self.cleanup()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.first, f"CLEANUP {SLUG} OK found=0 removed=0 left=0")

    def test_task_without_a_tmp_directory_is_ok(self):
        r = self.cleanup(slug="never-existed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_dry_run_lists_a_pid_file_process_and_leaves_it_running(self):
        p = self.spawn()
        self.pidfile("gateway", p.pid)
        r = self.cleanup()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("LEFT found=1 removed=0 left=1", r.first)
        self.assertIn(f"pid {p.pid}", r.stdout)
        self.assertIn("--apply", r.stdout)
        self.assertIsNone(p.poll())

    def test_apply_stops_the_process_and_its_children_and_drops_the_pid_file(self):
        parent = self.spawn("import subprocess, sys, time\n"
                            f"c = subprocess.Popen([sys.executable, '-c', {SLEEP!r}])\n"
                            "print(c.pid, flush=True)\ntime.sleep(120)")
        child_pid = int(parent.stdout.readline())
        f = self.pidfile("frontend", parent.pid)
        r = self.cleanup("--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("found=2 removed=2 left=0", r.first)
        self.assertTrue(self.gone(parent))
        self.assertEqual(subprocess.run(["ps", "-o", "stat=", "-p", str(child_pid)], capture_output=True, text=True).stdout.strip(), "")
        self.assertFalse(f.exists())

    def test_process_younger_than_its_pid_file_is_a_reused_pid_and_is_left_alone(self):
        p = self.spawn()
        f = self.pidfile("gateway", p.pid, age_s=3600)
        r = self.cleanup("--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("found=0", r.first)
        self.assertIsNone(p.poll())
        self.assertTrue(f.exists())

    def listener(self, cwd):
        p = self.spawn("import socket, time\ns = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen()\n"
                       "print(s.getsockname()[1], flush=True)\ntime.sleep(120)", cwd=cwd)
        return p, int(p.stdout.readline())

    def test_listener_on_a_recorded_port_is_stopped_only_when_it_runs_from_the_recorded_tree(self):
        tree = self.task / "walk" / "wt"
        tree.mkdir(parents=True)
        elsewhere = self.tmp / "other-tree"
        elsewhere.mkdir()
        ours, our_port = self.listener(tree)
        theirs, their_port = self.listener(elsewhere)
        self.env_log(f"frontend http://localhost:{our_port} tree={tree} pid=1 (log: x)\n"
                     f"gateway  http://localhost:{their_port} tree={tree} health=200 (log: y)\n")
        r = self.cleanup("--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"listens on :{our_port}", r.stdout)
        self.assertTrue(self.gone(ours))
        self.assertIsNone(theirs.poll())

    def record_db(self, name, conns=0):
        self.env_log(f"database {name} in container deepweave-postgres; redis index 5\n")
        self.state["dbs"][name] = conns
        self.save_state()

    def dropped(self):
        return [c["argv"][-1] for c in self.docker_calls() if c["argv"][:1] == ["exec"] and c["argv"][2] == "dropdb"]

    def test_apply_drops_the_walk_database_the_task_recorded(self):
        self.record_db("deepweave_walk_demo")
        self.state["dbs"]["deepweave_walk_someone_else"] = 0
        self.save_state()
        r = self.cleanup("--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.dropped(), ["deepweave_walk_demo"])

    def test_database_named_in_the_kit_config_counts_too(self):
        kit = self.task / "walk" / "kit"
        kit.mkdir(parents=True)
        (kit / "walk.config.json").write_text(json.dumps({"db": "deepweave_walk_cfg", "app": "http://localhost:1"}), encoding="utf-8")
        self.state["dbs"]["deepweave_walk_cfg"] = 0
        self.save_state()
        self.cleanup("--apply")
        self.assertEqual(self.dropped(), ["deepweave_walk_cfg"])

    def test_shared_or_foreign_database_names_are_never_candidates(self):
        for name in ("deepweave", "postgres", "deepweave_eval", "deepweave_walk_x; drop database deepweave"):
            with self.subTest(name):
                self.record_db(name)
                r = self.cleanup("--apply")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(self.dropped(), [])
                self.assertNotIn("dbs", r.stdout)

    def test_database_with_open_connections_is_left_and_reported(self):
        self.record_db("deepweave_walk_demo", conns=2)
        r = self.cleanup("--apply")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.dropped(), [])
        self.assertIn("left: 2 connection(s) still open", r.stdout)

    def test_unreachable_postgres_is_left_not_ok(self):
        self.record_db("deepweave_walk_demo")
        self.state["pg_down"] = True
        self.save_state()
        r = self.cleanup("--apply")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("left: not checked", r.stdout)

    def test_database_already_gone_is_not_an_item(self):
        self.env_log("database deepweave_walk_demo in container deepweave-postgres; redis index 5\n")
        r = self.cleanup()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_processes_are_stopped_before_the_database_is_dropped(self):
        p = self.spawn()
        self.pidfile("gateway", p.pid)
        self.record_db("deepweave_walk_demo")
        self.state["watch_pid"] = p.pid
        self.save_state()
        r = self.cleanup("--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        drop = [c for c in self.docker_calls() if c["argv"][:1] == ["exec"] and c["argv"][2] == "dropdb"][0]
        self.assertFalse(drop["watched_alive"])

    def test_apply_removes_trees_under_the_task_and_keeps_their_work_recoverable(self):
        tree = self.worktree("impl/wt-S1")
        (tree / "a.txt").write_text("one\nedited in the slice\n", encoding="utf-8")
        (tree / "new.go").write_text("package unmerged\n", encoding="utf-8")
        outside = self.worktree("keep-me", parent=self.tmp)
        r = self.cleanup("--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(tree.exists())
        self.assertTrue(outside.exists())
        listed = self.git("worktree", "list", "--porcelain")
        self.assertNotIn("wt-S1", listed)
        self.assertIn(str(outside), listed)
        row = (self.task / "cleanup-trees.txt").read_text(encoding="utf-8").splitlines()[-1]
        self.assertIn("impl/wt-S1", row)
        sha = row.rsplit("tree=", 1)[1]
        self.assertEqual(self.git("show", f"{sha}:new.go"), "package unmerged\n")
        self.assertEqual(self.git("show", f"{sha}:a.txt"), "one\nedited in the slice\n")

    def test_tree_whose_state_cannot_be_saved_is_not_removed(self):
        tree = self.worktree("impl/wt-S1")
        (tree / "new.go").write_text("package unmerged\n", encoding="utf-8")
        gitdir = pathlib.Path(self.git("rev-parse", "--absolute-git-dir", cwd=tree).strip())
        (gitdir / "index.lock").write_text("", encoding="utf-8")
        r = self.cleanup("--apply")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("left: could not save its state", r.stdout)
        self.assertTrue((tree / "new.go").exists())

    def test_dry_run_lists_trees_and_removes_none(self):
        tree = self.worktree("review/wt-snap")
        r = self.cleanup()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("review/wt-snap", r.stdout)
        self.assertTrue(tree.exists())
        self.assertFalse((self.task / "cleanup-trees.txt").exists())

    def test_directory_with_git_metadata_that_git_does_not_list_is_never_deleted(self):
        stray = self.task / "impl" / "wt-old"
        stray.mkdir(parents=True)
        (stray / ".git").write_text("gitdir: /nowhere\n", encoding="utf-8")
        (stray / "work.go").write_text("package w\n", encoding="utf-8")
        r = self.cleanup("--apply")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertTrue((stray / "work.go").exists())
        self.assertIn("left: inspect it", r.stdout)

    def test_apply_deletes_walk_binaries_and_stale_pid_files_and_keeps_evidence(self):
        binary = self.task / "walk" / "bin" / "gateway"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"\0" * 1024)
        stale = self.pidfile("gateway", 999999)
        log = self.env_log("started  2026-11-17 16:28:31\n")
        report = self.task / "review" / "report.md"
        report.parent.mkdir(parents=True)
        report.write_text("# findings\n", encoding="utf-8")
        inside_tree = self.worktree("walk/wt") / "bin" / "gateway"
        r = self.cleanup("--apply", "--only", "files")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(binary.exists())
        self.assertFalse(stale.exists())
        self.assertTrue(log.exists())
        self.assertTrue(report.exists())
        self.assertNotIn(str(inside_tree), r.stdout)

    def test_only_limits_what_is_touched(self):
        tree = self.worktree("impl/wt-S1")
        p = self.spawn()
        self.pidfile("gateway", p.pid)
        r = self.cleanup("--apply", "--only", "procs")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.gone(p))
        self.assertTrue(tree.exists())

    def test_apply_removes_leftover_testcontainers(self):
        self.state["ps"] = tc_row("exited1", "exited", 5) + tc_row("young1", "running", 2)
        self.save_state()
        r = self.cleanup("--apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rm = [c["argv"] for c in self.docker_calls() if c["argv"][0] == "rm"]
        self.assertEqual([a[-1] for a in rm], ["exited1"])

    def test_all_lists_every_task_and_refuses_to_apply(self):
        self.worktree("impl/wt-S1")
        other = self.root / "tmp" / "other-task" / "walk" / "bin"
        other.mkdir(parents=True)
        (other / "gateway").write_bytes(b"\0")
        (self.root / "tmp" / "clean-task").mkdir()
        r = self.cleanup("--all", slug=None)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"{SLUG}: trees=1", r.stdout)
        self.assertIn("other-task: files=1", r.stdout)
        self.assertNotIn("clean-task", r.stdout)
        self.assertIn("CLEANUP --all LEFT found=2 tasks=3", r.stdout)
        self.assertEqual(self.cleanup("--all", "--apply", slug=None).returncode, 2)
        self.assertTrue((other / "gateway").exists())

    def test_reads_the_lines_walk_env_really_writes(self):
        import cleanup
        src = (SCRIPT.parent / "walk_env.sh").read_text(encoding="utf-8")
        said = {}
        for line in src.splitlines():
            for role in ("frontend", "admin", "gateway", "database"):
                if f'say "{role} ' in line:
                    text = line.split('say "', 1)[1]
                    for var, value in (("$FE_PORT", "4310"), ("$ADMIN_PORT", "4311"), ("$GW_PORT", "7512"),
                                       ("$TREE", "/trees/wt"), ("$DB", "deepweave_walk_x"), ("$PG", "deepweave-postgres")):
                        text = text.replace(var, value)
                    said[role] = text
        self.assertEqual(sorted(said), ["admin", "database", "frontend", "gateway"])
        ports = {role: cleanup.ENV_SERVER_RE.match(said[role]) for role in ("frontend", "admin", "gateway")}
        self.assertEqual({r: m.group(2) for r, m in ports.items()}, {"frontend": "4310", "admin": "4311", "gateway": "7512"})
        self.assertEqual(ports["frontend"].group(3), "/trees/wt")
        self.assertEqual(ports["gateway"].group(3), "/trees/wt")
        self.assertEqual(cleanup.ENV_DB_RE.match(said["database"]).groups(), ("deepweave_walk_x", "deepweave-postgres"))

    def test_bad_arguments_are_tool_errors(self):
        for args, slug in ((["--only", "everything"], SLUG), ([], None), ([], "../escape")):
            with self.subTest(args=args, slug=slug):
                self.assertEqual(self.cleanup(*args, slug=slug).returncode, 2)


if __name__ == "__main__":
    unittest.main()
