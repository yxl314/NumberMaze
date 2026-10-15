#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查代码是否仍兼容 Python 3.4。

老机器上只有 3.4，而新语法与新版接口在本机不会报错，只会在那边失败。
这里静态拦住最常见的几类：f-string、3.5 起才有的接口、subprocess.run。
"""

import ast
import re
from pathlib import Path

from lib import config

# 3.5 起才有的方法名，按属性访问判定
BANNED_METHODS = ("read_text", "write_text", "run")

# 3.5 起才有的关键字参数，按关键字名判定
BANNED_KEYWORDS = ("exist_ok", "capture_output")

# 本文件自身列出了这些字符串，不参与检查
SELF = "test_py34.py"

def source_files():
    """返回本项目全部 Python 文件。

    本项目整体受 Python 3.4 约束——没有"哪一层可以不兼容"的说法：另一个
    项目是独立的目录与独立的包，不在这里。
    """
    root = Path(config.TOOL_ROOT)
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts
        if ".git" in parts or "third_party" in parts:
            continue
        if path.name == SELF:
            continue
        yield path
    # tools/dupcheck.py 明确声明"Python ≥ 3.4 可跑，与 ci 同档"——它随仓库分发、
    # 在老机器上跟着门禁一起跑。同目录的 export_requirements 是开发工具（只在新
    # 机器上跑），没有这句声明，所以只纳入声明了的这一个
    shared = root.parent / "tools" / "dupcheck.py"
    if shared.is_file():
        yield shared


def read(path):
    handle = open(str(path))
    try:
        return handle.read()
    finally:
        handle.close()


def test_no_python36_syntax():
    """不得使用 f-string 等 3.6 起的语法。

    f-string 在语法树里是 JoinedStr，而该类本身 3.6 才存在，
    因此用 getattr 取，缺失时说明运行环境就是 3.4，不会有 f-string。
    """
    joined_str = getattr(ast, "JoinedStr", None)
    if joined_str is None:
        return
    offenders = []
    for path in source_files():
        tree = ast.parse(read(path), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, joined_str):
                offenders.append("{}:{}".format(path, node.lineno))
    assert [] == offenders, "使用了 f-string: " + "、".join(offenders)


def test_text_writes_declare_encoding():
    """文本写入必须显式指定编码。

    区域设置为 C 时默认编码是 ASCII，写非 ASCII 内容会抛 UnicodeEncodeError。
    本机默认 UTF-8 不会暴露，只有老机器上才失败。
    """
    offenders = []
    for path in source_files():
        for number, line in enumerate(read(path).splitlines(), 1):
            if not re.search(r'open\([^)]*["\']w["\']', line):
                continue
            if "encoding=" in line:
                continue
            offenders.append("{}:{}".format(path, number))
    assert [] == offenders, "文本写入未指定编码: " + "、".join(offenders)


def test_no_python35_apis():
    """不得使用 3.5 起才有的接口。

    用语法树判定而非子串：子串会把 _read_text 这类私有函数名误判成
    Path.read_text，命名自由不该被检查绑架。
    """
    offenders = []
    for path in source_files():
        tree = ast.parse(read(path), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in BANNED_METHODS:
                # subprocess.run 与 Path.read_text/write_text 是不同对象，
                # 这里只拦 subprocess 模块上的 run 与 Path 上的读写
                if node.attr == "run":
                    base = node.value
                    if not (isinstance(base, ast.Name)
                            and base.id in ("subprocess", "subprocess32")):
                        continue
                offenders.append("{}:{} .{}".format(
                    path, node.lineno, node.attr))
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg in BANNED_KEYWORDS:
                        offenders.append("{}:{} {}=".format(
                            path, node.lineno, keyword.arg))
    assert [] == offenders, "使用了 3.5 起的接口: " + "、".join(offenders)
