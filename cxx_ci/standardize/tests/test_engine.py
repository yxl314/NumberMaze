#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原子改名引擎的单元测试。

这些机制的形状由实测决定：跨文件的改名必须"先收集、后一次性应用"，否则第一个
文件改完，包含它的文件就编译不过，clang-tidy 连分析都做不了。
"""

from pathlib import Path

import yaml

import pytest

from cxx_standardize import config as config_module, prefix, tidy, xfail
from cxx_standardize.errors import Code


def fix_file(project, name, entries):
    """造一份 --export-fixes 结果。entries 是 (文件, 偏移, 长度, 新文本, 基准目录)。"""
    rows = []
    for target, offset, length, text, base in entries:
        rows.append(
            "  - DiagnosticName:  readability-identifier-naming\n"
            "    DiagnosticMessage:\n"
            "      Message:         invalid case style for public member 'x'\n"
            f"      FilePath:        {target}\n"
            f"      FileOffset:      {offset}\n"
            "      Replacements:\n"
            f"        - FilePath:        {target}\n"
            f"          Offset:          {offset}\n"
            f"          Length:          {length}\n"
            f"          ReplacementText: {text}\n"
            "    Level:           Warning\n"
            f"    BuildDirectory:  {base}\n")
    body = "---\nMainSourceFile:  x\nDiagnostics:\n" + "".join(rows) + "...\n"
    return project.write(name, body)


class TestRawReplacements:
    """合并去重：同一处替换在多个翻译单元里各出现一次。"""

    def test_duplicates_collapse(self, project):
        target = project.write("src/a.cpp", "int Bad = 1;\n")
        base = str(project.path)
        first = fix_file(project, "a.yaml",
                         [(str(target), 4, 3, "t_Bad", base)])
        second = fix_file(project, "b.yaml",
                          [(str(target), 4, 3, "t_Bad", base)])
        merged = tidy.raw_replacements([first, second])
        # 值里第三个是诊断给出的 kind，前缀规范化要用它
        assert {(str(target), 4): (3, "t_Bad",
                                   "PublicMember")} == merged

    def test_same_spot_different_text_is_refused(self, project):
        """同点不一致只可能是配置不确定——拒跑，绝不猜。"""
        target = project.write("src/a.cpp", "int Bad = 1;\n")
        base = str(project.path)
        first = fix_file(project, "a.yaml",
                         [(str(target), 4, 3, "t_Bad", base)])
        second = fix_file(project, "b.yaml",
                          [(str(target), 4, 3, "v_Bad", base)])
        with pytest.raises(tidy.DeterministicConflict):
            tidy.raw_replacements([first, second])

    def test_relative_paths_resolve_against_the_build_directory(self, project):
        """qmake 的数据库里 FilePath 就是 ../../src/x.cpp 这种形状。"""
        project.write("build/src/CMakeCache.txt", "")
        target = project.write("src/a.cpp", "int Bad = 1;\n")
        base = str(project.path / "build" / "src")
        fixes = fix_file(project, "a.yaml",
                         [("../../src/a.cpp", 4, 3, "t_Bad", base)])
        merged = tidy.raw_replacements([fixes])
        assert (str(target), 4) in merged


class TestApplyByOffset:
    """按字节偏移降序直写：先改后面的不会挪动前面还没用的偏移。"""

    def test_multiple_edits_in_one_file(self, project):
        target = project.write("src/a.cpp", "int aa = 1; int bb = 2;\n")
        replacements = {(str(target), 4): (2, "t_aa"),
                        (str(target), 16): (2, "t_bb")}
        touched = tidy.apply_by_offset(replacements)
        assert [target] == touched
        assert "int t_aa = 1; int t_bb = 2;\n" == target.read_text(
            encoding="utf-8")

    def test_deletion_is_an_empty_replacement(self, project):
        target = project.write("src/a.cpp", "int Bad = 1;\n")
        tidy.apply_by_offset({(str(target), 4): (3, "")})
        assert "int  = 1;\n" == target.read_text(encoding="utf-8")

    def test_bytes_semantics_with_multibyte_text(self, project):
        """Offset/Length 是字节语义，全程按 bytes 操作才不会被中文挤歪。"""
        target = project.write("src/a.cpp", "// 注释\nint Bad = 1;\n")
        offset = len("// 注释\nint ".encode("utf-8"))
        tidy.apply_by_offset({(str(target), offset): (3, "t_Bad")})
        assert "// 注释\nint t_Bad = 1;\n" == target.read_text(encoding="utf-8")


class TestPrefixRetarget:
    """前缀用错要改正，不能只丢弃，也不能叠一层。

    clang-tidy 只会把配置里的 Prefix 拼到名字前面，遇到"名字上已经挂着别的前缀"
    时给出的是叠加结果（g_digits → s_g_digits）。位置由它提供，文本由这里算。
    """

    class Config:
        # 已知前缀集合就是这张表的值：判断"名字上挂的是不是我们的前缀"靠它，
        # 因此表必须写全（少了 g_，g_digits 里的 g_ 就认不出来，也就剥不掉）
        naming_prefix = {"StaticConstant": "s_", "StaticVariable": "s_",
                         "PublicMember": "t_", "PrivateMember": "m_",
                         "Parameter": "v_", "GlobalVariable": "g_",
                         "GlobalConstant": "g_"}

    def _target(self, kind, tidied, declared):
        return prefix.target_name(kind, tidied, self.Config(), declared)

    def test_bare_name_gets_its_prefix(self):
        assert "s_digits" == self._target("StaticConstant", "s_digits", "s_")

    def test_wrong_prefix_is_replaced_not_stacked(self):
        """静态常量挂 g_：clang-tidy 给 s_g_digits，正确目标是 s_digits。"""
        assert "s_digits" == self._target("StaticConstant", "s_g_digits", "s_")

    def test_member_with_another_prefix(self):
        """成员挂 m_ 而该类要求 t_：t_m_nums_size → t_nums_size。"""
        assert "t_nums_size" == self._target("PublicMember", "t_m_nums_size",
                                             "t_")

    def test_local_prefix_is_removed(self):
        """局部变量规范要求裸名：clang-tidy 给 _v_shift，目标是 shift。"""
        assert "shift" == self._target("LocalVariable", "_v_shift", "_")

    def test_prefix_not_in_the_table_is_not_stripped(self):
        """`k` 这类不在口径表里的开头不是"我们的前缀"，认不出来就不剥，
        按规则给它加上该类要求的前缀。"""
        assert "g_k_value" == self._target("GlobalConstant", "g_k_value", "g_")

    def test_unknown_prefix_is_kept(self):
        """nums_size 这种"不是已知前缀"的名字，加前缀即可，不该剥。"""
        assert "t_nums_size" == self._target("PublicMember", "t_nums_size", "t_")

    def test_case_conversion_comes_from_clang_tidy(self):
        """大小写不自己转：剥前缀不影响 clang-tidy 已经转好的部分。"""
        assert "t_nums_size" == self._target("PublicMember", "t_m_nums_size",
                                             "t_")

    def test_unexpected_text_is_refused(self):
        """文本开头不是它该拼的前缀，说明对配置的理解有偏差——不猜。"""
        assert "" == self._target("StaticConstant", "zzz", "s_")

    def test_degenerate_result_is_refused(self):
        assert "" == self._target("StaticConstant", "s_", "s_")

    def test_retarget_reports_the_rewrites(self):
        records = {("a.cpp", 4): (6, "s_g_digits", "StaticConstant"),
                   ("a.cpp", 20): (5, "t_pid", "PublicMember")}
        fixed, refused, rewritten = prefix.retarget(records, self.Config(),
                                                    {"StaticConstant": "s_"})
        assert [("s_g_digits", "s_digits")] == rewritten
        assert [] == refused
        assert "s_digits" == fixed[("a.cpp", 4)][1]

    def test_kind_is_read_from_the_message(self):
        assert "StaticConstant" == tidy.kind_of(
            "invalid case style for static constant 'g_digits'")
        assert "PublicMember" == tidy.kind_of(
            "invalid case style for public member 'pid'")
        assert "ScopedEnumConstant" == tidy.kind_of(
            "invalid case style for scoped enum constant 'e'")
        assert "" == tidy.kind_of("something else")


class Stub:
    """最小配置替身，只带元检查与 filter 检查要用的字段。"""

    def __init__(self, **fields):
        self.root = ""
        self.clang_tidy_config = ""
        self.tidy_header_filter = ""
        self.naming_prefix = {}
        for key, value in fields.items():
            setattr(self, key, value)


class TestHeaderFilterShape:
    """filter 写宽了会把系统头文件一起放进来。"""

    def test_too_broad_filter_is_refused(self):
        code, message = config_module._header_filter_problem(
            "(include|src|tests)/.*")
        assert Code.HEADER_FILTER_TOO_BROAD == code
        assert "系统头文件" in message

    def test_anchored_filter_passes(self):
        code, _ = config_module._header_filter_problem(
            "include/singleproc/.*")
        assert Code.OK == code

    def test_empty_filter_is_not_checked(self):
        assert Code.OK == config_module._header_filter_problem("")[0]

    def test_invalid_regexp_is_reported(self):
        code, message = config_module._header_filter_problem("include/([")
        assert Code.HEADER_FILTER_TOO_BROAD == code
        assert "合法正则" in message


class TestBaseline:
    """登记基线：让门禁"绿得有意义"，只对新增违规判红。"""

    def test_write_then_parse_round_trips(self, project):
        path = project.path / "standardize.xfail"
        entries = [xfail.Entry("style", "src/a.cpp",
                               "readability-identifier-naming"),
                   xfail.Entry("style", "src/b.cpp", "bugprone-empty-catch")]
        code, message = xfail.write(path, entries)
        assert Code.OK == code, message
        loaded, code, message = xfail.load(str(path), str(project.path))
        assert Code.OK == code, message
        assert {("src/a.cpp", "readability-identifier-naming"),
                ("src/b.cpp", "bugprone-empty-catch")} == {
            (item.value, item.detail) for item in loaded}

    def test_duplicates_are_collapsed(self, project):
        path = project.path / "standardize.xfail"
        entries = [xfail.Entry("style", "src/a.cpp", "c1"),
                   xfail.Entry("style", "src/a.cpp", "c1")]
        xfail.write(path, entries)
        assert 1 == Path(path).read_text(encoding='utf-8').count("src/a.cpp:c1")

    def test_unwritable_path_is_reported(self, project):
        code, message = xfail.write(project.path / "nope" / "x.xfail", [])
        assert Code.XFAIL_BAD_FILE == code
        assert message
