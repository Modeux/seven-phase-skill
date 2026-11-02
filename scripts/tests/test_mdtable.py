# -*- coding: utf-8 -*-
"""Tests for _mdtable.py: header-name column picking, id cells, table/heading walk.

Usage:
  python3 -m unittest discover -s scripts/tests -p 'test_mdtable.py' -v
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from _mdtable import id_class, id_prefix, pick_col, row_ids, tables  # noqa: E402


class MdTableTest(unittest.TestCase):
    def test_pick_col_prefers_exact_then_prefix_then_unavoided_substring(self):
        header = ["條目", "一句話", "來源", "嚴重度（宣稱→裁決）", "裁決", "處置"]
        self.assertEqual(pick_col(header, "裁決", avoid=("嚴重度",)), 4)
        self.assertEqual(pick_col(header, "嚴重度"), 3)
        self.assertEqual(pick_col(["編號", "**裁決**（第 7 階段）"], "裁決"), 1)
        self.assertEqual(pick_col(["編號", "嚴重度（宣稱→裁決）"], "裁決", avoid=("嚴重度",)), None)
        self.assertEqual(pick_col(["# ", "來源 lane"], "來源"), 1)

    def test_row_ids(self):
        cases = {
            "R3": ["R3"], "R6 取景綁定不驗": ["R6"], "**R2**": ["R2"], "L2、L4、L5": ["L2", "L4", "L5"],
            "L1–L3": ["L1", "L2", "L3"], "R3.1": ["R3.1"], "R1（併 R4）": ["R1"], "R35／L25": ["R35", "L25"],
            "Rate limit": [], "F5 下游": [], "R2D2": [], "": [], "條目": [],
        }
        for cell, want in cases.items():
            self.assertEqual(row_ids(cell), want, cell)

    def test_package_tagged_ids_parse_and_keep_their_class(self):
        # A second package in one REVIEW tags its ids P2R1／P2L3; before this they parsed to [] and the round vanished.
        cases = {
            "P2R1": ["P2R1"], "**P2L38**（低）": ["P2L38"], "P2L1–P2L3": ["P2L1", "P2L2", "P2L3"],
            "P2R3、P2R12": ["P2R3", "P2R12"], "N1": ["N1"], "NL3": ["NL3"], "P2": ["P2"], "X1": ["X1"],
        }
        for cell, want in cases.items():
            self.assertEqual(row_ids(cell, any_prefix=True), want, cell)
        self.assertEqual(row_ids("P2R1"), [], "plain R/L tables still ignore tagged ids")
        for rid, prefix, cls in [("P2R1", "P2R", "R"), ("P2L3", "P2L", "L"), ("NL3", "NL", "NL"), ("R3", "R", "R"),
                                 ("P2", "P", "P")]:
            self.assertEqual((id_prefix(rid), id_class(rid)), (prefix, cls), rid)

    def test_tables_carry_heading_path_and_drop_separators(self):
        text = "# A\n## B\n| x | y |\n|---|---|\n| 1 | 2 |\n\n### C\n| p |\n|:-:|\n| q |\n## D\ntext\n"
        ts = tables(text)
        self.assertEqual([[h for _, h in t.headings] for t in ts], [["A", "B"], ["A", "B", "C"]])
        self.assertEqual(ts[0].header, ["x", "y"])
        self.assertEqual([c for _, c in ts[0].rows], [["1", "2"]])
        self.assertEqual([c for _, c in ts[1].rows], [["q"]])


if __name__ == "__main__":
    unittest.main()
