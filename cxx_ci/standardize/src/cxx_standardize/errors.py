"""错误码。

沿用"返回值 + 错误码"的风格（不使用异常跨层传播）：调用方一眼能看出每个
失败分支，也不会因为漏 catch 而在某个角落崩掉。

对外的进程退出码只有三档（见 CONTRACT.md）：0 无待改、1 有待改或检查未通过、
2 工具或配置错误。内部编号比这细，出口处投影一次。
"""

from enum import IntEnum


class Exit(IntEnum):
    """进程退出码。"""

    OK = 0        # 无需改动
    PENDING = 1   # 有待改动或检查未通过
    ERROR = 2     # 工具或配置错误


class Code(IntEnum):
    """内部错误码，投影到 Exit。"""

    OK = 0

    # 有待改动
    STYLE_PENDING = 1
    FORMAT_PENDING = 2
    BASELINE_REJECTED = 3   # 注释优先的基线还不成立，需一次性切换

    # 检查未通过（改动无法自动完成）
    STYLE_FAILED = 10
    FORMAT_FAILED = 11
    FORMAT_UNSTABLE = 12    # 排版结果不幂等，拒绝应用
    FROZEN_TOUCHED = 13     # 修复落点落在禁改区，整体拒绝

    # 工具或配置错误
    CONFIG_NOT_FOUND = 20
    CONFIG_UNLOADABLE = 21
    CONFIG_MISSING_ITEM = 22
    CONFIG_BAD_VALUE = 23
    CONFIG_ROOT_MISSING = 24
    TOOL_MISSING = 25
    TOOL_TOO_OLD = 26
    PATH_MISSING = 27
    BASELINE_CHANGED = 28   # 指纹已变，需重新确认
    BASELINE_BAD_FILE = 29
    BASELINE_UNWRITABLE = 30
    XFAIL_BAD_FILE = 31
    XFAIL_STALE = 32
    DIRTY_TREE = 33
    NOT_A_REPOSITORY = 34
    GIT_UNAVAILABLE = 35
    DB_MISSING = 36
    # 编译数据库里记录的构建目录不存在：clang-tidy 会直接中止，属于环境问题
    DB_STALE_DIRECTORY = 37
    # BUILD_COMMAND 跑失败，编译数据库因此仍不可用
    BUILD_COMMAND_FAILED = 38
    # 注释还原后行注释后面还挂着代码：产物可编译但语义已变
    COMMENT_RESTORE_FAILED = 39
    TIDY_FIXES_BAD_FILE = 37
    COMMAND_UNRUNNABLE = 38
    INTERRUPTED = 39
    AUDIT_DEAD_KEY = 40
    AUDIT_BYPASS_LITERAL = 41
    AUDIT_STD_DRIFT = 42
    # 命名口径偏离：口径表与 .clang-tidy 不一致。属于配置错误，投影成 2
    NAMING_META_DRIFT = 43
    # HeaderFilter 写得太宽，会把系统头文件放进来
    HEADER_FILTER_TOO_BROAD = 44
    # 源码编码判定不出：文件只读，诊断照报但不改写（"有文件未完成"，非失败）
    ENCODING_UNKNOWN = 45
    # 按原编码写不回（出现了原编码表示不了的字符），同样只报不改
    ENCODING_UNWRITABLE = 46
    # 源码里出现绕过 source.py 的裸写文本调用
    AUDIT_RAW_IO = 47
    # 源码里又自行拼前缀做路径包含判断，应改用 paths.is_within
    AUDIT_PREFIX_DRIFT = 48
    # 配置里写了本工具不认识的键（多半是错字，或被重命名的旧项）
    AUDIT_UNKNOWN_KEY = 49
    # 同一个工程上已经有另一份工具在跑（互斥锁没拿到）
    ENV_BUSY = 50
    # 配置声明的契约版本与本工具实现的不一致：两侧取自不同代，拒绝执行
    CONTRACT_MISMATCH = 49


def exit_code(code):
    """把内部错误码投影成进程退出码。"""
    if code == Code.OK:
        return Exit.OK
    if code in (Code.STYLE_PENDING, Code.FORMAT_PENDING,
                Code.BASELINE_REJECTED, Code.STYLE_FAILED,
                Code.FORMAT_FAILED, Code.FORMAT_UNSTABLE, Code.XFAIL_STALE,
                Code.FROZEN_TOUCHED, Code.ENCODING_UNKNOWN,
                Code.ENCODING_UNWRITABLE):
        return Exit.PENDING
    if code == Code.INTERRUPTED:
        return Exit.ERROR
    return Exit.ERROR
