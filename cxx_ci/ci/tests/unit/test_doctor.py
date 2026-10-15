#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doctor 四段的形状与退路。

doctor 是"只读自检"，出事的方式很安静：它不该因为环境缺件而失败，也不该在
没有机器报告时给出半截结论。这里钉住三件事：

* 四段都在，且每段都有内容；
* 没有报告时退回读日志，**协议行的每一档都要接得住**——协议后来多了"已登记"
  一档（`#xfail`），退路漏改过一次，元组从 3 元素变 4 元素当场 ValueError，
  而当时整套测试都只喂报告路径，直到在真工程里跑 doctor 才炸出来；
* 建议性检查只打印、不改判定。
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib import doctor  # noqa: E402


class Stub(object):
    """够 doctor 用的最小配置替身。"""

    def __init__(self, **kwargs):
        self.root = ""
        self.log_dir = ""
        self.build_tool = "cmake"
        self.build_tool_bin = ""
        self.scope = None
        self.whitelist = None
        self.blacklist = None
        self.memcheck_scope = ""
        self.memcheck_repeat = 1
        for key, value in kwargs.items():
            setattr(self, key, value)


def write(path, text):
    handle = open(str(path), "w", encoding="utf-8")
    try:
        handle.write(text)
    finally:
        handle.close()


def test_log_fallback_parses_every_protocol_channel(tmp_path):
    """没有机器报告时退回读日志：协议行的每一档都要接住。"""
    directory = tmp_path / "logs"
    directory.mkdir()
    write(directory / "style.log",
          "#diag error: /p/a.cpp:3:1: 命名不合口径\n"
          "#fail: Some.case 断言不成立\n"
          "#xfail: Known.case 已登记\n"
          "#sum: 风格诊断 1 条\n")
    lines = doctor._log_lines(str(directory))
    text = "\n".join(lines)
    assert "风格诊断 1 条" in text, text


def test_log_fallback_without_directory_says_so(tmp_path):
    """没有日志目录时要说"没找到"，而不是给一段空结论。"""
    lines = doctor._log_lines(str(tmp_path / "nope"))
    assert any("没有找到" in line for line in lines), lines


def test_sections_has_all_four_parts(tmp_path):
    """四段都在，且每段都非空。"""
    config = Stub(root=str(tmp_path), log_dir=str(tmp_path / "logs"))
    sections = doctor.sections(config)
    titles = [title for title, _lines in sections]
    assert 4 == len(sections), titles
    assert any("环境" in title for title in titles), titles
    for _title, lines in sections:
        assert lines, "段内容为空"


def test_environment_part_works_without_a_config(tmp_path):
    """配置建不起来时也要给得出环境段——那正是最需要它的时候。"""
    lines = doctor.environment_lines(None)
    assert lines
    assert any("Python" in line for line in lines)


def test_config_section_marks_each_value_source():
    """生效配置要标出每一项的来源：配置文件 / 推导 / 缺省。"""
    config = Stub(root=str(Path("/tmp")), declared_keys=frozenset(["BUILD_TOOL"]))
    lines = doctor.config_lines(config)
    text = "\n".join(lines)
    assert "[配置文件]" in text
    assert "[缺省]" in text or "[推导]" in text


def test_platform_line_reports_level_and_reason():
    """支持面在 doctor 里要连判据一起打出来。"""
    lines = doctor.environment_lines(None)
    text = "\n".join(lines)
    assert "平台" in text
    assert "支持面" in text


def test_next_step_gives_a_command_for_a_fresh_project():
    """全新工程（什么都没跑过）也要给得出下一步——首次接入最该做的登记基线。"""
    config = Stub(root="/tmp")
    analysis = {"failed": [], "unfixed": 0, "stages": [], "lines": [],
                "report": ""}
    text = "\n".join(doctor.next_step_lines(config, analysis))
    assert "--help-config" in text
    assert "style --record" in text


class TestReportsAreMatchedToTheirOwner:
    """两侧共用一个日志目录，也共用同一份报告 schema。

    只按 mtime 取最新一份，会把对方的报告当成本侧结论：standardize 的 style 段
    被列成 ci 的阶段，还给出 ci 根本不认的复跑命令（--only style）。判定依据是
    报告自带的 tool 段。
    """

    def _write(self, path, tool):
        document = {"report_version": 1, "tool": tool, "stages": [
            {"name": "style" if "standardize" in tool else "build",
             "status": "failed", "seconds": 0.1, "diagnostics": [],
             "failures": [], "summary": "", "skipped_reason": "", "log": ""}]}
        handle = open(str(path), "w", encoding="utf-8")
        try:
            json.dump(document, handle)
        finally:
            handle.close()

    def test_a_standardize_report_is_not_taken_as_ours(self, tmp_path):
        self._write(tmp_path / "style.json", {"standardize": "1.0.0"})
        path, document = doctor._load_report(str(tmp_path), owner="ci")
        assert "" == path and document is None, (path, document)

    def test_our_own_report_is_taken(self, tmp_path):
        self._write(tmp_path / "ci.json", {"ci": "1.3.0", "standardize": ""})
        path, _document = doctor._load_report(str(tmp_path), owner="ci")
        assert path.endswith("ci.json"), path

    def test_the_other_side_is_found_for_the_other_section(self, tmp_path):
        self._write(tmp_path / "style.json", {"standardize": "1.0.0"})
        path, _document = doctor._load_report(str(tmp_path),
                                              owner="standardize")
        assert path.endswith("style.json"), path

    def test_an_unlabelled_report_is_accepted_either_way(self, tmp_path):
        """没有 tool 段的报告（更早的版本）不能被直接丢掉。"""
        handle = open(str(tmp_path / "old.json"), "w", encoding="utf-8")
        try:
            json.dump({"report_version": 1, "stages": []}, handle)
        finally:
            handle.close()
        path, _document = doctor._load_report(str(tmp_path), owner="ci")
        assert path.endswith("old.json"), path

    def test_no_owner_filter_keeps_the_old_behaviour(self, tmp_path):
        self._write(tmp_path / "style.json", {"standardize": "1.0.0"})
        path, _document = doctor._load_report(str(tmp_path))
        assert path.endswith("style.json"), path
