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
    return build.build_project(
        config, config.test_build_file, config.test_build_dir,
        args.build_type, args.jobs)


if __name__ == "__main__":
    sys.exit(cli.stage_main(__doc__, run))
