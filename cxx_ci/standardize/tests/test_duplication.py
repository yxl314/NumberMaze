"""重复度自检：把 tools/dupcheck.py --check 接进本侧测试。

只测生产代码——测试里重复的 fixture 是有意的可读性选择。守卫本身在仓库根的
tools/ 与 .dupcheck-*.json：两个项目各接一次，谁改动都不会悄悄长出新重复。
"""

import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[2] / "tools" / "dupcheck.py"


def test_duplication_is_within_baseline():
    proc = subprocess.run([sys.executable, str(TOOL), "--check"],
                          capture_output=True, text=True, check=False)
    assert 0 == proc.returncode, proc.stdout + proc.stderr
