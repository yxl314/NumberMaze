#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部工具的查找、命令执行与阶段共用的小工具。"""
import sys
if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")
import os
import shutil
import concurrent.futures as futures
import subprocess
from .errors import (COMMAND_UNRUNNABLE, OK, STAGE_UNAVAILABLE, TOOL_MISSING,
                     WORKDIR_MISSING)
from .output import decode, err, out
from .diagnostics import collect_diagnostics
from . import protocol


def find_tool(names):
    """在 PATH 上按顺序找到第一个可用的可执行文件。

    返回 (可执行文件, 错误码, 说明)。
    """
    for name in names:
        found = shutil.which(name)
        if found:
            return found, OK, ""
    return None, TOOL_MISSING, "找不到可执行文件: " + "、".join(names)

def job_count(jobs):
    """并行任务数，0 或负数表示按 CPU 核数。"""
    if jobs and jobs > 0:
        return jobs
    return os.cpu_count() or 1

def child_env(overrides=None, base=None):
    """构造子进程环境：继承当前环境再叠加配置里的注入项。

    GUI 项目无头运行需要 QT_QPA_PLATFORM 之类的变量，写在配置里比塞进
    命令本身干净，也不影响命令的可读性。
    """
    env = dict(base if base is not None else os.environ)
    for key, value in (overrides or {}).items():
        env[str(key)] = str(value)
    return env


def capture(cmd, cwd=None, merge=False, env=None, stdin=None):
    """执行命令并捕获输出，返回 (退出码, 原始标准输出, 原始标准错误)。

    merge 为真时把标准错误并入标准输出，此时第三个元素为空。
    env 为真时用给定的环境替换当前环境，用于固定输出语言等场景。
    stdin 给定时以字节写入子进程标准输入，调用方不必在源码树里落临时文件。
    """
    try:
        proc = subprocess.Popen(
            [str(part) for part in cmd],
            cwd=str(cwd) if cwd else None,
            env=env,
            stdin=subprocess.PIPE if stdin is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge else subprocess.PIPE,
        )
    except (OSError, IOError) as exc:
        # 程序不存在或不可执行：返回非零码并说明，不把异常抛给调用方
        return COMMAND_UNRUNNABLE, b"", str(exc).encode("utf-8", "replace")
    out, err = proc.communicate(stdin)
    return proc.returncode, out, err or b""

def parallel_map(function, items, jobs=0):
    """按给定并发度对每个元素调用 function，保持输入顺序返回结果。

    子进程是阻塞等待型，线程只负责 spawn 与收集，没有 GIL 瓶颈。用线程
    而非进程池，省掉序列化，也让 function 可以自由闭包配置对象。

    用标准库的线程池而不是自建队列：自建版本里某个元素抛异常只会让该线程
    退出，那个位置留成 None，调用方按元组解包时才炸，离真因很远。
    """
    items = list(items)
    limit = job_count(jobs)
    if limit <= 1 or len(items) <= 1:
        return [function(item) for item in items]
    with futures.ThreadPoolExecutor(max_workers=limit) as pool:
        return list(pool.map(function, items))


def run_with_diagnostics(cmd, cwd=None, env=None):
    """执行命令，回显原始输出，并把编译器诊断发成协议行。

    编译器告警的版式是固定的（路径:行:列: 级别: 消息），生成侧解析后发
    #diag，驱动层就不必对全文跑正则。返回退出码。
    """
    out("$ " + " ".join(str(part) for part in cmd))
    code, captured, _ = capture(cmd, cwd=cwd, merge=True, env=env)
    text = decode(captured)
    if text.strip():
        out(text.rstrip())
    for item in collect_diagnostics(text):
        out(protocol.diag(item.level, item.file, item.line, item.column,
                          item.message))
    return code


def require_test_workdir(config):
    """检查测试配置与工作目录。

    没配与配错要分开说：前者是"这个工程不跑测试"，后者是"路径写错了"。
    两种都返回 STAGE_UNAVAILABLE / WORKDIR_MISSING，由上层标 [SKIPPED]。

    返回 (工作目录, 错误码, 说明)。
    """
    if not getattr(config, "test_command", None):
        return None, STAGE_UNAVAILABLE, (
            "配置里没写 TEST_COMMAND，跳过测试与内存检查")
    if not config.test_workdir:
        return None, STAGE_UNAVAILABLE, (
            "配置里没写 TEST_WORKDIR，跳过测试与内存检查")
    if not os.path.isdir(config.test_workdir):
        return None, WORKDIR_MISSING, "测试工作目录不存在: " + config.test_workdir
    return config.test_workdir, OK, ""

def memcheck_command(binary, config, xml_path=""):
    """构造 valgrind 命令。

    只用 valgrind 3.8 上就有的选项（--errors-for-leak-kinds 是 3.9 才加的）。
    给出 xml_path 时改用结构化报告，退出码交给工具判定而非 valgrind：
    成败取决于归因结果，而不是有没有任何一条错误。
    """
    cmd = [binary, "--leak-check=full"]
    if xml_path:
        cmd += ["--xml=yes", "--xml-file=" + xml_path, "--error-exitcode=0"]
    else:
        cmd.append("--error-exitcode=1")
    if config.memcheck_suppressions and os.path.isfile(
            config.memcheck_suppressions):
        cmd.append("--suppressions=" + config.memcheck_suppressions)
    return cmd + list(config.memcheck_command)
