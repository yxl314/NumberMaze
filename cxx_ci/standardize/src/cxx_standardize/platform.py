#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""支持面：把"只在 UNIX 上跑"写成显式约束，而不是到处静默降级。

工具事实上依赖 POSIX 语义：内存检查只有 valgrind 与 ASan/LSan（GNU/Linux 专属，
ASan 运行库还靠 `<编译器> -print-file-name=libasan.so` 定位）、子进程与 `LC_ALL=C`
的输出前缀约定、路径与软链假定。这些原本一处都没写成约束，于是换个平台跑出来的
是"阶段一路 [SKIPPED]、内存检查静默不可用"这种半死不活的状态，而不是一句
"本平台不支持"。

与 `ci/lib/platform.py` 为**平行实现**（本侧不含 `memory_check_available` / `capability`：
standardize 不做内存检查，那两个函数只存在于真正用它们的一侧）：两个项目互不 import，只靠文件与命令行
契约协作。改动须同步两侧并跑 `tests/test_platform_parity.py` 的对照测试。

与其假装可移植然后到处降级，不如把定位钉死：

    full         GNU/Linux —— 全部能力
    limited      macOS / BSD —— 排版与风格可用，内存检查不可用
    unsupported  其余 —— 启动即拒

分工写清楚，别把两件事混成一句：

  * **级别**（`detect()`）按系统名判，取的是 `os.uname()` 的第一手信息而不是
    发行版字符串——`Linux` / `GNU/Linux`、Android 自报 `Linux` 这类命名差异靠
    "猜发行版"会误伤。启动守卫用它，必须快。
  * **某项能力到底能不能用**（`capability()`）去**实测**：装了 valgrind 的 BSD 是
    能用的，没装 valgrind 的 GNU/Linux 也是能用的（还能走 ASan），而 macOS 两条
    路都没有。查表判不出这些。
"""

import os
import fcntl
import sys


def find_binary(names):
    """PATH 上第一个可执行的候选。doctor 与能力探测共用这一份。"""
    return _default_find(names)


def first_line_of(command):
    """跑一条命令取第一行输出；跑不起来返回空串。"""
    return _default_run(command)


def _default_find(names):
    for name in names:
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if not entry:
                continue
            candidate = os.path.join(entry, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return ""


def _default_run(command):
    import subprocess

    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in (done.stdout or done.stderr or "").splitlines():
        if line.strip():
            return line.strip()
    return ""

FULL = "full"
LIMITED = "limited"
UNSUPPORTED = "unsupported"

# 支持面的一句话说明，两侧与文档用同一句
SUPPORT_LINE = ("支持面：GNU/Linux 全部能力；macOS / BSD 只保证排版与风格"
                "（内存检查不可用）；其余平台不支持")

UNIX_NAMES = ("linux", "darwin", "freebsd", "openbsd", "netbsd", "dragonfly")


def system_name():
    """系统名，取 `os.uname()`（POSIX）与 `platform.system()` 的第一手信息。"""
    uname = getattr(os, "uname", None)
    if uname is not None:
        return str(uname().sysname or "").lower()
    try:
        import platform as platform_module
    except ImportError:
        return ""
    return str(platform_module.system() or "").lower()


def detect():
    """探测本机能力，返回 (级别, 判据说明)。

    判据写进返回值是为了让 doctor 能把它原样打出来——"为什么判成这个级别"
    和"级别是什么"同样重要。
    """
    name = system_name()
    if not name:
        return UNSUPPORTED, "无法识别系统名（既没有 os.uname() 也没有 platform）"
    if name == "linux":
        # Android 也自报 Linux，但它没有 valgrind，glibc 的 asan 路径也不成立
        if os.path.isdir("/system/bin") and not os.path.isdir("/usr/bin"):
            return UNSUPPORTED, "Android（自报 Linux，但没有 GNU 工具链）"
        return FULL, "GNU/Linux"
    if name in UNIX_NAMES:
        return LIMITED, "{}（非 Linux：无 valgrind / LSan）".format(name)
    if name.startswith("cygwin") or name.startswith("msys") or "mingw" in name:
        return UNSUPPORTED, "{}（Windows 兼容层，POSIX 语义不完整）".format(name)
    return UNSUPPORTED, "{}（非 UNIX）".format(name)


def refusal(level, reason):
    """不支持时的一句话说明，入口直接打印它再退出。"""
    return ("本平台不支持：{}（{}）。\n{}".format(reason, level, SUPPORT_LINE))


def guard(stream=None):
    """入口守卫：不支持就打印说明并返回非零退出码，支持则返回 0。

    在**做任何事之前**调用：与其让用户等到内存检查阶段才发现不可用，
    不如启动就说清。
    """
    level, reason = detect()
    if level != UNSUPPORTED:
        return 0
    stream = stream or sys.stderr
    stream.write(refusal(level, reason) + "\n")
    return 2

LOCK_NAME = ".cxx-ci.lock"

# 锁由持有者写进环境，子进程据此知道"这棵树已经有人替我守着了"。
# 没有它就会自锁：standardize 拿到锁之后调 BUILD_COMMAND（内部是 ci.py），
# 子进程再要同一把锁就被自己拒掉，构建直接失败
LOCK_ENV = "CXX_CI_LOCK_HELD"


def lock_path(log_dir):
    """互斥锁文件的位置：日志目录下；没有日志目录时返回空串。"""
    return os.path.join(str(log_dir), LOCK_NAME) if log_dir else ""


def acquire_lock(log_dir):
    """占用这个工程的互斥锁，返回 (句柄或 None, 错误说明)。

    两份工具在同一个工程上同时跑会互相破坏：一份的 clean 会清掉另一份正在用的
    构建树（随后 configure 失败，好工程判红），阶段日志被两份交替追加成"失败+
    成功"的混合文件，memcheck 的结构化报告也会被写成两份文档拼在一起、解析报
    XML 结构损坏。

    flock 由内核在进程退出时自动释放，因此进程被 SIGKILL 也不会留下死锁；锁文件
    本身留在盘上无害。取不到锁时就直说"有另一份在跑"，不猜、不等待、不硬闯。
    """
    path = lock_path(log_dir)
    if not path:
        return None, ""
    if os.environ.get(LOCK_ENV):
        # 同一棵进程树里已经有人持锁：它保证了这个工程上只有我们这一串在跑。
        # 这里不再取锁，也不负责释放
        return None, ""
    try:
        if not os.path.isdir(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        handle = open(path, "w", encoding="utf-8")
    except (IOError, OSError) as exc:
        return None, "无法建立互斥锁 {}: {}".format(path, exc)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        handle.close()
        return None, ("另一个进程正在这个工程上跑（{}）：两个进程同时改同一棵"
                      "构建树会互相破坏，等它结束再试".format(path))
    try:
        handle.write(str(os.getpid()) + "\n")
        handle.flush()
    except (IOError, OSError):
        # 写不进 pid 只影响排查时的线索，互斥本身已经拿到了
        pass
    # 让子进程知道锁已经有人持着（BUILD_COMMAND / VERIFY_COMMAND 都会再起
    # ci.py，它们不该、也不能再取一次）
    os.environ[LOCK_ENV] = str(os.getpid())
    return handle, ""


def release_lock(handle):
    """释放互斥锁。"""
    if handle is None:
        return
    os.environ.pop(LOCK_ENV, None)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (IOError, OSError):
        # 解锁失败无所谓：close 会释放，进程退出时内核也会释放
        pass
    try:
        handle.close()
    except (IOError, OSError):
        # 同上：这个句柄已经没用了
        pass
