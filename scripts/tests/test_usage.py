# -*- coding: utf-8 -*-
"""Tests for _usage.py and the agentcost／taskcost CLIs on synthetic transcripts.

Usage:
  python3 -m unittest discover -s scripts/tests -p 'test_usage.py' -v
"""
import importlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
OPUS = "claude-opus-5-5"  # (4, 5, 0.2, 20) per MTok
M = 1_000_000


def iso(seconds_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - seconds_ago))


def usage(inp=0, cw=0, cr=0, out=0, cw5m=None, cw1h=None):
    u = {"input_tokens": inp, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr, "output_tokens": out}
    if cw5m is not None or cw1h is not None:
        u["cache_creation"] = {"ephemeral_5m_input_tokens": cw5m or 0, "ephemeral_1h_input_tokens": cw1h or 0}
    return u


def asst(mid, u, ts, model=OPUS, content=None, effort=None):
    r = {"type": "assistant", "timestamp": ts,
         "message": {"id": mid, "model": model, "role": "assistant", "content": content or [{"type": "text", "text": "x"}]}}
    if u is not None:
        r["message"]["usage"] = u
    if effort:
        r["effort"] = effort
    return r


def user(text, ts):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}


def tool_use(tid, name, **inp):
    return [{"type": "tool_use", "id": tid, "name": name, "input": inp}]


def write_jsonl(path, records, raw_lines=()):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in records] + list(raw_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_agent(session_dir, agent_id, records, meta, workflow=None):
    base = pathlib.Path(session_dir) / "subagents"
    if workflow:
        base = base / "workflows" / workflow
    write_jsonl(base / f"agent-{agent_id}.jsonl", records)
    (base / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return base / f"agent-{agent_id}.jsonl"


def run(script, *args):
    p = subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(f"{script} exit {p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}")
    return p.stdout


class UsageModuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.u = importlib.import_module("_usage")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_encode_project_dir_replaces_every_non_alphanumeric(self):
        self.assertEqual(self.u.encode_project_dir("/Users/x/repo/.claude/worktrees/together"),
                         "-Users-x-repo--claude-worktrees-together")
        self.assertEqual(self.u.encode_project_dir("/a/b_c.d"), "-a-b-c-d")

    def test_project_dirs_default_uses_encoded_cwd(self):
        cwd = "/Users/x/repo/.claude/worktrees/together"
        dirs = self.u.project_dirs(cwd=cwd, projects_root=self.root)
        self.assertEqual(dirs, [self.root / "-Users-x-repo--claude-worktrees-together"])

    def test_project_dirs_explicit_project_dir_wins(self):
        self.assertEqual(self.u.project_dirs(project_dir=str(self.root / "p"), all_worktrees=True),
                         [self.root / "p"])

    def test_all_worktrees_selects_main_worktrees_and_tmp_but_not_sibling_checkouts(self):
        main = "-Users-x-repo"
        for name in (main, f"{main}--claude-worktrees-a", f"{main}--claude-worktrees-b-tmp-review",
                     f"{main}-tmp-chat-review", f"{main}-image", f"{main}2", "-other"):
            (self.root / name).mkdir()
        dirs = self.u.project_dirs(all_worktrees=True, checkout="/Users/x/repo", projects_root=self.root)
        names = sorted(d.name for d in dirs)
        self.assertEqual(names, sorted([main, f"{main}--claude-worktrees-a", f"{main}--claude-worktrees-b-tmp-review",
                                        f"{main}-tmp-chat-review"]))

    def test_main_checkout_resolves_git_common_dir_parent(self):
        repo = self.root / "repo"
        repo.mkdir()
        try:
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git unavailable")
        (repo / "sub").mkdir()
        self.assertEqual(pathlib.Path(self.u.main_checkout(repo / "sub")).resolve(), repo.resolve())

    def test_split_tokens_uses_cache_creation_sub_object(self):
        self.assertEqual(self.u.split_tokens(usage(1, 30, 4, 5, cw5m=10, cw1h=20)), (1, 10, 20, 4, 5))

    def test_split_tokens_without_sub_object_treats_all_cache_writes_as_5m(self):
        self.assertEqual(self.u.split_tokens(usage(1, 30, 4, 5)), (1, 30, 0, 4, 5))

    def test_dollars_prices_1h_writes_at_twice_input_and_5m_at_table_rate(self):
        tokens = self.u.split_tokens(usage(M, 2 * M, M, M, cw5m=M, cw1h=M))
        self.assertAlmostEqual(self.u.dollars(OPUS, tokens), 4 + 5 + 8 + 0.2 + 20)
        self.assertAlmostEqual(self.u.dollars(OPUS, self.u.split_tokens(usage(M, 2 * M, M, M))), 4 + 10 + 0.2 + 20)

    def test_dollars_unknown_model_is_none_not_zero(self):
        self.assertIsNone(self.u.dollars("claude-unknown-9", (M, 0, 0, 0, 0)))

    def test_agentcost_prices_still_importable(self):
        agentcost = importlib.import_module("agentcost")
        self.assertIs(agentcost.PRICES, self.u.PRICES)

    def test_message_split_across_lines_takes_per_field_max_not_sum(self):
        ts = iso(60)
        f = write_jsonl(self.root / "s.jsonl", [
            asst("m1", usage(1000, 0, 500, 8), ts, effort="high"),
            asst("m1", usage(1000, 0, 500, 150), ts),
            asst("m1", usage(1000, 0, 500, 227), iso(30)),
        ])
        msgs = self.u.read_messages(f)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(tuple(msgs[0].tokens), (1000, 0, 0, 500, 227))
        self.assertEqual(msgs[0].effort, "high")
        self.assertEqual(msgs[0].timestamp, ts)
        self.assertEqual(msgs[0].model, OPUS)

    def test_records_before_since_excluded_even_in_recently_modified_file(self):
        f = write_jsonl(self.root / "s.jsonl", [
            asst("old", usage(out=10), iso(10 * 86400)),
            asst("new", usage(out=20), iso(3600)),
        ])
        msgs = self.u.read_messages(f, since=time.time() - 3 * 86400)
        self.assertEqual([m.id for m in msgs], ["new"])

    def test_until_excludes_later_records(self):
        f = write_jsonl(self.root / "s.jsonl", [
            asst("old", usage(out=10), iso(10 * 86400)),
            asst("new", usage(out=20), iso(3600)),
        ])
        msgs = self.u.read_messages(f, until=time.time() - 3 * 86400)
        self.assertEqual([m.id for m in msgs], ["old"])

    def test_file_older_than_since_is_skipped_by_mtime(self):
        f = write_jsonl(self.root / "s.jsonl", [asst("m", usage(out=1), iso(3600))])
        old = time.time() - 30 * 86400
        os.utime(f, (old, old))
        self.assertEqual(self.u.read_messages(f, since=time.time() - 3 * 86400), [])

    def test_quality_counters_for_missing_usage_missing_id_and_bad_json(self):
        ts = iso(60)
        noid = asst(None, usage(out=5), ts)
        del noid["message"]["id"]
        f = write_jsonl(self.root / "s.jsonl", [
            asst("m1", None, ts),
            noid,
            asst("m2", usage(out=7), ts),
        ], raw_lines=['{"type": "assistant", "message": {"id": "trunc'])
        q = {}
        msgs = self.u.read_messages(f, quality=q)
        self.assertEqual(q.get("no_usage"), 1)
        self.assertEqual(q.get("no_message_id"), 1)
        self.assertEqual(q.get("bad_json"), 1)
        self.assertEqual(sorted(m.tokens[4] for m in msgs), [0, 5, 7])

    def test_cost_of_counts_unpriced_messages(self):
        f = write_jsonl(self.root / "s.jsonl", [
            asst("m1", usage(inp=M), iso(60), model="claude-unknown-9"),
            asst("m2", usage(inp=M), iso(60)),
        ])
        q = {}
        total = sum(self.u.cost_of(m, q) for m in self.u.read_messages(f, quality=q))
        self.assertAlmostEqual(total, 4.0)
        self.assertEqual(q.get("unpriced"), 1)

    def test_on_line_sees_every_line_with_first_flag(self):
        ts = iso(60)
        f = write_jsonl(self.root / "s.jsonl", [
            user("hi", ts),
            asst("m1", usage(out=1), ts),
            asst("m1", usage(out=2), ts, content=tool_use("t1", "Read", file_path="a")),
        ])
        seen = []
        self.u.read_messages(f, all_lines=True,
                             on_line=lambda r, msg, first: seen.append((r["type"], msg and msg.id, first)))
        self.assertEqual(seen, [("user", None, False), ("assistant", "m1", True), ("assistant", "m1", False)])

    def test_agent_files_finds_ordinary_and_workflow_agents_and_ignores_journal(self):
        sess = self.root / "sid1"
        write_agent(sess, "a1", [asst("x", usage(out=1), iso(60))], {"agentType": "tdd-guide", "description": "write tests"})
        write_agent(sess, "w1", [asst("y", usage(out=1), iso(60))],
                    {"agentType": "go-reviewer", "description": "lane-go", "workflowPhase": "Review"}, workflow="wf_x")
        write_jsonl(sess / "subagents" / "workflows" / "wf_x" / "journal.jsonl", [{"type": "launched"}])
        found = {a.agent_id: a for a in self.u.agent_files(self.root)}
        self.assertEqual(set(found), {"a1", "w1"})
        self.assertFalse(found["a1"].is_workflow)
        self.assertEqual(found["a1"].label, "write tests")
        self.assertTrue(found["w1"].is_workflow)
        self.assertEqual(found["w1"].label, "lane-go")
        self.assertEqual(found["w1"].meta["agentType"], "go-reviewer")
        self.assertEqual({a.agent_id for a in self.u.agent_files(self.root, session="sid1")}, {"a1", "w1"})
        self.assertEqual(list(self.u.agent_files(self.root, session="nope")), [])


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = pathlib.Path(self.tmp.name) / "proj"

    def tearDown(self):
        self.tmp.cleanup()

    def test_agentcost_counts_workflow_agents_and_final_output_tokens(self):
        sess = self.proj / "sid1"
        ts = iso(3600)
        write_agent(sess, "a1", [
            asst("m1", usage(out=8), ts),
            asst("m1", usage(out=500_000), ts),
            asst("m1", usage(out=M), ts),
        ], {"agentType": "tdd-guide", "description": "t"})
        write_agent(sess, "w1", [asst("m2", usage(inp=M), ts)],
                    {"agentType": "go-reviewer", "description": "lane-go"}, workflow="wf_x")
        out = run("agentcost.py", "--project-dir", str(self.proj), "--days", "3")
        m = re.search(r"total \$([\d.]+) across (\d+) agents", out)
        self.assertIsNotNone(m, out)
        self.assertEqual((float(m.group(1)), int(m.group(2))), (24.0, 2), out)

    def test_agentcost_excludes_records_before_window(self):
        write_agent(self.proj / "sid1", "a1", [
            asst("old", usage(out=M), iso(10 * 86400)),
            asst("new", usage(inp=M), iso(3600)),
        ], {"agentType": "tdd-guide", "description": "t"})
        out = run("agentcost.py", "--project-dir", str(self.proj), "--days", "3")
        self.assertRegex(out, r"total \$4\.00 across 1 agents")

    def test_agentcost_reports_unpriced_count(self):
        write_agent(self.proj / "sid1", "a1", [asst("m", usage(inp=M), iso(3600), model="claude-unknown-9")],
                    {"agentType": "tdd-guide", "description": "t"})
        out = run("agentcost.py", "--project-dir", str(self.proj), "--days", "3")
        self.assertRegex(out, r"unpriced[^\n]*\b1\b")

    def test_taskcost_attributes_workflow_agent_and_main_turns(self):
        ts = iso(3600)
        write_jsonl(self.proj / "sid1.jsonl", [
            asst("m0", usage(inp=M), ts),
            asst("m1", usage(out=8), ts),
            asst("m1", usage(out=M), ts, content=tool_use("t1", "Read", file_path=".claude/PRPs/plans/foo/SPEC.md")),
            asst("m2", usage(inp=2 * M), ts),
        ])
        write_agent(self.proj / "sid1", "w1", [
            user("review tmp/foo/review/ snapshot", ts),
            asst("s1", usage(inp=10 * M), ts),
        ], {"agentType": "go-reviewer", "description": "lane-go"}, workflow="wf_x")
        out = run("taskcost.py", "--project-dir", str(self.proj), "--days", "3")
        rows = {line.split()[0]: line.split() for line in out.splitlines() if line.startswith(("foo", "(none)"))}
        self.assertIn("foo", rows, out)
        # WHY: m1 names the slug only on its second line, so the cursor at its first line is still (none):
        # (none) = m0 $4 + m1 $20; foo = m2 $8 main + workflow agent $40 sub.
        self.assertEqual(rows["(none)"][1:3], ["24", "0"], out)
        self.assertEqual(rows["foo"][1:3], ["8", "40"], out)


if __name__ == "__main__":
    unittest.main()
