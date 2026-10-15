#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命名口径元检查：把"规范本身"变成机器门禁。

命名口径写在 `.clang-tidy` 里只是文本。clang-tidy 只会照着当前配置继续绿，**守不住
规则本身**——把静态变量的 `s_` 悄悄改回 `g_`，它反而"合规地绿"。规范里记着一次
SVN 回流把 `s_` 静默改回 `g_`、一个月无人发现，这一层就是为那件事准备的。

做法是现场推导而不是比对快照：口径表（`NAMING_PREFIX` 等）是唯一事实源，从这里
推出 `.clang-tidy` 应为何，逐项比对，偏离即红。因此改口径只改配置表，不必同步
两份文本。

判据全部来自配置，脚本内零字面量——写死任何一个前缀，换一个工程就要改代码。
"""
import re
from pathlib import Path

import yaml

from .errors import Code
from .tidy import LOCAL_KINDS, NAMING_CHECK

# 命名空间的 kind 到前缀键名的映射由口径表给出；大小写统一按 lower_case 推导
CASE_LOWER = "lower_case"

def local_ban_regexp(banned):
    """按禁用前缀集生成局部变量的豁免正则。

    POSIX ERE 没有先行否定，因此按分支拆解：

      - `[^gmstv]…`      常规名，首字符不在禁用集里；
      - `[gmstv][^_]…`   以禁用字母开头但第二个字符不是下划线（mutex/value/token）；
      - `^[gmstv]$`      单字母名。

    这样"带禁用前缀"的名字（`v_foo`）落到检查里，其余一律豁免。
    """
    letters = re.escape(str(banned or ""))
    if not letters:
        return ""
    return ("^([^{letters}]|[{letters}][^_])[a-z0-9_]*$|^[{letters}]$"
            .format(letters=letters))


def naming_options(config_file):
    """读出 `.clang-tidy` 里全部 readability-identifier-naming 选项。"""
    path = Path(str(config_file or ""))
    if not path.is_file():
        return None
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8",
                                                  errors="replace"))
    except yaml.YAMLError:
        return None
    if not isinstance(document, dict):
        return None
    options = {}
    for item in document.get("CheckOptions") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if key.startswith(NAMING_CHECK + "."):
            options[key[len(NAMING_CHECK) + 1:]] = str(item.get("value"))
    return options


def expectations(config):
    """从口径表现场推导出 .clang-tidy 应为何，返回 {选项名: 期望值}。"""
    wanted = {}
    for kind, prefix in (config.naming_prefix or {}).items():
        wanted[kind + "Prefix"] = prefix
        wanted[kind + "Case"] = CASE_LOWER
    ban = local_ban_regexp(config.naming_local_ban)
    for kind in LOCAL_KINDS:
        wanted[kind + "Case"] = CASE_LOWER
        wanted[kind + "Prefix"] = "_"
        wanted[kind + "IgnoredRegexp"] = ban
    for key in config.naming_premise_true or ():
        wanted[key] = "true"
    return wanted


def problems(config):
    """比对口径表与配置，返回偏离说明（空列表表示一致）。"""
    if not (config.naming_prefix or {}):
        # 口径表为空 = 这个工程没有声明命名纪律，本层不适用。通用工具不该逼
        # 每个工程都声明一套前缀；声明了才守
        return []
    wanted = expectations(config)
    options = naming_options(config.clang_tidy_config)
    if options is None:
        return ["声明了命名口径却读不出风格配置: {}".format(
            config.clang_tidy_config)]
    found = []
    for key in sorted(wanted):
        actual = options.get(key)
        if actual != wanted[key]:
            found.append(".{} 期望 {!r} 实际 {!r}".format(
                key, wanted[key], actual))
    # 宏豁免必须存在且不得复活被禁的 key：宏展开生成的名字天然带各类前缀，
    # IgnoreMacros 是局部规则成立的前提，不是可选项
    for key in config.naming_exempt_keys or ():
        if key in options:
            found.append(".{} 不得出现（该项已被拍板移除）".format(key))
    # 口径表里要求了前缀、配置却没声明对应键：这类违规 clang-tidy 根本不报，
    # 也就没机会被前缀规范化改写。静默漏掉等于门禁开洞，要点出来
    for kind, prefix in (config.naming_prefix or {}).items():
        if prefix and kind + "Prefix" not in options:
            found.append(".{}Prefix 缺失（口径表要求 {!r}），该类的违规不会被"
                         "报告".format(kind, prefix))
    if len(options) < len(wanted):
        # 解析出的选项比应有数还少：要么配置被删过，要么解析方式变了。
        # 两种都不能当"通过"
        found.append("命名选项只有 {} 项，少于应有的 {} 项，配置可能被删减"
                     .format(len(options), len(wanted)))
    return found


def check(config):
    """元检查入口，返回 (错误码, 说明列表)。"""
    found = problems(config)
    if found:
        return Code.NAMING_META_DRIFT, found
    return Code.OK, []
