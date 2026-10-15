#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重复度度量：把"复用度"也变成可机检的纪律。

本仓库已经把配置纪律（audit 抓"改配置不生效"）、编码纪律（source 抓"裸写文本"）、
路径纪律（paths 抓"自行拼前缀"）都机检化了，唯独复用度不在任何自检里：新增一个
"差不多"的辅助函数比复用它更快，因为它不需要理解既有实现；而没有度量时，这种增量
在 review 里看起来完全正常。

这里实现三种度量（口径如下，可复核）：

1. 精确重复块：去注释、去空行、折叠空白后，取连续 6 行作种子，命中即向两边扩展
   到不同为止；只报 ≥12 行、且出现在 ≥2 处的块，重叠位置只计一次。
2. 函数级近似重复：按 AST 取函数体，≥18 行；先比函数体内标识符集合的 Jaccard
   ≥0.5，再用序列相似度 ≥0.82 定性；跳过同文件相邻函数。
3. 整文件相似度：同样归一化后做序列相似度，>0.45 才列出。

默认只输出报告；`--check` 比对 `.dupcheck-baseline.json`，超过基线即非零退出；
`--catalog` 列出公开函数签名，回答"这件事仓库里是不是已经有人做过"。

只测生产代码：测试里的重复 fixture 是有意的可读性选择，不在度量范围内。

与 `ci` 同档：只用标准库，Python ≥ 3.4 可跑。本工具是仓库级开发工具，不属于
`ci` 或 `standardize` 任一侧，不参与两侧的契约。
"""

import sys

if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")

import argparse
import ast
import difflib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_NAME = ".dupcheck-baseline.json"
ALLOW_NAME = ".dupcheck-allow.json"

# 生产代码的范围。测试代码不在其中：重复的 fixture 是有意的可读性选择
PRODUCTION_DIRS = ("ci/lib", "ci/stages", "standardize/src")
PRODUCTION_FILES = ("ci/ci.py", "standardize/standardize.py")

# 度量口径
SEED_LINES = 6
MIN_BLOCK_LINES = 12
MIN_FUNCTION_LINES = 18
JACCARD_MIN = 0.5
FUNCTION_SIMILARITY_MIN = 0.82
FILE_SIMILARITY_MIN = 0.45

# 函数节点类型。AsyncFunctionDef 是 3.5 才有的，取不到就只用 FunctionDef
_FUNCTION_KINDS = [ast.FunctionDef]
if hasattr(ast, "AsyncFunctionDef"):
    _FUNCTION_KINDS.append(ast.AsyncFunctionDef)
_FUNCTION_KINDS = tuple(_FUNCTION_KINDS)

_EXIT_OK = 0
_EXIT_REGRESSION = 1


# ------------------------------------------------------------------ 文件集

def production_files(root):
    """生产代码文件表：两个子项目的实现代码，不含测试与工具自身。"""
    root = Path(root)
    found = []
    for name in PRODUCTION_DIRS:
        directory = root / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py"), key=str):
            if _skipped(path):
                continue
            found.append(path)
    for name in PRODUCTION_FILES:
        path = root / name
        if path.is_file() and not _skipped(path):
            found.append(path)
    return found


def _skipped(path):
    parts = path.parts
    return "__pycache__" in parts or ".git" in parts


def read_lines(path):
    """按 UTF-8 读原文行；读不出来就返回空表，不让度量因一个坏文件整体失败。"""
    handle = open(str(path), "r", encoding="utf-8", errors="replace")
    try:
        return handle.read().splitlines()
    finally:
        handle.close()


def _comment_lines(source):
    """行号集合：这些行上只有注释（含行尾注释所在行的注释片段）。

    用 tokenize 而不是"行首是 #"：`x = 1  # 说明` 这种行尾注释、以及字符串
    字面量里的 `#`（URL、颜色值）都要区分开。只判断"行首 #"会把行尾注释留在
    骨架里——两段代码逐字相同、只有行尾注释不同时就会漏报（实测漏过）。
    """
    import io
    import tokenize

    found = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                found.add(token.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # 词法都过不去：退回保守做法，不剥任何注释
        return set()
    return found


def _is_string_node(node):
    """这个表达式是不是字符串字面量。

    3.4 把字符串与字节分成 ast.Str / ast.Bytes 两种节点，3.8 起统一到
    ast.Constant 且 ast.Str 逐步废弃——两边的名字都得认。
    """
    if isinstance(node, getattr(ast, "Constant", ())):
        return isinstance(getattr(node, "value", None), str)
    str_node = getattr(ast, "Str", None)
    return str_node is not None and isinstance(node, str_node)


def _docstring_spans(tree):
    """文档字符串占用的行号范围集合。

    文档字符串是语法节点，不是注释，tokenize 看不出来；不排掉的话，两个只有
    文档不同的函数会被算成"近似重复"。
    """
    spans = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef) + _FUNCTION_KINDS):
            continue
        body = getattr(node, "body", None) or []
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and _is_string_node(first.value):
            # end_lineno 是 3.8 才有的，取不到就按单行算
            last = getattr(first, "end_lineno", 0) or first.lineno
            for number in range(first.lineno, last + 1):
                spans.add(number)
    return spans


def normalized(lines, tree=None, source=None):
    """去注释、去文档字符串、去空行、折叠空白，返回 (骨架行, 真实行号)。

    第二个返回值是**原始文件里的 1 起行号**：报告与豁免标识都要能落到真实行上。
    直接返回骨架行的下标会让给出的位置是错的——那比不给更糟，因为读者会照着
    错位置去改。
    """
    comment = _comment_lines(source) if source is not None else set()
    docstrings = _docstring_spans(tree) if tree is not None else set()
    kept, numbers = [], []
    for number, line in enumerate(lines, 1):
        if number in docstrings:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if number in comment and stripped.startswith("#"):
            continue
        if comment and stripped.startswith("#"):
            continue
        # 行尾注释：把注释起点之后截掉（tokenize 给的是注释所在行）
        if number in comment:
            code = _strip_trailing_comment(stripped)
            if not code:
                continue
            stripped = code
        kept.append(re.sub(r"\s+", " ", stripped))
        numbers.append(number)
    return kept, numbers


def _strip_trailing_comment(text):
    """把行尾注释截掉。只处理最常见形态：不在引号里的 # 之后全是注释。"""
    quote = ""
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == "#":
            return text[:index].rstrip()
    return text


# ------------------------------------------------------------------ 度量 1

def find_blocks(sources):
    """精确重复块。sources 为 [(名字, 骨架行, 真实行号)]。

    对外给出的位置是**真实行号**：骨架行下标与它不同（注释、空行、文档字符串
    都被剥掉了），两者相差的数随文件而定，不能互相替代。
    """
    tables = {name: lines for name, lines, _numbers in sources}
    numbers = {name: nums for name, _lines, nums in sources}
    seeds = {}
    for name, lines, _nums in sources:
        for index in range(len(lines) - SEED_LINES + 1):
            seeds.setdefault(tuple(lines[index:index + SEED_LINES]), [])
            seeds[tuple(lines[index:index + SEED_LINES])].append((name, index))
    found = {}
    for _seed, spots in seeds.items():
        if len(spots) < 2:
            continue
        for left in range(len(spots)):
            for right in range(left + 1, len(spots)):
                name_a, index_a = spots[left]
                name_b, index_b = spots[right]
                start_a, start_b, length = _maximal_run(
                    tables, name_a, index_a, name_b, index_b)
                if length < MIN_BLOCK_LINES:
                    continue
                content = tuple(tables[name_a][start_a:start_a + length])
                found.setdefault(content, set())
                found[content].add((name_a, numbers[name_a][start_a], length))
                found[content].add((name_b, numbers[name_b][start_b], length))
    return found


def _maximal_run(tables, name_a, index_a, name_b, index_b):
    """从种子向两边扩展，返回 (起点A, 起点B, 块长)。"""
    lines_a, lines_b = tables[name_a], tables[name_b]
    back = 0
    while (index_a - back - 1 >= 0 and index_b - back - 1 >= 0
           and lines_a[index_a - back - 1] == lines_b[index_b - back - 1]):
        back += 1
    forward = SEED_LINES
    while (index_a + forward < len(lines_a) and index_b + forward < len(lines_b)
           and lines_a[index_a + forward] == lines_b[index_b + forward]):
        forward += 1
    return index_a - back, index_b - back, back + forward


# ------------------------------------------------------------------ 度量 2

def find_function_pairs(sources, trees):
    """函数级近似重复：先 Jaccard 后序列相似度，跳过同文件相邻函数。"""
    functions = []
    for name, lines, _numbers in sources:
        tree = trees.get(name)
        if tree is None:
            continue
        # 相邻判定按**源码行序**排一次再编号：ast.walk 的顺序不是源码顺序，
        # 用它判"相邻"会把两个不同 class 里的同名方法当成相邻而误跳过
        ordered = sorted(_functions(tree), key=lambda item: item.lineno)
        for order, node in enumerate(ordered):
            start, end = _body_span(node)
            if end - start + 1 < MIN_FUNCTION_LINES:
                continue
            body, _nums = normalized(lines[start - 1:end], node)
            functions.append({
                "file": name,
                "name": _function_name(node),
                "line": node.lineno,
                "order": order,
                "ids": _identifiers(node),
                "body": body,
            })
    pairs = []
    for left in range(len(functions)):
        for right in range(left + 1, len(functions)):
            first, second = functions[left], functions[right]
            if (first["file"] == second["file"]
                    and abs(first["order"] - second["order"]) == 1):
                continue
            jaccard = _jaccard(first["ids"], second["ids"])
            if jaccard < JACCARD_MIN:
                continue
            ratio = difflib.SequenceMatcher(None, first["body"],
                                            second["body"]).ratio()
            if ratio < FUNCTION_SIMILARITY_MIN:
                continue
            pairs.append((ratio, jaccard, first, second))
    return sorted(pairs, key=lambda item: (-item[0], item[2]["file"],
                                           item[2]["line"]))


def _functions(tree):
    return [node for node in ast.walk(tree)
            if isinstance(node, _FUNCTION_KINDS)]


def _function_name(node):
    return getattr(node, "name", "?")


def _body_span(node):
    """函数体在原文里的行区间（含边界）。不用 end_lineno：3.8 才有。"""
    first = node.body[0].lineno if node.body else node.lineno
    last = first
    for child in ast.walk(node):
        line = getattr(child, "lineno", None)
        if line and line > last:
            last = line
    return first, last


def _identifiers(node):
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
        elif isinstance(child, ast.arg):
            names.add(child.arg)
    return names


def _jaccard(left, right):
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 0.0
    return float(len(left & right)) / float(len(union))


# ------------------------------------------------------------------ 度量 3

def find_similar_files(sources):
    """整文件相似度：归一化后按序列相似度比对所有文件对。"""
    pairs = []
    for left in range(len(sources)):
        for right in range(left + 1, len(sources)):
            name_a, lines_a, _na = sources[left]
            name_b, lines_b, _nb = sources[right]
            ratio = difflib.SequenceMatcher(None, lines_a, lines_b).ratio()
            if ratio > FILE_SIMILARITY_MIN:
                pairs.append((ratio, name_a, name_b))
    return sorted(pairs, key=lambda item: (-item[0], item[1], item[2]))


# ------------------------------------------------------------------ 汇总

def measure(root=ROOT):
    """跑三种度量，返回一份可打印的度量结果。"""
    files = production_files(root)
    sources = []
    trees = {}
    total_lines = 0
    for path in files:
        name = _relative(root, path)
        lines = read_lines(path)
        total_lines += len(lines)
        tree = _parse(path)
        text = "\n".join(lines)
        kept, numbers = normalized(lines, tree, text)
        sources.append((name, kept, numbers))
        if tree is not None:
            trees[name] = tree
    blocks = find_blocks(sources)
    ordered = sorted(blocks.items(), key=lambda item: (-len(item[0]), item[0]))
    function_pairs = find_function_pairs(sources, trees)
    file_pairs = find_similar_files(sources)
    return {
        "files": len(files),
        "lines": total_lines,
        "blocks": ordered,
        "function_pairs": function_pairs,
        "file_pairs": file_pairs,
    }


def block_counts(blocks):
    """块级数字：组数与冗余行数，两者都用于比对基线。"""
    groups = len(blocks)
    redundant = 0
    for content, spots in blocks:
        redundant += len(content) * (len(spots) - 1)
    return groups, redundant


def _parse(path):
    try:
        handle = open(str(path), "r", encoding="utf-8", errors="replace")
    except IOError:
        return None
    try:
        text = handle.read()
    finally:
        handle.close()
    try:
        return ast.parse(text, filename=str(path))
    except SyntaxError:
        return None
    except ValueError:
        return None


def _relative(root, path):
    return os.path.relpath(str(path), str(root)).replace(os.sep, "/")


# ------------------------------------------------------------------ 报告

def render(result):
    """把度量结果画成给人看的报告。"""
    groups, redundant = block_counts(result["blocks"])
    lines = ["重复度度量（生产代码 {} 个文件 / {} 行）".format(
        result["files"], result["lines"])]
    lines.append("")
    lines.append("精确重复块（≥{} 行逐字相同）：{} 组，冗余约 {} 行".format(
        MIN_BLOCK_LINES, groups, redundant))
    for content, spots in result["blocks"]:
        where = "、".join(
            "{}:{}".format(name, start)
            for name, start, _length in sorted(spots))
        lines.append("  - {} 行 × {} 处：{}".format(
            len(content), len(spots), where))
    lines.append("")
    lines.append("函数级近似重复（≥{} 行，相似度 ≥{}）：{} 对".format(
        MIN_FUNCTION_LINES, FUNCTION_SIMILARITY_MIN,
        len(result["function_pairs"])))
    for ratio, jaccard, first, second in result["function_pairs"]:
        lines.append("  - {}:{} def {} ↔ {}:{} def {}  （Jaccard {} / 相似度 {}）"
                     .format(first["file"], first["line"], first["name"],
                             second["file"], second["line"], second["name"],
                             round(jaccard, 3), round(ratio, 3)))
    lines.append("")
    lines.append("整文件相似度（>{}）：{} 对".format(
        FILE_SIMILARITY_MIN, len(result["file_pairs"])))
    for ratio, name_a, name_b in result["file_pairs"]:
        lines.append("  - {} ↔ {}  （{}）".format(
            name_a, name_b, round(ratio, 3)))
    return "\n".join(lines)


# ------------------------------------------------------------------ 基线与豁免

def load_json(path, default):
    if not path.is_file():
        return default
    try:
        handle = open(str(path), "r", encoding="utf-8")
    except IOError:
        return default
    try:
        return json.loads(handle.read())
    finally:
        handle.close()


def _file_identity(finding):
    """把一条发现整理成可比对的标识串列表（相对路径或路径:名字）。"""
    kind = finding.get("kind")
    if kind == "block":
        return ["{}:{}".format(item[0], item[1])
                for item in sorted(finding["spots"])]
    if kind == "function_pair":
        return ["{}:{}".format(item["file"], item["name"])
                for item in finding["items"]]
    if kind == "file_pair":
        return list(finding["files"])
    return []


def all_findings(result):
    """三种度量合成统一的发现列表，供豁免匹配与过期检查。"""
    findings = []
    for content, spots in result["blocks"]:
        findings.append({"kind": "block", "length": len(content),
                         "redundant": len(content) * (len(spots) - 1),
                         "spots": sorted(spots)})
    for ratio, jaccard, first, second in result["function_pairs"]:
        findings.append({"kind": "function_pair",
                         "items": [first, second]})
    for ratio, name_a, name_b in result["file_pairs"]:
        findings.append({"kind": "file_pair", "files": sorted([name_a, name_b])})
    return findings


# 每个度量键在发现里的映射，测量与豁免都按同一张表走
_METRIC_KEYS = ("duplicate_blocks", "duplicate_lines",
                "near_duplicate_functions", "similar_file_pairs")


def _blank_counts():
    return dict((key, 0) for key in _METRIC_KEYS)


def _matching_entry(finding, entries):
    """返回解释该发现的条目下标；没有 why 的条目不算解释。

    条目的 `kind` 也要对上：同一组 match 上可能挂两类发现（既是一个重复块，
    也是一对相似文件），只看 match 会让先出现的那条把后面的永久遮蔽，另一条
    于是被误报"已不再命中"（过期）。kind 是 `a + b` 形式，含本发现的种类才算。
    """
    identity = _file_identity(finding)
    kind = str(finding.get("kind") or "")
    for index, entry in enumerate(entries):
        if not str(entry.get("why") or "").strip():
            continue
        declared = str(entry.get("kind") or "")
        if declared and kind and kind not in declared:
            continue
        match = [str(item) for item in (entry.get("match") or [])]
        if match and all(any(tag in item for item in identity) for tag in match):
            return index
    return None


def classify(result, entries):
    """把发现分成"原始"与"已登记解释"两组，并记下命中的豁免条目。"""
    raw = _blank_counts()
    explained = _blank_counts()
    hit = set()
    for finding in all_findings(result):
        kind = finding["kind"]
        if kind == "block":
            raw["duplicate_blocks"] += 1
            raw["duplicate_lines"] += finding["redundant"]
        elif kind == "function_pair":
            raw["near_duplicate_functions"] += 1
        elif kind == "file_pair":
            raw["similar_file_pairs"] += 1
        index = _matching_entry(finding, entries)
        if index is None:
            continue
        hit.add(index)
        if kind == "block":
            explained["duplicate_blocks"] += 1
            explained["duplicate_lines"] += finding["redundant"]
        elif kind == "function_pair":
            explained["near_duplicate_functions"] += 1
        elif kind == "file_pair":
            explained["similar_file_pairs"] += 1
    return raw, explained, hit


def unexplained_where(key, result, entries):
    """某一类里**未被豁免解释**的发现位置，供退化时点名。"""
    wanted = {"duplicate_blocks": "block",
              "near_duplicate_functions": "function_pair",
              "similar_file_pairs": "file_pair"}.get(key)
    if wanted is None:
        # duplicate_lines 是块级计数的派生量，位置就是块的位置
        wanted = "block"
    found = []
    for finding in all_findings(result):
        if finding.get("kind") != wanted:
            continue
        if _matching_entry(finding, entries) is not None:
            continue
        found.append("、".join(_file_identity(finding)))
    return found[:10]


def check(root, result, out):
    """比对基线并核对豁免清单；返回进程退出码。

    基线记录的是**未登记**的重复数字：能被豁免清单解释的重复不计入。因此
    "数字只能变好或按豁免清单解释"——改善会让数字下降，新增的重复要么改掉，
    要么在豁免清单里写清 why。清单里的条目若不再命中任何重复，说明该重复已
    消失，条目本身成了过期记录，同样要清掉。
    """
    baseline = load_json(Path(root) / BASELINE_NAME, {})
    allowed = load_json(Path(root) / ALLOW_NAME, {"entries": []})
    entries = allowed.get("entries")
    if not isinstance(entries, list):
        out("{} 缺少 entries 列表".format(ALLOW_NAME))
        return _EXIT_REGRESSION
    raw, explained, hit = classify(result, entries)
    measured = dict((key, raw[key] - explained[key]) for key in _METRIC_KEYS)

    measured_raw = baseline.get("measured")
    if isinstance(measured_raw, dict):
        # 实测原始数字：门禁看的是"扣掉已登记理由后还剩多少"，这里给总数，
        # 让"仓库一共测出多少、比上次多了哪一类"可见
        out("实测原始数字（未扣豁免）：" + "、".join(
            "{}={}".format(key, raw[key]) for key in _METRIC_KEYS))
        drift = [key for key in _METRIC_KEYS
                 if key in measured_raw and raw[key] > measured_raw[key]]
        if drift:
            out("  比基线记录的实测值又涨了：" + "、".join(drift)
                + "（记得同步 .dupcheck-baseline.json 的 measured）")

    problems = []
    for key in _METRIC_KEYS:
        limit = baseline.get(key)
        if limit is None:
            out("基线缺少 {}：请先用当前数字生成 {}".format(key, BASELINE_NAME))
            problems.append(key)
            continue
        if measured[key] > limit:
            out("重复度退化: {} 由 {} 升至 {}（原始 {} / 已登记 {}）".format(
                key, limit, measured[key], raw[key], explained[key]))
            # 指出"新增的重复在哪两处"：只说计数，人还得自己把整份报告翻一遍。
            # 这是门禁最常被问的问题（"这次多了哪几条"）
            for where in unexplained_where(key, result, entries):
                out("    " + where)
            problems.append(key)

    for index, entry in enumerate(entries):
        if not str(entry.get("why") or "").strip():
            out("豁免无效: 条目 {} 缺少 why 字段".format(
                entry.get("match") or index))
            problems.append("no-why")
        elif index not in hit:
            out("豁免清单过期: 条目 {} 已不再命中任何重复".format(
                entry.get("match")))
            problems.append("stale")

    if problems:
        return _EXIT_REGRESSION
    out("重复度未超过基线：{}".format("、".join(
        "{}={}（已登记 {}）".format(key, measured[key], explained[key])
        for key in _METRIC_KEYS)))
    return _EXIT_OK


# ------------------------------------------------------------------ 目录

def signature(node):
    """把形参表渲染成一行签名，默认值只显示"有"不展开。

    只关心"拿什么调用"，所以默认值写成 `=…`：把默认值原样打出来会让长签名
    折行，反而不便扫读。
    """
    arguments = node.args
    parts = []
    positional = list(getattr(arguments, "posonlyargs", [])) + \
        list(arguments.args)
    defaults = list(arguments.defaults)
    offset = len(positional) - len(defaults)
    for index, item in enumerate(positional):
        text = item.arg
        if index >= offset:
            text += "=…"
        parts.append(text)
    if arguments.vararg is not None:
        parts.append("*" + arguments.vararg.arg)
    elif arguments.kwonlyargs:
        parts.append("*")
    for item, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        parts.append(item.arg + ("=…" if default is not None else ""))
    if arguments.kwarg is not None:
        parts.append("**" + arguments.kwarg.arg)
    return "{}({})".format(getattr(node, "name", ""), ", ".join(parts))


def catalog(root=ROOT, out=None):
    """列出**生产代码**里的公开函数签名，按名字分组。

    从 AST 取，不执行代码：只想知道"这件事是不是已经有人做过"，一分钟能查完。
    看的是 `production_files` 那份范围——与度量同一份，所以 `ci/stages` 与两个
    入口脚本也在内，不会出现"度量扫得到、目录里查不到"的死角。
    """
    out = out or print
    groups = {}
    for path in production_files(root):
        tree = _parse(path)
        if tree is None:
            continue
        for node in tree.body:
            if not isinstance(node, _FUNCTION_KINDS):
                continue
            symbol = getattr(node, "name", "")
            if symbol.startswith("_"):
                continue
            groups.setdefault(symbol, []).append(
                "{}:{}  {}".format(_relative(root, path), node.lineno,
                                   signature(node)))
    for symbol in sorted(groups):
        out(symbol)
        for item in groups[symbol]:
            out("    " + item)


# ------------------------------------------------------------------ 入口

def build_parser():
    parser = argparse.ArgumentParser(
        description="重复度度量：精确重复块、函数级近似重复、整文件相似度")
    parser.add_argument("--root", default=str(ROOT),
                        help="仓库根（默认本文件所在仓库）")
    parser.add_argument("--check", action="store_true",
                        help="比对基线，退化或以非零退出")
    parser.add_argument("--catalog", action="store_true",
                        help="列出公开函数签名，供复用查询")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    root = Path(args.root)
    if args.catalog:
        catalog(root)
        return _EXIT_OK
    result = measure(root)
    if args.check:
        return check(root, result, print)
    print(render(result))
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
