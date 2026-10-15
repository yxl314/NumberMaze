#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ASan+LSan 双通道内存检测路线。

与 valgrind 路线互补：asan 快一个数量级，适合日常；valgrind 的调用栈归因
适合合入门禁的深度扫描。两条路线的判定方式不同，因此这里不复用
lib/valgrind.py。

为什么是双通道：

- 通道 A 抓 use-after-free、越界、双释这类内存错误，全量用例都跑，运行期
  关掉泄漏扫描。这些错误由拦截器在发生当场抓，不依赖 ptrace，环境鲁棒。
- 通道 B 只跑无 GUI 的逻辑用例（配置里的 asan_leak_args），打开泄漏扫描并
  用显式抑制清单压掉 Qt 对象树所有权带来的存量"泄漏"。常驻注入软件以 ptrace
  附着全部进程时，LSan 的收尾扫描遇到 Qt GUI 事件循环会被打到 fatal——那是
  环境冲突，不是被测代码泄漏；把泄漏扫描限制在无 GUI 用例上就绕开了这个
  冲突面。

影子构建是硬要求：asan 路线不编译主产物，注入只落在影子目录，日常检查不会
污染合入门禁用的主构建树。

运行时必须把 asan 运行库前置到 LD_PRELOAD：环境注入库会抢 DT_NEEDED 首位，
不前置时拦截器装不上，asan 只会静默放行。

判定上有一处与 valgrind 路线不同：内存指纹的优先级高于 xfail 登记。业务用例
可以带病登记（已知的断言失败），内存错误永远不可——输出里一旦出现
ASAN_ERROR_MARKS 里的指纹就判失败，哪怕该用例在清单里。
"""

import sys
if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")
import collections
import os

from . import build, errors, output, protocol, stage, testreport, tools, xfail

# 影子构建目录的缺省子目录名：拼在 BUILD_DIR 之下
DEFAULT_BUILD_SUBDIR = "tests_asan"

# asan 运行库文件名；路径由编译器自报，不去猜 /usr/lib
RUNTIME_NAME = "libasan.so"

# 两个通道。通道 A 抓发生当场的内存错误，通道 B 做收尾泄漏扫描。
CHANNEL_ERROR = "error"
CHANNEL_LEAK = "leak"

# 通道名到人读标签的映射，摘要与诊断共用，只在这里定义一次
CHANNEL_LABELS = {
    CHANNEL_ERROR: "通道 A（内存错误，全量用例）",
    CHANNEL_LEAK: "通道 B（泄漏扫描，无 GUI 用例）",
}

# 一次通道判定的结果。判定与展示分开：judge 是纯函数，report 负责上屏。
Judge = collections.namedtuple(
    "Judge", "code failures known marks totals message")


def build_dir(config):
    """影子构建目录：显式配置优先，缺省落在 BUILD_DIR 之下。"""
    configured = getattr(config, "asan_build_dir", "") or ""
    if configured:
        return configured
    return os.path.join(config.build_dir, DEFAULT_BUILD_SUBDIR)


def retarget(command, old_prefix, new_prefix):
    """把指令里以 old_prefix 开头的路径项改指到 new_prefix。

    只替换前缀本身，并要求边界落在路径分隔符上：BUILD_DIR 可能是另一个
    目录名的前半段，直接做字符串替换会把不相干的路径一起改掉。
    """
    result = []
    for part in command or ():
        text = str(part)
        if old_prefix and text == old_prefix:
            text = new_prefix
        elif (old_prefix and text.startswith(old_prefix)
              and text[len(old_prefix):len(old_prefix) + 1] == os.sep):
            text = new_prefix + text[len(old_prefix):]
        result.append(text)
    return result


def command(config):
    """asan 路线的测试指令。

    缺省由 MEMCHECK_COMMAND 推导：其中的 BUILD_DIR 前缀换成影子目录，
    参数部分原样保留——跑的还是同一份测试程序与同一套参数，只是二进制
    来自带净化的影子构建。
    """
    configured = getattr(config, "asan_command", None)
    if configured:
        return [str(part) for part in configured]
    # 测试构建目录常常与主构建目录分开（qmake 工程必须分开，否则测试工程按
    # 相对路径引用主构建的静态库会解析错）。只认 BUILD_DIR 的话，缺省指令会
    # 指向**普通测试树**——那棵树在 clean 之后是空的，报出来是"No rule to make
    # target"，跟代码问题毫无关系。两种前缀都试
    shadow = build_dir(config)
    # 先试 BUILD_DIR：测试目录通常在它之下，按 BUILD_DIR 换整个测试子树会一起
    # 搬到影子目录里（既有行为，别改）。TEST_BUILD_DIR 在 BUILD_DIR 之外时才轮到它
    for prefix in (config.build_dir, getattr(config, "test_build_dir", "")):
        if not prefix:
            continue
        result = retarget(config.memcheck_command, prefix, shadow)
        if result != [str(part) for part in (config.memcheck_command or ())]:
            return result
    return []


def inject_args(config):
    """构建侧的 asan 注入参数，按构建工具给各自的形式。

    qmake：QMAKE_CXXFLAGS+= 编译、LIBS+= 链接，链接项排在末尾（见
    build.configure_command 的说明）。cmake：CMAKE_CXX_FLAGS 与
    CMAKE_EXE_LINKER_FLAGS 两个 -D 等价注入。
    """
    cxxflags = " ".join(config.asan_cxxflags)
    ldflags = " ".join(config.asan_ldflags)
    if config.build_tool == "cmake":
        return ["-DCMAKE_CXX_FLAGS=" + cxxflags,
                "-DCMAKE_EXE_LINKER_FLAGS=" + ldflags]
    return ["QMAKE_CXXFLAGS+=" + cxxflags, "LIBS+=" + ldflags]


def configure_command(config, binary, build_file, build_type):
    """影子构建的配置命令：基础命令加 asan 注入，注入排在末尾。"""
    return build.configure_command(config, binary, build_file, build_type,
                                   inject_args(config))


def find_runtime_library(config):
    """按候选编译器逐个探测运行库，返回 (路径或空串, 试过的命令列表)。"""
    tried = []
    for compiler in compiler_candidates(config):
        tried.append("{} -print-file-name={}".format(compiler, RUNTIME_NAME))
        found = runtime_library(compiler)
        if found:
            return found, tried
    return "", tried


def compiler_candidates(config):
    """按序给出可用来探测 asan 运行库的编译器。

    CXX_COMPILER 留空的语义是"由构建工具自己决定用哪个编译器"，不是"没有编译
    器"——拿空串去跑 `-print-file-name` 只会得到一句"找不到 ASan 运行库"，而
    机器上其实装着（实测）。这里按 显式配置 → 构建工具的缺省 → c++ 的顺序给候选。
    """
    found = []
    explicit = getattr(config, "cxx_compiler", "") or ""
    if explicit:
        found.append(explicit)
    tool = (getattr(config, "build_tool", "") or "").lower()
    inferred = {"cmake": "c++", "qmake": "c++"}.get(tool, "")
    if inferred and inferred not in found:
        found.append(inferred)
    for name in ("c++", "g++", "clang++"):
        if name not in found:
            found.append(name)
    return found


def runtime_library(compiler):
    """查询编译器使用的 asan 运行库绝对路径；查不到返回空串。

    用 `<编译器> -print-file-name=libasan.so` 而不是猜 /usr/lib：多版本
    gcc、交叉编译与自定义安装前缀下，只有编译器自己知道运行库在哪。找不到
    时编译器会把文件名原样打回，因此还要确认它是绝对路径且真实存在。
    """
    if not compiler:
        return ""
    status, captured, _ = tools.capture(
        [compiler, "-print-file-name=" + RUNTIME_NAME])
    if status != 0:
        return ""
    lines = [line.strip() for line in output.decode(captured).splitlines()
             if line.strip()]
    if not lines:
        return ""
    path = lines[0]
    if not os.path.isabs(path) or not os.path.isfile(path):
        return ""
    return path


def _set_option(text, key, value):
    """在冒号分隔的选项串里替换或追加 key=value。

    使用者可能已在环境里写了别的选项（如 abort_on_error），整体覆盖会把
    它们悄悄丢掉，因此只动指定的那一项。
    """
    prefix = key + "="
    parts = [part for part in (text or "").split(":") if part]
    kept = [part for part in parts if not part.startswith(prefix)]
    kept.append(prefix + value)
    return ":".join(kept)


def environment(config, channel, library):
    """一个通道的子进程环境。

    基础环境与内存检查一致（MEMCHECK_ENV，缺省继承 TEST_ENV），在此之上：

    - LD_PRELOAD 前置 asan 运行库，已有的项保留在后面：环境注入库会抢
      DT_NEEDED 首位，不前置时拦截器装不上。
    - 通道 A 关掉泄漏扫描，只抓发生当场的内存错误。
    - 通道 B 打开泄漏扫描，并把抑制清单写进 LSAN_OPTIONS。
    """
    overrides = getattr(config, "memcheck_env", None)
    if overrides is None:
        overrides = getattr(config, "test_env", None)
    env = tools.child_env(overrides)
    existing = env.get("LD_PRELOAD", "").strip()
    # 已有的注入项留在后面，只保证 asan 排在首位
    env["LD_PRELOAD"] = library + " " + existing if existing else library
    env["ASAN_OPTIONS"] = _set_option(
        env.get("ASAN_OPTIONS", ""), "detect_leaks",
        "1" if channel == CHANNEL_LEAK else "0")
    if channel == CHANNEL_LEAK:
        env["LSAN_OPTIONS"] = _set_option(
            env.get("LSAN_OPTIONS", ""), "suppressions",
            config.asan_suppressions)
    return env


def require_suppressions(config):
    """通道 B 的抑制清单必须存在，返回 (路径, 错误码, 说明)。

    没有清单就裸跑，Qt 对象树所有权产生的存量"泄漏"会全部报成域内问题，
    通道 B 当场失去意义。因此这里直接终止，不接受"缺清单继续跑"。
    """
    path = getattr(config, "asan_suppressions", "") or ""
    if not path:
        return "", errors.ASAN_SUPPRESSIONS_MISSING, (
            "asan 路线的泄漏通道缺少抑制清单，请配置 ASAN_SUPPRESSIONS")
    if not os.path.isfile(path):
        return path, errors.ASAN_SUPPRESSIONS_MISSING, (
            "泄漏抑制清单不存在: " + path)
    return path, errors.OK, ""


def shadow_build(config, build_type, jobs=0):
    """在影子目录里编译测试工程（带 asan 注入），返回 (目录, 错误码)。

    只编译测试构建文件，主产物不动：注入只该影响这次检查用的二进制，日常
    跑一次不该把合入门禁用的主构建树也换成带净化器的版本。
    """
    if not os.path.isfile(config.test_build_file):
        output.err("构建文件不存在: " + config.test_build_file)
        return "", errors.BUILD_FILE_MISSING
    binary = build.build_tool_bin(config)
    if not binary:
        # 机器上没装构建工具是环境缺件：标不可用让上层跳过，而不是判红
        output.err("找不到构建工具 {}，可用 BUILD_TOOL_BIN 指定".format(
            config.build_tool))
        return "", errors.STAGE_UNAVAILABLE
    directory = build_dir(config)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    configure = configure_command(config, binary, config.test_build_file,
                                  build_type)
    if tools.run_with_diagnostics(configure, directory) != 0:
        return directory, errors.ASAN_BUILD_FAILED
    compile_command = build.build_command(config, binary, directory, jobs)
    if tools.run_with_diagnostics(compile_command, directory) != 0:
        return directory, errors.ASAN_BUILD_FAILED
    return directory, errors.OK


def channel_command(config, channel):
    """一个通道要跑的命令。

    两个通道用同一个二进制：通道 B 只在二进制之后插入 asan_leak_args
    （如 --no-gui），其余参数与通道 A 相同。
    """
    parts = command(config)
    if not parts:
        return []
    extra = list(getattr(config, "asan_leak_args", None) or ())
    if channel != CHANNEL_LEAK:
        extra = []
    return [parts[0]] + extra + list(parts[1:])


def run_channel(config, channel, library, workdir):
    """跑一个通道，返回 (退出码, 输出文本, 错误码, 说明)。"""
    target = channel_command(config, channel)
    if not target:
        return 0, "", errors.COMMAND_UNRUNNABLE, "asan 路线的测试指令为空"
    output.out("$ " + " ".join(str(part) for part in target))
    status, captured, _ = tools.capture(
        target, workdir, merge=True,
        env=environment(config, channel, library))
    text = output.decode(captured)
    if text.strip():
        stage.noise(config, "memcheck", text.rstrip())
    return status, text, errors.OK, ""


def memory_marks(text, marks):
    """输出里命中的内存指纹，按清单顺序去重返回。"""
    found = []
    for mark in marks or ():
        if mark and mark in text and mark not in found:
            found.append(mark)
    return found


def mark_lines(text, marks, limit=10):
    """含内存指纹的输出行，去重后最多取 limit 行。

    指纹本身只说明"有内存问题"，把它所在的那一行提出来当诊断，使用者才
    不必翻全量日志去找是哪一次访问出的错。
    """
    found = []
    for line in (text or "").splitlines():
        piece = line.strip()
        if not piece or piece in found:
            continue
        if any(mark and mark in piece for mark in marks or ()):
            found.append(piece)
            if len(found) >= limit:
                break
    return found


def judge(config, checklist, text, status):
    """判定一个通道的输出，返回 Judge。

    判定顺序是固定的：

    1. 内存指纹命中即失败，且不再看清单——业务用例可以带病登记，内存错误
       永远不可豁免；
    2. 逐例失败逐个查清单，未登记的判失败：FAIL! 用例集合必须是清单的子集，
       漏登记一个即判负；
    3. 没有逐例结果却非零退出，无法归属到具体用例，同样判失败——进程在
       asan 之外的信号下死掉也是真问题，不能因为清单里没有它就放过。

    退出码为 0 但有逐例失败时照样判失败：runner 静默漏报是最危险的一类。
    反过来，业务失败全部命中清单且没有内存指纹时算通过——xfail 的意义就是
    让存量业务问题不挡门禁。
    """
    failures, totals, code, message = testreport.extract(
        text, config.test_report_adapter, config.test_fail_pattern,
        config.test_total_pattern)
    if code != errors.OK:
        return Judge(code, [], [], [], None, message)
    marks = memory_marks(text, getattr(config, "asan_error_marks", None))
    unknown, known = [], []
    for item in failures:
        if checklist.test(item.case) is not None:
            known.append(item)
        else:
            unknown.append(item)
    if marks or unknown or (status != 0 and not failures):
        return Judge(errors.MEMCHECK_FAILED, unknown, known, marks, totals, "")
    return Judge(errors.OK, unknown, known, marks, totals, "")


def report(config, checklist, text, status, channel):
    """判定一个通道并把结果发成协议行，返回错误码。"""
    label = CHANNEL_LABELS.get(channel, channel)
    result = judge(config, checklist, text, status)
    if result.message:
        output.err(result.message)
        output.out(protocol.summary("{}: {}".format(label, result.message)))
        return result.code
    for line in mark_lines(text, result.marks):
        output.out(protocol.diag("error", "", 0, 0,
                                 "{} 内存指纹: {}".format(label, line)))
    if result.marks:
        output.out(protocol.summary(
            "{}: 内存指纹 {} 处（{}），不算 xfail 豁免".format(
                label, len(result.marks), "、".join(result.marks))))
    for item in result.known:
        output.out(protocol.summary("已登记 (xfail): {} {}".format(
            item.case, item.reason)))
    for item in result.failures:
        output.out(protocol.fail(item.case, item.reason))
    if result.totals is not None:
        output.out(protocol.summary("{}: {}".format(
            label, testreport.describe(
                result.totals, len(result.known) + len(result.failures)))))
    if result.known:
        output.out(protocol.summary("{}: 已登记 {} 例 (xfail)".format(
            label, len(result.known))))
    if (not result.failures and not result.marks and status != 0
            and result.code != errors.OK):
        output.out(protocol.summary(
            "{}: 进程非零退出（{}）且没有逐例失败信息，无法归属到已登记项"
            .format(label, status)))
    return result.code


def skipped_target(config):
    """命中的测试目标整体跳过内存检查，返回命中的名字。

    与 valgrind 路线共用 MEMCHECK_SKIP_TARGETS：有的目标在净化器下必然
    误报，逐个写抑制不划算。目标名取 asan 路线实际要跑的那个程序。
    """
    parts = command(config)
    program = parts[0] if parts else ""
    name = os.path.basename(program)
    for item in getattr(config, "memcheck_skip_targets", None) or ():
        if item and (item == name or item in program):
            return item
    return ""


def run(config, args):
    """asan 路线：影子构建加双通道判定，返回错误码。"""
    if not getattr(config, "test_command", None):
        output.err("配置里没写 TEST_COMMAND，跳过内存检查")
        return errors.STAGE_UNAVAILABLE
    # 这里**不**用 TEST_WORKDIR 做前置校验：asan 路线跑的是影子树里的二进制，
    # 普通测试树在 clean 之后本来就是空的。校验推迟到影子构建完成之后，
    # 对象是真正要跑的那个程序
    command_line = command(config)
    if not command_line:
        output.err("未能从 MEMCHECK_COMMAND 推导出影子指令（当前推导指向 {}，"
                   "影子树在 {}）：请显式写 ASAN_COMMAND".format(
                       config.build_dir, build_dir(config)))
        return errors.CONFIG_MISSING_ITEM
    hit = skipped_target(config)
    if hit:
        # 标 skipped 而不是 ok：跳过不等于通过
        output.out(protocol.summary(
            "memcheck skipped: 目标命中 MEMCHECK_SKIP_TARGETS（{}），未做内存检查"
            .format(hit)))
        return errors.OK
    # 抑制清单先查：缺清单必须当场终止，既不编译也不跑二进制。通道 B 是
    # 泄漏判定的唯一入口，没有清单的裸跑只会把存量"泄漏"全报成域内问题。
    _, code, message = require_suppressions(config)
    if code != errors.OK:
        output.err(message)
        output.out(protocol.summary(message))
        return code
    library, tried = find_runtime_library(config)
    if not library:
        # 缺 asan 运行库是环境缺件：上层标 skipped 并说明，不判红。
        # 报错里列出试过的完整命令——只给一个空位没人读得出来是"没写编译器"
        output.err("找不到 ASan 运行库（{}）。试过: {}。"
                   "请安装 libasan、在 CXX_COMPILER 里指明编译器，"
                   "或改用 MEMCHECK_TOOL=valgrind".format(
                       RUNTIME_NAME, "；".join(tried)))
        return errors.ASAN_TOOL_MISSING
    # 阶段脚本统一收到的共享参数；缺省值让直接调用本函数的测试也能跑
    _, code = shadow_build(config, getattr(args, "build_type", "debug"),
                           getattr(args, "jobs", 0))
    if code != errors.OK:
        if code == errors.ASAN_BUILD_FAILED:
            output.err("带 ASan 的影子构建失败（目录 {}）".format(
                build_dir(config)))
        else:
            output.err("影子构建未完成")
        return code
    program = str(command_line[0])
    if not os.path.isfile(program):
        # 影子构建跑完了程序还不在：报出**真正要跑的那个路径**与实际尝试过的
        # 命令，别让人对着"No rule to make target"猜
        output.err("影子树里没有要跑的程序: {}（生效的 asan 指令: {}；"
                   "影子树在 {}）".format(
                       program, " ".join(str(part) for part in command_line),
                       build_dir(config)))
        return errors.WORKDIR_MISSING
    checklist, code, message = xfail.load_checklist(config)
    if code != errors.OK:
        output.err(message)
        output.out(protocol.summary(message))
        return code
    final = errors.OK
    for channel in (CHANNEL_ERROR, CHANNEL_LEAK):
        status, text, code, message = run_channel(config, channel, library,
                                                  workdir)
        if code != errors.OK:
            output.err(message)
            return code
        code = report(config, checklist, text, status, channel)
        if code != errors.OK:
            final = code
            if errors.kind(code) != errors.KIND_MEMORY:
                # 适配器名或正则这类配置问题：换通道也是同一个错，当场终止
                return code
    # 清单腐化检查覆盖 test 类；asan 路线不登记 memcheck 类，因此不并入它
    code, notes = xfail.verdict(checklist, ("test",),
                                getattr(config, "xfail_strict", False))
    for note in notes:
        output.out(protocol.summary(note))
    if code != errors.OK:
        output.err("xfail 清单有已修复或已到期的登记项，请清理后重跑")
        output.out(protocol.summary(
            "xfail 清单有已修复或已到期的登记项，请清理后重跑"))
        return code
    return final
