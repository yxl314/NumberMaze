#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读自检：回答"现在生效的是什么"。

`ci.py doctor` 用它，把接入与排障要翻的信息收进两段，每段控制在一屏内：

    ① 环境     Python、构建工具、clang-format / clang-tidy、valgrind / libasan
    ② 配置     关键取值 + 每项来源（配置文件 / 推导 / 缺省）

本模块只读：不写文件、不改判定。数据都取自 build / asan / tools 的探测。
兼容 Python 3.4，只用 .format。
"""

import argparse
import os
import sys

if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")

from . import asan, build, platform, tools
from . import config as config_module
from .config import (CONFIG_ENV, CONFIG_FILE, DEFAULTS, advise, load_config)
from .errors import OK
from .output import decode, err as _err, out as _out

SECTIONS = ("① 环境", "② 生效配置")

# ② 里展示的关键项：接入与排障真正会看的那些，不是全部 55 项
KEY_VALUES = (
    ("ROOT", "root"),
    ("BUILD_TOOL", "build_tool"),
    ("BUILD_FILE", "build_file"),
    ("BUILD_DIR", "build_dir"),
    ("CXX_COMPILER", "cxx_compiler"),
    ("CXX_STANDARD", "cxx_standard"),
    ("TEST_COMMAND", "test_command"),
    ("TEST_WORKDIR", "test_workdir"),
    ("MEMCHECK_COMMAND", "memcheck_command"),
    ("MEMCHECK_TOOL", "memcheck_tool"),
    ("MEMCHECK_SCOPE", "memcheck_scope"),
    ("MEMCHECK_IGNORE_KINDS", "memcheck_ignore_kinds"),
    ("MEMCHECK_REPEAT", "memcheck_repeat"),
    ("SCOPE", "scope"),
    ("DIAGNOSTIC_LEVEL", "diagnostic_level"),
    ("DIAGNOSTIC_SOURCES", "diagnostic_sources"),
    ("CI_LOG_DIR", "log_dir"),
    ("XFAIL_FILE", "xfail_file"),
    ("STEPS_DEFAULT", "steps_default"),
    ("SOURCE_ENCODING", "source_encoding"),
)

_CLIP = 96


def _clip(text, limit=_CLIP):
    """把可能很长的取值压成一行，便于放进"每段一屏内"。"""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit - 3] + "..."


def _first_line(text):
    """首行非空文本。"""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


# ------------------------------------------------------------------ ① 环境

def _version_line(cmd):
    """跑一条 `--version` 类命令，取有代表性的那一行；失败返回空串。

    优先取含 "contract" 的行——两侧 `--version` 的统一形状都带它；只打印首行
    会在把整块环境都打出来的版本上拿到 "python: ..." 这类无关内容。
    """
    code, captured, _ = tools.capture(cmd, merge=True)
    if code != 0:
        return ""
    text = decode(captured)
    for line in text.splitlines():
        if "contract" in line:
            return line.strip()
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _pip_installed(path):
    """这份可执行文件是否来自 pip 安装。

    pip 装的 console script 落在 Python 环境自己的 bin 里（通常与解释器同目录），
    或直接落在 site-packages / dist-packages；apt 装的落在 /usr/bin。两个信号
    命中任一就标出来——版本混装是接入期最常见的坑。
    """
    real = os.path.realpath(str(path))
    if "site-packages" in real or "dist-packages" in real:
        return True
    return os.path.dirname(real) == os.path.dirname(
        os.path.realpath(sys.executable))


def environment_lines(config=None, find=None, run=None):
    """① 环境：Python、构建工具、clang 两件套、valgrind / libasan、另一侧版本。

    find / run 可注入，便于单测在不依赖机器实际安装的情况下断言输出形状。
    """
    find = find or platform.find_binary
    run = run or platform.first_line_of
    lines = ["Python {}.{}.{}（本工具要求 >= 3.4）".format(*sys.version_info[:3])]
    # 平台级别与判据：支持面在 README / CONTRACT / doctor 三处措辞一致。
    # 连判据一起打出来——"为什么判成这样"和"判成什么"一样重要
    level, reason = platform.detect()
    lines.append("平台: {}（{}）—— {}".format(level, reason,
                                             platform.SUPPORT_LINE))
    usable, why = platform.capability(level)
    lines.append("  内存检查实探测: {}（{}）".format(
        "可用" if usable else "不可用", why))
    if not usable:
        lines.append("  memcheck 阶段会标 [SKIPPED]（跳过不等于通过）")
    if config is not None:
        candidates = build.BUILD_TOOL_BINS.get(
            config.build_tool, (config.build_tool,))
        binary = getattr(config, "build_tool_bin", "") or find(candidates)
        if binary:
            version = run([binary, "--version"])
            lines.append("构建工具 {}: {}{}".format(
                config.build_tool, binary,
                "（" + version + "）" if version else ""))
        else:
            lines.append("构建工具 {}: 未找到（检查 PATH，或写 BUILD_TOOL_BIN）"
                         .format(config.build_tool))
    for name in ("clang-format", "clang-tidy"):
        path = find([name])
        if not path:
            lines.append("{}: 未找到（standardize 侧需要；apt 装 clang-tools）"
                         .format(name))
            continue
        version = run([path, "--version"])
        mark = "（pip 包）" if _pip_installed(path) else ""
        lines.append("{}: {}{}{}".format(
            name, path, mark, "（" + version + "）" if version else ""))
    standardize = find(["cxx-standardize"])
    if standardize:
        lines.append("cxx-standardize: {}（{}）".format(
            standardize, run([standardize, "--version"])))
    else:
        lines.append("cxx-standardize: 未找到（standardize 是独立项目，"
                     "style / format 阶段需要它）")
    valgrind = find(["valgrind"])
    if valgrind:
        version = run([valgrind, "--version"])
        lines.append("valgrind: {}{}".format(
            valgrind, "（" + version + "）" if version else ""))
    else:
        lines.append("valgrind: 未找到（asan 路线不依赖它，见 MEMCHECK_TOOL）")
    if config is not None:
        library, tried = asan.find_runtime_library(config)
        if library:
            lines.append("libasan: " + library)
        else:
            lines.append("libasan: 未找到（试过: {}）".format(
                "、".join(tried) or "无候选编译器"))
    lines.extend(_requirements_lines())
    return lines


def _requirements_lines():
    """依赖清单：按 requirements/ 校验，缺包或越界在这里点名。

    ci 侧零第三方依赖，所以主要是把"零依赖"变成看得见的事实——清单被加了包
    就会在这里显出来。
    """
    # requirements/ 在仓库根，也就是工具目录（ci/）的上一层
    tool_root = getattr(config_module, "TOOL_ROOT", None)
    if tool_root is None:
        return []
    path = os.path.join(os.path.dirname(str(tool_root)), "requirements", "ci.txt")
    if not os.path.isfile(path):
        return ["依赖清单: 缺少 {}（ci 侧应始终是空清单）".format(path)]
    packages = []
    try:
        handle = open(path, "r", encoding="utf-8")
        try:
            for line in handle:
                text = line.split("#", 1)[0].strip()
                if text:
                    packages.append(text)
        finally:
            handle.close()
    except (IOError, OSError):
        return ["依赖清单: 读不出 " + path]
    if packages:
        return ["依赖清单 {}: 有 {} 个包（ci 侧本应零第三方依赖）：{}".format(
            path, len(packages), "、".join(packages))]
    return ["依赖清单 {}: 空清单，符合零第三方依赖".format(path)]


# ------------------------------------------------------------------ ② 配置

def _format_value(value):
    """把取值压成一行可读文本。"""
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        return "[{}]".format(", ".join(_format_value(item) for item in value))
    if isinstance(value, dict):
        parts = ["{}: {}".format(key, _format_value(value[key]))
                 for key in sorted(value)]
        return "{" + ", ".join(parts) + "}"
    if value is None:
        return "None"
    text = str(value)
    return text if text else '""'


def _source_of(config, name, value):
    """这一项的取值从哪来：配置文件 / 推导 / 缺省。"""
    if name in getattr(config, "declared_keys", frozenset()):
        return "配置文件"
    if value != DEFAULTS.get(name):
        return "推导"
    return "缺省"


def config_lines(config):
    """② 生效配置：关键取值 + 每项来源。"""
    lines = []
    for name, attribute in KEY_VALUES:
        value = getattr(config, attribute, None)
        lines.append("{} = {}  [{}]".format(
            name, _clip(_format_value(value)), _source_of(config, name, value)))
    return lines


def sections(config):
    """doctor 的两段内容：[(标题, [行])...]。config 必须是已加载的配置。"""
    return [(SECTIONS[0], environment_lines(config)),
            (SECTIONS[1], config_lines(config))]


# ------------------------------------------------------------------ 入口

def main(argv=None):
    """`ci.py doctor`：自行载配置，打印两段。"""
    parser = argparse.ArgumentParser(
        prog="ci.py doctor",
        description="只读自检：环境与生效配置")
    parser.add_argument(
        "--config",
        default=os.environ.get(CONFIG_ENV, ""),
        help="项目配置的位置，缺省取环境变量 {} 或工具自带的 {}"
             .format(CONFIG_ENV, CONFIG_FILE),
    )
    args = parser.parse_args(argv)
    config, code, message = load_config(args.config or None)
    if config is None:
        # 配置还建不起来时也要有用：先报环境与失败原因，再指到该写什么
        _out(SECTIONS[0])
        for line in environment_lines(None):
            _out("  " + line)
        _err(message)
        _out("  配置无法加载：先按上面的报错写一份配置"
             "（示例见 config-example/ci_config.py）")
        return code
    for title, lines in sections(config):
        _out(title)
        for line in lines:
            _out("  " + line)
    return OK
