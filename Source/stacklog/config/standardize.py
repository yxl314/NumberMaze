from pathlib import Path

ROOT = Path("/home/yxl/桌面/code/stacklog")

CONTRACT_VERSION = 3
BUILD_TOOL = "cmake"
BUILD_DIR = ROOT / "build"
CXX_STANDARD = "c++11"
CLANG_FORMAT_CONFIG = ROOT / ".clang-format"
CLANG_TIDY_CONFIG = ROOT / ".clang-tidy"
CLANG_MAJOR = 17
CLANG_TIDY_EXTRA_ARGS = []
SCOPE = {"project": [ROOT / "src", ROOT / "include", ROOT / "tests", ROOT / "tool"],
         "excluded": [ROOT / "build", ROOT / "docs", ROOT / "cxx_ci"]}
FORMAT_COMMENTS = "protect-column"
BUILD_COMMAND = ["python3", ROOT / "cxx_ci" / "ci" / "ci.py",
                 "--only", "build,build_test", "--config",
                 ROOT / "config" / "ci.py"]
VERIFY_COMMAND = ["python3", ROOT / "cxx_ci" / "ci" / "ci.py", "--only", "build,build_test,run_test",
                  "--config", ROOT / "config" / "ci.py"]
TIDY_HEADER_FILTER = "include/stacklog/.*"
FROZEN = []
AUDIT_FILES_GLOBS = []
CI_LOG_DIR = ROOT / ".cxx_ci" / "logs"

# ---------------------------------------------------------------- 命名口径
# 口径表是唯一事实源：naming_meta 从这里现场推导 .clang-tidy 应为何，偏离即红。
# 改口径只改这张表，不必同步两份文本。
NAMING_PREFIX = {
    "PrivateMember": "m_", "ProtectedMember": "m_", "PublicMember": "t_",
    "Parameter": "v_",
    "GlobalVariable": "g_", "GlobalConstant": "g_",
    "GlobalPointer": "g_", "GlobalConstantPointer": "g_",
    "StaticVariable": "s_", "StaticConstant": "s_",
    "StaticPointer": "s_", "StaticConstantPointer": "s_",
    "ConstexprVariable": "g_",
}
# 局部变量禁用前缀集：工具拦跨类前缀误用，其余形态豁免
NAMING_LOCAL_BAN = "gmstv"
# 宏整体豁免：这些 key 不得出现在配置里（宏名与宏展开生成的名字天然带各类前缀）
NAMING_EXEMPT_KEYS = ["MacroDefinitionCase"]
# 前提项：IgnoreMacros 必须为 true，局部规则才成立
NAMING_PREMISE_TRUE = ["IgnoreMacros"]
# 前缀中间态交换表：没有 m_/g_ 迁到 t_/s_ 的需求，保持为空
NAMING_FIX_PREFIX_SWAPS = []
