"""C++ 代码排版与 clang-tidy 风格整理。

本包只跑在现代机器上（Python >= 3.11），可用 f-string、pathlib、typing、
dataclasses 与 pip 装的依赖。它与门禁项目 `ci` 不共享任何 Python 代码，
只通过文件与命令行协作——约定见仓库根的 CONTRACT.md。
"""

import sys

# 版本检查放在这里，且只用 3.4 也能解析的写法：本项目其余部分用了 3.11 语法，
# 在老机器上直跑源码时，先让包初始化给出明确的升级提示，而不是抛一个看不懂的
# SyntaxError。装成包时 requires-python 会在安装期直接拒绝。
if sys.version_info < (3, 11):
    sys.exit("cxx-standardize 需要 Python 3.11 或更高版本，当前为 %d.%d"
             % sys.version_info[:2])

# 版本常量集中在 .version，供 cli / report 与 `--version` 复用；这里再导出一次，
# 让 `from cxx_standardize import CONTRACT_VERSION, __version__` 照旧可用
from .version import CONTRACT_VERSION, MIN_PYTHON, __version__  # noqa: E402,F401

__all__ = ["CONTRACT_VERSION", "MIN_PYTHON", "__version__"]
