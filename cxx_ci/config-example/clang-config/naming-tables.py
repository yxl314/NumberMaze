# 命名口径表（示例）：把这份片段粘进 config/standardize.py 的"命名口径"一节。
#
# 这张表是命名规范的**唯一事实源**：standardize 从这里现场推导 .clang-tidy 应为
# 何并逐项比对，偏离即红。所以改口径只改这里，不必同步两份文本；反过来，有人把
# .clang-tidy 里的 s_ 悄悄改回 g_，第一次 check 就会报出来。
#
# 与同目录 .clang-tidy 的 CheckOptions 一一对应，两者合起来才是一套完整规范。

# 每一项是"该 kind 必须带的前缀"。留空的 kind 表示不要求前缀（类型名、方法名
# 这类）。表要写全：已知前缀集合 = 这里出现过的全部前缀值，用来判断"名字开头
# 是不是我们的前缀"；少了 g_，g_digits 里的 g_ 就认不出来，也就剥不掉。
NAMING_PREFIX = {
    # 类成员 / 结构体字段：clang 无"结构体字段"kind，靠访问级别区分
    "PrivateMember": "m_", "ProtectedMember": "m_", "PublicMember": "t_",
    # 参数（含 lambda 形参）
    "Parameter": "v_",
    # 全局（普通 / const / 指针 / const 指针）
    "GlobalVariable": "g_", "GlobalConstant": "g_",
    "GlobalPointer": "g_", "GlobalConstantPointer": "g_",
    # 函数内 static（四类都要写，漏一类就有一类声明漏检）
    "StaticVariable": "s_", "StaticConstant": "s_",
    "StaticPointer": "s_", "StaticConstantPointer": "s_",
    # constexpr：clang 把全局与函数内归为同一个 kind
    "ConstexprVariable": "g_",
}

# 局部变量禁用前缀集：局部按规范是裸名，出现这些前缀就算违规（`_` 惯例除外）。
# 留空表示不检查。这个集合同时决定：
#   1. .clang-tidy 里四个局部 kind 的 IgnoredRegexp 长什么样（唯一写法由工具推导，
#      手改即红）；
#   2. 全大写的单字母局部名（循环里的 i/j/k）不会被误判。
NAMING_LOCAL_BAN = "gmstv"

# 宏整体豁免：这些 key 不得出现在 .clang-tidy 里。宏名与宏展开生成的名字天然带
# 各类前缀，不豁免就没有一条前缀规则能落地。
NAMING_EXEMPT_KEYS = ["MacroDefinitionCase"]

# 前提项：这些 key 必须为 true，局部规则才成立。
NAMING_PREMISE_TRUE = ["IgnoreMacros"]

# 前缀中间态交换表，形如 (("t_m_", "t_"),)。没有"m_ 迁到 t_"这类需求就留空。
NAMING_FIX_PREFIX_SWAPS = []
