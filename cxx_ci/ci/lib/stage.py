#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段的执行、落盘与展示。

驱动层（ci.py 与 standardize.py）必须共用这一份实现，否则两个入口的输出
行为会分叉：直跑时准备步的 `make` 回显与"已是最新"会原样上屏，而经 ci.py
的同一阶段被筛掉了。

三条输出通道，各自的归属写在这里一处：

    协议行（#diag/#fail/#sum）  判定来源，展示层按策略回显
    阶段 stdout/stderr          完整落盘 CI_LOG_DIR/<stage>.log
    过程噪声（common.noise）    默认只落盘，CXX_CI_VERBOSE=1 时透传

`emit` 的 level 决定展示策略：quiet 只在失败时出声，normal 只回显协议行与
摘要，verbose 透传全量。standardize.py 是交互入口，阶段的差异清单与诊断
本身就是结论，因此用 full——它不筛，但仍走同一份落盘与横幅逻辑。
"""

import sys
if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")
import collections
import os
import subprocess
import time

from .diagnostics import (Diagnostic, collect_diagnostics, dedup_diagnostics,
                          filter_diagnostics, resolve_source)
from .errors import KIND_SKIPPED, OK, STAGE_TIMEOUT, compose, kind
from .output import decode, err, out
from . import platform
from .paths import scope_of, scope_roots
from .protocol import diag, parse, plain, plain_text
from .tools import capture

# 一个阶段的完整结果，避免多个位置参数在调用点被记错顺序
Result = collections.namedtuple(
    "Result", "name code elapsed out err timed_out")

# 透传开关：入口设进子进程环境，阶段脚本据此决定噪声是否上屏
VERBOSE_ENV = "CXX_CI_VERBOSE"

# 构建目录下候选基准的递归深度上限：编译器的工作目录可能是构建目录里
# 任意深度的子目录，但深到四五层已无实际意义，递归必须有界。
BASE_DEPTH = 4
BASE_LIMIT = 2000


def verbose():
    """当前进程是否处于透传模式。"""
    return os.environ.get(VERBOSE_ENV) == "1"


def set_verbose(on=True):
    """打开本进程的过程噪声透传。

    只用于"显式打开"：入口已经把 CXX_CI_VERBOSE 设进环境时，阶段脚本不能
    因为自己的开关缺省为假就把它关掉，因此这里没有关闭分支。
    """
    if on:
        os.environ[VERBOSE_ENV] = "1"


def c_locale_env(extra=None):
    """固定工具输出语言的环境变量。

    固定为 C 区域设置：编译器与 make 会把级别词本地化（中文环境下 g++ 输出
    "警告：" 而不是 "warning:"，make 的 "Entering directory" 也会变），而
    诊断解析与编译数据库解析都依赖固定版式，输出语言必须稳定。

    与 standardize 侧同名同实现——同一个概念在两侧用同一个名字。
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    for key, value in (extra or {}).items():
        env[str(key)] = str(value)
    return env


# ------------------------------------------------------------------ 落盘

def first_line(text):
    """取首行非空文本：把阶段的多行说明压成一行，供上层当跳过原因展示。"""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def log_path(config, stage):
    """阶段日志的路径；未配置日志目录时返回空串。"""
    directory = getattr(config, "log_dir", "") or ""
    if not directory:
        return ""
    return os.path.join(directory, stage + ".log")


def _open_log(config, stage, mode):
    """打开阶段日志，失败返回 None（日志写不成不该让阶段失败）。"""
    path = log_path(config, stage)
    if not path:
        return None
    try:
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        # 必须显式指定编码：el6 上 LC_ALL=C 时默认编码是 ASCII，
        # 写非 ASCII 的诊断会直接抛 UnicodeEncodeError
        return open(path, mode, encoding="utf-8")
    except (IOError, OSError):
        return None


def truncate_log(config, stage):
    """阶段开始前清空日志，避免上一次运行的内容混进来。

    同样尽力而为：清不掉就让它留着旧内容，也比整轮跑不动好——写入会追加，
    读者看到的至少是最新一次的输出。
    """
    handle = _open_log(config, stage, "w")
    if handle is None:
        return ""
    try:
        handle.close()
    except (IOError, OSError):
        return ""
    return log_path(config, stage)


def append_log(config, stage, text):
    """把一段文本追加到阶段日志，返回路径；写不成返回空串。

    日志是旁路产物：磁盘满、配额、设备写失败都不该把一次已经通过的阶段变成
    非 0 退出，更不该让整轮门禁半途而废（汇总、报告、收尾全都不再执行）。
    """
    if not text:
        return ""
    handle = _open_log(config, stage, "a")
    if handle is None:
        return ""
    try:
        handle.write(text)
        handle.close()
    except (IOError, OSError):
        # 写不成就算了：调用方只看返回值。close 的失败也算写失败，不能穿出去
        try:
            handle.close()
        except (IOError, OSError):
            # 上面那次 write 已经失败了，close 再失败也没有新信息
            pass
        return ""
    return log_path(config, stage)


def noise(config, stage, text):
    """阶段内部的过程噪声（准备步命令回显、子工具的"已是最新"）。

    默认只落盘，透传模式下才上屏。噪声既不是诊断也不是差异，混进 stdout
    会把结论埋掉；但完全不留下又无法排查，所以必须落盘。落盘时补一个换行，
    否则连续追加的段落会粘成一行。
    """
    if not text:
        return
    if verbose():
        out(text)
    append_log(config, stage, text if text.endswith("\n") else text + "\n")


def write_log(config, result):
    """把阶段的完整输出落盘，返回路径；写不成返回空串。

    控制台只显示筛过的诊断，被筛掉的内容必须能在这里查到。用追加而不是
    覆盖：阶段脚本自己写下的过程噪声已经在这个文件里了。
    """
    text = result.out
    if result.err:
        text += "\n---- stderr ----\n" + result.err
    return append_log(config, result.name, text)


def run_quiet(cmd, cwd, config, stage):
    """执行命令：诊断发协议行，其余输出作为过程噪声。

    准备步（make moc_/qrc_ 之类）的命令回显与"已是最新"既不是诊断也不是
    结论，直接打上屏正是直跑入口输出契约缺口的来源；这里统一交给 noise，
    默认只落盘，透传模式才上屏。
    """
    code, captured, _ = capture(cmd, cwd=cwd, merge=True)
    text = decode(captured)
    for item in collect_diagnostics(text):
        out(diag(item.level, item.file, item.line, item.column, item.message))
    body = "$ " + " ".join(str(part) for part in cmd)
    if text.strip():
        body += "\n" + text.rstrip()
    noise(config, stage, body)
    return code


# ------------------------------------------------------------------ 执行

def run_stage(config, name, script, extra=(), verbose=False, timeout=0):
    """执行一个阶段脚本，返回 Result。

    非透传模式捕获输出交给 emit 展示并落盘；透传模式让子进程直接继承终端，
    长构建才能看到实时进度。透传模式下不保证完整落盘。
    """
    cmd = [sys.executable, str(script)] + [str(item) for item in extra]
    limit = timeout or None
    env = c_locale_env({VERBOSE_ENV: "1"} if verbose else None)
    truncate_log(config, name)
    start = time.monotonic()
    if verbose:
        # 逐行泵出：既保留长构建的实时进度，又能把协议前缀挡在人眼之外；
        # 原始行照旧收进日志，便于逐字核对
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env)
        captured = []
        try:
            while True:
                raw = proc.stdout.readline()
                if not raw:
                    break
                captured.append(decode(raw))
                out(plain(decode(raw).rstrip("\n")))
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait()
            raise
        finally:
            proc.stdout.close()
        timed_out = False
        try:
            proc.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            timed_out = True
        elapsed = time.monotonic() - start
        code = STAGE_TIMEOUT if timed_out else compose(proc.returncode)
        return Result(name, code, elapsed, "".join(captured), "", timed_out)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env,
                            **platform.process_group_kwargs())
    timed_out = False
    try:
        captured, problems = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        # 超时后强杀整棵进程树：只杀阶段脚本那一个进程，它拉起的 cmake/编译
        # 器会继续跑，和下一次运行抢同一个构建目录，写出 file truncated 的产物
        platform.kill_process_tree(proc)
        captured, problems = proc.communicate()
        timed_out = True
    except KeyboardInterrupt:
        platform.kill_process_tree(proc, gentle=True)
        proc.wait()
        raise
    elapsed = time.monotonic() - start
    # 子进程给出的是退出状态；被信号杀死时 returncode 为负数，不能直接用
    code = STAGE_TIMEOUT if timed_out else compose(proc.returncode)
    return Result(name, code, elapsed,
                decode(captured), decode(problems), timed_out)


# ------------------------------------------------------------ 诊断展示

def diagnostic_bases(config):
    """相对路径的候选基准。

    编译器的工作目录随构建工具变化：qmake 在 build/src 之类的子目录里编译，
    告警路径是相对那里的。构建根及其下若干层子目录正好覆盖这些位置；深度
    有界，免得在巨大的构建树里白走。
    """
    bases = [config.root, config.build_dir, config.test_build_dir]
    for parent in (config.build_dir, config.test_build_dir):
        if not parent or not os.path.isdir(parent):
            continue
        for root, names, _ in os.walk(parent):
            depth = root[len(parent):].count(os.sep)
            if depth >= BASE_DEPTH:
                names[:] = []
                continue
            for name in sorted(names):
                bases.append(os.path.join(root, name))
            if len(bases) > BASE_LIMIT:
                return bases
    return bases


def owned(item, config):
    """把协议行的路径按 SCOPE 归因，返回 Diagnostic。

    协议行里的路径可能是相对的（相对编译器的工作目录），必须先归一化再比
    SCOPE 前缀；否则项目自身的告警会被一律判成域外而静默。
    """
    project, excluded = scope_roots(config)
    path, _ = resolve_source(item.file, config.root, diagnostic_bases(config))
    scope = scope_of(path, project, excluded)
    # 展示用编译器的版式，而不是协议行本身：读者要看的是诊断，不是传输格式
    raw = "{}:{}:{}: {}: {}".format(path, item.line, item.column, item.level,
                                    item.message)
    return Diagnostic(item.level, scope, path, item.line, item.column,
                      item.message, raw)


def stage_view(config, result):
    """把阶段输出归纳成结构化结论，供终端展示与机器报告共用。

    报告只做搬运：这里算出的诊断集合、失败清单与摘要就是终端展示的那一份，
    因此"报告说绿、终端说红"在结构上不可能发生——判定仍只由阶段负责，本函数
    不新增任何判定分支。
    """
    diags, fails, knowns, sums = parse(result.out + "\n" + result.err)
    heuristic = False
    if not diags and not fails and not sums:
        raw = collect_diagnostics(result.out + "\n" + result.err, config.root,
                                  diagnostic_bases(config))
        diags = [Diagnostic(item.level, "project", item.file, item.line,
                            item.column, item.message, item.raw)
                 for item in raw]
        heuristic = bool(diags)
    items = [owned(item, config) for item in diags]
    kept = filter_diagnostics(
        items, config.diagnostic_level, config.diagnostic_sources,
        config.diagnostic_ignore)
    if config.diagnostic_dedup:
        shown = dedup_diagnostics(kept)
    else:
        shown = [(item, 1) for item in kept]
    return {
        "diagnostics": shown,
        "fails": fails,
        # 已登记的存量问题：这里只**搬运**阶段自己发的清单。报告不再拿
        # checklist 重判一次——那等于让报告成为第二个判卷人
        "knowns": knowns,
        "sums": sums,
        "heuristic": heuristic,
        "total": len(items),
        "dropped": len(items) - len(kept),
        "merged": len(kept) - len(shown),
    }


def render_diagnostics(config, result, log_path):
    """回放协议行过策略管线，最多再给一行摘要。

    阶段脚本发 #diag 是判定来源；没有协议行时退回解析编译器版式，并在
    行尾标注 (启发式)，让读者知道这条结论有多可信。
    """
    if config.diagnostic_level == "off":
        return
    view = stage_view(config, result)
    for item, count in view["diagnostics"]:
        suffix = " [×{}]".format(count) if count > 1 else ""
        suffix += " (启发式)" if view["heuristic"] else ""
        out("    {}{}".format(item.raw, suffix))
    for text in view["sums"]:
        out("    " + text)
    if not view["total"]:
        return
    note = "    {} 条诊断".format(len(view["diagnostics"]))
    details = []
    if view["merged"]:
        details.append("合并重复 {} 条".format(view["merged"]))
    if view["dropped"]:
        details.append("过滤 {} 条".format(view["dropped"]))
    if details:
        note += "（" + "，".join(details)
        note += "，见 {}）".format(log_path) if log_path else "）"
    elif log_path:
        note += "（完整输出见 {}）".format(log_path)
    out(note)


def render_failure(config, result, log_path):
    """失败阶段：先给逐例失败清单与摘要，再给首个错误块。

    逐例失败与摘要先行，完整日志留在文件里按需查看。
    """
    _, fails, _knowns, sums = parse(result.out + "\n" + result.err)
    for text in sums:
        out("    " + text)
    for item in fails:
        out("    {} {}".format(item.case, item.reason))
    if fails:
        out("    {} 例失败".format(len(fails)))
    if config.fail_output == "full":
        if result.out:
            out(result.out.rstrip())
        if result.err:
            err(result.err.rstrip())
    elif log_path:
        out("    完整输出见 " + log_path)


def emit(config, result, level):
    """按输出等级打印阶段结果。

    quiet 只在失败时出声，normal 只回显协议行与摘要，verbose 透传全量。
    环境缺件（KIND_SKIPPED）既不是通过也不是失败，单独一档。
    """
    unavailable = kind(result.code) == KIND_SKIPPED
    failed = result.code != OK and not unavailable
    if not failed and not unavailable and level == "quiet":
        # quiet 省略的是"通过"，跳过是"没检查"，不能一起省
        return
    if result.code == OK:
        mark = "ok"
    elif unavailable:
        # 环境缺件既不是通过也不是失败，单独一档
        mark = "SKIPPED"
    elif result.timed_out:
        mark = "TIMEOUT"
    else:
        mark = "FAILED"
    out("[{:>7}] {} ({:.1f}s)".format(mark, result.name, result.elapsed))
    written = write_log(config, result)
    if level == "full" or level == "verbose":
        if result.out:
            out(plain_text(result.out).rstrip())
        if result.err:
            err(plain_text(result.err).rstrip())
    elif not failed:
        render_diagnostics(config, result, written)
    else:
        render_failure(config, result, written)
