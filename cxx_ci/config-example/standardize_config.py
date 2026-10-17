# 复制成工程自己的配置后按需修改（cp config-example/standardize_config.py config/standardize.py）。
# 路径一律写成 ROOT / "..."（必须是绝对路径）。
#
# 本项目的配置与门禁项目 ci 各有一份：公共字段（项目根、构建目录）在两处各写
# 一遍，语义由仓库根的 CONTRACT.md 约定。两侧不共享代码，因此这里不用去
# import 另一边的配置。

from pathlib import Path

# 跨项目契约版本，与 ci 侧、与本工具实现都须一致；不一致时拒绝执行（退出码 2）。
# 不写则取工具缺省并静默通过。
CONTRACT_VERSION = 3

# 被处理的项目根
ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------- 构建上下文
# 本包不跑构建，只读已有的编译数据库：要么项目自己的构建目录里已有，要么
# 由 provider 从构建文件现推一份。因此这里只需要"去哪儿找"
BUILD_TOOL = "cmake"
BUILD_DIR = ROOT / "build"
# 目标语言标准，编译标准锁定检查按它比对
CXX_STANDARD = "c++11"

# ---------------------------------------------------------------- clang 工具
# 两个文件必须真实存在：配置里点了名却找不到时会直接失败，不会退回各自的
# "向上查找"——那会让实际生效的配置随运行目录变化，也进不了基线指纹。
CLANG_FORMAT_CONFIG = ROOT / ".clang-format"
CLANG_TIDY_CONFIG = ROOT / ".clang-tidy"
# 钉定 clang 主版本。多版本共存时取到哪个只看 PATH，而结果随版本变化
CLANG_MAJOR = 17
# 额外传给 clang-tidy 的编译参数，逐个作为 --extra-arg-before 传入。
# 用于屏蔽第三方头文件在较新 clang 上的已知误报。
CLANG_TIDY_EXTRA_ARGS = []

# ---------------------------------------------------------------- 禁改区
# 绝不可写的路径：vendored 冻结库。与 BLACKLIST 分列——黑名单是"不必分析"的
# 宽容语义，这里是"绝不可写"的红线语义。--apply 会按替换块报出的落点检查，
# 并做写前快照与写后比对（越界写入会被还原）。
FROZEN = []

# ---------------------------------------------------------------- 处理范围
# 白名单与黑名单；黑名单优先。缺省取 SCOPE 里的 project / excluded。
WHITELIST = []
BLACKLIST = []
SCOPE = {
    "project": [ROOT / "src", ROOT / "include", ROOT / "tests"],
    "excluded": [ROOT / "build", ROOT / "build-tests", ROOT / "third_party"],
}

# ---------------------------------------------------------------- 排版
# 注释保护模式：
#   protect   先摘掉注释排代码，再按代码行回填，注释文本与间距原样保留（推荐）
#   none      完全信任 .clang-format
# 旧名 protect-column / placeholder 仍被接受，行为与 protect 相同
FORMAT_COMMENTS = "protect"

# ---------------------------------------------------------------- 风格

# pip wheel 与系统 clang 的取舍：wheel（缺省，装了对应包就用它）/ system
# （只认 PATH 上的，忽略 wheel）/ error（两者都在时直接报错，逼人明确挑一个）。
# 团队里装了 wheel 的机器与没装的结果不同，主版本号相同也不代表来源相同。
CLANG_PREFER = "wheel"

# 显式指定 clang 可执行文件。写了就必须存在且主版本达下限，找不到直接报错并
# 列出候选，**不退回 PATH 搜索**：主版本相同不代表来源相同（系统包与 pip wheel
# 都报 17.0.6），排版结果不保证一致。工具解析到的真实路径会进基线指纹，因此
# 换来源会被基线挡下并要求重新确认。留空才按 CLANG_MAJOR 在 PATH 上找。
CLANG_FORMAT_BIN = ""
CLANG_TIDY_BIN = ""

# -------------------------------------------------------------- 源码编码（source）

# 改写源码时的编码判定。缺省 "utf-8" 表示"只认 UTF-8（含 BOM）"；判定不出的
# 文件一律**只读**——诊断照报、改写跳过并说明，绝不被静默写成 U+FFFD。
# 老编码工程（GBK/GB18030 存量代码）显式写编码名（如 "gb18030"）按它严格解；
# 写 "auto" 则改用下面的候选列表逐个严格试。
SOURCE_ENCODING = "utf-8"

# 候选编码列表，缺省为空：**不猜 GBK**。只有 SOURCE_ENCODING="auto" 时才启用。
# 猜错比跳过危险得多——跳过只是不排版，猜错是把用户的中文注释永久写坏。
SOURCE_ENCODING_CANDIDATES = []

# 强制注入的 header-filter。命令行优先于项目 .clang-tidy 的 HeaderFilterRegex：
# 不强制的话，把它改宽会让外部头噪音涌入（--apply 还可能写到那里），改窄会把
# 本仓头文件的诊断静默藏掉。填的是"本仓头文件"的正则，例如
# r"include/<工程名>/.*"；留空表示不注入，由项目 .clang-tidy 自己说了算。
TIDY_HEADER_FILTER = ""

# ---------------------------------------------------------------- 代码守卫
# 受管源目录：其下检查头文件宏守卫与 detail:: 归属。
BOUNDARY_DIRS = [ROOT / "src", ROOT / "include"]
# detail:: 是内部实现层标记：允许出现在这些目录里，别处出现即越界。
DETAIL_DIRS = [ROOT / "src"]
DETAIL_MARK = "detail::"
# 需要检查宏守卫的头文件扩展名。
GUARD_EXTS = [".h", ".hh", ".hpp", ".hxx"]

# 分析前先生成 moc_/ui_/qrc_ 等派生文件。缺了它们，含 #include "X.moc" 的
# 翻译单元会被判成编译错误，该单元的 fix-it 也一并被抑制。
TIDY_PREPARE = True

# 产出编译数据库的指令。编译数据库是构建的产物：构建树被清理过、或只跑过
# 部分构建时，库里的 directory 会指向不存在的路径，clang-tidy 碰到这种条目会
# **直接中止**，一个翻译单元都分析不了。配了这条，风格检查会自己先构建一次
# 再分析（例如跑 ci 的 build 与 build_test）。留空则维持报错并说明。
BUILD_COMMAND = []

# 应用风格修复后用它验证改动没有破坏构建。改名类修复可能只改声明处而让别的
# 文件编译不过，留空则跳过验证（会明确提示）。
# 只想做门禁、不应用修复的项目可以留空。
VERIFY_COMMAND = ["python3", str(ROOT / ".." / "ci" / "ci.py"), "--only", "build"]

# ---------------------------------------------------------------- 命名口径
# 口径表决定前缀规范化（--apply 时自动改名）：名字上挂着别的前缀时，换成这里
# 要求的前缀。
#
# 表里每一项是"该 kind 必须带的前缀"。留空的 kind 表示"不要求前缀"（类型名、
# 方法名这类）。已知前缀集合 = 这里出现过的全部前缀值，用来判断"名字开头是不是
# 我们的前缀"，因此表要写全——少了 g_，g_digits 里的 g_ 就认不出来，也就剥不掉。
NAMING_PREFIX = {
    "PrivateMember": "m_", "ProtectedMember": "m_", "PublicMember": "t_",
    "Parameter": "v_",
    "GlobalVariable": "g_", "GlobalConstant": "g_",
    "GlobalPointer": "g_", "GlobalConstantPointer": "g_",
    "StaticVariable": "s_", "StaticConstant": "s_",
    "StaticPointer": "s_", "StaticConstantPointer": "s_",
    "ConstexprVariable": "g_",
}
# 前缀中间态交换表，形如 (("t_m_", "t_"),)。没有"m_ 迁到 t_"这类需求就留空。
NAMING_FIX_PREFIX_SWAPS = []

# ---------------------------------------------------------------- 登记清单
# 风格侧的登记清单，只登记 [style] 条目。门禁侧另有一份 ci.xfail，互不读取。
XFAIL_FILE = None          # 缺省 ROOT / "standardize.xfail"
XFAIL_STRICT = False       # True 时"已修复未清理"直接判失败，防止清单腐化

# ---------------------------------------------------------------- 输出
CI_LOG_DIR = None          # 缺省 BUILD_DIR / "ci-logs"
JOBS = 0                   # 0 表示按 CPU 核数
