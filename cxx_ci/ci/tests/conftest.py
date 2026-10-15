#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pytest 的公共夹具。"""

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# 让所有测试都能 import lib 与被测模块
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def clean_temp_dirs(monkeypatch):
    """测试结束后删除用 tempfile.mkdtemp 建出的目录。"""
    created = []
    original = tempfile.mkdtemp

    def tracking(*args, **kwargs):
        path = original(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", tracking)
    yield
    for path in created:
        shutil.rmtree(path, ignore_errors=True)

# 本目录与它的兄弟目录进 sys.path：两侧有同名测试文件，需要 --import-mode=importlib
# 才不冲突，而那个模式不会自动加测试目录
import sys as _sys
from pathlib import Path as _Path

for _name in ("", "integration", "unit"):
    _dir = _Path(__file__).resolve().parent / _name if _name else \
        _Path(__file__).resolve().parent
    if _dir.is_dir() and str(_dir) not in _sys.path:
        _sys.path.insert(0, str(_dir))
