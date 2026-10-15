#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置迁移：把旧项名一次性改干净，而不是每次运行时提示一句。

旧名（`WHITELIST` / `BLACKLIST`）改名为 `SCOPE` 之后，只保留"运行到就提示一次"
的做法，结果是每个工程自己决定什么时候改、改一半的配置文件留在仓库里：版本升了
好几轮，配置文件还停在两代前。这里让"升级配置"变成一个可执行的动作。

规则表驱动，改写前先 dry-run 打印 diff，落盘前写 `.bak`，改完把需要人工复核的
项点名列出。与 `source.py` 同一条纪律：**解不出编码 / 规则有歧义就不写**——
文本改写最怕的是把用户没打算动的地方一起改了。

本模块只做**文本层面的项名替换**，不执行被改的配置：执行它意味着把项目代码跑
起来，而迁移工具没有理由有那个权限。改名之外的东西（例如把两个列表合成一个
字典）交给人，工具只把锚点摆到眼前。
"""

import os
import re
import shutil
import sys

# 单行列表字面量：`[ ... ]` 里不含中括号与换行，才能安全地搬进字典
LIST_LINE = re.compile(r"^[ \t]*([A-Z_]+)[ \t]*=[ \t]*(\[[^\[\]]*\])[ \t]*$")

RULES = (
    # WHITELIST 的新名**不是** SCOPE 这个标识符：取值要从列表变成字典，形状变了。
    # 成对出现时由上面的合并逻辑处理；只出现一边时只报告、不动手
    ("WHITELIST", None, "SCOPE = {\"project\": <原 WHITELIST>, "
                        "\"excluded\": <原 BLACKLIST>}"),
    ("BLACKLIST", None, "与 WHITELIST 一并合成 SCOPE（见上一条）"),
    # 名字差一个前缀，写起来毫无察觉，而配错的后果是日志落到别处且没有提示
    ("LOG_DIR", "CI_LOG_DIR", "阶段日志目录的实际键名是 CI_LOG_DIR"),
)

BACKUP_SUFFIX = ".bak"

# 新名必须是纯标识符才算"改名"：带赋值语法的新名是另一套规则（改形状）
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _list_assignment(name, text):
    """找出 `NAME = [ ... ]` 单行列表，返回 (行号, 整行, 列表文本)。"""
    number, line = _assignment(name, text)
    if not number:
        return None
    match = LIST_LINE.match(line)
    if not match:
        return None
    return number, line, match.group(2)


def _assignment(name, text):
    """找出 `NAME = ...` 这类赋值所在的行号（1 起）与整行文本。

    只用行首锚定的正则：注释里提到某个配置项名不算配置。
    """
    pattern = re.compile(r"^[ \t]*" + re.escape(name) + r"[ \t]*=")
    for number, line in enumerate(text.splitlines(), 1):
        if pattern.match(line):
            return number, line
    return 0, ""


def plan(text):
    """算出要改哪几行，返回 (命中列表, 需要人工复核的说明列表)。

    命中列表每项是 (行号, 旧行, 新行, 新名)。

    WHITELIST/BLACKLIST 合成 SCOPE 需要**改取值形状**（两个列表 → 一个字典）。
    只在两行都是单行列表字面量、且彼此相邻时自动合并；形状不是这样就只报告
    不动手——"改名之后配置反而加载不了"比不迁移更糟，与 source.py 同一条纪律：
    规则有歧义就不写。
    """
    hits = []
    notes = []
    white = _list_assignment("WHITELIST", text)
    black = _list_assignment("BLACKLIST", text)
    if white and black:
        (first, wline, wvalue) = white
        (second, bline, bvalue) = black
        if abs(first - second) == 1:
            if _assignment("SCOPE", text)[0]:
                # 文件里已经有 SCOPE：再合并一次会写出两处赋值，改完更难读。
                # 只报告，由人决定删哪一份
                notes.append("第 {}、{} 行：文件里已经有 SCOPE 了，"
                             "请手工删掉这两行".format(first, second))
            else:
                merged = 'SCOPE = {{"project": {}, "excluded": {}}}'.format(
                    wvalue, bvalue)
                hits.append((first, wline + "\n" + bline, merged, "SCOPE"))
                notes.append("第 {}、{} 行：WHITELIST/BLACKLIST 已合并成 "
                             "SCOPE = {{project, excluded}}，请复核两个键的顺序"
                             .format(first, second))
    elif white or black:
        line = white or black
        notes.append("第 {} 行 {}：SCOPE 是字典 "
                     "{{\"project\": [...], \"excluded\": [...]}}，"
                     "本工具只在 WHITELIST 与 BLACKLIST **同时**是单行列表时"
                     "自动合并；这里形状不齐，请手工合并"
                     .format(line[0], "WHITELIST" if white else "BLACKLIST"))
    for old, new, why in RULES:
        if new is None:
            # 只报告不代改的条目（例如 BLACKLIST 要靠 WHITELIST 一起才成形）
            if white and black:
                continue
            number, _line = _assignment(old, text)
            if number:
                notes.append("第 {} 行 {}：{}".format(number, old, why))
            continue
        # 只有"新名是纯标识符"才算改名：取值形状不变，整行换个名字就行。
        # WHITELIST → SCOPE 那条要把两个列表合成一个字典，形状变了，必须走
        # 上面的人工复核路径——照着改名去改会写出一份加载不了的配置
        if not IDENTIFIER.match(new):
            number, _line = _assignment(old, text)
            if number:
                notes.append("第 {} 行 {}：{}".format(number, old, why))
            continue
        number, line = _assignment(old, text)
        if not number:
            continue
        if _assignment(new, text)[0]:
            # 目标名已经在文件里了：再写一遍会得到两处赋值，改完反而更难读
            notes.append("第 {} 行：{} 要改名为 {}，但文件里已经有 {} 了，"
                         "请手工合并".format(number, old, new, new))
            continue
        renamed = re.sub(r"^(\s*)" + re.escape(old) + r"(\s*=)",
                         lambda match: match.group(1) + new + match.group(2),
                         line)
        hits.append((number, line, renamed, new))
        notes.append("第 {} 行：{} 改名为 {}（{}）".format(number, old, new, why))
    return hits, notes


def render_diff(path, hits):
    """统一的 diff 文本，供 dry-run 与确认后打印。"""
    lines = ["--- {}".format(path), "+++ {}（迁移后）".format(path)]
    for number, old, new, _name in hits:
        lines.append("@@ 第 {} 行 @@".format(number))
        lines.append("-" + old)
        lines.append("+" + new)
    return "\n".join(lines)


def migrate(path, dry_run=True):
    """执行（或预演）迁移，返回 (错误码, 输出文本)。

    错误码 0 表示"没有需要改的"或"已改好"；1 表示需要人工复核（dry-run 命中）；
    2 表示读不出/写不回（编码判定不出），此时一个字节都不动。
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from lib import source  # noqa: E402

    text, info = source.read_source(path, _MinimalConfig())
    if text is None:
        return 2, "编码无法确定（{}）: {}，本文件不会被改写".format(
            info.reason, path)
    hits, notes = plan(text)
    if not hits and not notes:
        return 0, "{}：没有需要迁移的旧项".format(path)
    out = [render_diff(path, hits)] if hits else []
    if dry_run:
        out.append("（dry-run：没有写入。确认后去掉 --dry-run 落盘）")
        return 1 if hits else 0, "\n".join(out + notes)
    if not hits:
        # 只有需要人工处理的项：不动文件
        return 1, "\n".join(out + notes)
    updated = text
    for _number, old, new, _name in hits:
        # 命中可能是"两行合一"（WHITELIST+BLACKLIST），整段替换
        updated = updated.replace(old + "\n", new + "\n", 1)
    backup = path + BACKUP_SUFFIX
    shutil.copyfile(path, backup)
    code, message = source.write_source(path, updated, info)
    if code != 0:
        shutil.copyfile(backup, path)
        return 2, "写回失败，已还原: " + message
    out.append("已写入 {}（原文件备份在 {}）".format(path, backup))
    return 1, "\n".join(out + notes)


class _MinimalConfig(object):
    """迁移只读一个配置文件，编码按缺省只认 UTF-8。"""

    source_encoding = "utf-8"
    source_encoding_candidates = ()
