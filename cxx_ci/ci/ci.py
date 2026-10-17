#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按序执行基础流程的各阶段，屏蔽正常输出，仅暴露错误与警告。

只做基础流程：clean、build、build_test、run_test、memcheck。风格整理与
代码排版不在这里——它们是 standardize.py 的职责，那套脚本带 --apply 与
一次性切换，能改写工作区。门禁不该具备改写能力，因此这里连它们的脚本名
都不出现，`--only style` 会明确指路而不是悄悄跑一个可写脚本。

阶段脚本位于 scripts/，契约：
  - 以 `python3 scripts/<阶段>.py` 调用，并透传共享参数
  - 退出码 0 表示成功，非 0 表示失败
  - stdout 为常规日志，stderr 为诊断信息
  - 兼容 Python 3.4，本脚本与这些阶段脚本共用此基线
  - 输出中文前调用 output.ensure_utf8_streams()，否则非 UTF-8 区域设置下会报错

阶段的执行、落盘与展示都在 common/stage.py 里，standardize.py 走同一份
实现——两个入口各写一套展示，行为必然分叉。

子命令 `doctor` 只读自检，回答"现在生效的是什么、上次为什么红、下一步跑什么"。
配置项的说明与示例见仓库根的 config-example/ci_config.py。

共享参数见 lib/。
"""

import argparse
import os
import sys

if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")

from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib import (cli, config as config_module, doctor,  # noqa: E402
                 platform)
from lib import diagnostics as diagnostics_module  # noqa: E402
from lib import errors, output, stage, version  # noqa: E402

# 基础流程。风格与排版属于另一个项目，见仓库根的 CONTRACT.md。
STAGES = ("clean", "build", "build_test", "run_test", "memcheck")


def split_names(value):
    """把逗号分隔的阶段名切成列表。"""
    return [part.strip() for part in value.split(",") if part.strip()]


def default_stages(config):
    """默认阶段集合。

    项目可用 STEPS_DEFAULT 把某些阶段排除在默认全量之外（例如内存检查只在
    合入门禁里跑）；`--only` 仍然能显式点名它们。
    """
    configured = getattr(config, "steps_default", None)
    if not configured:
        return list(STAGES)
    return [name for name in STAGES if name in configured]


def select_stages(only, skip, defaults=None):
    """按 --only / --skip 过滤阶段，保持固定顺序。

    返回 (阶段列表, 未知阶段列表)。
    """
    chosen = only or list(defaults if defaults else STAGES)
    unknown = [name for name in set(chosen + skip) if name not in STAGES]
    stages = [name for name in STAGES if name in chosen and name not in skip]
    return stages, sorted(unknown)


def report(records, quiet, skipped=()):
    """打印各阶段耗时与总计；quiet 下成功时省略。"""
    if not records:
        return
    if quiet and all(result.code == 0 for result in records):
        return
    output.out("汇总: " + " | ".join(
        "{}{} {:.1f}s".format(result.name, "(跳过)" if result.name in skipped else "",
                             result.elapsed) for result in records))
    if skipped:
        # 原因逐条已经打过，这里只说结论：跳过既不是通过也不是失败
        output.out("未执行: " + "、".join(skipped)
                   + "（原因见上，这些阶段的检查没有生效，跳过不等于通过）")
    output.out("总计 {:.1f}s".format(sum(result.elapsed for result in records)))


def build_parser(config):
    """构造命令行解析器。"""
    parser = cli.make_parser(__doc__, config)
    parser.add_argument(
        "--level",
        choices=("quiet", "normal", "verbose"),
        default="normal",
        help="quiet 仅失败时输出，normal 附带警告行，verbose 全部透传",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="单个阶段超时秒数，0 表示不限",
    )
    parser.add_argument(
        "--only",
        type=split_names,
        default=[],
        help="只跑指定阶段，逗号分隔",
    )
    parser.add_argument(
        "--skip",
        type=split_names,
        default=[],
        help="跳过指定阶段，逗号分隔",
    )
    parser.add_argument(
        "--no-clean-after",
        action="store_true",
        help="结束后不清理，保留构建产物",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="打印版本与契约版本后退出（不需要配置）",
    )
    return parser


def main():
    output.ensure_utf8_streams()
    # 平台守卫放在最前：本工具依赖 POSIX 语义（valgrind / LSan / LC_ALL=C 的输出
    # 约定 / 软链），在不支持的系统上与其一路 [SKIPPED] 地半死不活，不如当场说清
    # guard 返回的是**退出状态**（8 位），要折算成 16 位错误码再返回：直接返回
    # 裸 2 会被 status() 折成 0，"本平台不支持"就变成了成功退出
    code = platform.guard()
    if code:
        return errors.from_status(code)
    argv = sys.argv[1:]
    # --version 必须在配置加载之前：没有配置的工程也要能问"这是哪一版"。
    # 形状与 cxx-standardize --version 一致（见 lib/version.py）。
    if "--version" in argv:
        output.out(version.version_line())
        return errors.OK
    # --help/-h/--version 也先短路：它们问的是"这个工具怎么用"，与有没有配置
    # 无关。放到配置加载之后时，一个还没接入的工程敲 --help 得到的是
    # "找不到配置"（退出 2，含义是"缺外部依赖"），答非所问
    if any(item in ("-h", "--help", "--version") for item in argv):
        return cli.help_or_version(argv, build_parser)
    # doctor 自行载配置（配置建不起来时也要能报环境与原因），不在这里加载
    if argv[:1] == ["doctor"]:
        return doctor.main(argv[1:])
    # --config 先落到环境变量上：配置的位置决定解析器（帮助文本与缺省值），
    # 而解析器要晚一步才建；阶段脚本是另起的进程，也靠这个变量拿到同一个配置
    path = cli.prescan_config()
    if path:
        os.environ[config_module.CONFIG_ENV] = path
    config, code, message = cli.load_or_report(path)
    if config is None:
        return code
    args = build_parser(config).parse_args()

    # 互斥：两份门禁同时跑同一个工程会互相破坏——一份的 clean 清掉另一份正在用的
    # 构建树（随后 configure 失败，好工程判红），阶段日志被两份交替追加成"失败+
    # 成功"的混合文件，memcheck 的结构化报告也会被写成两份文档拼在一起
    holder, why = platform.acquire_lock(getattr(config, "build_dir", ""))
    if holder is None and why:
        output.err(why)
        return errors.ENV_BUSY
    platform.install_term_handler()
    try:
        return run_stages(config, args)
    finally:
        platform.release_lock(holder)


def run_stages(config, args):
    """跑一轮门禁（互斥锁已由调用方持有时才会走到这里）。"""
    # 高危项就地提醒：只打印、不判红，且只在会出声的级别下打印——quiet 的语义
    # 是"只在失败时出声"，提醒混进去会破坏这条契约
    if args.level != "quiet":
        for note in config_module.advise(config):
            output.err(note)

    stages, unknown = select_stages(args.only, args.skip,
                                   default_stages(config))
    if unknown:
        output.err("未知阶段: " + ", ".join(unknown))
        return errors.UNKNOWN_STAGE
    if not stages:
        # 筛完一个都不剩（例如 --only build --skip build）：什么都不做就
        # 退出 0 等于告诉使用者"跑过了、没问题"，而实际上一段都没跑
        requested = args.only or list(default_stages(config))
        output.err("没有可执行的阶段: 选中 {} 个，被 --skip 排除了 {} 个"
                   "（选中: {}；跳过: {}）".format(
                       len(requested), len(args.skip),
                       "、".join(requested), "、".join(args.skip) or "无"))
        return errors.CONFIG_BAD_VALUE
    # 末尾清理只在跑完整流程且全部通过后执行；中途失败会提前返回，产物得以保留
    if not args.only and not args.no_clean_after:
        stages.append("clean")

    extra = cli.forward_args(args)
    records, skipped = [], []
    for name in stages:
        result = stage.run_stage(config, name, ROOT / "stages" / (name + ".py"), extra,
                                args.level == "verbose", args.timeout)
        try:
            stage.emit(config, result, args.level)
        except diagnostics_module.SlowPattern:
            # 配置里的忽略正则回溯爆炸。阶段本身可能已经跑完了，但结论没法
            # 可信地归并——把它报成配置错误并停下，比挂在那里等人猜要好
            output.err("DIAGNOSTIC_IGNORE 里有会回溯爆炸的正则：它没有在 "
                       "{} 秒内跑完。请改写这条正则（避免 (a+)+ 这类嵌套量词）"
                       "或去掉它".format(diagnostics_module.IGNORE_PATTERN_SECONDS))
            return errors.CONFIG_BAD_VALUE
        records.append(result)
        if errors.kind(result.code) == errors.KIND_SKIPPED:
            # 机器上缺 clang 或编译数据库：标 skipped 并说明原因。
            # 跳过不等于通过，因此既不改判定为红，也不假装它跑过了。
            skipped.append(name)
            output.out("阶段 {} 未执行（{}），本阶段的检查未生效".format(
                name, stage.first_line(result.err)
                or stage.first_line(result.out) or "未说明原因"))
            continue
        if result.code != 0:
            output.err("阶段 {} 未通过，产物保留以便排查".format(name))
            report(records, False)
            return result.code
    report(records, args.level == "quiet", skipped)
    return errors.OK


if __name__ == "__main__":
    sys.exit(cli.run_main(main, "已中断，产物保留"))
