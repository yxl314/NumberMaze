#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行全部测试：pytest 负责单元与集成，冒烟测试与重复度自检单独跑。

用法: python3 ci/tests/run.py
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 重复度自检是仓库级工具，不属于任一侧；两个项目各接一次，确保谁改动都不会
# 悄悄长出新重复。见 tools/dupcheck.py 与 .dupcheck-baseline.json
DUPCHECK = HERE.parents[1] / "tools" / "dupcheck.py"


def main():
    code = subprocess.call([sys.executable, "-m", "pytest", str(HERE),
                            "-q", "--ignore=" + str(HERE / "smoke")])
    smoke = subprocess.call([sys.executable, str(HERE / "smoke" / "run.py")])
    duplication = subprocess.call([sys.executable, str(DUPCHECK), "--check"])
    if code or smoke or duplication:
        return 1
    print("全部测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
