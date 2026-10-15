#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""错误码的一致性检查。

错误码是跨模块的约定：同一个取值被两个概念共用，调用方就分不清发生了什么；
定义散落在各自模块里，读的人也无从知道哪些已被占用。这两点都由检查兜住。
"""

import ast
import collections
import re
from pathlib import Path

from lib import errors
from lib.config import TOOL_ROOT


def error_names():
    """errors.py 里定义的全部错误码名字。"""
    root = Path(TOOL_ROOT)
    tree = ast.parse(read(root / "lib" / "errors.py"))
    names = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id.isupper():
                if target.id.startswith("KIND_") or target.id == "OK":
                    continue
                names.append(target.id)
    return names


def read(path):
    handle = open(str(path))
    try:
        return handle.read()
    finally:
        handle.close()


def test_codes_are_unique():
    """同一个取值不能被两个概念共用。"""
    seen = collections.defaultdict(list)
    for name in error_names():
        seen[getattr(errors, name)].append(name)
    clashes = ["{} = {}".format(value, names)
               for value, names in seen.items() if len(names) > 1]
    assert [] == clashes, "错误码取值重复: " + "；".join(clashes)


def test_codes_are_defined_in_one_place():
    """错误码只在 errors.py 里定义，不在各模块就地 compose。"""
    root = Path(TOOL_ROOT)
    offenders = []
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts
        if ".git" in parts or "__pycache__" in parts:
            continue
        if parts[-2:] == ("lib", "errors.py"):
            continue
        for number, line in enumerate(read(path).splitlines(), 1):
            if re.match(r"^[A-Z][A-Z_0-9]* = compose\(", line):
                offenders.append("{}:{}".format(path, number))
    assert [] == offenders, "错误码定义在 errors.py 之外: " + "、".join(offenders)


def test_detail_numbers_are_unique_within_a_kind():
    """同一类型内的编号不重复，便于按编号定位。"""
    seen = collections.defaultdict(list)
    for name in error_names():
        code = getattr(errors, name)
        seen[errors.kind(code)].append((errors.detail(code), name))
    clashes = []
    for kind_value, items in seen.items():
        by_detail = collections.defaultdict(list)
        for detail, name in items:
            by_detail[detail].append(name)
        for detail, names in by_detail.items():
            if len(names) > 1:
                clashes.append("类型 {} 编号 {}: {}".format(
                    kind_value, detail, names))
    assert [] == clashes, "编号重复: " + "；".join(clashes)


def test_compose_and_split_round_trip():
    for name in error_names():
        code = getattr(errors, name)
        assert code == errors.compose(errors.kind(code), errors.detail(code))


def test_status_projects_to_a_process_exit_code():
    """退出状态必须落在 0-255，否则 sys.exit 会截断成别的值。"""
    for name in error_names():
        code = getattr(errors, name)
        assert 0 <= errors.status(code) <= 255, name
