"""路径包含谓词：本仓库（standardize 侧）唯一实现。

与 `ci/lib/paths.py` 是同名同实现的**平行副本**，两侧互不 import（契约：
两个项目只靠文件与命令行协作）。改动这里时必须同步改另一侧，对照测试见
`tools/parity/`。

同一个"路径是否在某目录内"的谓词如果各处分开写、归一化方式再不一致，同一份
诊断会在编译、运行、内存检查三个阶段归因相反。因此只保留这两个具名入口。
"""

import os


def is_within(path, root, strict=False):
    """路径是否位于 root 之内（含边界判断）。**本仓库唯一的包含谓词**。

    默认对两边取 ``os.path.realpath``——即解软链接，与文件系统真实行为一致。
    共享目录、``/home`` 挂到别处、ccache/distcc 布局、把第三方库软链进工程时，
    "这个文件到底在不在构建树/项目里"只有解链后的答案是对的。

    strict 为真时与 root 相同不算在内。

    用 os.path.realpath 而非 Path.resolve：后者在路径不存在时会抛异常，
    而候选路径不存在正是常态。

    需要"不碰文件系统"的逻辑比较（路径尚不存在）时用 is_within_logical。
    """
    target = os.path.realpath(path)
    base = os.path.realpath(root)
    if strict and target == base:
        return False
    return target == base or target.startswith(base + os.sep)


def is_within_logical(path, root, strict=False):
    """逻辑包含：只做 abspath 归一化，**不解软链接、不碰文件系统**。

    仅用于"路径还不存在、不能拿它去解析软链"的场合。其余场合一律用
    is_within——两套归一化混用正是要收口的问题，别在这里开新口子。

    strict 语义与 is_within 一致：为真时与 root 相同不算在内。
    """
    target = os.path.normpath(os.path.abspath(path))
    base = os.path.normpath(os.path.abspath(root))
    if strict and target == base:
        return False
    return target == base or target.startswith(base + os.sep)


def is_frozen(path, frozen):
    """路径是否落在禁改区（含禁改区自身）。

    与 ci 侧同名同实现。委托 is_within：包含判定只留一个实现。
    """
    return any(is_within(path, item) for item in frozen or ())


def scope_of(path, project_roots=(), excluded_roots=()):
    """把一个路径判成 project / excluded / external。

    与 ci 侧同名同实现。排除区优先于项目区；包含判断一律委托 is_within，
    自己只保留三态语义。诊断归因、处理范围、内存归因共用这一个解析器——
    每个环节各造一套过滤，口径就会不一致。
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
