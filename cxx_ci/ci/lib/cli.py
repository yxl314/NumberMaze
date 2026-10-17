#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行参数与脚本入口。"""
import sys
if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")
import argparse
import os
from .version import CONTRACT_VERSION, __version__
from .output import err, ensure_utf8_streams, out
from .config import CONFIG_ENV, DEFAULT_BUILD_DIR, load_config
from .errors import INTERRUPTED, OK, status


def add_shared_args(parser, config=None):
    """向 parser 注入共享参数，返回同一个 parser。"""
    parser.add_argument(
        "--build-type",
        choices=("debug", "release"),
        default="debug",
        help="构建类型，缺省 debug",
    )
    parser.add_argument(
        "--build-dir",
        default=config.build_dir if config else DEFAULT_BUILD_DIR,
        help="构建目录，缺省取自 config/config.py",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=0,
        help="并行任务数，0 表示按 CPU 核数",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get(CONFIG_ENV, ""),
        help="项目配置的位置，缺省取环境变量 {} 或工具自带的 config/config.py"
             .format(CONFIG_ENV),
    )
    return parser

def make_parser(description, config=None):
    """构建带共享参数、保留原文换行的解析器。"""
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    return add_shared_args(parser, config)

def forward_args(args):
    """把共享参数还原成命令行片段，供 ci.py 透传给阶段脚本。"""
    forwarded = [
        "--build-type", args.build_type,
        "--build-dir", args.build_dir,
        "--jobs", str(args.jobs),
    ]
    if getattr(args, "config", ""):
        forwarded += ["--config", args.config]
    return forwarded


def prescan_config(argv=None):
    """在完整解析器之前先取出 --config。

    配置决定解析器本身（帮助文本里的缺省构建目录），因此必须先知道配置在哪。
    这里只认这一个选项，其余交给完整解析器。
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default="")
    known, _ = parser.parse_known_args(argv)
    return known.config

def run_main(main, message="已中断"):
    """执行入口函数，返回进程退出状态。

    main 返回错误码，这里折算成 8 位的退出状态。
    """
    try:
        return status(main())
    except KeyboardInterrupt:
        err(message)
        return status(INTERRUPTED)

def load_or_report(path=None):
    """加载配置并打印失败原因。

    返回 (配置, 错误码, 说明)。
    """
    config, code, message = load_config(path)
    if code != OK:
        err(message)
        # 没配置时先把"这个工具怎么用"指给使用者：否则敲了一个拼错的开关，
        # 拿到的只是"找不到配置"，下一步该做什么全靠猜
        err("（还没接入的工程先照 config-example/ci_config.py 写一份配置；"
            "用法见 `ci.py --help`）")
    return config, code, message

def stage_main(description, action, extend=None):
    """阶段脚本的统一入口：加载配置、解析参数、执行 action。

    action 即 `run(config, args)`，返回退出码；所有阶段共用这一个签名，驱动层
    才能一视同仁地调用它们——用不到 args 的阶段也照收。extend 可向解析器补充
    参数。
    """
    ensure_utf8_streams()
    # 先把 --config 落到环境变量上：配置的位置决定解析器，而解析器要晚一步才建
    path = prescan_config()
    if path:
        os.environ[CONFIG_ENV] = path
    config, code, message = load_or_report(path)
    if config is None:
        return code
    parser = make_parser(description, config)
    if extend:
        extend(parser)
    args = parser.parse_args()
    return run_main(lambda: action(config, args))


def help_or_version(argv, argv_source=None):
    """`--help` / `-h` / `--version`：不经配置就给答案。

    这些开关问的是"这个工具怎么用"，而带配置的解析器要先把配置载起来才建得出来
    （帮助文本与缺省值都来自配置元数据）。没接入的工程敲 --help 时不该得到
    "找不到配置"——那是答非所问，退出码 2 的含义还是"缺外部依赖"。

    带配置的解析器建得起来就用它（帮助里能看到本工程的缺省值），建不起来就退回
    一份最小说明。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        config, code, _message = load_config(prescan_config(argv))
    except Exception:
        config, code = None, 1
    if config is not None and argv_source is not None:
        parser = argv_source(config)
        parser.parse_args(argv)
        return 0
    if "--version" in argv:
        out("cxx_ci {}".format(__version__))
        return 0
    out("cxx_ci {}（契约版本 {}）".format(__version__, CONTRACT_VERSION))
    out("")
    out("用法: ci.py [动作] [选项]")
    out("  动作: doctor")
    out("  选项: --config PATH --only A,B --skip A,B --level quiet|normal|verbose")
    out("        --build-dir PATH --build-type debug|release --jobs N --timeout N")
    return 0
