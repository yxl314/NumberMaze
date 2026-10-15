#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目隔离：ci 是独立项目，不与另一个项目共享任何 Python 模块。

两个项目只通过文件与命令行契约协作（见仓库根的 CONTRACT.md）。共享模块
一旦出现，"改排版不会碰门禁"就不再成立，所以这条隔离要有用例守住。

与 boundary 阶段（内部面纪律：detail:: 归属、头文件宏守卫）是两回事。
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import config  # noqa: E402

# 只允许引用本项目的包与标准库
OWN_PACKAGE = "lib"

# 另一个项目的名字，出现在 import 里即越界
OTHER_PROJECT = ("cxx_standardize", "clangside", "standardize")


def read(path):
    handle = open(str(path), encoding="utf-8")
    try:
        return handle.read()
    finally:
        handle.close()


def python_files():
    for path in sorted(Path(config.TOOL_ROOT).rglob("*.py")):
        parts = path.relative_to(config.TOOL_ROOT).parts
        if "__pycache__" in parts or ".git" in parts:
            continue
        yield path


def imported_names(tree):
    """取出源码里 import 的顶层模块名。"""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                found.add(node.module.split(".")[0])
    return found


def test_no_module_from_the_other_project_is_imported():
    offenders = []
    for path in python_files():
        tree = ast.parse(read(path), filename=str(path))
        for name in imported_names(tree) & set(OTHER_PROJECT):
            offenders.append("{}: {}".format(path.name, name))
    assert [] == offenders, "引用了另一个项目的模块: " + "、".join(offenders)


def test_no_shared_package_outside_lib():
    """非标准库的 import 只能来自本项目自己的包。"""
    stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()
    if not stdlib:
        return
    offenders = []
    for path in python_files():
        tree = ast.parse(read(path), filename=str(path))
        for name in imported_names(tree):
            if name in stdlib or name == OWN_PACKAGE:
                continue
            # 测试自身与被测入口是例外：ci 是入口脚本，ci_support/helper 是夹具
            if name in ("pytest", "ci_support", "helper", "ci"):
                continue
            offenders.append("{}: {}".format(path.name, name))
    assert [] == offenders, "引用了本项目之外的模块: " + "、".join(offenders)


def test_lib_has_no_subpackage_layers():
    """实现库是平铺一层：不存在"哪一层可以不兼容"的问题。"""
    nested = [path.name for path in (Path(config.TOOL_ROOT) / "lib").iterdir()
              if path.is_dir() and path.name != "__pycache__"]
    assert [] == nested, "lib 下不该有子包: " + "、".join(nested)


def test_stages_only_use_the_base_flow():
    """阶段集合固定为五个，且都有对应脚本。"""
    import ci
    assert ("boundary", "clean", "build", "build_test", "run_test",
            "memcheck") == ci.STAGES
    for name in ci.STAGES:
        assert (Path(config.TOOL_ROOT) / "stages" / (name + ".py")).is_file()
