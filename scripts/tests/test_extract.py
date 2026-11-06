# -*- coding: utf-8 -*-
"""extract: the CLI's exact bytes, and the importable selector API mkbrief reuses.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_extract.py' -v
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import extract  # noqa: E402

DOC = """# PLAN
intro

## S1 character
s1 body

### S1 detail
detail body

## 切片 A
a body

## 切片 B
b body
"""


class ExtractBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "PLAN.md"
        self.path.write_text(DOC, encoding="utf-8")
        self.lines = DOC.splitlines()
        self.heads = extract.headings(self.lines)

    def tearDown(self):
        self.tmp.cleanup()


class CliTest(ExtractBase):
    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "extract.py"), str(self.path), *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_heading_selector_prints_tag_and_verbatim_section(self):
        got = self.cli("S1 character")
        self.assertEqual(got.returncode, 0)
        self.assertEqual(got.stdout,
                         "\n## S1 character（逐字摘自 `{}` L4–L9）\n\n"
                         "## S1 character\ns1 body\n\n### S1 detail\ndetail body\n".format(self.path))

    def test_line_range_and_bare_line_forms_print_the_same_tag_shape(self):
        self.assertEqual(self.cli("L5-L5").stdout, "\n## L5–L5（逐字摘自 `{}` L5–L5）\n\ns1 body\n".format(self.path))
        self.assertEqual(self.cli("L5").stdout, self.cli("5").stdout)
        self.assertEqual(self.cli("L5").stdout, self.cli("L5-L5").stdout)

    def test_first_takes_the_first_of_several_matches(self):
        self.assertIn("a body", self.cli("切片", "--first").stdout)
        self.assertNotIn("b body", self.cli("切片", "--first").stdout)

    def test_zero_and_several_matches_exit_with_the_found_list(self):
        none = self.cli("nope")
        self.assertEqual(none.returncode, 1)
        self.assertEqual(none.stdout, "")
        self.assertIn("matched 0 headings", none.stderr)
        many = self.cli("切片")
        self.assertEqual(many.returncode, 1)
        self.assertIn("matched 2 headings", many.stderr)
        self.assertIn("L10 切片 A; L13 切片 B", many.stderr)

    def test_list_prints_headings_with_line_numbers(self):
        self.assertEqual(self.cli("--list").stdout.splitlines()[1], "L4     ## S1 character")


class SelectorApiTest(ExtractBase):
    def test_parse_range_accepts_the_cli_forms_and_rejects_heading_text(self):
        self.assertEqual(extract.parse_range("L94"), (93, 94))
        self.assertEqual(extract.parse_range("94"), (93, 94))
        self.assertEqual(extract.parse_range("L120-L180"), (119, 180))
        self.assertEqual(extract.parse_range("120-180"), (119, 180))
        self.assertIsNone(extract.parse_range("S1 character"))
        self.assertIsNone(extract.parse_range("3.1"))

    def test_resolve_heading_returns_the_section_span_and_label(self):
        self.assertEqual(extract.resolve_heading(self.lines, self.heads, "S1 detail"), (6, 9, "S1 detail"))

    def test_resolve_heading_zero_match_raises_with_the_selector(self):
        with self.assertRaises(extract.SelectorError) as cm:
            extract.resolve_heading(self.lines, self.heads, "nope")
        self.assertIn("matched 0 headings", str(cm.exception))
        self.assertIn("nope", str(cm.exception))

    def test_resolve_heading_several_matches_needs_first(self):
        with self.assertRaises(extract.SelectorError) as cm:
            extract.resolve_heading(self.lines, self.heads, "切片")
        self.assertIn("L10 切片 A; L13 切片 B", str(cm.exception))
        self.assertEqual(extract.resolve_heading(self.lines, self.heads, "切片", first=True)[2], "切片 A")

    def test_render_level_only_changes_the_tag_heading(self):
        two = extract.render(self.lines, 4, 5, "L5–L5", "PLAN.md")
        three = extract.render(self.lines, 4, 5, "L5–L5", "PLAN.md", level=3)
        self.assertEqual(two, "\n## L5–L5（逐字摘自 `PLAN.md` L5–L5）\n\ns1 body\n")
        self.assertEqual(three, "\n### L5–L5（逐字摘自 `PLAN.md` L5–L5）\n\ns1 body\n")


class CliRangeBoundsTest(ExtractBase):
    def cli(self, *selectors):
        return subprocess.run([sys.executable, str(SCRIPTS / "extract.py"), str(self.path), *selectors],
                              capture_output=True, text=True, encoding="utf-8")

    def test_a_line_past_the_end_fails_instead_of_printing_an_empty_section(self):
        p = self.cli("L99")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")
        self.assertIn("L99", p.stderr)

    def test_a_range_running_past_the_end_fails_instead_of_mislabelling_the_excerpt(self):
        p = self.cli("L1-L200")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")

    def test_a_reversed_range_fails(self):
        self.assertNotEqual(self.cli("L5-L2").returncode, 0)

    def test_the_last_line_is_still_in_range(self):
        last = len(self.lines)
        p = self.cli("L{}".format(last))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(self.lines[-1], p.stdout)

    def test_a_bad_selector_later_in_the_list_prints_nothing_for_the_good_ones(self):
        p = self.cli("L1", "L99")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "")


if __name__ == "__main__":
    unittest.main()
