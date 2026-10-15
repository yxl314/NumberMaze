"""注释保护：让排版不触碰注释。

clang-format 没有**保列号**的等价能力：`AlignTrailingComments: true` 会把手工
对齐的行尾注释全部重排，`false` 更糟（全部塌成一个空格），`{Kind: Leave}` 保的
是代码与注释之间的间隔而不是绝对列号。三者都不是"保持作者写法"，所以这一层
必须自研。

做法是把注释换成块注释占位符交给 clang-format，占位符作为普通 token 会随
代码落到正确位置；排完再把占位符换回原注释文本，并按原列号补回空白。代码被
排长、越过原列号时注释退到代码后一格，这次退让会被记下来——它是"注释优先的
基线不成立"的直接证据，上层据此拒绝把这种结果当成稳定基线。
"""

from collections.abc import Sequence
from dataclasses import dataclass

# 占位符前缀候选。占位符要能被 clang-format 当作块注释接受，又不能与用户
# 代码里已有的同名标识符混淆，冲突时换用下一种。
MARKERS: tuple[str, ...] = ("CXX_CI_COMMENT", "CXX_CI_MARK", "CXX_CI_TMP")


@dataclass(frozen=True, slots=True)
class Comment:
    """一条注释及其在原文件里的位置。

    text 是含定界符的原文，own_line 表示注释前只有空白（独占一行），column
    是注释起点相对行首的列号（从 0 起），lead 是它前面的空白。column 与
    own_line 是还原时唯一的依据；注释内容本身只做原样回填，不再解析。
    """

    text: str
    lead: str
    own_line: bool
    column: int
    # 注释种类：line（`//`）或 block（`/* */`）。占位符必须保持同样的种类——
    # 行注释右边不可能再挂代码，clang-format 因此不会把下一行的 `{` 挪上来；
    # 换成块注释占位符就会，实测 `explicit Calc(int v) : m_v(v)  // 单位:毫秒`
    # 排完变成 `... // 单位:毫秒 {}`，`{` 被吃掉，再排一次结果又变
    kind: str = "block"
    # 代码末尾到注释起点之间的空格数。还原时按这个间距平移，而不是按绝对列号
    # 硬塞——代码变宽/变窄时注释跟着走，保持原有的视觉关系
    gap: int = 1


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

    引号本身不是注释的开头，但字符串里可以出现 `//` 与 `/*`：不先跳过字面量，
    扫描器会把 "http://x" 里的 `//` 当成行尾注释，后半行代码就全丢了。

    数字分隔符要先让开：`1'000` 里的 `'` 会被当成字符字面量的开头，一路吃到
    文件尾，那一行之后的注释全部丢失。
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
            # 转义序列整体跳过：`\"` 不结束字符串，`\\` 之后才是真引号
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    # 未闭合的字面量：吃到文件尾，宁可漏掉注释也不误判
    return n


def _skip_raw(text: str, i: int) -> int:
    """跳过 C++11 原始字符串字面量（含自定义定界符）。

    原始字符串里不转义，内容可以包含引号与 `//`；结束标记是 `)定界符"`，
    定界符由开头的 `R"定界符(` 决定。只按固定形状找结束会在自定义定界符上
    截错位置。
    """
    start = text.find("(", i + 2)
    if start < 0:
        return len(text)
    delim = text[i + 2:start]
    close = ")" + delim + '"'
    end = text.find(close, start)
    return len(text) if end < 0 else end + len(close)


def _line_start(text: str, i: int) -> int:
    """下标 i 所在行的行首下标。"""
    return text.rfind("\n", 0, i) + 1


def _own_line(text: str, i: int) -> bool:
    """注释是否独占一行（其前只有空白）。"""
    return text[_line_start(text, i):i].strip() == ""


def _lead(text: str, i: int) -> str:
    """注释前的空白，供调用方核对原始缩进。"""
    begin = i
    while begin > 0 and text[begin - 1] in " \t":
        begin -= 1
    return text[begin:i]


def scan_comments(text: str) -> list[Comment]:
    """找出所有注释，按出现顺序返回。

    扫描必须同时跳过字面量：注释符只有出现在代码里才算注释。块注释内部的
    引号同样是普通字符，不能让它开启字面量扫描——这一步交给"找到 `*/` 就
    整体跳过"来完成。
    """
    found: list[Comment] = []
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
        line_start = _line_start(text, i)
        kind = "line" if text.startswith("//", i) else "block"
        found.append(Comment(text[i:end], _lead(text, i), _own_line(text, i),
                             i - line_start, kind, _gap(text, i, line_start)))
        i = end
    return found


def marker_for(text: str, index: int) -> str:
    """选一个不与文本冲突的占位符前缀。

    index 是期望的起始候选（越界退回第一个），候选按顺序轮转尝试；全都被
    文本占用时返回最后一个：宁可占位符与代码同形，也不能返回空串让排版
    失去占位符。一次 strip/restore 必须用同一个前缀，因此调用方只选一次。
    """
    start = index if 0 <= index < len(MARKERS) else 0
    order = MARKERS[start:] + MARKERS[:start]
    for prefix in order:
        if prefix not in text:
            return prefix
    return MARKERS[-1]


def _gap(text: str, at: int, line_start: int) -> int:
    """注释起点与它前面代码末尾之间隔了几个空格。"""
    head = at
    while head > line_start and text[head - 1] in " \t":
        head -= 1
    return at - head


def strip_comments(text: str, prefix: str) -> str:
    """把注释换成块注释占位符，返回替换后的文本。

    位置在这里重新扫描确定，不吃调用方给的注释列表：`Comment` 只记列号不记
    绝对偏移，而"这一处是不是真的注释"只有走一遍全文才能判断。编号补齐四位，
    保证同一次替换里占位符长度一致。
    """
    out: list[str] = []
    i = 0
    n = len(text)
    k = 0
    while i < n:
        ch = text[i]
        if ch in "\"'" or text.startswith('R"', i):
            end = _skip_literal(text, i)
            out.append(text[i:end])
            i = end
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
        else:
            out.append(ch)
            i += 1
            continue
        # 占位符保持原注释的种类：见 Comment.kind 的说明
        if text.startswith("//", i):
            out.append(f"// {prefix}{k:04d}")
        else:
            out.append(f"/*{prefix}{k:04d}*/")
        k += 1
        i = end
    return "".join(out)


def restore_comments(text: str, comments: Sequence[Comment], prefix: str,
                     moved: list | None = None,
                     broken: list | None = None) -> str:
    """把占位符换回原注释，按原间距平移；代码列变了注释跟着走。

    给出 moved 时，把"注释列号与原来不同"的行尾注释记成
    (编号, 原列号, 排版后的列)。注释优先的基线要求列号不变，这种移动就是
    基线漂移的证据，上层据此判定，而不是把它当成代码问题。

    给出 broken 时，把"还原后注释后面还挂着代码"的行记成 (编号, 行号)。
    行注释右边绝不该再有代码——有就说明还原把代码变成了注释的一部分，
    这种产物可编译但语义已变，必须报错而不是写盘。
    """
    for k, item in enumerate(comments):
        token = placeholder(prefix, k, item.kind)
        at = text.find(token)
        if at < 0:
            # 占位符被 clang-format 吃掉（注释落在宏参数等奇怪位置）：留着
            # 不动比猜一个位置安全，注释文本仍会由调用方原先的字节兜底
            continue
        line_start = _line_start(text, at)
        if item.own_line:
            indent = text[line_start:at]
            text = text[:line_start] + indent + item.text + text[at + len(token):]
            continue
        head = at
        while head > line_start and text[head - 1] in " \t":
            head -= 1
        column = head - line_start
        # 按**原间距**平移，不按绝对列号硬塞：代码列变了注释跟着走，
        # 不会因为代码变宽而把注释挤到语法记号后面
        want = item.gap if item.gap > 0 else 1
        if column + want != item.column and moved is not None:
            moved.append((k, item.column, column + want))
        text = text[:head] + (" " * want) + item.text + text[at + len(token):]
        if broken is not None and item.kind == "line":
            # 从**注释末尾**往后看：注释本身当然占着这一行，要查的是它后面
            # 还有没有代码（那才是"代码被注释吃掉"）
            after = head + want + len(item.text)
            rest = text[after:_line_end(text, after)]
            if rest.strip():
                broken.append((k, text.count("\n", 0, after) + 1))
    return text


def _line_end(text: str, at: int) -> int:
    end = text.find("\n", at)
    return len(text) if end < 0 else end


def placeholder(prefix: str, index: int, kind: str = "block") -> str:
    """一条注释对应的占位符文本。种类必须与原注释一致，理由见 Comment.kind。"""
    if kind == "line":
        return f"// {prefix}{index:04d}"
    return f"/*{prefix}{index:04d}*/"
