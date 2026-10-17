#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行全部测试：pytest 负责单元与集成，冒烟测试单独跑。

用法: python3 ci/tests/run.py
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    code = subprocess.call([sys.executable, "-m", "pytest", str(HERE),
                            "-q", "--ignore=" + str(HERE / "smoke")])
    smoke = subprocess.call([sys.executable, str(HERE / "smoke" / "run.py")])
    if code or smoke:
        return 1
    print("全部测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
