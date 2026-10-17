#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置并编译测试构建文件。

构建工具、测试构建文件与测试输出目录取自 config/config.py，
不接受 --build-dir（该参数由 ci.py 统一透传，此处不适用）。
契约见 ci.py 的 docstring，共享参数见 lib/。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from lib import build, cli



def run(config, args):
    # --build-dir 是共享参数：点名换构建目录时，测试构建也要跟过去。
    # 配置里的 TEST_BUILD_DIR 是在加载配置时推导好的（留空＝与 BUILD_DIR 相同），
    # 那之后 --build-dir 再改主构建目录，它就与主构建目录分家了：一次运行把产物
    # 写进两棵树，还会出现两份 compile_commands.json
    target = args.build_dir
    if target and target != config.build_dir and \
            config.test_build_dir == config.build_dir:
        target = args.build_dir
    else:
        target = config.test_build_dir
    return build.build_project(
        config, config.test_build_file, target,
        args.build_type, args.jobs)


if __name__ == "__main__":
    sys.exit(cli.stage_main(__doc__, run))
