# 配置声明的契约版本必须与工具实现一致；两侧取自不同代会被拒绝
CONTRACT_VERSION = 3

from pathlib import Path

ROOT = Path("/home/yxl/桌面/code/singleproc")

# ---------------------------------------------------------------- 构建
BUILD_TOOL = "cmake"
BUILD_FILE = ROOT / "CMakeLists.txt"
BUILD_DIR = ROOT / "build"
CXX_COMPILER = "g++"
CXX_STANDARD = "c++11"

# ---------------------------------------------------------------- 测试
TEST_BUILD_FILE = ROOT / "CMakeLists.txt"
TEST_BUILD_DIR = ROOT / "build"
TEST_COMMAND = ["ctest", "--output-on-failure"]
TEST_WORKDIR = TEST_BUILD_DIR
TEST_REPORT_ADAPTER = "gtest"
TEST_FAIL_PATTERN = ""
TEST_TOTAL_PATTERN = ""

# ---------------------------------------------------------------- 内存检查
MEMCHECK_COMMAND = [TEST_BUILD_DIR / "tests" / "singleproc_test_guard"]
MEMCHECK_ENV = {}
MEMCHECK_SUPPRESSIONS = ROOT / "config" / "valgrind.supp"
MEMCHECK_SKIP_TARGETS = []
MEMCHECK_IGNORE_OBJECTS = []
MEMCHECK_REPEAT = 1
MEMCHECK_TOOL = "valgrind"
ASAN_CXXFLAGS = ["-fsanitize=address", "-fno-omit-frame-pointer"]
ASAN_LDFLAGS = ["-fsanitize=address"]
ASAN_LEAK_ARGS = []
ASAN_SUPPRESSIONS = ROOT / "config" / "lsan.supp"
ASAN_ERROR_MARKS = ["ERROR: AddressSanitizer", "ERROR: LeakSanitizer",
                    "SUMMARY: AddressSanitizer", "SUMMARY: LeakSanitizer",
                    "Direct leak", "Indirect leak", "heap-use-after-free",
                    "heap-buffer-overflow", "double-free",
                    "SEGV on unknown address"]

# ---------------------------------------------------------------- 内部面边界
BOUNDARY_DIRS = [ROOT / "src", ROOT / "include"]
DETAIL_DIRS = [ROOT / "src" / "platform"]
DETAIL_MARK = "detail::"
GUARD_EXTS = [".h", ".hh", ".hpp", ".hxx"]

# ---------------------------------------------------------------- 配置审计
AUDIT_FILES_GLOBS = []
STD_VARS = ["CMAKE_CXX_STANDARD"]
STD_PIN = "11"

# ---------------------------------------------------------------- 标准化
CLANG_FORMAT_CONFIG = ROOT / ".clang-format"
CLANG_TIDY_CONFIG = ROOT / ".clang-tidy"
CLANG_TIDY_EXTRA_ARGS = []

# ---------------------------------------------------------------- 诊断与范围
# 禁改区（vendored 冻结库）：绝不可写、也不可删。与 standardize 侧同名同语义
FROZEN = []
SCOPE = {"project": [ROOT / "src", ROOT / "include", ROOT / "tests", ROOT / "examples"],
         "excluded": [ROOT / "build", ROOT / "docs", ROOT / "cxx_ci"]}
DIAGNOSTIC_LEVEL = "error"
DIAGNOSTIC_SOURCES = "project"
DIAGNOSTIC_IGNORE = []
DIAGNOSTIC_DEDUP = True
CI_LOG_DIR = ROOT / ".cxx_ci" / "logs"
STEPS_DEFAULT = None
XFAIL_FILE = ROOT / "ci.xfail"
XFAIL_STRICT = True
