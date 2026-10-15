"""数字迷宫项目的 CI 配置。

本文件按 cxx_ci 的 config.py.example 改写而成，只覆盖本项目的实际取值；
未在此列出的项一律取工具的缺省值。路径用 ROOT / "..." 书写，
与运行时的当前工作目录无关。
"""

from pathlib import Path

CONTRACT_VERSION = 3

# 被 CI 的项目根。本配置位于 ROOT/config/ci.py，故向上一级即工程根。
# 不沿用示例里的 TOOL_ROOT：那是工具自身的变量，本项目把 cxx_ci 放在
# 工程根之下，若照抄示例会算成工程根的上一级。
ROOT = Path(__file__).resolve().parents[1]

# ---- 工程

BUILD_TOOL = "cmake"

BUILD_FILE = ROOT / "CMakeLists.txt"
BUILD_DIR = ROOT / "build"

CXX_COMPILER = "g++"

# 主工程锁定 C++17；两个外来库各自在 CMakeLists 里声明 C++11，
# 不依赖全局标准，因此这里钉死 c++17 不会影响它们。
CXX_STANDARD = "c++17"


# ---- 测试

# 测试与主工程共用一份构建文件：本项目用 ctest 驱动 core-test 与 restart-test。
TEST_BUILD_FILE = ROOT / "CMakeLists.txt"
TEST_BUILD_DIR = ROOT / "build"

# ctest 必须在构建目录内运行，它靠 CTestTestfile.cmake 定位用例。
TEST_COMMAND = ["ctest", "--output-on-failure"]
TEST_WORKDIR = TEST_BUILD_DIR

# 内存检查直接跑被测程序本身，绕开 ctest 这层驱动：
# valgrind 若去检查 ctest，看到的只是驱动进程，被测代码的泄漏无从归因。
# 取 core-test：它是覆盖核心逻辑的主用例，且不依赖 Qt。
MEMCHECK_COMMAND = [BUILD_DIR / "core-test"]


# ---- 范围与禁改区

SCOPE = {
    "project": [
        ROOT / "Source" / "NumberNaze",
    ],
    "excluded": [
        ROOT / "build",
        ROOT / "docs",
        ROOT / "cxx_ci",
        # 两个外来库是嵌入式引入的第三方产物，不计入本项目的门禁范围。
        ROOT / "Source" / "singleproc",
        ROOT / "Source" / "stacklog",
    ],
}

# 外来库虽然被排除在门禁范围之外，但也不该被格式化工具就地改写，
# 否则升级库版本时会与上游产生无谓差异。
FROZEN = [
    ROOT / "Source" / "singleproc",
    ROOT / "Source" / "stacklog",
    ROOT / "cxx_ci",
]


# ---- 内部面边界（boundary）

# 受管源目录：检查 detail:: 归属与头文件宏守卫。
BOUNDARY_DIRS = [ROOT / "Source" / "NumberNaze"]


# ---- 内存检查

# 测试是纯逻辑用例（不含定时器与异步事件循环），结果稳定，无需重复取交集。
MEMCHECK_REPEAT = 1


# ---- 输出

# 成功阶段把告警也回显出来：本项目要求零告警，漏掉就失去意义。
DIAGNOSTIC_LEVEL = "warning"


# ---- 自动导出

# 标准化的头文件宏守卫等检查项对本项目的头文件同样适用。

# [常用] 已登记存量问题的清单：本项目当前无存量问题，留空。
XFAIL_FILE = None
