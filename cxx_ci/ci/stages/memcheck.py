#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""执行测试指令并判定内存问题，按 MEMCHECK_TOOL 分派两条路线。

valgrind 路线出结构化报告，工具按调用栈把每条错误归到项目、忽略集或系统库。
这样新项目复制配置即可用，不必先学会写 valgrind 抑制 DSL；抑制文件降级为
可选精修。

asan 路线是影子构建加双通道：先抓发生当场的内存错误，再只对无 GUI 的逻辑
用例做泄漏扫描。它比 valgrind 快一个数量级，适合日常；归因式的深度扫描仍由
valgrind 路线负责合入门禁。两条路线的判定方式不同，asan 一侧实现在
lib/asan.py 里。契约见 ci.py 的 docstring，共享参数见 lib/。

被测程序含定时器或异步事件循环时，valgrind 的泄漏分类本身就会漂移——
"堆上是否仍残留指向该块的指针"随时序变化，同一份二进制两次运行的域内条数
可以差两三成。判定口径因此有两道收敛：把漂移类别移出失败判定（见
MEMCHECK_IGNORE_KINDS），以及多次运行取交集（MEMCHECK_REPEAT）。
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from lib import (asan, cli, config as config_module, errors, output,
                 paths, platform, protocol,
                 stage, tools, valgrind, xfail)



def env_for(config):
    """内存检查子进程的环境，缺省继承 TEST_ENV。"""
    overrides = config.memcheck_env
    if overrides is None:
        overrides = config.test_env
    return tools.child_env(overrides)


def ignore_prefixes():
    """忽略前缀：固定系统库目录。"""
    return list(valgrind.SYSTEM_PREFIXES)


def project_roots(config):
    """归因用的项目根。

    被测程序本身是项目的产物：它通常位于构建目录里，而构建目录在
    SCOPE.excluded 中，只看源码范围会把它的帧判成域外而放过真实泄漏。
    """
    roots, _ = paths.scope_roots(config)
    program = config.memcheck_command[0] if config.memcheck_command else ""
    if program:
        roots = list(roots) + [os.path.dirname(os.path.abspath(program))]
    return roots


def attribute_report(config, report):
    """对一份报告逐条归因，返回 verdicts。"""
    outside = (valgrind.OUTSIDE_FAIL if config.memcheck_scope == "all"
               else valgrind.OUTSIDE_IGNORE)
    roots = project_roots(config)
    return [valgrind.attribute(
        item, roots, config.memcheck_ignore_objects,
        ignore_prefixes(), outside, config.memcheck_ignore_symbols)
        for item in report]


def inside_signatures(report, verdicts):
    """一次运行里判为域内的条目签名集合。"""
    found = set()
    for index, item in enumerate(report):
        if verdicts[index][0] == valgrind.VERDICT_FAILURE:
            found.add(valgrind.signature(item))
    return found


def demote(verdict, signature, common_signatures, reason):
    """把"重复运行没能复现"的域内判定降级成忽略。

    交集语义就落在这里：只有每次都判域内的条目才算失败。降级保留归属帧与
    理由，摘要里照常计数，不是静默丢弃。
    """
    if verdict[0] != valgrind.VERDICT_FAILURE or signature in common_signatures:
        return verdict
    return (valgrind.VERDICT_IGNORED, verdict[1], reason)


def judge(config, reports):
    """汇总各次运行并归因，返回 (错误码, Summary, 域内清单, xfail 清单, 复现行)。

    reports 是每次运行的错误列表，最后一次用作展示与明细。取交集而不是取
    并集：门禁要的是"每次都成立的失败"，单次快照会把漂移当成回归。
    """
    checklist, code, message = xfail.load_checklist(config)
    if code != errors.OK:
        output.err(message)
        output.out(protocol.summary(message))
        return code, None, [], [], ""
    counts, runs, last = [], [], None
    for report in reports:
        verdicts = attribute_report(config, report)
        runs.append(inside_signatures(report, verdicts))
        counts.append(len(runs[-1]))
        last = (report, verdicts)
    common_signatures = set(runs[0]) if runs else set()
    for item in runs[1:]:
        common_signatures &= item
    report, verdicts = last
    note = ""
    if len(runs) > 1:
        # 计数按"签名"（调用栈）而不是错误条数：跨运行可比的是栈，不是条数
        note = "重复 {} 次域内签名 {} → 交集 {}".format(
            len(runs), " / ".join(str(value) for value in counts),
            len(common_signatures))
        verdicts = [demote(verdict, valgrind.signature(item),
                           common_signatures, "重复运行未复现，未取交集")
                    for item, verdict in zip(report, verdicts)]
    inside, known = [], []
    for item, verdict in zip(report, verdicts):
        if verdict[0] != valgrind.VERDICT_FAILURE:
            continue
        frame = verdict[1]
        where = frame.obj if frame else "归属不明"
        entry = checklist.symbol(frame.function if frame is not None else "",
                                 valgrind.signature(item))
        if entry is not None:
            known.append("{} {} <- {} (xfail: {})".format(
                item.kind, item.text, where, xfail.describe(entry)))
            continue
        inside.append("{} {} <- {}".format(item.kind, item.text, where))
    summary = valgrind.summarize(report, verdicts, config.memcheck_ignore_kinds)
    code, notes = xfail.verdict(
        checklist, ("memcheck",),
        getattr(config, "xfail_strict", False))
    for line in notes:
        output.out(protocol.summary(line))
    if code != errors.OK:
        output.err("xfail 清单有已修复或已到期的登记项，请清理后重跑")
        output.out(protocol.summary(
            "xfail 清单有已修复或已到期的登记项，请清理后重跑"))
    return (errors.MEMCHECK_FAILED if inside else code), summary, inside, known, note


def report_paths(config, repeat):
    """各次运行的结构化报告路径。"""
    paths = [os.path.join(config.log_dir, "memcheck.xml")]
    for index in range(2, repeat + 1):
        paths.append(os.path.join(config.log_dir,
                                  "memcheck-{}.xml".format(index)))
    return paths


def skipped_target(config):
    """命中的测试目标整体跳过内存检查，返回命中的名字。

    有些目标（如自带线程池或第三方后端的用例）在 valgrind 下必然误报，
    逐个写抑制不划算，按目标整体跳过更清楚——汇总里会标 skipped，不会
    让使用者以为它通过了。
    """
    program = config.memcheck_command[0] if config.memcheck_command else ""
    name = os.path.basename(program)
    for item in config.memcheck_skip_targets:
        if item and (item == name or item in program):
            return item
    return ""


def collect(config, binary, workdir, repeat):
    """按 MEMCHECK_REPEAT 跑若干次，返回 (报告列表, 错误码, 说明)。

    报告读不出来是工具链故障，与"确实有内存问题"必须分开：混在一个错误码
    里会让使用者分不清该修代码还是修环境。
    """
    reports = []
    for path in report_paths(config, repeat):
        command = tools.memcheck_command(binary, config, path)
        output.out("$ " + " ".join(str(part) for part in command))
        status, out, _ = tools.capture(command, workdir, merge=True,
                                        env=env_for(config))
        text = output.decode(out)
        if text.strip():
            stage.noise(config, "memcheck", text.rstrip())
        if not os.path.isfile(path):
            return None, errors.MEMCHECK_FAILED, (
                "valgrind 未产出结构化报告: " + path)
        report, code, message = valgrind.parse_xml(path)
        if code != errors.OK:
            return None, code, message
        reports.append(report)
        if status != 0:
            output.err("valgrind 未正常结束，退出码 " + str(status))
    return reports, errors.OK, ""


def run(config, args):
    """内存检查入口：按 MEMCHECK_TOOL 分派两条路线。"""
    # 平台能力先判：valgrind 与 LSan 都是 GNU/Linux 专属。macOS/BSD 上标 skipped
    # 并说明原因——不是"跑过了"，也不是"整条流水线全红"
    level, reason = platform.detect()
    if not platform.memory_check_available(level):
        output.out(protocol.summary(
            "memcheck skipped: 本平台不做内存检查（{}）".format(reason)))
        output.err("本平台不做内存检查：{}。{}".format(reason,
                                                     platform.SUPPORT_LINE))
        return errors.STAGE_UNAVAILABLE
    if config.memcheck_tool == "asan":
        # asan 路线的前置校验在 asan.run 里按影子指令做，这里不再用 TEST_WORKDIR
        # 卡它：那个目录与本路线无关，卡它会造成"配置看着没错却 0.1s 终止"
        if not config.memcheck_command:
            output.err("配置里没写 MEMCHECK_COMMAND（也没写 TEST_COMMAND），"
                       "跳过内存检查")
            return errors.STAGE_UNAVAILABLE
        return asan.run(config, args)
    return valgrind_run(config, args)


def valgrind_run(config, args):
    """valgrind 路线：取结构化报告、归因、按 xfail 清单判定。"""
    if not config.memcheck_command:
        output.err("配置里没写 MEMCHECK_COMMAND（也没写 TEST_COMMAND），"
                   "跳过内存检查")
        return errors.STAGE_UNAVAILABLE
    # 指向 ctest/make 这类驱动时，命令名的那个"程序"不是被测程序：valgrind 只会
    # 检查驱动本身，而驱动通常也不在构建目录里。照程序去找会报"可执行文件不存在"
    # ——报的是症状，真正要改的是 MEMCHECK_COMMAND。这里按"没有可检查的目标"处理：
    # 标 [SKIPPED] 并说清该填什么，跳过不等于通过
    driver = config_module.memcheck_driver(config)
    if driver:
        output.out(protocol.summary(
            "memcheck skipped: MEMCHECK_COMMAND 指向 {} 这类驱动，"
            "valgrind 只会检查驱动本身".format(driver)))
        output.err("MEMCHECK_COMMAND 指向 {} 这类驱动，valgrind 检查不到被测程序；"
                   "请在配置里把它改指具体的测试程序，例如 "
                   "TEST_BUILD_DIR / \"tests\" / \"demo_test\"".format(driver))
        return errors.STAGE_UNAVAILABLE
    workdir, code, message = tools.require_test_workdir(config)
    if code != errors.OK:
        output.err(message)
        return code
    program = Path(str(config.memcheck_command[0]))
    if not program.is_absolute():
        # --build-dir 是共享参数，build 阶段认它；这里不认就会去配置里的旧目录
        # 找产物，判卷跑的是上一次构建留下的可执行文件
        base = getattr(args, "build_dir", "") or config.build_dir
        program = Path(str(base)) / program
    if not program.exists():
        # 这类错最容易在接入新工程时踩到：把别家的配置抄过来，可执行文件名还是
        # 人家的。等到 valgrind 跑起来才炸，报出来的是"未产出结构化报告"，
        # 跟真正的原因隔了好几层，所以在这里一次说清
        output.err("内存检查的可执行文件不存在: {}（先跑一次 build；"
                   "若名字不对，改 MEMCHECK_COMMAND）".format(program))
        return errors.WORKDIR_MISSING
    hit = skipped_target(config)
    if hit:
        # 标 skipped 而不是 ok：跳过不等于通过
        output.out(protocol.summary(
            "memcheck skipped: 目标命中 MEMCHECK_SKIP_TARGETS（{}），未做内存检查"
            .format(hit)))
        return errors.OK
    binary, code, message = tools.find_tool(("valgrind",))
    if code != errors.OK:
        # 缺 valgrind 是环境缺件：上层标 skipped 并说明，不判红
        output.err("找不到 valgrind，请先安装")
        return errors.STAGE_UNAVAILABLE
    if not config.memcheck_suppressions:
        output.out("未配置抑制文件，按归因规则判定")
    elif not os.path.isfile(config.memcheck_suppressions):
        output.err("抑制文件不存在，按归因规则判定: "
                   + config.memcheck_suppressions)
    if config.log_dir and not os.path.isdir(config.log_dir):
        os.makedirs(config.log_dir)
    repeat = max(1, config.memcheck_repeat)
    if repeat > 1:
        output.out(protocol.summary(
            "重复 {} 次运行，只有每次都判域内的条目才算失败".format(repeat)))
    reports, code, message = collect(config, binary, workdir, repeat)
    if reports is None:
        output.err(message)
        return code
    code, summary, inside, known, note = judge(config, reports)
    if summary is None:
        return code
    paths = report_paths(config, repeat)
    if repeat == 1:
        detail = paths[0]
    else:
        detail = "{}（共 {} 份，另见同目录 memcheck-*.xml）".format(paths[0],
                                                              repeat)
    # 结论行与聚合行各占一条协议行：拼成一整段会让驱动层只取到第一行，
    # 使用者看到的就只剩一个无从下手的"域内 N 处"
    output.out(protocol.summary(
        (note + " | " if note else "") + summary.headline + " | 明细: " + detail))
    if summary.kinds:
        output.out(protocol.summary(summary.kinds))
    if summary.top:
        output.out(protocol.summary(summary.top))
    for line in known:
        # 与 run_test 同理：#sum 给人看，#xfail 给机器读（报告据此打"已知"
        # 标记，而不是自己去重判一遍）
        output.out(protocol.summary("已登记 (xfail): " + line))
        output.out(protocol.known(line, "memcheck"))
    for line in inside:
        output.out(protocol.diag("error", "", 0, 0, line))
    return code


if __name__ == "__main__":
    sys.exit(cli.stage_main(__doc__, run))
