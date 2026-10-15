"""requirements/ 与 pyproject.toml 不漂，且版本号两处一致。

`requirements/*.txt` 由 tools/export_requirements.py 从 standardize/pyproject.toml
生成。这里跑一次 `--check` 钉住"两边没各自漂"；再钉住 pyproject 的 version 与
包内 `__version__` 一致——`--version` 输出与出版元数据必须说同一个版本号。
"""

import subprocess
import sys
import tomllib
from pathlib import Path

import cxx_standardize

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "export_requirements.py"
PYPROJECT = ROOT / "standardize" / "pyproject.toml"


def test_requirements_match_pyproject():
    result = subprocess.run([sys.executable, str(TOOL), "--check"],
                            capture_output=True, text=True, check=False)
    assert 0 == result.returncode, (result.stdout, result.stderr)


def test_pyproject_version_matches_the_package():
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["version"] == cxx_standardize.__version__


def test_pyproject_python_floor_matches_version_module():
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    floor = ".".join(str(part) for part in cxx_standardize.MIN_PYTHON)
    assert project["requires-python"] == f">={floor}"
