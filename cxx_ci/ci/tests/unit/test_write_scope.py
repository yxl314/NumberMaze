#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""写盘越界判定：禁改区、排除区、以及**真实落点**跑到工程之外。

第三种以前没人管：工程内的软链指向工程外时，按链接路径看它在范围里，实际被
改写的是外面那个文件，而输出里打印的还是链接路径——事后完全看不出改的是谁。
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))

from lib import paths  # noqa: E402


def _write(path, text):
    """不用 Path.write_text：那是 3.5 起的接口，ci 侧要能在 3.4 上跑。"""
    handle = open(str(path), "w", encoding="utf-8")
    try:
        handle.write(text)
    finally:
        handle.close()


class FakeConfig(object):
    def __init__(self, root, frozen=(), excluded=()):
        self.frozen = list(frozen)
        self.scope = {"project": [root], "excluded": list(excluded)}
        self.whitelist = []
        self.blacklist = list(excluded)


def test_a_file_inside_the_project_may_be_written(tmp_path):
    config = FakeConfig(tmp_path)
    hit, _why = paths.write_blocked(config, tmp_path / "src" / "a.cpp")
    assert not hit


def test_the_excluded_area_may_not_be_written(tmp_path):
    config = FakeConfig(tmp_path, excluded=[tmp_path / "build"])
    hit, why = paths.write_blocked(config, tmp_path / "build" / "gen.cpp")
    assert hit and "排除区" == why, why


def test_the_frozen_area_may_not_be_written(tmp_path):
    config = FakeConfig(tmp_path, frozen=[tmp_path / "vendor"])
    hit, why = paths.write_blocked(config, tmp_path / "vendor" / "a.h")
    assert hit and "禁改区" == why, why


def test_a_symlink_pointing_outside_is_refused(tmp_path):
    """按链接路径看它在工程里，真实落点在外面——必须按真实落点判。"""
    outside = tmp_path / "outside"
    inside = tmp_path / "proj"
    outside.mkdir()
    (inside / "src").mkdir(parents=True)
    _write(outside / "shared.h", "int f();\n")
    link = inside / "src" / "shared.h"
    os.symlink(str(outside / "shared.h"), str(link))
    config = FakeConfig(inside)
    hit, why = paths.write_blocked(config, link)
    assert hit and "工程范围之外" == why, why


def test_a_symlink_staying_inside_is_allowed(tmp_path):
    real = tmp_path / "src" / "real.h"
    real.parent.mkdir(parents=True)
    _write(real, "int f();\n")
    link = tmp_path / "src" / "alias.h"
    os.symlink(str(real), str(link))
    hit, _why = paths.write_blocked(FakeConfig(tmp_path), link)
    assert not hit
