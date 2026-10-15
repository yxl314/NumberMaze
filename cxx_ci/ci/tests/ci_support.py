#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试专用的辅助函数。

只服务于测试，不属于被 CI 工具的公开接口。
"""

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import cli, config, stage


def capture(func, *args):
    """捕获 func 写到 stdout 与 stderr 的内容。

    返回 (返回值, 输出, 诊断)。
    """
    out, err = io.StringIO(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        result = func(*args)
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return result, out.getvalue(), err.getvalue()


class Stub(object):
    """构造只带指定属性的假配置。"""

    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


def parse(args):
    """按共享参数解析给定的命令行。"""
    return cli.make_parser("test").parse_args(args)


def load(path=None):
    """加载配置并断言成功，返回配置对象。"""
    loaded, code, message = config.load_config(path)
    assert loaded is not None, message
    return loaded


def make_result(name="build", code=0, elapsed=0.5, out="", err="",
              timed_out=False):
    """构造一个阶段结果。"""
    return stage.Result(name, code, elapsed, out, err, timed_out)
