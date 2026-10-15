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

    def test_verdict_class_checks_pending_and_negative_forms_first(self):
        cases = {
            "confirmed": "valid", "成立": "valid", "部分成立": "valid", "won't fix": "valid", "won’t fix": "valid",
            "不修": "valid", "收（fail-closed）": "valid", "owner 拍板": "valid", "**confirmed**": "valid",
            "false positive": "refuted", "FP": "refuted", "誤報": "refuted", "不成立": "refuted", "不收": "refuted",
            "unverified": "unverified", "未驗證": "unverified", "": None, "pending": "pending",
            # Shapes seen in real records:
            "需拍板→owner 拍板": "valid", "owner 拍板：「改描述」→「改動作」": "valid",
            "confirmed（owner #9；CRITICAL→HIGH）": "valid", "機制成立；第一次失敗本身會擋部署": "valid",
            "待驗證＋owner 拍板": "pending", "修片確認（註解）": "pending", "待裁決": "pending",
            "併 L2": None, "依 D1": None,
            # Independent-review cases (10-15):
            "未成立": "refuted", "不算成立": "refuted", "無法成立": "refuted", "未收": "refuted",
            "不確定是否成立": "pending", "需 owner 拍板": "pending", "待 owner 拍板": "pending",
            "needs owner decision": "pending", "需 owner（已拍板）": "valid", "成立→待修": "valid", "待修": "valid",
        }
        for text, want in cases.items():
            self.assertEqual(laneyield.verdict_class(text), want, text)

    def test_severity_class_reads_notes_and_arrows(self):
        cases = {"**LOW**（原 HIGH）": "L", "LOW（宣稱 MEDIUM）": "L", "MEDIUM（原 LOW）": "R", "HIGH／MEDIUM／LOW→MEDIUM": "R",
                 "MEDIUM（宣稱）→ LOW": "L", "LOW": "L", "—": None, "": None}
        for text, want in cases.items():
            self.assertEqual(laneyield.severity_class(text), want, text)

    def review_issues(self, doc, **kw):
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", doc)
        issues = []
        findings, verdicts = laneyield.parse(str(path), issues, **kw)
        return findings, verdicts, "\n".join(m for _, m in issues)

    def test_incremental_round_with_other_id_prefix_is_counted(self):
        # life-sense-ask-today shape: 增量審查 numbers its map N1…／NL1… under its own 地圖 heading.
        doc = """## 去重地圖
| 編號 | 宣稱嚴重度 | 一句話 | 來源 |
|---|---|---|---|
| R1 | HIGH | a | go#1 |
## 第 7 階段收斂裁決
| 編號 | 裁決 | 最終嚴重度 |
|---|---|---|
| R1 | confirmed | HIGH |
## 第 7 階段增量審查
### 去重地圖
| 編號 | 宣稱嚴重度 | 一句話 | 來源 |
|---|---|---|---|
| N1 | HIGH | b | spec#1 |
| NL1 | LOW | c | spec#2 |
## 第 7 階段增量收斂裁決
| 編號 | 裁決 | 最終嚴重度 |
|---|---|---|
| N1 | confirmed | HIGH |
"""
        findings, verdicts, issues = self.review_issues(doc)
        self.assertEqual(issues, "")
        self.assertEqual(findings, {"R1": ("R", ["go"]), "N1": ("R", ["spec"]), "NL1": ("L", ["spec"])})
        self.assertEqual(verdicts["N1"], "confirmed")

    def test_missing_verdict_table_unknown_verdict_ids_and_stray_map_tables_are_errors(self):
        no_verdicts = "## 去重地圖\n| 編號 | 一句話 | 來源 |\n|---|---|---|\n| R1 | a | go#1 |\n## 第 7 階段裁決\n" \
                      "| 編號 | 判定 |\n|---|---|\n| R1 | confirmed |\n"
        self.assertIn("no verdicts", self.collect_issues(no_verdicts))
        orphan = "## 去重地圖\n| 編號 | 一句話 | 來源 |\n|---|---|---|\n| R1 | a | go#1 |\n" \
                 "## 補充\n| 編號 | 宣稱嚴重度 | 來源 |\n|---|---|---|\n| R3 | HIGH | codex#1 |\n" \
                 "## 收斂裁決\n| 編號 | 裁決 |\n|---|---|\n| R1 | confirmed |\n| R3 | confirmed |\n| X1 | confirmed |\n"
        _, _, issues = self.review_issues(orphan)
        self.assertIn("verdict for R3 has no map row", issues)
        self.assertIn("looks like a map", issues)
        self.assertNotIn("X1", issues)

    def collect_issues(self, doc):
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", doc)
        issues = []
        laneyield.collect("s", [str(path)], [self.proj], issues=issues, with_cost=False)
        return "\n".join(m for _, m in issues)

    def test_fences_blank_line_and_escaped_pipes_do_not_lose_rows(self):
        doc = """## 去重地圖
| 編號 | 一句話 | 來源 | 備註 |
|---|---|---|---|
| R1 | `a \\| b` | go#1 | x |

| R2 | c | ts#1 | y |
## 收斂裁決
```bash
# 重跑閘
```
| 編號 | 裁決 | 最終嚴重度 | 依據 |
|---|---|---|---|
| R1 | confirmed | MEDIUM | `x \\| y` 探針 |
| R2 | confirmed | LOW | z |
"""
        findings, verdicts, issues = self.review_issues(doc)
        self.assertEqual(issues, "")
        self.assertEqual(findings, {"R1": ("R", ["go"]), "R2": ("L", ["ts"])})
        self.assertEqual(set(verdicts), {"R1", "R2"})

    def test_round_matching_by_longest_prefix_and_round_name_in_map_heading(self):
        nested = """## 包 1
### 第 6 階段
#### 去重地圖
| 編號 | 一句話 | 來源 |
|---|---|---|
| R1 | a | go#1 |
### 第 7 階段收斂裁決
| 編號 | 裁決 |
|---|---|
| R1 | false positive |
## 包 2
### 第 6 階段
#### 去重地圖
| 編號 | 一句話 | 來源 |
|---|---|---|
| R1 | b | ts#1 |
### 第 7 階段收斂裁決
| 編號 | 裁決 |
|---|---|
| R1 | confirmed |
"""
        _, verdicts, issues = self.review_issues(nested)
        self.assertEqual(issues, "")
        self.assertEqual(verdicts, {"R1@1": "false positive", "R1@2": "confirmed"})
        named = "## 包 1 去重地圖\n| 編號 | 一句話 | 來源 |\n|---|---|---|\n| R1 | a | go#1 |\n" \
                "## 包 2 去重地圖\n| 編號 | 一句話 | 來源 |\n|---|---|---|\n| R1 | b | ts#1 |\n"
        findings, _, issues = self.review_issues(named)
        self.assertNotIn("duplicate", issues)
        self.assertEqual(findings, {"R1@1": ("R", ["go"]), "R1@2": ("R", ["ts"])})

    def test_list_map_duplicates_and_source_id_boundaries(self):
        doc = "## 去重地圖\n**R1【HIGH】a**\n- 來源：go#1\n**R1【HIGH】b**\n- 來源：ts#1\n"
        findings, _, issues = self.review_issues(doc)
        self.assertIn("duplicate map id R1", issues)
        self.assertEqual(findings, {"R1": ("R", ["go"])})
        cjk = "## 去重地圖\n| 編號 | 一句話 | 來源 |\n|---|---|---|\n| R1 | a | go#2和ts#1（spec#3 同） |\n"
        findings, _, _ = self.review_issues(cjk)
        self.assertEqual(findings["R1"][1], ["go", "ts", "spec"])

    def test_bare_lane_word_beside_ids_counts_only_for_known_lanes(self):
        doc = "## 去重地圖\n| 編號 | 一句話 | 來源 |\n|---|---|---|\n| R1 | a | go#1、grok(HIGH)、rollback |\n"
        findings, _, issues = self.review_issues(doc, known=("go", "grok"))
        self.assertEqual(findings["R1"][1], ["go", "grok"])
        self.assertIn("來源 words rollback are not lane ids", issues)

    def test_lean_l_row_with_blank_final_severity_stays_l(self):
        doc = "## 審查\n| 編號 | 來源 | 宣稱嚴重度 | 最終嚴重度 | 裁決 |\n|---|---|---|---|---|\n" \
              "| L1 | go#1 | LOW | — | won't fix |\n| L2 | spec#1 | LOW |  | false positive |\n"
        path = self.write(".claude/PRPs/plans/s/PROGRESS.md", doc)
        findings, _ = laneyield.parse(str(path))
        self.assertEqual(findings, {"L1": ("L", ["go"]), "L2": ("L", ["spec"])})

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

    # Real-format fixtures (content de-identified): each reproduces a record laneyield used to misread.

    def test_notes_column_after_sources_is_not_read_as_lanes(self):
        # character-dual-portrait shape: 來源 then 幾路同報 then a 備註 column full of prose words.
        doc = """# REVIEW
## 4. 去重地圖
### 4.1 R（MEDIUM 以上）
| 編號 | 宣稱嚴重度 | 一句話 | 位置 | 來源 | 幾路同報 | 備註 |
|---|---|---|---|---|---|---|
| R1 | HIGH | rollback job 混版 | `a.go:1` | sec-a#1, go#2, timing#1 | 3 | 各路：job、rollback decision（owner）見 runbook |
| R2 | MEDIUM | 型別註解錯 | `b.ts:2` | comments#1, grok#3, ts#3 | 3 | 各路：comments、grok、ts MEDIUM |
### 4.2 L（LOW）
| 編號 | 宣稱嚴重度 | 一句話 | 位置 | 來源 | 幾路同報 | 備註 |
|---|---|---|---|---|---|---|
| L1 | LOW | toast 文案 | `c.ts:3` | spec#7 | 1 | |
"""
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", doc)
        issues = []
        findings, _ = laneyield.parse(str(path), issues)
        self.assertEqual(findings, {"R1": ("R", ["sec-a", "go", "timing"]), "R2": ("R", ["comments", "grok", "ts"]),
                                    "L1": ("L", ["spec"])})
        self.assertEqual(issues, [])

    def test_severity_column_named_with_verdict_word_is_not_the_verdict_column(self):
        # fk-ri-index shape: 嚴重度（宣稱→裁決） sits before 裁決; every R used to read as "no verdict".
        doc = """# fk
## 審查
| 條目 | 一句話 | 來源 | 嚴重度（宣稱→裁決） | 裁決 | 處置 |
|---|---|---|---|---|---|
| R1 | 全掃 | db#1、go#2、tests#3（codex 殘留節同報） | MEDIUM→MEDIUM | 成立 | 加索引 |
| R2 | 中途失敗 | codex#1、silent#1 | HIGH／MEDIUM／LOW→MEDIUM | 機制成立；重跑才靜默 | 改 |
| L1 | WHY 沒寫理由 | comments#3 | LOW | 成立 | 改寫 |
"""
        path = self.write(".claude/PRPs/plans/fk/PROGRESS.md", doc)
        stats, _ = laneyield.collect("fk", [str(path)], [self.proj])
        self.assertEqual((stats["db"]["R"], stats["db"]["Rok"]), (1, 1))
        self.assertEqual((stats["codex"]["R"], stats["codex"]["Rok"], stats["codex"]["uRok"]), (1, 1, 0))
        self.assertNotIn("codex", [lane for lane, s in stats.items() if s["R"] == 2])
        self.assertEqual((stats["comments"]["L"], stats["comments"]["R"]), (1, 0))

    def test_sources_column_before_an_empty_trailing_column(self):
        # life-sense-ask-today shape: a trailing 需 owner 拍板 column, mostly empty, hid the 來源 cell.
        doc = """## 去重地圖
| 編號 | 宣稱嚴重度 | 一句話 | 位置 | 來源 | 同報路數 | 需 owner 拍板 |
|---|---|---|---|---|---|---|
| R1 | HIGH | x | `a.go:1`；`b.sql:2` | codex#2、db#2、go#4 | 3 | |
| R2 | HIGH | y | `c.go:3` | impact#3 | 1 | 是 |
"""
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", doc)
        findings, _ = laneyield.parse(str(path))
        self.assertEqual(findings["R1"][1], ["codex", "db", "go"])
        self.assertEqual(findings["R2"][1], ["impact"])

    def test_verdict_rows_with_id_lists_and_titles(self):
        # age-gate-unify / life-sense shapes: `| L2、L4 |` merged rows and `| R6 title |` first cells.
        doc = """## 4. 去重地圖
| 編號 | 一句話 | 來源 |
|---|---|---|
| R6 | a | spec#1 |
| R7 | b | go#1 |
| L2 | c | go#2 |
| L4 | d | spec#2 |
## 11. 第 7 階段收斂裁決表
| 編號 | 裁決 | 最終嚴重度 | 依據 |
|---|---|---|---|
| R6 取景綁定不驗 | confirmed-部分（HIGH→MEDIUM） | MEDIUM | v |
| R7 | false positive | MEDIUM | v |
### L 表裁決（同層合併）
| 編號 | 裁決 | 處置 |
|---|---|---|
| L2、L4 | confirmed（註解） | 修 |
"""
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", doc)
        _, verdicts = laneyield.parse(str(path))
        self.assertEqual({k: laneyield.verdict_class(v) for k, v in verdicts.items()},
                         {"R6": "valid", "R7": "refuted", "L2": "valid", "L4": "valid"})

    def test_map_under_a_map_heading_wins_over_other_tables_with_sources(self):
        doc = """## 3. 去重地圖
| 編號 | 一句話 | 來源 |
|---|---|---|
| R1 | a | go#1 |
## 7. 要 owner 拍板的題目
| 編號 | 題目 | 來源 |
|---|---|---|
| R1 | 要不要修 | owner |
"""
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", doc)
        issues = []
        findings, _ = laneyield.parse(str(path), issues)
        self.assertEqual(findings, {"R1": ("R", ["go"])})
        self.assertEqual(issues, [])

    def test_data_problems_are_reported_not_dropped(self):
        doc = """## 去重地圖
| 編號 | 一句話 | 來源 |
|---|---|---|
| R1 | a | go#1 |
| R2 | b | 主 session 補記 |
| R1 | dup | spec#1 |
| R3 | c | go#2、rollback |
## 收斂裁決
| 編號 | 裁決 | 最終嚴重度 |
|---|---|---|
| R1 | 修片確認 | HIGH |
| R2 | 依 D1 | HIGH |
"""
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", doc)
        issues = []
        laneyield.collect("s", [str(path)], [self.proj], issues=issues, with_cost=False)
        text = "\n".join(msg for _, msg in issues)
        self.assertIn("R2: 來源 cell names no lane", text)
        self.assertIn("duplicate map id R1", text)
        self.assertIn("R verdict pending: 1 — R1", text)
        self.assertIn("R verdict unrecognized: 1 — R2", text)
        self.assertIn("R verdict missing: 1 — R3", text)

    def test_word_sources_that_are_not_lane_ids_are_flagged(self):
        doc = """## 去重地圖
| 編號 | 一句話 | 來源 |
|---|---|---|
| R1 | a | go#1、spec#2 |
| R2 | b | go（M）、job — 2 |
"""
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", doc)
        issues = []
        findings, _ = laneyield.parse(str(path), issues)
        self.assertEqual(findings["R2"], ("R", ["go"]))
        self.assertTrue(any("R2: 來源 words job are not lane ids" in m for _, m in issues), issues)

    def test_two_review_rounds_in_one_doc_keep_their_own_ids_and_verdicts(self):
        # journey-entry-and-home shape: 包 1 and 包 2a each number their map from R1 and have their own verdicts.
        doc = """# REVIEW
## 包 1 種子入口
### 4. 去重地圖
| 編號 | 一句話 | 來源 |
|---|---|---|
| R1 | a | go#1 |
### 9. 第 7 階段收斂裁決
| 編號 | 裁決 | 最終嚴重度 |
|---|---|---|
| R1 | false positive | HIGH |
## 包 2a 沉積
### 4. 去重地圖
| 編號 | 一句話 | 來源 |
|---|---|---|
| R1 | b | spec#1 |
| R2 | c | spec#2 |
### 9. 第 7 階段收斂裁決
| 編號 | 裁決 | 最終嚴重度 |
|---|---|---|
| R1 | confirmed | MEDIUM |
| R2 | confirmed | LOW |
"""
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", doc)
        issues = []
        findings, verdicts = laneyield.parse(str(path), issues)
        self.assertEqual(issues, [])
        self.assertEqual(findings, {"R1@1": ("R", ["go"]), "R1@2": ("R", ["spec"]), "R2": ("L", ["spec"])})
        self.assertEqual(verdicts, {"R1@1": "false positive", "R1@2": "confirmed", "R2": "confirmed"})

    def test_list_style_map_with_lane_ids_is_read(self):
        # chat-opener-icebreaker shape (pre-table map): bold id+【SEVERITY】 blocks with a `- 來源：` line.
        doc = """## 3. 去重地圖
### R（MEDIUM 以上）
**R1【CRITICAL】額度只綁單一對話**
- 位置：`a.go:1`
- 來源：security#1【CRITICAL】、go#1【HIGH】、spec#1【HIGH】（needs owner decision）
- 幾路同報：3

**L1【LOW】INVARIANT 寫錯**
- 來源：comments#2【LOW】
## 6. 第 7 階段收斂裁決（2026-10-09）
| 編號 | 裁決 | 依據 |
|---|---|---|
| R1 | confirmed（owner 拍板） | x |
"""
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", doc)
        issues = []
        findings, verdicts = laneyield.parse(str(path), issues)
        self.assertEqual(issues, [])
        self.assertEqual(findings, {"R1": ("R", ["security", "go", "spec"]), "L1": ("L", ["comments"])})
        self.assertEqual(laneyield.verdict_class(verdicts["R1"]), "valid")

    def test_heading_style_map_without_sources_column_is_an_error_not_zero(self):
        # character-life-sense REVIEW-together shape: one ### heading per finding, ids only in other tables.
        doc = """## 3. 去重地圖 R
### R1 [CRITICAL] 重按
來源：go、spec
## 收斂裁決
| 編號 | 裁決 |
|---|---|
| R1 重按 | confirmed |
## 嚴重度修正
| 編號 | 原宣稱 | 修正後 |
|---|---|---|
| R1 | CRITICAL | HIGH |
"""
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", doc)
        p = subprocess.run([sys.executable, str(SCRIPTS / "laneyield.py"), "--review", str(path), "--slug", "s",
                            "--no-cost", "--strict"], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("lane stats unavailable", p.stdout)

    def test_lanes_missing_from_the_review_dir_are_flagged(self):
        review = self.root / "tmp" / "s" / "review"
        self.write("tmp/s/review/tasks/go.md", "x")
        self.write("tmp/s/review/ext-codex.log", "x")
        doc = "## 去重地圖\n| 編號 | 一句話 | 來源 |\n|---|---|---|\n| R1 | a | go#1、codx#1 |\n"
        path = self.write(".claude/PRPs/plans/s/REVIEW.md", doc)
        issues = []
        laneyield.collect("s", [str(path)], [self.proj], issues=issues, review_dir=review, with_cost=False)
        self.assertTrue(any("lanes not in" in m and "codx" in m for _, m in issues), issues)


if __name__ == "__main__":
    unittest.main()
