# -*- coding: utf-8 -*-
"""mkbrief: manifest -> per-slice briefs, all-or-nothing, with a --check that catches stale briefs.

Usage: python3 -m unittest discover -s scripts/tests -p 'test_mkbrief.py' -v
"""
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
MK = SCRIPTS / "mkbrief.py"

PLAN = """# PLAN
intro

## S1 character
s1 body

### S1 detail
detail body

## S2 frontend
s2 body

## 切片 A
a body

## 切片 B
b body
"""

SPEC = "# SPEC\nline two\nline three\nline four\n"
PREAMBLE = "root: {root}\nout: tmp/s/impl\nalias: P=plans/PLAN.md\nalias: S=plans/SPEC.md\nshared: tmp/s/shared.md\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.write("plans/PLAN.md", PLAN)
        self.write("plans/SPEC.md", SPEC)
        self.write("tmp/s/shared.md", "shared body must not be inlined\n")
        self.write("tmp/s/S1-head.md", "S1 hand-written opening\n")
        self.write("web/CLAUDE.md", "# web\n")
        self.write("web/app/CLAUDE.md", "# app\n")
        self.write(".claude/rules/fe/a11y.md", '---\npaths:\n  - "web/**/*.html"\n---\n# a11y\n')
        self.write(".claude/rules/go/worker.md", '---\npaths:\n  - "srv/**/*.go"\n---\n# worker\n')
        self.write(".claude/rules/common/always.md", "# no paths\n")
        self.manifest = self.root / "tmp/s/m.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def mk(self, body, preamble=PREAMBLE):
        self.manifest.write_text(preamble.format(root=self.root) + body, encoding="utf-8")

    def run_mk(self, *args, cwd=None):
        got = subprocess.run([sys.executable, str(MK), str(self.manifest), *args], capture_output=True, text=True,
                             encoding="utf-8", cwd=cwd)
        got.all = got.stdout + got.stderr
        return got

    def brief(self, sid="S1"):
        return self.root / "tmp/s/impl" / "{}-brief.md".format(sid)

    def text(self, sid="S1"):
        return self.brief(sid).read_text(encoding="utf-8")

    def line_of(self, needle):
        for n, line in enumerate(self.manifest.read_text(encoding="utf-8").split("\n"), 1):
            if needle in line:
                return n
        self.fail("{!r} not in manifest".format(needle))


class HappyPathTest(Base):
    def test_sections_appear_in_the_contract_order(self):
        self.mk('== S1\nhead: tmp/s/S1-head.md\nextract: P "S1 character"\nfiles: web/x.html\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)
        t = self.text()
        order = [t.index(s) for s in ("S1 hand-written opening", "## 共用任務書", "## 必讀摘錄",
                                      "## 會自動載入的規則", "## 需要時再讀")]
        self.assertEqual(order, sorted(order))
        self.assertTrue(t.startswith("S1 hand-written opening"))

    def test_shared_is_listed_by_absolute_path_with_read_first_and_never_inlined(self):
        self.mk('== S1\nextract: S L2\n')
        self.run_mk()
        t = self.text()
        self.assertIn("`{}`".format(self.root / "tmp/s/shared.md"), t)
        self.assertIn("先讀", t)
        self.assertNotIn("shared body must not be inlined", t)

    def test_excerpt_is_verbatim_with_its_source_tagged(self):
        self.mk('== S1\nextract: P "S1 character"\nextract: S L2-L3\n')
        self.run_mk()
        t = self.text()
        self.assertIn("### S1 character（逐字摘自 `plans/PLAN.md` L4–L9）\n\n"
                      "## S1 character\ns1 body\n\n### S1 detail\ndetail body\n", t)
        self.assertIn("### L2–L3（逐字摘自 `plans/SPEC.md` L2–L3）\n\nline two\nline three\n", t)

    def test_several_selectors_on_one_line_keep_their_order(self):
        self.mk('== S1\nextract: P L11 "切片 B" L2\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)
        t = self.text()
        self.assertLess(t.index("s2 body"), t.index("b body"))
        self.assertLess(t.index("b body"), t.index("\nintro\n"))

    def test_first_flag_resolves_multiple_heading_matches(self):
        self.mk('== S1\nextract: P 切片 --first\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)
        self.assertIn("a body", self.text())
        self.assertNotIn("b body", self.text())

    def test_first_on_a_line_selector_is_accepted(self):
        self.mk('== S1\nextract: P L5 --first\n')
        self.assertEqual(self.run_mk().returncode, 0)
        self.assertIn("s1 body", self.text())

    def test_quoted_digits_are_a_heading_not_a_line_number(self):
        self.write("plans/N.md", "first\nsecond\n\n# 2\nunder two\n")
        self.mk('== S1\nextract: plans/N.md "2"\n')
        self.run_mk()
        self.assertIn("under two", self.text())
        self.assertNotIn("\nsecond\n", self.text())

    def test_all_flag_takes_the_whole_file(self):
        self.write("tmp/s/facts.md", "fact one\nfact two\n")
        self.mk('== S1\nextract: tmp/s/facts.md --all\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)
        self.assertIn("（逐字摘自 `tmp/s/facts.md` L1–L2）\n\nfact one\nfact two\n", self.text())

    def test_note_lines_stay_between_the_excerpts_they_introduce(self):
        self.mk('== S1\nextract: S L2\nnote: 衝突時以 SPEC 為準\nextract: S L4\n')
        self.run_mk()
        t = self.text()
        self.assertLess(t.index("line two"), t.index("衝突時以 SPEC 為準"))
        self.assertLess(t.index("衝突時以 SPEC 為準"), t.index("line four"))

    def test_preamble_head_and_ondemand_apply_to_every_slice_before_its_own(self):
        self.write("tmp/s/common.md", "COMMON OPENING\n")
        self.write("tmp/s/deep.md", "x\n")
        self.write("tmp/s/own.md", "x\n")
        self.mk('head: tmp/s/common.md\nondemand: tmp/s/deep.md\n'
                '== S1\nhead: tmp/s/S1-head.md\nextract: S L2\nondemand: tmp/s/own.md\n'
                '== S2\nextract: S L3\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)
        one = self.text("S1")
        self.assertLess(one.index("COMMON OPENING"), one.index("S1 hand-written opening"))
        self.assertLess(one.index("tmp/s/deep.md"), one.index("tmp/s/own.md"))
        self.assertIn("COMMON OPENING", self.text("S2"))
        self.assertNotIn("S1 hand-written", self.text("S2"))
        self.assertNotIn("tmp/s/own.md", self.text("S2"))

    def test_rule_list_matches_the_rulesfor_cli_for_the_same_paths(self):
        paths = ["web/app/x.html", "srv/w/y.go"]
        self.mk('== S1\nextract: S L2\nfiles: {}\n'.format(" ".join(paths)))
        self.run_mk()
        section = self.text().split("## 會自動載入的規則")[1].split("## ")[0]
        listed = re.findall(r"^- `([^`]+)`", section, re.M)
        cli = subprocess.run([sys.executable, str(SCRIPTS / "rulesfor.py"), *paths, "--root", str(self.root)],
                             capture_output=True, text=True, encoding="utf-8", check=True).stdout
        self.assertEqual(listed, re.findall(r"^\| `([^`]+)`", cli, re.M))
        self.assertEqual(sorted(listed), [".claude/rules/fe/a11y.md", ".claude/rules/go/worker.md",
                                          "web/CLAUDE.md", "web/app/CLAUDE.md"])
        self.assertIn("不要再 Read", section)

    def test_repeated_files_lines_accumulate(self):
        self.mk('== S1\nextract: S L2\nfiles: web/x.html\nfiles: srv/y.go\n')
        self.run_mk()
        self.assertIn(".claude/rules/fe/a11y.md", self.text())
        self.assertIn(".claude/rules/go/worker.md", self.text())

    def test_absolute_file_inside_root_counts_as_its_relative_path(self):
        self.mk('== S1\nextract: S L2\nfiles: {}/web/x.html\n'.format(self.root))
        self.assertEqual(self.run_mk().returncode, 0)
        self.assertIn(".claude/rules/fe/a11y.md", self.text())

    def test_new_file_paths_do_not_need_to_exist(self):
        self.mk('== S1\nextract: S L2\nfiles: web/new/not-yet.html\n')
        self.assertEqual(self.run_mk().returncode, 0)
        self.assertIn(".claude/rules/fe/a11y.md", self.text())

    def test_on_demand_lists_aliases_direct_sources_and_ondemand_once_each_with_notes(self):
        self.write("tmp/s/deep.md", "x\n")
        self.write("tmp/s/direct.md", "direct line\n")
        self.mk('== S1\nextract: S L2\nextract: tmp/s/direct.md L1\n'
                'ondemand: tmp/s/deep.md （§7 風險回滾）\nondemand: P （全部 D-M）\nondemand: plans/SPEC.md\n')
        self.run_mk()
        section = self.text().split("## 需要時再讀")[1]
        listed = re.findall(r"^- `([^`]+)`(.*)$", section, re.M)
        self.assertEqual([p for p, _ in listed], [str(self.root / p) for p in (
            "plans/PLAN.md", "plans/SPEC.md", "tmp/s/direct.md", "tmp/s/deep.md")])
        self.assertIn("全部 D-M", listed[0][1])
        self.assertIn("§7 風險回滾", listed[3][1])

    def test_ondemand_directory_is_allowed(self):
        self.write("tmp/s/shots/a.png", "x")
        self.mk('== S1\nextract: S L2\nondemand: tmp/s/shots\n')
        self.assertEqual(self.run_mk().returncode, 0)

    def test_paths_resolve_against_root_not_the_cwd(self):
        self.mk('== S1\nhead: tmp/s/S1-head.md\nextract: P L5\n')
        got = self.run_mk(cwd=str(SCRIPTS))
        self.assertEqual(got.returncode, 0, got.all)
        self.assertTrue(self.brief().is_file())

    def test_stdout_is_conclusion_then_one_line_per_id_with_size_and_path(self):
        self.mk('== S1\nextract: S L2\n== S2\nextract: S L3\n')
        lines = self.run_mk().stdout.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("OK", lines[0])
        self.assertRegex(lines[1], r"^S1  \d+\.\dKB  " + re.escape(str(self.brief("S1"))) + "$")
        self.assertRegex(lines[2], r"^S2  \d+\.\dKB  " + re.escape(str(self.brief("S2"))) + "$")

    def test_slice_without_head_or_shared_or_files_omits_those_sections(self):
        self.mk('== S1\nextract: S L2\n', preamble="root: {root}\nout: tmp/s/impl\nalias: S=plans/SPEC.md\n")
        self.assertEqual(self.run_mk().returncode, 0)
        t = self.text()
        self.assertTrue(t.startswith("## 必讀摘錄"))
        for gone in ("## 共用任務書", "## 會自動載入的規則"):
            self.assertNotIn(gone, t)

    def test_comments_and_blank_lines_are_ignored(self):
        self.mk('# a comment\n\n== S1\n# another: comment\nextract: S L2\n')
        self.assertEqual(self.run_mk().returncode, 0)

    def test_regeneration_is_byte_identical(self):
        self.mk('== S1\nhead: tmp/s/S1-head.md\nextract: P "S1 character"\nfiles: web/x.html\n')
        self.run_mk()
        first = self.brief().read_bytes()
        self.run_mk()
        self.assertEqual(self.brief().read_bytes(), first)


class ContentFailureTest(Base):
    def assert_fails_at(self, needle, *messages):
        got = self.run_mk()
        self.assertEqual(got.returncode, 1, got.all)
        self.assertIn("manifest L{}".format(self.line_of(needle)), got.all)
        for m in messages:
            self.assertIn(m, got.all)
        self.assertFalse(list((self.root / "tmp/s/impl").glob("*")) if (self.root / "tmp/s/impl").exists() else [])

    def test_selector_with_zero_matches_fails_with_line_number(self):
        self.mk('== S1\nextract: P "S1 character"\n== S2\nextract: P "no such heading"\n')
        self.assert_fails_at("no such heading", "matched 0", "S2")

    def test_selector_with_several_matches_without_first_fails_and_lists_them(self):
        self.mk('== S1\nextract: P 切片\n')
        self.assert_fails_at("extract: P 切片", "matched 2", "切片 A", "切片 B")

    def test_undefined_alias_fails_with_line_number(self):
        self.mk('== S1\nextract: Q L1\n')
        self.assert_fails_at("extract: Q", "alias", "Q")

    def test_missing_extract_source_fails(self):
        self.mk('== S1\nextract: plans/GONE.md L1\n')
        self.assert_fails_at("GONE.md", "plans/GONE.md")

    def test_extract_source_that_is_a_directory_fails(self):
        self.mk('== S1\nextract: plans L1\n')
        self.assert_fails_at("extract: plans", "plans")

    def test_missing_head_shared_ondemand_and_alias_target_each_fail(self):
        for body, needle, preamble in (
                ('== S1\nhead: tmp/s/GONE.md\nextract: S L1\n', "GONE.md", PREAMBLE),
                ('== S1\nextract: S L1\nondemand: tmp/s/GONE.md\n', "GONE.md", PREAMBLE),
                ('== S1\nextract: S L1\n', "GONE.md", PREAMBLE + "shared: tmp/s/GONE.md\n"),
                ('== S1\nextract: S L1\n', "GONE.md", PREAMBLE + "alias: G=plans/GONE.md\n")):
            with self.subTest(body=body, preamble=preamble[-30:]):
                self.mk(body, preamble=preamble.replace("shared: tmp/s/shared.md\nshared:", "shared:"))
                self.assert_fails_at(needle, "GONE.md")

    def test_line_range_past_the_end_fails_instead_of_an_empty_excerpt(self):
        self.mk('== S1\nextract: S L99\n')
        self.assert_fails_at("extract: S L99", "共 4 行")

    def test_line_range_ending_past_the_end_fails_instead_of_a_lying_tag(self):
        self.mk('== S1\nextract: S L2-L200\n')
        self.assert_fails_at("L2-L200", "共 4 行")

    def test_reversed_and_zero_ranges_fail(self):
        for sel in ("L3-L2", "L0"):
            with self.subTest(sel=sel):
                self.mk('== S1\nextract: S {}\n'.format(sel))
                self.assert_fails_at(sel)

    def test_range_on_blank_lines_only_fails(self):
        self.write("plans/B.md", "a\n\nb\n")
        self.mk('== S1\nextract: plans/B.md L2\n')
        self.assert_fails_at("B.md L2", "空")

    def test_empty_head_file_fails(self):
        self.write("tmp/s/empty.md", "\n \n")
        self.mk('== S1\nhead: tmp/s/empty.md\nextract: S L1\n')
        self.assert_fails_at("empty.md", "空")

    def test_slice_with_no_required_reading_fails(self):
        self.mk('== S1\nfiles: web/x.html\n')
        self.assert_fails_at("== S1", "S1")

    def test_files_outside_root_globs_and_commas_fail(self):
        for bad in ("/etc/passwd", "../up.go", "web/*.html", "web/a.html,"):
            with self.subTest(bad=bad):
                self.mk('== S1\nextract: S L1\nfiles: {}\n'.format(bad))
                self.assert_fails_at("files:", bad)

    def test_files_without_a_rules_directory_fails_rather_than_listing_nothing(self):
        shutil.rmtree(self.root / ".claude")
        self.mk('== S1\nextract: S L1\nfiles: web/x.html\n')
        self.assert_fails_at("files:", ".claude/rules")

    def test_every_error_is_reported_not_just_the_first(self):
        self.mk('== S1\nextract: P "nope one"\n== S2\nextract: P "nope two"\nhead: tmp/s/GONE.md\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 1)
        for needle in ("nope one", "nope two", "GONE.md"):
            self.assertIn("manifest L{}".format(self.line_of(needle)), got.all)
            self.assertIn(needle, got.all)


class AllOrNothingTest(Base):
    def test_failure_in_a_later_slice_leaves_no_brief_from_earlier_slices(self):
        self.mk('== S1\nextract: S L1\n== S2\nextract: P "nope"\n')
        self.assertEqual(self.run_mk().returncode, 1)
        self.assertFalse(self.brief("S1").exists())

    def test_failure_leaves_a_previous_good_brief_byte_identical(self):
        self.mk('== S1\nextract: S L1\n')
        self.assertEqual(self.run_mk().returncode, 0)
        before = self.brief().read_bytes()
        self.mk('== S1\nextract: S L2\n== S2\nextract: P "nope"\n')
        self.assertEqual(self.run_mk().returncode, 1)
        self.assertEqual(self.brief().read_bytes(), before)

    def test_target_that_cannot_be_replaced_fails_before_any_brief_moves(self):
        self.brief("S2").mkdir(parents=True)
        self.mk('== S1\nextract: S L1\n== S2\nextract: S L2\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 2, got.all)
        self.assertFalse(self.brief("S1").exists())
        self.assertEqual(sorted(p.name for p in (self.root / "tmp/s/impl").iterdir()), ["S2-brief.md"])

    def test_output_directory_blocked_by_a_file_is_a_tool_error(self):
        self.write("tmp/s/blocked", "file\n")
        self.mk('== S1\nextract: S L1\n',
                preamble="root: {root}\nout: tmp/s/blocked/impl\n" + "alias: S=plans/SPEC.md\n")
        got = self.run_mk()
        self.assertEqual(got.returncode, 2, got.all)


class ToolErrorTest(Base):
    def assert_exit2(self, needle=None):
        got = self.run_mk()
        self.assertEqual(got.returncode, 2, got.all)
        if needle:
            self.assertIn("manifest L{}".format(self.line_of(needle)), got.all)
        self.assertFalse((self.root / "tmp/s/impl").exists())

    def test_unknown_directive_names_its_line(self):
        self.mk('== S1\nextract: S L1\nextrakt: S L2\n')
        self.assert_exit2("extrakt:")

    def test_line_without_a_directive_names_its_line(self):
        self.mk('== S1\nextract: S L1\nfree text here\n')
        self.assert_exit2("free text here")

    def test_directives_in_the_wrong_scope_name_their_line(self):
        for body, needle in (('extract: S L1\n== S1\nextract: S L2\n', "extract: S L1"),
                             ('files: a.go\n== S1\nextract: S L2\n', "files: a.go"),
                             ('note: x y\n== S1\nextract: S L2\n', "note: x y"),
                             ('over-90kb: why\n== S1\nextract: S L2\n', "over-90kb"),
                             ('== S1\nextract: S L2\nalias: Z=plans/PLAN.md\n', "alias: Z"),
                             ('== S1\nextract: S L2\nroot: /x\n', "root: /x"),
                             ('== S1\nextract: S L2\nout: y\n', "out: y")):
            with self.subTest(needle=needle):
                self.mk(body)
                self.assert_exit2(needle)

    def test_duplicate_id_alias_root_and_bad_id_are_rejected(self):
        self.mk('== S1\nextract: S L1\n== S1\nextract: S L2\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 2, got.all)
        self.assertIn("manifest L{}".format(self.line_of("extract: S L2") - 1), got.all)
        self.mk('alias: P=plans/SPEC.md\n== S1\nextract: S L1\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 2, got.all)
        self.mk('out: other\n== S1\nextract: S L1\n')
        self.assertEqual(self.run_mk().returncode, 2)
        self.mk('== a/b\nextract: S L1\n')
        self.assert_exit2("== a/b")

    def test_unquoted_multiword_heading_is_rejected_not_read_as_two_selectors(self):
        self.mk('== S1\nextract: P S1 character\n')
        self.assert_exit2("extract: P S1")

    def test_unterminated_and_empty_quotes_are_rejected(self):
        for bad in ('extract: P "S1 character', 'extract: P ""'):
            with self.subTest(bad=bad):
                self.mk('== S1\n{}\n'.format(bad))
                self.assert_exit2(bad)

    def test_apostrophe_and_backslash_in_a_heading_need_no_escaping(self):
        self.write("plans/Q.md", "# owner's C:\\dir pick\nbody here\n")
        self.mk('== S1\nextract: plans/Q.md "owner\'s C:\\dir"\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)
        self.assertIn("body here", self.text())

    def test_extract_without_selector_and_unknown_flag_and_all_with_selector_are_rejected(self):
        for bad in ("extract: P", "extract: P L1 --firts", "extract: P L1 --all", "extract:"):
            with self.subTest(bad=bad):
                self.mk('== S1\n{}\n'.format(bad))
                self.assert_exit2(bad)

    def test_empty_over_90kb_reason_is_rejected(self):
        self.mk('== S1\nextract: S L1\nover-90kb:\n')
        self.assert_exit2("over-90kb:")

    def test_missing_root_out_and_no_slices_are_tool_errors(self):
        for preamble, body in (("out: a\n", "== S1\nextract: x L1\n"), ("root: {root}\n", "== S1\nextract: x L1\n"),
                               ("root: {root}\nout: a\n", "")):
            with self.subTest(preamble=preamble, body=body):
                self.mk(body, preamble=preamble)
                self.assertEqual(self.run_mk().returncode, 2)

    def test_relative_or_absent_root_is_a_tool_error(self):
        for root in ("relative/tree", "/no/such/tree/at/all"):
            with self.subTest(root=root):
                self.mk('== S1\nextract: x L1\n', preamble="root: {}\nout: a\n".format(root))
                got = self.run_mk()
                self.assertEqual(got.returncode, 2, got.all)
                self.assertIn(root, got.all)

    def test_manifest_file_absent_is_a_tool_error(self):
        got = self.run_mk()
        self.assertEqual(got.returncode, 2, got.all)

    def test_bad_arguments_are_a_tool_error(self):
        self.mk('== S1\nextract: S L1\n')
        self.assertEqual(self.run_mk("--nope").returncode, 2)
        self.assertEqual(self.run_mk("--check", "--list").returncode, 2)

    def test_only_with_an_unknown_id_is_a_tool_error(self):
        self.mk('== S1\nextract: S L1\n')
        got = self.run_mk("--only", "NOPE")
        self.assertEqual(got.returncode, 2, got.all)
        self.assertIn("NOPE", got.all)
        self.assertFalse(self.brief().exists())


class SizeLimitTest(Base):
    def test_exactly_90kb_of_required_reading_passes(self):
        self.write("tmp/s/big.md", "x" * 90000 + "\n")
        self.mk('== S1\nhead: tmp/s/big.md\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)
        self.assertIn("90.0KB", got.stdout)

    def test_one_byte_over_fails_naming_the_id_and_the_escape_hatch(self):
        self.write("tmp/s/big.md", "x" * 90001 + "\n")
        self.mk('== S1\nhead: tmp/s/big.md\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 1, got.all)
        self.assertIn("S1", got.all)
        self.assertIn("over-90kb", got.all)
        self.assertFalse(self.brief().exists())

    def test_head_and_excerpts_add_up_to_the_limit(self):
        self.write("tmp/s/big.md", "x" * 89000 + "\n")
        self.write("plans/L.md", "y" * 2000 + "\n")
        self.mk('== S1\nhead: tmp/s/big.md\nextract: plans/L.md L1\n')
        self.assertEqual(self.run_mk().returncode, 1)

    def test_size_counts_utf8_bytes_as_wc_c_does(self):
        self.write("tmp/s/big.md", "字" * 30001 + "\n")
        self.mk('== S1\nhead: tmp/s/big.md\n')
        self.assertEqual(self.run_mk().returncode, 1)

    def test_path_lists_do_not_count(self):
        self.write("tmp/s/big.md", "x" * 89990 + "\n")
        note = "n" * 500
        self.mk('== S1\nhead: tmp/s/big.md\nondemand: plans/SPEC.md {}\nfiles: web/x.html\n'.format(note))
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)

    def test_reason_lets_it_through_and_lands_in_the_brief_header(self):
        self.write("tmp/s/big.md", "x" * 90001 + "\n")
        self.mk('== S1\nhead: tmp/s/big.md\nover-90kb: 規格忠實度要讀 SPEC 全文\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)
        self.assertIn("規格忠實度要讀 SPEC 全文", self.text().splitlines()[0])

    def test_reason_under_the_limit_warns_and_stays_out_of_the_header(self):
        self.mk('== S1\nextract: S L1\nover-90kb: 過期的理由\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)
        self.assertNotIn("過期的理由", self.text())
        self.assertIn("over-90kb", got.all)

    def test_check_also_enforces_the_limit(self):
        self.write("tmp/s/big.md", "x" * 90001 + "\n")
        self.mk('== S1\nhead: tmp/s/big.md\n')
        self.assertEqual(self.run_mk("--check").returncode, 1)


class OverlapTest(Base):
    def test_nested_selectors_on_the_same_source_warn_with_both_lines(self):
        self.mk('== S1\nextract: P "S1 character"\nextract: P L5\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)
        self.assertIn("重疊", got.all)
        self.assertIn("manifest L{}".format(self.line_of('"S1 character"')), got.all)
        self.assertIn("manifest L{}".format(self.line_of("extract: P L5")), got.all)

    def test_overlap_on_a_single_line_warns(self):
        self.mk('== S1\nextract: S L1-L2 L2-L3\n')
        self.assertIn("重疊", self.run_mk().all)

    def test_adjacent_ranges_do_not_warn(self):
        self.mk('== S1\nextract: S L1-L2 L3-L4\n')
        self.assertNotIn("重疊", self.run_mk().all)

    def test_same_range_on_different_sources_or_slices_does_not_warn(self):
        self.mk('== S1\nextract: S L2\nextract: P L2\n== S2\nextract: S L2\n')
        self.assertNotIn("重疊", self.run_mk().all)

    def test_stdout_stays_within_30_lines_and_says_how_many_it_hid(self):
        self.mk('== S1\n' + "".join("extract: S L1 --first\n" for _ in range(12)))
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)
        self.assertLessEqual(len(got.stdout.splitlines()), 30)
        self.assertRegex(got.stdout, r"另 \d+ 條")


class CheckTest(Base):
    def generate(self):
        self.mk('== S1\nhead: tmp/s/S1-head.md\nextract: P "S1 character"\nextract: S L2\nfiles: web/x.html\n'
                '== S2\nextract: P "S2 frontend"\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)

    def test_passes_right_after_generation_and_writes_nothing(self):
        self.generate()
        stamps = {p: p.stat().st_mtime_ns for p in (self.root / "tmp/s/impl").iterdir()}
        got = self.run_mk("--check")
        self.assertEqual(got.returncode, 0, got.all)
        self.assertIn("OK", got.stdout.splitlines()[0])
        self.assertEqual({p: p.stat().st_mtime_ns for p in (self.root / "tmp/s/impl").iterdir()}, stamps)

    def test_excerpted_source_edit_marks_only_that_id_stale_and_keeps_the_old_brief(self):
        self.generate()
        before = self.brief("S2").read_bytes()
        self.write("plans/PLAN.md", PLAN.replace("s2 body", "s2 body CHANGED"))
        got = self.run_mk("--check")
        self.assertEqual(got.returncode, 1, got.all)
        self.assertRegex(got.stdout, r"(?m)^S2 .*過期")
        self.assertNotRegex(got.stdout, r"(?m)^S1 .*過期")
        self.assertEqual(self.brief("S2").read_bytes(), before)

    def test_stale_detail_names_the_manifest_line_that_changed(self):
        self.generate()
        self.write("plans/PLAN.md", PLAN.replace("s2 body", "s2 body CHANGED"))
        got = self.run_mk("--check")
        self.assertIn("manifest L{}".format(self.line_of('"S2 frontend"')), got.stdout)

    def test_line_shift_above_a_range_is_stale_and_warns_about_drift(self):
        self.generate()
        self.write("plans/SPEC.md", "# SPEC\ninserted\n" + SPEC.split("\n", 1)[1])
        got = self.run_mk("--check")
        self.assertEqual(got.returncode, 1, got.all)
        self.assertRegex(got.stdout, r"(?m)^S1 .*過期")
        self.assertIn("行號", got.stdout)

    def test_source_edit_outside_every_excerpt_is_not_stale(self):
        self.generate()
        self.write("plans/PLAN.md", PLAN.replace("b body", "b body CHANGED"))
        self.assertEqual(self.run_mk("--check").returncode, 0)

    def test_shared_body_edit_is_not_stale_because_only_its_path_is_in_the_brief(self):
        self.generate()
        self.write("tmp/s/shared.md", "shared body edited\n")
        self.assertEqual(self.run_mk("--check").returncode, 0)

    def test_head_edit_is_stale(self):
        self.generate()
        self.write("tmp/s/S1-head.md", "S1 opening rewritten\n")
        got = self.run_mk("--check")
        self.assertEqual(got.returncode, 1, got.all)
        self.assertIn("manifest L{}".format(self.line_of("head: tmp/s/S1-head.md")), got.stdout)

    def test_rule_scope_change_is_stale(self):
        self.generate()
        self.write(".claude/rules/fe/more.md", '---\npaths:\n  - "web/**/*.html"\n---\n# more\n')
        got = self.run_mk("--check")
        self.assertEqual(got.returncode, 1, got.all)
        self.assertRegex(got.stdout, r"(?m)^S1 .*過期")

    def test_manifest_edit_is_stale(self):
        self.generate()
        self.mk('== S1\nhead: tmp/s/S1-head.md\nextract: P "S1 character"\nfiles: web/x.html\n'
                '== S2\nextract: P "S2 frontend"\n')
        self.assertEqual(self.run_mk("--check").returncode, 1)

    def test_hand_edited_brief_is_stale(self):
        self.generate()
        self.brief("S2").write_text(self.text("S2") + "added by hand\n", encoding="utf-8")
        got = self.run_mk("--check")
        self.assertEqual(got.returncode, 1, got.all)
        self.assertRegex(got.stdout, r"(?m)^S2 .*過期")

    def test_missing_brief_is_not_ok(self):
        self.generate()
        self.brief("S2").unlink()
        got = self.run_mk("--check")
        self.assertEqual(got.returncode, 1, got.all)
        self.assertRegex(got.stdout, r"(?m)^S2 .*缺檔")
        self.assertFalse(self.brief("S2").exists())

    def test_heading_that_disappeared_fails_with_its_line(self):
        self.generate()
        self.write("plans/PLAN.md", PLAN.replace("## S2 frontend", "## S2 renamed"))
        got = self.run_mk("--check")
        self.assertEqual(got.returncode, 1, got.all)
        self.assertIn("manifest L{}".format(self.line_of('"S2 frontend"')), got.all)

    def test_deleted_source_and_deleted_shared_fail(self):
        self.generate()
        (self.root / "tmp/s/shared.md").unlink()
        self.assertEqual(self.run_mk("--check").returncode, 1)

    def test_only_limits_what_is_compared(self):
        self.generate()
        self.write("plans/PLAN.md", PLAN.replace("s2 body", "s2 body CHANGED"))
        self.assertEqual(self.run_mk("--check", "--only", "S1").returncode, 0)
        self.assertEqual(self.run_mk("--check", "--only", "S2").returncode, 1)

    def test_regenerating_makes_a_stale_brief_pass_again(self):
        self.generate()
        self.write("plans/PLAN.md", PLAN.replace("s2 body", "s2 body CHANGED"))
        self.assertEqual(self.run_mk().returncode, 0)
        self.assertEqual(self.run_mk("--check").returncode, 0)

    def test_shift_above_a_heading_section_is_stale_but_says_the_text_is_unchanged(self):
        self.mk('== S1\nextract: P "S2 frontend"\n')
        self.assertEqual(self.run_mk().returncode, 0)
        self.write("plans/PLAN.md", PLAN.replace("intro\n", "intro\ninserted\n"))
        got = self.run_mk("--check")
        self.assertEqual(got.returncode, 1, got.all)
        self.assertIn("內文沒變", got.stdout)
        self.assertNotIn("含行號選擇器", got.stdout)

    def test_range_whose_new_text_also_appears_elsewhere_still_warns_about_drift(self):
        self.write("plans/R.md", "alpha\nbeta\n")
        self.mk('== S1\nextract: plans/R.md L1\nextract: plans/R.md L2\n')
        self.assertEqual(self.run_mk().returncode, 0)
        self.write("plans/R.md", "beta\nalpha\n")
        got = self.run_mk("--check")
        self.assertEqual(got.returncode, 1, got.all)
        self.assertIn("含行號選擇器", got.stdout)
        self.assertNotIn("內文沒變", got.stdout)

    def test_several_changed_selectors_on_a_line_report_that_line_once(self):
        self.mk('== S1\nextract: S L2 L3\n')
        self.assertEqual(self.run_mk().returncode, 0)
        self.write("plans/SPEC.md", SPEC.replace("line two", "two!").replace("line three", "three!"))
        got = self.run_mk("--check")
        self.assertEqual(got.returncode, 1, got.all)
        self.assertEqual(got.stdout.count("manifest L{} ".format(self.line_of("extract: S L2 L3"))), 1)

    def test_tool_errors_stay_exit_2_under_check(self):
        self.mk('== S1\nextrakt: S L1\n')
        self.assertEqual(self.run_mk("--check").returncode, 2)


class OnlyAndListTest(Base):
    def test_only_writes_just_the_named_slices(self):
        self.mk('== S1\nextract: S L1\n== S2\nextract: S L2\n== S3\nextract: S L3\n')
        got = self.run_mk("--only", "S1", "S3")
        self.assertEqual(got.returncode, 0, got.all)
        self.assertTrue(self.brief("S1").exists())
        self.assertFalse(self.brief("S2").exists())
        self.assertTrue(self.brief("S3").exists())
        self.assertNotIn("S2", got.stdout)

    def test_only_can_repeat(self):
        self.mk('== S1\nextract: S L1\n== S2\nextract: S L2\n')
        self.assertEqual(self.run_mk("--only", "S1", "--only", "S2").returncode, 0)
        self.assertTrue(self.brief("S2").exists())

    def test_only_ignores_content_errors_in_other_slices_but_not_in_the_preamble(self):
        self.mk('== S1\nextract: S L1\n== S2\nextract: P "nope"\n')
        self.assertEqual(self.run_mk("--only", "S1").returncode, 0)
        self.mk('alias: G=plans/GONE.md\n== S1\nextract: S L1\n')
        self.assertEqual(self.run_mk("--only", "S1").returncode, 1)

    def test_list_prints_ids_and_writes_nothing(self):
        self.mk('== S1\nextract: S L1\n== S2\nextract: P "nope"\n')
        got = self.run_mk("--list")
        self.assertEqual(got.returncode, 0, got.all)
        self.assertRegex(got.stdout, r"(?m)^S1\b")
        self.assertRegex(got.stdout, r"(?m)^S2\b")
        self.assertFalse((self.root / "tmp/s/impl").exists())


class WarningTest(Base):
    def test_new_file_whose_directory_is_absent_warns_because_it_may_be_a_typo(self):
        self.mk('== S1\nextract: S L1\nfiles: wbe/app/x.html\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)
        self.assertIn("wbe/app/x.html", got.all)
        self.assertIn("manifest L{}".format(self.line_of("files:")), got.all)

    def test_new_file_in_an_existing_directory_does_not_warn(self):
        self.mk('== S1\nextract: S L1\nfiles: web/app/new.html\n')
        self.assertNotIn("警告", self.run_mk().all)

    def test_files_that_hit_nothing_say_so_in_the_brief_and_warn(self):
        self.mk('== S1\nextract: S L1\nfiles: plans/x.txt\n')
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)
        self.assertIn("沒有命中", self.text())
        self.assertIn("沒有命中", got.all)


class TrailingCommentTest(Base):
    # WHY: `out: tmp/x  # why` would otherwise create a directory literally named with the comment.
    def test_trailing_comment_on_a_path_directive_is_rejected(self):
        for preamble, body, needle in (
                ("root: {root}\nout: tmp/s/impl  # where\nalias: S=plans/SPEC.md\n", "== S1\nextract: S L1\n", "# where"),
                (PREAMBLE, "== S1\nextract: S L1\nhead: tmp/s/S1-head.md # why\n", "# why"),
                (PREAMBLE, "shared: tmp/s/shared.md # why\n== S1\nextract: S L1\n", "# why")):
            with self.subTest(needle=needle):
                self.mk(body, preamble=preamble.replace("shared: tmp/s/shared.md\n", "") if "shared" in body else preamble)
                got = self.run_mk()
                self.assertEqual(got.returncode, 2, got.all)
                self.assertIn("manifest L{}".format(self.line_of(needle)), got.all)

    def test_trailing_comment_on_extract_and_files_is_rejected(self):
        for line in ("extract: S L1 # why", "extract: P 切片 # why"):
            with self.subTest(line=line):
                self.mk("== S1\n{}\n".format(line))
                self.assertEqual(self.run_mk().returncode, 2)
        self.mk("== S1\nextract: S L1\nfiles: web/x.html # why\n")
        got = self.run_mk()
        self.assertNotEqual(got.returncode, 0, got.all)
        self.assertIn("manifest L{}".format(self.line_of("files:")), got.all)

    def test_hash_in_a_note_or_ondemand_note_is_plain_text(self):
        self.mk("== S1\nextract: S L1\nnote: 見 issue #7\nondemand: P 第 #3 節\n")
        got = self.run_mk()
        self.assertEqual(got.returncode, 0, got.all)
        self.assertIn("見 issue #7", self.text())
        self.assertIn("第 #3 節", self.text())


if __name__ == "__main__":
    unittest.main()
