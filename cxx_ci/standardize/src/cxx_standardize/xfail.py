"""风格侧的登记清单（standardize.xfail）。

存量违规需要一个中性档位：既不放任整仓全红（那会让人直接无视门禁），也不
逼着一次性豪赌式大改。清单给的就是这一档：

    命中的违规不计失败，但仍回显并标注 (xfail)；
    未登记的新违规照判失败；
    登记项已修复（本次没命中）则提示可移除，XFAIL_STRICT 打开时直接判失败。

清单是纯文本，一行一条，`#` 起注释：

    [style]
    src/util.cpp:readability-identifier-naming
    src/util.cpp:120:bugprone-branch-clone   # 待重构

行号可省略（表示该文件全部行）。分段头可省略，省略时按形状推断：末段像检查名
（`readability-identifier-naming`、`clang-analyzer-core.NullDereference`）的按
style，其余也按 style——本项目的清单只有这一种条目。

门禁侧另有一份清单（`ci.xfail`，登记用例与内存符号），归另一个项目管；本项目
不读也不解析它。
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import source as source_mod
from .errors import Code

KINDS = ("style",)

_SECTION = re.compile(r"^\[(?P<kind>\w+)\]$")
# 行内注释：`#` 在行首或前面是空白才算注释，路径里的 `#` 不当注释
_COMMENT = re.compile(r"(?:^|\s)#.*$")
# clang-tidy 检查名：模块前缀 + 连字符或点
_CHECK = re.compile(r"^[a-z][a-z0-9]*[-.][A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class Entry:
    """一条登记项。value 是文件路径，detail 是检查名，line 为 0 表示不限行。"""

    kind: str
    value: str
    detail: str = ""
    line: int = 0


@dataclass
class Registry:
    """清单文件本体。"""

    path: str = ""
    root: str = ""
    entries: list[Entry] = field(default_factory=list)


def _kind_of(section):
    """决定一条无分段头条目的种类。

    本项目的清单只有 style 一种，分段头写别的名字要报错而不是当成 style——
    写错了却静默生效，等于清单永远匹配不上。
    """
    if section:
        return section
    return "style"


def _parse_entry(text, section):
    """解析一条登记项，返回 (Entry, 错误说明)。"""
    kind = _kind_of(section)
    if kind != "style":
        return None, f"本清单只登记 style 条目，未知的分段头: [{kind}]"
    fields = text.split(":")
    check = fields[-1]
    if not _CHECK.match(check):
        return None, "style 条目必须以 clang-tidy 检查名结尾: " + text
    middle = fields[-2] if len(fields) >= 3 else ""
    if middle and not middle.isdigit():
        return None, "style 条目的行号必须是数字: " + text
    line = int(middle) if middle else 0
    path = ":".join(fields[:-2]) if middle else ":".join(fields[:-1])
    if not path:
        return None, "style 条目缺少文件路径: " + text
    return Entry("style", path, check, line), ""


def parse(text):
    """解析清单文本，返回 (条目列表, 错误码, 说明)。

    出错时给出出错行号与原始行：清单写错必须当场说清，否则使用者会以为门禁
    "莫名其妙变红"。
    """
    entries, section = [], ""
    for number, raw in enumerate(text.splitlines(), 1):
        line = _COMMENT.sub("", raw).strip()
        if not line:
            continue
        header = _SECTION.match(line)
        if header:
            kind = header.group("kind")
            if kind not in KINDS:
                return None, Code.XFAIL_BAD_FILE, (
                    f"第 {number} 行未知的分段头: [{kind}]")
            section = kind
            continue
        entry, message = _parse_entry(line, section)
        if entry is None:
            return None, Code.XFAIL_BAD_FILE, f"第 {number} 行{message}"
        entries.append(entry)
    return entries, Code.OK, ""


def load(path, root="", config=None):
    """读取清单文件，返回 (条目列表, 错误码, 说明)。文件不存在等于没有清单。

    走 source 判定编码：清单是工具写的 UTF-8 文件，但用户可能手工编辑过；
    判定不出编码时不猜、不覆盖，报错退回。
    """
    if not path or not Path(path).is_file():
        return [], Code.OK, ""
    text, info = source_mod.read_source(path, config)
    if text is None:
        return None, Code.XFAIL_BAD_FILE, (
            f"xfail 清单编码无法确定（{info.reason}）: {path}")
    return parse(text)


def describe(entry):
    """把登记项还原成便于展示的一行。"""
    parts = [entry.value]
    if entry.line:
        parts.append(str(entry.line))
    parts.append(entry.detail)
    return ":".join(parts)


def _same_file(root, path, registered):
    """诊断路径与登记路径是否指同一个文件。

    诊断给的是绝对路径，清单里写的是相对项目根的路径；两侧都归一化成相对
    路径再比，也接受"登记写成路径后缀"的简写。
    """
    if not path or not registered:
        return False
    target = Path(path).absolute()
    want = str(registered).replace("\\", "/").lstrip("./")
    try:
        relative = target.relative_to(Path(root or ".").absolute()).as_posix()
    except ValueError:
        # 不在项目根之下（例如系统头文件）：退回绝对路径比对，语义不变
        relative = target.as_posix()
    return relative == want or relative.endswith("/" + want)


class Checklist:
    """一次检查里登记清单的使用情况。

    登记项是否"已修复"只能由"本次有没有命中"推断，所以匹配与统计必须出自
    同一个对象，不能一处匹配、另一处另算一遍。
    """

    def __init__(self, registry):
        self.registry = registry
        self.used: list[Entry] = []

    def _hit(self, entry):
        if entry not in self.used:
            self.used.append(entry)
        return entry

    def style(self, path, line, check):
        """文件路径 + 行 + 检查名是否已登记，命中返回登记项。"""
        for entry in self.registry.entries:
            if entry.detail and check and entry.detail != check:
                continue
            if entry.line and line and entry.line != line:
                continue
            if not _same_file(self.registry.root, path, entry.value):
                continue
            return self._hit(entry)
        return None

    def stale(self, considered=None):
        """本次没命中的登记项，即已被修复、应当删行的那些。

        considered 给出本次真正检查过的文件；不在其中的条目无从判断，不能报
        "已修复"——增量模式只查改动集，全量报可移除会误伤。

        None 表示"不做文件级过滤"；传空列表表示"一个文件都没检查成"，此时
        任何条目都不能推断成已修复。两者必须分开：把空列表当成"不过滤"，恰好
        会在"全部文件都无法分析"时把整份清单误报成可移除。
        """
        result = []
        for entry in self.registry.entries:
            if entry in self.used:
                continue
            if considered is not None and not any(
                    _same_file(self.registry.root, item, entry.value)
                    for item in considered):
                continue
            result.append(entry)
        return result


def write(path, entries, config=None):
    """把登记项写成清单文本，返回 (错误码, 说明)。

    只写"文件:检查名"这一档：按文件整类登记，行数少、也贴合"这个文件的这类问题
    已经知道"的语义。行级登记留给人工细化。

    已存在的清单按它自己的编码与版式写回（用户可能手工加了注释行）；不存在则
    新建为 UTF-8。写不回去（编码判定不出或编不下）就报错，绝不改写原文件。
    """
    lines = ["# 由 cxx-standardize style --record 生成；只对新增违规判红",
             "[style]"]
    seen = set()
    for entry in sorted(entries, key=lambda item: (item.value, item.detail)):
        key = (entry.value, entry.detail)
        if key in seen:
            continue
        seen.add(key)
        lines.append("{}:{}".format(entry.value, entry.detail))
    text = "\n".join(lines) + "\n"
    code, message = source_mod.write_file(path, text, config)
    if code != 0:
        return Code.XFAIL_BAD_FILE, f"无法写入登记清单 {path}: {message}"
    return Code.OK, ""


def load_checklist(config):
    """按配置读取清单，返回 (Checklist, 错误码, 说明)。"""
    path = getattr(config, "xfail_file", "") or ""
    root = getattr(config, "root", "")
    entries, code, message = load(path, root, config)
    if code != Code.OK:
        return None, code, message
    return Checklist(Registry(str(path), str(root), entries or [])), Code.OK, ""


def verdict(checklist, considered=None, strict=False, hits=()):
    """汇总清单状态，返回 (错误码, 提示行列表)。

    considered 见 Checklist.stale。提示行由调用方打印，库只算结论——展示策略
    归各阶段，判定口径只有这一处。hits 是本次实际命中的键（可选）：给了它就能
    补齐第三态 **新增**（清单里没有、这次真的出现了）。三态收在一处，两侧的
    分类才有同一个口径可比。
    """
    notes = []
    stale = checklist.stale(considered)
    for entry in stale:
        notes.append("xfail 已修复，可移除: " + describe(entry))
    if hits:
        registered = set()
        for entry in getattr(checklist.registry, "entries", None) or ():
            key = getattr(entry, "value", "") or ""
            if key:
                registered.add(key)
        for key in hits:
            if key and key not in registered:
                notes.append("xfail 新增（清单里没有）: " + str(key))
    if strict and stale:
        return Code.XFAIL_STALE, notes
    return Code.OK, notes
