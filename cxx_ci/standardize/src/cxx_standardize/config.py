"""配置的加载与校验。

配置是一个 Python 模块（`config/config.py`），路径一律写成 `ROOT / "..."`。
校验在加载时一次做完：配置写错必须当场说清，而不是等某个阶段跑到一半才炸。

本项目的配置**自足**：项目根、构建目录、处理范围都在这里再写一份。它与门禁
项目 `ci` 各有一份配置，公共字段的语义由 CONTRACT.md 约定，不靠共享代码。
"""

import importlib.machinery
import importlib.util
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .errors import Code
from .version import CONTRACT_VERSION

# 本项目的根目录，用来定位默认配置
TOOL_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = TOOL_ROOT / "config" / "config.py"
CONFIG_NAME = "cxx_standardize_config"

# 配置位置的查找顺序：--config 显式给出 > 环境变量 > 工具自带的 config/config.py。
# 装成包（pipx 等）时工具目录里没有 config/，因此显式指定是常态而非例外。
CONFIG_ENV = "CXX_STANDARDIZE_CONFIG"

# 必填项：没有就给不出可用的默认值，缺了直接报错
# 只列本包真正会读的项。跑构建、找构建文件是 ci 的事，这里多列一项就等于
# 逼移植者填一个永远不生效的配置——配置审计的死配置检查会当场把它抓出来
REQUIRED = ("ROOT", "CLANG_FORMAT_CONFIG", "CLANG_TIDY_CONFIG")

# 注释保护模式。none 完全信任 .clang-format；另两种先把注释换成占位符
COMMENT_MODES = ("protect-column", "placeholder", "none")

# 编译数据库 provider 名

# 构建工具。cmake 的构建文件必须是 CMakeLists.txt，qmake 系是 .pro
BUILD_TOOLS = ("cmake", "qmake", "qmake6")

# 构建目录的缺省名。本包只读编译数据库，因此这里只是"去哪儿找"的缺省
DEFAULT_BUILD_DIR = "build"

DEFAULTS: dict[str, object] = {
    "BUILD_TOOL": "cmake",

    "CLANG_MAJOR": None,
    "CLANG_TIDY_EXTRA_ARGS": [],
    "WHITELIST": [],
    "BLACKLIST": [],
    "SCOPE": {},
    "FORMAT_COMMENTS": "placeholder",
    "TIDY_PREPARE": True,
    "INCREMENTAL": False,
    "INCREMENTAL_INCLUDES": True,
    "XFAIL_FILE": None,
    "XFAIL_STRICT": False,
    "CI_LOG_DIR": None,
    "JOBS": 0,
    "VERIFY_COMMAND": [],
    "BUILD_COMMAND": [],
    "CLANG_FORMAT_BIN": "",
    "CLANG_TIDY_BIN": "",
    "CLANG_PREFER": "wheel",
    "SOURCE_ENCODING": "utf-8",
    "SOURCE_ENCODING_CANDIDATES": [],
    # 禁改区：绝不可写的路径（vendored 冻结库）。与黑名单分列——黑名单是
    # "不必分析"的宽容语义，这里是"绝不可写"的红线语义
    "FROZEN": [],
    # 强制注入的 header-filter：命令行优先于项目 .clang-tidy，防它被改宽或改窄
    "TIDY_HEADER_FILTER": "",
    "NAMING_PREFIX": {},
    "NAMING_LOCAL_BAN": "gmstv",
    "NAMING_EXEMPT_KEYS": [],
    "NAMING_PREMISE_TRUE": [],
    "NAMING_FIX_PREFIX_SWAPS": [],
    # 配置审计：除本包自身外还要审的项目文件
    "AUDIT_FILES_GLOBS": [],
}


@dataclass
class Config:
    """一次运行用到的全部配置。"""

    root: Path
    build_tool: str
    build_dir: Path
    cxx_standard: str
    clang_format_config: Path
    clang_tidy_config: Path
    clang_major: int | None = None
    clang_tidy_extra_args: list[str] = field(default_factory=list)
    whitelist: list[Path] = field(default_factory=list)
    blacklist: list[Path] = field(default_factory=list)
    scope_roots: list[Path] = field(default_factory=list)
    scope_excluded: list[Path] = field(default_factory=list)
    format_comments: str = "placeholder"
    tidy_prepare: bool = True
    incremental: bool = False
    incremental_includes: bool = True
    xfail_file: Path | None = None
    xfail_strict: bool = False
    log_dir: Path | None = None
    jobs: int = 0
    verify_command: list[str] = field(default_factory=list)
    # 产出编译数据库的指令。编译数据库由构建产出，构建树被清理过、或只跑过
    # 部分构建时，数据库里的 directory 会指向不存在的路径，clang-tidy 遇到
    # 这种条目会直接中止。配了这条，工具会自己先构建一次再分析
    build_command: list[str] = field(default_factory=list)
    # 显式指定的 clang 可执行文件。写了就必须存在且版本达下限，找不到就报错并
    # 列出候选，不退回 PATH 搜索——"精确复现"与"随便找一个"不能混
    clang_format_bin: str = ""
    clang_tidy_bin: str = ""
    # 源码编码。缺省 "utf-8" 表示"只认 UTF-8（含 BOM）"；"auto" 才启用下面的
    # 候选列表；也可以写显式编码名（如 "gb18030"）只用它。
    source_encoding: str = "utf-8"
    # pip wheel 与系统 clang 的取舍：wheel（缺省）/ system / error
    clang_prefer: str = "wheel"
    # 候选编码，缺省为空：**不猜 GBK**。猜错比跳过危险得多——跳过只是不排版，
    # 猜错是把用户的中文注释永久写成 U+FFFD
    source_encoding_candidates: list[str] = field(default_factory=list)
    frozen: list[Path] = field(default_factory=list)
    tidy_header_filter: str = ""
    # 命名口径：kind → 前缀。改口径只改这张表，元检查据此现场推导 .clang-tidy
    # 应为何，偏离即红
    naming_prefix: dict = field(default_factory=dict)
    naming_local_ban: str = "gmstv"
    naming_exempt_keys: list = field(default_factory=list)
    naming_premise_true: list = field(default_factory=list)
    # 前缀中间态交换表，形如 (("t_m_", "t_"),)；默认空 = 不做任何交换
    naming_fix_prefix_swaps: list = field(default_factory=list)
    audit_files_globs: list[str] = field(default_factory=list)
    # 显式写在配置文件里的键名。用于区分"用户配了 SOURCE_ENCODING"与"它只是
    # 回落到了缺省值"——两者的提示语与 doc 含义不同。
    declared_keys: frozenset = field(default_factory=frozenset)

    @property
    def build_dirs(self) -> list[Path]:
        """需要编译数据库的构建目录。

        本包不跑构建，只读已有的编译数据库；测试工程与主工程同目录是常态，
        因此这里只有主构建目录一处。
        """
        return [self.build_dir]


class Reader:
    """读取配置模块的字段，累积第一条错误。

    只报第一条：配置错了一处往往连带后面都不可信，一次全列反而看不出该先
    改哪个。
    """

    def __init__(self, module, path):
        self.module = module
        self.path = path
        self.code = Code.OK
        self.message = ""
        self.notes = []

    def has(self, name):
        return hasattr(self.module, name)

    def value(self, name):
        """取值，缺省回落到 DEFAULTS。"""
        if hasattr(self.module, name):
            return getattr(self.module, name)
        return DEFAULTS.get(name)

    def fail(self, code, message):
        if self.code == Code.OK:
            self.code = code
            self.message = message

    def note(self, text):
        """非致命的提示，随配置返回由入口打印一次。"""
        self.notes.append(text)


def _load_module(path):
    """加载配置文件，返回 (模块, 错误码, 说明)。"""
    try:
        spec = importlib.util.spec_from_file_location(CONFIG_NAME, str(path))
        if spec is None or spec.loader is None:
            spec = importlib.util.spec_from_file_location(
                CONFIG_NAME, str(path),
                loader=importlib.machinery.SourceFileLoader(CONFIG_NAME,
                                                            str(path)))
        if spec is None or spec.loader is None:
            return None, Code.CONFIG_UNLOADABLE, (
                f"无法为配置文件建立加载器: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[CONFIG_NAME] = module
        spec.loader.exec_module(module)
    except FileNotFoundError:
        return None, Code.CONFIG_NOT_FOUND, (
            f"找不到配置文件: {path}\n"
            f"请把 config.py.example 复制为 config.py 后按需修改")
    except SyntaxError as exc:
        return None, Code.CONFIG_UNLOADABLE, (
            f"配置文件语法错误: {path}:{exc.lineno}: {exc.msg}")
    except Exception as exc:  # noqa: BLE001  配置是任意代码，边界处一律转成错误码
        return None, Code.CONFIG_UNLOADABLE, f"配置文件无法加载: {path}: {exc}"
    finally:
        sys.modules.pop(CONFIG_NAME, None)
    return module, Code.OK, ""


def _as_path(value):
    """把配置里的取值转成路径；转不了返回 None。

    配置写错（把整数、嵌套列表之类写进路径项）时不能让它一路走到 pathlib 里
    抛 TypeError：那对使用者是一段栈，而不是"哪一项、该写成什么样"。校验收在
    这一处，所有取路径的读取函数都过它。
    """
    if isinstance(value, (str, os.PathLike)):
        return Path(value)
    return None


def _absolute(reader, name, required=False):
    """读一个必须是绝对路径的字段。

    相对路径会随运行目录漂移，而排版结果必须可复现，因此一律要求写绝对路径。
    """
    value = reader.value(name)
    if value in (None, ""):
        if required:
            reader.fail(Code.CONFIG_MISSING_ITEM, f"配置缺少必需项: {name}")
        return None
    path = _as_path(value)
    if path is None:
        reader.fail(Code.CONFIG_BAD_VALUE,
                    "{} 必须是路径（字符串或 Path），实际是 {}；"
                    '请写成 ROOT / "..."'.format(name, type(value).__name__))
        return None
    if not path.is_absolute():
        reader.fail(Code.CONFIG_BAD_VALUE,
                    f'{name} 必须是绝对路径，请写成 ROOT / "{value}"')
        return None
    # 入口归一化一次（realpath）：后续比较一律用已归一化的值。与 ci 侧同一口径
    # ——软链布局下 abspath 与 realpath 混用会让同一份诊断在不同环节结论相反
    return Path(os.path.realpath(str(path)))


def _path_list(reader, name):
    """读一组路径，元素可以是 Path 也可以是 str；必须是绝对路径。

    与 ci 侧同一口径：漏写 ROOT 的路径会随运行目录漂移，而这里的判定决定
    哪些文件被分析、哪些区域不可写。静默拿 ROOT 补上会让"配置写错了"表现为
    "结果莫名其妙"，不如当场说清该写成 ROOT / "..."。
    """
    value = reader.value(name) or []
    if isinstance(value, (str, Path)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        reader.fail(Code.CONFIG_BAD_VALUE,
                    "{} 必须是路径列表，例如 [ROOT / \"src\"]，实际是 {}"
                    .format(name, type(value).__name__))
        return []
    found = []
    for index, item in enumerate(value):
        path = _as_path(item)
        if path is None:
            reader.fail(Code.CONFIG_BAD_VALUE,
                        "{} 第 {} 项必须是路径（字符串或 Path），实际是 {}"
                        .format(name, index + 1, type(item).__name__))
            continue
        if not path.is_absolute():
            reader.fail(Code.CONFIG_BAD_VALUE,
                        '{} 第 {} 项必须是绝对路径，请写成 ROOT / "{}"'
                        .format(name, index + 1, item))
            continue
        found.append(Path(os.path.realpath(str(path))))
    return found


def _text_value(reader, name):
    """取值必须是非空字符串；不做隐式 str()。

    把 [1, 2, 3] 转成 "[1, 2, 3]" 会得到一个看着像样的取值，而它永远匹配不上
    任何东西——使用者只会觉得"这功能没反应"。
    """
    value = reader.value(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        reader.fail(Code.CONFIG_BAD_VALUE,
                    "{} 必须是字符串，实际是 {}（写成 \"...\"）"
                    .format(name, type(value).__name__))
        return ""
    return value


def _string_list(reader, name):
    """一组字符串；写单个字符串按一项处理。

    接受 Path：配置约定就是"路径写成 ROOT / \"...\""，命令列表里出现 Path 是
    正常写法，不该被判成类型错误。逐项转成字符串（命令最终要以 argv 形式传给
    子进程）。
    """
    value = reader.value(name) or []
    if isinstance(value, (str, Path)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        reader.fail(Code.CONFIG_BAD_VALUE,
                    "{} 必须是列表，实际是 {}（写成 [\"...\"]）"
                    .format(name, type(value).__name__))
        return []
    found = []
    for index, item in enumerate(value):
        if isinstance(item, (str, Path)):
            found.append(str(item))
            continue
        reader.fail(Code.CONFIG_BAD_VALUE,
                    "{} 第 {} 项必须是字符串或路径，实际是 {}"
                    .format(name, index + 1, type(item).__name__))
    return found


def _mapping(reader, name):
    """一个字典；写成列表、字符串都不接受。

    dict() 对 [1, 2, 3] 会抛 ValueError，对 "ab" 会抛 ValueError——使用者拿到的
    是一段栈，而不是"哪一项、该写成什么样"。
    """
    value = reader.value(name)
    if value in (None, "", []):
        return {}
    if not isinstance(value, dict):
        reader.fail(Code.CONFIG_BAD_VALUE,
                    "{} 必须是字典，例如 {{\"s_\": \"g_\"}}，实际是 {}"
                    .format(name, type(value).__name__))
        return {}
    return dict(value)


def _pair_list(reader, name):
    """一组两元组，例如前缀替换表 [(\"s_\", \"g_\")]。"""
    value = reader.value(name) or []
    if not isinstance(value, (list, tuple)):
        reader.fail(Code.CONFIG_BAD_VALUE,
                    "{} 必须是两元组列表，实际是 {}"
                    .format(name, type(value).__name__))
        return []
    found = []
    for index, item in enumerate(value):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            reader.fail(Code.CONFIG_BAD_VALUE,
                        "{} 第 {} 项必须是 (旧, 新) 两元组"
                        .format(name, index + 1))
            continue
        found.append(tuple(str(part) for part in item))
    return found


def _encoding(reader, name, fallback):
    """编码名之类的单值字符串，没配就给缺省。"""
    value = reader.value(name)
    if value in (None, ""):
        return fallback
    if not isinstance(value, str):
        reader.fail(Code.CONFIG_BAD_VALUE,
                    "{} 必须是字符串，实际是 {}（写成 \"...\"）"
                    .format(name, type(value).__name__))
        return fallback
    return value


def _encoding_list(reader, name):
    """一组字符串；写单个字符串按一项处理，逐项要求是字符串。"""
    value = reader.value(name) or []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        reader.fail(Code.CONFIG_BAD_VALUE,
                    "{} 必须是字符串列表，实际是 {}"
                    .format(name, type(value).__name__))
        return []
    found = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            reader.fail(Code.CONFIG_BAD_VALUE,
                        "{} 第 {} 项必须是字符串，实际是 {}"
                        .format(name, index + 1, type(item).__name__))
            continue
        found.append(item)
    return found


def _text_list(reader, name):
    """读一组字符串；写单个字符串按一项处理，类型不对就当场说清。"""
    value = reader.value(name) or []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        reader.fail(Code.CONFIG_BAD_VALUE,
                    "{} 必须是字符串列表，例如 [\"*.py\"]，实际是 {}"
                    .format(name, type(value).__name__))
        return []
    return [str(item) for item in value]


def _flag(reader, name):
    value = reader.value(name)
    if not isinstance(value, bool):
        reader.fail(Code.CONFIG_BAD_VALUE, f"{name} 必须是 True 或 False")
        return bool(DEFAULTS.get(name))
    return value


def _choice(reader, name, allowed):
    value = reader.value(name)
    if value not in allowed:
        reader.fail(Code.CONFIG_BAD_VALUE,
                    f"{name} 只能是 {'、'.join(allowed)}，实际为 {value!r}")
        return allowed[0]
    return value


def _optional_int(reader, name):
    value = reader.value(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        reader.fail(Code.CONFIG_BAD_VALUE, f"{name} 必须是整数")
        return None
    return value


def _count(reader, name):
    value = reader.value(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        reader.fail(Code.CONFIG_BAD_VALUE, f"{name} 必须是 0 或更大的整数")
        return 0
    return value


def _build_file(reader, name, tool):
    """构建文件必须是绝对路径，且文件名要与构建工具匹配。"""
    path = _absolute(reader, name, required=True)
    if path is None:
        return None
    if tool == "cmake":
        matched, expected = path.name == "CMakeLists.txt", "CMakeLists.txt"
    else:
        matched, expected = path.name.endswith(".pro"), ".pro 文件"
    if not matched:
        reader.fail(Code.CONFIG_BAD_VALUE,
                    f"{name} 与 BUILD_TOOL={tool} 不匹配，应为 {expected}")
    return path


def check_contract(module):
    """比对配置声明的契约版本与本工具实现，返回 (错误码, 说明)。

    配置没写、或写的不是整数（视为没写）时静默通过，取工具缺省；写了就必须与
    工具一致——两侧取自不同代时，跨项目的文件与命令行契约未必还对得上，与其
    猜怎么兼容，不如当场拒绝并让人对齐到同一份 CONTRACT.md。
    """
    declared = getattr(module, "CONTRACT_VERSION", None)
    if not isinstance(declared, int) or isinstance(declared, bool):
        return Code.OK, ""
    if declared == CONTRACT_VERSION:
        return Code.OK, ""
    return Code.CONTRACT_MISMATCH, (
        f"配置声明的契约版本为 {declared}，本工具实现的是 {CONTRACT_VERSION}："
        f"配置/工具取自不同代，请对齐到同一份 CONTRACT.md")


def load_config(path=None):
    """加载并校验配置，返回 (配置, 错误码, 说明)。"""
    target = Path(path) if path else Path(
        os.environ.get(CONFIG_ENV) or CONFIG_FILE)
    module, code, message = _load_module(target)
    if code != Code.OK:
        return None, code, message
    reader = Reader(module, target)

    root = _absolute(reader, "ROOT", required=True)
    if root is None:
        return None, reader.code, reader.message
    if not root.is_dir():
        return None, Code.CONFIG_ROOT_MISSING, f"项目根不存在: {root}"

    for name in REQUIRED:
        if not reader.has(name):
            return None, Code.CONFIG_MISSING_ITEM, f"配置缺少必需项: {name}"

    code, message = check_contract(module)
    if code != Code.OK:
        return None, code, message

    build_tool = _choice(reader, "BUILD_TOOL", BUILD_TOOLS)
    config = Config(
        root=root,
        build_tool=build_tool,
        build_dir=(_absolute(reader, "BUILD_DIR", required=False)
                   or root / DEFAULT_BUILD_DIR),
        cxx_standard=str(reader.value("CXX_STANDARD") or ""),
        clang_format_config=_absolute(reader, "CLANG_FORMAT_CONFIG",
                                      required=True),
        clang_tidy_config=_absolute(reader, "CLANG_TIDY_CONFIG", required=True),
        clang_major=_optional_int(reader, "CLANG_MAJOR"),
        clang_tidy_extra_args=_text_list(reader, "CLANG_TIDY_EXTRA_ARGS"),
        whitelist=_path_list(reader, "WHITELIST"),
        blacklist=_path_list(reader, "BLACKLIST"),
        format_comments=_choice(reader, "FORMAT_COMMENTS", COMMENT_MODES),
        tidy_prepare=_flag(reader, "TIDY_PREPARE"),
        incremental=_flag(reader, "INCREMENTAL"),
        incremental_includes=_flag(reader, "INCREMENTAL_INCLUDES"),
        xfail_file=_absolute(reader, "XFAIL_FILE"),
        xfail_strict=_flag(reader, "XFAIL_STRICT"),
        log_dir=_absolute(reader, "CI_LOG_DIR"),
        jobs=_count(reader, "JOBS"),
        verify_command=_verify_command(reader),
        build_command=_string_list(reader, "BUILD_COMMAND"),
        clang_format_bin=_encoding(reader, "CLANG_FORMAT_BIN", ""),
        clang_tidy_bin=_encoding(reader, "CLANG_TIDY_BIN", ""),
        source_encoding=_encoding(reader, "SOURCE_ENCODING", "utf-8"),
        clang_prefer=_encoding(reader, "CLANG_PREFER", "wheel"),
        source_encoding_candidates=[
            str(item) for item in
            _encoding_list(reader, "SOURCE_ENCODING_CANDIDATES")],
        frozen=_path_list(reader, "FROZEN"),
        tidy_header_filter=_text_value(reader, "TIDY_HEADER_FILTER"),
        naming_prefix=_mapping(reader, "NAMING_PREFIX"),
        naming_local_ban=_encoding(reader, "NAMING_LOCAL_BAN", "gmstv"),
        naming_exempt_keys=_string_list(reader, "NAMING_EXEMPT_KEYS"),
        naming_premise_true=_string_list(reader, "NAMING_PREMISE_TRUE"),
        naming_fix_prefix_swaps=_pair_list(reader,
                                           "NAMING_FIX_PREFIX_SWAPS"),
        audit_files_globs=_text_list(reader, "AUDIT_FILES_GLOBS"),
        declared_keys=frozenset(vars(module)),
    )
    config.scope_roots, config.scope_excluded = _scope(reader, config)
    if not config.whitelist:
        config.whitelist = list(config.scope_roots)
    if not config.blacklist:
        config.blacklist = list(config.scope_excluded)
    if config.log_dir is None:
        config.log_dir = config.build_dir / "ci-logs"
    if config.xfail_file is None:
        config.xfail_file = config.root / "standardize.xfail"
    # 非致命提示随配置带出去，由入口打印一次：note() 以前只有写入端，没有读取端，
    # 加进去的话会被静默丢掉
    config.notes = list(reader.notes)
    # 失败检查放在**所有**读取之后：放在中间时，它后面的校验（作用域那几项就是）
    # 记下的失败没人再看，配置写错却一路跑到缺省值上
    if reader.code != Code.OK:
        return None, reader.code, reader.message
    return config, Code.OK, ""


def _scope(reader, config):
    """项目范围与排除范围。

    SCOPE 写成 {"project": [...], "excluded": [...]}；缺省用项目根与构建目录，
    后者必须排除，否则构建产物里的代码会被当成项目自己的。
    """
    raw = reader.value("SCOPE") or {}
    if not isinstance(raw, dict):
        reader.fail(Code.CONFIG_BAD_VALUE, "SCOPE 必须是字典")
        return [config.root], [config.build_dir]
    roots = [Path(item) for item in raw.get("project") or []]
    excluded = [Path(item) for item in raw.get("excluded") or []]
    if not roots:
        roots = [config.root]
    if not excluded:
        excluded = config.build_dirs
    return roots, excluded


def _verify_command(reader):
    """应用修复后用来验证构建的指令。

    留空表示不验证：风格修复（尤其是改名）可能只改声明处而让别的文件编译
    不过，不验证就等于把风险直接交给使用者，因此留空时上层会明确提示。
    """
    value = reader.value("VERIFY_COMMAND") or []
    if isinstance(value, str):
        reader.fail(Code.CONFIG_BAD_VALUE,
                    "VERIFY_COMMAND 必须是列表，如 [\"make\", \"-C\", BUILD_DIR]")
        return []
    return [str(item) for item in value]


