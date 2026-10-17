#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""支持面：把"只在 UNIX 上跑"写成显式约束，而不是到处静默降级。

工具事实上依赖 POSIX 语义：内存检查只有 valgrind 与 ASan/LSan（GNU/Linux 专属，
ASan 运行库还靠 `<编译器> -print-file-name=libasan.so` 定位）、子进程与 `LC_ALL=C`
的输出前缀约定、路径与软链假定。这些原本一处都没写成约束，于是换个平台跑出来的
是"阶段一路 [SKIPPED]、内存检查静默不可用"这种半死不活的状态，而不是一句
"本平台不支持"。

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
import signal
import time
import sys


def find_binary(names):
    """PATH 上第一个可用的可执行文件，找不到返回空串。

    与 standardize 侧同名同实现——同一个概念在两侧用同一个名字。查找本身
    委托 tools.find_tool，避免同一件事在侧内出现第二份实现。
    """
    from . import tools

    path, _code, _message = tools.find_tool(names)
    return path or ""


def first_line_of(command):
    """跑一条命令，取第一行有内容的输出；跑不起来返回空串。"""
    from . import tools

    status, captured, _ = tools.capture([str(part) for part in command])
    if status != 0 and not captured:
        return ""
    text = captured.decode("utf-8", "replace") if isinstance(captured, bytes) \
        else str(captured or "")
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


# 侧内沿用旧名，避免同一文件里两套叫法
_default_find = find_binary
_default_run = first_line_of

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


def memory_check_available(level=None):
    """本平台能不能做内存检查（按级别判，不探测工具）。"""
    if level is None:
        level, _reason = detect()
    return level == FULL


def capability(level=None, find=None, run=None):
    """内存检查在本机**实测**能不能用，返回 (可用, 判据)。

    与 `detect()` 分工不同，别混：`detect()` 按系统名判**支持级别**（启动守卫用，
    必须快、不能依赖工具装没装）；这里去**真的探一下**能不能跑——装了 valgrind
    的 BSD 是能用的，没装 valgrind 的 GNU/Linux 是能用的（还能走 ASan），而
    macOS 两条路都没有。查表只能判"这平台一般行不行"，能力要以实测为准。
    """
    level = level or detect()[0]
    if level == UNSUPPORTED:
        return False, "{} 不受支持".format(system_name() or "本平台")
    find = find or _default_find
    run = run or _default_run
    valgrind = find(["valgrind"])
    if valgrind and run([valgrind, "--version"]):
        return True, "valgrind {}".format(valgrind)
    if valgrind:
        return True, "valgrind {}（版本取不到，仍按可用算）".format(valgrind)
    if level == FULL:
        return True, "无 valgrind，但 GNU/Linux 上可走 ASan/LSan"
    return False, "{} 上既没有 valgrind 也没有 LSan".format(
        system_name() or "本平台")




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


def process_group_kwargs():
    """把子进程放进独立进程组（POSIX）。

    不能让阶段脚本与驱动同组：驱动收到 Ctrl-C 时终端会把信号发给整个前台进程组，
    那正好是我们想要的；但超时强杀只作用于驱动能直接看到的那个 pid，于是编译树
    会活下来。独立成组之后 killpg 才能一次收干净。
    """
    return {"start_new_session": True} if os.name == "posix" else {}


def kill_process_tree(proc, gentle=False):
    """杀掉子进程及其拉起的整棵树。

    gentle 为真时先 TERM 让工具自己收尾（编译器会删掉写了一半的产物），过一会儿
    再 KILL。进程组杀不掉（非 POSIX）时退回只杀直接子进程。
    """
    if os.name == "posix":
        try:
            group = os.getpgid(proc.pid)
        except OSError:
            group = None
        if group is not None:
            try:
                os.killpg(group, signal.SIGTERM if gentle else signal.SIGKILL)
            except OSError:
                # 进程组可能已经整个退出：杀不到不是错误，调用方接下来照常 wait
                pass
            if gentle:
                for _ in range(50):
                    if proc.poll() is not None:
                        break
                    time.sleep(0.1)
                try:
                    os.killpg(group, signal.SIGKILL)
                except OSError:
                    # 宽限期里它自己退了，没什么可杀的
                    pass
            return
    try:
        proc.kill()
    except OSError:
        # 同上：已经退出就无须再杀
        pass

LOCK_NAME = ".cxx-ci.lock"

# 锁由持有者写进环境，子进程据此知道"这棵树已经有人替我守着了"。
# 没有它就会自锁：standardize 拿到锁之后调 BUILD_COMMAND（内部是 ci.py），
# 子进程再要同一把锁就被自己拒掉，构建直接失败
LOCK_ENV = "CXX_CI_LOCK_HELD"


def lock_path(target):
    """互斥锁文件的位置：被保护的构建树里。

    锁要保护的其实是构建树（两份工具同时改同一棵树才会互相破坏），所以键取
    构建树而不是日志目录：只改 CI_LOG_DIR 就会拿到两把不同的锁，而它们改的是
    同一棵树。锁文件放在构建树里，clean 会显式保留它（见 clean.kept_names）。
    """
    return os.path.join(str(target), LOCK_NAME) if target else ""


def acquire_lock(target):
    """占用这棵构建树的互斥锁，返回 (句柄或 None, 错误说明)。

    两份工具在同一个工程上同时跑会互相破坏：一份的 clean 会清掉另一份正在用的
    构建树（随后 configure 失败，好工程判红），阶段日志被两份交替追加成"失败+
    成功"的混合文件，memcheck 的结构化报告也会被写成两份文档拼在一起、解析报
    XML 结构损坏。

    flock 由内核在进程退出时自动释放，因此进程被 SIGKILL 也不会留下死锁；锁文件
    本身留在盘上无害。取不到锁时就直说"有另一份在跑"，不猜、不等待、不硬闯。
    """
    path = lock_path(target)
    if not path:
        return None, ""
    if os.environ.get(LOCK_ENV) == path:
        # 同一棵进程树里已经有人持着**同一把**锁：它保证了这棵树上只有我们这一串
        # 在跑。这里不再取锁，也不负责释放。比对路径而不是"变量存在即跳过"：
        # 后者会让任何自带这个变量的环境（哪怕是别的工程设的）都绕开互斥
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
    # 让子进程知道**这一把**锁已经有人持着（BUILD_COMMAND / VERIFY_COMMAND 都会
    # 再起 ci.py，它们不该、也不能再取一次）。记的是锁文件路径而不是 pid：
    # 子进程要能判断"你持的是不是我要的这一把"，否则任何来源的同名变量都能绕过互斥
    os.environ[LOCK_ENV] = path
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


def install_term_handler():
    """把 SIGTERM 折算成 KeyboardInterrupt，让收尾与回滚照常走。

    只处理 Ctrl-C 时，`kill` 或 CI 系统取消作业送来的 SIGTERM 会让驱动就地消失：
    正在跑的阶段脚本与它拉起的编译树继续活着，而互斥锁随进程退出被内核释放——
    紧接着的下一次运行不会再被拦住，两棵树在同一目录里打架。
    """
    def handler(_number, _frame):
        raise KeyboardInterrupt()

    try:
        signal.signal(signal.SIGTERM, handler)
    except (ValueError, OSError):
        # 不在主线程或平台不支持：退回缺省行为
        pass


class PatternTimeout(Exception):
    """一段正则匹配没有在墙钟上限内跑完。"""


def run_with_deadline(func, seconds, error=None):
    """在墙钟上限内跑 func；超时抛 error（缺省 PatternTimeout）。

    用 SIGALRM 而不是线程：调用方是单线程的，而正则一旦开始回溯，只有信号能把
    它打断。平台没有 SIGALRM（非 POSIX）时退化为不设上限——那时行为与不设限
    一致，不会更糟。
    """
    error = error or PatternTimeout
    if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        return func()

    def _alarm(_number, _frame):
        raise error()

    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return func()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
