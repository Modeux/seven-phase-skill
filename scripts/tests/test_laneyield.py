# -*- coding: utf-8 -*-
"""Tests for laneyield.py: lean PROGRESS tables, verdict classes, final severity, Workflow lane cost.

Usage:
  python3 -m unittest discover -s scripts/tests -p 'test_laneyield.py' -v
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import laneyield  # noqa: E402

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
M = 1_000_000

PROGRESS = """# w3 pins

## 計畫
| 條目 | 來源 | 嚴重度 | 裁決 | 處置 |
|---|---|---|---|---|
| 不是審查 | spec#1 | HIGH | 收 | 不該被讀 |

## 審查
owner 定 2 路。

| 條目 | 來源 | 嚴重度 | 裁決 | 處置 |
|---|---|---|---|---|
| 檔頭寫錯 | codex#2、comment-analyzer#1 | MEDIUM | 收 | 改檔頭 |
| 只動格子 | comment-analyzer#2 | MEDIUM | 收（fail-closed） | bump |
| 靠人記得 | codex#1、comment-analyzer#3 | MEDIUM | 收（owner 10-08 選） | 凍結 |

## 結果
| 條目 | 來源 | 嚴重度 | 裁決 | 處置 |
|---|---|---|---|---|
| 也不是 | spec#2 | HIGH | 收 | - |
"""

REVIEW = """# REVIEW

## 3. 去重地圖
| 編號 | 摘要 | 來源 |
|---|---|---|
| R1 | a | go(M)、spec(M) — 2 |
| R2 | b | go(H) — 1 |
| R3 | c | spec(M) — 1 |
| R4 | d | go(H) — 1 |
| R5 | e | spec(M) — 1 |
| R6 | f | spec(M) — 1 |
| L1 | g | go(L) — 1 |

## 8. 第 7 階段收斂裁決（2026-10-09）
| 編號 | 裁決 | 最終嚴重度 | 依據 | 處置 |
|---|---|---|---|---|
| R1 | confirmed | MEDIUM | x | fix |
| R2 | won't fix（成立，本包不修） | HIGH | x | backlog |
| R3 | unverified | MEDIUM | x | backlog |
| R4 | confirmed（HIGH 降 LOW） | LOW | x | fix |
| R5 | 不收 | MEDIUM | x | - |
| R6 | 收 | MEDIUM | x | fix |
| L1 | false positive | LOW | x | - |
"""


def iso(seconds_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - seconds_ago))


def write_agent(session_dir, agent_id, prompt, inp_tokens, meta, workflow=None, wrote=None):
    base = pathlib.Path(session_dir) / "subagents"
    if workflow:
        base = base / "workflows" / workflow
    base.mkdir(parents=True, exist_ok=True)
    content = [{"type": "text", "text": "ok"}]
    if wrote:
        content = [{"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": wrote, "content": "x"}}]
    records = [
        {"type": "user", "timestamp": iso(60), "message": {"role": "user", "content": prompt}},
        {"type": "assistant", "timestamp": iso(50), "message": {
            "id": f"msg-{agent_id}", "model": "claude-opus-5-5", "content": content,
            "usage": {"input_tokens": inp_tokens, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0, "output_tokens": 0}}},
    ]
    (base / f"agent-{agent_id}.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    (base / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")


class LaneYieldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.proj = self.root / "proj"
        self.proj.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_verdict_class_checks_negative_forms_first(self):
        cases = {
            "confirmed": "valid", "成立": "valid", "部分成立": "valid", "won't fix": "valid", "won’t fix": "valid",
            "不修": "valid", "收（fail-closed）": "valid", "owner 拍板": "valid", "**confirmed**": "valid",
            "false positive": "refuted", "FP": "refuted", "誤報": "refuted", "不成立": "refuted", "不收": "refuted",
            "unverified": "unverified", "未驗證": "unverified", "": None, "pending": None,
        }
        for text, want in cases.items():
            self.assertEqual(laneyield.verdict_class(text), want, text)

    def test_lean_progress_table_credits_each_row_to_its_lanes(self):
        path = self.write(".claude/PRPs/plans/w3/PROGRESS.md", PROGRESS)
        stats, _ = laneyield.collect("w3", [str(path)], [self.proj])
        self.assertEqual(set(stats), {"codex", "comment-analyzer"})
        ca, cx = stats["comment-analyzer"], stats["codex"]
        self.assertEqual((ca["R"], ca["Rok"], ca["uR"], ca["uRok"]), (3, 3, 1, 1))
        self.assertEqual((cx["R"], cx["Rok"], cx["uR"], cx["uRok"]), (2, 2, 0, 0))

    def test_review_paths_fall_back_to_progress_when_no_review_doc(self):
        self.write(".claude/PRPs/plans/w3/PROGRESS.md", PROGRESS)
        self.assertEqual([pathlib.Path(p).name for p in laneyield.review_paths(self.root, "w3")], ["PROGRESS.md"])
        self.write(".claude/PRPs/plans/w3/REVIEW.md", REVIEW)
        self.assertEqual([pathlib.Path(p).name for p in laneyield.review_paths(self.root, "w3")], ["REVIEW.md"])

    def test_final_severity_column_overrides_id_prefix_and_verdict_classes(self):
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", REVIEW)
        stats, _ = laneyield.collect("s", [str(path)], [self.proj])
        go, spec = stats["go"], stats["spec"]
        # WHY: R2 is won't fix (valid, stays HIGH → R); R4 dropped to LOW so it is an L finding.
        self.assertEqual((go["R"], go["Rok"], go["uR"], go["uRok"], go["L"], go["uL"]), (2, 2, 1, 1, 2, 2))
        # WHY: R3 unverified is neither ok nor refuted; R5 不收 refuted; R6 收 valid.
        self.assertEqual((spec["R"], spec["Rok"], spec["uR"], spec["uRok"], spec["unver"]), (4, 2, 3, 1, 1))
        self.assertEqual(go["unver"], 0)

    def test_review_without_final_severity_keeps_id_prefix_class(self):
        old = REVIEW.replace("| 編號 | 裁決 | 最終嚴重度 | 依據 | 處置 |", "| 編號 | 裁決 | 嚴重度備註 | 依據 | 處置 |")
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", old)
        stats, _ = laneyield.collect("s", [str(path)], [self.proj])
        self.assertEqual((stats["go"]["R"], stats["go"]["L"]), (3, 1))

    def test_workflow_lane_cost_joined_by_label_when_dispatch_names_slug(self):
        sess = self.proj / "sid1"
        write_agent(sess, "w1", "review s: snapshot tmp/s/review/", M,
                    {"agentType": "go-reviewer", "description": "lane-go"}, workflow="wf_1")
        write_agent(sess, "w2", "review other-task", 2 * M,
                    {"agentType": "go-reviewer", "description": "lane-ts"}, workflow="wf_1")
        write_agent(sess, "w3", "verify s", 3 * M,
                    {"agentType": "converge-verifier", "description": "verify-x"}, workflow="wf_1")
        write_agent(sess, "a1", "write tmp/s/review/lane-spec.md for s", 5 * M,
                    {"agentType": "lane-reviewer", "description": "spec lane"})
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", REVIEW)
        _, costs = laneyield.collect("s", [str(path)], [self.proj])
        self.assertEqual(costs, {"go": 4.0, "spec": 20.0})

    def test_cli_review_progress_prints_lane_rows_with_unver_column(self):
        path = self.write(".claude/PRPs/plans/w3/PROGRESS.md", PROGRESS)
        p = subprocess.run([sys.executable, str(SCRIPTS / "laneyield.py"), "--review", str(path), "--slug", "w3",
                            "--project-dir", str(self.proj)], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("unver", p.stdout)
        lanes = [line.split()[0] for line in p.stdout.splitlines()[2:] if line.strip()]
        self.assertEqual(lanes[:2], ["comment-analyzer", "codex"], p.stdout)


if __name__ == "__main__":
    unittest.main()
