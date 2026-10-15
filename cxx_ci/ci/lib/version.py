#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具版本与契约版本。

工具版本与契约版本分开演进：工具版本随实现迭代，契约版本只在跨项目约定
变化时才动。两个数字都进机器可读报告的 tool 段，报告因此能自证"是哪一版
跑出来的"。
"""

# 本侧工具的语义化版本。出问题时用它定位"从哪一版开始"。
__version__ = "1.3.0"

# 本工具实现的跨项目契约版本，与 CONTRACT.md 一致
CONTRACT_VERSION = 3

# 本侧支持的最低 Python，供 doctor 与报告的环境段使用
MIN_PYTHON = (3, 4)


def version_line(project="ci"):
    """`--version` 的统一形状。

    `<项目> <x.y.z> contract <N> python >=<下限>`——两侧输出同一形状，人与脚本
    都只认这一行。契约版本必须与 standardize 侧一致（见 CONTRACT.md）。
    """
    return "{} {} contract {} python >={}.{}".format(
        project, __version__, CONTRACT_VERSION, MIN_PYTHON[0], MIN_PYTHON[1])
