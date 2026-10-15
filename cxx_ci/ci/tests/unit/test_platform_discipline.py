#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台纪律的源码级自检。

工具事实上依赖 POSIX 语义。把"只在 UNIX 上跑"写成显式约束之后，还要防它被
绕开——两件事最容易悄悄长回来：

1. **裸 `"/"` 拼路径**：`base + "/" + name` 在 Windows 上直接错，而 GNU/Linux
   上跑得通，于是错误只在别人的机器上出现。路径一律走 `os.path.join`。
2. **散落的平台分支**：`sys.platform` / `os.uname()` / `platform.system()` 出现在
   `platform.py` 之外，等于每个模块各自判一次平台，判据迟早不一致——而不一致
   的表现正是"同一件事在不同阶段结论不同"。判定只允许有一个入口。

这里只扫源码文本、不 import 任何模块：文件都读不成依赖，跨项目读也安全。
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

SIDES = {
    "ci": REPO / "ci",
    "standardize": REPO / "standardize" / "src" / "cxx_standardize",
}

# 两侧 platform.py 的相对位置不同（ci 侧在 lib/ 下）
PLATFORM_PATHS = {
    "ci": REPO / "ci" / "lib" / "platform.py",
    "standardize": REPO / "standardize" / "src" / "cxx_standardize"
                   / "platform.py",
}

# 判定平台只允许在这里做：两侧的 platform.py 各一份
PLATFORM_FILES = ("platform.py",)

# 平台相关取值的来源名。出现在别处就是"又自己判了一次"
PLATFORM_APIS = re.compile(
    r"\b(sys\.platform|os\.uname|platform\.system|platform\.machine|"
    r"platform\.release|os\.name)\b")

# 裸斜杠拼接：字符串字面量 "/" 出现在 + 或 f-string 里当分隔符用。
# 合法的例外：URL、正则、clang 的 `--style=file:` 前缀、行注释、诊断消息文本
BARE_SLASH = re.compile(r"""(?:\+\s*|\{\s*)["']/["']""")
# 这些行提到斜杠是"文本"而不是"路径分隔"：报错消息、文档、正则
SLASH_TEXT = re.compile(
    r"#|--style|file:|https?:|\\b分隔\\b|\\b斜杠\\b|\\b路径\\b|re\.compile")


def sources(root):
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts
        if "__pycache__" in parts or "tests" in parts:
            continue
        yield path


def read(path):
    handle = open(str(path), "r", encoding="utf-8")
    try:
        return handle.read()
    finally:
        handle.close()


def offenders(pattern, allow_files=(), skip_text=False):
    """全仓扫描，返回 "相对路径:行号: 行内容" 列表。"""
    found = []
    for side, root in SIDES.items():
        for path in sources(root):
            if path.name in allow_files:
                continue
            for number, line in enumerate(read(path).splitlines(), 1):
                if pattern.search(line) is None:
                    continue
                if skip_text and SLASH_TEXT.search(line):
                    continue
                try:
                    shown = path.relative_to(REPO)
                except ValueError:
                    shown = path
                found.append("{}:{}: {}".format(shown, number, line.strip()))
    return found


def test_no_bare_slash_path_concatenation():
    """路径拼接一律走 os.path.join，不写 `base + "/" + name`。"""
    found = offenders(BARE_SLASH, allow_files=PLATFORM_FILES, skip_text=True)
    assert [] == found, (
        "发现裸斜杠拼接（Windows 上会错，而 GNU/Linux 上跑得通，"
        "错误只在别人机器上出现）：\n" + "\n".join(found))


def test_platform_decisions_live_only_in_platform_module():
    """平台判定只允许出现在 platform.py：散落的分支迟早判据不一致。"""
    found = offenders(PLATFORM_APIS, allow_files=PLATFORM_FILES)
    assert [] == found, (
        "平台相关取值出现在 platform.py 之外，等于每个模块各自判一次平台：\n"
        + "\n".join(found))


def test_both_sides_have_the_platform_module():
    """两侧各要有一份：平行实现的一致性由 test_platform_parity.py 守。"""
    for side, path in PLATFORM_PATHS.items():
        assert path.is_file(), "{} 侧缺少 platform.py: {}".format(side, path)


def test_unsupported_platform_exits_nonzero_at_process_level():
    """不支持平台的退出码必须真非 0，且要在**做任何事之前**发生。

    这里必须走进程级：`platform.guard()` 返回的是 8 位**退出状态**，而 ci 的错误码
    是 16 位。把状态直接当错误码返回时，`status(2)` 折成 0——stderr 明说"本平台
    不支持"，进程却"成功"退出。只断言 `guard() == 2` 的函数级测试看不见这一层，
    所以这里起真进程。
    """
    import os
    import sys
    import tempfile
    import textwrap

    shim = tempfile.mkdtemp(prefix="cxx_ci_platform_shim_")
    with open(os.path.join(shim, "sitecustomize.py"), "w") as handle:
        handle.write(textwrap.dedent("""
            import os


            class _Uname(object):
                sysname = "Windows"
                nodename = "x"
                release = "x"
                version = "x"
                machine = "x86_64"


            os.uname = lambda: _Uname()
        """))
    env = dict(os.environ, PYTHONPATH=shim)
    # 用 tools.capture 而不是 subprocess.run：3.4 上没有后者那些参数
    sys.path.insert(0, str(REPO / "ci"))
    from lib import tools

    status, _out, err = tools.capture(
        [sys.executable, str(REPO / "ci" / "ci.py"), "--version"], env=env)
    # capture 返回的是原始字节，自己解一下（3.4 上不假设有 text=True）
    text = err.decode("utf-8", "replace") if isinstance(err, bytes) else str(err)
    assert 2 == status, (
        "不支持平台必须非 0 退出，实际 {}；stderr={}".format(status, text))
    assert "本平台不支持" in text
