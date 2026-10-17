#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""standardize 侧的 doctor：回答"现在生效的是什么"。

    ① 环境       Python、clang 两件套的实际路径与版本、来源（pip 包 / 系统）、
                 CLANG_PREFER 生效值、平台级别与判据
    ② 生效配置   关键取值 + 每项来源（配置文件 / 推导 / 缺省）
"""

from __future__ import annotations

import os
import sys

from . import platform, version

# 每行截断长度：doctor 是一屏内能读完的东西，不是日志倾倒
_CLIP = 120

# 生效配置里值得展示的项，按重要性排。顺序即阅读顺序
KEY_CONFIG = (
    "clang_major", "clang_prefer", "clang_format_bin", "clang_tidy_bin",
    "format_comments", "source_encoding", "naming_prefix",
    "tidy_header_filter", "frozen",
)


def _clip(text, limit=_CLIP):
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _first_line(text):
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


# ------------------------------------------------------------------ ① 环境

# 找可执行文件与取版本这两件小事，platform.py 里已经有一份；同一侧内部不再
# 复制第二份——可移植的公共部分在每侧内部各自收口，平行实现的面积只减不增
_default_find = platform.find_binary
_default_run = platform.first_line_of


def _wheel_path(path):
    """这个可执行文件是不是来自 pip wheel（包内的 data/bin 下）。"""
    marker = os.sep + "data" + os.sep + "bin" + os.sep
    return marker in os.path.abspath(path)


def environment_lines(config=None, find=None, run=None):
    """① 环境。find / run 可注入，便于单测断言输出形状。"""
    find = find or _default_find
    run = run or _default_run
    lines = ["Python {}.{}.{}（本工具要求 >= {}）".format(
        sys.version_info[0], sys.version_info[1], sys.version_info[2],
        ".".join(str(item) for item in version.MIN_PYTHON))]
    # 平台级别与判据：支持面在 README / CONTRACT / doctor 三处措辞一致，
    # 这里连判据一起打出来——"为什么判成这样"和"判成什么"一样重要
    level, reason = platform.detect()
    lines.append("平台: {}（{}）—— {}".format(level, reason,
                                             platform.SUPPORT_LINE))
    prefer = str(getattr(config, "clang_prefer", "") or "wheel")
    lines.append("CLANG_PREFER = {}（pip 包与系统 clang 的取舍）".format(prefer))
    for name in ("clang-format", "clang-tidy"):
        explicit = ""
        if name == "clang-format":
            explicit = str(getattr(config, "clang_format_bin", "") or "")
        else:
            explicit = str(getattr(config, "clang_tidy_bin", "") or "")
        path = explicit or find([name])
        if not path:
            lines.append("{}: 未找到（apt 装 clang-tools，"
                         "或 pip install -r requirements/standardize.txt）"
                         .format(name))
            continue
        mark = "（显式指定）" if explicit else (
            "（pip 包）" if _wheel_path(path) else "（系统）")
        version_text = run([path, "--version"])
        lines.append("{}: {}{}{}".format(
            name, path, mark, "（" + version_text + "）" if version_text else ""))
    lines.append(_dependencies_line())
    return lines


def _dependencies_line():
    """依赖清单在不在、装没装：按 requirements/ 校验，缺包在这里点名。"""
    root = _repo_root()
    if root is None:
        return "依赖清单: 未找到 requirements/（本工具随仓库分发时才带）"
    path = os.path.join(root, "requirements", "standardize.txt")
    if not os.path.isfile(path):
        return "依赖清单: 缺少 " + path
    missing = []
    for name in ("yaml",):
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    if missing:
        return ("依赖清单 {}: 缺包 {}（pip install -r {})".format(
            path, "、".join(missing), path))
    return "依赖清单 {}: 已满足".format(path)


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.isdir(os.path.join(here, "requirements")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return None


# ------------------------------------------------------------------ ② 配置

def _format_value(value):
    if isinstance(value, dict):
        return "{" + "、".join("{}: {}".format(k, _clip(v, 40))
                              for k, v in sorted(value.items())) + "}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        return "[" + "、".join(_clip(item, 40) for item in value) + "]"
    if isinstance(value, str):
        return value if value else '""'
    return str(value)


def _source_of(config, name, value):
    """这一项是项目写的、推导出来的、还是取缺省。"""
    declared = getattr(config, "declared_keys", None)
    if declared is not None and name in declared:
        return "配置文件"
    if value in (None, "", [], (), {}):
        return "缺省"
    return "推导"


def config_lines(config):
    """② 生效配置：关键取值 + 每项来源。"""
    lines = []
    for name in KEY_CONFIG:
        if not hasattr(config, name):
            continue
        value = getattr(config, name)
        lines.append("  {} = {}  [{}]".format(
            name.upper(), _format_value(value), _source_of(config, name, value)))
    return lines


# ------------------------------------------------------------------ 入口

def sections(config=None):
    """两段文本：环境与生效配置。"""
    result = [("① 环境", environment_lines(config))]
    if config is not None:
        result.append(("② 生效配置", config_lines(config)))
    return result
