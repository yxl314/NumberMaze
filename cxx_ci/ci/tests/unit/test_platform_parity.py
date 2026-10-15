#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台判定与本侧模块的一致性。

与 `tools/parity/platform_expected.json` 比对——那是两侧共用的一份定义。
**只 import 本侧模块**：两个项目互不 import 是设计约束，对照靠共用数据而不是
共用代码（与 audit / xfail 的对照测试同一策略）。
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ci"))

from lib import platform  # noqa: E402


def _repo_root():
    """往上找到含 tools/parity 的那一层：两侧测试的目录深度不同。"""
    for parent in Path(__file__).resolve().parents:
        if (parent / "tools" / "parity" / "platform_expected.json").is_file():
            return parent
    raise AssertionError("找不到 tools/parity/platform_expected.json")


def _expected():
    handle = open(str(REPO / "tools" / "parity" / "platform_expected.json"),
                  "r", encoding="utf-8")
    try:
        return json.loads(handle.read())
    finally:
        handle.close()


REPO = _repo_root()
EXPECTED = _expected()


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


def test_capability_is_probed_not_guessed():
    """能力是实测的：unsupported 一定不可用；full 上没 valgrind 也仍算可用（走 ASan）。"""
    usable, why = platform.capability(level=platform.UNSUPPORTED)
    assert usable is False and why
    usable, why = platform.capability(
        level=platform.FULL, find=lambda names: "",
        run=lambda command: "")
    assert usable is True, why
    usable, why = platform.capability(
        level=platform.LIMITED, find=lambda names: "",
        run=lambda command: "")
    assert usable is False, why


class TestProjectLock:
    """同一个工程上只允许一份工具在跑。

    两份同时跑会互相破坏：一份的 clean 清掉另一份正在用的构建树（随后 configure
    失败，好工程判红），阶段日志被交替追加成"失败+成功"的混合文件，memcheck 的
    结构化报告被写成两份文档拼在一起、解析报结构损坏。
    """

    def _as_another_process(self):
        """清掉"锁已由本进程树持有"的标记，模拟另一棵进程树。"""
        os.environ.pop(platform.LOCK_ENV, None)

    def test_the_second_holder_is_refused(self, tmp_path):
        first, why = platform.acquire_lock(str(tmp_path))
        assert first is not None, why
        try:
            self._as_another_process()
            second, why = platform.acquire_lock(str(tmp_path))
            assert second is None, "第二个持有者不该拿到锁"
            assert "另一个进程" in why, why
        finally:
            platform.release_lock(first)

    def test_the_lock_is_released_for_the_next_run(self, tmp_path):
        first, _why = platform.acquire_lock(str(tmp_path))
        platform.release_lock(first)
        again, why = platform.acquire_lock(str(tmp_path))
        assert again is not None, why
        platform.release_lock(again)

    def test_a_child_in_the_same_tree_may_run(self, tmp_path):
        """standardize 持锁后要能调 ci.py（BUILD_COMMAND）：同一棵树里不再取锁。

        没有这条继承就会自锁：构建命令内部的 ci.py 被自己拒掉，好工程判红。
        """
        first, why = platform.acquire_lock(str(tmp_path))
        assert first is not None, why
        try:
            again, why = platform.acquire_lock(str(tmp_path))
            assert again is None and "" == why, (again, why)
        finally:
            platform.release_lock(first)

    def test_two_projects_do_not_block_each_other(self, tmp_path):
        one = tmp_path / "one"
        two = tmp_path / "two"
        one.mkdir()
        two.mkdir()
        first, _why = platform.acquire_lock(str(one))
        self._as_another_process()
        second, why = platform.acquire_lock(str(two))
        assert second is not None, why
        platform.release_lock(first)
        platform.release_lock(second)
