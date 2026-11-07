# -*- coding: utf-8 -*-
"""planview.py: dashboard counts, the SPEC／PLAN diagram gate, and the embedded-markdown page.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_planview.py' -v
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import planview  # noqa: E402

SPEC = """# x SPEC

## 方向決議
| 題 | 決議 | 理由 |
|---|---|---|
| 1 | A | a |
| 2 | B | b |

## 假設表
| 假設 | 出處 | 錯了用戶看到什麼 |
|---|---|---|
| h | 無 | x |
"""

PLAN = """# x PLAN

## §0 定案
| 編號 | 決議 | 理由 |
|---|---|---|
| D1 | a | r |
| D2 | b | r |
| D3 | c | r |

## 切片
```mermaid
flowchart LR
  A --> B
```
| 片 | wave |
|---|---|
| A | 1 |
| B | 2 |

## 測試
| 編號 | 測什麼 |
|---|---|
| T1 | x |
| H1-3 | y |

```
## 閘
| 不是表格 | 在 code fence 裡 |
```

## PLAN 審查地圖
| 編號 | 宣稱嚴重度 | 一句話 | 位置 | 來源 | 處置 |
|---|---|---|---|---|---|
| R1 | HIGH | a | f | codex#1 | 改 PLAN（§0；測試 T1） |
| R2 | HIGH | b | f | grok#2 | 交 owner：要推翻 D1 |
| L1 | LOW | c | f | agy#3 | |
"""


class CountsTest(unittest.TestCase):
    def test_rows_under_heading_keyword(self):
        self.assertEqual(planview.rows_under(SPEC, "決議"), 2)
        self.assertEqual(planview.rows_under(SPEC, "假設"), 1)
        self.assertEqual(planview.rows_under(PLAN, "定案"), 3)
        self.assertEqual(planview.rows_under(PLAN, "測試"), 2)

    def test_table_inside_code_fence_is_not_counted(self):
        self.assertEqual(planview.rows_under(PLAN, "閘"), 0)

    def test_owner_items_are_empty_or_handed_over(self):
        self.assertEqual(planview.owner_items(PLAN), 2)

    def test_summary_links_chip_to_section_index(self):
        s = {label: (n, idx) for label, n, idx in planview.summary("PLAN", PLAN)}
        self.assertEqual(s["定案"], (3, 1))
        self.assertEqual(s["切片"], (2, 2))
        self.assertEqual(s["交 owner"], (2, 4))
        self.assertEqual(s["圖"], (1, None))


class DiagramGateTest(unittest.TestCase):
    def _run(self, spec, plan):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            (p / "SPEC.md").write_text(spec, encoding="utf-8")
            (p / "PLAN.md").write_text(plan, encoding="utf-8")
            return planview.check(p)

    def test_no_diagram_and_no_declaration_fails_per_doc(self):
        bad = self._run(SPEC, PLAN)
        self.assertEqual(len(bad), 1)
        self.assertTrue(bad[0].startswith("SPEC.md"))

    def test_mermaid_block_passes(self):
        self.assertEqual(self._run(SPEC + "\n```mermaid\nstateDiagram-v2\n a --> b\n```\n", PLAN), [])

    def test_declared_reason_passes_but_empty_reason_does_not(self):
        self.assertEqual(self._run(SPEC + "\n無圖：純文案改動，沒有狀態或時序。\n", PLAN), [])
        self.assertEqual(self._run(SPEC + "\n- **無圖**：純文案改動。\n", PLAN), [])
        self.assertEqual(len(self._run(SPEC + "\n無圖：\n", PLAN)), 1)

    def test_declaration_inside_code_fence_does_not_count(self):
        self.assertEqual(len(self._run(SPEC + "\n```\n無圖：在範例裡\n```\n", PLAN)), 1)

    def test_missing_plan_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            (p / "SPEC.md").write_text(SPEC + "\n無圖：x\n", encoding="utf-8")
            self.assertEqual(planview.check(p), [])


class PageTest(unittest.TestCase):
    def test_markdown_embedded_verbatim_with_script_end_split(self):
        md = PLAN + "\n<script>alert(1)</script>\n"
        page = planview.render("x", {"SPEC": SPEC, "PLAN": md}, "11-07 10:00")
        self.assertIn('"PLAN": "# x PLAN', page)
        self.assertNotIn("</script>\\n", page)  # the literal end tag never appears inside the JSON payload
        self.assertIn("<\\/script>", page)
        self.assertEqual(page.count("</script>"), 4)  # marked, mermaid, DOCS, page script
        self.assertIn('href="#PLAN-4">交 owner 2', page)
        self.assertIn('class="chip warn" href="#doc-SPEC">圖 0', page)

    def test_progress_frontmatter_becomes_chips_not_a_setext_heading(self):
        prog = "---\nstatus: deploy\nnext: 等部署驗收\n---\n# x PROGRESS\n\n## 下一步\n等\n"
        page = planview.render("x", {"PROGRESS": prog}, "11-07 10:00")
        self.assertIn('"PROGRESS": "# x PROGRESS', page)
        self.assertNotIn("status: deploy", page)
        self.assertIn('class="chip ok" href="#doc-PROGRESS" title="等部署驗收">status deploy', page)
        self.assertIn("next：等部署驗收", page)

    def test_main_writes_under_tmp_progress_and_reports_gate(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "t"
            p.mkdir()
            (p / "SPEC.md").write_text(SPEC, encoding="utf-8")
            (p / "PLAN.md").write_text(PLAN, encoding="utf-8")
            out = pathlib.Path(d) / "page.html"
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = planview.main([str(p), "--out", str(out)])
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())
            text = buf.getvalue()
            self.assertIn("SPEC 決議 2、假設 1、圖 0", text)
            self.assertIn("PLAN 定案 3、切片 2、測試 2、審查地圖 3、交 owner 2、圖 1", text)
            self.assertIn("圖閘未過 — SPEC.md", text)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(planview.main([str(p), "--check"]), 1)


if __name__ == "__main__":
    unittest.main()
