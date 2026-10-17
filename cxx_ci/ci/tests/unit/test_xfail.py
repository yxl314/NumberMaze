import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import errors, xfail
from ci_support import Stub


SAMPLE = """\
# 已登记的历史遗留
[test]
TestMath::testSub()
[memcheck]
NormalButton::
sig:1a2b3c4d
"""


def make(text, root="/proj"):
    """解析清单并断言成功，返回 Checklist。"""
    entries, code, message = xfail.parse(text)
    assert errors.OK == code, message
    return xfail.Checklist(xfail.Registry("", root, entries))


class TestParse:
    def test_two_kinds_are_recognised(self):
        entries, code, _ = xfail.parse(SAMPLE)
        assert errors.OK == code
        assert ["test", "memcheck", "memcheck"] == [item.kind for item in entries]

    def test_inline_comment_is_stripped(self):
        entries, code, _ = xfail.parse(
            "[test]\nTestMath::testSub()   # 待重构\n")
        assert errors.OK == code
        assert "TestMath::testSub()" == entries[0].value

    def test_sectionless_entries_are_inferred(self):
        """不写分段头时按形状推断：`::` 结尾或 sig: 是 memcheck，其余是 test。"""
        entries, code, _ = xfail.parse(
            "TestMath::testSub()\nNormalButton::\nsig:1a2b3c4d\n")
        assert errors.OK == code
        assert ["test", "memcheck", "memcheck"] == [item.kind
                                                    for item in entries]

    def test_unknown_section_is_reported(self):
        _, code, message = xfail.parse("[styel]\nsrc/a.cpp:x-y\n")
        assert errors.XFAIL_BAD_FILE == code
        assert "分段头" in message

    def test_unknown_kind_in_section_header_is_reported(self):
        _, code, message = xfail.parse("[memcheck]\nNormalButton\n")
        assert errors.XFAIL_BAD_FILE == code
        assert "符号前缀" in message

    def test_memcheck_entry_needs_prefix_or_hash(self):
        _, code, message = xfail.parse("[memcheck]\nNormalButton\n")
        assert errors.XFAIL_BAD_FILE == code
        assert "memcheck" in message

    def test_error_message_carries_the_line_number(self):
        _, code, message = xfail.parse(
            "# 注释\n[memcheck]\nNormalButton\n")
        assert errors.XFAIL_BAD_FILE == code
        assert "第 3 行" in message

    def test_missing_file_is_not_an_error(self):
        """没有清单时门禁按原有语义判定，不是配置错误。"""
        config = Stub(xfail_file="/nonexistent/ci.xfail", root="/proj")
        book, code, _ = xfail.load_checklist(config)
        assert errors.OK == code
        assert [] == book.registry.entries


class TestTestMatch:
    def test_exact_name_matches(self):
        book = make(SAMPLE)
        assert book.test("TestMath::testSub()") is not None

    def test_name_is_compared_verbatim(self):
        book = make(SAMPLE)
        assert book.test("TestMath::testSub") is None
        assert book.test("TestMath::testOther()") is None


class TestSymbolMatch:
    def test_symbol_prefix_matches(self):
        book = make(SAMPLE)
        assert book.symbol("NormalButton::NormalButton_init()") is not None

    def test_other_symbol_does_not_match(self):
        book = make(SAMPLE)
        assert book.symbol("Menu::make_menus()") is None

    def test_signature_hash_matches(self):
        book = make(SAMPLE)
        assert book.symbol("", "1a2b3c4d") is not None
        assert book.symbol("", "ffffffff") is None


class TestStale:
    def test_used_entry_is_not_stale(self):
        book = make(SAMPLE)
        book.test("TestMath::testSub()")
        stale = book.stale(("test",))
        assert [] == stale

    def test_unused_entry_is_stale(self):
        book = make(SAMPLE)
        stale = book.stale(("test",))
        assert ["TestMath::testSub()"] == [item.value for item in stale]

class TestVerdict:
    def test_no_stale_is_ok(self):
        book = make("[test]\nA.b()\n")
        book.test("A.b()")
        code, notes = xfail.verdict(book, ("test",), False)
        assert errors.OK == code
        assert [] == notes

    def test_stale_is_reported_but_passes_by_default(self):
        book = make("[test]\nA.b()\n")
        code, notes = xfail.verdict(book, ("test",), False)
        assert errors.OK == code
        assert "可移除" in notes[0]

    def test_strict_turns_stale_into_failure(self):
        book = make("[test]\nA.b()\n")
        code, _ = xfail.verdict(book, ("test",), True)
        assert errors.XFAIL_STALE == code

    def test_kinds_limit_what_can_be_judged(self):
        book = make(SAMPLE)
        code, notes = xfail.verdict(book, ("test",), True)
        assert errors.XFAIL_STALE == code
        # 只判 test 时 memcheck 条目不在范围内，不该被当成"已修复"
        assert 1 == len(notes)
        assert "testSub" in notes[0]


