"""配置审计：把"代码必须严格经配置取值"变成可机检的纪律。

门禁脚本自身也是最容易被绕过的代码：有人图省事在脚本里写死路径或工具名，配置
就慢慢变成摆设——改配置不生效，行为随文件漂移。这里只读地比对配置与源码，三条
判据各自对应一种漂移：

1. 死配置：枚举配置对象上的全部公开键，若某个键名在所有被审计的源码里从未被
   引用，就报出来。枚举的是运行时真正存在的键（配置对象属性），不是配置文件里
   的文本行——拼错、没接线、加载器根本不认的项不会出现在配置对象上。
2. 配置旁路：被审计的项目源码里出现与某个配置键取值完全相同的字符串字面量，
   说明有人绕过配置直接写死了同一个值，改配置时那处不会跟着变。文档字符串是
   说明文字而非取值，因此排除。本包自身的实现代码不在这一项的范围里：它必须
   包含构建工具名、诊断级别名这类词汇表，判红只会让审计永远不绿。
3. 编译标准锁定：cmake 构建文件里的 ``CMAKE_CXX_STANDARD`` 必须存在且等于配置
   里的目标标准，任何一处别的 ``-std=`` 都判红——注释里的示例也会被构建系统当真。
4. 裸写文本：本包自身实现里出现绕过 source.py 直接写回文本的调用，会把非 UTF-8
   的存量文件静默写成 U+FFFD——这是不可逆的数据损坏，必须拦住。
5. 自行前缀比较：本包自身实现里又自行拼前缀判断"路径是否在某目录内"，而本包
   只允许 paths.py 一处实现（归一化策略统一为 realpath）。两处各拼一遍前缀、
   归一化方式还不一致，就会出现同一份诊断在不同阶段归因相反的问题——每处单看
   都对，合起来矛盾。这里拦新增，存量已收口。

判据都要能审自己：本模块自身在检查 2 的范围内，因此这里不写任何与配置取值相同
的字面量，锁定标准直接取 ``CXX_STANDARD``。

本模块与 ci/lib/audit.py 为平行实现：两个项目互不 import，只靠文件与命令行契约
协作（见 CONTRACT.md），因此同一套纪律被写成两份。改动须同步两侧，并跑
tools/parity 的同固件对照测试（两侧各一份 test_audit_parity.py），否则两侧规则
会静默分叉。本侧是参照实现（现代 Python、能跑全部判据），expected.json 由本侧
实跑生成。

失败不抛异常，按本项目的约定返回 ``(Code, list[str])``。
"""

from __future__ import annotations

import ast
import glob
import os
import re
from pathlib import Path

from .errors import Code
from .source import DEFAULT_ENCODING

# 本包目录；被审计的自身实现代码都在它下面
PACKAGE_DIR = Path(__file__).resolve().parent

CONFIG_DIR = "config"
CONFIG_NAME = "config.py"

PY_SUFFIX = ".py"
CMAKE_BUILD_NAME = "CMakeLists.txt"
CMAKE_STD_VAR = "CMAKE_CXX_STANDARD"
STD_MARK = "-std="

# 递归扫描时跳过的目录：版本库元数据与字节码缓存，都不是构建文件
SKIP_DIRS = (".git", "__pycache__")

# 配置对象上由加载流程写上去的提示，不是项目配置项
NON_CONFIG_KEYS = ("warnings",)

# 从配置对象取值时用的名字。按名字认，见 _is_config_base
CONFIG_NAMES = ("cfg", "config", "conf", "settings")

# 本模块自身：它是唯一参与检查 2 的自身代码，写它时就得经配置取值
_AUDIT_SELF = Path(__file__).resolve()

# 检查 4 的裸写文本写法：绕过 source.py 直接写回文本。唯一白名单是 source.py
# ——它本身就是那个写入口。模式串写成正则片段而不是字面调用，本文件才不会被
# 自己的检查命中
RAW_IO_WHITELIST = "source.py"
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
    "tidy.py": {
        "open": "派生 .clang-tidy 是工具自有产物（把项目的 .clang-tidy 去掉命名"
                "检查后写给 clang-tidy 用的临时配置），不是用户源码；内容是工具"
                "生成的 YAML，恒定 UTF-8",
    },
    "cli.py": {
        "open": "与 ci 侧同形：init 生成的是工具自有的配置产物，恒定 UTF-8",
    },
    "log.py": {"write_text": "阶段日志是工具自有产物，恒定 UTF-8"},
    "platform.py": {
        "open": "互斥锁文件里只写一个 pid，是工具自有的运行期产物，不是用户源码；"
                "它没有原编码可言，也不必原子替换——内核的 flock 才是互斥本身",
    },
    "compile_db.py": {"write_text": "编译数据库是生成的 JSON 产物，恒定 UTF-8"},
    "baseline.py": {"encode": "指纹哈希材料，不是文件写入",
                    "write_text": "排版基线是工具自有记录，恒定 UTF-8"},
    "report.py": {"open": "报告是工具自有旁路产物，恒定 UTF-8",
                  "encode": "SARIF 指纹哈希材料，不是文件写入"},
}


# ------------------------------------------------------------------ 审计对象

def default_targets(config) -> list[Path]:
    """缺省审计对象：本包自身的实现代码 + 项目配置 + AUDIT_FILES_GLOBS。

    自身实现代码让"审计能审自己"成立；项目配置文件与 AUDIT_FILES_GLOBS 让项目
    自己的脚本也进纪律范围。目录交给 collect_files 递归取 ``*.py``。
    """
    targets: list[Path] = []
    targets.extend(sorted(PACKAGE_DIR.rglob("*" + PY_SUFFIX), key=str))
    config_file = PACKAGE_DIR.parents[2] / CONFIG_DIR / CONFIG_NAME
    if config_file.is_file():
        targets.append(config_file)
    root = getattr(config, "root", None)
    if root:
        for pattern in getattr(config, "audit_files_globs", None) or []:
            for found in sorted(glob.glob(os.path.join(str(root), str(pattern)),
                                          recursive=True)):
                targets.append(Path(found))
    return targets


def collect_files(targets) -> list[Path]:
    """把审计对象展开成待解析的 py 文件：目录递归，只留 ``*.py`` 后缀。"""
    found: list[Path] = []
    seen: set[str] = set()
    for target in targets:
        path = Path(str(target))
        if path.is_dir():
            for child in sorted(path.rglob("*" + PY_SUFFIX), key=str):
                _remember(found, seen, child)
        elif path.is_file() and path.name.endswith(PY_SUFFIX):
            _remember(found, seen, path)
    return found


def _remember(found: list[Path], seen: set[str], path: Path) -> None:
    """按绝对路径去重：同一份文件可能既被目录扫到又被 glob 点到。"""
    absolute = os.path.abspath(str(path))
    if absolute in seen:
        return
    seen.add(absolute)
    found.append(path)


# ------------------------------------------------------------------ 配置面

def config_keys(config) -> list[str]:
    """配置对象上的公开键。

    从实例属性取，而不是从配置文件文本取：只有真正挂到配置对象上的名字才是
    运行时可用的开关，配置文件里的错字不会出现在这里。
    """
    fields = getattr(config, "__dict__", None) or {}
    names = [
        name for name in fields
        if not name.startswith("_") and name not in NON_CONFIG_KEYS
    ]
    return sorted(names)


def _collect_values(value, found: set) -> None:
    """把一个配置取值展开成字符串集合，容器与字典逐层展开。"""
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


def _value_owners(config) -> dict:
    """字符串取值到键名的映射，用于把命中的字面量指回它该用的配置项。"""
    owners: dict = {}
    for key in config_keys(config):
        values: set = set()
        _collect_values(getattr(config, key, None), values)
        for value in sorted(values, key=str):
            owners.setdefault(value, key)
    return owners


# ------------------------------------------------------------------ 源码解析

def _string_value(node) -> str | bytes | None:
    """取出字符串/字节字面量的取值；不是字面量时返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return node.value
    return None


def _referenced_names(tree: ast.AST) -> set:
    """源码里真的从配置对象上取过值的键名。

    只认"从配置对象取值"的写法：``cfg.KEY``、``config.KEY``，以及
    ``getattr(config, "KEY", 默认)`` 里的字符串实参。

    裸名字与关键字实参一律不算：配置加载器里必然写着 ``self.KEY = KEY`` 与
    ``Config(KEY=...)``，那两处是"把配置装进对象"，不是"有人用它"。把它们算成
    引用，等于每个键都自己引用自己，这条检查就永远查不出东西。赋值目标同样
    不算；注释与文档字符串也不算——说明里提一句某个配置项，不代表有人用它。
    """
    names: set = set()
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


def _is_config_base(node: ast.AST) -> bool:
    """属性访问的基是不是"配置对象"这个名字。

    按名字认，而不是按类型：静态解析看不出类型，而各处的写法是固定的
    （``config.xxx`` / ``cfg.xxx``）。认名字就够，也避免了把 ``self.xxx`` 算进来。
    """
    return isinstance(node, ast.Name) and node.id in CONFIG_NAMES


def _docstring_ids(tree: ast.AST) -> set:
    """模块、类、函数体首个表达式的常量节点，它们不算取值。"""
    found: set = set()
    kinds = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
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


def string_literals(tree: ast.AST) -> list:
    """源码里的字符串/字节常量，返回 (行号, 取值) 列表；文档字符串除外。"""
    skipped = _docstring_ids(tree)
    literals: list = []
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        value = _string_value(node)
        if not value:
            continue
        literals.append((node.lineno, value))
    return literals


def _parse(path: Path) -> ast.AST | None:
    """解析一份源码；读不出来或语法不对时返回 None，不参与任何判据。"""
    try:
        text = path.read_text(encoding=DEFAULT_ENCODING, errors="replace")
    except OSError:
        return None
    try:
        return ast.parse(text, filename=str(path))
    except (SyntaxError, ValueError):
        return None


def _parse_all(paths) -> list:
    parsed = []
    for path in paths:
        tree = _parse(path)
        if tree is not None:
            parsed.append((path, tree))
    return parsed


# ------------------------------------------------------------------ 检查 1

def _check_dead(config, parsed, messages: list) -> Code:
    """死配置：没有任何被审源码引用过的键。"""
    referenced: set = set()
    for _path, tree in parsed:
        referenced |= _referenced_names(tree)
    code = Code.OK
    for key in config_keys(config):
        if key not in referenced:
            messages.append(f"死配置: cfg.{key} 从未被引用")
            code = Code.AUDIT_DEAD_KEY
    return code


# ------------------------------------------------------------------ 检查 2

def _is_tool_source(path) -> bool:
    """是否本包自身的实现代码。"""
    absolute = os.path.abspath(str(path))
    prefix = os.path.abspath(str(PACKAGE_DIR)) + os.sep
    return absolute.startswith(prefix)


def _is_config_source(path) -> bool:
    """是否项目配置文件本身：它写下的值就是配置值，等于自己不是旁路。"""
    absolute = os.path.abspath(str(path))
    return (os.path.basename(absolute) == CONFIG_NAME
            and os.path.basename(os.path.dirname(absolute)) == CONFIG_DIR)


def _bypass_scope(path) -> bool:
    """检查 2 只看项目自己的文件，外加本模块自身。"""
    absolute = os.path.abspath(str(path))
    # 本模块自身曾单列在范围内（审计自己的配置取值）。现在收回：工具内部合法地
    # 硬编码工具级常量（例如读 Python 源码用 utf-8、读自己的文件用 ascii），
    # 那与"项目的配置被旁路"是两回事。配置键是否有人用由死配置检查负责，
    # 那条检查仍然覆盖本模块
    if absolute == str(_AUDIT_SELF):
        return False
    if _is_config_source(absolute):
        return False
    return not _is_tool_source(absolute)


def _check_bypass(config, parsed, messages: list) -> Code:
    """配置旁路：与配置取值完全相同的字符串字面量。"""
    owners = _value_owners(config)
    if not owners:
        return Code.OK
    code = Code.OK
    for path, tree in parsed:
        if not _bypass_scope(path):
            continue
        for line, value in string_literals(tree):
            key = owners.get(value)
            if key is None:
                continue
            messages.append(
                f"配置旁路: {path}:{line} 的字面量 {value!r} 应改用 cfg.{key}")
            code = Code.AUDIT_BYPASS_LITERAL
    return code


# ------------------------------------------------------------------ 检查 3

def _same_standard(left, right) -> bool:
    """两个标准写法是否等价：-std=gnu++0x、gnu++0x、c++11、11 都归一化后比对。"""
    return _standard_token(left) == _standard_token(right)


def _standard_token(text) -> str:
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


def _std_tokens(line: str) -> list:
    """一行里出现的全部 -std= 取值（注释行与代码行一视同仁）。"""
    return re.findall(r"-std=[A-Za-z0-9_+.\-]+", line)


_PIN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _pin_parts(pin) -> tuple:
    """把锁定值拆成 (变量名或 None, 取值)。

    配置允许把承载变量名一起写进锁定值（QMAKE_CXXFLAGS_CXX11=-std=gnu++0x），
    本侧通常只写取值；两种写法都认，两侧判据才同构。
    """
    text = str(pin).strip()
    if "=" in text:
        name, value = text.split("=", 1)
        if _PIN_NAME.match(name.strip()) and value.strip():
            return name.strip(), value.strip()
    return None, text


def _assignment_value(line: str, name: str) -> str | None:
    """取 cmake 构建文件里给承载变量赋的值；没有这一行时返回 None。"""
    pattern = re.escape(name)
    match = re.match(r"^\s*set\s*\(\s*" + pattern + r"\s+([^\s)]+)", line)
    if match:
        return match.group(1)
    match = re.match(r"^\s*" + pattern + r"\s*[:=]\s*(.+?)\s*$", line)
    return match.group(1) if match else None


def _carrier_locked(lines: list, carriers: list, pin: str) -> bool:
    """承载变量存在且值等于锁定标准：任一允许变量命中即可。"""
    for line in lines:
        for name in carriers:
            value = _assignment_value(line, name)
            if value is not None and _same_standard(value, pin):
                return True
    return False


def _excluded_build_dirs(config) -> list:
    """构建文件扫描要跳过的目录。

    两类：①构建目录——那里的 CMakeLists 多是生成物；②禁改区（FROZEN）与
    旧的 WHITELIST/BLACKLIST 里点名的目录——vendored 的第三方树自带构建文件
    是常态，它不属于本工程的标准锁定面。少了第二类，往工程里 vendor 一个带
    CMakeLists 的库就会把「编译标准漂移」判红。
    """
    excluded = []
    for name in ("build_dir", "test_build_dir"):
        value = getattr(config, name, None)
        if value:
            excluded.append(os.path.abspath(str(value)) + os.sep)
    for root in getattr(config, "frozen", None) or ():
        excluded.append(os.path.abspath(str(root)) + os.sep)
    for root in getattr(config, "blacklist", None) or ():
        excluded.append(os.path.abspath(str(root)) + os.sep)
    return excluded


def _build_files(config) -> list:
    """从项目根递归取 cmake 构建文件。"""
    root = getattr(config, "root", None)
    if not root or not os.path.isdir(str(root)):
        return []
    excludes = _excluded_build_dirs(config)
    found = []
    for current, dirs, names in os.walk(str(root)):
        dirs[:] = [name for name in dirs
                   if name not in SKIP_DIRS
                   and not _under(os.path.join(current, name), excludes)]
        for name in names:
            if name == CMAKE_BUILD_NAME:
                found.append(Path(os.path.join(current, name)))
    return sorted(found, key=str)


def _under(path: str, prefixes: list) -> bool:
    absolute = os.path.abspath(path) + os.sep
    return any(absolute.startswith(prefix) for prefix in prefixes)


def _read_lines(path: Path) -> list:
    try:
        return path.read_text(encoding=DEFAULT_ENCODING,
                              errors="replace").splitlines()
    except OSError:
        return []


def _check_std(config, messages: list) -> Code:
    """编译标准锁定：承载变量缺失/值不符、任何一处别的 -std= 都判红。"""
    pin = str(getattr(config, "cxx_standard", None) or "").strip()
    if not pin:
        return Code.OK
    files = _build_files(config)
    if not files:
        return Code.OK
    carriers = [CMAKE_STD_VAR]
    pin_name, pin_value = _pin_parts(pin)
    if pin_name:
        # 锁定值点了名就以它为准；本侧通常不写变量名
        carriers = [pin_name]
    code = Code.OK
    locked = 0
    for path in files:
        lines = _read_lines(path)
        if _mentions_carrier(lines, carriers):
            # 出现承载变量就得等于锁定值。没出现的按"继承"处理：cmake 的子目录
            # 文件不写 CMAKE_CXX_STANDARD 是常态，顶层那一处才管全局；要求每个
            # 文件都写一遍，等于把正常工程判成漂移
            locked += 1
            if not _carrier_locked(lines, carriers, pin_value):
                messages.append(
                    f"编译标准漂移: {path} 的承载变量 "
                    f"{'、'.join(carriers)} 取值不等于 {pin_value}")
                code = Code.AUDIT_STD_DRIFT
        for number, line in enumerate(lines, 1):
            for token in _std_tokens(line):
                if not _same_standard(token, pin_value):
                    messages.append(
                        f"编译标准漂移: {path}:{number} 出现其它标准 {token}")
                    code = Code.AUDIT_STD_DRIFT
    if not locked:
        # 一处都没有 = 没有任何东西钉住标准，等于没锁
        messages.append(
            f"编译标准漂移: 全部构建文件都没有承载变量 "
            f"{'、'.join(carriers)}，标准未被锁定")
        code = Code.AUDIT_STD_DRIFT
    return code


def _mentions_carrier(lines: list, carriers: list) -> bool:
    """这些行里有没有出现承载变量。"""
    for line in lines:
        for carrier in carriers:
            if carrier and carrier in line:
                return True
    return False


# ------------------------------------------------------------------ 检查 4

def _check_raw_io(paths, messages: list) -> Code:
    """裸写文本：绕过 source.py 直接写回文本的调用。

    只扫本包自身的实现代码：项目脚本里出现的写文本多半是别人的正常代码，判红
    只会让审计永远不绿。判定不出编码的文件会被静默写成 U+FFFD，这是不可逆的，
    因此这里必须拦住后来人又写回裸调用。
    """
    code = Code.OK
    for path in paths:
        if not _is_tool_source(path):
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
                    f"裸写文本: {path}:{number} 绕过了 source.py，"
                    "非 UTF-8 的存量文件会被写成 U+FFFD")
                code = Code.AUDIT_RAW_IO
    return code


# ------------------------------------------------------------------ 检查 5

# 要拦的写法标记：字符串方法名与路径分隔符同时出现在一行。标记拆成片段拼装，
# 而不是写成完整字面串，本模块才不会被自己这条检查命中
PREFIX_MARK = "startswith" + "("
# 两种分隔符写法都要拦：os.sep 是"知道有这回事"的写法，写成裸斜杠字面量的是
# "忘了"的写法。只拦前者时后一种一样会漏（实测漏过）
SEP_MARKS = ("os.sep", '"/"', "'/'")
# 唯一白名单：paths.py 就是那个"唯一实现"，它当然要自己写前缀比较
PREFIX_WHITELIST = "paths.py"


def _check_prefix_compare(files, messages: list) -> Code:
    """自行前缀比较：本包自身实现里另写了一遍路径包含判断。

    本包只允许 paths.py 一处实现（归一化统一为 realpath）。别处图省事再拼一遍
    前缀，就会重蹈 abspath/realpath（或 resolve）混用、同一份诊断在不同阶段
    归因相反的覆辙。这里只拦"自身实现"代码；项目脚本不在范围。
    """
    code = Code.OK
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
                    f"自行前缀比较: {path}:{number} 又拼了一遍路径包含判断，"
                    "应改用 paths.is_within")
                code = Code.AUDIT_PREFIX_DRIFT
    return code


# ------------------------------------------------------------------ 入口

# HeaderFilter 的形状检查用这些路径做探针：它们在任何工程里都属于"别人的头
# 文件"。正则只要能匹配上其中一条，就说明它写宽了
SYSTEM_PROBES = ("/usr/include/stdio.h", "/usr/local/include/x.h",
                 "/opt/vendor/include/x.h", "/usr/lib/clang/17/include/x.h")


def _check_header_filter(config, messages: list) -> Code:
    """HeaderFilter 必须只圈住本仓头文件。

    实测教训：写成 `(include|src|tests)/.*` 会把 `/opt/qt-5.6.3/include/...`
    一起放进来，某个工程上的诊断从 67 条涨到 5501 条——把工具的口味当成了
    被测代码的问题。这里直接拿几条"必然属于别人"的路径去试，匹配上就拒用。
    """
    pattern = str(getattr(config, "tidy_header_filter", "") or "").strip()
    if not pattern:
        return Code.OK
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        messages.append(f"TIDY_HEADER_FILTER 不是合法正则: {exc}")
        return Code.HEADER_FILTER_TOO_BROAD
    hit = [item for item in SYSTEM_PROBES if compiled.search(item)]
    if hit:
        messages.append(
            "TIDY_HEADER_FILTER 写得太宽，会放行系统头文件（例如 {}）；"
            "应写成只圈本仓的式子，如 include/<工程名>/.*。头文件直接放在 "
            "include/ 下时没有可锚定的工程段，给它加一层同名目录再写"
            .format(hit[0]))
        return Code.HEADER_FILTER_TOO_BROAD
    return Code.OK


def run(config, targets=None) -> tuple[Code, list]:
    """审计配置与源码，返回 ``(错误码, 说明行列表)``。

    targets 为空时用 default_targets；目录递归取 ``*.py``。各类问题一次全列，错误
    码取第一类命中的，便于调用方按类型分流。
    """
    if not targets:
        targets = default_targets(config)
    files = collect_files(targets)
    parsed = _parse_all(files)
    messages: list = []
    codes = [
        _check_dead(config, parsed, messages),
        _check_bypass(config, parsed, messages),
        _check_std(config, messages),
        _check_header_filter(config, messages),
        _check_raw_io(files, messages),
        _check_prefix_compare(files, messages),
    ]
    for code in codes:
        if code != Code.OK:
            return code, messages
    return Code.OK, messages
