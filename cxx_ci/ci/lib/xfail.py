#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""已登记存量问题的清单（xfail）。

门禁对"历史遗留"需要一个中性档位：既不放任整仓全红（那会让人直接无视
门禁），也不逼着一次性豪赌式大改（改坏了无法回滚）。清单给的就是这一档：

    命中的违规不计失败，但仍回显并标注 (xfail)；
    未登记的新违规照判失败；
    登记项已修复（本次没命中）则提示可移除，XFAIL_STRICT 打开时直接判失败。

清单是纯文本，一行一条，`#` 起注释，行尾可写 `@YYYY-MM-DD` 作复核到期日。
两种条目：

    [test]     用例名                                    （与适配器报出的名字逐字相同）
    [memcheck] 符号前缀（如 NormalButton::）或 sig:签名哈希

分段头可省略：省略时按条目形状推断——`sig:` 开头或 `::` 结尾按 memcheck，
其余按 test。名字里带 `::` 的用例与符号前缀形状相近，拿不准时用分段头写死，
别依赖推断。

风格侧另有一个独立的清单，归另一个项目管，本项目不读也不解析它。
"""

import collections
import os
import re
import time

from . import source
from .errors import OK, XFAIL_BAD_FILE, XFAIL_STALE

# 条目种类；分段头与推断都产出这三个值
KINDS = ("test", "memcheck")

# 一条登记项。kind 决定 value 的含义：
#   test     value 是用例名
#   memcheck value 是符号前缀，或 "sig:" 加签名哈希
Entry = collections.namedtuple("Entry", "kind value detail line review")

# 清单文件本体
Registry = collections.namedtuple("Registry", "path root entries")

_SECTION = re.compile(r"^\[(?P<kind>\w+)\]$")
_REVIEW = re.compile(r"@(?P<date>\d{4}-\d{2}-\d{2})\s*$")
# 行内注释：`#` 在行首或前面是空白才算注释，路径里的 `#` 不当注释
_COMMENT = re.compile(r"(?:^|\s)#.*$")
_SIG = re.compile(r"^sig:[0-9a-fA-F]{4,}$")


def _kind_of(text, section):
    """决定一条无分段头条目的种类。

    推断只在形状足够明确时生效，判断依据写在这里一处，别处不再猜。
    """
    if section:
        return section
    if _SIG.match(text) or text.endswith("::"):
        return "memcheck"
    return "test"


def _parse_entry(text, section):
    """解析一条登记项，返回 (Entry, 错误说明)。"""
    kind = _kind_of(text, section)
    if kind == "test":
        return Entry("test", text, "", 0, ""), ""
    if _SIG.match(text) or text.endswith("::"):
        return Entry("memcheck", text, "", 0, ""), ""
    return None, "memcheck 条目必须是符号前缀（以 :: 结尾）或 sig:哈希: " + text


def parse(text):
    """解析清单文本，返回 (条目列表, 错误码, 说明)。

    出错时给出出错行号与原始行：清单写错必须当场说清，否则使用者会以为
    门禁"莫名其妙变红"。
    """
    entries, section = [], ""
    for number, raw in enumerate(text.splitlines(), 1):
        line = _COMMENT.sub("", raw).strip()
        if not line:
            continue
        match = _SECTION.match(line)
        if match:
            if match.group("kind") not in KINDS:
                return None, XFAIL_BAD_FILE, "第 {} 行未知的分段头: {}".format(
                    number, line)
            section = match.group("kind")
            continue
        review = ""
        match = _REVIEW.search(line)
        if match:
            review = match.group("date")
            line = line[:match.start()].strip()
            if not line:
                return None, XFAIL_BAD_FILE, "第 {} 行只有复核日期: {}".format(
                    number, raw.strip())
        entry, message = _parse_entry(line, section)
        if entry is None:
            return None, XFAIL_BAD_FILE, "第 {} 行: {}".format(number, message)
        entries.append(entry._replace(review=review))
    return entries, OK, ""


def load(path, root="", config=None):
    """读取清单文件，返回 (条目列表, 错误码, 说明)。

    文件不存在不算错：登记是可选档位，没有清单时门禁按原有语义判定。
    走 source 判定编码——清单虽由人工编辑，但可能是 GBK 等老编码；判定不出
    编码时不猜、按坏文件报错，绝不拿 U+FFFD 顶替。
    """
    if not path or not os.path.isfile(path):
        return [], OK, ""
    text, info = source.read_source(path, config)
    if text is None:
        return None, XFAIL_BAD_FILE, "xfail 清单编码无法确定（{}）: {}".format(
            info.reason, path)
    return parse(text)


def describe(entry):
    """把登记项还原成便于展示的一行。"""
    return entry.value


class Checklist(object):
    """一次检查里登记清单的使用情况。

    登记项是否"已修复"只能由"本次有没有命中"推断，所以匹配与统计必须
    出自同一个对象，不能一处匹配、另一处另算一遍。
    """

    def __init__(self, registry):
        self.registry = registry
        self.used = []

    def _hit(self, entry):
        if entry not in self.used:
            self.used.append(entry)
        return entry

    def _entries(self, kind):
        return [item for item in self.registry.entries if item.kind == kind]

    def test(self, case):
        """用例名是否已登记。名字按适配器报出的原样逐字比较。"""
        for entry in self._entries("test"):
            if entry.value == case:
                return self._hit(entry)
        return None

    def symbol(self, function, signature=""):
        """符号前缀或签名哈希是否已登记。"""
        for entry in self._entries("memcheck"):
            if entry.value.startswith("sig:"):
                if signature and entry.value[4:].lower() == signature.lower():
                    return self._hit(entry)
                continue
            if function and function.startswith(entry.value):
                return self._hit(entry)
        return None

    def stale(self, kinds):
        """本次没命中的登记项，即已被修复、应当删行的那些。

        kinds 限定本次判定覆盖的种类：没跑到的种类不能推断成已修复。
        """
        return [entry for entry in self.registry.entries
                if entry.kind in kinds and entry not in self.used]

    def expired(self, today):
        """复核到期日已过的登记项。"""
        return [entry for entry in self.registry.entries
                if entry.review and entry.review < today]


def load_checklist(config):
    """按配置读取清单，返回 (Checklist, 错误码, 说明)。"""
    path = getattr(config, "xfail_file", "") or ""
    root = getattr(config, "root", "")
    entries, code, message = load(path, root, config)
    if code != OK:
        return None, code, message
    return Checklist(Registry(path, root, entries or [])), OK, ""


def verdict(checklist, kinds, strict=False, today="", hits=()):
    """汇总清单状态，返回 (错误码, 提示行列表)。

    kinds 限定本次判定覆盖的种类：没跑到的种类不能推断成"已修复"。提示行
    由调用方打印，库只算结论——展示策略归各阶段，判定口径只有这一处。

    hits 是本次实际命中的条目键（可选）。给了它就能补齐第三态——**新增**：
    登记清单没写、而这次真的出现了的那些。不给也能用（新增由调用方自己算），
    但把三态收在一处，两侧的"已修复 / 仍存在 / 新增"分类才有同一个口径可比。
    """
    if not today:
        today = time.strftime("%Y-%m-%d")
    notes = []
    stale = checklist.stale(kinds)
    for entry in stale:
        notes.append("xfail 已修复，可移除: " + describe(entry))
    expired = checklist.expired(today)
    for entry in expired:
        notes.append("xfail 复核已到期（{}）: {}".format(
            entry.review, describe(entry)))
    if hits:
        # Entry 是 namedtuple(kind value detail line review)：登记键就是 value
        registered = set()
        for entry in getattr(checklist.registry, "entries", None) or ():
            key = getattr(entry, "value", "") or ""
            if key:
                registered.add(key)
        for key in hits:
            if key and key not in registered:
                notes.append("xfail 新增（清单里没有）: " + str(key))
    if strict and (stale or expired):
        return XFAIL_STALE, notes
    return OK, notes
