"""接入探测：为新工程探测 clang 工具、禁改区与范围，生成带依据注释的配置。

`cxx-standardize init` 用它把"每个取值是从哪儿读出来的"写进生成的配置：探测到的
项后面跟 `# 依据: ...`，探不到的写 `# TODO: 需要人工填写（缺 …）`。探测只列候选、
不猜——与 config.py 同一条纪律：配置写错必须当场说清，而不是跑到一半才炸。

本模块只做探测与渲染两件事；落盘与提示由 cli.py 的 init 负责，渲染结果因此可以
脱离文件系统单独断言。缺的 .clang-format / .clang-tidy 由 ensure_config_files 落最小
模板：配置里点了名却找不到时会**直接失败**，不会退回逐级向上查找，因此缺了必须
当场补上——补的是**工程**里的文件，不是工具目录。
"""

import os
import subprocess
from pathlib import Path

from . import fmt, paths, source
from .errors import Code
from .version import CONTRACT_VERSION, __version__

# 探测时不再下探的目录：构建产物、依赖、版本控制
SKIP_DIRS = {"build", "out", "third_party", "vendor", "external", "3rdparty",
             "extern", "node_modules", ".git", ".svn", ".hg"}

# 视为"vendored 冻结库"的目录名
VENDORED_NAMES = {"third_party", "vendor", "external", "3rdparty", "extern"}

# 构建目录的常见名字
BUILD_DIR_NAMES = ("build", "out")

# 下探深度（相对项目根）
MAX_DEPTH = 2

# 没有 git 时的源码目录命名惯例
FALLBACK_SOURCE_DIRS = ("src", "include", "tests", "test", "lib")

# 头文件后缀：判断某目录是否"直接持头文件"用
HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx", ".inl", ".h++")

# 版本控制目录：任何遍历都跳过
VCS_DIRS = {".git", ".svn", ".hg"}

# 源码后缀：判断"这个目录算不算项目自己的代码"用
SOURCE_SUFFIXES = HEADER_SUFFIXES + (".c", ".cc", ".cpp", ".cxx", ".c++")

# vendored 判据之一：目录里有许可证文件
LICENSE_STEMS = ("license", "licence", "copying")

_TODO = "TODO: 需要人工填写"

# 缺 .clang-format 时落的最小模板。只钉基样式，其余按团队规范细调。
TEMPLATE_CLANG_FORMAT = """\
# 由 cxx-standardize init 生成的最小模板：只钉住基样式，其余按团队规范细调。
BasedOnStyle: LLVM
"""

# 缺 .clang-tidy 时落的最小模板。先开一组常用检查，再按团队规范收窄。
TEMPLATE_CLANG_TIDY = """\
# 由 cxx-standardize init 生成的最小模板：先开一组常用检查，再按团队规范收窄。
Checks: >-
  bugprone-*,
  performance-*,
  readability-*
WarningsAsErrors: ''
"""


def _rel(root, path):
    return os.path.relpath(str(path), str(root)).replace(os.sep, "/")


def read_text(path):
    """读一份探测用的文本，读不出返回 None。

    只读，因此按 UTF-8 宽松解：解不出的字节替换掉也不影响关键字匹配，与
    source.py 的"写文件必须先判定编码"是两件事。
    """
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def scan(root):
    """遍历项目根，返回 (文件表, 目录表)，均为相对 root 的 posix 路径。

    只下探 MAX_DEPTH 层，跳过 SKIP_DIRS 与隐藏目录——构建树与依赖目录里的
    文件不是候选，扫它们只会引入噪声。
    """
    base = Path(root).resolve()
    depth_base = len(base.parts)
    files, dirs = [], []
    for dirpath, dirnames, filenames in os.walk(str(base)):
        here = Path(dirpath)
        depth = len(here.parts) - depth_base
        dirnames[:] = sorted(
            name for name in dirnames
            if name not in SKIP_DIRS and not name.startswith("."))
        for name in dirnames:
            dirs.append((here / name).relative_to(base).as_posix())
        for name in filenames:
            files.append((here / name).relative_to(base).as_posix())
        if depth >= MAX_DEPTH:
            dirnames[:] = []
    return sorted(files), sorted(dirs)


def all_dirs(root):
    """枚举项目根下的目录，返回相对 root 的 posix 路径。

    与 scan() 的区别：**不跳过** third_party / vendor 这类 vendored 目录——识别
    禁改区正需要看见它们；只跳过隐藏目录与版本控制目录。深度同样到 MAX_DEPTH。
    """
    base = Path(root).resolve()
    depth_base = len(base.parts)
    found = []
    for dirpath, dirnames, _filenames in os.walk(str(base)):
        here = Path(dirpath)
        depth = len(here.parts) - depth_base
        dirnames[:] = sorted(name for name in dirnames
                             if name not in VCS_DIRS and not name.startswith("."))
        for name in dirnames:
            found.append((here / name).relative_to(base).as_posix())
        if depth >= MAX_DEPTH:
            dirnames[:] = []
    return sorted(found)


def _shallowest(items):
    """取层级最浅、再按字典序最小的一项。"""
    return min(items, key=lambda rel: (rel.count("/"), rel))


def _listing(items, chosen=None):
    """把候选列成一句话，标出选中的那一项。"""
    parts = []
    for rel in sorted(items, key=lambda item: (item.count("/"), item)):
        parts.append(rel + ("（选用）" if rel == chosen else ""))
    return "、".join(parts)


def detect_build(files):
    """判定构建工具与构建文件，返回 (工具, 构建文件, 依据说明)。

    cmake 与 qmake 候选并存时不猜，工具与文件都返回 None。
    """
    cmake = sorted(rel for rel in files
                   if rel.rsplit("/", 1)[-1] == "CMakeLists.txt")
    pro = sorted(rel for rel in files if rel.endswith(".pro"))
    if cmake and pro:
        return None, None, (
            "同时找到 cmake 与 qmake 的构建文件，需人工二选一："
            "cmake 候选 {}；qmake 候选 {}".format(_listing(cmake), _listing(pro)))
    if cmake:
        chosen = _shallowest(cmake)
        return "cmake", chosen, "项目里找到 " + _listing(cmake, chosen)
    if pro:
        chosen = _shallowest(pro)
        return "qmake", chosen, (
            "项目里找到 {}；若是 Qt6 工程请把 BUILD_TOOL 改成 \"qmake6\""
            .format(_listing(pro, chosen)))
    return None, None, "项目里没找到 CMakeLists.txt 或 *.pro"


def detect_build_dir(root):
    """已存在的构建目录名，没有返回 None。"""
    try:
        names = sorted(os.listdir(str(root)))
    except OSError:
        return None
    found = [name for name in names
             if name in BUILD_DIR_NAMES and (Path(root) / name).is_dir()]
    return _shallowest(found) if found else None


def detect_clang_major():
    """探测 clang-format 的主版本，返回 (版本或 None, 依据说明)。

    只用 PATH（含 pip wheel 自带）上找到的那一个：pin 主版本是让排版结果可复现的
    前提，探到哪个就钉哪个，探不到留 TODO 由人定。
    """
    binary, code, message = fmt.resolve("clang-format")
    if code != Code.OK or not binary:
        return None, "PATH 上没有可用的 clang-format（{}）".format(message)
    found, vcode, detail = fmt.version(binary)
    if vcode != Code.OK or found is None:
        # 原样带上 version() 的说法：跑不起来（缺动态库）与跑得起来但认不出
        # 是两回事，前者要提示 LD_LIBRARY_PATH，笼统说成"认不出主版本"会把
        # 最有用的那句丢掉
        return None, detail or "找到了 {} 但认不出主版本".format(binary)
    return found, "从 {} 读出的主版本".format(binary)


def _header_dirs(files):
    """直接持有头文件的目录 → 头文件数。"""
    counts = {}
    for rel in files:
        if rel.lower().endswith(HEADER_SUFFIXES):
            directory = rel.rsplit("/", 1)[0] if "/" in rel else "."
            counts[directory] = counts.get(directory, 0) + 1
    return counts


def _include_roots(holders):
    """头文件目录里出现的、以 `include` 结尾的目录前缀，取最浅的那层。

    实际持头文件的往往是 `include/<工程名>`，include 根是它的前缀 `include`，
    因此要往上找出名为 include 的那一段。
    """
    roots = set()
    for directory in holders:
        parts = directory.split("/")
        for index, part in enumerate(parts):
            if part == "include":
                roots.add("/".join(parts[:index + 1]))
                break
    return sorted(roots, key=lambda item: (item.count("/"), item))


def _subdirs_under(holders, top):
    """top 之下、直接持头文件的子目录名。"""
    # 分隔符取 os.sep：相对路径由 os.walk 产出，写死 "/" 会静默匹配不上
    prefix = top + os.sep
    return sorted({
        directory[len(prefix):].split(os.sep)[0]
        for directory in holders
        if directory.startswith(prefix)
        and "/" not in directory[len(prefix):]})


def detect_header_filter(root, files):
    """按工程 include 形状给 TIDY_HEADER_FILTER 建议，返回 (正则或空, 依据说明)。"""
    holders = _header_dirs(files)
    include_roots = _include_roots(holders)
    if include_roots:
        top = include_roots[0]
        if top in holders:
            # 头文件直接躺在 include/ 下时，没有可锚定的工程段：写成 include/.*
            # 会连 /usr/include/stdio.h 一起放行，正是配置审计要拦的那条。既然
            # 猜不出安全值，就如实留空让人来定，不生成一个自己会拒的配置
            return "", ("头文件直接位于 {0}/ 下，没有可锚定的工程段；"
                        "请写成 {0}/<工程名>/.* 这类只圈本仓的式子".format(top))
        subdirs = _subdirs_under(holders, top)
        if len(subdirs) == 1:
            return (r"{}/{}/.*".format(top, subdirs[0]),
                    "{} 下只有子目录 {} 直接持头文件".format(top, subdirs[0]))
        if subdirs:
            name = Path(root).name
            if name in subdirs:
                return (r"{}/{}/.*".format(top, name),
                        "子目录 {} 与工程名同名，按惯例取它".format(name))
            return (r"{}/.*".format(top),
                    "{} 下有多个子目录持头文件（{}），先用它覆盖全部并请复核"
                    .format(top, "、".join(subdirs)))
    source_like = sorted(
        (directory for directory in holders
         if directory == "src" or directory.startswith("src/")),
        key=lambda item: (item.count("/"), item))
    if source_like:
        return (r"{}/.*".format(source_like[0]),
                "头文件位于 {} 下（未发现 include 目录）".format(source_like[0]))
    return "", "没探到头文件所在目录；留空表示不注入，由项目 .clang-tidy 自己说了算"


def _has_license(directory):
    try:
        names = os.listdir(str(directory))
    except OSError:
        return False
    for name in names:
        if name.lower().split(".")[0] not in LICENSE_STEMS:
            continue
        # 必须是文件：叫 LICENSE 的**目录**不是许可证（判据写的是"含 LICENSE"，
        # 目录命中会让一份没有许可证的 vendored 树被塞进禁改区）
        if os.path.isfile(os.path.join(str(directory), name)):
            return True
    return False


def _has_own_build_file(files, directory):
    """目录下是否直接有本工程的构建文件。"""
    prefix = directory + os.sep
    for rel in files:
        if not rel.startswith(prefix):
            continue
        rest = rel[len(prefix):]
        if "/" in rest:
            continue
        if rest == "CMakeLists.txt" or rest.endswith(".pro"):
            return True
    return False


def detect_frozen(root, files, dirs):
    """识别 vendored 冻结库目录，返回 (目录表, 依据说明)。

    判据：目录名命中 third_party|vendor|external 之类，或目录含许可证文件且
    没有本工程自己的构建文件（那就是别人搬进来的东西）。FROZEN 是"绝不可写"的
    红线语义，因此这里宁可少判——漏判只是没进禁改区，误判会挡住正常改写。
    """
    root = Path(root)
    vendored = []
    for directory in dirs:
        if directory.rsplit(os.sep, 1)[-1].lower() in VENDORED_NAMES:
            vendored.append(directory)
    prefixes = tuple(item + os.sep for item in vendored)
    for directory in dirs:
        if directory in vendored or directory.startswith(prefixes):
            continue
        if (_has_license(root / directory)
                and not _has_own_build_file(files, directory)):
            vendored.append(directory)
    vendored = sorted(set(vendored))
    if not vendored:
        return [], "没探到 vendored 目录（名字命中 third_party 之类，或含许可证且无本工程构建文件）"
    return vendored, "命中 vendored 判据：" + "、".join(vendored)


def _git_tracked_top(root):
    """git 跟踪的顶层目录名；不在 git 仓库里返回 None。"""
    try:
        result = subprocess.run(
            ["git", "ls-files"], cwd=str(root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    dirs = {line.split("/", 1)[0] for line in result.stdout.splitlines()
            if "/" in line}
    return sorted(dirs)


def _has_sources(files, directory):
    prefix = directory + os.sep
    for rel in files:
        if rel.startswith(prefix) and rel.lower().endswith(SOURCE_SUFFIXES):
            return True
    return False


def own_vendored_dir(root):
    """本工具自己被拷进工程时所占的那个顶层目录名；不在工程内则返回空串。

    标准接入流程就是把 cxx_ci/ 整个拷进工程。它被版本控制跟着、里面又有源码，
    于是会被探成"项目自己的代码"——工具于是去分析自己那份 vendored googletest，
    报出一堆与用户无关的诊断，而且 --apply 会去改它。这里按本文件的位置认出
    自己，排除区与禁改区都收进来。
    """
    here = Path(__file__).resolve().parent
    root = Path(str(root)).resolve()
    if not paths.is_within(here, root, strict=False):
        return ""
    relative = here.relative_to(root)
    return relative.parts[0] if relative.parts else ""


def detect_scope(root, files, frozen):
    """项目范围与排除范围，返回 (project, excluded, 依据说明)。

    有 git 就从版本控制跟踪的顶层目录取——构建产物不在其中；没有 git 就按
    src / include / tests 命名惯例退回。excluded 始终排掉构建目录与 vendored
    目录，否则构建产物里的代码会被当成项目自己的。
    """
    root = Path(root)
    tracked = _git_tracked_top(root)
    project, excluded = [], []
    if tracked is not None:
        for name in tracked:
            if name in VENDORED_NAMES or name in BUILD_DIR_NAMES:
                excluded.append(name)
            elif _has_sources(files, name):
                project.append(name)
        note = "git 跟踪的顶层目录"
    else:
        for name in FALLBACK_SOURCE_DIRS:
            if (root / name).is_dir():
                project.append(name)
        note = "src / include / tests 命名惯例（未发现 git）"
    # 工具自己那份拷贝永远不是被测代码，也绝不该被 --apply 改写
    own = own_vendored_dir(root)
    if own and own not in frozen:
        frozen.append(own)
    for name in frozen:
        if name not in excluded:
            excluded.append(name)
    build_dir = detect_build_dir(root)
    if build_dir and build_dir not in excluded:
        excluded.append(build_dir)
    project = [name for name in project if name != own]
    project = sorted(set(project))
    excluded = sorted(set(excluded))
    if not project:
        project = ["."]
        note += "，未探到源码目录，暂用整个项目根（请复核）"
    if excluded:
        note += "；排除 " + "、".join(excluded)
    return project, excluded, note


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


class Item:
    """配置里的一项：可直接写出的取值代码 + 依据/待填说明。"""

    def __init__(self, code, comment="", todo=False):
        self.code = code
        # 注释一律带前缀：读者要能一眼分清"这条有依据"与"这条要人填"，
        # 不带前缀的说明混在中间，等于两者都看不出来
        if comment and not comment.startswith(("依据:", _TODO)):
            comment = "依据: " + comment
        self.comment = comment
        self.todo = todo


class Plan:
    """一次探测的结论，按生成配置的分节组织。"""

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


def _config_file_items(root, created):
    """两个 clang 配置文件项：依据写清它是已有还是刚生成。"""
    items = []
    for name, filename in (("CLANG_FORMAT_CONFIG", ".clang-format"),
                           ("CLANG_TIDY_CONFIG", ".clang-tidy")):
        if filename in created:
            comment = ("依据: 缺 {}，已按最小模板生成——配置里点了名却找不到会"
                       "直接拒绝，不退成逐级向上查找".format(filename))
        else:
            comment = "依据: 项目根下已存在 {}".format(filename)
        items.append((name, Item(_path_expr(filename), comment)))
    return items


def probe(root, created=()):
    """探测一个工程，返回可直接渲染的 Plan。"""
    root = Path(root)
    created = set(created)
    files, _source_dirs = scan(root)
    dirs = all_dirs(root)
    plan = Plan(root)

    build_tool, _build_file, build_note = detect_build(files)
    if build_tool is None:
        build_items = [
            ("BUILD_TOOL", Item('"cmake"',
                                _TODO + "（" + build_note + "）", todo=True)),
        ]
    else:
        build_items = [
            ("BUILD_TOOL", Item('"{}"'.format(build_tool),
                                "依据: " + build_note)),
        ]
    build_dir = detect_build_dir(root)
    if build_dir:
        build_items.append(
            ("BUILD_DIR", Item(_path_expr(build_dir),
                               "依据: 项目根下已存在目录 " + build_dir)))
    else:
        build_items.append(
            ("BUILD_DIR", Item('ROOT / "build"', "缺省构建目录名（未发现已有构建目录）")))
    plan.section("构建上下文", build_items)

    tools_items = _config_file_items(root, created)
    major, major_note = detect_clang_major()
    if major is None:
        tools_items.append(
            ("CLANG_MAJOR", Item("None", _TODO + "（" + major_note + "）", todo=True)))
    else:
        tools_items.append(
            ("CLANG_MAJOR", Item(str(major), "依据: " + major_note)))
    plan.section("clang 工具", tools_items)

    frozen, frozen_note = detect_frozen(root, files, dirs)
    if frozen:
        plan.section("禁改区", [
            ("FROZEN", Item(_path_list_expr(frozen), "依据: " + frozen_note)),
        ])

    project, excluded, scope_note = detect_scope(root, files, frozen)
    plan.section("处理范围", [
        ("SCOPE", Item(_scope_expr(project, excluded), "依据: " + scope_note)),
    ])

    header_filter, header_note = detect_header_filter(root, files)
    if header_filter:
        filter_item = Item('r"{}"'.format(header_filter), "依据: " + header_note)
    else:
        filter_item = Item('""', _TODO + "（" + header_note + "）", todo=True)
    plan.section("风格", [("TIDY_HEADER_FILTER", filter_item)])

    # 自动修复会改代码，改坏没改坏要靠这条指令验证；留空时 --apply 会拒绝动手。
    # 工具自己被拷进工程时，验证指令就是那份 ci.py —— 由工具的位置直接推出来，
    # 不必让使用者去猜路径
    own = own_vendored_dir(root)
    if own:
        command = ('["python3", ROOT / "{}" / "ci" / "ci.py",'
                   ' "--only", "build,build_test,run_test"]'.format(own))
        verify_item = Item(command, "依据: 工具随工程分发在 {}/ 下，"
                                    "用它的构建/测试阶段验证修复".format(own))
    else:
        verify_item = Item(
            "[]", _TODO + "（自动修复会改代码，需给出验证指令；"
                          "留空则 --apply 拒绝动手）", todo=True)
    plan.section("验证", [("VERIFY_COMMAND", verify_item)])
    return plan


def _scope_expr(project, excluded):
    """SCOPE 是多行字典，渲染成可读的几行。"""
    return "\n".join([
        "{",
        '    "project": {},'.format(_path_list_expr(project)),
        '    "excluded": {},'.format(_path_list_expr(excluded)),
        "}",
    ])


_HEADER = '''"""项目配置（由 cxx-standardize init 探测生成）。

生成者: cxx-standardize {version}（跨项目契约 contract {contract}）。
每条取值后的 `# 依据:` 说明它从哪读出来；标 `# TODO:` 的项需人工填写。
探测只列候选、不猜：构建工具多份并存时会留成 TODO 由人定。
"""'''


def render(plan):
    """把 Plan 渲染成配置文本。"""
    lines = [_HEADER.format(version=__version__, contract=CONTRACT_VERSION), ""]
    add = lines.append
    add("from pathlib import Path")
    add("")
    add("# 与另一个项目（ci）之间的契约版本：两侧一致才执行，不一致拒绝")
    add("CONTRACT_VERSION = {}".format(CONTRACT_VERSION))
    add("")
    add("# 本文件位于 <工程>/config/standardize.py，父目录即项目根")
    add("ROOT = Path(__file__).resolve().parents[1]")
    for title, items in plan.sections:
        add("")
        add("# " + "-" * 16 + " " + title)
        for name, item in items:
            if item.comment:
                for line in item.comment.splitlines():
                    add("# " + line)
            add("{} = {}".format(name, item.code))
    add("")
    return "\n".join(lines)


def write_config(target, text):
    """把生成的配置写到 target，返回 (错误码, 说明)。

    目标一定是**新建**文件：已存在时由调用方先行拒绝，因此这里按 UTF-8 新建。
    经 source.write_file 落盘——本工具写文本的入口只有它一个，绕过它会被配置
    审计的裸写文本检查拦住。
    """
    return source.write_file(target, text, None)


def ensure_config_files(root):
    """缺失的 .clang-format / .clang-tidy 落最小模板。

    返回 (新建的文件名列表, 失败说明列表)。写的是**工程**里的文件，不是工具
    目录——项目配置不进工具目录，与 `config/config.py.example` 同源。
    """
    root = Path(root)
    created, errors = [], []
    for filename, text in ((".clang-format", TEMPLATE_CLANG_FORMAT),
                           (".clang-tidy", TEMPLATE_CLANG_TIDY)):
        path = root / filename
        if path.exists():
            continue
        code, message = source.write_file(path, text, None)
        if code:
            errors.append("{}：{}".format(filename, message))
        else:
            created.append(filename)
    return created, errors


def summary(plan, target, created=()):
    """生成完成后给使用者的一段提示。"""
    lines = []
    if created:
        lines.append("已生成最小配置模板: " + "、".join(created)
                     + "（配置里点了名却找不到会被直接拒绝，不退成逐级向上查找）")
    lines.append("已生成: " + str(target))
    todos = plan.todos()
    if todos:
        lines.append("有 {} 项需要人工填写: {}".format(
            len(todos), "、".join(todos)))
    lines.append("下一步: 复核生成的配置，然后用 `--config {}` 跑 "
                 "cxx-standardize".format(target))
    return lines
