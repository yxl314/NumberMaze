#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""错误码。

错误码是 16 位整数：高 8 位是错误类型，低 8 位是该类型内的具体错误编号。
进程退出状态只有 8 位，因此退出时用 status() 取错误类型，具体编号由日志
给出；跨进程传递的退出状态用 compose 还原成错误码。
"""

# ---------------------------------------------------------------- 错误类型

KIND_USAGE = 1        # 用法或配置有误
KIND_ENVIRONMENT = 2  # 缺少外部依赖
KIND_BUILD = 3        # 构建未通过
KIND_TEST = 4         # 测试未通过
KIND_MEMORY = 5       # 内存检查未通过
KIND_TIMEOUT = 8      # 阶段超时
KIND_INTERRUPTED = 9  # 被中断
KIND_SKIPPED = 10     # 阶段在当前机器上不可用（缺必需的外部工具）

OK = 0


def compose(kind, detail=0):
    """把错误类型与类型内编号拼成错误码。"""
    return ((kind & 0xFF) << 8) | (detail & 0xFF)


def kind(code):
    """取出错误类型。"""
    return (code >> 8) & 0xFF


def detail(code):
    """取出类型内的具体编号。"""
    return code & 0xFF


def from_status(value):
    """把一个进程退出状态折算回错误码。

    退出状态只有 8 位，而错误码是 16 位（高 8 位类型、低 8 位编号）。直接把
    裸 1/2 当错误码返回会被 status() 折成 0——"拒绝执行"于是变成"成功退出"，
    这是最坏的一种错：终端说不行，脚本看到的是 0。凡是要返回一个"退出状态"
    而不是错误码的地方，都必须过这里补上类型位。
    """
    if not value:
        return OK
    if value == 130:
        return compose(KIND_INTERRUPTED, 1)
    return compose(value & 0xFF, 0)


def status(code):
    """进程退出状态。

    取错误类型；被中断沿用 shell 惯例的 130。
    """
    if code == OK:
        return OK
    if kind(code) == KIND_INTERRUPTED:
        return 130
    return kind(code)


# ------------------------------------------------------------ 用法或配置

CONFIG_NOT_FOUND = compose(KIND_USAGE, 1)      # 配置文件不存在
CONFIG_UNLOADABLE = compose(KIND_USAGE, 2)     # 配置文件无法加载
CONFIG_MISSING_ITEM = compose(KIND_USAGE, 3)   # 配置缺少必需项
CONFIG_BAD_VALUE = compose(KIND_USAGE, 4)      # 配置取值非法
CONFIG_ROOT_MISSING = compose(KIND_USAGE, 5)   # 配置的项目根不存在
UNKNOWN_STAGE = compose(KIND_USAGE, 6)         # 未知的阶段名
BUILD_FILE_MISSING = compose(KIND_USAGE, 8)    # 构建文件不存在
UNSAFE_DELETE = compose(KIND_USAGE, 10)        # 拒绝删除项目外的路径
XFAIL_BAD_FILE = compose(KIND_USAGE, 13)       # xfail 清单语法错误
XFAIL_STALE = compose(KIND_USAGE, 14)          # xfail 登记项已修复或复核到期

# ---------------------------------------------------------------- 环境依赖

TOOL_MISSING = compose(KIND_ENVIRONMENT, 1)        # 找不到必需的外部工具
WORKDIR_MISSING = compose(KIND_ENVIRONMENT, 3)     # 测试工作目录不存在
COMMAND_UNRUNNABLE = compose(KIND_ENVIRONMENT, 4)  # 指令无法执行
ENV_BUSY = compose(KIND_ENVIRONMENT, 6)            # 同一工程上已有另一份在跑
# 配置声明的契约版本与本工具实现的不一致：两侧取自不同代，拒绝执行
# （放在环境类，使退出码为 2，与 standardize 侧的"工具或配置错误"对齐）
CONTRACT_MISMATCH = compose(KIND_ENVIRONMENT, 5)
# 单独一类而不是环境类的一档：跨进程只传得回类，上层靠它区分
# 跳过与环境错误导致的失败
STAGE_UNAVAILABLE = compose(KIND_SKIPPED, 1)  # 阶段在当前机器上不可用
ASAN_TOOL_MISSING = compose(KIND_SKIPPED, 2)  # 机器上没有 ASan 运行库

# ------------------------------------------------------------------ 构建

BUILD_CONFIGURE_FAILED = compose(KIND_BUILD, 1)  # 配置阶段失败
BUILD_COMPILE_FAILED = compose(KIND_BUILD, 2)    # 编译阶段失败
ASAN_BUILD_FAILED = compose(KIND_BUILD, 3)       # 带 ASan 的影子构建失败

# ------------------------------------------------------------------ 测试

TEST_FAILED = compose(KIND_TEST, 1)  # 测试未通过
TEST_REPORT_BAD_ADAPTER = compose(KIND_TEST, 2)   # 测试报告适配器名非法
TEST_REPORT_BAD_PATTERN = compose(KIND_TEST, 3)   # 测试报告正则非法或缺失

# -------------------------------------------------------------- 内存检查

MEMCHECK_FAILED = compose(KIND_MEMORY, 1)  # 域内存在内存问题
XML_UNREADABLE = compose(KIND_MEMORY, 2)   # valgrind 结构化报告读不到
XML_MALFORMED = compose(KIND_MEMORY, 3)    # valgrind 结构化报告版式损坏
ASAN_SUPPRESSIONS_MISSING = compose(KIND_MEMORY, 4)  # 泄漏抑制清单不存在

# ------------------------------------------------------------ 超时与中断

STAGE_TIMEOUT = compose(KIND_TIMEOUT, 1)      # 阶段执行超时
INTERRUPTED = compose(KIND_INTERRUPTED, 1)    # 被中断
