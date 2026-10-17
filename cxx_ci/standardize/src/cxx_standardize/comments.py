"""注释保护：让排版不触碰注释。

做法分三步：先把注释从源码里摘掉得到纯代码，交给 clang-format 排版；再用
difflib 把"纯代码的行"与"排版后的行"对齐，按行把原注释回填——行尾注释挂回
对应行的行尾，独占一行的注释挂回它下方那条代码之前。注释文本与代码到注释的
间距原样保留。

按行对齐而不是按代码内容查找：`}` 这类短行在文件里出现多次，内容查找会把
注释挂到错误的一行上。对齐用代码行的顺序信息，重排、拆行都不会错位。
"""

import difflib
import re
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

# 兼容旧配置里的注释模式取值：none 关掉保护，其余一律按保护处理
OFF = "none"

# clang-format 的 FixNamespaceComments 会给命名空间收尾 `}` 补 `// namespace X`。
# 排纯代码时它是新造出来的注释，回填前丢弃。
_NS_COMMENT = re.compile(
    r"^(?P<body>.*\S)\s+//\s*namespace(?:\s+[A-Za-z_][\w:]*)?\s*$")


def drop_namespace_comments(text: str) -> str:
    """去掉 clang-format 自己补上的命名空间收尾注释。"""
    out = []
    for line in text.splitlines(keepends=True):
        body = line[:-1] if line.endswith("\n") else line
        match = _NS_COMMENT.match(body)
        if match and match.group("body").rstrip().endswith("}"):
            out.append(match.group("body") + ("\n" if line.endswith("\n") else ""))
        else:
            out.append(line)
    return "".join(out)


@dataclass(frozen=True, slots=True)
class Comment:
    """一条注释及其回填依据。

    text 含定界符的原文；own_line 表示注释独占一行；indent 是独占行注释的行首
    缩进；column 是行尾注释起点的原列号（从 0 起）；line 是它关联的纯代码行下标
    （行尾注释=所在行，独占行注释=下方第一条代码行；等于代码行数表示文件尾）。
    """

    text: str
    lead: str
    own_line: bool
    column: int
    kind: str = "block"
    gap: int = 1
    indent: str = ""
    line: int = 0


def _is_digit_separator(text: str, i: int) -> bool:
    """`'` 是不是 C++14 的数字分隔符（如 1'000、0x1F'0000）。

    分隔符两侧都是十六进制数字才这么判：字符字面量的两侧不会同时是数字
    （`'0'` 的右侧是引号，`x'a'` 的左侧是 x），因此不会误伤。
    """
    if i <= 0 or i + 1 >= len(text):
        return False
    before, after = text[i - 1], text[i + 1]
    return before in "0123456789abcdefABCDEF" and after in "0123456789abcdefABCDEF"


def _skip_literal(text: str, i: int) -> int:
    """跳过字符串或字符字面量，返回结束后的下标。

    字符串里可以出现 `//` 与 `/*`：不先跳过字面量，扫描器会把 "http://x" 里的
    `//` 当成行尾注释，后半行代码就全丢了。数字分隔符要先让开：`1'000` 里的
    `'` 被当成字符字面量开头会一路吃到文件尾。
    """
    if text[i] == "'" and _is_digit_separator(text, i):
        return i + 1
    quote = text[i]
    if quote == "R" and text.startswith('R"', i):
        return _skip_raw(text, i)
    i += 1
    n = len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return n


def _skip_raw(text: str, i: int) -> int:
    """跳过 C++11 原始字符串字面量（含自定义定界符）。

    结束标记是 `)定界符"`，定界符由开头的 `R"定界符(` 决定；只按固定形状找
    结束会在自定义定界符上截错位置。
    """
    start = text.find("(", i + 2)
    if start < 0:
        return len(text)
    delim = text[i + 2:start]
    close = ")" + delim + '"'
    end = text.find(close, start)
    return len(text) if end < 0 else end + len(close)


def _spans(text: str) -> list[tuple[int, int]]:
    """所有注释的 [起点, 终点) 区间。"""
    found: list[tuple[int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'" or text.startswith('R"', i):
            i = _skip_literal(text, i)
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
        else:
            i += 1
            continue
        found.append((i, end))
        i = end
    return found


def _line_offsets(text: str) -> list[int]:
    """每一行行首的下标。"""
    offsets = [0]
    for index, ch in enumerate(text):
        if ch == "\n":
            offsets.append(index + 1)
    return offsets


def _clean_lines(text: str, spans: Sequence[tuple[int, int]]) -> list[str]:
    """逐行给出"去掉注释后"的文本，行结构与原文件一一对应。

    换行一律保留（哪怕它落在块注释内部），这样 clean 的下标始终等于原文件
    行号，锚点与行号才对得上。
    """
    masked = bytearray(len(text))
    for start, end in spans:
        for i in range(start, end):
            masked[i] = 1
    lines: list[str] = []
    buf: list[str] = []
    for index, ch in enumerate(text):
        if ch == "\n":
            lines.append("".join(buf) + "\n")
            buf = []
            continue
        if not masked[index]:
            buf.append(ch)
    if buf:
        lines.append("".join(buf))
    return lines


def _parse(text: str) -> tuple[list[str], list[Comment]]:
    """摘注释：返回 (纯代码行, 注释列表)。

    纯代码行只保留含代码的行（仅注释的行整行删掉）；注释的 line 指向它在
    纯代码行里的关联行。
    """
    spans = _spans(text)
    if not spans:
        return text.splitlines(keepends=True), []
    clean = _clean_lines(text, spans)
    offsets = _line_offsets(text)
    covered: set[int] = set()
    for start, end in spans:
        first = bisect_right(offsets, start) - 1
        last = bisect_right(offsets, end - 1) - 1 if end > start else first
        for row in range(first, last + 1):
            covered.add(row)
    # 仅注释的行整行删掉；其余行（含空行）保留
    remap: dict[int, int] = {}
    kept: list[str] = []
    for row, line in enumerate(clean):
        if line.strip() == "" and row in covered:
            continue
        remap[row] = len(kept)
        kept.append(line)

    def code_index(row):
        """原行号 → 纯代码行下标；被删的行落到它后面第一行。"""
        for probe in range(row, len(clean)):
            if probe in remap:
                return remap[probe]
        return len(kept)

    comments: list[Comment] = []
    for start, end in spans:
        row = bisect_right(offsets, start) - 1
        line_start = offsets[row]
        lead = text[line_start:start]
        kind = "line" if text.startswith("//", start) else "block"
        # 独占一行 = 前面只有空白，且这一行本身没有代码（块注释后面可能还有代码）
        own_line = lead.strip() == "" and clean[row].strip() == ""
        if own_line:
            index = code_index(row + 1)
            comments.append(Comment(text[start:end], lead, True, 0, kind,
                                    indent=lead, line=index))
        else:
            head = start
            while head > line_start and text[head - 1] in " \t":
                head -= 1
            comments.append(Comment(text[start:end], text[head:start], False,
                                    start - line_start, kind,
                                    gap=start - head, line=code_index(row)))
    return kept, comments


def scan_comments(text: str) -> list[Comment]:
    """找出所有注释，按出现顺序返回。"""
    return _parse(text)[1]


def strip_comments(text: str) -> str:
    """去掉全部注释：仅注释的行整行删掉，含代码的行保留代码。"""
    return "".join(_parse(text)[0])


def split(text: str) -> tuple[str, list[Comment]]:
    """一次拿到纯代码文本与注释列表。"""
    lines, comments = _parse(text)
    return "".join(lines), comments


def _normalize(line: str) -> str:
    """去掉全部空白：对齐时忽略缩进与空格差异。"""
    return "".join(line.split())


def _row_map(code_lines: Sequence[str],
             formatted_lines: Sequence[str]) -> dict[int, tuple[int, int]]:
    """纯代码行下标 → (排版后起始行, 结束行)。

    用 difflib 对齐两边的代码行：重排、增删空行、把一条语句拆成多行都能对上。
    """
    matcher = difflib.SequenceMatcher(
        a=[_normalize(line) for line in code_lines],
        b=[_normalize(line) for line in formatted_lines],
        autojunk=False)
    mapping: dict[int, tuple[int, int]] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                mapping[i1 + offset] = (j1 + offset, j1 + offset)
        elif tag in ("replace", "delete", "insert"):
            last = max(j1, j2 - 1)
            for index in range(i1, i2):
                mapping[index] = (j1, last)
    return mapping


def restore_comments(formatted: str, comments: Sequence[Comment],
                     code_text: str) -> tuple[str, list]:
    """把注释回填到排版结果里，返回 (文本, 无法回填的注释下标)。

    code_text 是摘掉注释后的纯代码，用来与 formatted 做行对齐。
    """
    code_lines = code_text.splitlines()
    formatted_lines = formatted.splitlines()
    mapping = _row_map(code_lines, formatted_lines)
    total = len(formatted_lines)

    before: dict[int, list[Comment]] = {}
    after: dict[int, list[Comment]] = {}
    tail: list[Comment] = []
    unplaced: list[int] = []
    for index, item in enumerate(comments):
        if item.line >= len(code_lines):
            tail.append(item)
            continue
        start, end = mapping.get(item.line, (total, total))
        if item.own_line:
            before.setdefault(start, []).append(item)
        else:
            after.setdefault(end, []).append(item)

    out: list[str] = []
    for row, line in enumerate(formatted_lines):
        for item in before.get(row, ()):
            out.append(item.indent + item.text)
        newline = line
        if row in after:
            # 注释优先：钉回作者原来的列号；代码对齐后越过该列时才右移，
            # 让开代码只留一个空格
            newline = line.rstrip()
            for item in after[row]:
                spaces = max(item.column - len(newline), 1)
                newline = newline + (" " * spaces) + item.text
        out.append(newline)
    for item in tail:
        out.append(item.indent + item.text)
    text = "\n".join(out)
    if formatted.endswith("\n"):
        text += "\n"
    return text, unplaced
