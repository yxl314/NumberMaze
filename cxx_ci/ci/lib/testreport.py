#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从测试程序输出里提取逐例失败。

有的 runner 配置下逐例失败而进程退出 0，只看退出码会把整阶段误判为通过，
这是最危险的一类漏报。这里把"输出里的失败"变成结构化结果，判定就不再
只依赖退出码。

框架名只是适配器表的键：每种框架自带指纹、失败行与总数行的规则。新增框架
往表里加一项即可，不在别处散落 if 分支；regex 与 exit-code 是两条不靠
指纹的兜底路径，前者接自研 runner，后者保留旧的退出码语义。

一个 runner 常把多个测试可执行文件串起来跑（如 qmake 的多套件 tests），
每跑完一个就打印一段汇总。适配器对汇总行**逐段累加**，只取首段会把用例
总数报成单个可执行文件的规模，与真实规模差一个数量级。
"""

import collections
import re

from . import protocol
from .errors import (OK, TEST_REPORT_BAD_ADAPTER,
                     TEST_REPORT_BAD_PATTERN)

# 适配器名，按 auto 的试探顺序排列
ADAPTERS = ("auto", "gtest", "qtest", "catch2", "regex", "exit-code")

# 一条逐例失败
# 与协议行同型：解析结果最终要发成 #fail 协议行，不另造一个类型
Failure = protocol.Fail

# 汇总结果。suites 为 0 表示框架没有给出套件概念（如 regex 适配器），
# 此时展示层不报套件数，而不是编一个 1 出来。
Totals = collections.namedtuple("Totals", "cases suites")

# 框架适配器的三个动作：指纹、取失败、取汇总
_Framework = collections.namedtuple("_Framework", "probe failures total")

# ------------------------------------------------------------------- 版式
#
# 失败行要容忍用例名带括号、参数化后缀与 trailing 的 where 子句；原因是
# 给人看的，取断言首行说明即可。指纹用 search 逐框架试探，因此都开
# MULTILINE，让 ^ 匹配任意行的行首。

_GTEST_PROBE = re.compile(r"^\[=+\]|^\[\s+(?:RUN|OK|FAILED)\s+\]",
                          re.MULTILINE)
_GTEST_FAIL = re.compile(
    r"^\[\s+FAILED\s+\]\s+(?P<case>[A-Za-z_][\w./-]*)"
    r"(?:\s+\([^)]*\))?(?:,\s*where\b.*)?\s*$")
_GTEST_ASSERT = re.compile(r"^(?P<loc>\S.*?:\d+):\s*Failure\s*$")
# 只认 gtest 自己的标记行：用例块里测试打印的行可能以 "[" 开头（如 "[debug] ..."），
# 不能一见方括号就当边界
_GTEST_MARK = re.compile(r"^\[\s+(?:RUN|OK|FAILED)\s+\]|^\[[-=]")
_GTEST_TOTAL = re.compile(
    r"^\[=+\]\s+(?P<total>\d+)\s+tests?\s+from\s+(?P<suites>\d+)\s+"
    r"test\s+(?:suites?|cases?)\s+ran\.", re.MULTILINE)

_QT_PROBE = re.compile(
    r"^\*{5,}\s*Start testing|^FAIL!\s+:|^Totals:\s*\d+\s+passed",
    re.MULTILINE)
_QT_FAIL = re.compile(
    r"^\s*FAIL!\s*:\s*(?P<case>\S+?\([^)]*\)|\S+)\s*(?P<reason>.*)$")
_QT_TOTAL = re.compile(r"^Totals:\s*(?P<body>.*)$", re.MULTILINE)
_QT_TOTAL_ITEM = re.compile(r"(\d+)\s+(passed|failed|skipped|blacklisted)")

_CATCH_PROBE = re.compile(
    r"^~{20,}$|^\.{20,}$|^test cases:\s*\d+|^\s*\S+:\d+:\s*FAILED:",
    re.MULTILINE)
_CATCH_RULE = re.compile(r"^-{20,}$")
_CATCH_FAIL = re.compile(
    r"^\s*(?P<file>\S.*?):(?P<line>\d+):\s*FAILED:\s*(?P<rest>.*)$")
_CATCH_TOTAL = re.compile(
    r"^test cases:\s*(?P<total>\d+)\s*\|"
    r"|^All tests passed \(\d+ assertions? in (?P<all>\d+) test cases?\)",
    re.MULTILINE)


# --------------------------------------------------------------- 逐框架实现

def _gtest_failures(text):
    """gtest 的逐例失败。

    末尾 "listed below" 会把失败用例再列一遍，因此按用例名去重；原因取
    该用例断言块的来源与首行说明。
    """
    lines = text.splitlines()
    failures, seen = [], set()
    for index, line in enumerate(lines):
        match = _GTEST_FAIL.match(line)
        if not match:
            continue
        case = match.group("case")
        if case in seen:
            continue
        seen.add(case)
        failures.append(Failure(case, _gtest_reason(lines, index)))
    return failures


def _gtest_reason(lines, index):
    """向上找最近的断言头，拼出来源与该块的首行说明。"""
    location, header = None, -1
    for back in range(index - 1, -1, -1):
        line = lines[back].strip()
        if not line:
            continue
        match = _GTEST_ASSERT.match(line)
        if match:
            location, header = match.group("loc"), back
            break
        if _GTEST_MARK.match(line):
            break  # 撞到 RUN/OK/汇总行，说明没有断言块
    if location is None:
        return ""
    for forward in range(header + 1, index):
        line = lines[forward].strip()
        if line:
            return location + ": " + line
    return location


def _qtest_failures(text):
    """qtest 的逐例失败，原因取 FAIL! 行冒号后的剩余文本。"""
    failures = []
    for line in text.splitlines():
        match = _QT_FAIL.match(line)
        if match:
            failures.append(Failure(match.group("case"),
                                    match.group("reason").strip()))
    return failures


def _catch2_failures(text):
    """catch2 的逐例失败。

    失败行只有来源与断言，没有用例名；用例名在上一个 "---" 分隔的标题块
    里，多行表示嵌套 section，按 " / " 拼起来。标题块缺失时退回来源。
    """
    lines = text.splitlines()
    failures, seen, header, case = [], set(), None, None
    for index, line in enumerate(lines):
        if _CATCH_RULE.match(line):
            if header is None:
                header = []
            else:
                if header:
                    case = " / ".join(header)
                header = None
            continue
        if header is not None:
            piece = line.strip()
            if piece:
                header.append(piece)
            continue
        match = _CATCH_FAIL.match(line)
        if not match:
            continue
        name = case or match.group("file")
        if name in seen:
            continue
        seen.add(name)
        reason = match.group("rest").strip()
        if not reason:
            reason = _catch2_expression(lines, index)
        failures.append(Failure(name, reason))
    return failures


def _catch2_expression(lines, index):
    """失败行下面第一个非空行就是断言表达式。"""
    for forward in range(index + 1, len(lines)):
        line = lines[forward].strip()
        if not line:
            continue
        if _CATCH_RULE.match(line) or line.startswith("with expansion"):
            return ""
        return line
    return ""


def _gtest_total(text):
    """累加每一段汇总行。

    汇总行由框架每个测试进程各打印一行；把多个可执行文件串起来跑（多套件
    runner 的常态）就会有多行。只取第一行会把用例总数报成单个进程的规模，
    与真实规模差一个数量级。套件数取框架自报的 "from M test suites"。
    """
    cases, suites, found = 0, 0, False
    for match in _GTEST_TOTAL.finditer(text):
        found = True
        cases += int(match.group("total"))
        suites += int(match.group("suites"))
    return Totals(cases, suites) if found else None


def _qtest_total(text):
    """累加全部 Totals 行。

    Totals 行分别给 passed/failed/skipped/blacklisted，只有求和才是该行的
    用例总数；多套件 runner 会打印多行，必须逐行求和。套件数就是汇总行条数
    ——QTest 的 Totals 一行对应一个测试可执行文件，框架不再单独报套件数。
    """
    cases, suites = 0, 0
    for match in _QT_TOTAL.finditer(text):
        counts = _QT_TOTAL_ITEM.findall(match.group("body"))
        if not counts:
            continue
        cases += sum(int(value) for value, _ in counts)
        suites += 1
    return Totals(cases, suites) if suites else None


def _catch2_total(text):
    """累加全部汇总行，套件数取汇总行条数。"""
    cases, suites = 0, 0
    for match in _CATCH_TOTAL.finditer(text):
        cases += int(match.group("total") or match.group("all"))
        suites += 1
    return Totals(cases, suites) if suites else None


# 适配器名到探测规则的映射，顺序即 auto 的试探顺序
_FRAMEWORKS = collections.OrderedDict((
    ("gtest", _Framework(_GTEST_PROBE, _gtest_failures, _gtest_total)),
    ("qtest", _Framework(_QT_PROBE, _qtest_failures, _qtest_total)),
    ("catch2", _Framework(_CATCH_PROBE, _catch2_failures, _catch2_total)),
))


# --------------------------------------------------------------- 公开接口

def fingerprint(text):
    """返回命中的框架名与依据，供诊断输出说明判定来源。

    都不命中时返回 (None, "")。依据取指纹命中的那一段文本，便于解释
    为什么判成这个框架。
    """
    for name, rules in _FRAMEWORKS.items():
        match = rules.probe.search(text)
        if match:
            return name, match.group(0).strip()
    return None, ""


def detect(text):
    """按序试探各框架指纹，返回首个命中的适配器名；都不命中返回 "exit-code"。"""
    return fingerprint(text)[0] or "exit-code"


def extract(text, adapter, fail_pattern=None, total_pattern=None):
    """返回 (失败列表, 汇总或 None, 错误码, 说明)。

    adapter 为 "auto" 时先 detect；"regex" 用传入的两个正则；
    "exit-code" 不解析逐例结果，返回空列表。有失败时错误码是 TEST_FAILED，
    正则或适配器名非法时是 CONFIG_BAD_VALUE，其余是 OK。汇总列缺失不算
    出错，返回 None；汇总与展示文案见 describe。
    """
    if adapter == "auto":
        adapter = detect(text)
    if adapter == "regex":
        return _extract_regex(text, fail_pattern, total_pattern)
    rules = _FRAMEWORKS.get(adapter)
    if rules is None:
        if adapter == "exit-code":
            # 不解析逐例结果，成败完全交给退出码
            return [], None, OK, ""
        return [], None, TEST_REPORT_BAD_ADAPTER, "未知的测试报告适配器: " + str(adapter)
    failures = rules.failures(text)
    return _result(adapter, failures, rules.total(text))


def describe(totals, failures):
    """汇总文案：用例数、套件数与失败数。

    套件数为 0 表示该适配器没有套件概念，此时不显示套件段，而不是报一个
    恒为 1 的假数字。展示文案只此一处定义，阶段脚本与库调用方共用。
    """
    if totals.suites:
        return "共 {} 例（{} 套件），失败 {} 例".format(
            totals.cases, totals.suites, failures)
    return "共 {} 例，失败 {} 例".format(totals.cases, failures)


def _extract_regex(text, fail_pattern, total_pattern):
    """自研 runner：失败正则必需，总数正则可选。

    用例名与原因优先取命名组 case/reason，其次取前两个捕获组，只有一个
    捕获组时它就是用例名。不猜版式，命中一行即一例。总数正则只给用例总数，
    自研 runner 没有套件概念，因此套件数记 0。
    """
    if not fail_pattern:
        return [], None, TEST_REPORT_BAD_PATTERN, "regex 适配器缺少 fail_pattern"
    pattern, code, message = _compile(fail_pattern, "失败")
    if code != OK:
        return [], None, code, message
    failures = []
    for line in text.splitlines():
        match = pattern.search(line)
        if match:
            case, reason = _groups(match)
            if case:
                failures.append(Failure(case, reason))
    totals = None
    if total_pattern:
        pattern, code, message = _compile(total_pattern, "总数")
        if code != OK:
            return [], None, code, message
        match = pattern.search(text)
        if match:
            totals = Totals(_number(match), 0)
    return _result("regex", failures, totals)


def _groups(match):
    named = match.groupdict()
    if named.get("case") is not None:
        return named["case"].strip(), (named.get("reason") or "").strip()
    if match.re.groups >= 2:
        return (match.group(1) or "").strip(), (match.group(2) or "").strip()
    if match.re.groups == 1:
        return (match.group(1) or "").strip(), ""
    return match.group(0).strip(), ""


def _number(match):
    named = match.groupdict()
    if named.get("total"):
        return int(named["total"])
    for value in match.groups():
        if value and value.isdigit():
            return int(value)
    found = re.search(r"\d+", match.group(0))
    return int(found.group(0)) if found else None


def _compile(pattern, what):
    """编译调用方给的正则。

    非法正则只报错不抛出：配置里的笔误不该让阶段脚本崩掉，按约定返回
    错误码由调用方统一汇报。
    """
    try:
        return re.compile(pattern), OK, ""
    except re.error as exc:
        return None, TEST_REPORT_BAD_PATTERN, "{}正则无效: {}".format(what, exc)


def _result(adapter, failures, totals):
    if not failures:
        return failures, totals, OK, ""
    if totals is None:
        message = "{}: {} 例失败".format(adapter, len(failures))
    else:
        message = "{}: {}".format(adapter, describe(totals, len(failures)))
    # 解析成功即返回 OK：用例失败由调用方看失败列表判定，
    # 把"解析失败"与"用例失败"揉进同一个码会让调用方分不清
    return failures, totals, OK, message
