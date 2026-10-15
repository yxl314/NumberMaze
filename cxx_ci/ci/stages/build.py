#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置并编译项目主构建文件。

构建工具、构建文件与输出目录取自 config/config.py，
输出目录可用 --build-dir 覆盖。
契约见 ci.py 的 docstring，共享参数见 lib/。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from lib import build, cli



def run(config, args):
    return build.build_project(
        config, config.build_file, args.build_dir, args.build_type, args.jobs)


if __name__ == "__main__":
    sys.exit(cli.stage_main(__doc__, run))
