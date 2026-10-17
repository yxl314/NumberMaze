#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doctor 两段的形状与退路。

doctor 是"只读自检"：它不该因为环境缺件而失败，也不该在没配置时不给环境段。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib import doctor  # noqa: E402


class Stub(object):
    """够 doctor 用的最小配置替身。"""

    def __init__(self, **kwargs):
        self.root = ""
        self.build_tool = "cmake"
        self.build_tool_bin = ""
        self.scope = None
        self.whitelist = None
        self.blacklist = None
        self.memcheck_scope = ""
        self.memcheck_repeat = 1
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_sections_has_environment_and_config(tmp_path):
    config = Stub(root=str(tmp_path))
    sections = doctor.sections(config)
    titles = [title for title, _lines in sections]
    assert 2 == len(sections), titles
    assert any("环境" in title for title in titles), titles
    assert any("配置" in title for title in titles), titles
    for _title, lines in sections:
        assert lines, "段内容为空"


def test_environment_part_works_without_a_config():
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
