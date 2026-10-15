"""pytest 的公共夹具。"""

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# 本目录进 sys.path：测试之间互相 import 兄弟模块（std_support）。用
# --import-mode=importlib 时 pytest 不会按 basename 建模块、也不加这个目录，
# 而两侧有同名测试文件，必须用 importlib 模式才不冲突
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# 让测试都能 import cxx_standardize
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def project():
    """一个隔离的项目沙箱：临时目录 + 假工具 + 配置模板。"""
    from std_support import Project

    box = Project()
    yield box
    box.dispose()


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
