#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在配置的工作目录内执行测试指令。

指令与工作目录取自 config/config.py 的 TEST_COMMAND 与 TEST_WORKDIR。
逐例结果由测试报告适配器解析后发成 #fail / #sum 协议行，驱动层据此判定
成败——这样即使 runner 退出码为 0，只要有逐例失败也不会被漏报。
契约见 ci.py 的 docstring，共享参数见 lib/。
"""

import os
import os
import sys

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from lib import cli, errors, output, protocol, testreport, tools, xfail



def env_for(config):
    """测试子进程的环境，叠加配置里的 TEST_ENV。"""
    return tools.child_env(config.test_env)


def report(text, config):
    """解析逐例结果并发协议行，返回 (新增失败数, 已登记数, 错误码)。

    已登记（xfail）的用例不计失败但仍回显并标注，未登记的新失败照判失败；
    登记项已修复则提示可移除，XFAIL_STRICT 下判红防腐化。
    """
    failures, totals, code, message = testreport.extract(
        text, config.test_report_adapter, config.test_fail_pattern,
        config.test_total_pattern)
    if code != errors.OK:
        output.err(message)
        output.out(protocol.summary(message))
        return 0, 0, code
    checklist, code, message = xfail.load_checklist(config)
    if code != errors.OK:
        output.err(message)
        output.out(protocol.summary(message))
        return len(failures), 0, code
    known, fresh = [], []
    for item in failures:
        (known if checklist.test(item.case) else fresh).append(item)
    # 已登记的走 #sum：它既不是失败也不是诊断，但必须回显，否则使用者
    # 看不见存量问题的规模；#fail 只留给真正需要修的那些
    for item in known:
        # 两条都发：#sum 给人看（终端要能一眼看到存量规模），#xfail 给机器
        # 读（报告据此打"已知"标记）。只发 #sum 的话，报告想标记"已知"就只能
        # 去解析摘要文本，或者自己重判一次——两条路都是"报告替终端判卷"
        output.out(protocol.summary("已登记 (xfail): {} {}".format(
            item.case, item.reason)))
        output.out(protocol.known(item.case, item.reason))
    for item in fresh:
        output.out(protocol.fail(item.case, item.reason))
    if totals is not None:
        output.out(protocol.summary(testreport.describe(totals, len(failures))))
    if known:
        output.out(protocol.summary("已登记 {} 例 (xfail)，新增失败 {} 例".format(
            len(known), len(fresh))))
    # 只有确实跑出了用例结果，"没命中"才能推断成"已修复"：runner 中途
    # 异常退出时一条结果都没有，此时报"可移除"纯属误导
    judged = totals is not None and totals.cases > 0
    code, notes = xfail.verdict(
        checklist, ("test",) if judged else (),
        getattr(config, "xfail_strict", False))
    for note in notes:
        output.out(protocol.summary(note))
    return len(fresh), len(known), code


def run(config, args):
    workdir, code, message = tools.require_test_workdir(config)
    if code != errors.OK:
        output.err(message)
        return code
    program = config.test_command[0]
    found, code, message = tools.find_tool((program,))
    if not (os.path.isfile(program) or found):
        output.err("测试指令不可执行: " + program)
        return errors.COMMAND_UNRUNNABLE
    # 命令行回显进日志，失败时便于复现
    output.out("$ " + " ".join(str(part) for part in config.test_command))
    status, out, _ = tools.capture(config.test_command, workdir, merge=True,
                                    env=env_for(config))
    text = output.decode(out)
    # 原始输出照常打印，日志里要能查到全量
    if text.strip():
        output.out(text.rstrip())
    failures, known, code = report(text, config)
    if code != errors.OK:
        # 登记项已修复却没删行，或清单本身写错：都不是"测试通过"
        return code
    if status != 0:
        if not failures and not known:
            # 没有逐例结果却非零退出，多半是进程异常终止
            output.out(protocol.summary("进程异常终止（无逐例结果）"))
        return errors.TEST_FAILED
    # 退出码为 0 但有逐例失败：静默漏报最危险的一类，必须判失败
    return errors.TEST_FAILED if failures else errors.OK


if __name__ == "__main__":
    sys.exit(cli.stage_main(__doc__, run))
