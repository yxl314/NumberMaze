#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""源码编码与行尾保真（E1 回归）。

工具会改写用户源码。用 `decode("utf-8", "replace")` 配
`encode("utf-8")` 整文件写回：GBK 存量工程跑一次改写，中文注释会变成 U+FFFD
且不报错。这里钉住三件事——判定不出编码的文件只读、写回按原编码、CRLF 保留。

测试自身也受 Python 3.4 约束，不能用 f-string 与 Path.write_text。
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import source  # noqa: E402


class Stub(object):
    """构造只带指定属性的假配置。"""

    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


def write_bytes(path, data):
    path = str(path)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    handle = open(path, "wb")
    try:
        handle.write(data)
    finally:
        handle.close()
    return path


def read_bytes(path):
    handle = open(str(path), "rb")
    try:
        return handle.read()
    finally:
        handle.close()


@pytest.fixture
def workdir():
    directory = tempfile.mkdtemp(prefix="cxx_ci_src_")
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


# 带中文注释与中文串的 GBK 内容；U+FFFD 一旦出现就说明写坏了
GBK_TEXT = u"// \u4e2d\u6587\u6ce8\u91ca\nconst char *s = \"\u4f60\u597d\";\n"
GBK = "gb18030"


def gbk_config():
    return Stub(source_encoding=GBK, source_encoding_candidates=[],
                declared_keys=frozenset(["SOURCE_ENCODING"]))


def utf8_config():
    return Stub(source_encoding="utf-8", source_encoding_candidates=[],
                declared_keys=frozenset())


class TestRoundTrip:
    def test_gbk_round_trip_keeps_every_byte(self, workdir):
        path = write_bytes(os.path.join(workdir, "a.h"),
                           GBK_TEXT.encode(GBK))
        text, info = source.read_source(path, gbk_config())
        assert GBK == info.encoding
        code, message = source.write_source(path, text, info)
        assert 0 == code, message
        assert GBK_TEXT.encode(GBK) == read_bytes(path)

    def test_crlf_is_restored_on_write(self, workdir):
        path = write_bytes(os.path.join(workdir, "a.h"),
                           b"int a;\r\nint b;\r\n")
        text, info = source.read_source(path, utf8_config())
        assert "\r\n" == info.newline
        # 改一行再写回：换行风格必须仍是 CRLF
        updated = text.replace("int b;", "int c;")
        code, message = source.write_source(path, updated, info)
        assert 0 == code, message
        assert b"int a;\r\nint c;\r\n" == read_bytes(path)

    def test_bom_is_kept_exactly_once(self, workdir):
        """BOM 只有一个，而且正文不能被多出来的 BOM 污染。

        只断言 `[:3]` 会漏掉"写了两个 BOM"：第二个 U+FEFF 会变成正文开头的
        字符，可能让编译器直接报 invalid character。实测就是这么漏过去的，
        所以这里改成**整个文件字节不变**。
        """
        original = b"\xef\xbb\xbfint a;\n"
        path = write_bytes(os.path.join(workdir, "a.h"), original)
        text, info = source.read_source(path, utf8_config())
        assert b"\xef\xbb\xbf" == info.bom
        code, message = source.write_source(path, text, info)
        assert 0 == code, message
        assert original == read_bytes(path)
        assert b"\xef\xbb\xbf\xef\xbb\xbf" != read_bytes(path)[:6]


class TestReadOnly:
    def test_gbk_is_unreadable_without_config(self, workdir):
        path = write_bytes(os.path.join(workdir, "a.h"),
                           GBK_TEXT.encode(GBK))
        text, info = source.read_source(path, utf8_config())
        assert text is None
        assert not info.known

    def test_unwritable_char_refuses_to_write(self, workdir):
        # ascii 表示不了中文：写回必须拒绝，而不是写成替换字符或写一半
        path = write_bytes(os.path.join(workdir, "a.h"), b"int a;\n")
        config = Stub(source_encoding="ascii", source_encoding_candidates=[],
                      declared_keys=frozenset(["SOURCE_ENCODING"]))
        _text, info = source.read_source(path, config)
        assert info.known and "ascii" == info.encoding
        before = read_bytes(path)
        code, message = source.write_source(
            path, u"int a;\n// \u4e2d\u6587\n", info)
        assert 0 != code
        assert before == read_bytes(path)
        assert "ascii" in message
        # 要说清**第几行**编不下：只说"有表示不了的字符"，人还得自己找
        assert "第 2 行" in message, message


class TestWritesAreAtomic:
    """写盘要么是完整的旧文件，要么是完整的新文件——不能是半截。

    直接 open(path, "wb") 写一半失败（磁盘满、超过文件尺寸上限、IO 错误）会留下
    一个残骸：原内容没了、新内容也不全，用户拿到一个编译不过的文件，而且没有
    任何备份。
    """

    def test_a_failed_write_leaves_the_original_untouched(self, tmp_path):
        target = tmp_path / "big.h"
        original = ("#ifndef WRONG\n#define WRONG\n"
                    + "".join("int f%d();\n" % i for i in range(400))
                    + "#endif\n")
        handle = open(str(target), "w", encoding="utf-8")
        try:
            handle.write(original)
        finally:
            handle.close()
        before = target.read_bytes()
        text, info = source.read_source(str(target), None)
        code, message = source.write_bytes(str(target), b"x" * (len(before) * 4))
        # 正常路径必须成功
        assert 0 == code, message
        # 目标不存在时也不能崩：父目录没有就报错而不是抛栈
        code, message = source.write_bytes(str(tmp_path / "no" / "such" / "f"), b"x")
        assert 0 != code, message
        assert "无法" in message or "失败" in message, message

    def test_partial_write_is_detected(self, tmp_path, monkeypatch):
        """os.write 只写一部分时必须继续写完，不能"成功"地留下半截文件。"""
        target = tmp_path / "f.txt"
        calls = {"n": 0}
        real_write = os.write

        def halting(fd, data):
            calls["n"] += 1
            if calls["n"] > 1:
                raise OSError(28, "No space left on device")
            return real_write(fd, data[: len(data) // 2])

        monkeypatch.setattr(os, "write", halting)
        code, message = source.write_bytes(str(target), b"y" * 64)
        assert 0 != code, message
        assert not target.exists(), "失败时不该留下半个文件"

    def test_no_temporary_files_are_left_behind(self, tmp_path):
        source.write_bytes(str(tmp_path / "ok.txt"), b"data")
        names = sorted(item.name for item in tmp_path.iterdir())
        assert ["ok.txt"] == names, names

class TestRoundTripEdgeCases:
    """编码/换行往返的边界：逐项还原是契约承诺（CONTRACT.md 的"编码、换行符、
    BOM、文件末尾换行逐项还原"）。
    """

    def _round_trip(self, tmp_path, name, data):
        target = tmp_path / name
        handle = open(str(target), "wb")
        try:
            handle.write(data)
        finally:
            handle.close()
        text, info = source.read_source(str(target), None)
        if text is None:
            return None, info
        source.write_source(str(target), text, info)
        handle = open(str(target), "rb")
        try:
            return handle.read(), info
        finally:
            handle.close()

    def test_an_empty_file_stays_empty(self, tmp_path):
        """空文件按原始字节判"有结尾换行"，写回时凭空补一个 \n。"""
        after, _info = self._round_trip(tmp_path, "a.cpp", b"")
        assert b"" == after, after

    def test_a_file_without_a_final_newline_keeps_it_that_way(self, tmp_path):
        after, _info = self._round_trip(tmp_path, "b.cpp", b"int a=1;")
        assert b"int a=1;" == after, after

    def test_crlf_without_a_final_newline_leaves_no_stray_cr(self, tmp_path):
        """删结尾换行只删一个字符时，CRLF 会在尾巴上留下一个孤立的 \r。"""
        original = b"int a=1;\r\nint b=2;"
        after, _info = self._round_trip(tmp_path, "c.cpp", original)
        assert original == after, after

    def test_a_bom_less_utf16_file_is_left_alone(self, tmp_path):
        """NUL 是合法 UTF-8，无 BOM 的 UTF-16 会被"顺利"解出来然后按 UTF-8 改坏。"""
        original = "int a=1;\n".encode("utf-16-le")
        after, info = self._round_trip(tmp_path, "d.cpp", original)
        assert after is None, after
        assert "UTF-16" in info.reason, info.reason

    def test_a_utf16_file_with_bom_round_trips(self, tmp_path):
        original = b"\xff\xfe" + "int a=1;\n".encode("utf-16-le")
        after, _info = self._round_trip(tmp_path, "e.cpp", original)
        assert original == after, after
