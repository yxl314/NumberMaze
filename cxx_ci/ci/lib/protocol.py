#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段脚本与驱动层之间的输出契约。

阶段脚本在原始输出里额外打印协议行，人读仍是普通文本；驱动层只认协议行，
不再对全文跑关键字正则。这样做的理由是可判定性：生成侧知道自己在跑什么
（编译器、测试框架、valgrind），判定因此来自结构而不是子串猜测。

协议行：
    #diag error: <文件>:<行>:<列>: <消息>
    #diag warn : <文件>:<行>:<列>: <消息>
    #fail: <用例> <原因>
    #sum: <一行摘要>
"""

import collections
import re

# 协议前缀
DIAG = "#diag"
FAIL = "#fail"
SUM = "#sum"
# 已登记的存量问题。`#sum` 是给人看的回显，机器要判"这条算不算失败"不能去
# 解析摘要文本——那等于让报告重新判一次。单独给一条协议行，报告只搬运
XFAIL = "#xfail"

# 协议级别名到内部级别的映射，warn 与 warning 都接受
LEVEL_ALIASES = {"error": "error", "warn": "warning", "warning": "warning",
                 "note": "note"}

Diag = collections.namedtuple("Diag", "level file line column message")
Fail = collections.namedtuple("Fail", "case reason")
Known = collections.namedtuple("Known", "case reason")

_DIAG = re.compile(r"^#diag\s+(?P<level>\w+)\s*:\s*(?P<rest>.*)$")
_LOCATION = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?:(?P<column>\d+):)?\s*(?P<message>.*)$")
_FAIL = re.compile(r"^#fail\s*:\s*(?P<case>\S*)\s*(?P<reason>.*)$")
_SUM = re.compile(r"^#sum\s*:\s*(?P<text>.*)$")
_XFAIL = re.compile(r"^#xfail\s*:\s*(?P<case>\S*)\s*(?P<reason>.*)$")


def diag(level, path, line, column, message):
    """生成一条诊断协议行。"""
    return "{} {}: {}:{}:{}: {}".format(DIAG, level, path, line, column,
                                        message)


def fail(case, reason):
    """生成一条失败用例协议行。"""
    return "{}: {} {}".format(FAIL, case, reason).rstrip()


def summary(text):
    """生成一条摘要协议行。"""
    return "{}: {}".format(SUM, text)


def known(case, reason):
    """生成一条"已登记的存量问题"协议行。

    与 `#fail` 同形但语义相反：它既不是失败也不是通过。报告拿它给条目打
    "已知"标记，平台面板于是能把它显示成"已知"而不是"失败"。
    """
    return "{}: {} {}".format(XFAIL, case, reason).rstrip()


def parse_diag(line):
    """解析一行诊断协议，不是则返回 None。"""
    match = _DIAG.match(line.strip())
    if not match:
        return None
    level = LEVEL_ALIASES.get(match.group("level").lower())
    if level is None:
        return None
    where = _LOCATION.match(match.group("rest").strip())
    if not where:
        return None
    column = where.group("column")
    return Diag(level, where.group("file"), int(where.group("line")),
                int(column) if column else 0, where.group("message"))


def parse_fail(line):
    """解析一行失败用例协议，不是则返回 None。"""
    match = _FAIL.match(line.strip())
    if not match:
        return None
    return Fail(match.group("case"), match.group("reason"))


def parse_known(line):
    """解析一行"已登记"协议，不是则返回 None。"""
    match = _XFAIL.match(line.strip())
    if not match:
        return None
    return Known(match.group("case") or "", match.group("reason").strip())


def parse_sum(line):
    """解析一行摘要协议，不是则返回 None。"""
    match = _SUM.match(line.strip())
    if not match:
        return None
    return match.group("text")


def plain(line):
    """把一行协议行还原成人读文本，非协议行原样返回。

    协议前缀是阶段与驱动之间的内部通道，不该出现在面向人的输出里；还原时
    保留级别与位置，语义不丢。日志仍记原始协议行，便于逐字核对。
    """
    item = parse_diag(line)
    if item is not None:
        return "{}:{}:{}: {}: {}".format(item.file, item.line, item.column,
                                         item.level, item.message)
    item = parse_fail(line)
    if item is not None:
        return (item.case + " " + item.reason).rstrip()
    text = parse_sum(line)
    if text is not None:
        return text
    return line


def plain_text(text):
    """逐行剥掉协议前缀，其余原样保留（缩进与空行都不动）。"""
    return "\n".join(plain(line) for line in text.splitlines())


def parse(text):
    """从整段输出里提取协议记录，返回 (诊断, 失败, 已登记, 摘要)。"""
    diags, fails, knowns, sums = [], [], [], []
    for line in text.splitlines():
        item = parse_diag(line)
        if item is not None:
            diags.append(item)
            continue
        item = parse_fail(line)
        if item is not None:
            fails.append(item)
            continue
        item = parse_known(line)
        if item is not None:
            knowns.append(item)
            continue
        item = parse_sum(line)
        if item is not None:
            sums.append(item)
    return diags, fails, knowns, sums
