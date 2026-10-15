"""注释保护的单元测试。

要守住两件事：扫描器不会被字面量里的注释符骗到（否则注释文本直接被吞进
占位符替换，代码会损坏），以及"占位符—注释"的往返在代码没有变位时逐列还原
（否则注释优先的基线永远不可复现）。
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
    MARKERS,
    Comment,
    marker_for,
    restore_comments,
    scan_comments,
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
        assert found[0].own_line

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


class TestMarker:
    def test_prefers_the_first_free_candidate(self):
        assert MARKERS[0] == marker_for("int a;", 0)

    def test_skips_candidates_already_in_text(self):
        text = "// " + MARKERS[0]
        assert MARKERS[1] == marker_for(text, 0)

    def test_falls_back_to_last_candidate(self):
        text = MARKERS[0] + MARKERS[1] + MARKERS[2]
        assert MARKERS[-1] == marker_for(text, 0)

    def test_index_rotates_the_candidates(self):
        assert MARKERS[1] == marker_for("int a;", 1)

    def test_out_of_range_index_starts_from_the_first(self):
        assert MARKERS[0] == marker_for("int a;", 99)
        assert MARKERS[0] == marker_for("int a;", -1)


class TestRoundTrip:
    def test_strip_replaces_every_comment(self):
        found = scan_comments(SOURCE)
        stripped = strip_comments(SOURCE, marker_for(SOURCE, 0))
        assert "// 行尾注释" not in stripped
        assert "// 独占一行" not in stripped
        # 占位符保持原注释种类：行注释用行占位符，块注释用块占位符。
        # 这不是形式问题——块占位符右边可以挂代码，clang-format 会把下一行的
        # `{` 挪上来，行注释占位符则不会（见 Comment.kind）
        assert stripped.count("// " + MARKERS[0]) == 2
        assert stripped.count("/*" + MARKERS[0]) == len(found) - 2

    def test_strip_then_restore_recovers_original_text(self):
        """没有任何位移时，还原必须逐列回到原文。"""
        found = scan_comments(SOURCE)
        prefix = marker_for(SOURCE, 0)
        stripped = strip_comments(SOURCE, prefix)
        moved: list = []
        restored = restore_comments(stripped, found, prefix, moved)
        assert SOURCE == restored
        assert [] == moved

    def test_restore_is_identity_without_move(self):
        """排版没有改动时，还原结果与输入完全一致。"""
        found = scan_comments(SOURCE)
        prefix = marker_for(SOURCE, 0)
        stripped = strip_comments(SOURCE, prefix)
        assert SOURCE == restore_comments(stripped, found, prefix)

    def test_moved_is_empty_when_column_survives(self):
        found = scan_comments(SOURCE)
        prefix = marker_for(SOURCE, 0)
        stripped = strip_comments(SOURCE, prefix)
        moved: list = []
        restore_comments(stripped, found, prefix, moved)
        assert [] == moved

    def test_moved_records_comment_pushed_off_its_column(self):
        """代码被排长、越过原列号时，这条退让必须被记下来。"""
        text = "int x = 1; // 行尾\n"
        found = scan_comments(text)
        prefix = marker_for(text, 0)
        stripped = strip_comments(text, prefix)
        shifted = stripped.replace("int x = 1;", "int x" + "x" * 40 + " = 1;")
        moved: list = []
        restored = restore_comments(shifted, found, prefix, moved)
        assert "// 行尾" in restored
        assert 1 == len(moved)
        index, original, actual = moved[0]
        assert 0 == index
        assert original == text.index("// 行尾")
        assert actual > original

    def test_own_line_comment_keeps_its_text(self):
        text = "void f() {\n        // 独占\n}\n"
        found = scan_comments(text)
        prefix = marker_for(text, 0)
        stripped = strip_comments(text, prefix)
        assert text == restore_comments(stripped, found, prefix)

    def test_comment_inside_string_survives_strip(self):
        text = 'const char *s = "/* 不是注释 */";\n'
        found = scan_comments(text)
        assert [] == found
        assert text == strip_comments(text, marker_for(text, 0))


class TestComment:
    def test_comment_is_an_immutable_value(self):
        item = Comment("// x", "  ", False, 4)
        assert Comment("// x", "  ", False, 4) == item
        with pytest.raises(FrozenInstanceError):
            item.text = "// y"  # type: ignore[misc]


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
