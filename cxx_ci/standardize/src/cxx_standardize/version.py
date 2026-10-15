"""工具版本与契约版本。

工具版本随实现迭代，契约版本只在跨项目约定变化时才动。两个数字都进机器可读
报告的 tool 段与 `--version` 输出，报告因此能自证"是哪一版跑出来的"。
"""

# 本侧工具的语义化版本。出问题时用它定位"从哪一版开始"。
__version__ = "1.0.0"

# 本工具实现的跨项目契约版本，与 CONTRACT.md 一致
CONTRACT_VERSION = 3

# 本侧支持的最低 Python。装成包时由 requires-python 拒绝更低版本，直跑源码时
# 由 __init__ 的版本闸给出提示
MIN_PYTHON = (3, 11)


def version_line(project="cxx-standardize"):
    """`--version` 的统一形状。

    `<项目> <x.y.z> contract <N> python >=<下限>`——两侧输出同一形状，人与脚本
    都只认这一行。契约版本必须与 ci 侧一致（见 CONTRACT.md）。
    """
    return (f"{project} {__version__} contract {CONTRACT_VERSION} "
            f"python >={MIN_PYTHON[0]}.{MIN_PYTHON[1]}")
