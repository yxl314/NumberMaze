#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""注释与文档的质量检查。

注释是写给"只有当前快照"的读者的：他看不到这次改动的来龙去脉，也看不到任何
外部材料。所以注释里出现需求来源、变更历史、行号日期这类信息时，读者拿到的
要么是读不懂的碎片，要么是很快失真的账本——两者都让注释变成负担。

检查的四件事：
  * 只写代码本身 —— 不出现需求来源、讨论、任务背景；
  * 不把读者拒之门外 —— 不出现指向别处的引用；
  * 不记录易变信息 —— 不出现日期、行号、变更叙述；
  * 简洁精炼 —— 单条注释不超过行数上限，且不写大段铺陈。

除了注释与文档，还查几条能客观判定的代码纪律：

  * 简洁易懂 —— 模块级定义与 import 是否有没人用的（多余的东西就该删）；
  * 少用难跨平台的写法 —— 平台专有 API 是否集中在 platform.py；
  * 禁止静默回退 —— 异常被吞掉之后既不记录也不返回，等于无声换了一条路；
  * 底层屏蔽 / 最小暴露 —— 是否跨模块引用别人的私有名。

判据都取"能证伪"的形态：宁可漏报也不误报——误报会让人学会忽略这个检查。
"""

import ast
import io
import os
import re
import sys
import tokenize
from pathlib import Path

# 单条注释（含连续 # 行组成的一块）的行数上限。超过它多半是在讲一个故事，
# 而不是在说明代码
MAX_COMMENT_LINES = 8
MAX_DOCSTRING_LINES = 40

# 需求来源、讨论与任务背景：读者拿不到这些材料
EXTERNAL = (
    "提案", "改进提案", "需求来源", "任务背景", "对话", "本轮", "本次改动",
    "规则清单", "拍板", "评审意见", "issue", "票据",
)

# 变更叙述：注释该说"现在是什么"，不是"过去是什么"
HISTORY = (
    "曾经", "原先", "改动前", "早先", "最初", "以前是", "原来是", "返工",
    "踩过", "踩到过", "实测过", "修过", "旧版", "老实现",
)

# 指向别处：注释必须能独立读懂
POINTER = (
    "见上文", "如前所述", "参见", "如上所述", "下述", "见 §", "见第",
)

# 易变信息：日期、行号
VOLATILE = (
    re.compile(r"\b20\d\d[-/年]\d\d?\b"),
    re.compile(r"第\s*\d+\s*行"),
)

# 整文件豁免：检查器自身必须提到它检查的那些词（规则表就是由它们构成的），
# 拿它去查自己只会得到一堆"规则表命中规则"的噪声
EXEMPT_FILES = ("rules_check.py",)

# 白名单：整行命中这些说明它是在讲正当概念，不是在记账
ALLOW = (
    # doctor 的"上次结论"指上一轮运行的结果，是工具的概念而不是改动史
    re.compile(r"上次(结论|运行|跑|的结果)"),
    # "原来的字节"这类指输入数据的原始形态
    re.compile(r"原先的(字节|编码|换行)"),
)


def _read(path):
    handle = open(str(path), "r", encoding="utf-8")
    try:
        return handle.read()
    finally:
        handle.close()


def _standalone(text, token):
    """这个注释是不是独占一行（行首除了空白没有别的东西）。

    行尾注释不参与"注释块有多长"的判定：一整列代码行各带一句行尾说明是正常的
    写法，把它们连起来当成一个几十行的注释块，量到的是代码行数而不是注释长度。
    """
    line = text.splitlines()[token.start[0] - 1]
    return not line[:token.start[1]].strip()


def _comment_blocks(path):
    """产出 (起始行号, 行数, 文本) —— 连续相邻且各自独占一行的 # 行算一块。"""
    text = _read(path)
    lines = text.splitlines()
    block, start, previous = [], 0, 0
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type != tokenize.COMMENT:
            continue
        line = token.start[0]
        if not _standalone(text, token):
            # 行尾注释：内容照查，但不成块、不计长度
            yield line, 1, token.string
            block, previous = [], line
            continue
        if block and line == previous + 1:
            block.append(token.string)
        else:
            if block:
                yield start, len(block), "\n".join(block)
            block, start = [token.string], line
        previous = line
    if block:
        yield start, len(block), "\n".join(block)


def _docstrings(path):
    """产出 (起始行号, 行数, 文本)。"""
    try:
        tree = ast.parse(_read(path))
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = getattr(body[0].value, "value", None)
        if not isinstance(value, str):
            continue
        yield body[0].lineno, len(value.splitlines()), value


def _allowed(text):
    return any(pattern.search(text) for pattern in ALLOW)


def check_text(text):
    """一段注释/文档里的违规，返回 [(规则名, 命中片段)]。"""
    found = []
    for line in text.splitlines():
        if _allowed(line):
            continue
        for word in EXTERNAL:
            if word in line:
                found.append(("只写代码本身", word))
        for word in HISTORY:
            if word in line:
                found.append(("不记录易变信息（变更叙述）", word))
        for word in POINTER:
            if word in line:
                found.append(("不把读者拒之门外", word))
        for pattern in VOLATILE:
            match = pattern.search(line)
            if match:
                found.append(("不记录易变信息（日期/行号）", match.group(0)))
    return found


def check_file(path, side_text="", test_text="", tree=None,
               check_unused=True):
    """一个文件里的全部违规，返回 [(行号, 规则名, 片段)]。"""
    if os.path.basename(str(path)) in EXEMPT_FILES:
        return []
    issues = []
    for start, count, text in _comment_blocks(path):
        for rule, hit in check_text(text):
            issues.append((start, rule, hit))
        if count > MAX_COMMENT_LINES:
            issues.append((start, "简洁精炼",
                           "注释块 {0} 行（上限 {1}）".format(
                               count, MAX_COMMENT_LINES)))
    for start, count, text in _docstrings(path):
        for offset, line in enumerate(text.splitlines()):
            for rule, hit in check_text(line):
                issues.append((start + offset, rule, hit))
        if count > MAX_DOCSTRING_LINES:
            issues.append((start, "简洁精炼",
                           "文档字符串 {0} 行（上限 {1}）".format(
                               count, MAX_DOCSTRING_LINES)))
    if tree is not None:
        issues += _code_issues(path, tree, side_text)
        if check_unused:
            issues += _unused_issues(path, tree, side_text, test_text)
    return sorted(set(issues))


# ---------------------------------------------------------------- 代码纪律

# 平台专有接口：应集中在 platform.py（少用难跨平台的写法，确需使用时隔离一处）
PLATFORM_APIS = re.compile(
    r"\bos\.fork\b|\bos\.setsid\b|\bsignal\.|\btermios\b|\bfcntl\b|"
    r"/proc/|/sys/|os\.uname\b|sys\.platform\b|platform\.system\b|"
    r"\bos\.geteuid\b|\bpty\b")

PLATFORM_HOME = "platform.py"

# 处理器里出现这些调用，说明错误被记录了，不算静默
REPORTERS = ("err", "warn", "log", "out", "noise", "print", "fail",
             "summary", "append", "stderr", "raise")


def _ends_the_flow(node):
    last = node.body[-1] if node.body else None
    return isinstance(last, (ast.Return, ast.Raise))


def _records(node):
    for call in [item for item in ast.walk(node) if isinstance(item, ast.Call)]:
        name = ""
        if isinstance(call.func, ast.Name):
            name = call.func.id
        elif isinstance(call.func, ast.Attribute):
            name = call.func.attr
        if any(token in name for token in REPORTERS):
            return True
    return False


def _has_comment(path, start, end):
    for number, _count, text in _comment_blocks(path):
        if start <= number <= end:
            return True
    return False


def _code_issues(path, tree, side_sources):
    """代码纪律：静默回退、平台写法散落、跨模块私有名。"""
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # 测试里吞异常常是"清理时尽力而为"，不属于生产路径
            if "tests" in str(path).split(os.sep):
                continue
            # 吞掉错误之后既不记录也不返回，就是"无声地换了一条路"
            if _ends_the_flow(node) or _records(node):
                continue
            last = node.body[-1] if node.body else node
            if _has_comment(path, node.lineno,
                            last.end_lineno or node.lineno):
                continue
            if all(isinstance(item, (ast.Pass, ast.Continue, ast.Break))
                   for item in node.body):
                issues.append((node.lineno, "禁止静默回退",
                               "异常被吞掉，既不记录也不返回"))
        if isinstance(node, ast.ImportFrom):
            # 底层屏蔽：跨模块引用别人的私有名等于绕过它对外的那层
            module = node.module or ""
            if not (node.level or "." in module):
                continue
            for alias in node.names:
                name = alias.name
                if name.startswith("_") and not name.startswith("__"):
                    issues.append((node.lineno, "底层屏蔽",
                                   "从 {} 引用私有名 {}".format(module, name)))

    name = os.path.basename(str(path))
    if name != PLATFORM_HOME and "tests" not in str(path).split(os.sep):
        text = _read(path)
        for number, line in enumerate(text.splitlines(), 1):
            match = PLATFORM_APIS.search(line)
            if match and not line.lstrip().startswith("#"):
                issues.append((number, "少用难跨平台的写法",
                               "{} 应集中在 {}".format(match.group(0),
                                                       PLATFORM_HOME)))
    return issues


def _unused_issues(path, tree, side_text, test_text):
    """简洁易懂：没人用的模块级定义与 import。"""
    issues = []
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    used |= {n.value.id for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    exported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "__all__" for t in node.targets):
            try:
                exported |= set(ast.literal_eval(node.value))
            except ValueError:
                pass
    lines = _read(path).splitlines()
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported[alias.asname or alias.name.split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported[alias.asname or alias.name] = node.lineno
    for name, lineno in sorted(imported.items()):
        if name in used or name == "annotations" or name in exported:
            continue
        text = lines[lineno - 1] if lineno <= len(lines) else ""
        if "noqa" in text:
            continue
        issues.append((lineno, "简洁易懂", "未使用的 import: " + name))
    for node in tree.body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0],
                                                         ast.Name):
            names = [node.targets[0].id]
        for name in names:
            if name.startswith("__"):
                continue
            if side_text.count(name) + test_text.count(name) <= 1:
                issues.append((node.lineno, "简洁易懂",
                               "没有引用的定义: " + name))
    return issues


# 文档：规则清单管的是"注释与文档"，README / 契约 / 示例配置同样要对读者负责
DOC_FILES = (
    "README.md", "CONTRACT.md", "INSTALL.md", "ci/README.md",
    "standardize/README.md", "ci/CHANGELOG.md", "standardize/CHANGELOG.md",
    "ci/config/config.py.example", "standardize/config/config.py.example",
    "run_all.sh", "run_tests.sh", "requirements/ci.txt",
    "requirements/standartize.txt",
)


def check_doc(path):
    """按行查一份文档，返回 [(行号, 规则名, 片段)]。"""
    issues = []
    try:
        text = _read(path)
    except (IOError, OSError, UnicodeDecodeError):
        return issues
    for number, line in enumerate(text.splitlines(), 1):
        for rule, hit in check_text(line):
            issues.append((number, rule, hit))
    return issues


def sources(root):
    for base, dirs, names in os.walk(str(root)):
        dirs[:] = [name for name in dirs
                   if name not in ("__pycache__", ".git", "build",
                                   "third_party")]
        for name in sorted(names):
            if name.endswith(".py"):
                yield os.path.join(base, name)


def _corpus(paths):
    """把若干文件拼成一段语料，供"这个名字有没有人用"判定。"""
    parts = []
    for path in paths:
        try:
            parts.append(_read(path))
        except (IOError, OSError, UnicodeDecodeError):
            continue
    return "\n".join(parts)


def report_file(path, side_text="", test_text="", check_unused=True):
    """查一个文件，打印每条违规，返回条数。"""
    try:
        tree = ast.parse(_read(path))
    except (SyntaxError, UnicodeDecodeError):
        tree = None
    total = 0
    for number, rule, hit in check_file(path, side_text, test_text, tree,
                                        check_unused):
        sys.stdout.write("{0}:{1}: [{2}] {3}\n".format(path, number, rule,
                                                        hit))
        total += 1
    return total


def report(root):
    """查一棵树，返回问题数。"""
    return sum(report_file(path) for path in sources(str(root)))


# 两侧各自的源码树与测试树：查"没人用"时要连测试一起看——只在源码里找引用，
# 会把"只有测试用"的辅助误判成多余
SIDES = (
    ("ci", ("ci/lib", "ci/stages", "ci/ci.py"), "ci/tests"),
    ("standardize", ("standardize/src",), "standardize/tests"),
)
TOOL_FILES = ("tools/dupcheck.py", "tools/export_requirements.py")


def main(argv):
    here = Path(__file__).resolve().parent.parent
    targets = [item for item in argv[1:] if not item.startswith("-")]
    total = 0
    if targets:
        for target in targets:
            total += report(target)
    else:
        for _name, source_dirs, test_dir in SIDES:
            # 整侧的源码与测试合成语料，供"有没有人用"判定
            files = []
            for name in source_dirs:
                target = here / name
                if not target.exists():
                    continue
                if target.is_file():
                    files.append(target)
                else:
                    files += [Path(item) for item in sources(str(target))]
            test_root = here / test_dir
            side_text = _corpus(files)
            test_text = (_corpus(sources(str(test_root)))
                         if test_root.exists() else "")
            for target in files:
                total += report_file(target, side_text, test_text)
            # 测试本身也要合注释纪律；测试函数由框架发现，不查"没人用"
            if test_root.exists():
                for target in sources(str(test_root)):
                    total += report_file(target, check_unused=False)
        # 独立脚本：它的 main / build_parser 就是入口，不查"没人用"
        for name in TOOL_FILES:
            target = here / name
            if target.exists():
                total += report_file(target, check_unused=False)
        for name in DOC_FILES:
            target = here / name
            if not target.exists():
                continue
            for number, rule, hit in check_doc(str(target)):
                sys.stdout.write("{0}:{1}: [{2}] {3}\n".format(
                    name, number, rule, hit))
                total += 1
    sys.stdout.write("共 {0} 处\n".format(total))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
