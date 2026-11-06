# -*- coding: utf-8 -*-
"""Shared harness for test_gate.py and test_mutate.py: fake go/npx/docker that replay real captured output.

The fixtures in fixtures/gate/ were captured from the real engines (go1.27, ng test/vitest 4.1.5,
pytest/ruff/mypy in the project's container); only the scratchpad path prefix was rewritten to /SP.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "gate"
GATE = SCRIPTS / "gate.py"
MUTATE = SCRIPTS / "mutate.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

FAKE_SRC = r'''#!%s
import json, os, signal, subprocess, sys, time
name = os.path.basename(sys.argv[0])
argv = sys.argv[1:]
cfg = json.load(open(os.environ["FAKE_CFG"], encoding="utf-8"))
log = {"name": name, "argv": argv, "cwd": os.getcwd(), "goroot": os.environ.get("GOROOT"),
       "pwd": os.environ.get("PWD"), "pid": os.getpid()}
c = cfg.get(name, {})
if name == "docker":
    c = c.get(argv[0] if argv else "", {})
overlay = {}
if "-overlay" in argv:
    overlay = json.load(open(argv[argv.index("-overlay") + 1], encoding="utf-8"))["Replace"]
for rule in c.get("when", []):
    path = overlay.get(rule["path"], rule["path"])
    try:
        seen = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        seen = ""
    if rule["contains"] in seen:
        c = rule["use"]
        break
if c.get("shell"):
    log["shell"] = subprocess.run(c["shell"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(log) + "\n")
if c.get("file"):
    sys.stdout.write(open(c["file"], encoding="utf-8").read())
if c.get("text"):
    sys.stdout.write(c["text"])
sys.stdout.flush()
if c.get("sleep"):
    time.sleep(c["sleep"])
if c.get("kill_self"):
    os.kill(os.getpid(), signal.SIGKILL)
sys.exit(c.get("rc", 0))
''' % sys.executable


def fixture_rc(name):
    return int((FIXTURES / f"{name}.rc").read_text())


def go_replay(name):
    return {"file": str(FIXTURES / f"{name}.jsonl"), "rc": fixture_rc(name)}


def text_replay(name):
    return {"file": str(FIXTURES / f"{name}.txt"), "rc": fixture_rc(name)}


def count_go_events(name, action):
    """Independent count from the fixture itself, so a parser bug cannot agree with itself."""
    n = 0
    for line in (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("Action") == action and d.get("Test"):
            n += 1
    return n


class Harness(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        # WHY: realpath because macOS tempdirs live behind the /var -> /private/var symlink and the
        # tools resolve the tree; comparisons below would otherwise fail on the prefix alone.
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_obj.cleanup)
        self.tmp = pathlib.Path(os.path.realpath(self.tmp_obj.name))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for tool in ("go", "npx", "docker"):
            p = self.bin / tool
            p.write_text(FAKE_SRC, encoding="utf-8")
            p.chmod(0o755)
        self.cfg_path = self.tmp / "fake-cfg.json"
        self.log_path = self.tmp / "fake-log.jsonl"
        self.cfg = {}
        self.write_cfg()
        self.tree = self.tmp / "tree"
        self.make_layout(self.tree)
        self.repo = self.tmp / "repo"
        (self.repo / "ai-service/.venv").mkdir(parents=True)
        (self.repo / "ai-service/.venv/pyvenv.cfg").write_text(
            "home = /usr/local/bin\nimplementation = CPython\nversion_info = 3.12.13\n", encoding="utf-8")
        self.env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "GOROOT": "/bogus/goroot",
                    "FAKE_CFG": str(self.cfg_path), "FAKE_LOG": str(self.log_path),
                    "DOCKERSLOT_DIR": str(self.tmp / "slots"), "MUTATE_BIND_SETTLE_S": "0"}

    def make_layout(self, tree):
        for d in ("backend-golang/internal/logger", "backend-golang/internal/tag",
                  "angular-workspace/node_modules", "angular-workspace/projects/frontend/src/app",
                  "ai-service/tests", "ai-service/app"):
            (tree / d).mkdir(parents=True, exist_ok=True)
        (tree / "angular-workspace/projects/frontend/src/app/site-content.spec.ts").write_text("// spec\n", encoding="utf-8")
        (tree / "angular-workspace/projects/frontend/src/app/other.spec.ts").write_text("// spec\n", encoding="utf-8")
        (tree / "ai-service/tests/test_config.py").write_text("# test\n", encoding="utf-8")

    def write_cfg(self):
        self.cfg_path.write_text(json.dumps(self.cfg), encoding="utf-8")

    def fake(self, **tools):
        self.cfg.update(tools)
        self.write_cfg()

    def calls(self, name=None):
        if not self.log_path.exists():
            return []
        rows = [json.loads(x) for x in self.log_path.read_text(encoding="utf-8").splitlines()]
        return [r for r in rows if name is None or r["name"] == name]

    def run_tool(self, script, *args, timeout=60):
        return subprocess.run([sys.executable, str(script), *map(str, args)], env=self.env, capture_output=True,
                              text=True, timeout=timeout)

    def gate(self, *args, **kw):
        r = self.run_tool(GATE, *args, **kw)
        r.first = r.stdout.splitlines()[0] if r.stdout.strip() else ""
        return r

    def summary_of(self, r):
        out = r.first.rsplit("out=", 1)[1]
        return json.loads(pathlib.Path(out + ".summary.json").read_text(encoding="utf-8"))

    def fresh_out(self, name="run.out"):
        d = tempfile.mkdtemp(dir=self.tmp)
        return pathlib.Path(d) / name

    def has(self, tool):
        return shutil.which(tool) is not None
