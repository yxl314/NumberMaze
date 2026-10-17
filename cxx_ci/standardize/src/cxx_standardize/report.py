"""机器可读报告：内部 schema（与 ci 侧同形状的平行实现）。

报告是**旁路产物**：它只搬运动作已经算出的结论（状态、诊断、"需人工"清单、
摘要），不在 writer 里新增任何判定分支——否则终端与报告迟早会打架。schema 在
CONTRACT.md 登记，ci 与 standardize 两侧各有一份平行实现，共用同一份 JSON 定义
而不共享代码。

格式是 json：直接落盘内部 schema，便于两次运行做 diff。

与本项目其余部分不同，这里保留协议化的形状（键名）与 ci 侧逐字对齐：报告要跨
两个独立子项目可比较，形状是契约的一部分，不随本侧风格漂移。
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field

from . import CONTRACT_VERSION, __version__, baseline as baseline_mod, fmt, log
from . import source, xfail
from .errors import Code

# 内部 schema 的版本。schema 变化时自增，与工具版本、契约版本分开演进。
REPORT_VERSION = 1

# 支持的报告格式
FORMATS = ("json",)

# 多格式并列写文件时给各格式加的后缀；单格式直接写在 --report 指定的路径上
EXTENSIONS = {"json": "json"}

# 动作三态。本项目没有"跳过"这一档，保留常量以便与 ci 侧 schema 同形。
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# 报告里的配置摘要：键名固定、取值按本侧配置对象映射，跨机器比对用。
DIGEST_KEYS = ("BUILD_TOOL", "SCOPE", "SOURCE_ENCODING")


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
    """生效配置的关键取值，用于排查"同一份代码在两台机器上结论不同"。"""
    scope = {
        "project": [str(item)
                    for item in (getattr(config, "scope_roots", None) or [])],
        "excluded": [str(item)
                     for item in (getattr(config, "scope_excluded", None) or [])],
    }
    values = {
        "BUILD_TOOL": getattr(config, "build_tool", None),
        "SCOPE": scope,
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


def build(config, collector, conclusion=None):
    """把收集到的阶段结论搬成内部 schema 的报告字典。

    conclusion 是入口给出的整体结论。报告是**搬运工**：它不自己判卷，但必须把
    入口的判定带出去——没有这一项时，"一段都没跑就失败"的报告在平台眼里是
    "零失败零跳过"，等于把一次失败静默成通过。
    """
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
    document = {
        "report_version": REPORT_VERSION,
        "tool": tool_info(),
        "environment": environment(getattr(collector, "tools", None)),
        "config_digest": config_digest(config),
        "baseline": baseline(config),
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


def _source_config():
    """给 source.write_file 用的最小配置：只带编码判定所需的字段。"""
    return _SourceConfig()


class _SourceConfig(object):
    """写自有的文本文件只需要"按什么编码读回已有的那一份"。"""

    source_encoding = "utf-8"
    source_encoding_candidates = []
    declared_keys = frozenset()


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
            log.out(render(document, fmt).rstrip("\n"))
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
