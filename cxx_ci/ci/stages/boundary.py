#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部面边界检查：detail:: 归属与头文件宏守卫。

只读检查，排在构建之前——纪律违规没必要再花构建时间。`--fix` 只补/改宏守卫；
detail:: 越界不提供自动修，出现它意味着有人绕过门面直接调内部实现，只能人工
改回公开入口。
契约见 ci.py 的 docstring，共享参数见 lib/。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from lib import boundary, cli, errors, output


def add_args(parser):
    """boundary 独有参数。"""
    parser.add_argument(
        "--fix",
        action="store_true",
        help="补齐或改正头文件宏守卫（detail:: 越界不自动修）",
    )


def run(config, args):
    # 取全部源文件：detail:: 越界可能出现在 .cpp 里，宏守卫则只对头文件有意义
    files = boundary.source_files(config.boundary_dirs, boundary.SOURCE_EXTS)
    if not files:
        output.out("没有需要检查的文件")
        return errors.OK
    code = errors.OK

    leaks = boundary.find_detail_leaks(files, config.detail_dirs,
                                       config.detail_mark)
    if leaks:
        # 越界调用内部实现只能人工改回公开入口，因此这里没有自动修
        output.err("{} 处 {} 越出模块目录:".format(len(leaks),
                                                 config.detail_mark))
        for path, number, line in leaks:
            output.out("    {}:{}: {}".format(path, number, line))
        # 纯拒绝会让人停在"知道错了但不知道该怎么配"，把动作给出来
        output.out("若本工程的 {} 就是某个目录的内部实现层，"
                   "把该目录写进 DETAIL_DIRS".format(config.detail_mark))
        code = errors.BOUNDARY_DETAIL_LEAK
    else:
        output.out("{} 未越出模块目录".format(config.detail_mark))

    report = boundary.guard_report(files, config.root, config.guard_exts)
    if report and getattr(args, "fix", False):
        changed = boundary.fix_guards(report, config)
        output.out("已修正 {} 个头文件的宏守卫".format(len(changed)))
        report = boundary.guard_report(files, config.root, config.guard_exts)
    if report:
        for note in boundary.describe(report):
            output.err(note)
        if not getattr(args, "fix", False):
            # 纯拒绝会让人停在"知道错了但不知道怎么改"，把动作给出来
            output.out("补齐或改正守卫: python3 " + os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "boundary.py")
                + " --fix")
        # 守卫名不符与残缺都是"守卫没起作用"，与缺失分开报，改法不同
        kinds = set(item[3] for item in report)
        if "missing" in kinds:
            code = errors.BOUNDARY_GUARD_MISSING
        elif "misnamed" in kinds:
            code = errors.BOUNDARY_GUARD_MISNAMED
        else:
            code = errors.BOUNDARY_GUARD_BROKEN
    else:
        output.out("头文件宏守卫齐备且命名正确")
    return code


if __name__ == "__main__":
    sys.exit(cli.stage_main(__doc__, run, add_args))
