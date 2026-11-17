# -*- coding: utf-8 -*-
"""List and remove what a task left running or lying around: one verdict that says whether anything is left.

Usage:
  python3 cleanup.py --slug S [--root R] [--only KIND,...]            # dry run: list
  python3 cleanup.py --slug S [--root R] [--only KIND,...] --apply    # stop, drop, remove
  python3 cleanup.py --all [--root R]                                 # dry run over every <R>/tmp/<slug>/

R is the checkout that owns <R>/tmp/<slug>/ (default: the git toplevel of the current directory). Kinds, in the
order they are handled:
  procs       processes the task started: a live pid in tmp/<slug>/**/pids/*.pid that is no younger than its pid
              file, its descendants, and whatever listens on a port a walk's env.log recorded while running from the
              tree that log names (walk_env.sh's own rule). SIGTERM, then SIGKILL after 3 s.
  dbs         walk databases named in tmp/<slug>/**/run/env.log or kit/walk.config.json, only `deepweave_walk_*`,
              only with no connection left. The shared `deepweave` database is never a candidate.
  trees       git worktrees under tmp/<slug>/. Before removal each tree's full state (tracked, modified, untracked,
              not ignored) is written as a git tree object and logged in tmp/<slug>/cleanup-trees.txt; a tree whose
              state cannot be saved is left alone.
  files       walk gateway binaries (tmp/<slug>/**/bin/gateway, ~100 MB each, rebuilt by walk_env.sh) and stale
              pid files. Reports, logs, screenshots and gate output are evidence and stay.
  containers  leftover testcontainers on this machine (not per task; see _dockerclean.py).
--all never applies: another session may be in the middle of one of those tasks.

First line: CLEANUP <slug> OK|LEFT found=N removed=N left=N, then one line per item with what happened to it.
Exit: 0 nothing is left; 1 something is (a dry run that found anything, or an item that could not be removed);
      2 tool error.
"""
import argparse
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _dockerclean  # noqa: E402
from _common import git, toplevel  # noqa: E402

KINDS = ("procs", "dbs", "trees", "files", "containers")
PG_CONTAINER = "deepweave-postgres"
# INVARIANT: the shared dev database and anything a walk did not create can never match.
WALK_DB_RE = re.compile(r"^deepweave_walk_[a-z0-9_]+$")
# INVARIANT[walk-env-log-format]: these read the `frontend|admin|gateway http://localhost:<port> tree=<tree>` and
# `database <name> in container <pg>;` lines walk_env.sh writes to <round>/run/env.log. Change both together.
ENV_SERVER_RE = re.compile(r"^(frontend|admin|gateway)\s+http://localhost:(\d+)(?:\s+tree=(\S+))?")
ENV_DB_RE = re.compile(r"^database (\S+) in container (\S+);")
# WHY: walk_env.sh writes the pid file right after it starts the process, so a process that started later than
# the file is a reused pid, not ours.
PIDFILE_SLACK_S = 60
TERM_GRACE_S = 3
TREE_LOG = "cleanup-trees.txt"
EX_LEFT = 1
EX_TOOL = 2


class ToolError(Exception):
    pass


class Item:
    def __init__(self, kind, text, **data):
        self.kind, self.text, self.data = kind, text, data
        self.state = "found"

    def line(self):
        return f"- {self.kind:<10} {self.text}  [{self.state}]"


def run(cmd, timeout=120):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ToolError(f"{cmd[0]} failed: {e}")


def inside(path, parent):
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    return path == parent or path.startswith(parent + os.sep)


def registered_trees(root, task):
    out = []
    for line in git(root, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree ") and inside(line[9:], task) and os.path.realpath(line[9:]) != os.path.realpath(task):
            out.append(os.path.realpath(line[9:]))
    return out


def scan(task):
    """Walk records under the task directory, without descending into trees or dependency folders."""
    found = {"pidfiles": [], "envlogs": [], "configs": [], "binaries": [], "stray_trees": []}
    for cur, dirs, files in os.walk(task):
        here = pathlib.Path(cur)
        if here != task and (here / ".git").exists():
            found["stray_trees"].append(str(here))
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d != "node_modules"]
        for f in files:
            p = here / f
            if here.name == "pids" and f.endswith(".pid"):
                found["pidfiles"].append(p)
            elif f == "env.log" and here.name == "run":
                found["envlogs"].append(p)
            elif f == "walk.config.json" and here.name == "kit":
                found["configs"].append(p)
            elif f == "gateway" and here.name == "bin" and p.is_file() and not p.is_symlink():
                found["binaries"].append(p)
    return found


def read_walks(found):
    """(ports with the tree that must be serving them, database names) the task's walks recorded."""
    ports, dbs = {}, set()
    for log in found["envlogs"]:
        tree, mine = None, []
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            m = ENV_SERVER_RE.match(line)
            if m:
                mine.append(int(m.group(2)))
                tree = m.group(3) or tree
            m = ENV_DB_RE.match(line)
            if m:
                dbs.add(m.group(1))
        if tree:
            for port in mine:
                ports[port] = tree
    for cfg in found["configs"]:
        try:
            name = json.loads(cfg.read_text(encoding="utf-8")).get("db")
        except (ValueError, AttributeError):
            name = None
        if isinstance(name, str):
            dbs.add(name)
    return ports, sorted(d for d in dbs if WALK_DB_RE.match(d))


def process_table():
    r = run(["ps", "-axo", "pid=,ppid=,etime=,command="])
    if r.returncode != 0:
        raise ToolError(f"ps failed: {r.stderr.strip()}")
    table, now = {}, time.time()
    for line in r.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue
        m = re.match(r"^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$", parts[2])
        if not m:
            raise ToolError(f"unparsable ps row: {line.strip()[:120]}")
        d, h, mi, s = (int(x or 0) for x in m.groups())
        table[int(parts[0])] = {"ppid": int(parts[1]), "start": now - (d * 86400 + h * 3600 + mi * 60 + s),
                                "cmd": parts[3] if len(parts) > 3 else ""}
    return table


def descendants(table, roots):
    out, frontier = set(roots), set(roots)
    while frontier:
        frontier = {pid for pid, p in table.items() if p["ppid"] in frontier and pid not in out}
        out |= frontier
    return out


def listener_pids(port):
    r = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
    return [int(x) for x in r.stdout.split() if x.isdigit()]


def cwd_of(pid):
    r = run(["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"])
    for line in r.stdout.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def find_procs(task, found, ports):
    table = process_table()
    why = {}
    for f in found["pidfiles"]:
        try:
            pid = int(f.read_text().strip())
        except (ValueError, OSError):
            continue
        p = table.get(pid)
        if p and p["start"] <= f.stat().st_mtime + PIDFILE_SLACK_S:
            why[pid] = str(f.relative_to(task))
    for pid in descendants(table, set(why)) - set(why):
        why[pid] = "child of a pid-file process"
    for port, tree in sorted(ports.items()):
        for pid in listener_pids(port):
            cwd = cwd_of(pid)
            # INVARIANT: a port alone proves nothing; another tree may have taken it since the walk ran.
            if pid not in why and cwd and inside(cwd, tree):
                why[pid] = f"listens on :{port} from {os.path.relpath(tree, task) if inside(tree, task) else tree}"
    def cmd(pid):
        return table[pid]["cmd"].replace(str(task) + os.sep, "")[:70] if pid in table else "?"

    return [Item("procs", f"pid {pid} {cmd(pid)} ({reason})", pid=pid) for pid, reason in sorted(why.items())]


def alive(pid):
    # WHY ps, not kill(pid, 0): a killed process stays a zombie until its parent reaps it, and signal 0 still
    # reaches a zombie.
    stat = run(["ps", "-o", "stat=", "-p", str(pid)]).stdout.strip()
    return bool(stat) and not stat.startswith("Z")


def kill_procs(items):
    for it in items:
        try:
            os.kill(it.data["pid"], signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + TERM_GRACE_S
    while time.monotonic() < deadline and any(alive(it.data["pid"]) for it in items):
        time.sleep(0.1)
    for it in items:
        if alive(it.data["pid"]):
            try:
                os.kill(it.data["pid"], signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    time.sleep(0.2)
    for it in items:
        it.state = "left: still alive after SIGKILL" if alive(it.data["pid"]) else "stopped"


def pg_user():
    r = run(["docker", "exec", PG_CONTAINER, "printenv", "POSTGRES_USER"])
    if r.returncode != 0:
        raise ToolError(f"cannot reach {PG_CONTAINER}: {(r.stderr or r.stdout).strip()[:160]}")
    return r.stdout.strip() or "deepweave"


def db_conns(user, names):
    """{name: connection count} for the names that exist."""
    quoted = ", ".join(f"'{n}'" for n in names)
    r = run(["docker", "exec", PG_CONTAINER, "psql", "-X", "-U", user, "-d", "postgres", "-Atc",
             "select d.datname, (select count(*) from pg_stat_activity a where a.datname = d.datname) "
             f"from pg_database d where d.datname in ({quoted})"])
    if r.returncode != 0:
        raise ToolError(f"psql in {PG_CONTAINER} failed: {(r.stderr or r.stdout).strip()[:160]}")
    return {row.split("|")[0]: int(row.split("|")[1]) for row in r.stdout.splitlines() if "|" in row}


def find_dbs(names):
    if not names:
        return []
    try:
        conns = db_conns(pg_user(), names)
    except ToolError as e:
        it = Item("dbs", f"{', '.join(names)} (recorded by a walk)")
        it.state = f"left: not checked, {e}"
        return [it]
    return [Item("dbs", f"{n} conns={conns[n]}", name=n) for n in names if n in conns]


def drop_dbs(items):
    for it in items:
        if "name" not in it.data:
            continue
        name = it.data["name"]
        try:
            user = pg_user()
            # WHY re-read: the gateway that held the connections was stopped a moment ago, in this same run.
            deadline = time.monotonic() + 5
            while db_conns(user, [name]).get(name, 0) and time.monotonic() < deadline:
                time.sleep(0.5)
            left = db_conns(user, [name]).get(name)
            if left:
                it.state = f"left: {left} connection(s) still open, not started by this task"
                continue
            if left is None:
                it.state = "dropped"
                continue
            r = run(["docker", "exec", PG_CONTAINER, "dropdb", "-U", user, "--if-exists", name])
            it.state = "dropped" if r.returncode == 0 else f"left: dropdb failed, {(r.stderr or r.stdout).strip()[:120]}"
        except ToolError as e:
            it.state = f"left: {e}"


def find_trees(root, task, found):
    listed = registered_trees(root, task)
    items = [Item("trees", os.path.relpath(t, task), path=t) for t in listed]
    for t in found["stray_trees"]:
        if os.path.realpath(t) not in listed:
            it = Item("trees", f"{os.path.relpath(t, task)} (has .git but git does not list it)")
            it.state = "left: inspect it, then delete the directory by hand"
            items.append(it)
    return items


def remove_trees(root, task, items):
    log = pathlib.Path(task) / TREE_LOG
    for it in items:
        tree = it.data.get("path")
        if not tree:
            continue
        # INVARIANT: save before remove, and no save means no remove. A slice tree can hold work that was never
        # merged back, and `worktree remove --force` leaves nothing to recover it from.
        add = run(["git", "-C", tree, "add", "-A"], timeout=600)
        snap = run(["git", "-C", tree, "write-tree"]) if add.returncode == 0 else add
        head = run(["git", "-C", tree, "rev-parse", "HEAD"])
        if snap.returncode != 0 or not snap.stdout.strip():
            it.state = f"left: could not save its state, {(snap.stderr or snap.stdout).strip()[:120]}"
            continue
        sha = snap.stdout.strip()
        with open(log, "a", encoding="utf-8") as f:
            if f.tell() == 0:
                f.write("# Trees removed by cleanup.py. `tree` is the whole working tree as it was (not ignored files).\n"
                        "# See the changes: git diff <head> <tree>   Restore: git archive <tree> | tar -x -C <dir>\n"
                        "# Unreferenced objects are pruned by git gc after about two weeks.\n")
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {os.path.relpath(tree, task)} head={head.stdout.strip()} tree={sha}\n")
        r = run(["git", "-C", str(root), "worktree", "remove", "--force", tree], timeout=600)
        it.state = f"removed, state saved as tree {sha[:12]}" if r.returncode == 0 else \
            f"left: worktree remove failed, {(r.stderr or r.stdout).strip()[:120]}"
    run(["git", "-C", str(root), "worktree", "prune"])


def find_files(task, found):
    items = [Item("files", f"{p.relative_to(task)} {p.stat().st_size // 1048576}M", path=p) for p in found["binaries"]]
    table = process_table()
    for f in found["pidfiles"]:
        try:
            dead = int(f.read_text().strip()) not in table
        except (ValueError, OSError):
            dead = True
        if dead:
            items.append(Item("files", f"{f.relative_to(task)} (stale pid file)", path=f))
    return items


def remove_files(items):
    for it in items:
        try:
            it.data["path"].unlink()
            it.state = "deleted"
        except FileNotFoundError:
            it.state = "deleted"
        except OSError as e:
            it.state = f"left: {e}"


def find_containers():
    try:
        found, notes = _dockerclean.split(_dockerclean.rows())
    except _dockerclean.CleanError as e:
        it = Item("containers", "leftover testcontainers")
        it.state = f"left: not checked, {e}"
        return [it], []
    return [Item("containers", f"{r['id']} {r['why']} {r['state']} {r['name']}", id=r["id"]) for r in found], notes


def remove_containers(items):
    ids = [it.data["id"] for it in items if "id" in it.data]
    try:
        _dockerclean.remove(ids)
        outcome = "removed"
    except _dockerclean.CleanError as e:
        outcome = f"left: {e}"
    for it in items:
        if "id" in it.data:
            it.state = outcome


def collect(root, slug, kinds):
    """Everything found for one task, in handling order; nothing is changed."""
    task = pathlib.Path(root) / "tmp" / slug
    items, notes = [], []
    found = scan(task) if task.is_dir() else {"pidfiles": [], "envlogs": [], "configs": [], "binaries": [], "stray_trees": []}
    ports, dbs = read_walks(found)
    if "procs" in kinds:
        items += find_procs(task, found, ports)
    if "dbs" in kinds:
        items += find_dbs(dbs)
    if "trees" in kinds and task.is_dir():
        items += find_trees(root, task, found)
    if "files" in kinds:
        items += find_files(task, found)
    if "containers" in kinds:
        got, notes = find_containers()
        items += got
    return task, items, notes


def apply(root, task, items):
    by = {k: [it for it in items if it.kind == k and it.state == "found"] for k in KINDS}
    # INVARIANT: processes first. They hold the database connections and run from the trees.
    if by["procs"]:
        kill_procs(by["procs"])
    drop_dbs(by["dbs"])
    if by["trees"]:
        remove_trees(root, task, by["trees"])
    remove_files(by["files"])
    # WHY: a pid file whose process was just stopped is stale now, and was not when the listing was made.
    stopped = {it.data["pid"] for it in by["procs"] if it.state == "stopped"}
    if stopped and task.is_dir():
        for f in scan(task)["pidfiles"]:
            try:
                if int(f.read_text().strip()) in stopped:
                    f.unlink()
            except (ValueError, OSError):
                pass
    if by["containers"]:
        remove_containers(by["containers"])


def report(slug, items, notes, applied):
    left = [it for it in items if it.state == "found" or it.state.startswith("left")]
    removed = len(items) - len(left)
    lines = [f"CLEANUP {slug} {'LEFT' if left else 'OK'} found={len(items)} removed={removed} left={len(left)}"]
    lines += [it.line() for it in items]
    lines += [f"note: {n}" for n in notes]
    if left and not applied:
        lines.append("dry run: pass --apply to clean up")
    return lines, bool(left)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--only", help="comma-separated kinds: " + ", ".join(KINDS))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    try:
        if bool(a.slug) == a.all:
            raise ToolError("give exactly one of --slug S and --all")
        if a.all and a.apply:
            raise ToolError("--all only lists; apply one task at a time with --slug")
        kinds = tuple(k.strip() for k in a.only.split(",")) if a.only else KINDS
        bad = [k for k in kinds if k not in KINDS]
        if bad:
            raise ToolError(f"--only: unknown kind {', '.join(bad)}; choose from {', '.join(KINDS)}")
        if a.slug and (os.sep in a.slug or a.slug in (".", "..")):
            raise ToolError(f"--slug is a directory name under tmp/, got {a.slug}")
        try:
            root = pathlib.Path(os.path.realpath(a.root)) if a.root else toplevel()
            git(root, "rev-parse", "--git-dir")
        except (subprocess.CalledProcessError, OSError):
            raise ToolError(f"not a git checkout: {a.root or os.getcwd()}")
        if a.all:
            slugs = sorted(p.name for p in (root / "tmp").iterdir() if p.is_dir()) if (root / "tmp").is_dir() else []
            task_kinds = tuple(k for k in kinds if k != "containers")
            any_left, total = False, 0
            for slug in slugs:
                _, items, _ = collect(root, slug, task_kinds)
                if items:
                    counts = " ".join(f"{k}={sum(1 for it in items if it.kind == k)}" for k in task_kinds
                                      if any(it.kind == k for it in items))
                    print(f"{slug}: {counts}")
                    any_left, total = True, total + len(items)
            if "containers" in kinds:
                got, notes = find_containers()
                if got:
                    print(f"(machine) containers={len(got)}")
                    any_left, total = True, total + len(got)
                for n in notes:
                    print(f"note: {n}")
            print(f"CLEANUP --all {'LEFT' if any_left else 'OK'} found={total} tasks={len(slugs)} root={root}")
            return EX_LEFT if any_left else 0
        task, items, notes = collect(root, a.slug, kinds)
        if a.apply:
            apply(root, task, items)
        lines, left = report(a.slug, items, notes, a.apply)
        print("\n".join(lines))
        return EX_LEFT if left else 0
    except ToolError as e:
        print(f"CLEANUP TOOL-ERROR: {e}")
        return EX_TOOL


if __name__ == "__main__":
    sys.exit(main())
