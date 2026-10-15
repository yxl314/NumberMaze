#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路径包含判定与本侧实现的一致性。

与 `tools/parity/paths_cases.json` 比对——那是两侧共用的一份定义。**只 import
本侧模块**：两个项目互不 import 是设计约束，对照靠共用数据而不是共用代码
（与 audit / xfail / platform 的对照测试同一策略）。

两侧都必须满足同一张表，因为同一个文件在 `clean` / `diagnostics` / `valgrind`
四处会被判成一样，靠的就是这个谓词只有一个口径。
"""

import json
from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ci"))

from lib import paths  # noqa: E402

def _repo_root():
    for parent in Path(__file__).resolve().parents:
        if (parent / "tools" / "parity" / "paths_cases.json").is_file():
            return parent
    raise AssertionError("找不到 tools/parity/paths_cases.json")


REPO = _repo_root()
def _load():
    """ci 侧测试也守 3.4：Path.read_text 是 3.5 才有的。"""
    handle = open(str(REPO / "tools" / "parity" / "paths_cases.json"),
                  "r", encoding="utf-8")
    try:
        return json.loads(handle.read())["cases"]
    finally:
        handle.close()


CASES = _load()


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["why"])
def test_is_within_matches_the_shared_table(case):
    assert case["inside"] == paths.is_within(case["path"], case["root"]), (
        case["why"])


def test_equals_root_is_inside_by_default_and_outside_when_strict():
    """strict 的语义只有一条：等于 root 本身算不算在内。"""
    assert paths.is_within("/a/b", "/a/b") is True
    assert paths.is_within("/a/b", "/a/b", strict=True) is False


def test_is_frozen_delegates_to_the_same_predicate():
    """禁改区判定走同一个包含谓词，不另拼前缀。"""
    assert paths.is_frozen("/w/f/a.cpp", ["/w/f"]) is True
    assert paths.is_frozen("/w/g/a.cpp", ["/w/f"]) is False
    assert paths.is_frozen("/w/f/a.cpp", []) is False
