#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置文件的加载与取值校验。"""
import sys
if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")
import os
import sys
import importlib.machinery
import importlib.util
from pathlib import Path

from .errors import (CONFIG_BAD_VALUE, CONFIG_MISSING_ITEM,
                     CONFIG_NOT_FOUND, CONFIG_ROOT_MISSING,
                     CONFIG_UNLOADABLE, CONTRACT_MISMATCH, OK)
from . import paths
from .valgrind import DEFAULT_IGNORE_KINDS
from .version import CONTRACT_VERSION



TOOL_ROOT = Path(__file__).resolve().parents[1]

CONFIG_FILE = TOOL_ROOT / "config" / "config.py"

CONFIG_NAME = "cxx_ci_config"

# 配置位置的查找顺序：显式给的路径 > 环境变量 > 工具自带的 config/config.py。
# 阶段脚本由驱动层拉起，驱动层把 --config 折算成这个环境变量传下去。
CONFIG_ENV = "CXX_CI_CONFIG"

# 本工具实现的跨项目契约版本定义在 lib/version.py。它是工具的属性，不是项目的
# 配置项：项目配置写不写都行，但一旦写了就必须与本工具一致——不一致说明两
# 侧取自不同代，文件与命令行契约未必还对得上（见下面的 check_contract）。
BUILD_TOOLS = ("cmake", "qmake", "qmake6")

DEFAULT_BUILD_DIR = "build"

# 只要求"猜不出来"的两项：项目根与构建工具。其余按构建工具探测或缺省，
# 探测到什么会随配置的提示打出来——移植到新项目时先把这两个填上就能跑
REQUIRED = ("ROOT", "BUILD_TOOL")

# 构建文件按构建工具探测时用的名字
BUILD_FILE_CANDIDATES = {
    "cmake": ("CMakeLists.txt",),
    "qmake": (".pro",),
    "qmake6": (".pro",),
}

# 构建目录的缺省名
DEFAULT_BUILD_DIR = "build"

# 未显式写出的项一律取这里的缺省，配置文件不必逐项列全
# 测试报告适配器与编译数据库 provider 的名字，配置取值必须来自这里
TEST_ADAPTERS = ("auto", "gtest", "regex", "exit-code")

DEFAULTS = {
    # 构建与测试项：没写就按构建工具探测或缺省（见 load_config），因此缺省值
    # 都是"空"而不是某个具体路径
    "BUILD_TOOL_BIN": "",
    "BUILD_FILE": None,
    "BUILD_DIR": None,
    "CXX_COMPILER": "",
    "CXX_STANDARD": "",
    "TEST_BUILD_FILE": None,
    "TEST_BUILD_DIR": None,
    "TEST_COMMAND": None,
    "TEST_WORKDIR": None,
    "MEMCHECK_COMMAND": None,
    "MEMCHECK_SUPPRESSIONS": "",
    "WHITELIST": [],
    "BLACKLIST": [],
    "SCOPE": None,
    "DIAGNOSTIC_LEVEL": "error",
    "DIAGNOSTIC_SOURCES": "project",
    "DIAGNOSTIC_IGNORE": [],
    "DIAGNOSTIC_DEDUP": True,
    "FAIL_OUTPUT": "summary",
    "CI_LOG_DIR": None,
    "TEST_REPORT_ADAPTER": "auto",
    "TEST_FAIL_PATTERN": "",
    "TEST_TOTAL_PATTERN": "",
    "MEMCHECK_SCOPE": "project",
    "MEMCHECK_IGNORE_OBJECTS": [],
    "MEMCHECK_IGNORE_SYMBOLS": [],
    # 缺省值取自 valgrind 模块，避免同一份"哪些类别不计入失败"写两处
    "MEMCHECK_IGNORE_KINDS": list(DEFAULT_IGNORE_KINDS),
    "MEMCHECK_SKIP_TARGETS": [],
    "MEMCHECK_REPEAT": 1,
    "XFAIL_FILE": None,
    "XFAIL_STRICT": False,
    "TEST_ENV": {},
    "MEMCHECK_ENV": None,
    "STEPS_DEFAULT": None,
    # 内存检查路线：valgrind 或 asan（影子构建 + 双通道）
    "MEMCHECK_TOOL": "valgrind",
    "ASAN_BUILD_DIR": None,
    "ASAN_COMMAND": None,
    "ASAN_CXXFLAGS": ["-fsanitize=address", "-fno-omit-frame-pointer"],
    "ASAN_LDFLAGS": ["-fsanitize=address"],
    "ASAN_LEAK_ARGS": [],
    "ASAN_SUPPRESSIONS": "",
    # 内存类指纹：命中即判失败，任何 xfail 登记都不豁免
    "ASAN_ERROR_MARKS": [
        "ERROR: AddressSanitizer", "ERROR: LeakSanitizer",
        "SUMMARY: AddressSanitizer", "SUMMARY: LeakSanitizer",
        "Direct leak", "Indirect leak", "heap-use-after-free",
        "heap-buffer-overflow", "double-free", "SEGV on unknown address",
    ],
    # 源码编码：判定不出编码的文件只读，绝不被静默写成 U+FFFD
    "SOURCE_ENCODING": "utf-8",
    "SOURCE_ENCODING_CANDIDATES": [],
}

MEMCHECK_TOOLS = ("valgrind", "asan")

class Config(object):
    """一次运行用到的项目配置，路径均为绝对路径。

    root 是被 CI 的项目根，通常与 cxx_ci 同级；其余路径都以它为基准。
    """

    def __init__(self, root, build_tool, build_tool_bin, build_file, build_dir,
                 cxx_compiler, cxx_standard,
                 frozen,
                 test_build_file, test_build_dir, test_command, test_workdir,
                 memcheck_command,
                 whitelist, blacklist,
                 memcheck_suppressions, scope,
                 diagnostic_level, diagnostic_sources,
                 diagnostic_ignore, diagnostic_dedup, fail_output, log_dir,
                 test_report_adapter, test_fail_pattern, test_total_pattern,
                 memcheck_scope, memcheck_ignore_objects,
                 memcheck_ignore_kinds, memcheck_skip_targets,
                 memcheck_ignore_symbols, memcheck_repeat,
                 xfail_file, xfail_strict,
                 test_env, memcheck_env, steps_default,
                 memcheck_tool, asan_build_dir, asan_command, asan_cxxflags,
                 asan_ldflags, asan_leak_args, asan_suppressions,
                 asan_error_marks):
        self.root = root
        self.build_tool = build_tool
        self.build_tool_bin = build_tool_bin
        self.build_file = build_file
        self.build_dir = build_dir
        # 禁改区：绝不可写、也不可删。与 standardize 侧的 FROZEN 同名同语义，
        # 是两侧共用的契约项（见 CONTRACT.md），不在这里也不在那边另立一份
        self.frozen = frozen
        self.cxx_compiler = cxx_compiler
        self.cxx_standard = cxx_standard
        self.test_build_file = test_build_file
        self.test_build_dir = test_build_dir
        self.test_command = test_command
        self.test_workdir = test_workdir
        self.memcheck_command = memcheck_command
        self.whitelist = whitelist
        self.blacklist = blacklist
        self.memcheck_suppressions = memcheck_suppressions
        self.scope = scope
        self.diagnostic_level = diagnostic_level
        self.diagnostic_sources = diagnostic_sources
        self.diagnostic_ignore = diagnostic_ignore
        self.diagnostic_dedup = diagnostic_dedup
        self.fail_output = fail_output
        self.log_dir = log_dir
        self.test_report_adapter = test_report_adapter
        self.test_fail_pattern = test_fail_pattern
        self.test_total_pattern = test_total_pattern
        self.memcheck_scope = memcheck_scope
        self.memcheck_ignore_objects = memcheck_ignore_objects
        self.memcheck_ignore_kinds = memcheck_ignore_kinds
        self.memcheck_skip_targets = memcheck_skip_targets
        self.memcheck_ignore_symbols = memcheck_ignore_symbols
        self.memcheck_repeat = memcheck_repeat
        self.xfail_file = xfail_file
        self.xfail_strict = xfail_strict
        self.test_env = test_env
        self.memcheck_env = memcheck_env
        self.steps_default = steps_default
        self.memcheck_tool = memcheck_tool
        self.asan_build_dir = asan_build_dir
        self.asan_command = asan_command
        self.asan_cxxflags = asan_cxxflags
        self.asan_ldflags = asan_ldflags
        self.asan_leak_args = asan_leak_args
        self.asan_suppressions = asan_suppressions
        self.asan_error_marks = asan_error_marks
        # 下面三项在 load_config 里按需补齐（见那里的注释）；直接构造 Config 的
        # 调用方（测试）没有它们也能跑：source 用 getattr 取缺省
        self.source_encoding = DEFAULTS.get("SOURCE_ENCODING", "utf-8")
        self.source_encoding_candidates = []
        self.declared_keys = frozenset()


def _load_module(path):
    """加载配置文件，返回 (模块, 错误码, 错误说明)。

    兼容 Python 3.4 与新版 importlib。执行配置文件可能抛出任意异常，
    这里在边界处捕获并转成错误码。
    """
    try:
        if hasattr(importlib.util, "module_from_spec"):
            spec = importlib.util.spec_from_file_location(CONFIG_NAME, str(path))
            if spec is None or spec.loader is None:
                # 扩展名不是 .py 时 importlib 认不出加载器，显式指定
                spec = importlib.util.spec_from_file_location(
                    CONFIG_NAME, str(path),
                    loader=importlib.machinery.SourceFileLoader(
                        CONFIG_NAME, str(path)))
            module = importlib.util.module_from_spec(spec)
            sys.modules[CONFIG_NAME] = module
            try:
                spec.loader.exec_module(module)
            finally:
                sys.modules.pop(CONFIG_NAME, None)
            return module, OK, ""
        # Python 3.4 没有 module_from_spec
        loader = importlib.machinery.SourceFileLoader(CONFIG_NAME, str(path))
        try:
            return loader.load_module(), OK, ""
        finally:
            sys.modules.pop(CONFIG_NAME, None)
    except Exception as exc:
        return None, CONFIG_UNLOADABLE, "配置文件无法加载: {}: {}".format(path, exc)


class Reader(object):
    """逐项读取配置，记录第一个错误。

    校验不抛异常：失败时记下错误码与说明，由 load_config 统一返回。
    """

    def __init__(self, module):
        self.module = module
        self.code = OK
        self.message = ""
        self.warnings = []

    def warn(self, message):
        """记下一条提示，不阻断加载。"""
        if not hasattr(self, "warnings"):
            self.warnings = []
        self.warnings.append(message)

    def fail(self, code, message):
        """记录第一个错误，后续错误不再覆盖。"""
        if self.code == OK:
            self.code = code
            self.message = message

    def has(self, name):
        """配置里是否显式写了该项。"""
        return hasattr(self.module, name)

    def value(self, name):
        """读取原始取值，未写时取缺省。"""
        if hasattr(self.module, name):
            return getattr(self.module, name)
        return DEFAULTS.get(name)


def _as_path(value):
    """把配置里的取值转成路径；转不了返回 None。

    配置写错（把整数、嵌套列表之类写进路径项）时不能让它一路走到 pathlib 里
    抛 TypeError：那对使用者是一段栈，而不是"哪一项、该写成什么样"。校验收在
    这一处，所有取路径的读取函数都过它。
    """
    if isinstance(value, (str, os.PathLike)):
        return Path(value)
    return None


def _absolute(reader, name):
    """配置里的路径必须是绝对路径，否则会随运行目录漂移。

    返回前归一化一次（realpath）：后续比较一律用已归一化的值，避免同一套判定
    里 abspath 与 realpath 混用——两者在软链布局下会给出相反结论，而每一处
    单独看都"对"。判定本身收口在 paths.is_within（它仍对两边取 realpath，
    以防调用方传进未归一化的值）；入口归一化让 doctor 与报告里显示的是真实
    路径而不是软链路径。
    """
    value = reader.value(name)
    path = _as_path(value)
    if path is None:
        reader.fail(CONFIG_BAD_VALUE,
                    "{} 必须是路径（字符串或 Path），实际是 {}；"
                    '请写成 ROOT / "..."'.format(name, type(value).__name__))
        return ""
    if not path.is_absolute():
        reader.fail(CONFIG_BAD_VALUE,
                    '{} 必须是绝对路径，请写成 ROOT / "{}"'.format(name, value))
        return ""
    return os.path.realpath(str(path))


def _discover_build_file(reader, root, tool):
    """按构建工具在项目根里找构建文件，返回路径或空串。

    找不到就报错并说清该写什么，不做"猜一个"的静默回退：构建文件猜错，后面
    每一步都会以别的面目失败。
    """
    if not root or not os.path.isdir(root):
        return ""
    names = BUILD_FILE_CANDIDATES.get(tool, ())
    found = []
    for name in os.listdir(root):
        for candidate in names:
            if name == candidate or (candidate == ".pro"
                                     and name.endswith(".pro")):
                found.append(os.path.join(root, name))
    if not found:
        reader.fail(CONFIG_MISSING_ITEM,
                    "项目根下找不到 {} 的构建文件（{}），请显式写 BUILD_FILE"
                    .format(tool, "、".join(names)))
        return ""
    found.sort()
    reader.warn("自动探测到构建文件: " + found[0])
    return found[0]


def _build_file_or_discover(reader, name, tool, root, fallback=""):
    """构建文件：写了就校验，没写就按构建工具探测；再没有就用 fallback。"""
    if reader.has(name) and reader.value(name):
        return _build_file(reader, name, tool)
    if fallback:
        return fallback
    return _discover_build_file(reader, root, tool)


def _build_file(reader, name, tool):
    """构建文件必须是绝对路径，且文件名要与构建工具匹配。"""
    path = Path(_absolute(reader, name))
    if tool == "cmake":
        matched, expected = path.name == "CMakeLists.txt", "CMakeLists.txt"
    else:
        matched, expected = path.name.endswith(".pro"), ".pro 文件"
    if not matched:
        reader.fail(CONFIG_BAD_VALUE,
                    "{} 与 BUILD_TOOL={} 不匹配，应为 {}".format(
                        name, tool, expected))
    return str(path)


def _optional_command(reader, name):
    """可选的 argv 列表。

    没写、写 None 或写空列表都表示"按缺省推导"，不算配置错误——这三个写法在
    使用者看来是同一件事。
    """
    if not reader.has(name):
        return None
    value = reader.value(name)
    if value is None or value == "" or value == [] or value == ():
        return None
    return _command(reader, name)


def _command(reader, name):
    """运行指令必须是非空的 argv 列表，不做路径约束。"""
    value = reader.value(name)
    if not isinstance(value, (list, tuple)) or not value:
        reader.fail(CONFIG_BAD_VALUE, "{} 必须是非空列表".format(name))
        return []
    items = []
    for item in value:
        text = str(item)
        if not text:
            reader.fail(CONFIG_BAD_VALUE, "{} 含空项".format(name))
        items.append(text)
    return items


def _encoding_candidates(reader):
    """候选编码名列表；写单个字符串按一项处理，逐项要求是字符串。"""
    value = reader.value("SOURCE_ENCODING_CANDIDATES") or []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        reader.fail(CONFIG_BAD_VALUE,
                    "SOURCE_ENCODING_CANDIDATES 必须是编码名列表，实际是 {}"
                    .format(type(value).__name__))
        return []
    found = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            reader.fail(CONFIG_BAD_VALUE,
                        "SOURCE_ENCODING_CANDIDATES 第 {} 项必须是编码名字符串，"
                        "实际是 {}".format(index + 1, type(item).__name__))
            continue
        found.append(item)
    return found


def _text(reader, name):
    """取值必须是非空字符串。

    不做隐式 str()：把 [1, 2, 3] 转成 "[1, 2, 3]" 会得到一个看着像样的取值，
    而它永远匹配不上任何东西——使用者只会觉得"这功能没反应"。
    """
    value = reader.value(name)
    if value is not None and not isinstance(value, str):
        reader.fail(CONFIG_BAD_VALUE,
                    "{} 必须是字符串，实际是 {}（写成 \"...\"）"
                    .format(name, type(value).__name__))
        return ""
    text = (value or "").strip()
    if not text:
        reader.fail(CONFIG_BAD_VALUE, "{} 不能为空".format(name))
    return text


def _optional_text(reader, name):
    """取值可为空字符串，空表示"不传这一项"。"""
    value = reader.value(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        reader.fail(CONFIG_BAD_VALUE, "{} 必须是字符串".format(name))
        return ""
    return value.strip()


def _mapping(reader, name):
    """读取字符串到字符串的映射，用于环境变量注入。"""
    value = reader.value(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        reader.fail(CONFIG_BAD_VALUE, "{} 必须是字典".format(name))
        return {}
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            reader.fail(CONFIG_BAD_VALUE,
                        "{} 的键与值都必须是字符串".format(name))
            return {}
        result[key] = item
    return result


def _scope(reader):
    """读取 SCOPE，未写时回落到旧的 WHITELIST/BLACKLIST。

    旧名保留可用，只提示一次；两者都没有视为配置错误。
    """
    if reader.has("SCOPE"):
        value = reader.value("SCOPE")
        if not isinstance(value, dict):
            reader.fail(CONFIG_BAD_VALUE, "SCOPE 必须是字典")
            return None
        result = {}
        for key in ("project", "excluded"):
            result[key] = _scope_paths(reader, "SCOPE", value.get(key) or [])
        return result
    if reader.has("WHITELIST") or reader.has("BLACKLIST"):
        reader.warn("WHITELIST/BLACKLIST 已更名为 SCOPE，"
                    "建议写成 SCOPE = {{\"project\": [...], "
                    "\"excluded\": [...]}}")
        return {"project": _path_list(reader, "WHITELIST"),
                "excluded": _path_list(reader, "BLACKLIST")}
    reader.fail(CONFIG_MISSING_ITEM,
                "配置缺少 SCOPE（或旧名 WHITELIST 与 BLACKLIST）")
    return None


def _scope_paths(reader, name, items):
    """SCOPE 里的路径同样要求绝对路径。"""
    result = []
    for index, item in enumerate(items):
        path = Path(str(item))
        if not path.is_absolute():
            reader.fail(CONFIG_BAD_VALUE,
                        "{}.{} 第 {} 项必须是绝对路径".format(
                            name, "project", index + 1))
            continue
        result.append(os.path.realpath(str(path)))
    return result


def _absolute_or_default(reader, name, default):
    """路径：写了就要求绝对路径，没写用缺省值。"""
    if reader.has(name) and reader.value(name):
        return _absolute(reader, name)
    return default


def _optional_absolute(reader, name):
    """可选的绝对路径，未配置时返回空串。"""
    value = reader.value(name)
    if not value:
        return ""
    return _absolute(reader, name)


def _absolute_roots(reader, name):
    """读取一组绝对路径（禁改区这类"目录根"），逐个归一化。"""
    value = reader.value(name) or []
    if not isinstance(value, (list, tuple)):
        reader.fail(CONFIG_BAD_VALUE,
                    "{} 必须是路径列表，例如 [ROOT / \"third_party\"]".format(name))
        return []
    found = []
    for item in value:
        if not str(item):
            continue
        path = Path(str(item))
        if not path.is_absolute():
            # 相对路径会随运行目录漂移，禁改区判定跟着失效——宁可不配
            reader.fail(CONFIG_BAD_VALUE,
                        '{} 里的路径必须是绝对路径，请写成 ROOT / "{}"'
                        .format(name, item))
            continue
        found.append(str(path))
    return found


def _count(reader, name):
    """读取"至少为 1"的整数，用于重复次数这类取值。"""
    value = reader.value(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        reader.fail(CONFIG_BAD_VALUE,
                    "{} 必须是 1 或更大的整数".format(name))
        return 1
    return value


def _flag(reader, name):
    """读取布尔值。"""
    value = reader.value(name)
    if not isinstance(value, bool):
        reader.fail(CONFIG_BAD_VALUE, "{} 必须是 True 或 False".format(name))
        return False
    return value


def _choice(reader, name, allowed):
    """取值必须来自给定集合，脚本要按它分支。"""
    text = _text(reader, name)
    if text and text not in allowed:
        reader.fail(CONFIG_BAD_VALUE,
                    "{} 必须是 {} 之一，实际为 {}".format(
                        name, "、".join(allowed), text))
    return text


def _text_list(reader, name):
    """读取字符串列表，逐项要求是字符串；未配置时为空列表。"""
    value = reader.value(name) or []
    if not isinstance(value, (list, tuple)):
        reader.fail(CONFIG_BAD_VALUE,
                    "{} 必须是字符串列表，例如 [\"*.py\"]，实际是 {}"
                    .format(name, type(value).__name__))
        return []
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            reader.fail(CONFIG_BAD_VALUE,
                        "{} 第 {} 项必须是字符串".format(name, index + 1))
            continue
        result.append(item)
    return result


def _path_item_list(reader, name):
    """路径类列表：Path 与 str 都接受，统一归一化成字符串。

    同为"路径列表"的配置项却要求某一种类型，使用者就得记住哪个是特例，
    零文件接入的体验就破了。在读取器里一次收敛，别处不再逐项特判。

    不用 os.fspath：它是 Python 3.6 起才有的，而本工具要兼容 3.4。
    """
    value = reader.value(name) or []
    # 写单个字符串按一项处理：不然 "abc" 会被逐字符拆成 a、b、c 三项，
    # 配置看着没错、行为完全不是那个意思
    if isinstance(value, (str, Path)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        reader.fail(CONFIG_BAD_VALUE,
                    "{} 必须是路径或字符串列表，例如 [\"tests/x\"]，实际是 {}"
                    .format(name, type(value).__name__))
        return []
    result = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            text = item
        elif isinstance(item, Path):
            text = str(item)
        else:
            reader.fail(CONFIG_BAD_VALUE,
                        "{} 第 {} 项必须是路径或字符串".format(name, index + 1))
            continue
        if text:
            result.append(text)
    return result


def _path_list(reader, name):
    """读取路径列表，逐项要求绝对路径。"""
    value = reader.value(name)
    if not isinstance(value, (list, tuple)):
        reader.fail(CONFIG_BAD_VALUE,
                    "{} 必须是路径列表，例如 [ROOT / \"src\"]，实际是 {}"
                    .format(name, type(value).__name__))
        return []
    result = []
    for index, item in enumerate(value):
        path = _as_path(item)
        if path is None:
            reader.fail(CONFIG_BAD_VALUE,
                        "{} 第 {} 项必须是路径（字符串或 Path），实际是 {}"
                        .format(name, index + 1, type(item).__name__))
            continue
        if not path.is_absolute():
            reader.fail(CONFIG_BAD_VALUE,
                        "{} 第 {} 项必须是绝对路径".format(name, index + 1))
            continue
        result.append(os.path.realpath(str(path)))
    return result


def contract_version(module):
    """本次运行遵循的跨项目契约版本。

    配置里声明了就用它，没写则用本工具实现的版本。声明值是否合法由
    check_contract 判定——它才是加载配置时真正把关的地方，本函数只负责取值。
    """
    value = getattr(module, "CONTRACT_VERSION", CONTRACT_VERSION)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return CONTRACT_VERSION


def check_contract(module):
    """比对配置声明的契约版本与本工具实现，返回 (错误码, 说明)。

    配置没写、或写的不是整数（视为没写）时静默通过，取工具缺省；写了就必须与
    工具一致——两侧取自不同代时，跨项目的文件与命令行契约未必还对得上，与其
    猜怎么兼容，不如当场拒绝并让人对齐到同一份 CONTRACT.md。
    """
    declared = getattr(module, "CONTRACT_VERSION", None)
    if isinstance(declared, bool):
        return CONTRACT_MISMATCH, (
            "CONTRACT_VERSION 写成了布尔值；请写整数（当前契约是 {}）"
            .format(CONTRACT_VERSION))
    if isinstance(declared, float) or (
            isinstance(declared, str) and declared.strip()):
        # 写成 2.0 或 "2" 时被当成"没写"静默放行，而契约是"一旦写了就必须一致"
        return CONTRACT_MISMATCH, (
            "CONTRACT_VERSION 写成了 {}（{!r}）；请写整数，当前契约是 {}"
            .format(type(declared).__name__, declared, CONTRACT_VERSION))
    if not isinstance(declared, int):
        return OK, ""
    if declared == CONTRACT_VERSION:
        return OK, ""
    return CONTRACT_MISMATCH, (
        "配置声明的契约版本为 {}，本工具实现的是 {}：配置/工具取自不同代，"
        "请对齐到同一份 CONTRACT.md".format(declared, CONTRACT_VERSION))


def config_path(path=None):
    """配置的位置：显式路径 > 环境变量 > 工具自带。"""
    if path:
        return Path(path)
    return Path(os.environ.get(CONFIG_ENV) or CONFIG_FILE)


def load_module(path=None):
    """只加载配置文件、不做校验，返回 (模块, 错误码, 说明)。

    给需要读单个声明（如契约版本）的调用方用：它们不想因为配置里别处写错就
    拿不到这个数字。
    """
    return _load_module(config_path(path))


def _unknown_key_names(declared):
    """declared 里本工具不认识的大写名字（多半是错字或归另一侧）。

    只在这里判"认识不认识"：审计要用它决定是否判错字，advise 要用它提醒，
    两处口径必须同一份。
    """
    known = set(DEFAULTS) | set(REQUIRED) | set(NON_CONFIG_KEYS) | set(
        KEYS_OUTSIDE_METADATA)
    return sorted(name for name in declared
                  if name.isupper() and name not in known)


def load_config(path=None):
    """导入配置文件并校验取值，返回 (配置, 错误码, 错误说明)。"""
    target = config_path(path)
    if not target.exists():
        return None, CONFIG_NOT_FOUND, (
            "找不到配置文件: " + str(target)
            + "\n请从 config-example/ci_config.py 复制一份配置后按需修改")
    module, code, message = _load_module(target)
    if code != OK:
        return None, code, message
    missing = [name for name in REQUIRED if not hasattr(module, name)]
    if missing:
        return None, CONFIG_MISSING_ITEM, "配置缺少项: " + "、".join(missing)
    # 契约校验下移到各侧入口：只用 ci.py（老机器日常门禁）时也要能发现配置取自
    # 不同代，而不必依赖编排脚本比对
    code, message = check_contract(module)
    if code != OK:
        return None, code, message

    reader = Reader(module)
    tool = _choice(reader, "BUILD_TOOL", BUILD_TOOLS)
    root = _absolute(reader, "ROOT")
    if root and not os.path.isdir(root):
        reader.fail(CONFIG_ROOT_MISSING, "项目根不存在，请确认配置: " + root)
    # 构建文件与构建目录：没写就按构建工具探测/取缺省名，探测结果会提示一次
    build_file = _build_file_or_discover(reader, "BUILD_FILE", tool, root)
    build_dir = _absolute_or_default(reader, "BUILD_DIR",
                                     os.path.join(root, DEFAULT_BUILD_DIR))
    test_build_file = _build_file_or_discover(reader, "TEST_BUILD_FILE", tool,
                                              root, fallback=build_file)
    test_build_dir = _absolute_or_default(reader, "TEST_BUILD_DIR", build_dir)
    scope = _scope(reader)
    # 日志目录缺省放在构建目录下；clean 会保留它，否则摘要给出的路径会变成死链
    log_dir = _optional_absolute(reader, "CI_LOG_DIR")
    if not log_dir and build_dir:
        log_dir = os.path.join(build_dir, "ci-logs")
    # 登记清单缺省放在项目根。没有独立的"关闭"开关：清单文件不存在就等于
    # 没有清单，门禁按原有语义判定，不必再多记一个配置项。
    xfail_file = _optional_absolute(reader, "XFAIL_FILE") or (
        os.path.join(root, "ci.xfail") if root else "")
    config = Config(
        root=root,
        build_tool=tool,
        build_tool_bin=_optional_text(reader, "BUILD_TOOL_BIN"),
        build_file=build_file,
        build_dir=build_dir,
        frozen=_absolute_roots(reader, "FROZEN"),
        cxx_compiler=_optional_text(reader, "CXX_COMPILER"),
        cxx_standard=_optional_text(reader, "CXX_STANDARD"),
        test_build_file=test_build_file,
        test_build_dir=test_build_dir,
        test_command=_optional_command(reader, "TEST_COMMAND") or [],
        test_workdir=_optional_absolute(reader, "TEST_WORKDIR"),
        memcheck_command=(_optional_command(reader, "MEMCHECK_COMMAND")
                          or _optional_command(reader, "TEST_COMMAND") or []),
        whitelist=_path_list(reader, "WHITELIST"),
        blacklist=_path_list(reader, "BLACKLIST"),
        scope=scope,
        memcheck_suppressions=_optional_absolute(reader, "MEMCHECK_SUPPRESSIONS"),
        diagnostic_level=_choice(reader, "DIAGNOSTIC_LEVEL",
                                 ("off", "note", "warning", "error")),
        diagnostic_sources=_choice(reader, "DIAGNOSTIC_SOURCES",
                                   ("project", "all")),
        diagnostic_ignore=_text_list(reader, "DIAGNOSTIC_IGNORE"),
        diagnostic_dedup=_flag(reader, "DIAGNOSTIC_DEDUP"),
        fail_output=_choice(reader, "FAIL_OUTPUT", ("summary", "full")),
        log_dir=log_dir,
        test_report_adapter=_choice(reader, "TEST_REPORT_ADAPTER", TEST_ADAPTERS),
        test_fail_pattern=_optional_text(reader, "TEST_FAIL_PATTERN"),
        test_total_pattern=_optional_text(reader, "TEST_TOTAL_PATTERN"),
        memcheck_scope=_choice(reader, "MEMCHECK_SCOPE", ("project", "all")),
        memcheck_ignore_objects=_path_item_list(reader, "MEMCHECK_IGNORE_OBJECTS"),
        memcheck_ignore_kinds=_text_list(reader, "MEMCHECK_IGNORE_KINDS"),
        memcheck_skip_targets=_path_item_list(reader, "MEMCHECK_SKIP_TARGETS"),
        memcheck_ignore_symbols=_text_list(reader, "MEMCHECK_IGNORE_SYMBOLS"),
        memcheck_repeat=_count(reader, "MEMCHECK_REPEAT"),
        xfail_file=xfail_file,
        xfail_strict=_flag(reader, "XFAIL_STRICT"),
        test_env=_mapping(reader, "TEST_ENV"),
        memcheck_env=_mapping(reader, "MEMCHECK_ENV") if reader.has("MEMCHECK_ENV") else None,
        steps_default=_text_list(reader, "STEPS_DEFAULT") or None,
        memcheck_tool=_choice(reader, "MEMCHECK_TOOL", MEMCHECK_TOOLS),
        asan_build_dir=_optional_absolute(reader, "ASAN_BUILD_DIR"),
        asan_command=_optional_command(reader, "ASAN_COMMAND"),
        asan_cxxflags=_text_list(reader, "ASAN_CXXFLAGS"),
        asan_ldflags=_text_list(reader, "ASAN_LDFLAGS"),
        asan_leak_args=_text_list(reader, "ASAN_LEAK_ARGS"),
        asan_suppressions=_optional_absolute(reader, "ASAN_SUPPRESSIONS"),
        asan_error_marks=_text_list(reader, "ASAN_ERROR_MARKS"),
    )
    # 源码编码：判定不出编码的文件一律只读（见 lib/source.py）。候选缺省为空——
    # 不猜 GBK；写 SOURCE_ENCODING="auto" 才启用 SOURCE_ENCODING_CANDIDATES
    encoding = reader.value("SOURCE_ENCODING")
    if encoding is not None and not isinstance(encoding, str):
        reader.fail(CONFIG_BAD_VALUE,
                    "SOURCE_ENCODING 必须是编码名字符串，实际是 {}"
                    .format(type(encoding).__name__))
    config.source_encoding = str(encoding or "utf-8")
    config.source_encoding_candidates = _encoding_candidates(reader)
    # 失败检查放在**所有**读取之后：放在中间时，它后面的校验（源码编码这两项就是）
    # 记下的失败没人再看，配置写错却一路跑到缺省值上
    if reader.code != OK:
        return None, reader.code, reader.message
    # 显式写在配置文件里的键名：source 用它区分"用户配了"与"回落到缺省"
    config.unknown_keys = _unknown_key_names(
        frozenset(name for name in vars(module) if not name.startswith("_")))
    config.declared_keys = frozenset(name for name in vars(module)
                                     if not name.startswith("_"))
    # 提示（如旧名 WHITELIST/BLACKLIST）随配置返回，由入口打印一次
    config.warnings = list(reader.warnings)
    return config, OK, ""


# ------------------------------------------------------------- 配置键


def config_keys():
    """可写的配置键集。

    等于"有缺省的项 + 必需项 + FROZEN"：FROZEN 既不在 REQUIRED（缺省空即可）
    也不在 DEFAULTS（两侧共用、语义特殊），但它可以写，因此单列。元数据必须与
    它逐项对齐。
    """
    return set(DEFAULTS) | set(REQUIRED) | set(["FROZEN"])


def example_path():
    """仓库自带的示例配置位置（手写维护）。

    工具既可能直接位于仓库根（部署到项目里的拷贝），也可能位于仓库根的
    cxx_ci/ 子目录（开发仓），因此向上找一层含 config-example/ci_config.py 的目录。
    """
    here = TOOL_ROOT
    for _ in range(4):
        candidate = here / "config-example" / "ci_config.py"
        if candidate.is_file():
            return candidate
        here = here.parent
    return TOOL_ROOT / "config-example" / "ci_config.py"


# 配置对象上不是配置项的公开名（"认不认识"判定要跳过）
NON_CONFIG_KEYS = ("warnings", "unknown_keys", "declared_keys", "notes")

# 由本模块单独处理、不算配置开关的项：契约版本在 check_contract 里读
KEYS_OUTSIDE_METADATA = ("CONTRACT_VERSION",)


# 内存检查指令指向这些驱动时，valgrind 只会检查驱动本身，测不到被测程序
_DRIVER_BASENAMES = ("ctest", "make", "qmake", "ninja")


def memcheck_driver(config):
    """内存检查指令若指向构建/测试驱动，返回那个驱动名，否则返回空串。

    驱动名不能拿去当被测程序：valgrind 只会检查驱动本身，而驱动通常又不在
    构建目录里，于是判成"可执行文件不存在"。判定收在这一处，提醒与阶段行为
    才不会各说各的。
    """
    command = getattr(config, "memcheck_command", None) or []
    if not command:
        return ""
    base = os.path.basename(str(command[0]))
    return base if base in _DRIVER_BASENAMES else ""


def _excluded_roots(config):
    """生效的排除区：SCOPE.excluded，未写 SCOPE 时回落旧名 BLACKLIST。"""
    scope = getattr(config, "scope", None)
    if scope:
        return list(scope.get("excluded") or [])
    return list(getattr(config, "blacklist", None) or [])


def _covered(roots, target):
    """target 是否被任一排除根覆盖。用全仓唯一的包含谓词，不自拼前缀。"""
    return any(paths.is_within(target, item) for item in roots if item)


def advise(config):
    """配置加载后的一次建议性检查：返回要打印的提示行，只提醒、不判红。

    三条都要求"风险确实存在"才出声，避免对合法配置误报：
      ① 构建目录已在磁盘上、却没进排除区——里面的生成代码会被当成项目自己的；
      ② valgrind 全量口径且未配任何忽略集——外部库存量泄漏会一起判红；
      ③ 内存检查指令指向 ctest/make 这类驱动——valgrind 只检查到驱动本身。
    """
    notes = []
    excluded = _excluded_roots(config)
    for label, path in (("构建目录", getattr(config, "build_dir", "")),
                        ("测试构建目录", getattr(config, "test_build_dir", ""))):
        if path and os.path.isdir(path) and not _covered(excluded, path):
            notes.append(
                "提示: {} {} 存在但未列入 SCOPE.excluded，构建产物会被当成项目"
                "自身的代码参与诊断归属与内存归因；建议把它写进 excluded"
                .format(label, path))
    declared = getattr(config, "declared_keys", frozenset())
    ignore_keys = ("MEMCHECK_IGNORE_OBJECTS", "MEMCHECK_IGNORE_SYMBOLS",
                   "MEMCHECK_IGNORE_KINDS")
    if (getattr(config, "memcheck_tool", "") == "valgrind"
            and getattr(config, "memcheck_scope", "") == "all"
            and not any(key in declared for key in ignore_keys)):
        notes.append(
            "提示: MEMCHECK_SCOPE=all 且未配置任何忽略集，外部库的存量泄漏会"
            "一起判红；改回 project，或配 MEMCHECK_IGNORE_SYMBOLS / "
            "MEMCHECK_IGNORE_OBJECTS 把不修的摘出去")
    unknown = getattr(config, "unknown_keys", None) or []
    if unknown:
        notes.append(
            "提示: 配置里有本工具不认识的项（{}）；它们会被忽略。若归另一侧或"
            "是你自己定义的常量，忽略这条即可".format("、".join(unknown)))
    driver = memcheck_driver(config)
    if driver:
        notes.append(
            "提示: MEMCHECK_COMMAND 指向 {} 这类驱动，valgrind 只会检查驱动本身；"
            "建议改指具体的测试程序".format(driver))
    return notes
