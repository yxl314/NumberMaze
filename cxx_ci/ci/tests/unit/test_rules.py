#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""注释与文档纪律的常驻自检。

判据都在 `tools/rules_check.py` 里，这里只负责把它接进门禁——纪律写在纸上
不会自己生效，得有人每次都跑一遍。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib import tools  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
CHECKER = REPO / "tools" / "rules_check.py"


def test_comment_and_doc_discipline_is_clean():
    """注释、文档与几条代码纪律全部达标。"""
    assert CHECKER.is_file(), "缺少 " + str(CHECKER)
    # 用 tools.capture 而不是 subprocess.run：3.4 上没有后者那些参数
    status, out, _err = tools.capture([sys.executable, str(CHECKER)], cwd=REPO)
    text = out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out)
    assert 0 == status, "注释与文档自检未通过：\n" + text
    assert "共 0 处" in text, text
