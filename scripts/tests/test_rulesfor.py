# -*- coding: utf-8 -*-
"""rulesfor: which rule files a matrix hits, and the PLAN gate over them.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_rulesfor.py' -v
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import rulesfor  # noqa: E402


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class RulesForTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        write(self.root, "CLAUDE.md", "# root\n")
        write(self.root, "web/CLAUDE.md", "# web\n## Build\n")
        write(self.root, ".claude/rules/fe/a11y.md",
              '---\npaths:\n  - "web/**/*.html"\n  - "web/**/*.spec.ts"\n---\n# a11y\n## Focus\n## Live region\n')
        write(self.root, ".claude/rules/go/worker.md",
              '---\npaths:\n  - "srv/worker/*_turn.go"\n---\n# worker\n## Turn\n')
        write(self.root, ".claude/rules/common/always.md", "# no paths\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_star_does_not_cross_directories_but_double_star_does(self):
        got = rulesfor.hits(self.root, ["web/a/b/x.html", "srv/worker/sub/j_turn.go"])
        self.assertIn(".claude/rules/fe/a11y.md", got)
        self.assertNotIn(".claude/rules/go/worker.md", got)

    def test_double_star_slash_matches_zero_directories(self):
        self.assertIn(".claude/rules/fe/a11y.md", rulesfor.hits(self.root, ["web/x.spec.ts"]))

    def test_ts_source_alone_misses_the_html_and_spec_scoped_rule(self):
        # WHY: the gap this script closes — planning reads x.ts, a11y.md only loads on .html/.spec.ts.
        self.assertNotIn(".claude/rules/fe/a11y.md", rulesfor.hits(self.root, ["web/x.ts"]))

    def test_subdirectory_claude_md_listed_root_and_pathless_rules_skipped(self):
        got = rulesfor.hits(self.root, ["web/x.html"])
        self.assertIn("web/CLAUDE.md", got)
        self.assertNotIn("CLAUDE.md", got)
        self.assertNotIn(".claude/rules/common/always.md", got)

    def _plan(self, rows):
        body = rulesfor.skeleton(self.root, ["web/x.html", "srv/worker/j_turn.go"]).splitlines()
        head, table = body[:4], body[4:]
        filled = []
        for line in table:
            name = line.split("|")[1].strip().strip("`")
            filled.append(line[:-3] + "| " + rows[name] + " |" if name in rows else None)
        plan = self.root / "PLAN.md"
        plan.write_text("# PLAN\n" + "\n".join(head + [r for r in filled if r]) + "\n## 下一節\n", encoding="utf-8")
        return plan

    def test_check_passes_when_every_hit_has_a_disposition(self):
        plan = self._plan({".claude/rules/fe/a11y.md": "套用：§Focus → 片 B 測 3",
                           ".claude/rules/go/worker.md": "不相關：只改 log 字串",
                           "web/CLAUDE.md": "不相關：不碰 build"})
        self.assertEqual(rulesfor.check(self.root, plan), [])

    def test_check_flags_missing_row_and_blank_disposition(self):
        plan = self._plan({".claude/rules/fe/a11y.md": "", "web/CLAUDE.md": "不相關：不碰 build"})
        errs = rulesfor.check(self.root, plan)
        self.assertEqual(len(errs), 2)
        self.assertTrue(any("缺列" in e and "worker.md" in e for e in errs))
        self.assertTrue(any("a11y.md" in e and "開頭" in e for e in errs))

    def test_check_fails_without_section_or_marker(self):
        plan = self.root / "PLAN.md"
        plan.write_text("# PLAN\n", encoding="utf-8")
        self.assertEqual(len(rulesfor.check(self.root, plan)), 1)
        plan.write_text("# PLAN\n## 規則對照\n| 規則檔 | 處置 |\n|---|---|\n", encoding="utf-8")
        self.assertIn("註記", rulesfor.check(self.root, plan)[0])


if __name__ == "__main__":
    unittest.main()
