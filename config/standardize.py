"""数字迷宫项目的风格与排版配置。

与 config/ci.py 保持同一套范围口径：管到本项目自己的代码，
嵌入的两个外来库与工具自身都排除在外。
"""

from pathlib import Path

CONTRACT_VERSION = 3

ROOT = Path(__file__).resolve().parents[1]

# ---- 工程

BUILD_TOOL = "cmake"
BUILD_DIR = ROOT / "build"
CXX_STANDARD = "c++17"

CLANG_FORMAT_CONFIG = ROOT / ".clang-format"
CLANG_TIDY_CONFIG = ROOT / ".clang-tidy"

# 钉定 clang 主版本：本机同时装着系统包与 pip wheel，
# 版本不同排版结果不保证一致，因此不让它按 PATH 自行挑选。
CLANG_MAJOR = 21
CLANG_PREFER = "system"


# ---- 范围与禁改区

SCOPE = {
    "project": [
        ROOT / "Source" / "NumberNaze",
    ],
    "excluded": [
        ROOT / "build",
        ROOT / "docs",
        ROOT / "cxx_ci",
        ROOT / "Source" / "singleproc",
        ROOT / "Source" / "stacklog",
    ],
}

# 外来库与工具目录不许就地改写，否则升级版本时会与上游产生无谓差异。
FROZEN = [
    ROOT / "Source" / "singleproc",
    ROOT / "Source" / "stacklog",
    ROOT / "cxx_ci",
]

# ---------------------------------------------------------------- 代码守卫
BOUNDARY_DIRS = [ROOT / "Source" / "NumberNaze"]
DETAIL_DIRS = []
DETAIL_MARK = "detail::"


# ---- 编译数据库与分析

# 编译数据库由构建产出：构建树被清理过时库里的 directory 会指向不存在的
# 路径，clang-tidy 碰到这种条目会直接中止，所以先构建一次。
# 构建树可能被清理过（CI 的 clean 阶段就会清），此时单跑 cmake --build 会因
# 缺少 CMakeCache.txt 直接失败，编译数据库也就无从产出。因此走一个先配置
# 再构建的包装脚本，缺构建树时它自己会补上配置这一步。
BUILD_COMMAND = [str(ROOT / "tools" / "build.sh")]

# 应用修复后验证构建没被破坏——改名类修复可能只改了声明处。
VERIFY_COMMAND = [str(ROOT / "tools" / "build.sh")]

# 只对本项目自己的头文件出诊断。不设限的话，嵌入的两个外来库会被一并扫描，
# 报出的是它们的既有问题——那些不该由本项目负责，更不该被 --apply 改写。
TIDY_HEADER_FILTER = r".*Source/NumberNaze/.*"

# 本项目使用 Qt：moc 生成的派生文件缺了会让含 #include "X.moc" 的
# 翻译单元被判成编译错误，其 fix-it 也一并被抑制。
TIDY_PREPARE = True


# ---- 命名口径

# 前缀口径表。工具的元检查据此现场推导 .clang-tidy 应为何，偏离即红；
# 列表中每个 kind 的大小写由工具统一按 lower_case 要求。
#
# 方法与成员刻意不列入：它们是 camelCase（setBoard、boardChanged、m_undoAction），
# 与 Qt 自身惯例一致，项目 API 与 Qt 基类签名同一风格。工具对表内 kind 一律
# 要求 lower_case，列进来就会与 .clang-tidy 冲突，因此这些 kind 的形态交给
# .clang-tidy 自行声明，本表只管确实采用 lower_case 的那些。
NAMING_PREFIX = {
    "GlobalVariable": "g_",
    "GlobalConstant": "g_",
    "StaticVariable": "s_",
    "StaticConstant": "s_",
    "ConstexprVariable": "g_",
    "LocalVariable": "_",
    "LocalConstant": "_",
    "Parameter": "v_",
}

# 前缀中间态交换表：无 m_ 迁到 t_ 这类需求，留空。
NAMING_FIX_PREFIX_SWAPS = []


# ---- 已登记问题

# 当前无存量问题，留空；清单文件不存在即等于没有。
XFAIL_FILE = None

CI_LOG_DIR = ROOT / "build" / "ci-logs"
