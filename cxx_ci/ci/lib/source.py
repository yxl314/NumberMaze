#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""源码文本的唯一读写入口：编码、换行、BOM 全程保真。

与 standardize/src/cxx_standardize/source.py 是**平行实现**：两个项目互不
import，只靠文件与命令行契约协作（契约见 CONTRACT.md）。因此这里不共享代码，
但两侧的判定顺序、写前校验与配置项语义必须一致，改动须同步；两边的 GBK/CRLF
回归测试就是钉住这份一致性的地方。

**为什么必须有这个模块**：本工具要改写用户的代码。用 `decode("utf-8", "replace")`
读、用 `encode("utf-8")` 整文件写回时，解不出的字节会变成 U+FFFD 而不抛异常
——一个 GBK 编码的存量工程跑一次改写，**每一处中文注释与中文字符串字面量都会
变成 U+FFFD**，而且不报错、退出码可以是 0（U+FFFD 是合法字符，照样编得过），
事后不可逆。这不是效果问题，是工具会吃掉用户的代码。

同族的第二类静默改写是换行符：CRLF 的存量文件经过"按行切分→重排→整体写回"
之后必须仍是 CRLF，这一点也在这里收口。

**原则**：
  - 判定不出编码的文件一律**只读**：检查照报，改写跳过并说明原因。
  - 写回时按原编码 strict 编码，编不下（例如 GBK 文件里出现了 GBK 表示不了的
    新字符）就拒绝写并指出来，绝不"写一半"。
  - 除改写的意图之外，其余字节不变：编码、换行、BOM、末尾换行逐项还原。

判定顺序（`SOURCE_ENCODING`）：
  BOM → 单一编码严格解码（缺省 `utf-8`）→ 写 `"auto"` 时改用
  `SOURCE_ENCODING_CANDIDATES` 逐个严格试 → 放弃。**候选列表缺省为空**：不猜
  GBK——猜错比跳过危险得多。
"""

import codecs
import os
import sys
import tempfile
if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")

# 认得出的 BOM：按长度倒序试，避免 utf-16-le 抢在 utf-16 前面
BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)

DEFAULT_ENCODING = "utf-8"


class SourceInfo(object):
    """一份源码文件的编码与版式事实，写回时逐项还原。"""

    def __init__(self, path=""):
        self.path = path
        # 判定出的编码（Python 的编码名，如 utf-8 / gb18030 / utf-8-sig）
        self.encoding = ""
        # 判定来源，用于报错："bom" / "utf-8" / "configured" / "candidate" /
        # "new" / ""（未判定出）
        self.origin = ""
        # 换行符："\r\n" 或 "\n"
        self.newline = "\n"
        # BOM 字节，写回时原样前置
        self.bom = b""
        # 原文件末尾是否有换行
        self.had_final_newline = True
        # 编码是否已判定。False 表示"只读"：可以出检查结果，不可以改写
        self.known = False
        # 只读原因（known 为 False 时有值）
        self.reason = ""
        # 非缺省编码的判定是否来自用户显式配置
        self.explicit = False

    def describe(self):
        if not self.known:
            return "编码未判定（{}）".format(self.reason)
        return "{}（{}），换行 {}".format(
            self.encoding, self.origin,
            "CRLF" if self.newline == "\r\n" else "LF")


def _detect_newline(data):
    """文件主要用哪种换行。以出现次数多的一种为准。"""
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _bom_of(data):
    for marker, encoding in BOMS:
        if data.startswith(marker):
            return marker, encoding
    return b"", ""


def _candidates(config):
    """返回 (候选编码列表, 是否单一编码模式)。

    非 auto 时只认 SOURCE_ENCODING 本身（缺省 utf-8）；auto 时改用
    SOURCE_ENCODING_CANDIDATES 列表。
    """
    setting = str(getattr(config, "source_encoding", DEFAULT_ENCODING)
                  or DEFAULT_ENCODING).strip()
    if setting.lower() not in ("auto", ""):
        return [setting], True
    found = []
    for item in getattr(config, "source_encoding_candidates", None) or ():
        text = str(item or "").strip()
        if text and text not in found:
            found.append(text)
    return found, False


def _declared(config, name):
    """这个键是不是用户显式写在配置文件里的。"""
    return name in (getattr(config, "declared_keys", None) or ())


def read_source(path, config):
    """读一份源码，返回 (文本或 None, SourceInfo)。

    文本为 None 表示编码未判定：调用方可以出检查结果，但不得改写该文件。
    """
    info = SourceInfo(path=str(path))
    try:
        handle = open(str(path), "rb")
    except (IOError, OSError) as exc:
        info.reason = "无法读取: {}".format(exc)
        return None, info
    try:
        data = handle.read()
    finally:
        handle.close()

    info.newline = _detect_newline(data)
    info.had_final_newline = data.endswith(b"\n") or data == b""
    info.explicit = _declared(config, "SOURCE_ENCODING")

    marker, bom_encoding = _bom_of(data)
    if marker:
        info.bom, info.encoding, info.origin, info.known = (
            marker, bom_encoding, "bom", True)
        return _decode(data[len(marker):], info)

    candidates, single = _candidates(config)
    # 先严格试 UTF-8：绝大多数文件在这里就定了，也绝不冤枉一个合法的 UTF-8。
    # 显式指定了别的编码（single 且不是 utf-8）则不试，免得被恰好合法的文件绕过
    if not single or candidates[0] == DEFAULT_ENCODING:
        text = _try(data, DEFAULT_ENCODING, info)
        if text is not None:
            info.origin = "utf-8"
            return text, info
        if single and candidates[0] == DEFAULT_ENCODING:
            info.reason = ("不是合法 UTF-8。缺省只认 UTF-8；"
                           "老编码工程请显式配 SOURCE_ENCODING")
            return None, info
    if not candidates:
        info.reason = ("不是合法 UTF-8，且未配置 SOURCE_ENCODING_CANDIDATES"
                       "（本工具不猜编码）")
        return None, info
    for name in candidates:
        text = _try(data, name, info)
        if text is not None:
            info.origin = "configured" if single else "candidate"
            return text, info
    info.reason = "候选编码都解不出: " + "、".join(candidates)
    return None, info


def _try(data, encoding, info):
    try:
        text = data.decode(encoding, errors="strict")
    except (UnicodeDecodeError, LookupError):
        return None
    info.encoding, info.known = encoding, True
    return text


def _decode(body, info):
    try:
        text = body.decode(info.encoding, errors="strict")
    except (UnicodeDecodeError, LookupError) as exc:
        info.known = False
        info.reason = "BOM 指明的编码解不出: {}".format(exc)
        return None, info
    return text, info


def restore_newlines(text, info):
    """把 LF 文本还原成原文件的换行风格。

    统一在"读出来是 LF、写回去按原样"这一层做转换，边界上一次性转完。
    """
    if not info.newline or info.newline == "\n":
        return text
    return text.replace("\r\n", "\n").replace("\n", info.newline)


def _body_encoding(encoding):
    """正文该用的编码名：BOM 由 info.bom 单独前置，编码器不能再加一个。

    `utf-8-sig` 这个编解码器**自带 BOM**：`"x".encode("utf-8-sig")` 已经含
    `EF BB BF`，再前置 info.bom 就写出两个 BOM。第二个会变成正文开头的
    U+FEFF，可能直接让编译器报 invalid character——实测每写一次就多一个。
    BOM 的写入只由 info.bom 一处负责，这里换成不带 BOM 的编码名。
    """
    return BOM_LESS.get(encoding, encoding)


# 编解码器自身会加 BOM 的名字 → 等价的"不带 BOM"编码名
BOM_LESS = {
    "utf-8-sig": "utf-8",
    "utf-16": "utf-16-le",
    "utf-32": "utf-32-le",
}


def encode_source(text, info):
    """按原编码编码，返回 (字节或 None, 说明)。

    编不下就返回 None 与"哪一行编不下"：宁可拒绝写，也不写一半。
    """
    if not info.known:
        return None, "编码未判定，不能写回"
    body = restore_newlines(text, info)
    if info.had_final_newline and not body.endswith("\n"):
        body += info.newline
    elif not info.had_final_newline and body.endswith("\n"):
        body = body[:-1]
    try:
        return info.bom + body.encode(_body_encoding(info.encoding),
                                      errors="strict"), ""
    except UnicodeEncodeError as exc:
        line = body.count("\n", 0, exc.start) + 1
        return None, ("原文是 {}，写回时第 {} 行有它表示不了的字符: {!r}"
                      .format(info.encoding, line, body[exc.start:exc.end]))


def write_bytes(path, payload):
    """原子写入：先写同目录临时文件、fsync、再改名覆盖。

    直接 open(path, "wb") 写一半失败（磁盘满、超过文件尺寸上限、IO 错误）会留
    下一个**半截文件**：原内容没了，新内容也不完整。用户拿到的是一个编译不过
    的残骸，而且没有任何备份。改名在同一文件系统上是原子的，因此要么是旧的
    那一份，要么是完整的新那一份。

    返回 (错误码, 说明)；失败时原文件一个字节都没动。
    """
    path = str(path)
    directory = os.path.dirname(path) or "."
    temporary = None
    try:
        handle, temporary = tempfile.mkstemp(prefix=".cxx-write-", dir=directory)
    except (IOError, OSError) as exc:
        return 2, "无法在 {} 下建临时文件: {}".format(directory, exc)
    try:
        try:
            # os.write 可能只写进去一部分（磁盘满、超过文件尺寸上限时就是），
            # 必须自己循环到写完：忽略返回值会"成功"地留下一个半截文件
            written = 0
            while written < len(payload):
                count = os.write(handle, payload[written:])
                if not count:
                    raise IOError("写入 {} 字节后停滞".format(written))
                written += count
            os.fsync(handle)
        finally:
            os.close(handle)
        os.replace(temporary, path)
        temporary = None
    except (IOError, OSError) as exc:
        return 2, "写入 {} 失败（原文件未改动）: {}".format(path, exc)
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                # 清理临时文件是尽力而为：删不掉也不该盖住上面那个真正的写入错误
                pass
    return 0, ""


def write_source(path, text, info):
    """按原编码与版式写回，返回 (错误码, 说明)。

    编码已判定、且 strict 编得下才写；否则原文件一个字节都不动。写入是原子的：
    写一半失败时留下的是原文件，不是残骸。
    """
    payload, message = encode_source(text, info)
    if payload is None:
        return 1, message
    return write_bytes(path, payload)


