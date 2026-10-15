#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内存检查指令指向构建/测试驱动时的处理。

驱动名（ctest / make / qmake / ninja）不是被测程序：valgrind 只会检查驱动本身，
而驱动通常也不在构建目录里。照着程序名去找，报出来的是"可执行文件不存在"——
那是症状；真正要改的是 MEMCHECK_COMMAND。因此这两种情况必须分开：
指向驱动 → 标 [SKIPPED] 并说清该填什么；指向一个具体但缺失的程序 → 判红。
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))

from lib import config as config_module  # noqa: E402


class FakeConfig(object):
    def __init__(self, command, test_command=None):
        self.memcheck_command = command
        self.test_command = test_command if test_command is not None else command


class TestDriverPredicate:
    def test_driver_basenames_are_recognized(self):
        for name in ("ctest", "make", "qmake", "ninja"):
            assert name == config_module.memcheck_driver(FakeConfig([name])), name

    def test_absolute_path_to_a_driver_is_recognized(self):
        config = FakeConfig(["/usr/bin/ctest", "--output-on-failure"])
        assert "ctest" == config_module.memcheck_driver(config)

    def test_a_real_program_is_not_a_driver(self):
        config = FakeConfig(["build/tests/demo_test"])
        assert "" == config_module.memcheck_driver(config)

    def test_no_command_is_not_a_driver(self):
        assert "" == config_module.memcheck_driver(FakeConfig([]))

    def test_driver_is_reported_by_advise(self):
        """提醒与阶段行为出自同一个判据，不能各说各的。"""
        notes = config_module.advise(FakeConfig(["ctest", "--output-on-failure"]))
        assert any("驱动" in note for note in notes), notes

    def test_a_real_program_produces_no_driver_note(self):
        notes = config_module.advise(FakeConfig(["build/tests/demo_test"]))
        assert not any("驱动" in note for note in notes), notes
