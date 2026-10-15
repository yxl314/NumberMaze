#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""集成测试的公共夹具。"""

import pytest

from helper import Sandbox


@pytest.fixture(scope="module")
def box():
    """整个测试模块共用一个沙箱。"""
    sandbox = Sandbox()
    yield sandbox
    sandbox.dispose()

# 本目录与它的兄弟目录进 sys.path：两侧有同名测试文件，需要 --import-mode=importlib
# 才不冲突，而那个模式不会自动加测试目录
import sys as _sys
from pathlib import Path as _Path

for _name in ("", "integration", "unit"):
    _dir = _Path(__file__).resolve().parent / _name if _name else \
        _Path(__file__).resolve().parent
    if _dir.is_dir() and str(_dir) not in _sys.path:
        _sys.path.insert(0, str(_dir))
