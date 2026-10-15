#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""接入探测：为新工程探测构建、测试与范围，生成带依据注释的配置。

`ci.py init` 用它把"每个取值是从哪儿读出来的"写进生成的配置：探测到的项后面
跟 `# 依据: ...`，探不到的写 `# TODO: 需要人工填写（缺 …）`。探测只列候选、
不猜——构建文件猜错，后面每一步都会以别的面目失败（与 config.py 同一条纪律）。

本模块只做探测与渲染，不写文件；落盘与提示由 ci.py 的 init 子命令负责，渲染
结果因此可以脱离文件系统单独断言。
"""

import os
import re
import sys

if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")

from . import paths, tools
from .errors import OK
from .output import decode
from .paths import CXX_SUFFIXES
from .version import CONTRACT_VERSION, __version__

# 探测时不再下探的目录：构建产物、依赖、版本控制
SKIP_DIRS = ("build", "third_party", "vendor", "external", "3rdparty",
             "extern", "node_modules", ".git", ".svn", ".hg")

# 视为"外部/依赖代码"的目录名：进 SCOPE.excluded
EXTERNAL_DIRS = ("third_party", "vendor", "external", "3rdparty", "extern")

def own_vendored_dir(root):
    """本工具自己被拷进工程时所占的那个顶层目录名；不在工程内则返回空串。

    标准接入流程就是把 cxx_ci/ 整个拷进工程。它被版本控制跟着、里面又有源码，
    于是会被探成"项目自己的代码"——工具于是去分析自己那份 vendored googletest，
    报出一堆与用户无关的诊断。这里按本文件的位置认出自己，交给调用方排除。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(str(root))
    if not paths.is_within(here, root):
        return ""
    relative = os.path.relpath(here, root)
    return relative.split(os.sep)[0] if relative != "." else ""


# 构建目录的常见名字，用于识别已有构建树
BUILD_DIR_NAMES = ("build", "out")

# 构建文件下探深度（相对项目根）。真实工程的构建文件不会更深，再深只会把
# cmake 模块目录、示例目录里的同名文件扫成假候选
MAX_DEPTH = 2

CMAKE_BUILD_FILE = "CMakeLists.txt"

# 没有 git 时的源码目录命名惯例
FALLBACK_SOURCE_DIRS = ("src", "include", "tests", "test", "lib")

_TODO_PREFIX = "TODO: 需要人工填写"


def _relative(root, path):
    """相对 root 的路径，统一成 `/` 分隔，供写进配置。"""
    return os.path.relpath(str(path), str(root)).replace(os.sep, "/")


def _absolute(root, rel):
    return os.path.join(str(root), rel.replace("/", os.sep))


def read_text(path):
    """读一份探测用的文本（构建文件、清单之类）。

    只读，因此按 UTF-8 宽松解：解不出的字节替换掉也不影响关键字匹配，
    与 source.py 的"写文件必须先判定编码"是两件事。
    """
    try:
        handle = open(str(path), "r", encoding="utf-8", errors="replace")
    except (IOError, OSError):
        return None
    try:
        return handle.read()
    finally:
        handle.close()


def walk_files(root):
    """遍历项目根下的文件，返回 [(相对路径, 绝对路径)]。

    只下探 MAX_DEPTH 层，跳过 SKIP_DIRS 与隐藏目录——构建树与依赖目录里
    的文件不是候选，扫它们只会引入噪声。
    """
    root = os.path.abspath(str(root))
    base = root.rstrip(os.sep).count(os.sep)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base
        dirnames[:] = sorted(name for name in dirnames
                             if name not in SKIP_DIRS
                             and not name.startswith("."))
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        for name in sorted(filenames):
            absolute = os.path.join(dirpath, name)
            found.append((_relative(root, absolute), absolute))
    return found


def build_candidates(files):
    """构建文件候选，按类别分组：cmake / pro / pri。"""
    result = {"cmake": [], "pro": [], "pri": []}
    for rel, _absolute_path in files:
        name = os.path.basename(rel)
        if name == CMAKE_BUILD_FILE:
            result["cmake"].append(rel)
        elif name.endswith(".pro"):
            result["pro"].append(rel)
        elif name.endswith(".pri"):
            result["pri"].append(rel)
    return result


def _shallowest(items):
    """取层级最浅、再按字典序最小的一项：优先项目根下的那一份。"""
    return min(items, key=lambda rel: (rel.count("/"), rel))


def _listing(items, chosen=None):
    """把候选列成一句话，标出选中的那一项。"""
    parts = []
    for rel in sorted(items, key=lambda item: (item.count("/"), item)):
        parts.append(rel + ("（选用）" if rel == chosen else ""))
    return "、".join(parts)


def _ambiguous(items):
    """同层有多份 → 真的分不出来，不该猜。

    只有一份在**最浅层**时才算选得出来：多份并列同层，挑哪个都会挑错，
    而挑错构建文件后面每一段都跟着错。
    """
    if len(items) < 2:
        return False
    shallowest = min(rel.count("/") for rel in items)
    return len([rel for rel in items if rel.count("/") == shallowest]) > 1


def detect_build(files):
    """判定构建工具与构建文件。

    返回 (工具, 构建文件相对路径, 依据说明)。规则是**不猜**：cmake 与 qmake
    候选并存、或同一类在同一层有多份时，工具与文件都返回 None，说明里列出
    候选让人二选一。
    """
    candidates = build_candidates(files)
    cmake, pro = candidates["cmake"], candidates["pro"]
    if cmake and pro:
        return None, None, (
            "同时找到 cmake 与 qmake 的构建文件，需人工二选一："
            "cmake 候选 {}；qmake 候选 {}".format(
                _listing(cmake), _listing(pro)))
    if cmake:
        if _ambiguous(cmake):
            return None, None, (
                "同层有多份 CMakeLists.txt，没法替你挑，请显式写 BUILD_FILE："
                + _listing(cmake))
        chosen = _shallowest(cmake)
        return "cmake", chosen, "项目里找到 " + _listing(cmake, chosen)
    if pro:
        if _ambiguous(pro):
            return None, None, (
                "同层有多份 .pro，没法替你挑，请显式写 BUILD_FILE："
                + _listing(pro))
        chosen = _shallowest(pro)
        return "qmake", chosen, (
            "项目里找到 {}；若是 Qt6 工程请把 BUILD_TOOL 改成 \"qmake6\""
            .format(_listing(pro, chosen)))
    # 只有 .pri 的工程（把工程主体放在 .pri 里的 qmake 布局）：以前这里收集了
    # 候选却从不使用，于是 .pri-only 工程只得到一句"没找到"，候选也从不列出
    pri = candidates["pri"]
    if pri:
        return "qmake", None, (
            "只找到 .pri（没有 .pro / CMakeLists.txt），请显式写 BUILD_FILE。"
            "候选：" + _listing(pri))
    return None, None, "项目里没找到 CMakeLists.txt 或 *.pro"


_CMKE_PROJECT = re.compile(r"^\s*project\s*\(", re.MULTILINE)


def detect_test_build_file(root, files, build_tool, build_file):
    """独立测试工程：自带 project() 的那一份；没有就返回 None。

    cmake 的测试入口通常写在子目录（tests/CMakeLists.txt），但那份**不是独立
    工程**——它 add_executable 引用的是主工程里的 target，单独 configure 会失败
    （缺 project()、target 也不存在）。把它当测试构建文件，等于让 build_test
    第一次跑就红。只有自带 project() 的候选才算独立的测试工程；否则测试与主构建
    共用同一份顶层文件（一次 configure 同时管构建与测试）。
    """
    candidates = build_candidates(files)
    if build_tool == "cmake":
        rest = [rel for rel in candidates["cmake"] if rel != build_file]
        standalone = [rel for rel in rest
                      if _CMKE_PROJECT.search(read_text(_absolute(root, rel))
                                              or "")]
        return _shallowest(standalone) if standalone else None
    if build_tool in ("qmake", "qmake6"):
        rest = [rel for rel in candidates["pro"] if rel != build_file]
        return _shallowest(rest) if rest else None
    return None


# 测试/构建驱动：当成被测程序去检查没有意义，valgrind 只会检查驱动本身
_COMMAND_DRIVERS = ("ctest", "make", "qmake", "ninja")


def _command_driver(command):
    """指令文本（形如 '["ctest", ...]'）若指向驱动，返回驱动名。"""
    for name in _COMMAND_DRIVERS:
        if '"' + name + '"' in command:
            return name
    return ""


def _command_is_driver(command):
    return bool(_command_driver(command))


_TEST_MACROS = (re.compile(r"\benable_testing\s*\("),
                re.compile(r"\badd_test\s*\("))


def find_cmake_test_file(root, rels):
    """哪一份构建文件里写了 enable_testing()/add_test；没有返回空串。

    返回文件名而不只是布尔值：测试构建文件就该指向**写了测试入口**的那一份。
    随便挑一份同层文件（例如按字典序取 src/）会让 cmake 直接拒绝——源目录与
    顶层不一致，build_test 第一天就红。
    """
    for rel in rels:
        if not rel:
            continue
        text = read_text(_absolute(root, rel))
        if text and any(pattern.search(text) for pattern in _TEST_MACROS):
            return rel
    return ""


def detect_test_command(root, build_tool, files, build_file, test_build_file):
    """测试指令，返回 (代码文本或 None, 依据说明)。

    cmake 工程按 ctest 惯例；qmake 工程若存在测试 .pro 则用 make check；
    其余留空——留空时 run_test 与 memcheck 会标 [SKIPPED] 并说明缺什么。
    """
    # 扫**全部** cmake 构建文件，而不是主文件加一份：enable_testing()/add_test
    # 通常不在顶层（顶层只写 add_subdirectory），而在 tests/ 这类子目录里。
    # 只看两份时，子目录多于一个就会挑错哪份，测试入口静默变缺
    rels = [rel for rel in build_candidates(files)["cmake"] if rel]
    if build_tool == "cmake" and find_cmake_test_file(root, rels):
        return ('["ctest", "--output-on-failure"]',
                "构建文件里有 enable_testing()/add_test，按 ctest 惯例")
    if build_tool in ("qmake", "qmake6") and test_build_file:
        return ('["make", "-C", TEST_BUILD_DIR, "check"]',
                "找到测试工程 {}，按 qmake 的 check 目标".format(
                    test_build_file))
    return None, ("构建文件里没读出测试入口；留空则 run_test 与 memcheck 会标 "
                  "[SKIPPED]（跳过不等于通过）")


_STANDARD_CMAKE = re.compile(r"CMAKE_CXX_STANDARD\s+(\d+)")
_STANDARD_FLAG = re.compile(r"-std=([A-Za-z0-9+]+)")
_STANDARD_TOKEN = re.compile(r"\b(?:c|gnu)\+\+(?:\d\d|0x|1z|2a|2b)\b")


def detect_cxx_standard(root, build_files):
    """从构建文件里读目标语言标准，返回 (标准或 None, 依据说明)。"""
    for rel in build_files:
        if not rel:
            continue
        text = read_text(_absolute(root, rel))
        if not text:
            continue
        match = _STANDARD_CMAKE.search(text)
        if match:
            return "c++" + match.group(1), (
                "{} 里的 CMAKE_CXX_STANDARD".format(rel))
        match = _STANDARD_FLAG.search(text)
        if match:
            return match.group(1), "{} 里的 -std=".format(rel)
        match = _STANDARD_TOKEN.search(text)
        if match:
            return match.group(0), "{} 里出现的标准标记".format(rel)
    return None, ("构建文件里没读到目标语言标准；留空表示不传 -std=，"
                  "由构建工具自己的缺省决定")


def existing_build_dirs(root):
    """项目根下已存在的构建目录名（build / out 之类）。"""
    try:
        names = sorted(os.listdir(str(root)))
    except (IOError, OSError):
        return []
    found = []
    for name in names:
        if name.startswith("."):
            continue
        if os.path.isdir(os.path.join(str(root), name)):
            found.append(name)
    return found


def _git_tracked_dirs(root):
    """git 跟踪的顶层目录名；不在 git 仓库里返回 None。"""
    git, code, _message = tools.find_tool(["git"])
    if code != OK or not git:
        return None
    code, captured, _stderr = tools.capture(
        [git, "ls-files"], cwd=str(root))
    if code != 0:
        return None
    dirs = set()
    for line in decode(captured).splitlines():
        line = line.strip()
        if not line or "/" not in line:
            continue
        dirs.add(line.split("/", 1)[0])
    return sorted(dirs)


def _has_sources(files, directory):
    """目录下是否有源码文件（按扩展名判定）。"""
    # 相对路径由 os.walk / os.path.relpath 产出，分隔符是 os.sep：
    # 写死 "/" 在非 "/" 分隔符的系统上会静默匹配不上
    prefix = directory + os.sep
    for rel, _absolute_path in files:
        if rel.startswith(prefix) and rel.endswith(CXX_SUFFIXES):
            return True
    return False


def detect_scope(root, files):
    """项目范围与排除范围。

    有 git 就从版本控制跟踪的目录取——构建产物不在其中；没有 git 就按
    src / include / tests 命名惯例退回。excluded 始终把构建目录与外部
    依赖目录排除在外，否则构建产物里的代码会被当成项目自己的。
    """
    tracked = _git_tracked_dirs(root)
    project, excluded = [], []
    if tracked is not None:
        for name in tracked:
            if name in EXTERNAL_DIRS or name in BUILD_DIR_NAMES:
                excluded.append(name)
            elif _has_sources(files, name):
                project.append(name)
        source = "git 跟踪的顶层目录"
    else:
        for name in FALLBACK_SOURCE_DIRS:
            if os.path.isdir(_absolute(root, name)) and name not in BUILD_DIR_NAMES:
                project.append(name)
        source = "src / include / tests 命名惯例（未发现 git）"

    # 磁盘上存在但没进 project 的构建与依赖目录，一律排除
    for name in existing_build_dirs(root):
        if name in EXTERNAL_DIRS or name in BUILD_DIR_NAMES or _is_build_like(name):
            if name not in excluded:
                excluded.append(name)
    for name in EXTERNAL_DIRS:
        if os.path.isdir(_absolute(root, name)) and name not in excluded:
            excluded.append(name)
    # 工具自己那份拷贝永远不是被测代码
    own = own_vendored_dir(root)
    if own and own not in excluded:
        excluded.append(own)

    project = [name for name in project if name != own]
    project = sorted(set(project))
    excluded = sorted(set(excluded))
    if not project:
        # 探不到源码目录时用整个项目根：SCOPE.project 只回答"哪些算项目自己的"
        project = ["."]
        note = source + "，未探到源码目录，暂用整个项目根（请复核）"
    else:
        note = source
    if excluded:
        note += "；排除 " + "、".join(excluded)
    return project, excluded, note


def _is_build_like(name):
    """像构建目录的名字：build、build-debug、out 之类。"""
    lowered = name.lower()
    return (lowered in BUILD_DIR_NAMES
            or lowered.startswith("build")
            or lowered.startswith("out-"))


class Item(object):
    """配置里的一项：可直接写出的取值代码 + 依据/待填说明。"""

    def __init__(self, code, comment="", todo=False):
        self.code = code
        # 注释一律带前缀：读者要能一眼分清"这条有依据"与"这条要人填"，
        # 不带前缀的说明混在中间，等于两者都看不出来
        if comment and not comment.startswith(("依据:", _TODO_PREFIX)):
            comment = "依据: " + comment
        self.comment = comment
        self.todo = todo


class Plan(object):
    """一次探测的结论，按生成配置的分节组织。

    渲染与 todo 清点共用同一份结构：加一项只会漏在测试里，不会漏在渲染里。
    """

    def __init__(self, root):
        self.root = str(root)
        self.sections = []

    def section(self, title, items):
        self.sections.append((title, list(items)))
        return self

    def all_items(self):
        for _title, items in self.sections:
            for name, item in items:
                yield name, item

    def todos(self):
        """需要人工填写的项名。"""
        return [name for name, item in self.all_items() if item.todo]


def probe(root):
    """探测一个工程，返回可直接渲染的 Plan。"""
    files = walk_files(root)
    plan = Plan(root)

    build_tool, build_file, build_note = detect_build(files)
    if build_tool is None:
        plan.section("构建", [
            ("BUILD_TOOL", Item('""', _TODO_PREFIX + "（" + build_note + "）",
                                todo=True)),
            ("BUILD_FILE", Item('""',
                                _TODO_PREFIX + "（构建工具定不下来，构建文件"
                                "也定不下来）", todo=True)),
        ])
        test_build_file = None
    else:
        plan.section("构建", [
            ("BUILD_TOOL", Item('"{}"'.format(build_tool),
                                "依据: " + build_note)),
            ("BUILD_FILE", Item(_path_expr(build_file),
                                "依据: " + build_note)),
        ])
        test_build_file = detect_test_build_file(
            root, files, build_tool, build_file) or build_file

    build_dirs = [name for name in existing_build_dirs(root)
                  if name in BUILD_DIR_NAMES or _is_build_like(name)]
    if build_dirs:
        chosen_dir = _shallowest(build_dirs)
        dir_item = Item(_path_expr(chosen_dir),
                        "依据: 项目根下已存在目录 " + _listing(build_dirs,
                                                              chosen_dir))
    else:
        dir_item = Item('ROOT / "build"', "缺省构建目录名（未发现已有构建目录）")
    plan.sections[-1][1].append(("BUILD_DIR", dir_item))

    if build_tool is None:
        plan.sections[-1][1].append(
            ("CXX_STANDARD", Item('""', _TODO_PREFIX + "（构建工具定不下来）",
                                  todo=True)))
    else:
        standard, standard_note = detect_cxx_standard(
            root, [build_file, test_build_file])
        if standard is None:
            plan.sections[-1][1].append(
                ("CXX_STANDARD", Item('""', _TODO_PREFIX + "（" + standard_note
                                      + "）", todo=True)))
        else:
            plan.sections[-1][1].append(
                ("CXX_STANDARD", Item('"{}"'.format(standard),
                                      "依据: " + standard_note)))

    if build_tool is None:
        test_items = [
            ("TEST_BUILD_FILE", Item('""', _TODO_PREFIX + "（构建工具定不下来）",
                                     todo=True)),
            ("TEST_BUILD_DIR", Item('""', _TODO_PREFIX + "（构建工具定不下来）",
                                    todo=True)),
            ("TEST_COMMAND", Item("[]", _TODO_PREFIX + "（构建工具定不下来）",
                                  todo=True)),
            ("TEST_WORKDIR", Item('""', _TODO_PREFIX + "（构建工具定不下来）",
                                  todo=True)),
        ]
    else:
        if test_build_file:
            test_file_item = Item(_path_expr(test_build_file),
                                  "依据: 找到测试工程 " + test_build_file)
        else:
            test_file_item = Item('""', "未找到独立测试构建文件，与主构建文件共用")
        # 测试构建目录与主构建目录能不能共用，取决于源目录是否同一份：
        # 测试构建文件就是顶层那份时，一次 configure 同时管构建与测试，共用一个
        # 目录是对的；指向子目录（tests/CMakeLists.txt）时，同一棵树会被两个不同
        # 源目录配置，cmake 直接报 "does not match the source ... used to
        # generate cache"——生成的配置第一次跑就红。那种情况必须分一个目录出来
        shared = (not test_build_file) or (test_build_file == build_file)
        if shared:
            if build_dirs:
                test_dir_item = Item(_path_expr(chosen_dir), "与 BUILD_DIR 共用")
                workdir = chosen_dir
            else:
                test_dir_item = Item('ROOT / "build"', "与 BUILD_DIR 共用（缺省）")
                workdir = "build"
        else:
            separate = (chosen_dir + "/test") if build_dirs else "build/test"
            test_dir_item = Item(
                _path_expr(separate),
                "依据: 测试构建文件是 {}，与主构建不同源目录，必须单独一棵树"
                .format(test_build_file))
            workdir = separate
        command, command_note = detect_test_command(
            root, build_tool, files, build_file, test_build_file)
        if command is None:
            command_item = Item("[]", _TODO_PREFIX + "（" + command_note + "）",
                                todo=True)
            workdir_item = Item('""', "未配测试指令，工作目录也不必填")
        else:
            command_item = Item(command, "依据: " + command_note)
            workdir_item = Item(_path_expr(workdir),
                                "依据: 测试在构建目录内运行")
        # MEMCHECK_COMMAND 缺省取 TEST_COMMAND。测试指令是 ctest/make 这类驱动
        # 时，这个缺省值不是被测程序——valgrind 只会检查驱动本身。与其让配置里
        # 根本没有这一项、由缺省悄悄变成驱动，不如把结果写出来：是程序就写它，
        # 是驱动就标待填并说明原因
        if command is None:
            memcheck_item = Item("[]", "未配测试指令，内存检查也无从取值")
        elif _command_is_driver(command):
            memcheck_item = Item(
                "[]", _TODO_PREFIX
                + "（测试指令指向 {} 这类驱动，valgrind 只会检查驱动本身；"
                  "填具体的测试程序）".format(_command_driver(command)),
                todo=True)
        else:
            memcheck_item = Item(command, "依据: 与 TEST_COMMAND 相同（不在构建目录内运行时按 BUILD_DIR 找）")
        test_items = [
            ("TEST_BUILD_FILE", test_file_item),
            ("TEST_BUILD_DIR", test_dir_item),
            ("TEST_COMMAND", command_item),
            ("TEST_WORKDIR", workdir_item),
            ("MEMCHECK_COMMAND", memcheck_item),
        ]
    plan.section("测试", test_items)

    project, excluded, scope_note = detect_scope(root, files)
    plan.section("范围", [
        ("SCOPE", Item(_scope_expr(project, excluded), "依据: " + scope_note)),
    ])
    return plan


def _path_expr(rel):
    """把相对路径写成 `ROOT / "a" / "b"` 形状。"""
    if rel in ("", "."):
        return "ROOT"
    expr = "ROOT"
    for part in rel.split("/"):
        if part and part != ".":
            expr += ' / "{}"'.format(part)
    return expr


def _path_list_expr(rels):
    if not rels:
        return "[]"
    return "[{}]".format(", ".join(_path_expr(rel) for rel in rels))


def _scope_expr(project, excluded):
    """SCOPE 是多行字典，渲染成可读的几行。"""
    return "\n".join([
        "{",
        '    "project": {},'.format(_path_list_expr(project)),
        '    "excluded": {},'.format(_path_list_expr(excluded)),
        "}",
    ])


_HEADER = '''"""项目配置（由 ci.py init 探测生成）。

生成者: ci {version}（跨项目契约 contract {contract}）。
每条取值后的 `# 依据:` 说明它从哪读出来；标 `# TODO:` 的项需人工填写。
探测只列候选、不猜：构建工具与构建文件多份并存时会留成 TODO 由人定。
"""'''


def render(plan):
    """把 Plan 渲染成配置文本。"""
    lines = [_HEADER.format(version=__version__, contract=CONTRACT_VERSION), ""]
    add = lines.append
    add("from pathlib import Path")
    add("")
    add("# 与另一个项目（standardize）之间的契约版本：两侧一致才执行，不一致拒绝")
    add("CONTRACT_VERSION = {}".format(CONTRACT_VERSION))
    add("")
    add("# 本文件位于 <工程>/config/ci.py，父目录即项目根")
    add("ROOT = Path(__file__).resolve().parents[1]")
    for title, items in plan.sections:
        add("")
        add("# " + "-" * 20 + " " + title)
        for name, item in items:
            if item.comment:
                for line in item.comment.splitlines():
                    add("# " + line)
            add("{} = {}".format(name, item.code))
    add("")
    return "\n".join(lines)


def summary(plan, target):
    """生成完成后给使用者的一段提示。"""
    lines = ["已生成: " + str(target)]
    todos = plan.todos()
    if todos:
        lines.append("有 {} 项需要人工填写: {}".format(
            len(todos), "、".join(todos)))
    lines.append("下一步: 复核生成的配置，然后用 `--config {}` 跑 ci.py".format(
        target))
    return lines
