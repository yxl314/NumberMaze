"""注释保护的单元测试。

要守住两件事：扫描器不会被字面量里的注释符骗到（否则注释文本直接被摘掉，
代码会损坏），以及"摘注释—排版—按代码行回填"能原样还原注释文本与间距。
"""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

# 本包只要求 pip 可装，但测试要在未安装的源码树上直接跑得起来
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cxx_standardize.comments import (
    Comment,
    drop_namespace_comments,
    restore_comments,
    scan_comments,
    split,
    strip_comments,
)

SOURCE = """int f(int a, int b) {
  int x = a + b;      // 行尾注释
  // 独占一行
  return x;
}
"""


class TestScan:
    def test_records_text_column_and_own_line(self):
        text = "int a = 1; // 行尾\n// 独占\n"
        found = scan_comments(text)
        assert ["// 行尾", "// 独占"] == [item.text for item in found]
        assert not found[0].own_line
        assert found[0].column == text.index("// 行尾")
        assert found[1].own_line
        assert found[1].column == 0

    def test_block_comment_is_one_comment(self):
        text = "/* 第一行\n   第二行 */int a;\n"
        found = scan_comments(text)
        assert ["/* 第一行\n   第二行 */"] == [item.text for item in found]

    def test_quote_inside_char_literal_is_not_a_string(self):
        """字符字面量里的双引号不能开启字符串扫描。"""
        text = "char q = '\"'; // 行尾\n"
        found = scan_comments(text)
        assert ["// 行尾"] == [item.text for item in found]
        assert found[0].column == text.index("// 行尾")

    def test_slashes_inside_string_are_not_a_comment(self):
        """字符串里的 // 是 URL，不是注释。"""
        text = 'const char *url = "http://x/y"; // 行尾\n'
        found = scan_comments(text)
        assert ["// 行尾"] == [item.text for item in found]
        assert found[0].column == text.index("// 行尾")

    def test_escaped_backslash_does_not_end_string(self):
        """字符串末尾的 `\\\\` 是两个字符，后面的引号才结束字符串。"""
        text = 'const char *p = "a\\\\"; // 行尾\n'
        found = scan_comments(text)
        assert ["// 行尾"] == [item.text for item in found]

    def test_escaped_quote_does_not_end_string(self):
        text = 'const char *p = "a\\"b"; // 行尾\n'
        found = scan_comments(text)
        assert ["// 行尾"] == [item.text for item in found]

    def test_raw_string_with_delimiter_hides_markers(self):
        """原始字符串（含自定义定界符）里的 // 与 /* 都不算注释。"""
        text = 'auto s = R"tag( // 不是注释 /* 也不是 )tag"; // 行尾\n'
        found = scan_comments(text)
        assert ["// 行尾"] == [item.text for item in found]

    def test_default_delimited_raw_string_is_skipped(self):
        text = 'auto s = R"( /* 不是注释 */ )"; // 行尾\n'
        found = scan_comments(text)
        assert ["// 行尾"] == [item.text for item in found]

    def test_quote_inside_block_comment_is_ignored(self):
        """块注释里的单引号不能开启字符字面量扫描。"""
        text = "/* don't */ int a = 1; // 行尾\n"
        found = scan_comments(text)
        assert ["/* don't */", "// 行尾"] == [item.text for item in found]

    def test_preprocessor_trailing_comment_is_scanned(self):
        text = "#include <vector> // 需要\nint a;\n"
        found = scan_comments(text)
        assert ["// 需要"] == [item.text for item in found]
        assert not found[0].own_line
        assert found[0].column == text.index("// 需要")

    def test_comment_lead_is_kept(self):
        text = "int a;   // 行尾\n"
        found = scan_comments(text)
        assert "   " == found[0].lead


class TestRoundTrip:
    def test_strip_removes_every_comment(self):
        stripped = strip_comments(SOURCE)
        assert "// 行尾注释" not in stripped
        assert "// 独占一行" not in stripped
        # 仅注释的整行被删掉，含代码的行保留
        assert "int x = a + b;" in stripped
        assert stripped.count("\n") == 4

    def test_strip_keeps_code_inside_strings(self):
        text = 'const char *s = "/* 不是注释 */";\n'
        assert [] == scan_comments(text)
        assert text == strip_comments(text)

    def test_restore_recovers_original_without_reformat(self):
        code, found = split(SOURCE)
        restored, unplaced = restore_comments(code, found, code)
        assert [] == unplaced
        assert SOURCE == restored

    def test_trailing_comment_follows_reindented_code(self):
        """代码缩进变了，行尾注释仍挂回同一行，间距按原样。"""
        text = "int x = 1; // 单位:毫秒\n"
        code, found = split(text)
        reformatted = "    int x = 1;\n"  # clang-format 改了缩进
        restored, unplaced = restore_comments(reformatted, found, code)
        assert [] == unplaced
        assert "    int x = 1; // 单位:毫秒\n" == restored

    def test_trailing_comment_keeps_its_column(self):
        """代码被排短时，注释仍钉在作者原来的列号上。"""
        text = "int   x   =   1;      // 对齐\n"
        code, found = split(text)
        reformatted = "int x = 1;\n"  # clang-format 把多空格压成一个，代码变短
        restored, _ = restore_comments(reformatted, found, code)
        assert restored.index("// 对齐") == text.index("// 对齐")

    def test_trailing_comment_steps_aside_when_code_overlaps(self):
        """代码排长越过原列号时，注释右移一个空格让开。"""
        text = "int x = 1; // 单位\n"
        code, found = split(text)
        reformatted = "int " + "x" * 40 + " = 1;\n"
        restored, _ = restore_comments(reformatted, found, code)
        assert restored == reformatted.rstrip() + " // 单位\n"

    def test_own_line_comment_inserted_before_its_code(self):
        text = "void f() {\n    // 独占\n    g();\n}\n"
        code, found = split(text)
        assert "// 独占" not in code
        restored, unplaced = restore_comments(code, found, code)
        assert [] == unplaced
        assert text == restored

    def test_short_anchor_matches_whole_line_not_substring(self):
        """`}` 不能被当成子串匹配到 `};` 上，注释要落在正确的那一行。"""
        text = "struct S {};\nint f() {\n  return 1;\n} // 收尾\n"
        code, found = split(text)
        restored, unplaced = restore_comments(code, found, code)
        assert [] == unplaced
        assert text == restored


class TestComment:
    def test_comment_is_an_immutable_value(self):
        item = Comment("// x", "  ", False, 4)
        assert Comment("// x", "  ", False, 4) == item
        with pytest.raises(FrozenInstanceError):
            item.text = "// y"  # type: ignore[misc]


def test_clang_format_namespace_comment_is_dropped():
    """clang-format 会给命名空间收尾 `}` 补注释，回填前要去掉。"""
    assert "}\n" == drop_namespace_comments("} // namespace mvc\n")
    assert "}\n" == drop_namespace_comments("} // namespace\n")
    # 不是收尾 `}` 的注释不动
    assert "x(); // namespace\n" == drop_namespace_comments("x(); // namespace\n")


def test_digit_separator_is_not_a_char_literal():
    """C++14 的 `1'000` 里那个撇号不是字符字面量的开头。

    判错会一路吃到文件尾，那一行之后的注释全部丢失——比"多认一条"严重得多。
    """
    text = "int a = 1'000; // 行尾\nint b = 0x1F'0000; /* 块 */\n"
    found = scan_comments(text)
    assert ["// 行尾", "/* 块 */"] == [item.text for item in found]


def test_char_literal_still_wins_over_digit_separator():
    """撇号两侧不同时是十六进制数字时，仍然是字符字面量。"""
    text = "if (c == '\\'') return; // 转义单引号\nchar d = 'a'; // 字符\n"
    found = scan_comments(text)
    assert ["// 转义单引号", "// 字符"] == [item.text for item in found]
