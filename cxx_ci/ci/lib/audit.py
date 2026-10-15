#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置审计：把"代码必须严格经配置取值"变成可机检的纪律。

门禁脚本自身也是最容易被绕过的代码：有人图省事在脚本里写死路径或工具名，配置
就慢慢变成摆设——改配置不生效，行为随文件漂移。这里只读地比对配置与源码，三条
判据各自对应一种漂移：

1. 死配置：枚举配置对象上的全部公开键，若某个键名在所有被审计的源码里从未被
   引用，就报出来。枚举的是运行时真正存在的键（配置对象属性），不是配置文件里
   的文本行——拼错、没接线、加载器根本不认的项不会出现在配置对象上。
2. 配置旁路：被审计的项目源码里出现与某个配置键取值完全相同的字符串字面量，
   说明有人绕过配置直接写死了同一个值，改配置时那处不会跟着变。文档字符串是
   说明文字而非取值，因此排除。工具自身的实现代码不在这一项的范围里：它必须
   包含构建工具名、诊断级别名这类词汇表，判红只会让审计永远不绿，反而没人看。
3. 编译标准锁定：构建文件里的承载变量必须存在且等于锁定的标准，任何一处别的
   -std= 都判红——注释里的示例也会被 qmake 当真，这是真实事故。
4. 裸写文本：工具自身实现里出现绕过 source.py 直接写回文本的调用，会把非
   UTF-8 的存量工程静默写成 U+FFFD——这是不可逆的数据损坏，必须拦住。
5. 自行前缀比较：工具自身实现里又自行拼前缀判断"路径是否在某目录内"，而全仓
   只允许 paths.py 一处实现（归一化策略统一为 realpath）。两处各拼一遍前缀、
   归一化方式还不一致，就会出现同一份诊断在不同阶段归因相反的问题——每处单看
   都对，合起来矛盾。这里拦新增，存量已收口。

判据都要能审自己：本文件自身在检查 2 的范围内，因此这里不写任何与配置取值相同
的字面量，构建工具的区分也只看构建文件后缀，不比对工具名。

本文件与 standardize/src/cxx_standardize/audit.py 为平行实现：两个项目互不 import，
只靠文件与命令行契约协作（见 CONTRACT.md），因此同一套纪律被写成两份。改动须同步
两侧，并跑 tools/parity 的同固件对照测试（两侧各一份 test_audit_parity.py），
否则两侧规则会静默分叉。参照实现是 standardize 侧。

失败不抛异常，返回 (错误码, 说明行列表)，与项目其余部分一致。
"""
import sys

if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")

import ast
import difflib
import glob
import os
import re
from pathlib import Path

from .config import (CONFIG_METADATA, KEYS_OUTSIDE_METADATA,
                     NON_CONFIG_KEYS)
from .errors import (AUDIT_BYPASS_LITERAL, AUDIT_DEAD_KEY, AUDIT_PREFIX_DRIFT,
                     AUDIT_RAW_IO, AUDIT_STD_DRIFT, AUDIT_UNKNOWN_KEY, OK)

# 本工具的根目录（ci/）；被审计的自身实现代码都在它下面
TOOL_ROOT = Path(__file__).resolve().parents[1]

ENTRY_FILE = "ci.py"
SOURCE_DIRS = ("lib", "stages")
CONFIG_DIR = "config"
CONFIG_NAME = "config.py"

PY_SUFFIX = ".py"
PRO_SUFFIX = ".pro"
CMAKE_BUILD_NAME = "CMakeLists.txt"
CMAKE_STD_VAR = "CMAKE_CXX_STANDARD"
STD_MARK = "-std="

# 递归扫描时跳过的目录：版本库元数据与字节码缓存，都不是构建文件
SKIP_DIRS = (".git", "__pycache__")

# 配置对象上由加载流程写上去的提示，不是项目配置项
# 从配置对象取值时用的名字。按名字认，见 _is_config_base
CONFIG_NAMES = ("cfg", "config", "conf", "settings")

# 本工具的实现根：检查 2 据此把工具自身排除在外，见 _bypass_scope
_TOOL_SELF = os.path.abspath(str(Path(__file__).resolve()))

# 检查 4 的裸写文本写法：绕过 source.py 直接写回文本。唯一白名单是 source.py
# ——它本身就是那个写入口。模式串写成正则片段而不是字面调用，这样本文件不会
# 被自己的检查命中
RAW_IO_WHITELIST = "source.py"
# 只扫 lib/：那是工具真正持有文件 I/O 的一层。stages/ 是薄编排层，且集成测试
# 会用桩脚本覆写它——把桩里的正常写文件算成工具源码的裸写，是测试夹具的假阳性，
# 不是纪律本身要拦的东西
RAW_IO_SCOPE_DIRS = ("lib", "ci.py", "run_all.sh")
# 允许一层嵌套括号的片段，单独拼出来（见下）
_NESTED_PARENS = r"(?:[^()]|\([^()]*\))*"
RAW_IO_PATTERNS = (
    # 允许一层嵌套括号。真实代码里模式的取法是"先 str() 或 os.path.join()，
    # 再给写入模式"，而 [^)]* 会在内层右括号处断掉——实测这条规则曾对全仓真实
    # 代码 0 命中。示例不写全（写全了这一行自己就会被命中），嵌套片段也单独拼
    ("open", re.compile(r"open\s*\(" + _NESTED_PARENS + r"[\"']w[\"']")),
    ("write_text", re.compile(r"\.write_text\s*\(")),
    ("encode", re.compile(r"\.encode\s*\(\s*[\"']utf-8")),
)
# 每项豁免都要写清理由：这些写是工具自有产物或字节通道，不是用户源码。
# 豁免清单不得当垃圾桶——新增一项必须能说清"为什么它不经过 source.py 也安全"
RAW_IO_EXEMPT = {
    "ci.py": {
        "open": "init 生成的是工具自有产物（<工程>/config/ci.py），写的是本工具"
                "渲染出来的配置文本，不是用户源码；生成物恒定 UTF-8。它本身就是"
                "配置，而不是被工具改写的用户源码，因此不经过 source.py",
    },
    "platform.py": {
        "open": "互斥锁文件里只写一个 pid，是工具自有的运行期产物，不是用户源码；"
                "它没有原编码可言，也不必原子替换——内核的 flock 才是互斥本身",
    },
    "tools.py": {"encode": "子进程输出编码，走字节通道而不是写文件"},
    "valgrind.py": {"encode": "哈希材料需要固定编码，不是文件写入"},
    "report.py": {
        "open": "报告是工具自有的旁路产物（JSON/XML/SARIF），不是用户源码，"
                "不经过 source.py；写的是本工具生成的内容",
        "encode": "SARIF 指纹材料需要固定编码，不是文件写入",
    },
}

_CONSTANT = getattr(ast, "Constant", None)
_STR = getattr(ast, "Str", None)
_BYTES = getattr(ast, "Bytes", None)
_ASYNC_DEF = getattr(ast, "AsyncFunctionDef", None)


# ------------------------------------------------------------------ 审计对象

def default_targets(config):
    """缺省审计对象：本工具自身的实现代码 + 项目配置 + AUDIT_FILES_GLOBS。

    自身实现代码让"审计能审自己"成立；项目配置文件与 AUDIT_FILES_GLOBS 让项目
    自己的脚本也进纪律范围。目录交给 collect_files 递归取 *.py。
    """
    targets = []
    entry = TOOL_ROOT / ENTRY_FILE
    if entry.is_file():
        targets.append(entry)
    for name in SOURCE_DIRS:
        directory = TOOL_ROOT / name
        if directory.is_dir():
            targets.append(directory)
    config_file = TOOL_ROOT / CONFIG_DIR / CONFIG_NAME
    if config_file.is_file():
        targets.append(config_file)
    root = getattr(config, "root", None)
    if root:
        for pattern in getattr(config, "audit_files_globs", None) or []:
            for found in _expand_glob(str(root), str(pattern)):
                targets.append(Path(found))
    return targets


def _expand_glob(root, pattern):
    """展开配置里的相对 glob。

    Python 3.4 的 glob 不认 **（递归通配是 3.5 才有的），这里自己按相对路径逐级
    匹配，避免配置写了 tools/**/*.py 却一声不响地什么都没扫到。
    """
    if "**" not in pattern:
        return sorted(glob.glob(os.path.join(root, pattern)))
    matcher = re.compile(_glob_regex(pattern))
    found = []
    for current, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in names:
            relative = os.path.relpath(os.path.join(current, name), root)
            if matcher.match(relative.replace(os.sep, "/")):
                found.append(os.path.join(current, name))
    return sorted(found)


def _glob_regex(pattern):
    """把 glob 翻成正则：**/ 跨目录，* 不跨目录。"""
    pieces = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            pieces.append("(?:[^/]+/)*")
            index += 3
        elif pattern.startswith("**", index):
            pieces.append(".*")
            index += 2
        elif pattern[index] == "*":
            pieces.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            pieces.append("[^/]")
            index += 1
        else:
            pieces.append(re.escape(pattern[index]))
            index += 1
    return "^" + "".join(pieces) + "$"


def collect_files(targets):
    """把审计对象展开成待解析的 py 文件：目录递归，只留 *.py 后缀。"""
    found = []
    seen = set()
    for target in targets:
        path = Path(str(target))
        if path.is_dir():
            for child in sorted(path.rglob("*" + PY_SUFFIX), key=str):
                _remember(found, seen, child)
        elif path.is_file() and path.name.endswith(PY_SUFFIX):
            _remember(found, seen, path)
    return found


def _remember(found, seen, path):
    """按绝对路径去重：同一份文件可能既被目录扫到又被 glob 点到。"""
    absolute = os.path.abspath(str(path))
    if absolute in seen:
        return
    seen.add(absolute)
    found.append(path)


# ------------------------------------------------------------------ 配置面

def config_keys(config):
    """配置对象上的公开键。

    从实例属性取，而不是从配置文件文本取：只有真正挂到配置对象上的名字才是
    运行时可用的开关，配置文件里的错字不会出现在这里。
    """
    fields = getattr(config, "__dict__", None) or {}
    names = []
    for name in fields:
        if name.startswith("_") or name in NON_CONFIG_KEYS:
            continue
        names.append(name)
    return sorted(names)


def _collect_values(value, found):
    """把一个配置取值展开成字符串集合，容器与字典逐层展开。

    Path 也折算成字符串：配置里路径写成 Path 还是 str 不该改变判据。
    """
    if isinstance(value, (str, bytes)):
        if value:
            found.add(value)
    elif isinstance(value, Path):
        found.add(str(value))
    elif isinstance(value, dict):
        for key, item in value.items():
            _collect_values(key, found)
            _collect_values(item, found)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _collect_values(item, found)


def _value_owners(config):
    """字符串取值到键名的映射，用于把命中的字面量指回它该用的配置项。"""
    owners = {}
    for key in config_keys(config):
        values = set()
        _collect_values(getattr(config, key, None), values)
        for value in sorted(values, key=str):
            if value not in owners:
                owners[value] = key
    return owners


# ------------------------------------------------------------------ 源码解析

def _string_value(node):
    """取出字符串/字节字面量的取值；不是字面量时返回 None。

    Python 3.4 把字符串与字节分成两类节点，3.6 起统一成 Constant；两种版式都认，
    本文件才能在老机器上跑。
    """
    if _CONSTANT is not None and isinstance(node, _CONSTANT):
        value = node.value
        if isinstance(value, (str, bytes)):
            return value
        return None
    if _STR is not None and isinstance(node, _STR):
        return node.s
    if _BYTES is not None and isinstance(node, _BYTES):
        return node.s
    return None


def _referenced_names(tree):
    """源码里真的从配置对象上取过值的键名。

    只认"从配置对象取值"的写法：`cfg.KEY`、`config.KEY`，以及
    `getattr(config, "KEY", 默认)` 里的字符串实参。

    裸名字与关键字实参一律不算：配置加载器里必然写着 `self.KEY = KEY` 与
    `Config(KEY=...)`，那两处是"把配置装进对象"，不是"有人用它"。把它们算成
    引用，等于每个键都自己引用自己，这条检查就永远查不出东西。赋值目标同样
    不算；注释与文档字符串也不算——说明里提一句某个配置项，不代表有人用它。
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if isinstance(node.ctx, ast.Load) and _is_config_base(node.value):
                names.add(node.attr)
        elif isinstance(node, ast.Call):
            function = node.func
            if (isinstance(function, ast.Name) and function.id == "getattr"
                    and node.args and _is_config_base(node.args[0])):
                for argument in node.args[1:]:
                    literal = _string_value(argument)
                    if isinstance(literal, str):
                        names.add(literal)
    return names


def _is_config_base(node):
    """属性访问的基是不是"配置对象"这个名字。

    按名字认，而不是按类型：静态解析看不出类型，而各处的写法是固定的
    （`config.xxx` / `cfg.xxx`）。认名字就够，也避免了把 `self.xxx` 算进来。
    """
    return isinstance(node, ast.Name) and node.id in CONFIG_NAMES


def _docstring_ids(tree):
    """模块、类、函数体首个表达式的常量节点，它们不算取值。"""
    found = set()
    kinds = [ast.Module, ast.ClassDef, ast.FunctionDef]
    if _ASYNC_DEF is not None:
        kinds.append(_ASYNC_DEF)
    kinds = tuple(kinds)
    for node in ast.walk(tree):
        if not isinstance(node, kinds):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and _string_value(first.value) is not None:
            found.add(id(first.value))
    return found


def string_literals(tree):
    """源码里的字符串/字节常量，返回 (行号, 取值) 列表；文档字符串除外。"""
    skipped = _docstring_ids(tree)
    literals = []
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        value = _string_value(node)
        if not value:
            continue
        literals.append((node.lineno, value))
    return literals


def _parse(path):
    """解析一份源码；读不出来或语法不对时返回 None，不参与任何判据。

    工程里可能混着非 UTF-8 的注释或其他解释器才认的语法，审计不该因此崩掉：
    拿不准的文件既不当作"有人引用"，也不凭空要求一个键。
    """
    handle = open(str(path), "r", encoding="utf-8", errors="replace")
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


def _parse_all(paths):
    parsed = []
    for path in paths:
        tree = _parse(path)
        if tree is not None:
            parsed.append((path, tree))
    return parsed


# ------------------------------------------------------------------ 检查 1

def _check_dead(config, parsed, messages):
    """死配置：没有任何被审源码引用过的键。"""
    referenced = set()
    for _path, tree in parsed:
        referenced |= _referenced_names(tree)
    code = OK
    for key in config_keys(config):
        if key not in referenced:
            messages.append("死配置: cfg.{} 从未被引用".format(key))
            code = AUDIT_DEAD_KEY
    return code


# ------------------------------------------------------------------ 检查 2

def _is_tool_source(path):
    """是否本工具自身的实现代码（ci.py / lib / stages / config）。"""
    absolute = os.path.abspath(str(path))
    if absolute == _TOOL_SELF:
        return True
    entry = os.path.abspath(str(TOOL_ROOT / ENTRY_FILE))
    if absolute == entry:
        return True
    for name in SOURCE_DIRS + (CONFIG_DIR,):
        prefix = os.path.abspath(str(TOOL_ROOT / name)) + os.sep
        if absolute.startswith(prefix):
            return True
    return False


def _is_config_source(path):
    """是否项目配置文件本身：它写下的值就是配置值，等于自己不是旁路。"""
    absolute = os.path.abspath(str(path))
    return (os.path.basename(absolute) == CONFIG_NAME
            and os.path.basename(os.path.dirname(absolute)) == CONFIG_DIR)


def _bypass_scope(path):
    """检查 2 只看项目自己的文件。

    工具实现一律不看：它必然含 cmake/qmake、CMAKE_CXX_STANDARD 这类与配置取值
    同形的 schema 词汇，那些是"工具支持的取值"，不是"绕过配置写死的项目事实"。
    配置文件本身也不看：它写下的值就是配置值。
    """
    absolute = os.path.abspath(str(path))
    if _is_config_source(absolute):
        return False
    return not _is_tool_source(absolute)


def _check_unknown_keys(config, messages):
    """配置里写了本工具不认识的键。

    与"死配置"是一对：那条查"认识的键没生效"，这条查"写了却根本不认识"。
    错字与被重命名的旧项都落在这里。少了这条，使用者改了配置、工具照旧按缺省跑，
    谁也不知道那份配置没被读进去。
    """
    for name, near in probable_typos(config):
        messages.append(
            "配置项名疑似写错: {}，是不是想写 {}；本工具不认识这个名字，会整个"
            "忽略它".format(name, near))
    return AUDIT_UNKNOWN_KEY if messages else OK


# 疑似错字的相似度下限。实测：真错字（BUILD_DIRS / TEST_COMMNAD /
# MEMCHECK_COMMNAND / LOG_DIR）在 0.82 以上；另一侧的键（CLANG_TIDY_CONFIG /
# BUILD_COMMAND / TIDY_HEADER_FILTER）在 0.65 以下。取 0.75 把两者分开——
# 误报会把一份完全正常的配置判红，那比漏报坏得多
TYPO_SIMILARITY = 0.75


def unknown_keys(config):
    """配置里写了、但本工具不认识的大写名字。"""
    declared = getattr(config, "declared_keys", None) or frozenset()
    known = set(CONFIG_METADATA) | set(NON_CONFIG_KEYS) | set(
        KEYS_OUTSIDE_METADATA)
    return sorted(name for name in declared
                  if name.isupper() and name not in known)


def probable_typos(config):
    """不认识的键里，与某个已知键像到足以判定是错字的那些。

    返回 [(名字, 疑似本意)]。其余不认识的键交给 advise 提醒——它们多半归另一侧
    或本就是使用者自己定义的常量，不该judged成错误。
    """
    known = sorted(set(CONFIG_METADATA) | set(NON_CONFIG_KEYS) | set(
        KEYS_OUTSIDE_METADATA))
    found = []
    for name in unknown_keys(config):
        near = difflib.get_close_matches(name, known, n=1,
                                         cutoff=TYPO_SIMILARITY)
        if near:
            found.append((name, near[0]))
    return found


def _check_bypass(config, parsed, messages):
    """配置旁路：与配置取值完全相同的字符串字面量。"""
    owners = _value_owners(config)
    if not owners:
        return OK
    code = OK
    for path, tree in parsed:
        if not _bypass_scope(path):
            continue
        for line, value in string_literals(tree):
            key = owners.get(value)
            if key is None:
                continue
            messages.append("配置旁路: {}:{} 的字面量 {!r} 应改用 cfg.{}".format(
                path, line, value, key))
            code = AUDIT_BYPASS_LITERAL
    return code


# ------------------------------------------------------------------ 检查 3

def _qmake_project(config):
    """构建文件是 .pro 就是 qmake 系。

    按构建文件后缀分派而不是比对 BUILD_TOOL 的名字：名字是配置取值，写进本文件
    会被自己的检查 2 抓住；后缀由加载器与构建工具绑定，同样可靠。
    """
    build_file = str(getattr(config, "build_file", None) or "")
    return build_file.endswith(PRO_SUFFIX)


def _same_standard(left, right):
    """两个标准写法是否等价：-std=gnu++0x、gnu++0x、c++98、98 都归一化后比对。"""
    return _standard_token(left) == _standard_token(right)


def _standard_token(text):
    value = str(text).strip().strip('"\'')
    for prefix in (STD_MARK, "std="):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    for prefix in ("gnu++", "c++", "gnu", "c"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    return value.strip().strip('"\'')


def _std_tokens(line):
    """一行里出现的全部 -std= 取值（注释行与代码行一视同仁）。"""
    return re.findall(r"-std=[A-Za-z0-9_+.\-]+", line)


_PIN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _pin_parts(pin):
    """把 STD_PIN 拆成 (变量名或 None, 取值)。

    配置允许把承载变量名一起写进锁定值（QMAKE_CXXFLAGS_CXX11=-std=gnu++0x），
    这样"二选一"由配置作者当场定下来，不用审计再去猜 mkspec；只写取值也支持。
    """
    text = str(pin).strip()
    if "=" in text:
        name, value = text.split("=", 1)
        if _PIN_NAME.match(name.strip()) and value.strip():
            return name.strip(), value.strip()
    return None, text


def _assignment_value(line, name, qmake):
    """取构建文件里给承载变量赋的值；没有这一行时返回 None。"""
    pattern = re.escape(name)
    if qmake:
        match = re.match(r"^\s*" + pattern + r"\s*[+*]?=\s*(.*?)\s*$", line)
        return match.group(1) if match else None
    match = re.match(r"^\s*set\s*\(\s*" + pattern + r"\s+([^\s)]+)", line)
    if match:
        return match.group(1)
    match = re.match(r"^\s*" + pattern + r"\s*[:=]\s*(.+?)\s*$", line)
    return match.group(1) if match else None


def _carrier_locked(lines, carriers, pin, qmake):
    """承载变量存在且值等于锁定标准：任一允许变量命中即可。"""
    for line in lines:
        for name in carriers:
            value = _assignment_value(line, name, qmake)
            if value is not None and _same_standard(value, pin):
                return True
    return False


def _excluded_build_dirs(config):
    """构建文件扫描要跳过的目录。

    两类：①构建目录——那里的 CMakeLists 多是生成物；②`SCOPE.excluded` 里
    点名的目录——vendored 的第三方树自带构建文件是常态，它不属于本工程的
    标准锁定面。少了第二类，往工程里 vendor 一个带 CMakeLists 的库就会把
    "编译标准漂移"判红。
    """
    excluded = []
    for name in ("build_dir", "test_build_dir"):
        value = getattr(config, name, None)
        if value:
            excluded.append(os.path.abspath(str(value)) + os.sep)
    # 排除区取自 SCOPE.excluded（未写 SCOPE 时回落旧的 BLACKLIST）：
    # 与范围归因同一个解析器，不再各算一套
    from .paths import scope_roots
    _project, excluded_roots = scope_roots(config)
    for root in excluded_roots:
        excluded.append(os.path.abspath(str(root)) + os.sep)
    return excluded


def _build_files(config):
    """从项目根递归取构建文件：qmake 取 *.pro，cmake 取 CMakeLists.txt。"""
    root = getattr(config, "root", None)
    if not root or not os.path.isdir(str(root)):
        return []
    qmake = _qmake_project(config)
    excludes = _excluded_build_dirs(config)
    found = []
    for current, dirs, names in os.walk(str(root)):
        dirs[:] = [name for name in dirs
                   if name not in SKIP_DIRS
                   and not _under(os.path.join(current, name), excludes)]
        for name in names:
            if qmake:
                matched = name.endswith(PRO_SUFFIX)
            else:
                matched = name == CMAKE_BUILD_NAME
            if matched:
                found.append(Path(os.path.join(current, name)))
    return sorted(found, key=str)


def _under(path, prefixes):
    absolute = os.path.abspath(path) + os.sep
    for prefix in prefixes:
        if absolute.startswith(prefix):
            return True
    return False


def _read_lines(path):
    handle = open(str(path), "r", encoding="utf-8", errors="replace")
    try:
        return handle.read().splitlines()
    finally:
        handle.close()


def _check_std(config, messages):
    """编译标准锁定：承载变量缺失/值不符、任何一处别的 -std= 都判红。"""
    pin = str(getattr(config, "std_pin", None) or "").strip()
    if not pin:
        return OK
    files = _build_files(config)
    if not files:
        return OK
    qmake = _qmake_project(config)
    if qmake:
        carriers = [str(item) for item in
                    (getattr(config, "std_vars", None) or [])]
    else:
        carriers = [CMAKE_STD_VAR]
    pin_name, pin_value = _pin_parts(pin)
    if pin_name:
        # 锁定值点了名就以它为准：mkspec 的"二选一"由配置作者定，不由审计猜
        carriers = [pin_name]
    code = OK
    locked = 0
    for path in files:
        lines = _read_lines(path)
        if _mentions_carrier(lines, carriers):
            # 出现承载变量就得等于锁定值。没出现的按"继承"处理：cmake 的子目录
            # 文件不写 CMAKE_CXX_STANDARD 是常态，顶层那一处才管全局；要求每个
            # 文件都写一遍，等于把正常工程判成漂移
            locked += 1
            if not _carrier_locked(lines, carriers, pin_value, qmake):
                messages.append(
                    "编译标准漂移: {} 的承载变量 {} 取值不等于 {}".format(
                        path, "、".join(carriers), pin_value))
                code = AUDIT_STD_DRIFT
        for number, line in enumerate(lines, 1):
            for token in _std_tokens(line):
                if not _same_standard(token, pin_value):
                    messages.append(
                        "编译标准漂移: {}:{} 出现其它标准 {}".format(
                            path, number, token))
                    code = AUDIT_STD_DRIFT
    if not locked:
        # 一处都没有 = 没有任何东西钉住标准，等于没锁
        messages.append(
            "编译标准漂移: 全部构建文件都没有承载变量 {}，标准未被锁定".format(
                "、".join(carriers) if carriers else "（STD_VARS 未配置）"))
        code = AUDIT_STD_DRIFT
    return code


def _mentions_carrier(lines, carriers):
    """这些行里有没有出现承载变量。"""
    for line in lines:
        for carrier in carriers:
            if carrier and carrier in line:
                return True
    return False


# ------------------------------------------------------------------ 检查 4

def _in_raw_io_scope(path):
    """裸写文本只扫工具持有文件 I/O 的那一层，见 RAW_IO_SCOPE_DIRS。"""
    absolute = os.path.abspath(str(path))
    for name in RAW_IO_SCOPE_DIRS:
        prefix = os.path.abspath(str(TOOL_ROOT / name)) + os.sep
        if absolute.startswith(prefix):
            return True
    return False


def _check_raw_io(paths, messages):
    """裸写文本：绕过 source.py 直接写回文本的调用。

    只扫工具自身的实现代码：项目脚本里出现的写文本多半是别人的正常代码，判红
    只会让审计永远不绿。判定不出编码的文件会被静默写成 U+FFFD，这是不可逆的，
    因此这里必须拦住后来人又写回裸调用。
    """
    code = OK
    for path in paths:
        if not _in_raw_io_scope(path):
            continue
        name = os.path.basename(str(path))
        if name == RAW_IO_WHITELIST:
            continue
        exempt = RAW_IO_EXEMPT.get(name, {})
        for number, line in enumerate(_read_lines(path), 1):
            for rule, pattern in RAW_IO_PATTERNS:
                if rule in exempt or not pattern.search(line):
                    continue
                messages.append(
                    "裸写文本: {}:{} 绕过了 source.py，非 UTF-8 的存量文件"
                    "会被写成 U+FFFD".format(path, number))
                code = AUDIT_RAW_IO
    return code


# ------------------------------------------------------------------ 检查 5

# 要拦的写法标记：字符串方法名与路径分隔符同时出现在一行。标记拆成片段拼装，
# 而不是写成完整字面串，本文件才不会被自己这条检查命中
PREFIX_MARK = "startswith" + "("
# 两种分隔符写法都要拦：os.sep 是"知道有这回事"的写法，写成裸斜杠字面量的是
# "忘了"的写法。只拦前者时后一种一样会漏（实测漏过）
SEP_MARKS = ("os.sep", '"/"', "'/'")
# 唯一白名单：paths.py 就是那个"唯一实现"，它当然要自己写前缀比较
PREFIX_WHITELIST = "paths.py"


def _check_prefix_compare(files, messages):
    """自行前缀比较：工具自身实现里另写了一遍路径包含判断。

    全仓只允许 paths.py 一处实现（归一化统一为 realpath）。别处图省事再拼一遍
    前缀，就会重蹈 abspath/realpath 混用、同一份诊断在不同阶段归因相反的覆辙。
    这里只拦"自身实现"码；项目脚本不在范围，也不比对工具名。
    """
    code = OK
    for path in files:
        if not _is_tool_source(path):
            continue
        if os.path.basename(str(path)) == PREFIX_WHITELIST:
            continue
        for number, line in enumerate(_read_lines(path), 1):
            # 只看代码，不看注释：规则针对的是写法，说明里举例不该被判红
            # （写这条检查时就被自己的注释命中过）
            code_text = line.split("#", 1)[0]
            if PREFIX_MARK in code_text and any(
                    mark in code_text for mark in SEP_MARKS):
                messages.append(
                    "自行前缀比较: {}:{} 又拼了一遍路径包含判断，"
                    "应改用 paths.is_within".format(path, number))
                code = AUDIT_PREFIX_DRIFT
    return code


# ------------------------------------------------------------------ 入口

def run(config, targets=None):
    """审计配置与源码，返回 (错误码, 说明行列表)。

    targets 为空时用 default_targets；目录递归取 *.py。各类问题一次全列，错误码
    取第一类命中的，便于调用方按类型分流。
    """
    if not targets:
        targets = default_targets(config)
    files = collect_files(targets)
    parsed = _parse_all(files)
    messages = []
    codes = [
        _check_dead(config, parsed, messages),
        _check_unknown_keys(config, messages),
        _check_bypass(config, parsed, messages),
        _check_std(config, messages),
        _check_raw_io(files, messages),
        _check_prefix_compare(files, messages),
    ]
    for code in codes:
        if code != OK:
            return code, messages
    return OK, messages
