#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路径解析、包含判断与文件收集。"""
import sys
if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")
import os
from pathlib import Path


COMPILE_DATABASE = "compile_commands.json"
HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx", ".h++", ".inl", ".ipp")

SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".c++", ".cu", ".cuh")

CXX_SUFFIXES = SOURCE_SUFFIXES + HEADER_SUFFIXES

def resolve_path(value, root):
    """把路径解析为绝对路径，相对路径以给定的项目根为基准。"""
    path = Path(value)
    return str(path if path.is_absolute() else Path(root) / path)

def resolve_paths(value, root):
    """把逗号分隔的路径解析为绝对路径列表。"""
    result = []
    for item in value.split(","):
        item = item.strip()
        if item:
            result.append(resolve_path(item, root))
    return result

def is_inside_frozen(path, frozen):
    """路径**落在**禁改区内（含其自身）。单向判定。

    用于"构建目录不能与禁改区重叠"这类配置校验：只有 BUILD_DIR 在禁改区里面才
    是配置错，禁改区在 BUILD_DIR 里面（vendored 依赖就地构建）是正常形态。

    委托给 is_within：包含判定只留一个实现，别处不再各写一遍前缀比较。
    """
    return any(is_within(path, item) for item in frozen or ())


def is_frozen(path, frozen):
    """路径是否与禁改区重叠（在禁改区内、或本身就是禁改区的上层）。

    禁改区是两侧共用的契约项（见 CONTRACT.md）：standardize 侧靠它守"绝不可写"，
    ci 侧靠它守"绝不可删"。判定只有这一处，别处不再各自拼前缀匹配——
    两处判定不一致，红线就会出现缺口。

    两个方向都要看：删除是按目录树自顶向下走的，`build/third_party` 这个上层
    目录本身不在禁改区内，但删掉它就把里面的冻结产物一起删了。只看"在禁改区
    内"会让红线在删除场景下形同虚设。
    """
    return any(is_within(path, item) or is_within(item, path)
               for item in frozen or ())


def is_within(path, root, strict=False):
    """路径是否位于 root 之内（含边界判断）。**本仓库唯一的包含谓词**。

    归一化策略写在 docstring 里，全仓一致：默认对两边取 ``os.path.realpath``
    ——即解软链接，与文件系统真实行为一致。共享目录、``/home`` 挂到别处、
    ccache/distcc 布局、把第三方库软链进工程时，"这个文件到底在不在构建树/
    项目里"只有解链后的答案是对的。各处分别用 realpath 与 abspath 会让同一份
    诊断在编译、测试、内存检查三个阶段归因相反——那种"每处单看都对、合起来
    矛盾"的问题排查成本极高。因此：一切与真实文件系统相关的判断都走这里。

    strict 为真时与 root 相同不算在内。

    用 os.path.realpath 而非 Path.resolve：后者在 Python 3.4 上遇到不存在的
    路径会抛异常，而构建目录不存在正是常态。

    需要"不碰文件系统"的逻辑比较（路径尚不存在）时用 is_within_logical。
    """
    target = os.path.realpath(path)
    base = os.path.realpath(root)
    if strict and target == base:
        return False
    return target == base or target.startswith(base + os.sep)


def is_within_logical(path, root, strict=False):
    """逻辑包含：只做 abspath 归一化，**不解软链接、不碰文件系统**。

    仅用于"路径还不存在、不能拿它去解析软链"的场合（例如把编译器给的相对路径
    拼出来的候选绝对路径与项目根比较）。其余场合一律用 is_within——两套归一化
    混用正是 F1 要收口的问题，别在这里开新口子。

    strict 语义与 is_within 一致：为真时与 root 相同不算在内。
    """
    target = os.path.normpath(os.path.abspath(path))
    base = os.path.normpath(os.path.abspath(root))
    if strict and target == base:
        return False
    return target == base or target.startswith(base + os.sep)


def collect_files(paths, excludes, suffixes=CXX_SUFFIXES):
    """收集待处理文件，排除黑名单路径。"""
    found = set()
    for entry in paths:
        if os.path.isfile(entry):
            found.add(entry)
            continue
        for root, _, names in os.walk(entry):
            for name in names:
                if name.endswith(suffixes):
                    found.add(os.path.join(root, name))
    kept = [path for path in found
            if not any(is_within(path, item) for item in excludes)]
    # 源文件排在头文件之前：clang-tidy 的改名修复要在同一个翻译单元里
    # 同时改声明与引用，先改头文件会让源文件里的引用变成编译错误而拒绝修复。
    return sorted(kept, key=lambda path: (path.endswith(HEADER_SUFFIXES), path))


def scope_of(path, project_roots=(), excluded_roots=()):
    """判定路径的归属：excluded / project / external。

    诊断展示、style 与 format 的处理范围、内存归因共用这一个解析器，
    避免每个阶段各造一套过滤而口径不一致。排除区优先于项目区。

    包含判断一律委托 is_within，自己只保留"排除区优先于项目区"的三态语义：
    另写一遍前缀比较就会与 is_within 的 realpath 口径相反，同一份诊断在编译、
    运行、内存检查三个阶段被归成不同归属。
    """
    for root in excluded_roots:
        if root and is_within(path, root):
            return "excluded"
    for root in project_roots:
        if root and is_within(path, root):
            return "project"
    return "external"


def scope_roots(config):
    """从配置取出项目区与排除区，兼容 SCOPE 与旧的 WHITELIST/BLACKLIST。"""
    scope = getattr(config, "scope", None)
    if scope:
        return (list(scope.get("project") or []),
                list(scope.get("excluded") or []))
    return (list(getattr(config, "whitelist", None) or []),
            list(getattr(config, "blacklist", None) or []))

def write_blocked(config, path):
    """写这个路径是否越界，返回 (是否阻止, 原因)。

    两种越界都要挡：①落在禁改区里；②**真实落点**跑到工程范围之外。

    第二种以前没人管：工程内的软链指向工程外时，按链接路径看它在范围里，而
    实际被改写的是外面那个文件——事后从输出里也看不出改的是谁（打印的是链接
    路径）。因此判定必须解链后再比，而不是比字符串前缀。
    """
    frozen = getattr(config, "frozen", None) or ()
    if is_frozen(path, frozen):
        return True, "禁改区"
    project, excluded = scope_roots(config)
    if not project:
        return False, ""
    if not any(is_within(path, root) for root in project):
        # is_within 内部取 realpath：写穿软链时这里看的就是真实落点
        return True, "工程范围之外"
    if any(is_within(path, root) for root in excluded):
        return True, "排除区"
    return False, ""
