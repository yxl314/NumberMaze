#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""standardize 侧的 doctor：回答"现在生效的是什么、上次为什么红、下一步跑什么"。

与 `ci/lib/doctor.py` 是**平行实现**（两个项目互不 import）。ci 侧那份看的是构建
与门禁，这份看的是排版 / 风格 / 命名。四段结构两侧一致，措辞也一致，好让人在两
边看到同一套说法：

    ① 环境       Python、clang 两件套的实际路径与版本、来源（pip 包 / 系统）、
                 CLANG_PREFER 生效值、平台级别与判据
    ② 生效配置   关键取值 + 每项来源（配置文件 / 推导 / 缺省）
    ③ 上次结论   从 CI_LOG_DIR 与报告读回，按检查名与"需人工"聚合
    ④ 下一步     具体命令，而不是让人去猜配置项名
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


# ------------------------------------------------------------------ ③ 上次结论

def _load_report(directory):
    """最近一份报告（工具写的 JSON），没有就返回 None。"""
    import glob
    import json

    if not directory or not os.path.isdir(directory):
        return None
    newest = None
    for path in glob.glob(os.path.join(directory, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (IOError, OSError, ValueError):
            # 读不出/不是 JSON：当成"这份报告不存在"跳过。doctor 是只读自检，
            # 不能因为一份残缺的历史报告就整体失败
            continue
        if not isinstance(document, dict) or "report_version" not in document:
            continue
        if newest is None or path > newest[0]:
            newest = (path, document)
    return newest[1] if newest else None


def analyse(config, log_dir=None):
    """③ 上次结论：从报告（优先）或日志读回，聚合成"哪一类多少条"。"""
    import glob

    directory = log_dir or _report_dir(config)
    document = _load_report(directory)
    if document:
        rows = []
        for stage in document.get("stages") or []:
            rows.append((stage.get("name", "?"), stage.get("status", "?"),
                         _clip(stage.get("summary", ""))))
        return {"source": "报告", "stages": rows,
                "report": directory}
    # 退路：日志里数一数诊断行
    rows = []
    if directory and os.path.isdir(directory):
        for path in sorted(glob.glob(os.path.join(directory, "*.log"))):
            name = os.path.basename(path)[:-4]
            count = 0
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        if ": error:" in line or ": warning:" in line:
                            count += 1
            except (IOError, OSError):
                # 单份日志读不出：跳过它继续数别的。doctor 是只读自检，
                # 不能因为一份残缺日志就整体失败
                continue
            rows.append((name, "见日志", "{} 条诊断".format(count)))
    return {"source": "日志" if rows else "无", "stages": rows,
            "report": directory}


def _report_dir(config):
    """报告与日志所在目录：配置里的 CI_LOG_DIR 优先，其次环境变量，最后缺省。

    只认环境变量时，配置里明写了 CI_LOG_DIR 的工程会被报成"还没有跑过"——而它
    明明跑过很多次，只是日志不在缺省位置。三个真实工程都把日志放在
    ROOT/.cxx_ci/logs，于是这一侧的上次结论永远是空的。
    """
    explicit = getattr(config, "log_dir", None)
    if explicit:
        return str(explicit)
    from_env = os.environ.get("CXX_CI_LOG_DIR", "")
    if from_env:
        return from_env
    root = str(getattr(config, "root", "") or "")
    build = os.environ.get("CXX_CI_BUILD_DIR", "")
    if build:
        return os.path.join(build, "ci-logs")
    return os.path.join(root, "build", "ci-logs") if root else ""


def last_result_lines(config, analysis=None):
    analysis = analysis or analyse(config)
    lines = ["来源: {}（{}）".format(analysis["source"], analysis["report"])]
    if not analysis["stages"]:
        lines.append("  还没有跑过：先跑一次 `cxx-standardize` 看当前状态")
        return lines
    for name, status, summary in analysis["stages"]:
        lines.append("  {:<10} {:<8} {}".format(name, status, summary))
    return lines


# ------------------------------------------------------------------ ④ 下一步

def next_step_lines(config, analysis=None):
    analysis = analysis or analyse(config)
    lines = []
    unfixed = 0
    for _name, _status, summary in analysis["stages"]:
        for token in str(summary).split():
            if token.isdigit():
                unfixed = max(unfixed, int(token))
    lines.append("  看当前状态（只报告不改）：  cxx-standardize --config <配置> check")
    lines.append("  一次修完（排版+风格+命名）：cxx-standardize --config <配置> --apply")
    if unfixed:
        # 有存量违规时给的是"把它登记成基线"，而不是让人一条条手工改
        lines.append("  把存量登记成基线（门禁当天变绿、以后只对新问题判红）："
                     "  cxx-standardize --config <配置> style --record")
    else:
        lines.append("  首次接入建议先登记存量："
                     "  cxx-standardize --config <配置> style --record")
    lines.append("  配置项含义与改动时机：      cxx-standardize --help-config")
    return lines


# ------------------------------------------------------------------ 入口

def sections(config=None, log_dir=None):
    """四段文本，每段一屏内。"""
    result = [("① 环境", environment_lines(config))]
    if config is not None:
        result.append(("② 生效配置", config_lines(config)))
    analysis = analyse(config, log_dir) if config is not None else None
    if analysis is not None:
        result.append(("③ 上次结论", last_result_lines(config, analysis)))
        result.append(("④ 下一步", next_step_lines(config, analysis)))
    return result
