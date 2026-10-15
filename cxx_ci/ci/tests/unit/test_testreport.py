#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试报告适配器的单元测试。

样本取自各框架的真实输出。重点是"什么不算逐例失败"：gtest 末尾的
"listed below" 会把用例再列一遍，catch2 的失败行本身没有用例名，这些地方
认错就会多报或漏报，而漏报正是这个适配器要挡住的一类错误。
"""

from lib import errors, testreport

GTEST = """[==========] Running 3 tests from 2 test suites.
[----------] Global test environment set-up.
[----------] 2 tests from MathTest
[ RUN      ] MathTest.Addition
[       OK ] MathTest.Addition (0 ms)
[ RUN      ] MathTest.Subtraction
/proj/tests/math_test.cpp:42: Failure
Expected equality of these values:
  a - b
    Which is: 5
  c
    Which is: 3
[  FAILED  ] MathTest.Subtraction (1 ms)
[----------] 2 tests from MathTest (1 ms total)

[----------] 1 test from StringTest
[ RUN      ] StringTest.Split
/proj/tests/string_test.cpp:18: Failure
Value of: parts.size()
  Actual: 2
Expected: 3
[  FAILED  ] StringTest.Split (0 ms)
[----------] 1 test from StringTest (0 ms total)

[----------] Global test environment tear-down
[==========] 3 tests from 2 test suites ran. (1 ms total)
[  PASSED  ] 1 test.
[  FAILED  ] 2 tests, listed below:
[  FAILED  ] MathTest.Subtraction
[  FAILED  ] StringTest.Split

 2 FAILED TESTS
"""

GTEST_TOTAL_LINE = "[==========] 3 tests from 2 test suites ran. (1 ms total)"

QTEST = """********* Start testing of TestMath *********
Config: Using QtTest library 5.15.2, Qt 5.15.2
PASS   : TestMath::initTestCase()
PASS   : TestMath::testAdd()
FAIL!  : TestMath::testSub() Compared values are not the same
   Actual   (a - b): 5
   Expected (c)    : 3
   Loc: [/proj/tests/math_test.cpp(42)]
FAIL!  : TestMath::testDiv(data) 除法: 除数不能为零
PASS   : TestMath::cleanupTestCase()
Totals: 4 passed, 2 failed, 1 skipped, 0 blacklisted, 12ms
********* Finished testing of TestMath *********
"""

QTEST_CN = """********* Start testing of TestIO *********
FAIL!  : TestIO::test名字(data) 打开文件失败: 路径 (空) 不存在
Totals: 0 passed, 1 failed, 0 skipped, 0 blacklisted, 3ms
********* Finished testing of TestIO *********
"""

# 多套件 runner：一个 runner 把多个测试可执行文件串起来跑，每跑完一个就
# 打印一段 Totals。只取首段会把 34 例报成 11 例，失败数也会漏计。
QTEST_MULTI = """********* Start testing of TestA *********
PASS   : TestA::initTestCase()
FAIL!  : TestA::case1() 断言失败
Totals: 10 passed, 1 failed, 0 skipped, 0 blacklisted, 5ms
********* Finished testing of TestA *********
********* Start testing of TestB *********
FAIL!  : TestB::case2() 断言失败
FAIL!  : TestB::case3() 超时
Totals: 13 passed, 2 failed, 0 skipped, 0 blacklisted, 6ms
********* Finished testing of TestB *********
********* Start testing of TestC *********
FAIL!  : TestC::case4() 断言失败
Totals: 7 passed, 1 failed, 0 skipped, 0 blacklisted, 4ms
********* Finished testing of TestC *********
"""

GTEST_MULTI = """[==========] 2 tests from 1 test suite ran. (1 ms total)
[  PASSED  ] 2 tests.
[==========] 5 tests from 3 test suites ran. (2 ms total)
[  PASSED  ] 5 tests.
"""

CATCH2_MULTI = """===============================================================================
test cases: 2 | 1 passed | 1 failed
assertions: 3 | 1 passed | 2 failed
===============================================================================
test cases: 4 | 4 passed | 0 failed
assertions: 6 | 6 passed | 0 failed
"""

CATCH2 = """~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
math_test is a Catch v2.13.10 host application.
Run with -? for options

-------------------------------------------------------------------------------
MathTest
  Subtraction
-------------------------------------------------------------------------------
/proj/tests/math_test.cpp:30
...............................................................................

/proj/tests/math_test.cpp:30: FAILED:
  CHECK( a - b == c )
with expansion:
  5 == 3

-------------------------------------------------------------------------------
MathTest
  Division
-------------------------------------------------------------------------------
/proj/tests/math_test.cpp:50
...............................................................................

/proj/tests/math_test.cpp:50: FAILED:
  REQUIRE( b != 0 )
中文说明: 除数不能为零

===============================================================================
test cases: 2 | 1 passed | 1 failed
assertions: 3 | 1 passed | 2 failed
"""

CUSTOM = """共 3 个用例
FAIL my_case: 断言失败
FAIL other_case: 超时
"""


def cases(failures):
    """把失败列表压成 (用例, 原因) 便于比较。"""
    return [(item.case, item.reason) for item in failures]


class TestAdapterTable:
    def test_adapters_are_exactly_the_contract(self):
        assert ("auto", "gtest", "qtest", "catch2", "regex",
                "exit-code") == testreport.ADAPTERS

    def test_failure_has_case_and_reason(self):
        item = testreport.Failure("A.b", "断言失败")
        assert "A.b" == item.case
        assert "断言失败" == item.reason


class TestGtest:
    def test_detect(self):
        assert "gtest" == testreport.detect(GTEST)

    def test_fingerprint_reports_evidence(self):
        name, evidence = testreport.fingerprint(GTEST)
        assert "gtest" == name
        assert "======" in evidence

    def test_extract_cases_and_reasons(self):
        failures, totals, code, _ = testreport.extract(GTEST, "auto")
        assert testreport.Totals(3, 2) == totals
        assert errors.OK == code  # 解析成功即 OK，用例失败看列表
        assert [
            ("MathTest.Subtraction",
             "/proj/tests/math_test.cpp:42: Expected equality of these values:"),
            ("StringTest.Split",
             "/proj/tests/string_test.cpp:18: Value of: parts.size()"),
        ] == cases(failures)

    def test_listed_below_does_not_duplicate(self):
        failures, _, _, _ = testreport.extract(GTEST, "gtest")
        assert 2 == len(failures)

    def test_parameterized_line_with_where_clause(self):
        text = "[  FAILED  ] FooTest.Bar/0, where GetParam() = 42\n"
        failures, total, code, _ = testreport.extract(text, "gtest")
        assert [("FooTest.Bar/0", "")] == cases(failures)
        assert errors.OK == code  # 解析成功即 OK，用例失败看列表
        assert total is None

    def test_clean_run_has_no_failures(self):
        text = "[==========] 1 test from 1 test suite ran. (0 ms total)\n"
        assert ([], testreport.Totals(1, 1), errors.OK, "") == testreport.extract(
            text, "gtest")

    def test_bracket_line_inside_assertion_block_is_not_a_boundary(self):
        """用例自己打印的行可能以 "[" 开头，不能当成标记行截断取原因。"""
        text = ("[ RUN      ] WidgetTest.Elements\n"
                "/proj/tests/widget_test.cpp:9: Failure\n"
                "[debug] 实际值: 3\n"
                "[  FAILED  ] WidgetTest.Elements (0 ms)\n")
        failures, _, _, _ = testreport.extract(text, "gtest")
        assert [("WidgetTest.Elements",
                 "/proj/tests/widget_test.cpp:9: [debug] 实际值: 3")] \
            == cases(failures)


class TestQtest:
    def test_detect(self):
        assert "qtest" == testreport.detect(QTEST)

    def test_extract_cases_and_reasons(self):
        failures, totals, code, _ = testreport.extract(QTEST, "auto")
        assert testreport.Totals(7, 1) == totals
        assert errors.OK == code  # 解析成功即 OK，用例失败看列表
        assert [
            ("TestMath::testSub()", "Compared values are not the same"),
            ("TestMath::testDiv(data)", "除法: 除数不能为零"),
        ] == cases(failures)

    def test_reason_with_colon_parentheses_and_chinese(self):
        failures, totals, code, _ = testreport.extract(QTEST_CN, "qtest")
        assert testreport.Totals(1, 1) == totals
        assert errors.OK == code  # 解析成功即 OK，用例失败看列表
        assert [("TestIO::test名字(data)", "打开文件失败: 路径 (空) 不存在")] \
            == cases(failures)

    def test_totals_sums_every_category(self):
        text = "Totals: 2 passed, 1 failed, 3 skipped, 4 blacklisted, 9ms\n"
        _, totals, _, _ = testreport.extract(text, "qtest")
        assert testreport.Totals(10, 1) == totals


class TestCatch2:
    def test_detect(self):
        assert "catch2" == testreport.detect(CATCH2)

    def test_extract_cases_from_header_and_expression(self):
        failures, totals, code, _ = testreport.extract(CATCH2, "auto")
        assert testreport.Totals(2, 1) == totals
        assert errors.OK == code  # 解析成功即 OK，用例失败看列表
        assert [
            ("MathTest / Subtraction", "CHECK( a - b == c )"),
            ("MathTest / Division", "REQUIRE( b != 0 )"),
        ] == cases(failures)

    def test_all_passed_summary_gives_total(self):
        text = "===============================================================================\n" \
               "All tests passed (3 assertions in 2 test cases)\n"
        failures, totals, code, _ = testreport.extract(text, "catch2")
        assert [] == failures
        assert testreport.Totals(2, 1) == totals
        assert errors.OK == code

    def test_failure_without_header_falls_back_to_location(self):
        text = "/proj/tests/a.cpp:7: FAILED:\n  CHECK( 1 == 2 )\n"
        failures, _, _, _ = testreport.extract(text, "catch2")
        assert [("/proj/tests/a.cpp", "CHECK( 1 == 2 )")] == cases(failures)


class TestAuto:
    def test_no_fingerprint_falls_back_to_exit_code(self):
        assert "exit-code" == testreport.detect("只是一些日志\n没有框架指纹")
        assert ([], None, errors.OK, "") == testreport.extract("随便一行\n", "auto")

    def test_empty_input(self):
        assert (None, "") == testreport.fingerprint("")
        assert "exit-code" == testreport.detect("")

    def test_first_fingerprint_wins(self):
        """混合输出按表序取第一个命中的，而不是"最像"的那个。"""
        mixed = "FAIL!  : A::b() 失败\n" + CATCH2
        assert "qtest" == testreport.detect(mixed)
        assert "gtest" == testreport.detect(GTEST + QTEST)

    def test_auto_extracts_from_mixed_output(self):
        failures, _, code, _ = testreport.extract("FAIL!  : A::b() 失败\n" + CATCH2,
                                              "auto")
        assert [("A::b()", "失败")] == cases(failures)
        assert errors.OK == code  # 解析成功即 OK，用例失败看列表


class TestRegex:
    def test_named_groups_and_total(self):
        failures, totals, code, _ = testreport.extract(
            CUSTOM, "regex",
            r"^FAIL (?P<case>\S+?):\s*(?P<reason>.*)$",
            r"共 (?P<total>\d+) 个用例")
        assert testreport.Totals(3, 0) == totals
        assert errors.OK == code  # 解析成功即 OK，用例失败看列表
        assert [("my_case", "断言失败"), ("other_case", "超时")] \
            == cases(failures)

    def test_positional_groups(self):
        failures, total, code, _ = testreport.extract(
            "用例 a.b 失败\n", "regex", r"用例 (\S+) (\S+)")
        assert [("a.b", "失败")] == cases(failures)
        assert total is None
        assert errors.OK == code  # 解析成功即 OK，用例失败看列表

    def test_single_group_is_the_case(self):
        failures, _, _, _ = testreport.extract("[x] something\n", "regex",
                                           r"^\[(\w+)\]")
        assert [("x", "")] == cases(failures)

    def test_without_total_pattern_total_is_none(self):
        failures, total, code, _ = testreport.extract(
            "FAIL a: 原因\n", "regex", r"^FAIL (?P<case>\w+): (?P<reason>.*)$")
        assert 1 == len(failures)
        assert total is None
        assert errors.OK == code  # 解析成功即 OK，用例失败看列表

    def test_no_match_is_not_an_error(self):
        assert ([], None, errors.OK, "") == testreport.extract(
            "什么都没命中\n", "regex", r"^FAIL (\w+)")

    def test_missing_fail_pattern(self):
        failures, total, code, message = testreport.extract(CUSTOM, "regex")
        assert [] == failures
        assert total is None
        assert errors.TEST_REPORT_BAD_PATTERN == code
        assert "fail_pattern" in message

    def test_invalid_fail_pattern(self):
        failures, total, code, message = testreport.extract(
            CUSTOM, "regex", "(")
        assert ([], None, errors.TEST_REPORT_BAD_PATTERN) == (failures, total, code)
        assert "失败正则无效" in message

    def test_invalid_total_pattern(self):
        failures, total, code, message = testreport.extract(
            CUSTOM, "regex", r"^FAIL (\w+)", "[")
        assert ([], None, errors.TEST_REPORT_BAD_PATTERN) == (failures, total, code)
        assert "总数正则无效" in message


class TestExitCode:
    def test_ignores_per_case_result(self):
        """exit-code 是旧语义的回归口：有失败行也照样返回空列表。"""
        assert ([], None, errors.OK, "") == testreport.extract(GTEST, "exit-code")

    def test_unknown_adapter_is_a_config_error(self):
        failures, total, code, message = testreport.extract(GTEST, "junit")
        assert [] == failures
        assert total is None
        assert errors.TEST_REPORT_BAD_ADAPTER == code
        assert "junit" in message


class TestMultiSuite:
    """多套件 runner 的汇总必须逐段累加，而不是只取首段。

    只取首段会把用例总数报成单个可执行文件的规模，展示的规模差一个数量级，
    按此判断覆盖度会被彻底误导；失败若发生在非首段套件则会被漏计。
    """

    def test_qtest_totals_are_summed(self):
        failures, totals, code, _ = testreport.extract(QTEST_MULTI, "qtest")
        assert testreport.Totals(34, 3) == totals
        assert errors.OK == code
        # 失败数等于三段之和，而不是首段那一条
        assert 4 == len(failures)

    def test_qtest_summary_text_reports_suites(self):
        _, totals, _, _ = testreport.extract(QTEST_MULTI, "qtest")
        assert "共 34 例（3 套件），失败 4 例" == testreport.describe(totals, 4)

    def test_single_suite_is_unchanged(self):
        """单套件输出与逐条求和一致，不因累加而改变。"""
        _, totals, _, _ = testreport.extract(QTEST, "auto")
        assert testreport.Totals(7, 1) == totals

    def test_gtest_totals_are_summed(self):
        _, totals, _, _ = testreport.extract(GTEST_MULTI, "gtest")
        assert testreport.Totals(7, 4) == totals

    def test_catch2_totals_are_summed(self):
        _, totals, _, _ = testreport.extract(CATCH2_MULTI, "catch2")
        assert testreport.Totals(6, 2) == totals

    def test_describe_without_suites_omits_that_part(self):
        """没有套件概念的适配器不报一个恒为 1 的假数字。"""
        assert "共 3 例，失败 1 例" == testreport.describe(
            testreport.Totals(3, 0), 1)

    def test_exit_code_adapter_is_unaffected(self):
        assert ([], None, errors.OK, "") == testreport.extract(
            QTEST_MULTI, "exit-code")


class TestMissingTotalLine:
    def test_gtest_total_none_without_summary(self):
        text = GTEST.replace(GTEST_TOTAL_LINE + "\n", "")
        failures, total, code, _ = testreport.extract(text, "gtest")
        assert 2 == len(failures)
        assert total is None
        assert errors.OK == code  # 解析成功即 OK，用例失败看列表

    def test_qtest_total_none_without_summary(self):
        text = QTEST.replace("Totals: 4 passed, 2 failed, 1 skipped, "
                             "0 blacklisted, 12ms\n", "")
        failures, total, code, _ = testreport.extract(text, "qtest")
        assert 2 == len(failures)
        assert total is None
        assert errors.OK == code  # 解析成功即 OK，用例失败看列表

    def test_catch2_total_none_without_summary(self):
        text = CATCH2.split("=========")[0]
        failures, total, code, _ = testreport.extract(text, "catch2")
        assert 2 == len(failures)
        assert total is None
        assert errors.OK == code  # 解析成功即 OK，用例失败看列表
