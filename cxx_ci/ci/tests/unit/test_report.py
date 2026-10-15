#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机器可读报告的单元测试。

三条硬约束：
  - 报告只搬运结论：诊断集合必须与终端展示的一一对应，不能是第二套判定；
  - 三态不塌陷：skipped 不能变成 passed；
  - xfail 登记项既不是失败也不是通过。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib import errors, protocol, report, stage
from ci_support import Stub, capture, make_result


class FakeConfig(Stub):
    """build 需要的配置字段；路径都指向不存在的固定目录，取值稳定。"""

    def __init__(self, **overrides):
        fields = dict(
            root="/proj",
            scope={"project": ["/proj/src"], "excluded": []},
            whitelist=["/proj/src"],
            blacklist=[],
            build_dir="/proj/build",
            test_build_dir="/proj/build",
            log_dir="/proj/build/ci-logs",
            diagnostic_level="warning",
            diagnostic_sources="project",
            diagnostic_ignore=[],
            diagnostic_dedup=True,
            fail_output="summary",
            xfail_file="/proj/ci.xfail",
            build_tool="cmake",
            memcheck_tool="asan",
            memcheck_scope="project",
            source_encoding="utf-8",
        )
        fields.update(overrides)
        Stub.__init__(self, **fields)


def stage_of(entries):
    """用协议行拼一个阶段结果。"""
    text = "\n".join(entries)
    return make_result(name="build", code=0, elapsed=1.5, out=text)


GOLDEN_STAGE = stage_of([
    protocol.diag("warning", "/proj/src/a.cpp", 3, 1,
                  "未使用变量 [-Wunused-variable]"),
    protocol.summary("编译完成"),
])


def golden_doc():
    document = report.build(FakeConfig(), [GOLDEN_STAGE], [])
    # 随机器变化的都要钉成固定值，否则黄金文件会在装了 clang 的机器上红：
    # Python 版本、clang 的实际路径与版本。黄金文件的意义就是"输出形状不随
    # 环境漂"，所以这里把整段环境替换掉
    document["environment"] = {"python": "PY", "clang_format": "",
                               "clang_version": ""}
    return document


GOLDEN_JSON = """\
{
  "baseline": {
    "format_fingerprint": "",
    "xfail_count": 0,
    "xfail_file": "/proj/ci.xfail"
  },
  "config_digest": {
    "BUILD_TOOL": "cmake",
    "DIAGNOSTIC_LEVEL": "warning",
    "DIAGNOSTIC_SOURCES": "project",
    "MEMCHECK_SCOPE": "project",
    "MEMCHECK_TOOL": "asan",
    "SCOPE": {
      "excluded": [],
      "project": [
        "/proj/src"
      ]
    },
    "SOURCE_ENCODING": "utf-8"
  },
  "environment": {
    "clang_format": "",
    "clang_version": "",
    "python": "PY"
  },
  "report_version": 1,
  "stages": [
    {
      "diagnostics": [
        {
          "check": "-Wunused-variable",
          "column": 1,
          "count": 1,
          "file": "/proj/src/a.cpp",
          "level": "warning",
          "line": 3,
          "message": "未使用变量 [-Wunused-variable]",
          "owner": "project",
          "xfail": false
        }
      ],
      "failures": [],
      "log": "/proj/build/ci-logs/build.log",
      "name": "build",
      "seconds": 1.5,
      "skipped_reason": "",
      "status": "passed",
      "summary": "编译完成 | 1 条诊断",
      "timed_out": false
    }
  ],
  "tool": {
    "ci": "1.3.0",
    "contract": 3,
    "standardize": ""
  }
}
"""

GOLDEN_JUNIT = """\
<?xml version="1.0" encoding="UTF-8"?>
<testsuites name="cxx_ci" tests="1" failures="0" skipped="0" time="1.500">
  <testsuite name="build" tests="1" failures="0" skipped="0" time="1.500">
    <testcase name="build" classname="cxx_ci"/>
    <system-out>编译完成 | 1 条诊断</system-out>
  </testsuite>
</testsuites>
"""

GOLDEN_SARIF = """\
{
  "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
  "runs": [
    {
      "properties": {
        "contract": 3,
        "report_version": 1
      },
      "results": [
        {
          "level": "warning",
          "locations": [
            {
              "physicalLocation": {
                "artifactLocation": {
                  "uri": "/proj/src/a.cpp"
                },
                "region": {
                  "startColumn": 1,
                  "startLine": 3
                }
              }
            }
          ],
          "message": {
            "text": "未使用变量 [-Wunused-variable]"
          },
          "partialFingerprints": {
            "cxx_ci/v1": "5d64ddfb96212c5dcba42da31f0647d2866da413df98267edb3aa6de0e779df6"
          },
          "properties": {
            "count": 1,
            "owner": "project"
          },
          "ruleId": "-Wunused-variable"
        }
      ],
      "tool": {
        "driver": {
          "informationUri": "",
          "name": "cxx_ci",
          "version": "1.3.0"
        }
      }
    }
  ],
  "version": "2.1.0"
}
"""


class TestGolden:
    """一个阶段结果 → 三种格式的黄金文件断言。"""

    def test_json(self):
        assert GOLDEN_JSON == report.to_json(golden_doc())

    def test_junit(self):
        assert GOLDEN_JUNIT == report.to_junit(golden_doc())

    def test_sarif(self):
        assert GOLDEN_SARIF == report.to_sarif(golden_doc())


class TestStatus:
    def test_ok_is_passed(self):
        document = report.build(FakeConfig(), [make_result(code=0)], [])
        assert "passed" == document["stages"][0]["status"]

    def test_failure_is_failed(self):
        document = report.build(FakeConfig(), [make_result(code=1)], [])
        assert "failed" == document["stages"][0]["status"]

    def test_skipped_does_not_collapse_into_passed(self):
        """跳过既不是通过也不是失败：报告里仍是独立一档，并带原因。"""
        result = make_result(name="memcheck",
                             code=errors.STAGE_UNAVAILABLE,
                             err="机器上没有 valgrind")
        document = report.build(FakeConfig(), [result], ["memcheck"])
        stage_entry = document["stages"][0]
        assert "skipped" == stage_entry["status"]
        assert "机器上没有 valgrind" == stage_entry["skipped_reason"]
        xml = report.to_junit(document)
        assert "<skipped message=\"机器上没有 valgrind\"/>" in xml

    def test_timeout_is_failed(self):
        document = report.build(
            FakeConfig(), [make_result(code=errors.STAGE_TIMEOUT,
                                       timed_out=True)], [])
        entry = document["stages"][0]
        assert "failed" == entry["status"]
        assert entry["timed_out"] is True


class TestXfail:
    def checklist(self, tmp_path, text):
        path = tmp_path / "ci.xfail"
        with open(str(path), "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_xfail_is_neither_failure_nor_pass(self, tmp_path):
        """已登记项的"已知"标记来自阶段发的 #xfail，报告只搬运。

        真实管线里阶段把已登记项发成 #sum（给人看）+ #xfail（给机器读），
        #fail 只留真正要修的。以前这里用一条 #fail 制造覆盖——那个输入终端会
        打成失败、报告却标成 skipped，正好固化了"报告与终端分叉"。
        """
        path = self.checklist(tmp_path, "[test]\nA.b\n")
        config = FakeConfig(root=str(tmp_path), xfail_file=str(path))
        result = make_result(
            name="run_test", code=1,
            out=protocol.summary("已登记 (xfail): A.b 断言不成立") + "\n"
                + protocol.known("A.b", "断言不成立") + "\n"
                + protocol.fail("C.d", "真失败"))
        document = report.build(config, [result], [])
        failures = document["stages"][0]["failures"]
        assert [("A.b", True), ("C.d", False)] == [
            (item["case"], item["xfail"]) for item in failures]
        xml = report.to_junit(document)
        # 已登记项走 <skipped>，未登记项才是 <failure>
        assert "已登记 (xfail)" in xml
        assert xml.count("<failure") == 1
        assert xml.count("<skipped") == 1

    def test_report_does_not_reclassify_a_fail_by_itself(self, tmp_path):
        """清单里有 A.b，但阶段只发了 #fail——报告**不得**自己把它改判成已知。

        报告一旦有权重判，终端与报告就会各说各的。
        """
        path = self.checklist(tmp_path, "[test]\nA.b\n")
        config = FakeConfig(root=str(tmp_path), xfail_file=str(path))
        result = make_result(name="run_test", code=1,
                             out=protocol.fail("A.b", "断言不成立"))
        document = report.build(config, [result], [])
        assert [("A.b", False)] == [
            (item["case"], item["xfail"])
            for item in document["stages"][0]["failures"]]

    def test_xfail_count_recorded_in_baseline(self, tmp_path):
        path = self.checklist(tmp_path, "[test]\nA.b\n[memcheck]\nFoo::\n")
        config = FakeConfig(root=str(tmp_path), xfail_file=str(path))
        document = report.build(config, [make_result(code=0)], [])
        assert 2 == document["baseline"]["xfail_count"]


class TestDiagnostics:
    def test_diagnostics_match_terminal_output_one_to_one(self):
        """报告的诊断集合 = 终端展示的那些行，不是第二套判定。"""
        config = FakeConfig()
        result = stage_of([
            protocol.diag("warning", "/proj/src/a.cpp", 3, 1, "第一条"),
            protocol.diag("error", "/proj/src/b.cpp", 8, 2, "第二条"),
        ])
        _, out, _ = capture(stage.emit, config, result, "normal")
        document = report.build(config, [result], [])
        entries = document["stages"][0]["diagnostics"]
        assert 2 == len(entries)
        # 终端上每条诊断恰好一行：用消息逐条核对不重不漏
        for item in entries:
            assert 1 == out.count(item["message"])

    def test_duplicate_diagnostics_carry_the_count(self):
        config = FakeConfig()
        line = protocol.diag("warning", "/proj/src/a.cpp", 3, 1, "重复")
        result = stage_of([line, line])
        document = report.build(config, [result], [])
        entries = document["stages"][0]["diagnostics"]
        assert 1 == len(entries)
        assert 2 == entries[0]["count"]
        _, out, _ = capture(stage.emit, config, result, "normal")
        assert "[×2]" in out

    def test_check_name_lifted_from_message(self):
        config = FakeConfig()
        result = stage_of([protocol.diag("warning", "/proj/src/a.cpp", 1, 1,
                                         "msg [modernize-use-nullptr]")])
        document = report.build(config, [result], [])
        entry = document["stages"][0]["diagnostics"][0]
        assert "modernize-use-nullptr" == entry["check"]
        assert "project" == entry["owner"]
        sarif = report.to_sarif(document)
        assert "\"ruleId\": \"modernize-use-nullptr\"" in sarif


class TestWrite:
    def test_single_format_writes_exactly_to_target(self, tmp_path):
        target = str(tmp_path / "report.json")
        written, problems = report.write(golden_doc(), target, ["json"])
        assert [target] == written
        assert [] == problems
        assert target.endswith(".json")
        with open(target, encoding="utf-8") as handle:
            assert GOLDEN_JSON == handle.read()

    def test_multiple_formats_get_suffixes(self, tmp_path):
        target = str(tmp_path / "report")
        written, problems = report.write(golden_doc(), target,
                                         ["json", "junit", "sarif"])
        assert [] == problems
        assert (str(tmp_path / "report.json"),
                str(tmp_path / "report.xml"),
                str(tmp_path / "report.sarif")) == tuple(written)

    def test_write_failure_is_a_problem_not_a_crash(self, tmp_path):
        """目录建不出来时只回问题，不改判定——报告是旁路产物。"""
        blocker = tmp_path / "blocker"
        with open(str(blocker), "w", encoding="utf-8") as handle:
            handle.write("x")
        target = str(blocker / "report.json")  # 父路径是个普通文件
        written, problems = report.write(golden_doc(), target, ["json"])
        assert [] == written
        assert problems

    def test_stdout_target(self):
        result, _out, _err = capture(report.write, golden_doc(), "-", ["json"])
        written, problems = result
        assert ["<stdout>"] == written
        assert [] == problems

    def test_unknown_format_raises(self):
        raised = False
        try:
            report.render(golden_doc(), "yaml")
        except ValueError:
            raised = True
        assert raised
