"""源码编码与行尾保真（E1 回归，standardize 侧）。

工具会改写用户源码。用 `decode("utf-8", "replace")` 配 `encode("utf-8")`
整文件写回时：GBK 存量工程跑一次排版，中文注释与中文字符串都会变成 U+FFFD，
而且不报错（U+FFFD 是合法字符，退出码可以是 0）。

这里钉住三件事：配了 `SOURCE_ENCODING` 时按原编码写回且中文不损；没配时
判定不出就跳过并让退出码非零；CRLF 文件跑完仍是 CRLF。

与 ci/tests/unit/test_source.py 是同一份回归的两侧平行实现——两个项目互不
import，一致性靠这两份测试各自钉住（对照见 tools/parity）。
"""

from std_support import Project

# 假的 clang-format：问版本时报 17.x；给内容时把连续空格压成一个。
# 这个变换幂等，不碰中文（中文不是空白），也不会动注释占位符（占位符里没有
# 连续空格），足以驱动一次真实可复现的"重排"。
FAKE_FORMAT = """#!/usr/bin/env python3
import re
import sys

if "--version" in sys.argv:
    print("Ubuntu clang-format version 17.0.6")
    sys.exit(0)

sys.stdout.write(re.sub(" {2,}", " ", sys.stdin.read()))
"""

CJK_COMMENT = "// 你好，中文注释\n"


def gbk_bytes():
    return (CJK_COMMENT + "int   value = 1;\n").encode("gb18030")


def write_bytes(project, relative, data):
    path = project.path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def with_encoding(project, encoding):
    project.configure(project.read("std/config/config.py")
                      + "\nSOURCE_ENCODING = " + repr(encoding) + "\n")


def test_gbk_file_is_reformatted_without_losing_chinese(project):
    """配了 gb18030：排版照样做，中文注释按原编码写回，不出现 U+FFFD。"""
    path = write_bytes(project, "src/gbk.h", gbk_bytes())
    with_encoding(project, "gb18030")
    directory = project.fake_tool("clang-format", FAKE_FORMAT)
    code, out, err = project.run(["format", "--apply"],
                                 env=project.env(directory))
    assert 0 == code, (out, err)
    data = path.read_bytes()
    # 写回仍是合法 gb18030：若被 UTF-8 覆盖，这里先解不出来
    text = data.decode("gb18030")
    assert "你好，中文注释" in text
    assert "int value = 1;" in text
    assert "\ufffd" not in text
    assert b"\xef\xbf\xbd" not in data


def test_gbk_file_is_skipped_when_encoding_is_not_configured(project):
    """没配 SOURCE_ENCODING：判定不出就只读，文件一个字节都不动，退出码非零。"""
    path = write_bytes(project, "src/gbk.h", gbk_bytes())
    before = path.read_bytes()
    directory = project.fake_tool("clang-format", FAKE_FORMAT)
    code, out, err = project.run(["format", "--apply"],
                                 env=project.env(directory))
    assert 0 != code, (out, err)
    assert before == path.read_bytes()
    assert "编码无法确定" in (out + err)


def test_crlf_is_preserved_across_reformatting(project):
    """CRLF 的存量文件排完仍是 CRLF，不塌成 LF。"""
    path = write_bytes(project, "src/crlf.cpp",
                       b"int   a = 1;\r\nint b = 2;\r\n")
    directory = project.fake_tool("clang-format", FAKE_FORMAT)
    code, out, err = project.run(["format", "--apply"],
                                 env=project.env(directory))
    assert 0 == code, (out, err)
    data = path.read_bytes()
    assert data.count(b"\n") == data.count(b"\r\n")
    assert b"int a = 1;\r\n" in data


def test_bom_is_kept_exactly_once(project):
    """带 BOM 的 UTF-8 文件写回后 BOM 只有一个。

    `utf-8-sig` 编码器自带 BOM，再前置 info.bom 就会写出两个——第二个变成
    正文开头的 U+FEFF。只断言前 3 字节看不出来，所以断言整个文件字节不变。
    """
    from cxx_standardize import source

    project.write(".clang-format", "BasedOnStyle: LLVM\n")
    original = b"\xef\xbb\xbfint a;\n"
    path = project.path / "src" / "a.cpp"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(original)
    # 编码判定只需要这两个属性，用一个最小替身，不必把配置真加载一遍
    class _Config:
        source_encoding = "utf-8"
        source_encoding_candidates = ()
        declared_keys = ()

    text, info = source.read_source(path, _Config())
    payload, message = source.encode_source(text, info)
    assert payload is not None, message
    assert original == payload
