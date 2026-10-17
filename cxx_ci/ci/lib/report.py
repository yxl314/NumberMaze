#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机器可读报告：内部 schema。

报告是**旁路产物**：它只搬运阶段已经算出的结论（状态、诊断、失败、摘要），
不在 writer 里新增任何判定分支——否则终端与报告迟早会打架。schema 在
CONTRACT.md 登记，ci 与 standardize 两侧各有一份平行实现，共用同一份 JSON
定义而不共享代码。

格式是 json：直接落盘内部 schema，便于两次运行做 diff。

所有取值都来自既有的阶段结果对象、协议行、归因结果与登记清单，报告只做搬运。
"""

import json
import os
import re
import sys

if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")

from . import source, tools, xfail
from .errors import KIND_SKIPPED, OK, kind
from .output import out as _stdout
from .stage import first_line, log_path, stage_view
from .version import CONTRACT_VERSION, __version__

# 内部 schema 的版本。schema 变化时自增，与工具版本、契约版本分开演进。
REPORT_VERSION = 1

# 支持的报告格式
FORMATS = ("json",)

# 多格式并列写文件时给各格式加的后缀；单格式直接写在 --report 指定的路径上
EXTENSIONS = {"json": "json"}

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


def relative_path(root, path):
    """把绝对路径折成相对工程根的路径；折不动就原样返回。

    绝对路径里含检出目录：同一份源码换个目录检出，路径全变。
    """
    if not root or not path:
        return path or ""
    root = os.path.abspath(str(root)).rstrip(os.sep) + os.sep
    text = os.path.abspath(str(path))
    if text.startswith(root):
        return text[len(root):]
    return text


def diagnostic_entry(item, count, xfail_flag, root=""):
    """把一条归因后的诊断搬成报告条目。"""
    return {
        "level": item.level,
        "file": item.file,
        "path": relative_path(root, item.file),
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


def conclusion_of(code, detail=""):
    """把入口的退出码折成报告里的整体结论。

    报告不判卷：这里只是把入口已经做出的判定搬成结构化字段。code 为 None 时
    不写这一项（调用方没给判定，就不假装有）。
    """
    if code is None:
        return None
    value = int(code)
    return {
        "code": value,
        "status": STATUS_PASSED if value == 0 else STATUS_FAILED,
        "detail": str(detail or ("通过" if value == 0 else "运行未完成")),
    }


def build(config, records, skipped=(), conclusion=None):
    """把阶段结果搬成内部 schema 的报告字典。

    records 是 stage.Result 的序列；skipped 是当前机器上不可用的阶段名集合。

    conclusion 是入口给出的整体结论（退出码 + 一句话）。报告是**搬运工**：
    它不自己判卷，但必须把入口的判定带出去——没有这一项时，"一段都没跑就
    失败"的报告在平台眼里是"零失败零跳过"，等于把一次失败静默成通过。
    """
    skipped = set(skipped or ())
    checklist = _checklist(config)
    stages = []
    for result in records:
        view = stage_view(config, result)
        stage_skipped = (result.name in skipped
                         or kind(result.code) == KIND_SKIPPED)
        diagnostics = [diagnostic_entry(item, count, False, config.root)
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
    document = {
        "report_version": REPORT_VERSION,
        "tool": tool_info(),
        "environment": environment(),
        "config_digest": config_digest(config),
        "baseline": baseline(config, checklist),
        "stages": stages,
    }
    if conclusion:
        document["conclusion"] = conclusion
    # 自相矛盾的搬运结果要被看见：阶段判绿却有逐例失败，说明上游哪里出了问题
    # （伪造的协议行就是一条来路）。报告不重判，但也不该把矛盾原样发出去
    inconsistent = [stage["name"] for stage in stages
                    if stage["status"] == STATUS_PASSED and stage["failures"]]
    if inconsistent:
        document["inconsistent"] = inconsistent
    return document


# ------------------------------------------------------------------ JSON

def to_json(document):
    """内部 schema 的 JSON 文本。排序输出，两次运行的报告可逐行 diff。"""
    return json.dumps(document, indent=2, sort_keys=True,
                      ensure_ascii=False) + "\n"


# ------------------------------------------------------------------ 落盘

def _source_config():
    """给 source.write_file 用的最小配置：只带编码判定所需的字段。"""
    return _SourceConfig()


class _SourceConfig(object):
    """写自有的文本文件只需要"按什么编码读回已有的那一份"。"""

    source_encoding = "utf-8"
    source_encoding_candidates = ()


def render(document, fmt):
    """按格式渲染报告文本。"""
    if fmt == "json":
        return to_json(document)
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
            # 原子写：两份工具（或同一份的两个进程）写同一个报告路径时，
            # 就地截断重写会互相破坏，读到的可能是半份文档；改名是原子的，
            # 读到的要么是这一份、要么是那一份
            code, message = source.write_file(path, render(document, fmt),
                                              _source_config())
            if code != 0:
                raise IOError(message)
        except (IOError, OSError, ValueError, TypeError) as exc:
            problems.append("写报告失败 {}: {}".format(path, exc))
            continue
        written.append(path)
    return written, problems
