"""同固件对照：xfail 的三态分类两侧必须同形。

ci 与 standardize 各有一份 xfail 清单实现（平行实现，见各自模块头），都在实现
"登记项若不再命中要反向报已修复"这条规则，格式却不同（本侧是文件:检查名，
另一侧是用例名/符号前缀）。这里把同一份场景（tools/parity/xfail_cases.json：
哪些项登记了、哪些本次命中、复核日期）喂给本侧 Checklist/verdict，把结果归一
化成"仍存在/已修复/新增/复核到期"四个标签集合，与场景里声明的期望比对；另一
侧用同一份 JSON 做同样的比对，两个集合都被钉住即为同形。

本测试不 import 另一侧的任何模块：只读它落下的 JSON。
"""

import json
from pathlib import Path

from cxx_standardize import xfail
from cxx_standardize.errors import Code

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tools" / "parity" / "xfail_cases.json"


def load_cases():
    return json.loads(CASES.read_text(encoding="utf-8"))


def native(case):
    """按本侧格式渲染一条：本侧条目是 文件:检查名。"""
    return ("src/{}.cpp".format(case["label"]), 1,
            "style-{}".format(case["label"]))


def label(entry):
    return entry.value[len("src/"):-len(".cpp")]


def registered(cases):
    entries = []
    for case in cases:
        if case["registered"]:
            path, line, check = native(case)
            entries.append(xfail.Entry("style", path, check, line, case["review"]))
    return xfail.Registry("", "", entries)


def classify(registry, cases, today):
    """本侧 Checklist 的三态 + 到期分类，标签一律用场景里的 label。"""
    checklist = xfail.Checklist(registry)
    for case in cases:
        if case["hit"] and case["registered"]:
            checklist.style(*native(case))
    still_here = set(label(entry) for entry in checklist.used)
    fixed = set(label(entry) for entry in checklist.stale(None))
    new = set()
    for case in cases:
        if case["hit"] and not case["registered"]:
            assert checklist.style(*native(case)) is None
            new.add(case["label"])
    expired = set(label(entry) for entry in checklist.expired(today))
    return checklist, {
        "still_here": still_here,
        "fixed": fixed,
        "new": new,
        "expired": expired,
    }


def test_xfail_classification_matches_fixture():
    data = load_cases()
    registry = registered(data["cases"])
    checklist, result = classify(registry, data["cases"], data["today"])
    expected = dict((key, set(value)) for key, value in data["expected"].items())
    assert expected == result

    code, notes = xfail.verdict(checklist, None, today=data["today"])
    assert Code.OK == code
    assert any("xfail 已修复，可移除" in note for note in notes)
    assert any("xfail 复核已到期" in note for note in notes)

    strict_code, strict_notes = xfail.verdict(
        checklist, None, strict=True, today=data["today"])
    assert Code.XFAIL_STALE == strict_code
    assert strict_notes == notes

def test_new_state_comes_from_the_production_verdict():
    """第三态"新增"必须由生产 verdict 产出，而不是测试自己算。

    以前这里 `new.add(...)` 是测试自己拼的：生产 verdict 只产出"已修复/到期"，
    "新增"由各调用方各算一遍。两侧若在"新增"的呈现上分叉，这条对照测试看不见
    ——因为它根本没经过生产逻辑。现在 verdict 收下本次实际命中的键，三态口径
    回到一处。
    """
    data = load_cases()
    registry = registered(data["cases"])
    checklist = xfail.Checklist(registry)
    hits = []
    for case in data["cases"]:
        if not case["hit"]:
            continue
        if case["registered"]:
            # 本侧按 kind 取对应接口把登记项标成命中
            entry = registry.get(case["label"]) if hasattr(registry, "get") else None
            if entry is not None:
                checklist._hit(entry)
        hits.append(case["label"])
    _code, notes = xfail.verdict(checklist, xfail.KINDS, today=data["today"],
                                 hits=hits)
    assert any("xfail 新增（清单里没有）" in note for note in notes), notes
