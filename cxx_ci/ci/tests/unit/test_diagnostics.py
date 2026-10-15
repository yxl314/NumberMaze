#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断解析的单元测试。

重点是"什么不算诊断"：业务日志与工具自身的文本都不能被当诊断，
这正是旧的关键字过滤出错的地方。
"""

import pytest

from lib import diagnostics

ROOT = "/proj"


def parse(line, bases=None):
    return diagnostics.parse_diagnostic(line, ROOT, bases)


class TestCompilerFormat:
    def test_warning_with_column(self):
        item = parse("/proj/src/a.cpp:12:5: warning: 未使用的变量")
        assert "warning" == item.level
        assert "project" == item.source
        assert 12 == item.line
        assert 5 == item.column

    def test_error_without_column(self):
        item = parse("/proj/src/a.cpp:3: error: 编译失败")
        assert "error" == item.level
        assert 0 == item.column

    def test_note_is_lowest_level(self):
        item = parse("/proj/src/a.cpp:3:1: note: 补充说明")
        assert "note" == item.level

    def test_external_header(self):
        item = parse("/opt/qt/include/q.h:9:1: warning: 已废弃")
        assert "external" == item.source

    def test_relative_path_resolved_against_candidate_base(self):
        """qmake 在 build/src 下编译，路径是相对那里的。"""
        item = parse("../../src/a.cpp:1:2: warning: 项目自身",
                     bases=["/proj/build/src"])
        assert "project" == item.source
        assert "/proj/src/a.cpp" == item.file

    def test_relative_path_outside_all_bases_is_external(self):
        item = parse("../../../other/a.cpp:1:2: warning: 别的项目",
                     bases=["/proj/build/src"])
        assert "external" == item.source

    def test_path_inside_root_but_not_under_base_is_project(self):
        item = parse("../../other/a.cpp:1:2: warning: 仍在项目内",
                     bases=["/proj/build/src"])
        assert "project" == item.source


class TestNotDiagnostics:
    """这些都不该被当成诊断。"""

    @pytest.mark.parametrize("line", [
        "2026-01-01 |ERROR| 用例故意走错误分支",
        "2026-01-01 |WARN| 重试一次后成功",
        "$ valgrind --error-exitcode=1 --leak-check=full true",
        "==123== ERROR SUMMARY: 0 errors from 0 contexts",
        "==123== Memcheck, a memory error detector",
        "make: *** [Makefile:3: all] Error 1",
        "error: 没有文件路径的行",
        "",
    ])
    def test_rejected(self, line):
        assert parse(line) is None


class TestValgrind:
    def test_nonzero_error_count_is_a_diagnostic(self):
        item = parse("==123== ERROR SUMMARY: 3 errors from 2 contexts")
        assert "error" == item.level

    def test_zero_error_count_is_not(self):
        assert parse("==123== ERROR SUMMARY: 0 errors from 0 contexts") is None


class TestFilter:
    def make(self, level="warning", source="project"):
        return diagnostics.Diagnostic(
            level, source, "/proj/src/a.cpp", 1, 1, "消息", "原始行")

    def test_level_threshold(self):
        items = [self.make("warning"), self.make("error")]
        kept = diagnostics.filter_diagnostics(items, "error")
        assert ["error"] == [item.level for item in kept]

    def test_off_drops_everything(self):
        assert [] == diagnostics.filter_diagnostics([self.make("error")], "off")

    def test_project_only_by_default(self):
        items = [self.make(source="project"), self.make(source="external")]
        kept = diagnostics.filter_diagnostics(items, "warning")
        assert ["project"] == [item.source for item in kept]

    def test_all_keeps_external(self):
        items = [self.make(source="external")]
        assert 1 == len(diagnostics.filter_diagnostics(items, "warning", "all"))

    def test_ignore_regex(self):
        items = [self.make()]
        assert [] == diagnostics.filter_diagnostics(
            items, "warning", "project", ["消息"])


class TestDedup:
    def make(self, line=1, message="同样的问题"):
        return diagnostics.Diagnostic(
            "warning", "project", "/proj/src/a.cpp", line, 1, message, "原始")

    def test_same_diagnostic_merges(self):
        shown = diagnostics.dedup_diagnostics([self.make()] * 100)
        assert 1 == len(shown)
        assert 100 == shown[0][1]

    def test_different_line_does_not_merge(self):
        shown = diagnostics.dedup_diagnostics([self.make(1), self.make(2)])
        assert 2 == len(shown)

    def test_order_is_preserved(self):
        shown = diagnostics.dedup_diagnostics(
            [self.make(1, "甲"), self.make(2, "乙"), self.make(1, "甲")])
        assert ["甲", "乙"] == [item.message for item, _ in shown]


class TestResolveSource:
    """诊断路径的归一化：先按候选基准，再退回按后缀在项目内查找。

    不做这一步就直接拿相对路径去比 SCOPE 前缀，项目自身的告警会被一律判成
    域外而静默。
    """

    def test_absolute_path_is_normalised(self):
        path, inside = diagnostics.resolve_source("/proj/./src/a.cpp", "/proj")
        assert "/proj/src/a.cpp" == path
        assert inside

    def test_parent_segments_are_normalised(self):
        path, inside = diagnostics.resolve_source("/proj/build/../src/a.cpp", "/proj")
        assert "/proj/src/a.cpp" == path
        assert inside

    def test_relative_resolved_against_candidate_base(self):
        path, inside = diagnostics.resolve_source(
            "../../src/a.cpp", "/proj", ["/proj/build/tests"])
        assert "/proj/src/a.cpp" == path
        assert inside

    def test_falls_back_to_suffix_lookup(self, tmp_path):
        """基准猜不全时（qmake 子工程深度不定）按后缀在项目内找。"""
        target = tmp_path / "Source" / "CDBurn" / "Tools" / "Logger"
        target.mkdir(parents=True)
        source = target / "Logger.cpp"
        handle = open(str(source), "w")
        handle.write("int x;\n")
        handle.close()
        path, inside = diagnostics.resolve_source(
            "../../Source/CDBurn/Tools/Logger/Logger.cpp", str(tmp_path))
        assert inside
        assert str(source) == path

    def test_longest_suffix_wins(self, tmp_path):
        """同名文件不得互相牵连：命中最长的后缀。"""
        deep = tmp_path / "Source" / "Util.cpp"
        deep.parent.mkdir(parents=True)
        for path, text in ((deep, "int deep;\n"),
                           (tmp_path / "Util.cpp", "int shallow;\n")):
            handle = open(str(path), "w")
            handle.write(text)
            handle.close()
        path, inside = diagnostics.resolve_source("../../Source/Util.cpp",
                                             str(tmp_path))
        assert inside
        assert str(deep) == path

    def test_external_absolute_stays_external(self):
        path, inside = diagnostics.resolve_source("/opt/qt/q.h", "/proj")
        assert not inside

    def test_relative_external_stays_external(self, tmp_path):
        (tmp_path / "src").mkdir()
        path, inside = diagnostics.resolve_source("../../../usr/include/stdio.h",
                                             str(tmp_path))
        assert not inside

    def test_root_boundary_is_respected(self, tmp_path):
        """前缀相同的兄弟目录不算项目内（/proj2 不是 /proj 的子目录）。"""
        root = tmp_path / "proj"
        sibling = tmp_path / "proj2"
        root.mkdir()
        sibling.mkdir()
        _, inside = diagnostics.resolve_source(str(sibling / "a.cpp"), str(root))
        assert not inside

    def test_empty_path(self):
        assert ("", False) == diagnostics.resolve_source("", "/proj")
        assert ("", False) == diagnostics.resolve_source(None, "/proj")
