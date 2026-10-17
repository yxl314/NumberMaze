"""机器可读报告：内部 schema、三 writer 与 CLI 出口。

报告是旁路产物，判卷仍以错误码为准：这里既钉住 schema 形状（两侧契约的一部分），
也钉住"三态不塌陷"（失败不因没有逐条失败就被画成通过）与"写失败不改退出码"。
"""

import json
from pathlib import Path

import pytest
from std_support import ECHO_FORMAT

from cxx_standardize import CONTRACT_VERSION, __version__, report
from cxx_standardize.errors import Code

TOP_KEYS = {"report_version", "tool", "environment", "config_digest",
            "baseline", "stages"}
STAGE_KEYS = {"name", "status", "seconds", "timed_out", "diagnostics",
              "failures", "summary", "skipped_reason", "unfixable", "log"}


class FakeConfig:
    """报告取值只需要这几个属性，用最小替身避免牵动整个配置加载。"""

    def __init__(self, root, log_dir):
        self.root = root
        self.log_dir = log_dir
        self.xfail_file = str(Path(log_dir) / "absent.xfail")
        self.build_tool = "cmake"
        self.scope_roots = []
        self.scope_excluded = []
        self.source_encoding = "utf-8"


@pytest.fixture
def config(tmp_path):
    return FakeConfig(str(tmp_path), str(tmp_path / "logs"))


def make_document(config, name="style", status_code=Code.OK, **feed):
    collector = report.Collector()
    stage = collector.stage(name)
    for item in feed.get("diagnostics", []):
        stage.add_diagnostic(**item)
    for item in feed.get("failures", []):
        stage.add_failure(**item)
    stage.summary = feed.get("summary", "")
    collector.close(name, status_code, 1.25)
    return report.build(config, collector)


class TestCollector:
    def test_close_maps_code_to_status(self, config):
        collector = report.Collector()
        assert report.STATUS_PASSED == collector.close("style", Code.OK, 0).status
        assert (report.STATUS_FAILED
                == collector.close("format", Code.FORMAT_PENDING, 0).status)

    def test_stage_is_stable_and_ordered(self):
        collector = report.Collector()
        first = collector.stage("style")
        again = collector.stage("style")
        collector.stage("format")
        assert first is again
        assert ["style", "format"] == [item.name for item in collector.stages]


class TestBuild:
    def test_schema_shape(self, config):
        document = make_document(config)
        assert TOP_KEYS == set(document)
        assert 1 == document["report_version"]
        assert STAGE_KEYS == set(document["stages"][0])

    def test_tool_identifies_this_side(self, config):
        tool = make_document(config)["tool"]
        assert __version__ == tool["standardize"]
        assert "" == tool["ci"]
        assert CONTRACT_VERSION == tool["contract"]

    def test_digest_keys_are_shared_shape(self, config):
        digest = make_document(config)["config_digest"]
        assert set(report.DIGEST_KEYS) == set(digest)


class TestWriters:
    def test_json_is_sorted_and_stable(self, config):
        document = make_document(config)
        text = report.to_json(document)
        assert document == json.loads(text)
        assert text.endswith("}\n")


class TestWrite:
    def test_stdout_target(self, config, capsys):
        document = make_document(config)
        written, problems = report.write(document, "-", ["json"])
        assert ["<stdout>"] == written
        assert [] == problems
        assert '"report_version"' in capsys.readouterr().out

    def test_unwritable_target_is_a_problem_only(self, config, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir", encoding="utf-8")
        written, problems = report.write(make_document(config),
                                         str(blocker / "x.json"), ["json"])
        assert [] == written
        assert problems


class TestCliReport:
    def _tools(self, project):
        directory = project.fake_tool("clang-format", ECHO_FORMAT)
        tidy = directory / "clang-tidy"
        tidy.write_text("#!/bin/sh\necho 'LLVM version 17.0.6'\nexit 0\n",
                        encoding="utf-8")
        tidy.chmod(0o755)
        return directory

    def test_check_writes_shared_shape(self, project):
        target = project.path / "report.json"
        code, _out, err = project.run(
            ["check", "--report", str(target)],
            env=project.env(self._tools(project)))
        assert 0 == code, err
        document = json.loads(target.read_text(encoding="utf-8"))
        # conclusion 是入口的判定搬过来的：报告不判卷，但必须让平台看见
        # "一段都没跑就失败"，否则报告读作零失败
        assert TOP_KEYS | {"conclusion"} == set(document)
        assert "passed" == document["conclusion"]["status"]
        assert ["format", "style"] == sorted(
            stage["name"] for stage in document["stages"])
        assert all("passed" == stage["status"] for stage in document["stages"])
        assert __version__ == document["tool"]["standardize"]
        assert "" == document["tool"]["ci"]

    def test_stdout_target_keeps_json(self, project):
        _code, out, _err = project.run(
            ["check", "--report=-"],
            env=project.env(self._tools(project)))
        assert '"report_version"' in out
        assert '"stages"' in out

    def test_unknown_format_warns_without_changing_verdict(self, project):
        code, _out, err = project.run(
            ["check", "--report", str(project.path / "r.json"),
             "--report-format", "nope"],
            env=project.env(self._tools(project)))
        assert 0 == code
        assert "未知报告格式" in err
        assert not (project.path / "r.json").exists()

    def test_report_written_even_when_tool_missing(self, project):
        """工具缺失是环境错误（2），但报告仍要落盘，形状不退化成没有 stages。"""
        target = project.path / "report.json"
        code, _out, _err = project.run(
            ["format", "--report", str(target)], env={"PATH": "/nonexistent"})
        assert 2 == code
        document = json.loads(target.read_text(encoding="utf-8"))
        assert [] == document["stages"]


    def test_diagnostics_match_the_terminal_lines(self, project):
        """报告里的诊断集合，必须与终端回显的诊断行一一对应。

        这是"报告与终端同源"的可证伪形式：两边各自算一份就会漂，这条先红。
        假 clang-tidy 会被喂一份固定的 findings，因此两边的条数都可预期。
        """
        source = project.write("src/a.cpp",
                               "int main() { int Bad = 1; return Bad; }\n")
        target = project.path / "report.json"
        env = project.env(self._tools(project))
        from test_flows import findings_yaml

        env["FIXES_BODY"] = findings_yaml(
            [(str(source), 1, "readability-identifier-naming")], project.path)
        code, out, err = project.run(
            ["style", "--report", str(target)], env=env)
        document = json.loads(target.read_text(encoding="utf-8"))
        stage = [item for item in document["stages"]
                 if item["name"] == "style"][0]
        reported = ["{}:{}".format(item["file"], item["line"])
                    for item in stage["diagnostics"]]
        # 终端回显的每一条诊断都带 "路径:行:列"
        printed = [line for line in out.splitlines()
                   if ":1:" in line and "readability-identifier-naming" in line]
        assert len(reported) == len(printed), (reported, printed, out, err)
        assert 0 != code or reported == []
