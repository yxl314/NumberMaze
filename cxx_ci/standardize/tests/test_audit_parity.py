"""同固件对照：standardize 侧 audit 与 ci 侧 audit 必须审出同一份结论。

两份 audit.py 是刻意的平行实现（见两侧文件头），各自只有本侧的单元测试，没有
任何机制保证同一份规则在两侧审出同样的结果——改一条判据只改一侧、修 bug 只修
手边那一份，都会静默分叉。这里喂同一份固件（tools/parity/audit_fixture/）给
本侧 audit，把说明行归一化成 (判据, 相对路径, 配置键) 三元集合，与参照实跑
生成的 expected.json 比对；两侧的固定差异登记在 tools/parity/exemptions.json。

本侧是参照实现（现代 Python、能跑全部判据），expected.json 由本侧实跑生成，
不得手写。本测试不 import 另一侧的任何模块：只读它落下的 JSON。
"""

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

from cxx_standardize import audit
from cxx_standardize.errors import Code

ROOT = Path(__file__).resolve().parents[2]
PARITY = ROOT / "tools" / "parity"
FIXTURE = PARITY / "audit_fixture"

SIDE = "standardize"


# ------------------------------------------------------------------ 归一化

# 说明行 → (判据, 相对路径, 配置键) 的分类表。两侧文案逐字同形（只有 format
# 机制不同），因此用同一套正则解析。顺序有意义：先长的、更具体的在前。
_RULES = (
    ("std_unlocked",
     re.compile(r"^编译标准漂移: 全部构建文件都没有承载变量 (.+?)，标准未被锁定$"),
     lambda m: ("std_unlocked", "", m.group(1))),
    ("std_carrier",
     re.compile(r"^编译标准漂移: (.+?) 的承载变量 (.+?) 取值不等于 (.+)$"),
     lambda m: ("std_carrier", m.group(1), m.group(2))),
    ("std_token",
     re.compile(r"^编译标准漂移: (.+?):(\d+) 出现其它标准 (.+)$"),
     lambda m: ("std_token", "{}:{}".format(m.group(1), m.group(2)),
                m.group(3))),
    ("dead_key",
     re.compile(r"^死配置: cfg\.(\w+) 从未被引用$"),
     lambda m: ("dead_key", "", m.group(1))),
    ("bypass",
     re.compile(r"^配置旁路: (.+?):(\d+) 的字面量 '([^']*)' 应改用 cfg\.(\w+)$"),
     lambda m: ("bypass", "{}:{}".format(m.group(1), m.group(2)), m.group(4))),
    ("raw_io",
     re.compile(r"^裸写文本: (.+?):(\d+) 绕过"),
     lambda m: ("raw_io", "{}:{}".format(m.group(1), m.group(2)), "")),
    ("prefix_compare",
     re.compile(r"^自行前缀比较: (.+?):(\d+) 又拼了一遍"),
     lambda m: ("prefix_compare", "{}:{}".format(m.group(1), m.group(2)), "")),
    ("header_filter",
     re.compile(r"^TIDY_HEADER_FILTER 写得太宽.*例如 ([^）]+)）"),
     lambda m: ("header_filter", "", m.group(1))),
    ("header_filter",
     re.compile(r"^TIDY_HEADER_FILTER 不是合法正则: (.+)$"),
     lambda m: ("header_filter", "", "regex-invalid")),
)


def classify(line):
    """把一条说明行归成三元组；认不出来就返回 None。

    认不出意味着判据文案变了或新增了判据——对照测试据此报错，逼作者同步固件，
    而不是悄悄放行。
    """
    for _label, pattern, build in _RULES:
        match = pattern.match(line)
        if match is not None:
            return build(match)
    return None


def normalize(messages, root):
    """把说明行集合归一化成 (判据, 相对路径, 配置键) 三元集合。

    路径相对化只剥掉固件根前缀，比的是"结论集合"，不比文字顺序。
    """
    prefix = str(root) + os.sep
    found = set()
    for raw in messages:
        line = raw.replace(prefix, "")
        item = classify(line)
        assert item is not None, "对照固件出现无法归类的说明行: " + raw
        found.add(item)
    return found


# ------------------------------------------------------------------ 夹具与参照

def load_json(path):
    handle = open(str(path), "r", encoding="utf-8")
    try:
        return json.loads(handle.read())
    finally:
        handle.close()


def config_for(fixture):
    """本侧配置对象：键名必须受控，避免多出多余的死配置键。"""
    return SimpleNamespace(
        root=str(fixture),
        cxx_standard="17",
        tidy_header_filter="(include|src|tests)/.*",
        out_dir="build/out",
        release_tag="v9.9",
        unused_flag="never-read",
    )


def exempt_rules(entries, side):
    """本侧登记的豁免判据集合：无 why 的条目不生效。"""
    rules = set()
    for entry in entries:
        if str(entry.get("side")) == side and str(entry.get("why") or "").strip():
            rules.add(str(entry.get("rule")))
    return rules


def test_audit_matches_reference_fixture():
    """本侧 audit 对固件的结论，须与参照实现实跑出的 expected.json 相等。"""
    reference = load_json(PARITY / "expected.json")
    entries = load_json(PARITY / "exemptions.json")["entries"]

    code, messages = audit.run(config_for(FIXTURE), [FIXTURE])

    exempt = exempt_rules(entries, SIDE)
    ours = set(item for item in normalize(messages, FIXTURE) if item[0] not in exempt)
    expected = set(
        (str(item[0]), str(item[1]), str(item[2]))
        for item in reference["findings"] if str(item[0]) not in exempt
    )

    assert Code.AUDIT_DEAD_KEY == code, messages
    assert expected == ours, (
        "两侧 audit 结论不一致：\n参照多出 {}\n本侧多出 {}".format(
            sorted(expected - ours), sorted(ours - expected)))

def test_exemptions_are_all_still_in_use():
    """豁免条目必须真的还在解释差异：不再命中的条目要删掉。

    过期豁免与缺失豁免同样危险——留着它，下次真的出现同类差异时会被静默吞掉。
    这里的判据是"这条 rule 在参照实现里确实存在、而在本侧确实不存在"，也就是
    它确实在解释一处差异。
    """
    reference = load_json(PARITY / "expected.json")
    entries = load_json(PARITY / "exemptions.json")["entries"]
    reference_rules = set(str(item[0]) for item in reference["findings"])

    stale = [entry for entry in entries
             if entry.get("side") == SIDE
             and str(entry.get("rule")) not in reference_rules]
    assert [] == stale, (
        "豁免清单过期: 以下条目声明的判据在参照实现里已不存在，请删掉——"
        "{}".format([entry.get("rule") for entry in stale]))

def test_cmake_mode_does_not_read_the_qmake_project_file():
    """固件里同时有 CMakeLists.txt 与 app.pro，两侧都该只认 cmake 那一份。

    app.pro 里写了一个 CMakeLists 中不存在的标准记号（-std=gnu++98）；
    它要是出现在结论集合里，说明有人在 cmake 模式下误读了 .pro。
    """
    reference = load_json(PARITY / "expected.json")
    tokens = set(str(item[2]) for item in reference["findings"]
                 if str(item[0]) == "std_token")
    assert "-std=gnu++98" not in tokens, (
        "参照实跑把 app.pro 也读进来了，cmake / qmake 的构建文件没区分开")
