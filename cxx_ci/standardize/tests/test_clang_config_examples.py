#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例 clang 配置必须真的能用，而且要跟着规范一起变。

这三份文件（.clang-format / .clang-tidy / naming-tables.py）是使用者抓过去就
开跑的起点。放着不管它们会悄悄过期：口径表加了前缀、示例没跟，抓过去的人第一次
跑门禁就红。所以这里把"示例与规范一致"钉成测试。

判据不写死任何前缀：口径表从示例自带的 naming-tables.py 里读，逐项比对示例的
.clang-tidy。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cxx_standardize import prefix  # noqa: E402
from cxx_standardize.tidy import LOCAL_KINDS  # noqa: E402

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "clang-config"
CLANG_TIDY = EXAMPLES / ".clang-tidy"
CLANG_FORMAT = EXAMPLES / ".clang-format"
TABLES = EXAMPLES / "naming-tables.py"


class Tables(object):
    """把 naming-tables.py 当成配置片段读进来（它就是给人粘贴的片段）。"""

    def __init__(self, path):
        namespace = {}
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"),
             namespace)
        self.naming_prefix = namespace.get("NAMING_PREFIX") or {}
        self.naming_local_ban = namespace.get("NAMING_LOCAL_BAN") or ""
        self.naming_exempt_keys = namespace.get("NAMING_EXEMPT_KEYS") or []
        self.naming_premise_true = namespace.get("NAMING_PREMISE_TRUE") or []
        self.clang_tidy_config = str(CLANG_TIDY)


def test_the_example_files_exist():
    for path in (CLANG_TIDY, CLANG_FORMAT, TABLES, EXAMPLES / "README.md"):
        assert path.is_file(), "示例文件缺失: {}".format(path)


def test_the_example_tidy_config_declares_every_kind_the_table_asks_for():
    """口径表要求前缀的每一类，示例里都要有对应键。

    少了某类的 Prefix，那一类的违规根本不报，也不会有任何提示。
    """
    tables = Tables(TABLES)
    options = prefix.naming_options(str(CLANG_TIDY))
    assert options is not None, "读不出示例的 CheckOptions"
    for kind, item in tables.naming_prefix.items():
        assert item, kind
        assert item == options.get(kind + "Prefix"), kind
        assert "lower_case" == options.get(kind + "Case"), kind
    for kind in LOCAL_KINDS:
        assert "_" == options.get(kind + "Prefix"), kind
        assert prefix.local_ban_regexp(tables.naming_local_ban) == \
            options.get(kind + "IgnoredRegexp"), kind


def test_the_example_tidy_config_is_valid_yaml():
    document = yaml.safe_load(CLANG_TIDY.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    assert document.get("Checks")
    assert document.get("CheckOptions")
    # 宏豁免是前提，不是可选项
    values = {item["key"]: item["value"] for item in document["CheckOptions"]}
    assert "true" == values.get(
        "readability-identifier-naming.IgnoreMacros")


def test_the_example_tidy_config_does_not_revive_banned_keys():
    tables = Tables(TABLES)
    options = prefix.naming_options(str(CLANG_TIDY))
    for key in tables.naming_exempt_keys:
        assert key not in options, key


def _clang_format_binary():
    for name in ("clang-format", "clang-format-17"):
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return ""


def test_the_example_format_config_is_accepted_by_clang_format(tmp_path):
    """让真的 clang-format 读一遍：写错的键会被它当场拒掉。

    机器上没装 clang-format 时跳过——这是"配置本身对不对"的检查，不该因为
    环境缺件而失败；接入流程里 init 会提示怎么装。
    """
    binary = _clang_format_binary()
    if not binary:
        pytest.skip("机器上没有 clang-format")
    probe = tmp_path / "probe.cpp"
    probe.write_text("struct S { int a; };\n", encoding="utf-8")
    result = subprocess.run(
        [binary, "--style=file:" + str(CLANG_FORMAT), str(probe)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert 0 == result.returncode, result.stderr.decode("utf-8", "replace")
    assert result.stdout, "clang-format 没有输出"


def test_the_example_format_config_pins_the_documented_choices():
    """示例里几条被文档引用过的取值要留住。

    它们不是随手定的：DerivePointerAlignment 一旦打开，clang-format 会按文件里
    出现最多的写法自己改主意，同一份配置在不同文件上给出不同结果；ReflowComments
    打开会把手工排的注释表压散。
    """
    document = yaml.safe_load(CLANG_FORMAT.read_text(encoding="utf-8"))
    assert "LLVM" == document.get("BasedOnStyle")
    assert 4 == document.get("IndentWidth")
    assert "Never" == document.get("UseTab")
    assert "false" == str(document.get("DerivePointerAlignment")).lower()
    assert "false" == str(document.get("ReflowComments")).lower()
    assert "false" == str(document.get("SortIncludes")).lower()
