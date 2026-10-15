#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机器可读报告：内部 schema 与三个 writer。

报告是**旁路产物**：它只搬运阶段已经算出的结论（状态、诊断、失败、摘要），
不在 writer 里新增任何判定分支——否则终端与报告迟早会打架。schema 在
CONTRACT.md 登记，ci 与 standardize 两侧各有一份平行实现，共用同一份 JSON
定义而不共享代码。

三种格式：

    json   默认，直接落盘内部 schema，便于两次运行做 diff
    junit  每阶段一个 <testsuite>，逐例失败一个 <testcase>；三态靠 <skipped>
    sarif  每条诊断一条 result，带 ruleId 与 region；xfail 记为已知（已知问题
           既不是失败也不是通过）

所有取值都来自既有的阶段结果对象、协议行、归因结果与登记清单，报告只做搬运。
"""

import hashlib
import json
import os
import re
import sys

if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")

from . import tools, xfail
from .errors import KIND_SKIPPED, OK, kind
from .output import out as _stdout
from .stage import first_line, log_path, stage_view
from .version import CONTRACT_VERSION, __version__

# 内部 schema 的版本。schema 变化时自增，与工具版本、契约版本分开演进。
REPORT_VERSION = 1

# 支持的报告格式；多格式可逗号并列
FORMATS = ("json", "junit", "sarif")

# 多格式并列写文件时给各格式加的后缀；单格式直接写在 --report 指定的路径上
EXTENSIONS = {"json": "json", "junit": "xml", "sarif": "sarif"}

# 阶段三态。跳过既不是通过也不是失败，在报告里仍是独立一档。
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# 报告里的配置摘要：只取"两台机器取值不同就会结论不同"的关键项。取值全部来自
# 已生效的配置对象，不在这里重算任何推导。
DIGEST_KEYS = ("BUILD_TOOL", "SCOPE", "MEMCHECK_TOOL", "MEMCHECK_SCOPE",
               "DIAGNOSTIC_LEVEL", "DIAGNOSTIC_SOURCES", "SOURCE_ENCODING")

# 诊断消息尾部的检查名：clang-tidy 的 [check-name]、编译告警的 [-Wxxx]
_CHECK = re.compile(r"\[(?P<name>[^\[\]]+)\]\s*$")

_SARIF_LEVELS = {"error": "error", "warning": "warning", "note": "note"}


# ------------------------------------------------------------------ 取值搬运

def check_of(message):
    """从诊断消息里取尾部的检查名；取不到返回空串。"""
    match = _CHECK.search(message or "")
    return match.group("name") if match else ""


def status_of(result, skipped_stage):
    """阶段三态。超时算失败；跳过单独一档，不塌成通过。"""
    if skipped_stage or kind(result.code) == KIND_SKIPPED:
        return STATUS_SKIPPED
    if result.code == OK:
        return STATUS_PASSED
    return STATUS_FAILED


def diagnostic_entry(item, count, xfail_flag):
    """把一条归因后的诊断搬成报告条目。"""
    return {
        "level": item.level,
        "file": item.file,
        "line": item.line,
        "column": item.column,
        "message": item.message,
        "check": check_of(item.message),
        "owner": item.source,
        "count": count,
        "xfail": bool(xfail_flag),
    }


def summary_text(view):
    """阶段摘要：协议行的 #sum 加上诊断条数说明，与终端那行摘要同源。"""
    parts = list(view["sums"])
    if view["total"]:
        note = "{} 条诊断".format(len(view["diagnostics"]))
        details = []
        if view["merged"]:
            details.append("合并重复 {} 条".format(view["merged"]))
        if view["dropped"]:
            details.append("过滤 {} 条".format(view["dropped"]))
        if details:
            note += "（" + "，".join(details) + "）"
        parts.append(note)
    return " | ".join(parts)


def tool_info():
    """tool 段：本侧版本与契约版本。

    两侧不 import 对方，"standardize" 那一格只有由 standardize 产出的报告才
    填得上；ci 侧留空，形状两侧一致。
    """
    return {"ci": __version__, "standardize": "", "contract": CONTRACT_VERSION}


def environment():
    """环境段：Python 版本与 clang 两件套的实际路径与版本。

    报告要带来源指纹，用于排查"同一份代码在两台机器上结论不同"。ci 侧虽然
    不跑 clang，但**诊断是编译器产出的**——报告里没有编译器身份，跨机差异就
    答不全。这里直接解析，解析不到留空（报告是旁路产物，不因环境缺件而失败）。
    """
    values = {
        "python": "{}.{}.{}".format(*sys.version_info[:3]),
        "clang_format": "",
        "clang_version": "",
    }
    path = _find_binary(("clang-format", "clang-format-17"))
    if path:
        values["clang_format"] = path
        values["clang_version"] = _first_line(
            tools.capture([path, "--version"])[1])
    return values


def _find_binary(names):
    """PATH 上第一个可执行的候选，找不到返回空串。"""
    for name in names:
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if not entry:
                continue
            candidate = os.path.join(entry, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return ""


def _first_line(captured):
    text = captured.decode("utf-8", "replace") if isinstance(captured, bytes) \
        else str(captured or "")
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def config_digest(config):
    """生效配置的关键取值，用于排查"同一份代码在两台机器上结论不同"。"""
    values = {}
    for key in DIGEST_KEYS:
        if key == "SCOPE":
            values[key] = getattr(config, "scope", None)
        else:
            values[key] = getattr(config, key.lower(), None)
    return values


def baseline(config, checklist):
    """基线段：登记清单的路径与条目数。排版指纹属于 standardize，ci 侧留空。"""
    count = 0
    if checklist is not None:
        count = len(getattr(checklist.registry, "entries", None) or [])
    return {
        "format_fingerprint": "",
        "xfail_file": getattr(config, "xfail_file", "") or "",
        "xfail_count": count,
    }


def _checklist(config):
    """读登记清单用于标注与计数；报告是旁路产物，读不出就当没有清单。"""
    try:
        checklist, _code, _message = xfail.load_checklist(config)
    except (IOError, OSError, ValueError):
        return None
    return checklist


def build(config, records, skipped=()):
    """把阶段结果搬成内部 schema 的报告字典。

    records 是 stage.Result 的序列；skipped 是当前机器上不可用的阶段名集合。
    """
    skipped = set(skipped or ())
    checklist = _checklist(config)
    stages = []
    for result in records:
        view = stage_view(config, result)
        stage_skipped = (result.name in skipped
                         or kind(result.code) == KIND_SKIPPED)
        diagnostics = [diagnostic_entry(item, count, False)
                       for item, count in view["diagnostics"]]
        # 失败清单来自两个通道：#fail 是真要修的，#xfail 是已登记的存量。
        # 报告**不**拿 checklist 重判——判定由阶段负责，否则终端与报告迟早
        # 各说各的。已登记项照样进清单，只是标成"已知"：平台面板因此能把它
        # 显示成"已知"，而不是"失败"或"通过"
        failures = []
        for item in view.get("knowns") or ():
            case = item[0] if isinstance(item, tuple) else item.case
            reason = item[1] if isinstance(item, tuple) else item.reason
            failures.append({"case": case, "reason": reason, "xfail": True})
        for item in view["fails"]:
            failures.append({"case": item.case, "reason": item.reason,
                             "xfail": False})
        reason = ""
        if stage_skipped:
            reason = (first_line(result.err) or first_line(result.out)
                      or "未说明原因")
        stages.append({
            "name": result.name,
            "status": status_of(result, stage_skipped),
            "seconds": round(float(result.elapsed), 3),
            "timed_out": bool(result.timed_out),
            "diagnostics": diagnostics,
            "failures": failures,
            "summary": summary_text(view),
            "skipped_reason": reason,
            "log": log_path(config, result.name),
        })
    return {
        "report_version": REPORT_VERSION,
        "tool": tool_info(),
        "environment": environment(),
        "config_digest": config_digest(config),
        "baseline": baseline(config, checklist),
        "stages": stages,
    }


# ------------------------------------------------------------------ JSON

def to_json(document):
    """内部 schema 的 JSON 文本。排序输出，两次运行的报告可逐行 diff。"""
    return json.dumps(document, indent=2, sort_keys=True,
                      ensure_ascii=False) + "\n"


# ------------------------------------------------------------------ JUnit

def _xml(text):
    """XML 文本转义（含属性用的引号）。"""
    text = str(text if text is not None else "")
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;")
            .replace("'", "&apos;"))


def _junit_suite(stage):
    """一个阶段的 <testsuite>，返回 (文本, 计数)。

    JUnit 只承载逐例失败：诊断归 SARIF，不在这里重复，第三方（owner 非
    project）的诊断因此不会淹掉面板。三态里的"跳过"用 <skipped> 表达，
    xfail 登记项也走 <skipped>——已知问题既不是 failure 也不是 pass。
    """
    name = _xml(stage["name"])
    status = stage.get("status")
    seconds = float(stage.get("seconds") or 0.0)
    cases = []
    counts = {"tests": 1, "failures": 0, "skipped": 0}
    if status == STATUS_SKIPPED:
        counts["skipped"] = 1
        cases.append(
            '    <testcase name="{}" classname="cxx_ci">\n'
            '      <skipped message="{}"/>\n'
            '    </testcase>\n'.format(
                name, _xml(stage.get("skipped_reason") or "未说明原因")))
    else:
        fails = stage.get("failures") or []
        if not fails:
            # 没有逐例失败：通过给一条通过用例，失败给一条失败用例，否则
            # 面板会把一个失败的阶段看成一例"通过"
            if status == STATUS_FAILED:
                counts["failures"] = 1
                cases.append(
                    '    <testcase name="{}" classname="cxx_ci">\n'
                    '      <failure message="阶段失败">{}</failure>\n'
                    '    </testcase>\n'.format(
                        name, _xml(stage.get("summary") or "阶段失败")))
            else:
                cases.append(
                    '    <testcase name="{}" classname="cxx_ci"/>\n'.format(
                        name))
        else:
            counts["tests"] = len(fails)
            for item in fails:
                case = _xml(item.get("case") or "")
                reason = _xml(item.get("reason") or "")
                if item.get("xfail"):
                    counts["skipped"] += 1
                    cases.append(
                        '    <testcase name="{}" classname="{}">\n'
                        '      <skipped message="已登记 (xfail): {}"/>\n'
                        '    </testcase>\n'.format(case, name, reason))
                else:
                    counts["failures"] += 1
                    cases.append(
                        '    <testcase name="{}" classname="{}">\n'
                        '      <failure message="{}">{}</failure>\n'
                        '    </testcase>\n'.format(case, name, reason, reason))
    summary = stage.get("summary") or ""
    system_out = ('    <system-out>{}</system-out>\n'.format(_xml(summary))
                  if summary else "")
    text = ('  <testsuite name="{}" tests="{}" failures="{}" skipped="{}" '
            'time="{:.3f}">\n{}{}  </testsuite>\n').format(
                name, counts["tests"], counts["failures"], counts["skipped"],
                seconds, "".join(cases), system_out)
    return text, counts


def to_junit(document):
    suites = []
    tests = failures = skipped = 0
    total_time = 0.0
    for stage in document["stages"]:
        text, counts = _junit_suite(stage)
        suites.append(text)
        tests += counts["tests"]
        failures += counts["failures"]
        skipped += counts["skipped"]
        total_time += float(stage.get("seconds") or 0.0)
    header = ('<?xml version="1.0" encoding="UTF-8"?>\n'
              '<testsuites name="cxx_ci" tests="{}" failures="{}" skipped="{}" '
              'time="{:.3f}">\n').format(tests, failures, skipped, total_time)
    return header + "".join(suites) + "</testsuites>\n"


# ------------------------------------------------------------------ SARIF

def _fingerprint(path, check, message):
    """跨运行可比的部分指纹：文件 + 检查名 + 消息摘要。

    同一处告警在两次运行里给出同一条指纹，平台才能认出"还是那一条"，而不是
    每次都当新问题。行号不进材料——行号随无关改动漂移，会让同一条告警变脸。
    """
    material = "\n".join([path or "", check or "", message or ""])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _sarif_result(item):
    path = item.get("file") or ""
    check = item.get("check") or ""
    message = item.get("message") or ""
    result = {
        "ruleId": check,
        "level": _SARIF_LEVELS.get(item.get("level"), "warning"),
        "message": {"text": message},
        "partialFingerprints": {"cxx_ci/v1": _fingerprint(path, check, message)},
        "properties": {
            "owner": item.get("owner") or "",
            "count": item.get("count", 1),
        },
    }
    if path:
        location = {"physicalLocation": {"artifactLocation": {"uri": path}}}
        region = {}
        line = int(item.get("line") or 0)
        column = int(item.get("column") or 0)
        if line > 0:
            region["startLine"] = line
        if column > 0:
            region["startColumn"] = column
        if region:
            location["physicalLocation"]["region"] = region
        result["locations"] = [location]
    if item.get("xfail"):
        # 已知问题：平台据此显示为"既非失败也非通过"
        result["baselineState"] = "unchanged"
        result["properties"]["knownIssue"] = True
    return result


def to_sarif(document):
    results = []
    for stage in document["stages"]:
        for item in stage.get("diagnostics") or []:
            results.append(_sarif_result(item))
    run = {
        "tool": {"driver": {
            "name": "cxx_ci",
            "version": __version__,
            "informationUri": "",
        }},
        "results": results,
        "properties": {
            "contract": CONTRACT_VERSION,
            "report_version": REPORT_VERSION,
        },
    }
    schema = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [run],
    }
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------ 落盘

def render(document, fmt):
    """按格式渲染报告文本。"""
    if fmt == "json":
        return to_json(document)
    if fmt == "junit":
        return to_junit(document)
    if fmt == "sarif":
        return to_sarif(document)
    raise ValueError("未知报告格式: " + str(fmt))


def write(document, target, formats):
    """写报告，返回 (已写路径列表, 问题说明列表)。

    写失败只回问题，不改门禁结论——报告是旁路产物，目录不可写不该把一次
    通过的构建判成失败。target 为 "-" 时打到标准输出。
    """
    written, problems = [], []
    if target == "-":
        for fmt in formats:
            _stdout(render(document, fmt).rstrip("\n"))
        return ["<stdout>"], problems
    for fmt in formats:
        path = target if len(formats) == 1 else target + "." + EXTENSIONS.get(
            fmt, fmt)
        try:
            directory = os.path.dirname(path)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory)
            handle = open(path, "w", encoding="utf-8")
            try:
                handle.write(render(document, fmt))
            finally:
                handle.close()
        except (IOError, OSError, ValueError, TypeError) as exc:
            problems.append("写报告失败 {}: {}".format(path, exc))
            continue
        written.append(path)
    return written, problems
