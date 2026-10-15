"""机器可读报告：内部 schema 与三个 writer（与 ci 侧同形状的平行实现）。

报告是**旁路产物**：它只搬运动作已经算出的结论（状态、诊断、"需人工"清单、
摘要），不在 writer 里新增任何判定分支——否则终端与报告迟早会打架。schema 在
CONTRACT.md 登记，ci 与 standardize 两侧各有一份平行实现，共用同一份 JSON 定义
而不共享代码。

三种格式：

    json   默认，直接落盘内部 schema，便于两次运行做 diff
    junit  每个动作一个 <testsuite>，"需人工"清单逐条一个 <testcase>；三态靠
           <skipped>
    sarif  每条诊断一条 result，带 ruleId 与 region；xfail 记为已知（已知问题
           既不是失败也不是通过）

与本项目其余部分不同，这里保留协议化的形状（键名、writer 文本）与 ci 侧逐字
对齐：报告要跨两个独立子项目可比较，形状是契约的一部分，不随本侧风格漂移。
"""

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field

from . import CONTRACT_VERSION, __version__, baseline as baseline_mod, fmt, log
from . import xfail
from .errors import Code

# 内部 schema 的版本。schema 变化时自增，与工具版本、契约版本分开演进。
REPORT_VERSION = 1

# 支持的报告格式；多格式可逗号并列
FORMATS = ("json", "junit", "sarif")

# 多格式并列写文件时给各格式加的后缀；单格式直接写在 --report 指定的路径上
EXTENSIONS = {"json": "json", "junit": "xml", "sarif": "sarif"}

# 动作三态。本项目没有"跳过"这一档，保留常量以便与 ci 侧 schema 同形。
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# 报告里的配置摘要：形状与 ci 侧一致（同一组键），键名固定、取值按本侧配置
# 对象映射，两侧对读时能逐键比对。
DIGEST_KEYS = ("BUILD_TOOL", "SCOPE", "MEMCHECK_TOOL", "MEMCHECK_SCOPE",
               "DIAGNOSTIC_LEVEL", "DIAGNOSTIC_SOURCES", "SOURCE_ENCODING")

_SARIF_LEVELS = {"error": "error", "warning": "warning", "note": "note"}


# ------------------------------------------------------------------ 结论搬运

@dataclass
class Stage:
    """一个动作（style / format）的阶段结论，报告只搬运它。"""

    name: str
    status: str = STATUS_PASSED
    seconds: float = 0.0
    timed_out: bool = False
    diagnostics: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    summary: str = ""
    skipped_reason: str = ""
    # 工具不提供修复块的诊断条数。单列出来而不是只写进 summary 文本：平台要能
    # 单独取数，"改不动"才不会看起来像"没干活"
    unfixable: int = 0

    def add_diagnostic(self, file, line, column, level, message, check,
                       owner="", count=1, xfail=False):
        """搬进一条诊断。取值全部来自既有的 Finding 与登记清单，不做推理。"""
        self.diagnostics.append({
            "level": str(level),
            "file": str(file),
            "line": int(line),
            "column": int(column),
            "message": message,
            "check": check,
            "owner": owner,
            "count": count,
            "xfail": bool(xfail),
        })

    def add_failure(self, case, reason, xfail=False):
        """搬进一条"需人工/无法自动完成"的条目（对应 ci 的逐例失败）。"""
        self.failures.append({"case": case, "reason": reason,
                              "xfail": bool(xfail)})


class Collector:
    """收集各动作的结论，供 build 搬进报告。

    各动作函数把结构化结果投喂给这里；阶段状态由调用方按错误码收口（close），
    报告自身绝不重新判定。
    """

    def __init__(self):
        self.stages: list = []
        # 解析到的 clang 工具，供 environment 段使用；未解析时为 None
        self.tools = None

    def stage(self, name):
        """取某动作的阶段对象，没有就新建。"""
        for stage in self.stages:
            if stage.name == name:
                return stage
        stage = Stage(name)
        self.stages.append(stage)
        return stage

    def close(self, name, code, seconds):
        """按错误码收口一个阶段：状态只由此处的 code 决定。"""
        stage = self.stage(name)
        stage.status = STATUS_PASSED if code == Code.OK else STATUS_FAILED
        stage.seconds = seconds
        return stage


def tool_info():
    """tool 段：本侧版本与契约版本。

    两侧不 import 对方，"ci" 那一格只有由 ci 产出的报告才填得上；本侧留空，
    形状两侧一致。
    """
    return {"ci": "", "standardize": __version__, "contract": CONTRACT_VERSION}


def environment(tools=None):
    """环境段：Python 版本与解析到的 clang-format。键名与 ci 侧一致。"""
    binary = getattr(tools, "clang_format", None) if tools is not None else None
    return {
        "python": "{}.{}.{}".format(*sys.version_info[:3]),
        "clang_format": str(binary) if binary else "",
        "clang_version": fmt.version_text(binary) if binary else "",
    }


def config_digest(config):
    """生效配置的关键取值，用于排查"同一份代码在两台机器上结论不同"。

    键名固定为 ci 侧那一组；本侧没有的项（内存检查相关）留 None，形状不塌。
    """
    scope = {
        "project": [str(item)
                    for item in (getattr(config, "scope_roots", None) or [])],
        "excluded": [str(item)
                     for item in (getattr(config, "scope_excluded", None) or [])],
    }
    values = {
        "BUILD_TOOL": getattr(config, "build_tool", None),
        "SCOPE": scope,
        "MEMCHECK_TOOL": None,
        "MEMCHECK_SCOPE": None,
        "DIAGNOSTIC_LEVEL": None,
        "DIAGNOSTIC_SOURCES": None,
        "SOURCE_ENCODING": getattr(config, "source_encoding", None),
    }
    return {key: values.get(key) for key in DIGEST_KEYS}


def baseline(config):
    """基线段：排版指纹与登记清单的路径、条目数。"""
    fingerprint = ""
    record, code, _message = baseline_mod.read(getattr(config, "log_dir", None))
    if code == Code.OK and record is not None:
        fingerprint = str(record.get("fingerprint", "") or "")
    count = 0
    checklist, ccode, _message = xfail.load_checklist(config)
    if checklist is not None:
        count = len(checklist.registry.entries)
    return {
        "format_fingerprint": fingerprint,
        "xfail_file": str(getattr(config, "xfail_file", "") or ""),
        "xfail_count": count,
    }


def build(config, collector):
    """把收集到的阶段结论搬成内部 schema 的报告字典。"""
    stages = []
    for stage in collector.stages:
        stages.append({
            "name": stage.name,
            "status": stage.status,
            "seconds": round(float(stage.seconds), 3),
            "timed_out": bool(stage.timed_out),
            "diagnostics": stage.diagnostics,
            "failures": stage.failures,
            "summary": stage.summary,
            "skipped_reason": stage.skipped_reason,
            "unfixable": int(getattr(stage, "unfixable", 0) or 0),
            "log": log.log_path(config, stage.name),
        })
    return {
        "report_version": REPORT_VERSION,
        "tool": tool_info(),
        "environment": environment(getattr(collector, "tools", None)),
        "config_digest": config_digest(config),
        "baseline": baseline(config),
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
    """一个动作的 <testsuite>，返回 (文本, 计数)。

    JUnit 只承载逐条"需人工"，诊断归 SARIF，不在这里重复。xfail 登记项走
    <skipped>——已知问题既不是 failure 也不是 pass。
    """
    name = _xml(stage["name"])
    status = stage.get("status")
    seconds = float(stage.get("seconds") or 0.0)
    cases = []
    counts = {"tests": 1, "failures": 0, "skipped": 0}
    if status == STATUS_SKIPPED:
        counts["skipped"] = 1
        cases.append(
            '    <testcase name="{}" classname="cxx_standardize">\n'
            '      <skipped message="{}"/>\n'
            '    </testcase>\n'.format(
                name, _xml(stage.get("skipped_reason") or "未说明原因")))
    else:
        fails = stage.get("failures") or []
        if not fails:
            # 没有逐条失败：通过给一条通过用例，失败给一条失败用例，否则
            # 面板会把一个失败的动作看成一例"通过"
            if status == STATUS_FAILED:
                counts["failures"] = 1
                cases.append(
                    '    <testcase name="{}" classname="cxx_standardize">\n'
                    '      <failure message="动作失败">{}</failure>\n'
                    '    </testcase>\n'.format(
                        name, _xml(stage.get("summary") or "动作失败")))
            else:
                cases.append(
                    '    <testcase name="{}" classname="cxx_standardize"/>\n'
                    .format(name))
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
              '<testsuites name="cxx_standardize" tests="{}" failures="{}" '
              'skipped="{}" time="{:.3f}">\n').format(
                  tests, failures, skipped, total_time)
    return header + "".join(suites) + "</testsuites>\n"


# ------------------------------------------------------------------ SARIF

def _fingerprint(path, check, message):
    """跨运行可比的部分指纹：文件 + 检查名 + 消息摘要。

    行号不进材料——行号随无关改动漂移，会让同一条告警变脸。
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
            "name": "cxx_standardize",
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
            log.out(render(document, fmt).rstrip("\n"))
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
