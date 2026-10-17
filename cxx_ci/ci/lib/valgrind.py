#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""valgrind XML 结果的解析与归因。

用"归因"替代手写抑制：抑制 DSL 的命名块与帧链随编译器/glibc 版本过期，
而"这条错误的栈顶第一个有归属的帧属于谁"只取决于构建布局，不随版本漂移。
归因只回答"该不该由本项目负责"，因此没有 <obj> 的帧无法归属只能跳过；整条
栈都没有归属时按失败处理——宁误报不漏报，避免把真正的泄漏静默吞掉。
"""

import sys
if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")
import collections
import hashlib
import os
import re
import xml.etree.ElementTree as ET

from . import errors
from .errors import OK
from .paths import is_within

# ------------------------------------------------------------------ 判定

VERDICT_FAILURE = "failure"  # 计入域内问题
VERDICT_IGNORED = "ignored"  # 归因忽略

# 归因作用域：project 只认项目根内的对象（旧 WHITELIST 语义），
# all 表示不设域，全量计入（还原旧的裸跑语义）。
OUTSIDE_IGNORE = "project"
OUTSIDE_FAIL = "all"

# XML 读不了与 XML 损坏都要与"确实有内存问题"区分开：前者是工具链故障，
# 后者是门禁结论，混在一个错误码里会让使用者分不清该修代码还是修环境。
XML_UNREADABLE = errors.XML_UNREADABLE
XML_MALFORMED = errors.XML_MALFORMED
# 固定系统前缀集：这些代码不可能由使用者的项目修复。写成前缀是为了覆盖
# 多架构目录（/usr/lib/x86_64-linux-gnu）与带位数的目录（/usr/lib64）。
SYSTEM_PREFIXES = ("/usr/lib", "/usr/local/lib", "/lib",
                   "/System/Library", "/Library")

# 忽略计数的固定分类键，始终出现，便于展示层直接取用
_BUCKETS = ("kind", "external", "system")

# 缺省不计入失败的类别，配置的缺省值取自这里，只有一处定义。
#   still-reachable  valgrind 自己也认为不算泄漏
#   possibly-lost    含定时器/异步事件循环的测试里，这类分类随时序漂移
#                    （"堆上是否仍残留指向该块的指针"），同一份二进制两次运行
#                    的域内条数能差两三成；确凿的泄漏由 definitely-lost 与
#                    invalid-*/uninit-* 兜住
DEFAULT_IGNORE_KINDS = ("still-reachable", "possibly-lost")

Frame = collections.namedtuple("Frame", "obj function")
Error = collections.namedtuple("Error", "kind text frames")


# ------------------------------------------------------------ XML 解析

def _text(element, path):
    """取子元素的文本并去掉首尾空白，缺失或为空时返回空串。"""
    value = element.findtext(path)
    return value.strip() if value else ""


def _frame(element):
    """解析一个 <frame>。

    <obj> 缺失表示归属不明，必须保留为 None 而不是空串：空串会被误当作
    一个真实路径去和项目根比较。
    """
    obj = _text(element, "obj")
    function = _text(element, "fn")
    return Frame(obj or None, function or None)


# valgrind 3.26 的已知缺陷：still-reachable 计数为 0 时漏写闭合标签，整份
# 报告因而不合法。定点补上；真正被截断的文件仍会因后续缺标签而解析失败。
_EMPTY_STILL_REACHABLE = re.compile(
    br"(<still_reachable>\s*<bytes>0</bytes>\s*<blocks>0</blocks>\s*)"
    br"(?=<suppressed>)")


_XML_DECL = re.compile(br"<\?xml[^>]*\?>")


def _documents(data):
    """把 valgrind 的 XML 输出切成若干份合法文档。

    被测程序 fork 之后，子进程继承的是**已经写了一半的**同一个文件描述符：
    它只写自己那段 <status>/<heap_summary>/<leak_summary> 与一个 </valgrindoutput>，
    开头那份 <valgrindoutput> 与 XML 声明是父进程写过的，不会再写。于是文件里
    出现 1 个开标签、N 个闭标签，整份不是合法 XML，但按闭标签切开后每一段都能
    补齐成一份——父进程与每个子进程的泄漏因此都归因得到。

    只有一份文档（不开 fork 的常规情况）时原样返回，不做任何改写。
    """
    if data.count(b"</valgrindoutput>") <= 1:
        return [_EMPTY_STILL_REACHABLE.sub(br"\1</still_reachable>", data)]
    documents = []
    for index, part in enumerate(data.split(b"</valgrindoutput>")):
        if not part.strip():
            continue
        body = _EMPTY_STILL_REACHABLE.sub(br"\1</still_reachable>", part)
        if index:
            # 后续每一段都缺开标签，补一个；XML 声明只能出现在最前面，去掉
            body = _XML_DECL.sub(b"", body)
            body = b"<valgrindoutput>" + body
        documents.append(body + b"</valgrindoutput>")
    return documents


def parse_xml(path):
    """读 XML 返回 (错误列表, 错误码, 说明)。

    读取与解析的失败都在边界处转成错误码，不向调用方抛异常。
    一个 <error> 可能带多条 <stack>（如分配与释放不匹配），按出现顺序
    把帧摊平，归因只关心栈顶，摊平不影响判定。
    """
    try:
        handle = open(path, "rb")
    except (IOError, OSError) as exc:
        return None, XML_UNREADABLE, "无法读取 valgrind XML: {}: {}".format(
            path, exc)
    try:
        data = handle.read()
    finally:
        handle.close()
    errors = []
    for document in _documents(data):
        try:
            root = ET.fromstring(document)
        except ET.ParseError as exc:
            return None, XML_MALFORMED, "valgrind XML 结构损坏: {}: {}".format(
                path, exc)
        for element in root.iter("error"):
            frames = []
            for stack in element.findall("stack"):
                for item in stack.findall("frame"):
                    frames.append(_frame(item))
            errors.append(Error(_text(element, "kind"),
                                _text(element, "xwhat/text"), frames))
    return errors, OK, ""


# -------------------------------------------------------------- 归因

def _normalize_kind(kind):
    """把错误类型归一成配置里好写的名字。

    valgrind XML 用 Leak_StillReachable 这种驼峰加下划线的名字，而使用者
    在配置里只会写 still-reachable；Leak_ 前缀是所有泄漏类的共同前缀，去掉
    才能让 MEMCHECK_IGNORE_KINDS 同时适配泄漏与错误两类。
    """
    text = str(kind or "").strip()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", text)
    text = re.sub(r"[-_]+", "-", text).lower().strip("-")
    if text.startswith("leak-"):
        text = text[len("leak-"):]
    return text


def _prefix_hit(obj, prefixes):
    """返回第一个命中的前缀，未命中返回 None。

    注意这是**字符串前缀**匹配，不是目录包含判定：比的是 valgrind 报出来的
    对象名（`/usr/lib/.../libc.so.6` 这类），而且按配置语义就该不带路径边界
    ——MEMCHECK_IGNORE_OBJECTS 里写 `/usr/lib` 要能盖住它下面所有库。目录包含
    一律走 paths.is_within，别把这两件事合并。
    """
    target = os.path.normpath(obj)
    for prefix in prefixes:
        if prefix and target.startswith(os.path.normpath(prefix)):
            return prefix
    return None


def _object_hit(obj, objects):
    """忽略对象按精确路径比较，避免同名的不同库互相牵连。"""
    target = os.path.normpath(obj)
    for item in objects:
        if item and target == os.path.normpath(item):
            return item
    return None


def symbol_prefix(function):
    """函数名的符号前缀：去掉参数表与最后一段。

    NormalButton::NormalButton_init() -> NormalButton::
    QMetaObject::activate(...)        -> QMetaObject::
    没有 :: 时返回整个名字，聚合时仍能看出是哪一个符号。
    """
    text = (function or "").strip().split("(", 1)[0].strip()
    if not text:
        return ""
    if "::" not in text:
        return text
    return text.rsplit("::", 1)[0] + "::"


def _symbol_hit(function, symbols):
    """按去修饰符号前缀匹配，返回命中的前缀。

    静态链接把 vendored 库链进可执行文件后，帧的归属对象就是可执行文件
    本身，路径级忽略（IGNORE_OBJECTS 与前缀）对它完全无效——那个路径正是
    项目自己的产物。符号名是此时唯一还能区分"这段代码谁写的"的依据。
    """
    name = (function or "").strip()
    if not name:
        return None
    for item in symbols:
        if item and name.startswith(item):
            return item
    return None


def signature(error):
    """错误的签名：调用栈帧链的稳定摘要。

    字节数与上下文不进签名——同一处泄漏每次漏多少会变，签名要认的是
    "哪条栈"。给出签名是为了让登记清单能精确指向一条泄漏。
    """
    material = "\n".join("{}|{}".format(frame.obj or "", frame.function or "")
                         for frame in error.frames)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def _is_interceptor(obj):
    """是否 valgrind 自己的替换代码。

    vgpreload 的 malloc/free 是插桩载体而不是泄漏点，且总在分配栈的最
    顶端；若把它当作可归属帧，真实项目泄漏会因栈顶是 /usr/lib* 而全被判成
    系统域、整条忽略，门禁形同虚设。因此它按"归属不明"对待，继续往下看。
    """
    name = os.path.basename(obj)
    return name.startswith("vgpreload") or "/valgrind/" in obj


def attribute(error, project_roots, ignore_objects=(), ignore_prefixes=(),
              outside=OUTSIDE_IGNORE, ignore_symbols=()):
    """把一条错误归因。

    从栈顶向下取第一个"可归属帧"（obj 已知，且不是 valgrind 自己的替换代码），
    判定：
      命中忽略集（ignore_objects / ignore_prefixes / ignore_symbols） → "ignored"
      落在 project_roots 内                                         → "failure"
      两不沾且 scope 为 project                                     → "ignored"（域外不可修，不冤枉使用者）
      两不沾且 scope 为 all                                         → "failure"
    全栈 unknown                                                    → "failure" 并标注"归属不明"（宁误报不漏报）

    返回 (判定, 归属帧或 None, 理由)。

    符号级忽略必须排在项目根判定之前：静态链接时归属对象就是项目自己的
    可执行文件，先判项目根的话所有 vendored 泄漏都会被算成项目问题。
    """
    target = None
    for frame in error.frames:
        if frame.obj and not _is_interceptor(frame.obj):
            target = frame
            break
    if target is None:
        # 没有可归因的帧时不能猜：宁可得罪使用者也比吞掉真实泄漏好
        return VERDICT_FAILURE, None, "归属不明：整条栈没有已知归属的帧"

    obj = target.obj
    hit = _object_hit(obj, ignore_objects)
    if hit is not None:
        return VERDICT_IGNORED, target, "命中忽略对象: " + hit
    prefix = _prefix_hit(obj, ignore_prefixes)
    if prefix is not None:
        return VERDICT_IGNORED, target, "命中忽略前缀: " + prefix
    symbol = _symbol_hit(target.function, ignore_symbols)
    if symbol is not None:
        return VERDICT_IGNORED, target, "命中忽略符号: " + symbol
    for root in project_roots:
        if root and is_within(obj, root):
            return VERDICT_FAILURE, target, "落在项目根内: " + obj
    if outside == OUTSIDE_FAIL:
        return VERDICT_FAILURE, target, "域外对象且 outside=OUTSIDE_FAIL，计入失败: " + obj
    return VERDICT_IGNORED, target, "域外对象且 outside=OUTSIDE_IGNORE，归因忽略: " + obj


# -------------------------------------------------------------- 汇总

def _is_system(obj):
    """对象是否来自固定系统目录。"""
    return _prefix_hit(obj, SYSTEM_PREFIXES) is not None


def _frame_label(frame):
    """把归属帧渲染成便于定位的片段。"""
    if frame is None:
        return ""
    parts = [part for part in (frame.obj, frame.function) if part]
    return "（位置: " + " ".join(parts) + "）" if parts else ""


# 归因摘要。headline 与 top 是要给人看的两行，details 是逐条明细（留给日志）；
# 分开返回而不是拼成一整段：控制台只该看到前两行，明细上百条时会把结论埋掉。
Summary = collections.namedtuple(
    "Summary", "count ignored buckets kinds headline top details")


def _ranked_prefixes(failures, limit):
    """域内条目按符号前缀聚合，按条数降序取前 limit 个。

    面对几十条域内问题时，使用者需要先看清"大头是谁"：静态链接下往往有
    十几条同属一个 vendored 符号前缀，聚合行会立刻点出来。
    """
    counts = {}
    for _, frame, _ in failures:
        name = symbol_prefix(frame.function) if frame is not None else ""
        key = name or "（归属不明）"
        counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:limit]


def top_line(failures, limit=5):
    """域内条目按符号前缀聚合的一行；只有一条时不值得聚合，返回空串。"""
    if len(failures) < 2:
        return ""
    ranked = _ranked_prefixes(failures, limit)
    return "域内条目按符号前缀聚合 Top {}: {}".format(
        len(ranked), "、".join("{} {}".format(name, count)
                               for name, count in ranked))


def kind_line(failures):
    """域内条目按错误类别计数的一行。

    抖动来自某一类（异步/定时器测试下的 possibly-lost），把各类别摆出来
    才能看出该忽略哪一类，而不是只看到一个会变的"域内 N 处"。
    """
    if not failures:
        return ""
    counts = {}
    for error, _, _ in failures:
        name = _normalize_kind(error.kind) or "unknown"
        counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return "域内条目按类别: " + "、".join(
        "{} {}".format(name, count) for name, count in ranked)


def summarize(errors, verdicts, ignore_kinds=DEFAULT_IGNORE_KINDS, top=5):
    """把一份归因结果汇总成 Summary。

    verdicts 与 errors 一一对应，元素是 attribute 的返回值。ignore_kinds
    在此处生效而不是在 attribute 里：类型忽略是展示策略，与"这条栈归谁"
    无关，放在这里才能让同一份归因结果换一种策略复用。

    忽略计数只分三类：kind（按错误类型忽略）、system（固定系统目录）、
    external（其余，含显式忽略集与域外对象）。
    """
    ignored_kinds = set(_normalize_kind(item) for item in ignore_kinds)
    counts = dict((key, 0) for key in _BUCKETS)
    failures = []
    ignored = 0
    items = errors or []
    for index, error in enumerate(items):
        verdict = verdicts[index] if index < len(verdicts or ()) else None
        if _normalize_kind(error.kind) in ignored_kinds:
            counts["kind"] += 1
            ignored += 1
            continue
        if verdict is None:
            # 归因结果缺失等同于归属不明，不能当作通过
            failures.append((error, None, "归属不明：缺少归因结果"))
            continue
        decision, frame, reason = verdict
        if decision == VERDICT_FAILURE:
            failures.append((error, frame, reason))
            continue
        ignored += 1
        if frame is not None and frame.obj and _is_system(frame.obj):
            counts["system"] += 1
        else:
            counts["external"] += 1

    headline = "域内问题 {} 处 | 忽略 {} 处（外部 {} / 系统 {} / 类型 {}）".format(
        len(failures), ignored, counts["external"], counts["system"],
        counts["kind"])
    details = []
    for error, frame, reason in failures:
        text = error.text or error.kind or "未命名的错误"
        details.append("  - [{}] {}（{}）{} [sig:{}]".format(
            error.kind or "未知", text, reason, _frame_label(frame),
            signature(error)))
    return Summary(len(failures), ignored, counts, kind_line(failures),
                   headline, top_line(failures, top), details)
