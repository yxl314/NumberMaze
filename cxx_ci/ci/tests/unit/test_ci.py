#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ci.py 纯函数的单元测试。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "test"))

import ci


def read(path):
    """读文本；测试自身也要跑在 Python 3.4 上，不能用 read_text。"""
    handle = open(str(path), encoding="utf-8")
    try:
        return handle.read()
    finally:
        handle.close()

from lib import protocol, stage
from ci_support import capture, make_result


class TestSplitNames:
    def test_empty(self):
        assert [] == ci.split_names('')

    def test_trims_spaces_and_drops_blanks(self):
        assert ['build', 'run_test'] == ci.split_names(' build , run_test ,')


class TestSelectSteps:
    def test_default_keeps_canonical_order(self):
        stages, unknown = ci.select_stages([], [])
        assert list(ci.STAGES) == stages
        assert [] == unknown

    def test_only_keeps_canonical_order(self):
        """--only 只跑点名的那些，按固定顺序排列。"""
        stages, unknown = ci.select_stages(["build", "clean"], [])
        assert ['clean', 'build'] == stages

    def test_skip(self):
        stages, unknown = ci.select_stages(
            [], ["memcheck", "run_test", "build_test", "style", "format"])
        assert ['boundary', 'clean', 'build'] == stages

    def test_style_and_format_are_not_ci_stages(self):
        """风格与排版属于另一个项目：本项目连它们的名字都不该出现。

        ci 只做基础流程，因此"CI 不会改文件"是结构上的保证，而不是靠
        "我们没传 --apply"这条调用约定。
        """
        assert 'style' not in ci.STAGES
        assert 'format' not in ci.STAGES
        # 阶段名不得出现在源码里：只做基础流程，不感知另一个项目
        source = read(ci.__file__)
        assert '"style"' not in source
        assert '"format"' not in source

    def test_base_flow_is_complete(self):
        assert ('boundary', 'clean', 'build', 'build_test', 'run_test',
                'memcheck') == ci.STAGES

    def test_default_stages_honours_config(self):
        class Stub(object):
            steps_default = ["clean", "build"]

        assert ['clean', 'build'] == ci.default_stages(Stub())

    def test_default_stages_falls_back_to_all(self):
        class Stub(object):
            steps_default = None

        assert list(ci.STAGES) == ci.default_stages(Stub())

    def test_unknown_stage_is_reported(self):
        stages, unknown = ci.select_stages(["nope"], [])
        assert ['nope'] == unknown


class FakeConfig(object):
    """emit 需要的配置字段。"""

    def __init__(self, root, log_dir, level="error", sources="project",
                 ignores=(), dedup=True):
        self.root = str(root)
        # SCOPE 已取代 WHITELIST/BLACKLIST，emit 走 scope_roots
        self.scope = {"project": [str(root)], "excluded": []}
        self.whitelist = [str(root)]
        self.blacklist = []
        self.fail_output = "summary"
        self.build_dir = str(root)
        self.test_build_dir = str(root)
        self.log_dir = str(log_dir)
        self.diagnostic_level = level
        self.diagnostic_sources = sources
        self.diagnostic_ignore = list(ignores)
        self.diagnostic_dedup = dedup


def project_warning(root, path=None, line=12, message="unused variable"):
    """构造一条项目自身的编译器告警，路径落在 root 内。"""
    if path is None:
        path = str(root) + "/src/a.cpp"
    return "{}:{}:5: warning: {}".format(path, line, message)


class TestEmit:
    def test_quiet_success_is_silent(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path)
        _, out, err = capture(stage.emit, config, make_result(code=0, out="日志"),
                              "quiet")
        assert '' == out
        assert '' == err

    def test_quiet_failure_shows_verdict_and_log_path(self, tmp_path):
        """失败先给结论：逐例失败与日志路径，不再整段倾倒。"""
        config = FakeConfig(tmp_path, tmp_path)
        _, out, err = capture(stage.emit, config,
                              make_result(code=1, out="普通输出", err="错误输出"),
                              "quiet")
        assert 'FAILED' in out
        assert '完整输出见' in out

    def test_fail_output_full_keeps_old_behaviour(self, tmp_path):
        """FAIL_OUTPUT=full 还原旧的全量倾倒。"""
        config = FakeConfig(tmp_path, tmp_path)
        config.fail_output = "full"
        _, out, err = capture(stage.emit, config,
                              make_result(code=1, out="普通输出", err="错误输出"),
                              "quiet")
        assert '普通输出' in out
        assert '错误输出' in err

    def test_fail_protocol_lines_are_listed(self, tmp_path):
        """#fail 协议行先于完整日志给出，这是"先给结论"。"""
        config = FakeConfig(tmp_path, tmp_path)
        result = make_result(code=1, out=protocol.fail("A.b", "断言不成立"))
        _, out, _ = capture(stage.emit, config, result, "normal")
        assert 'A.b' in out
        assert '1 例失败' in out

    def test_timeout_is_marked(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path)
        _, out, _ = capture(stage.emit, config,
                            make_result(code=1, elapsed=2.0, timed_out=True),
                            "normal")
        assert 'TIMEOUT' in out

    def test_business_log_with_error_word_is_not_a_diagnostic(self, tmp_path):
        """自带日志系统的被测程序不该被当成诊断来源。"""
        config = FakeConfig(tmp_path, tmp_path, level="warning")
        result = make_result(out="2026-01-01 |ERROR| 用例故意走错误分支")
        _, out, _ = capture(stage.emit, config, result, "normal")
        assert '|ERROR|' not in out

    def test_tool_command_line_is_not_a_diagnostic(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path, level="warning")
        result = make_result(out="$ valgrind --error-exitcode=1 true")
        _, out, _ = capture(stage.emit, config, result, "normal")
        assert '--error-exitcode' not in out

    def test_project_warning_is_shown(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path, level="warning")
        result = make_result(out=project_warning(tmp_path))
        _, out, _ = capture(stage.emit, config, result, "normal")
        assert 'unused variable' in out

    def test_external_warning_is_hidden_by_default(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path, level="warning")
        result = make_result(out=project_warning(tmp_path, "/opt/qt/q.h",
                                                            9, "deprecated"))
        _, out, _ = capture(stage.emit, config, result, "normal")
        assert 'deprecated' not in out
        assert '过滤' in out

    def test_external_warning_shown_when_sources_all(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path, level="warning",
                            sources="all")
        result = make_result(out=project_warning(tmp_path, "/opt/qt/q.h",
                                                            9, "deprecated"))
        _, out, _ = capture(stage.emit, config, result, "normal")
        assert 'deprecated' in out

    def test_level_off_is_completely_silent(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path, level="off")
        result = make_result(out=project_warning(tmp_path))
        _, out, _ = capture(stage.emit, config, result, "normal")
        assert 'unused variable' not in out
        assert '条诊断' not in out

    def test_ignore_regex_silences(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path, level="warning",
                            ignores=["unused variable"])
        result = make_result(out=project_warning(tmp_path))
        _, out, _ = capture(stage.emit, config, result, "normal")
        assert 'unused variable' not in out

    def test_duplicates_are_merged(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path, level="warning")
        result = make_result(
            out="\n".join([project_warning(tmp_path)] * 100))
        _, out, _ = capture(stage.emit, config, result, "normal")
        assert '[×100]' in out
        assert 1 == out.count('unused variable')

    def test_full_output_is_written_to_log(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path, level="off")
        result = make_result(name="build", out="原始输出\n第二行")
        capture(stage.emit, config, result, "normal")
        handle = open(str(tmp_path / "build.log"))
        try:
            written = handle.read()
        finally:
            handle.close()
        assert '原始输出' in written
        assert '第二行' in written


class TestReport:
    def test_quiet_success_is_silent(self):
        _, out, err = capture(ci.report, [make_result()], True)
        assert '' == out

    def test_quiet_failure_still_reports(self):
        _, out, err = capture(ci.report, [make_result(code=1)], True)
        assert '总计' in out

    def test_reports_per_stage_and_total(self):
        _, out, err = capture(ci.report,
                           [make_result(name="clean", elapsed=1.0),
                            make_result(name="build", elapsed=2.5)], False)
        assert 'build 2.5s' in out
        assert '总计 3.5s' in out

