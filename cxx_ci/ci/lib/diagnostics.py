#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从阶段输出里提取结构化诊断。

只认固定的版式，不做关键字猜测：编译器告警是 "路径:行:列: 级别: 消息"，
valgrind 汇总行是 "==PID== ERROR SUMMARY: N errors"。其余文本一律不是诊断
——测试程序的业务日志、工具自己的命令回显都落在这里，不会被误当诊断。
"""

import collections
import os
import re

from . import platform as platform_module

from .paths import is_within, is_within_logical

# 诊断级别，数值越大越严重
LEVELS = {"note": 0, "warning": 1, "error": 2}

# 级别名到阈值的映射，供配置使用
LEVEL_NAMES = {"off": -1, "note": 0, "warning": 1, "error": 2}

Diagnostic = collections.namedtuple(
    "Diagnostic", "level source file line column message raw")

# 编译器告警：路径:行:列: 级别: 消息（列可省略）
_COMPILER = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?:(?P<column>\d+):)?\s*"
    r"(?P<level>fatal error|error|warning|note):\s*(?P<message>.*)$")

# valgrind 汇总：==PID== ERROR SUMMARY: N errors from M contexts
_VALGRIND = re.compile(
    r"^==\d+==\s+ERROR SUMMARY:\s*(?P<count>\d+)\s+errors?")



def _by_suffix(path, root):
    """按去掉前导 ./ 与 ../ 后的后缀在项目内找文件。

    编译器给出的相对路径是相对它自己的工作目录的，而那个目录随构建工具
    与子工程深度变化（qmake 影子构建常在 build/<子工程> 里编译，路径形如
    ../../src/…）。逐个猜基准目录既猜不全也不稳，因此直接按后缀在项目
    内查找：命中最长的后缀优先，避免同名文件互相牵连。
    """
    parts = []
    for piece in os.path.normpath(path).split(os.sep):
        if piece in ("", ".", ".."):
            continue
        parts.append(piece)
    for start in range(len(parts)):
        candidate = os.path.join(root, *parts[start:])
        if os.path.isfile(candidate):
            return os.path.normpath(os.path.abspath(candidate))
    return ""


def resolve_source(path, root=None, bases=None):
    """把诊断里的路径归一化成绝对路径，并判断是否属于项目。

    相对路径相对的是编译器的工作目录，而那个目录随构建工具变化，因此先按
    候选基准逐个尝试，再退回按后缀在项目内查找。返回 (绝对路径, 是否属于
    项目)。不做这一步就直接拿相对路径去比 SCOPE 前缀，项目自身的告警会被
    一律判成域外而静默。
    """
    text = str(path or "").strip()
    if not text:
        return "", False
    if os.path.isabs(text):
        resolved = os.path.normpath(text)
        if root:
            return resolved, is_within(resolved, root)
        return resolved, False
    if root:
        for base in (bases or [root]):
            if not base:
                continue
            candidate = os.path.normpath(os.path.join(base, text))
            # 候选路径可能并不存在，不能拿它去解软链，用逻辑比较
            if is_within_logical(candidate, root):
                return candidate, True
        found = _by_suffix(text, root)
        if found:
            return found, True
        return os.path.normpath(os.path.join(root, text)), False
    # 没有项目根时无从判断归属，只能归一化到当前目录
    return os.path.normpath(os.path.abspath(text)), False


def parse_diagnostic(line, root=None, bases=None):
    """解析一行，返回 Diagnostic；不是诊断则返回 None。

    给出 root 时按路径判断归属：落在 root 内算项目自身，否则算域外。
    bases 是相对路径的候选基准，缺省只有 root。
    """
    text = line.strip()
    match = _COMPILER.match(text)
    if match:
        source = "project"
        if root:
            path, inside = resolve_source(match.group("file"), root, bases)
            if not inside:
                source = "external"
        else:
            path = match.group("file")
        column = match.group("column")
        return Diagnostic(
            match.group("level"), source, path,
            int(match.group("line")),
            int(column) if column else 0,
            match.group("message"), text)
    match = _VALGRIND.search(text)
    if match:
        # 零错误不是诊断
        if int(match.group("count")) == 0:
            return None
        return Diagnostic("error", "external", "", 0, 0, text, text)
    return None


def collect_diagnostics(text, root=None, bases=None):
    """从整段输出里收集诊断，保持出现顺序。"""
    found = []
    for line in text.splitlines():
        item = parse_diagnostic(line, root, bases)
        if item is not None:
            found.append(item)
    return found


def dedup_diagnostics(items):
    """按 (文件, 行, 列, 消息) 去重，保留首次出现顺序。

    返回 [(诊断, 出现次数)]。
    """
    order, counts = [], {}
    for item in items:
        key = (item.file, item.line, item.column, item.message)
        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1
    seen, result = {}, []
    for item in items:
        key = (item.file, item.line, item.column, item.message)
        if key in seen:
            continue
        seen[key] = True
        result.append((item, counts[key]))
    return result


# 一条忽略正则最多允许跑多久（秒）。配置里的正则由使用者手写，写成像
# (a+)+$ 这种回溯爆炸式时，Python 的 re 会一直算下去——挂在**驱动层**，
# 阶段超时管不到它，整轮门禁就那么停在那里。超过上限就报错退出，把"卡住"
# 变成"看得见的失败"
IGNORE_PATTERN_SECONDS = 2.0


# 探测串：重复的几种常见字符 + 一个不匹配的结尾，足以引爆 (a+)+$ 这类回溯
# 长度取 48 个 a：实测 (a+)+$ 在 24 个 a 上约 0.9s（低于上限、拦不住），
# 在 48 个上远超上限。合法正则在这个规模上是微秒级，不会误报
PROBE_TEXT = ("a" * 48 + "0" * 8 + " " * 8 + os.sep * 8 + "." * 8 + "!")


class SlowPattern(platform_module.PatternTimeout):
    """一条忽略正则跑超时了。"""


def filter_diagnostics(items, level="error", sources="project", ignores=()):
    """按阈值、归属与忽略正则筛选诊断。

    level 为级别名，低于该级别的丢弃；sources 为 project 时只留项目自身的；
    ignores 里的正则命中则静默丢弃。

    忽略正则跑超时（回溯爆炸）时抛 SlowPattern，由调用方转成一条明确的配置
    错误——总比整轮门禁停在那里好。
    """
    threshold = LEVEL_NAMES.get(level, LEVEL_NAMES["error"])
    if threshold < 0:
        return []
    patterns = []
    for item in ignores:
        pattern = re.compile(item)
        # 先拿一小段"混合重复字符且必然不匹配"的字符串做限时探测：回溯爆炸的
        # 正则（(a+)+$ 之类）在这里就会暴露，不必等到真有诊断进来。长度取 64：
        # 正常正则哪怕慢，也不会在这个规模上跑到秒级
        platform_module.run_with_deadline(
            lambda p=pattern: p.search(PROBE_TEXT),
            IGNORE_PATTERN_SECONDS, error=SlowPattern)
        patterns.append(pattern)
    result = []
    for item in items:
        if LEVELS.get(item.level, 0) < threshold:
            continue
        if sources == "project" and item.source != "project":
            continue
        matched = False
        for pattern in patterns:
            # 原始行与消息都参与匹配，用户写哪一种都能命中
            hit = platform_module.run_with_deadline(
                lambda p=pattern, i=item: p.search(i.raw) or p.search(i.message),
                IGNORE_PATTERN_SECONDS, error=SlowPattern)
            if hit:
                matched = True
                break
        if matched:
            continue
        result.append(item)
    return result
