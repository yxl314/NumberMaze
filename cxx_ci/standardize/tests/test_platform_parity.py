#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台判定与本侧模块的一致性。

与 `tools/parity/platform_expected.json` 比对——那是两侧共用的一份定义。
**只 import 本侧模块**：两个项目互不 import 是设计约束，对照靠共用数据而不是
共用代码（与 audit / xfail 的对照测试同一策略）。
"""

import json
from pathlib import Path

import pytest

from cxx_standardize import platform


def _repo_root():
    for parent in Path(__file__).resolve().parents:
        if (parent / "tools" / "parity" / "platform_expected.json").is_file():
            return parent
    raise AssertionError("找不到 tools/parity/platform_expected.json")


REPO = _repo_root()
EXPECTED = json.loads((REPO / "tools" / "parity"
                       / "platform_expected.json").read_text(encoding="utf-8"))


def test_support_line_matches_the_shared_definition():
    assert EXPECTED["support_line"] == platform.SUPPORT_LINE


def test_levels_and_unix_names_match():
    assert EXPECTED["levels"]["full"] == platform.FULL
    assert EXPECTED["levels"]["limited"] == platform.LIMITED
    assert EXPECTED["levels"]["unsupported"] == platform.UNSUPPORTED
    assert EXPECTED["unix_names"] == list(platform.UNIX_NAMES)


@pytest.mark.parametrize("name", sorted(EXPECTED["level_table"]))
def test_level_table(monkeypatch, name):
    monkeypatch.setattr(platform, "system_name", lambda: name)
    assert EXPECTED["level_table"][name] == platform.detect()[0]


def test_guard_refuses_unsupported_and_allows_supported(monkeypatch):
    import io

    monkeypatch.setattr(platform, "detect",
                        lambda: (platform.UNSUPPORTED, "别处"))
    stream = io.StringIO()
    assert 2 == platform.guard(stream)
    assert platform.SUPPORT_LINE in stream.getvalue()
    monkeypatch.setattr(platform, "detect", lambda: (platform.FULL, "GNU/Linux"))
    assert 0 == platform.guard()


def test_memory_capability_is_ci_only():
    """standardize 不做内存检查，能力探针只存在于真正用它的一侧。

    这不是漏移植：把用不上的函数也复制一份，平行实现的面积只会白白变大。
    两侧必须一致的是**级别判定**（上面几条），不是能力探针。
    """
    assert not hasattr(platform, "capability")
