#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读自检：回答"现在生效的是什么、上次为什么红、下一步跑什么"。

`ci.py doctor` 用它，把接入与排障要翻的几处信息收进四段，每段控制在一屏内：

    ① 环境     Python、构建工具、clang-format / clang-tidy、valgrind / libasan
    ② 配置     关键取值 + 每项来源（配置文件 / 推导 / 缺省）
    ③ 上次结论 从 CI_LOG_DIR 的机器报告读回，读不到就退回阶段日志的协议行
    ④ 下一步   具体命令，而不是让人去猜配置名

本模块只读：不写文件、不改判定。数据都取自既有模块（report 的 schema、
protocol 的协议行、build / asan / tools 的探测），因此 doctor 与门禁的结论
同源，不会自成一派。兼容 Python 3.4，只用 .format。
"""

import argparse
import collections
import glob
import json
import os
import sys

if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")

from . import asan, build, platform, protocol, report, tools
from . import config as config_module
from .config import (CONFIG_ENV, CONFIG_FILE, DEFAULTS, advise, load_config)
from .errors import OK
from .output import decode, err as _err, out as _out

SECTIONS = ("① 环境", "② 生效配置", "③ 上次结论", "④ 下一步")

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


# ------------------------------------------------------------------ ③ 上次结论

def _report_owner(document):
    """报告是哪一侧写的：读 tool 段（CONTRACT.md 登记的字段）。

    两侧共用同一个日志目录，也共用同一份报告 schema。只按 mtime 取最新的一份
    会把对方的报告当成本侧的结论：standardize 的 style 段被列成 ci 的阶段，还会
    给出 ci 根本不认的复跑命令（--only style）。
    """
    tool = document.get("tool") or {}
    if not isinstance(tool, dict):
        return ""
    if str(tool.get("ci") or ""):
        return "ci"
    if str(tool.get("standardize") or ""):
        return "standardize"
    return ""


def _load_report(directory, owner=""):
    """CI_LOG_DIR 里最新的机器报告，返回 (路径, 文档)；没有返回 ("", None)。

    只认含 report_version 的 JSON——那是 E2 的报告 schema；别的 json 一律不碰。
    owner 非空时只收那一侧写的报告（见 _report_owner）。
    """
    best, stamp = ("", None), -1.0
    if directory and os.path.isdir(directory):
        for path in glob.glob(os.path.join(directory, "*.json")):
            try:
                handle = open(path, "r", encoding="utf-8")
                try:
                    document = json.load(handle)
                finally:
                    handle.close()
            except (IOError, OSError, ValueError):
                # 报告读不出/不是 JSON：当成"这份报告不存在"跳过。doctor 是
                # 只读自检，不能因为一份残缺的历史报告就整体失败
                continue
            if not isinstance(document, dict) or "report_version" not in document:
                continue
            if owner and _report_owner(document) not in ("", owner):
                continue
            mtime = os.path.getmtime(path)
            if mtime > stamp:
                best, stamp = (path, document), mtime
    return best if best[1] is not None else ("", None)


def _diag_note(diagnostics):
    """诊断按级别与检查名聚合，压成一行。"""
    if not diagnostics:
        return ""
    levels = collections.Counter(item.get("level") or "?" for item in diagnostics)
    checks = collections.Counter(item.get("check") or "(无名检查)"
                                 for item in diagnostics)
    text = "诊断 {}（{}）".format(
        len(diagnostics),
        "、".join("{} {}".format(key, levels[key]) for key in sorted(levels)))
    text += "；检查名: " + "、".join(
        "{} {}".format(name, count) for name, count in checks.most_common(3))
    return text


def _fail_note(failures):
    """逐例失败按"已知登记 / 未登记"聚合。"""
    if not failures:
        return ""
    known = [item for item in failures if item.get("xfail")]
    unknown = [item.get("case") or "" for item in failures if not item.get("xfail")]
    text = "失败 {} 例".format(len(failures))
    if known:
        text += "（其中已知登记(xfail) {}）".format(len(known))
    if unknown:
        text += "；未登记: " + "、".join(unknown[:3])
    return text


def _stage_line(stage):
    """一个阶段一行：名称、状态、耗时，再接摘要与诊断/失败聚合。"""
    head = "{:<10} {:<8} {:>6.1f}s".format(
        stage.get("name") or "", stage.get("status") or "",
        float(stage.get("seconds") or 0.0))
    notes = [section for section in (
        _clip(stage.get("summary") or "", 60),
        _diag_note(stage.get("diagnostics") or []),
        _fail_note(stage.get("failures") or [])) if section]
    if notes:
        head += "  | " + " | ".join(notes)
    return "  " + head


def _count_log(path):
    """读一份阶段日志，返回 (诊断, 失败, 摘要)。"""
    try:
        handle = open(path, "r", encoding="utf-8", errors="replace")
        try:
            text = handle.read()
        finally:
            handle.close()
    except (IOError, OSError):
        return None
    return protocol.parse(text)


def _log_lines(directory):
    """没有机器报告时的退路：逐阶段读日志里的协议行。"""
    if not directory or not os.path.isdir(directory):
        return ["没有找到 CI_LOG_DIR: " + (directory or "未配置")]
    paths = sorted(glob.glob(os.path.join(directory, "*.log")))
    if not paths:
        return ["CI_LOG_DIR 下没有日志: " + directory]
    lines = ["来源: 阶段日志（未找到机器报告）"]
    checks = collections.Counter()
    for path in paths:
        name = os.path.splitext(os.path.basename(path))[0]
        parsed = _count_log(path)
        if parsed is None:
            lines.append("  {:<10} 读取失败".format(name))
            continue
        # 协议解析后来多了"已登记"一档（#xfail），这里要一起接住
        diagnostics, failures, _knowns, sums = parsed
        for item in diagnostics:
            checks[report.check_of(item.message) or "(无名检查)"] += 1
        notes = []
        if sums:
            notes.append(_clip(sums[-1], 60))
        if diagnostics:
            notes.append("诊断 {}".format(len(diagnostics)))
        if failures:
            notes.append("失败 {} 例".format(len(failures)))
        lines.append("  {:<10} {}".format(name, " | ".join(notes) or "无协议行"))
    if checks:
        lines.append("诊断检查名: " + "、".join(
            "{} {}".format(key, checks[key]) for key in checks.most_common(3)
            if key))
    return lines


def analyse(config, log_dir=None):
    """③ 的数据：读回上次结论，返回 {lines, failed, unfixed}。

    failed 是未通过的阶段名，供 ④ 生成"复跑哪一阶段"的具体命令。
    """
    directory = log_dir if log_dir is not None else (
        getattr(config, "log_dir", "") or "")
    path, document = _load_report(directory, owner="ci")
    if document is None:
        lines = _log_lines(directory)
        failed = []
        unfixed = 0
        ran = False
        for path in sorted(glob.glob(os.path.join(directory or "", "*.log"))):
            parsed = _count_log(path)
            if not parsed:
                continue
            # 协议解析后来多了"已登记"一档（#xfail），这里要一起接住
            diagnostics, failures, _knowns, _sums = parsed
            ran = True
            if failures:
                failed.append(os.path.splitext(os.path.basename(path))[0])
            unfixed += len(diagnostics) + len(failures)
        return {"lines": lines, "failed": failed, "unfixed": unfixed,
                "ran": ran}
    lines = ["来源: {}（report_version {}）".format(
        path, document.get("report_version"))]
    failed, unfixed = [], 0
    for stage in document.get("stages") or []:
        if stage.get("status") != "passed":
            failed.append(stage.get("name") or "")
        unfixed += (len(stage.get("diagnostics") or [])
                    + len(stage.get("failures") or []))
        lines.append(_stage_line(stage))
    if len(lines) == 1:
        lines.append("  报告里没有阶段")
    return {"lines": lines, "failed": failed, "unfixed": unfixed,
            "ran": bool(document.get("stages"))}


def standardize_conclusion_lines(config, find=None):
    """ci 汇总 standardize 的结论：它在同一份日志目录下落的报告。

    两侧不 import，靠报告文件对接——报告 schema 的用途之一就是这个。
    """
    directory = getattr(config, "log_dir", "") or ""
    loaded = _load_report(directory, owner="standardize")
    # _load_report 返回 (路径, 文档)；没有报告时是 None
    document = loaded[1] if isinstance(loaded, tuple) else loaded
    if not document:
        return ["standardize: 没有找到它的报告（它跑过并带 --report 才会有）"]
    stages = [item for item in (document.get("stages") or [])
              if item.get("name") in ("style", "format")]
    if not stages:
        return ["standardize: 报告里没有 style / format 段"]
    lines = []
    for stage in stages:
        lines.append("  standardize/{}: {} —— {}".format(
            stage.get("name"), stage.get("status"),
            stage.get("summary", "")))
    return lines


def last_result_lines(config, analysis=None):
    """③ 上次结论：本侧各阶段 + 另一侧（standardize）的结论。

    另一侧的结论从它落的报告里读（两侧不 import，靠文件对接）。
    """
    lines = list((analysis or analyse(config))["lines"])
    lines.extend(standardize_conclusion_lines(config))
    return lines


# ------------------------------------------------------------------ ④ 下一步

def next_step_lines(config, analysis=None):
    """④ 下一步：给出可直接抄的命令，不让人去猜配置名。"""
    analysis = analysis or analyse(config)
    failed = [name for name in analysis["failed"] if name]
    lines = []
    if failed:
        lines.append("复跑未通过的阶段看完整诊断: python3 ci/ci.py --only {} "
                     "--level verbose".format(",".join(failed)))
    lines.append("查看全部配置项与推导规则: python3 ci/ci.py --help-config")
    if failed or analysis["unfixed"] or not analysis.get("ran"):
        # 全新工程（还没跑过）也给出这条：首次接入最该做的是把存量一次性登记成
        # 基线，门禁当天变绿、以后只对新问题判红——而不是"先跑一天调门禁"
        lines.append("把存量违规一次性落成基线（之后只对新问题判红）: "
                     "cxx-standardize --config config/standardize.py "
                     "style --record")
    if (getattr(config, "memcheck_scope", "") == "all"
            and getattr(config, "memcheck_repeat", 1) < 2):
        lines.append("内存结论随时序抖动就复跑取交集: 在配置里写 MEMCHECK_REPEAT = 2")
    for note in advise(config):
        lines.append(note)
    return lines


def sections(config):
    """doctor 的四段内容：[(标题, [行])...]。config 必须是已加载的配置。"""
    analysis = analyse(config)
    return [(SECTIONS[0], environment_lines(config)),
            (SECTIONS[1], config_lines(config)),
            (SECTIONS[2], last_result_lines(config, analysis)),
            (SECTIONS[3], next_step_lines(config, analysis))]


# ------------------------------------------------------------------ 入口

def main(argv=None):
    """`ci.py doctor`：自行载配置，打印四段。"""
    parser = argparse.ArgumentParser(
        prog="ci.py doctor",
        description="只读自检：环境、生效配置、上次结论与下一步")
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
        _out(SECTIONS[3])
        _out("  配置无法加载，先按上面的报错把 config/config.py 建起来，"
             "再跑 init 探测: python3 ci/ci.py init")
        return code
    for title, lines in sections(config):
        _out(title)
        for line in lines:
            _out("  " + line)
    return OK
