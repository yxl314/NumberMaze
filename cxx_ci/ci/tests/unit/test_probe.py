#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""init 探测与渲染的单元测试。

探测结果被断言到具体取值，生成的配置则直接喂回 load_config——"生成的配置必须
能被自己加载"是 init 的存在意义，不能只断言字符串。
"""

import contextlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from lib import probe
from lib.config import load_config


def make_project(files):
    """在临时目录里造一个工程，files 是 {相对路径: 内容}。"""
    root = tempfile.mkdtemp(prefix="cxx_ci_probe_")
    for rel, text in files.items():
        path = os.path.join(root, rel)
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        handle = open(path, "w", encoding="utf-8")
        try:
            handle.write(text)
        finally:
            handle.close()
    return root


def write_out(root, plan):
    """把渲染结果写到 <root>/config/ci.py，返回该路径。"""
    path = os.path.join(root, "config", "ci.py")
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    handle = open(path, "w", encoding="utf-8")
    try:
        handle.write(probe.render(plan))
    finally:
        handle.close()
    return path


class TestDetectBuild:
    def test_cmake_only(self):
        root = make_project({"CMakeLists.txt": "project(x)\n"})
        tool, build_file, _note = probe.detect_build(probe.walk_files(root))
        assert "cmake" == tool
        assert "CMakeLists.txt" == build_file

    def test_qmake_only(self):
        root = make_project({"app.pro": "TEMPLATE = app\n"})
        tool, build_file, note = probe.detect_build(probe.walk_files(root))
        assert "qmake" == tool
        assert "app.pro" == build_file
        assert "qmake6" in note

    def test_ambiguity_is_not_guessed(self):
        """cmake 与 qmake 候选并存时不猜，列出候选让人二选一。"""
        root = make_project({"CMakeLists.txt": "", "app.pro": ""})
        tool, build_file, note = probe.detect_build(probe.walk_files(root))
        assert tool is None and build_file is None
        assert "二选一" in note
        assert "CMakeLists.txt" in note and "app.pro" in note

    def test_nothing_found(self):
        root = make_project({"README.md": "hi\n"})
        tool, build_file, _note = probe.detect_build(probe.walk_files(root))
        assert tool is None and build_file is None


class TestDetectCxxStandard:
    def test_cmake_standard(self):
        root = make_project({"CMakeLists.txt": "set(CMAKE_CXX_STANDARD 14)\n"})
        standard, _note = probe.detect_cxx_standard(root, ["CMakeLists.txt"])
        assert "c++14" == standard

    def test_std_flag(self):
        root = make_project({"app.pro": "QMAKE_CXXFLAGS += -std=gnu++98\n"})
        standard, _note = probe.detect_cxx_standard(root, ["app.pro"])
        assert "gnu++98" == standard

    def test_qmake_config_token(self):
        root = make_project({"app.pro": "CONFIG += c++11\n"})
        standard, _note = probe.detect_cxx_standard(root, ["app.pro"])
        assert "c++11" == standard

    def test_missing_standard_reports_todo(self):
        root = make_project({"CMakeLists.txt": "project(x)\n"})
        standard, note = probe.detect_cxx_standard(root, ["CMakeLists.txt"])
        assert standard is None
        assert "-std=" in note


@contextlib.contextmanager
def _patched_own(name):
    """把 own_vendored_dir 换成固定答案：真实实现看的是本文件的位置。"""
    original = probe.own_vendored_dir
    probe.own_vendored_dir = lambda root: name
    try:
        yield
    finally:
        probe.own_vendored_dir = original


class TestDetectTestCommand:
    def test_cmake_ctest(self):
        root = make_project({"CMakeLists.txt": "enable_testing()\n"})
        files = probe.walk_files(root)
        command, _note = probe.detect_test_command(
            root, "cmake", files, "CMakeLists.txt", None)
        assert '["ctest", "--output-on-failure"]' == command

    def test_qmake_check_target(self):
        root = make_project({"app.pro": "", "test/test.pro": ""})
        files = probe.walk_files(root)
        command, _note = probe.detect_test_command(
            root, "qmake", files, "app.pro", "test/test.pro")
        assert command is not None and "check" in command

    def test_absent_test_entry_says_skipped(self):
        root = make_project({"CMakeLists.txt": "project(x)\n"})
        files = probe.walk_files(root)
        command, note = probe.detect_test_command(
            root, "cmake", files, "CMakeLists.txt", None)
        assert command is None
        assert "SKIPPED" in note


class TestDetectScope:
    def test_fallback_uses_naming_convention(self):
        root = make_project({"CMakeLists.txt": "", "src/a.cpp": "",
                             "include/b.h": ""})
        project, _excluded, _note = probe.detect_scope(
            root, probe.walk_files(root))
        assert "src" in project and "include" in project

    def test_build_dir_is_always_excluded(self):
        root = make_project({"CMakeLists.txt": "", "src/a.cpp": ""})
        directory = os.path.join(root, "build")
        os.makedirs(directory)
        handle = open(os.path.join(directory, "c.cpp"), "w", encoding="utf-8")
        try:
            handle.write("")
        finally:
            handle.close()
        _project, excluded, _note = probe.detect_scope(
            root, probe.walk_files(root))
        assert "build" in excluded


class TestRender:
    def test_rendered_config_loads(self):
        root = make_project({"CMakeLists.txt":
                             "set(CMAKE_CXX_STANDARD 11)\nenable_testing()\n",
                             "src/a.cpp": ""})
        plan = probe.probe(root)
        path = write_out(root, plan)
        config, code, message = load_config(path)
        assert config is not None, message
        assert "cmake" == config.build_tool
        assert os.path.join(root, "CMakeLists.txt") == config.build_file
        assert ["ctest", "--output-on-failure"] == config.test_command
        assert os.path.join(root, "src") in config.scope["project"]

    def test_root_is_self_locating(self):
        """ROOT 从配置文件自身推出来：整份配置可随工程一起搬。"""
        root = make_project({"CMakeLists.txt": "project(x)\n"})
        text = probe.render(probe.probe(root))
        assert "ROOT = Path(__file__).resolve().parents[1]" in text
        assert "CONTRACT_VERSION = 3" in text

    def test_todos_are_reported(self):
        root = make_project({"README.md": "hi\n"})
        plan = probe.probe(root)
        assert "BUILD_TOOL" in plan.todos()
        assert "TEST_COMMAND" in plan.todos()

    def test_ambiguity_leaves_todos_and_keeps_loading_possible(self):
        """候选并存时留成 TODO：文件仍可写出，只是需要人来定。"""
        root = make_project({"CMakeLists.txt": "", "app.pro": ""})
        plan = probe.probe(root)
        assert "BUILD_TOOL" in plan.todos()
        text = probe.render(plan)
        assert "# TODO:" in text
        assert "BUILD_TOOL = \"\"" in text


class TestTestBuildFileNeedsAStandaloneProject:
    """子目录里的 CMakeLists 不是独立工程，不能当测试构建文件。

    它 add_executable 引用的是主工程里的 target，单独 configure 会失败（缺
    project()、target 也不存在），build_test 第一天就红。只有自带 project() 的
    候选才算独立测试工程；否则测试与主构建共用顶层那一份。
    """

    def test_subdirectory_without_project_is_not_a_test_project(self):
        root = make_project({
            "CMakeLists.txt": "project(x)\nadd_subdirectory(tests)\n",
            "tests/CMakeLists.txt": "enable_testing()\nadd_test(NAME t COMMAND t)\n",
        })
        files = probe.walk_files(root)
        found = probe.detect_test_build_file(root, files, "cmake",
                                             "CMakeLists.txt")
        assert found is None, found

    def test_subdirectory_with_its_own_project_is_a_test_project(self):
        root = make_project({
            "CMakeLists.txt": "project(x)\n",
            "tests/CMakeLists.txt": "project(x_tests)\nenable_testing()\n",
        })
        files = probe.walk_files(root)
        found = probe.detect_test_build_file(root, files, "cmake",
                                             "CMakeLists.txt")
        assert "tests/CMakeLists.txt" == found, found


class TestToolDoesNotAnalyseItsOwnCopy:
    """标准接入流程是把 cxx_ci/ 整个拷进工程。

    它被版本控制跟着、里面又有源码，会被探成"项目自己的代码"——工具于是去分析
    自己那份 vendored googletest，报出一堆与用户无关的诊断。探测必须认出自己。
    """

    def test_own_directory_is_excluded(self):
        root = make_project({
            "CMakeLists.txt": "project(x)\n",
            "src/main.cpp": "int main() { return 0; }\n",
            "cxx_ci/ci/lib/probe.py": "x = 1\n",
        })
        # 直接把 cxx_ci 认成"工具自己"：真实场景里这句话由 __file__ 推出
        with _patched_own("cxx_ci"):
            project, excluded, _note = probe.detect_scope(
                root, probe.walk_files(root))
        assert "cxx_ci" in excluded, excluded
        assert "cxx_ci" not in project, project

    def test_no_vendored_copy_leaves_scope_alone(self):
        root = make_project({
            "CMakeLists.txt": "project(x)\n",
            "src/main.cpp": "int main() { return 0; }\n",
        })
        with _patched_own(""):
            project, excluded, _note = probe.detect_scope(
                root, probe.walk_files(root))
        assert "cxx_ci" not in excluded, excluded
