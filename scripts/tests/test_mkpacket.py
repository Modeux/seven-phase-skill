# -*- coding: utf-8 -*-
"""mkpacket: one manifest -> codex／grok／agy review packets, excerpts taken mechanically from the matrix,
agy trimmed down a fixed ladder to the CLI cap, header count checked against matrix rows.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_mkpacket.py' -v
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
MK = SCRIPTS / "mkpacket.py"


def numbered(prefix, n, width):
    return "".join(f"{prefix}{i:03d} " + "x" * width + "\n" for i in range(1, n + 1))


A_GO = "".join(
    ("func Alpha(n int) int {\n" if i == 30 else f"// a{i:03d} " + "a" * 96 + "\n") for i in range(1, 81)
)
B_TS = numbered("// b", 40, 196)
TINY_TS = numbered("// t", 15, 196)

PLAN_OLD = """# PLAN

## 0 定案
- 決定一：照做。

## 單一 writer 檔案矩陣

| 片 | 檔案 | 符號／行號 | 呼叫者與備註 |
|---|---|---|---|
| A | `app/src/a.go` | `Alpha` L10-L12、L50–52 | 多段 |
| A | `app/src/a.go` | `Alpha` | 只給函式名 |
| B | `app/web/b.ts` | :1-30 | |
| B | `.../web/new.ts` | 新檔 | 省略前綴 |
| B | `app/web/tiny.ts` | L1-L12 | 只證明存在 |

## 規則對照

<!-- rulesfor: app/src/a.go -->
| 規則檔 | 節 | 命中 | 處置 |
|---|---|---|---|
| `.claude/rules/x.md` | 節一 | HITCOLUMN-LONG-TEXT app/src/a.go | 套用 |

## 本任務用到的工具
TOOLS-SECTION-BODY

## 風險與回滾
RISK-BODY

## 不做清單
NOTDO-BODY
"""

PLAN_NEW = """# PLAN

## 0 定案
- 決定一。

## 單一 writer 檔案矩陣

| 片 | 檔案 | writer |
|---|---|---|
| A | `app/src/a.go` | tdd |
| B | `app/web/b.ts` | tdd |

## 風險與回滾
r

## 不做清單
n
"""

MATRIX_NEW = """# 矩陣

## A

| 檔案 | 符號／行號 | 呼叫者 |
|---|---|---|
| `app/src/a.go` | `Alpha` L10-L12 | x |

## B

- `app/web/b.ts` L1–5 呼叫者 y
- `app/web/new.ts` 新檔
"""

SPEC = ("# SPEC\n\n## 方向決議\nDIRECTION-BODY\n\n## 給 PM 的設計改動說明\nPM-SECTION-BODY\n\n"
        "## 驗收\nACCEPT-BODY\n\n## 背景\nBACKGROUND-BODY\n" + "背景填充 " * 600 + "\n")

RULE = "# 規則 X\n\n## 節一\n第一節內文 RULE-ONE\n\n### 節一子節\n子節內文\n\n## 節二\n第二節內文 RULE-TWO\n"

DIFF_SRC = ("diff --git a/app/src/a.go b/app/src/a.go\n--- a/app/src/a.go\n+++ b/app/src/a.go\n@@ -1 +1 @@\n"
            "-old\n+SRC-DIFF-A\n"
            "diff --git a/app/web/b.ts b/app/web/b.ts\n--- a/app/web/b.ts\n+++ b/app/web/b.ts\n@@ -1 +1 @@\n"
            "-old\n+SRC-DIFF-B\n")
DIFF_DOCS = "diff --git a/docs/x.md b/docs/x.md\n@@ -1 +1 @@\n-o\n+DOCS-DIFF-BODY\n"
DIFF_TESTS = "diff --git a/app/src/a_test.go b/app/src/a_test.go\n@@ -1 +1 @@\n-o\n+TESTS-DIFF-BODY\n"

HEADER = re.compile(r"^### ", re.M)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.snap = self.root / "wt-snap"
        self.write("wt-snap/app/src/a.go", A_GO)
        self.write("wt-snap/app/web/b.ts", B_TS)
        self.write("wt-snap/app/web/tiny.ts", TINY_TS)
        self.write("wt-snap/.claude/rules/x.md", RULE)
        self.write("wt-snap/tmp/review-input/PLAN.md", PLAN_OLD)
        self.write("wt-snap/tmp/review-input/SPEC.md", SPEC)
        self.write("wt-snap/tmp/review-input/plan-appendix/tests.md", "# 測試\nAPPENDIX-TESTS\n")
        self.write("wt-snap/tmp/review-input/plan-appendix/notes.md", "# 筆記\nAPPENDIX-NOTES\n")
        self.write("brief.md", "# 任務書\nBRIEF-BODY 請審 PLAN。\n")
        self.write("change-src.patch", DIFF_SRC)
        self.write("change-docs.patch", DIFF_DOCS)
        self.write("change-tests.patch", DIFF_TESTS)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def read(self, rel):
        return (self.root / rel).read_text(encoding="utf-8")

    def manifest(self, extra="", kind="plan-review", matrix="tmp/review-input/PLAN.md", rules=None):
        rules = ["rule: .claude/rules/x.md § 節一"] if rules is None else rules
        lines = [
            f"kind: {kind}",
            "snapshot: wt-snap                     # 讀程式碼的樹",
            "brief: brief.md",
            "plan: tmp/review-input/PLAN.md",
        ]
        if kind == "plan-review":
            lines += ["spec: tmp/review-input/SPEC.md", "appendix: tmp/review-input/plan-appendix",
                      f"matrix: {matrix}"] + rules
        else:
            lines += ["diff-src: change-src.patch", "diff-docs: change-docs.patch", "diff-tests: change-tests.patch"]
        lines += ["role: 跨層視角 ROLE-BODY", "agy-drop-spec: 給 PM 的設計改動說明", "agy-drop-plan: 本任務用到的工具"]
        self.write("manifest.txt", "\n".join(lines) + "\n" + extra)
        return self.root / "manifest.txt"

    def run_mk(self, *args, limit=None):
        env = dict(os.environ)
        env.pop("MKPACKET_AGY_MAX", None)
        if limit is not None:
            env["MKPACKET_AGY_MAX"] = str(limit)
        return subprocess.run([sys.executable, str(MK), *map(str, args)], capture_output=True, text=True,
                              env=env, timeout=60)

    def verdict(self, res):
        return (res.stdout.splitlines() or [""])[0]

    def field(self, res, key):
        m = re.search(rf"\b{key}=(\S+)", self.verdict(res))
        self.assertIsNotNone(m, f"no {key}= in {self.verdict(res)!r}\n{res.stdout}{res.stderr}")
        return m.group(1)


def excerpt_part(text):
    return text.split("# 程式碼摘錄", 1)[1].split("# 規則原文", 1)[0]


class PlanReviewPackets(Base):
    def test_writes_three_packets_with_verdict_line(self):
        res = self.run_mk(self.manifest())
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertRegex(self.verdict(res),
                         r"^PACKET OK rows=5 codex=\d+ grok=\d+ agy=\d+ agy-ladder=1$")
        for name in ("packet-codex.md", "packet-grok.md", "packet-agy.md"):
            self.assertTrue((self.root / name).is_file(), name)
        self.assertEqual(int(self.field(res, "grok")), len(self.read("packet-grok.md").encode("utf-8")))

    def test_codex_packet_lists_paths_without_excerpts(self):
        self.run_mk(self.manifest())
        codex = self.read("packet-codex.md")
        self.assertIn("BRIEF-BODY", codex)
        self.assertIn("## 你的角色\n跨層視角 ROLE-BODY", codex)
        for path in ("tmp/review-input/PLAN.md", "tmp/review-input/SPEC.md", "tmp/review-input/plan-appendix"):
            self.assertIn(path, codex)
        self.assertNotIn("# 程式碼摘錄", codex)
        self.assertNotIn("DIRECTION-BODY", codex)
        self.assertIn("不要修改任何檔案", codex)

    def test_grok_packet_carries_full_plan_spec_appendix_excerpts_rules(self):
        self.run_mk(self.manifest())
        grok = self.read("packet-grok.md")
        self.assertTrue(grok.startswith("你在唯讀快照內"), grok[:80])
        for marker in ("# PLAN 全文", "TOOLS-SECTION-BODY", "HITCOLUMN-LONG-TEXT", "# SPEC 全文", "PM-SECTION-BODY",
                       "# plan-appendix", "APPENDIX-TESTS", "APPENDIX-NOTES",
                       "# 程式碼摘錄（照矩陣機械取出，前後各多 10 行）", "# 規則原文"):
            self.assertIn(marker, grok)
        self.assertNotIn("ROLE-BODY", grok)

    def test_excerpt_headers_equal_matrix_rows_with_multirange_and_newfile_shapes(self):
        self.run_mk(self.manifest())
        part = excerpt_part(self.read("packet-grok.md"))
        heads = [line for line in part.splitlines() if line.startswith("### ")]
        self.assertEqual(heads, [
            "### app/src/a.go:L1-L22, L40-L62",
            "### app/src/a.go:L1-L80",
            "### app/web/b.ts:L1-L40",
            "### app/web/new.ts (new file in this PLAN, no code yet)",
            "### app/web/tiny.ts:L1-L15",
        ])
        self.assertIn("```\n// a001 ", part)
        self.assertIn("func Alpha(n int) int {", part)

    def test_line_range_formats_are_tolerated(self):
        plan = PLAN_OLD.replace("| :1-30 |", "| L2–4; 6-7, `x` L9 |")
        self.write("wt-snap/tmp/review-input/PLAN.md", plan)
        res = self.run_mk(self.manifest(), "--check", limit=10**9)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        res = self.run_mk(self.manifest())
        part = excerpt_part(self.read("packet-grok.md"))
        self.assertIn("### app/web/b.ts:L1-L19\n", part)

    def test_prose_only_cell_becomes_exists_only_row_with_a_note(self):
        plan = PLAN_OLD.replace("| :1-30 |", "| 加表 |").replace(
            "| 只給函式名 |", "| 只給函式名 |\n| A | `app/src/a.go` | `imports` | Angular imports |")
        self.write("wt-snap/app/src/a.go", A_GO.replace("// a070 ", "  imports: [A]; // a070 ", 1))
        self.write("wt-snap/tmp/review-input/PLAN.md", plan)
        res = self.run_mk(self.manifest())
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.field(res, "rows"), "6")
        self.assertRegex(res.stdout, r"(?m)^note: .*PLAN\.md:\d+ app/web/b\.ts: no range or name, L1-L12 only")
        heads = [line for line in excerpt_part(self.read("packet-grok.md")).splitlines() if line.startswith("### ")]
        self.assertIn("### app/web/b.ts:L1-L22", heads)
        self.assertIn("### app/src/a.go:L20-L80", heads)

    def test_new_shape_matrix_read_from_appendix_matrix_file(self):
        self.write("wt-snap/tmp/review-input/PLAN.md", PLAN_NEW)
        self.write("wt-snap/tmp/review-input/plan-appendix/matrix.md", MATRIX_NEW)
        for matrix in ("tmp/review-input/plan-appendix/matrix.md", "tmp/review-input/PLAN.md"):
            with self.subTest(matrix=matrix):
                res = self.run_mk(self.manifest(matrix=matrix))
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
                self.assertEqual(self.field(res, "rows"), "3")
                part = excerpt_part(self.read("packet-grok.md"))
                heads = [line for line in part.splitlines() if line.startswith("### ")]
                self.assertEqual(heads, ["### app/src/a.go:L1-L22", "### app/web/b.ts:L1-L15",
                                         "### app/web/new.ts (new file in this PLAN, no code yet)"])

    def test_rule_section_is_verbatim_with_heading_and_subsections(self):
        self.run_mk(self.manifest())
        rules = self.read("packet-grok.md").split("# 規則原文", 1)[1]
        self.assertIn("### .claude/rules/x.md § 節一\n## 節一\n第一節內文 RULE-ONE\n\n### 節一子節\n子節內文\n", rules)
        self.assertNotIn("RULE-TWO", rules)

    def test_agy_packet_drops_listed_sections_and_hit_column(self):
        res = self.run_mk(self.manifest())
        agy = self.read("packet-agy.md")
        self.assertTrue(agy.startswith("你沒有工具，只分析這份 packet 的文字。"), agy[:80])
        self.assertIn("BRIEF-BODY", agy)
        self.assertNotIn("TOOLS-SECTION-BODY", agy)
        self.assertNotIn("PM-SECTION-BODY", agy)
        self.assertNotIn("HITCOLUMN-LONG-TEXT", agy)
        self.assertIn("| 規則檔 | 節 | 處置 |", agy)
        self.assertIn("RISK-BODY", agy)
        self.assertIn("BACKGROUND-BODY", agy)
        self.assertIn("RULE-ONE", agy)
        self.assertEqual(self.field(res, "agy-ladder"), "1")

    def test_agy_packets_carry_full_appendix_including_each_split_part(self):
        res = self.run_mk(self.manifest())
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        extra = "agy-split: src=app/src/\nagy-split: web=app/web/\n"
        res = self.run_mk(self.manifest(extra))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        for name in ("packet-agy.md", "packet-agy-src.md", "packet-agy-web.md"):
            text = self.read(name)
            for marker in ("# plan-appendix\n", "## tmp/review-input/plan-appendix/notes.md\n# 筆記\nAPPENDIX-NOTES",
                           "## tmp/review-input/plan-appendix/tests.md\n# 測試\nAPPENDIX-TESTS"):
                self.assertIn(marker, text, name)
            self.assertLess(text.index("# plan-appendix"), text.index("# 程式碼摘錄"), name)

    def test_check_writes_nothing(self):
        res = self.run_mk(self.manifest(), "--check")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(self.verdict(res).startswith("PACKET OK rows=5 "), res.stdout)
        self.assertEqual(sorted(p.name for p in self.root.glob("packet-*")), [])

    def test_out_dir_option(self):
        res = self.run_mk(self.manifest(), "--out", self.root / "outdir")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue((self.root / "outdir/packet-grok.md").is_file())


class Failures(Base):
    def test_missing_rule_section_exits_1(self):
        res = self.run_mk(self.manifest(rules=["rule: .claude/rules/x.md § 沒有這節"]))
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertTrue(self.verdict(res).startswith("PACKET FAIL"), res.stdout)
        self.assertIn("沒有這節", res.stdout)
        self.assertEqual(list(self.root.glob("packet-*")), [])

    def test_function_name_not_found_exits_1_naming_row(self):
        self.write("wt-snap/tmp/review-input/PLAN.md", PLAN_OLD.replace("| `Alpha` | 只給函式名", "| `Gamma` | 只給函式名"))
        res = self.run_mk(self.manifest())
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("Gamma", res.stdout)
        self.assertIn("app/src/a.go", res.stdout)

    def test_unreadable_file_exits_1(self):
        self.write("wt-snap/tmp/review-input/PLAN.md", PLAN_OLD.replace("`app/web/b.ts`", "`app/web/gone.ts`"))
        res = self.run_mk(self.manifest())
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("app/web/gone.ts", res.stdout)

    def test_stale_line_range_past_end_exits_1(self):
        self.write("wt-snap/tmp/review-input/PLAN.md", PLAN_OLD.replace("| :1-30 |", "| L90-L95 |"))
        res = self.run_mk(self.manifest())
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("L90", res.stdout)

    def test_no_matrix_rows_exits_1(self):
        self.write("wt-snap/tmp/review-input/PLAN.md", "# PLAN\n\n## 0 定案\nx\n")
        res = self.run_mk(self.manifest())
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)

    def test_agy_cap_import_failure_exits_2(self):
        lonely = self.root / "lonely"
        lonely.mkdir()
        shutil.copy(MK, lonely / "mkpacket.py")
        res = subprocess.run([sys.executable, str(lonely / "mkpacket.py"), str(self.manifest())],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("AGY_MAX_BYTES", res.stdout + res.stderr)


class AgyLadder(Base):
    def sizes_by_ladder(self):
        """Walk the ladder by setting the cap one byte under the previous step's size."""
        res = self.run_mk(self.manifest(), "--check", limit=10**9)
        sizes = {1: int(self.field(res, "agy"))}
        for step in (2, 3, 4):
            res = self.run_mk(self.manifest(), "--check", limit=sizes[step - 1] - 1)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(self.field(res, "agy-ladder"), str(step), res.stdout)
            sizes[step] = int(self.field(res, "agy"))
            self.assertLess(sizes[step], sizes[step - 1])
        return sizes

    def test_ladder_steps_apply_in_order(self):
        sizes = self.sizes_by_ladder()
        self.run_mk(self.manifest(), limit=sizes[1] - 1)
        agy = self.read("packet-agy.md")
        self.assertIn("ACCEPT-BODY", agy)
        self.assertIn("DIRECTION-BODY", agy)
        self.assertNotIn("BACKGROUND-BODY", agy)
        self.assertIn("### app/src/a.go:L1-L22, L40-L62", agy)

        self.run_mk(self.manifest(), limit=sizes[2] - 1)
        agy = self.read("packet-agy.md")
        self.assertNotIn("BACKGROUND-BODY", agy)
        self.assertIn("### app/src/a.go:L10-L12, L50-L52", agy)
        self.assertIn("### app/web/tiny.ts:L1-L12\n```", agy)

        res = self.run_mk(self.manifest(), limit=sizes[3] - 1)
        agy = self.read("packet-agy.md")
        self.assertIn("### app/src/a.go:L10-L12, L50-L52", agy)
        tiny = [line for line in agy.splitlines() if line.startswith("### app/web/tiny.ts")]
        self.assertEqual(len(tiny), 1, tiny)
        self.assertNotIn("// t001", agy)
        self.assertEqual(len(HEADER.findall(excerpt_part(agy))), 5)
        self.assertEqual(self.field(res, "rows"), "5")

    def test_cannot_fit_without_split_exits_2(self):
        sizes = self.sizes_by_ladder()
        res = self.run_mk(self.manifest(), limit=sizes[4] - 1)
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertTrue(self.verdict(res).startswith("PACKET FAIL"), res.stdout)
        self.assertIn("agy 裁不下：", res.stdout)
        self.assertIn("agy-split", res.stdout)

    def test_split_parts_each_fit_and_name_their_scope(self):
        sizes = self.sizes_by_ladder()
        limit = sizes[4] - 1
        extra = "agy-split: src=app/src/\nagy-split: web=app/web/\n"
        res = self.run_mk(self.manifest(extra), limit=limit)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertRegex(self.verdict(res), r" agy=src:\d+,web:\d+ agy-ladder=src:\d,web:\d$")
        self.assertFalse((self.root / "packet-agy.md").exists())
        for label, rows in (("src", 2), ("web", 3)):
            text = self.read(f"packet-agy-{label}.md")
            self.assertLessEqual(len(text.encode("utf-8")), limit)
            self.assertIn(f"本份只審 {label}；其餘由另一份材料審。", text)
            self.assertEqual(len(HEADER.findall(excerpt_part(text))), rows, label)
        self.assertNotIn("app/web/", excerpt_part(self.read("packet-agy-src.md")))


class ReviewKind(Base):
    def test_diffs_are_inlined_or_pathed_per_lane(self):
        res = self.run_mk(self.manifest(kind="review"))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertRegex(self.verdict(res), r"^PACKET OK rows=0 codex=\d+ grok=\d+ agy=\d+ agy-ladder=1$")
        codex, grok, agy = (self.read(f"packet-{n}.md") for n in ("codex", "grok", "agy"))
        for path in ("change-src.patch", "change-docs.patch", "change-tests.patch"):
            self.assertIn(path, codex)
        for body in ("SRC-DIFF-A", "DOCS-DIFF-BODY", "TESTS-DIFF-BODY"):
            self.assertNotIn(body, codex)
        self.assertIn("ROLE-BODY", codex)

        self.assertIn("# 非測試碼 diff", grok)
        self.assertIn("SRC-DIFF-A", grok)
        self.assertNotIn("DOCS-DIFF-BODY", grok)
        self.assertNotIn("TESTS-DIFF-BODY", grok)
        self.assertIn("change-docs.patch", grok)
        self.assertIn("change-tests.patch", grok)

        self.assertTrue(agy.startswith("你沒有工具"), agy[:40])
        for body in ("SRC-DIFF-A", "SRC-DIFF-B", "DOCS-DIFF-BODY", "決定一", "RISK-BODY", "NOTDO-BODY"):
            self.assertIn(body, agy)
        for body in ("TESTS-DIFF-BODY", "TOOLS-SECTION-BODY", "單一 writer 檔案矩陣"):
            self.assertNotIn(body, agy)

    def test_review_split_divides_source_diff_by_prefix(self):
        extra = "agy-split: src=app/src/\nagy-split: web=app/web/\n"
        res = self.run_mk(self.manifest(extra, kind="review"))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        src, web = self.read("packet-agy-src.md"), self.read("packet-agy-web.md")
        self.assertIn("SRC-DIFF-A", src)
        self.assertNotIn("SRC-DIFF-B", src)
        self.assertIn("SRC-DIFF-B", web)
        self.assertNotIn("SRC-DIFF-A", web)


if __name__ == "__main__":
    unittest.main()
