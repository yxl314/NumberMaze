#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段执行与展示的单元测试。

三条输出通道的归属必须稳定，否则两个入口的输出行为会分叉：协议行是判定
来源，阶段 stdout/stderr 完整落盘，过程噪声默认静默、--verbose 才透传。
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "test"))

from lib import errors, protocol, stage
from ci_support import Stub, capture, make_result


def write(path, text):
    with open(str(path), "w", encoding="utf-8") as handle:
        handle.write(text)
    return str(path)


def probe(lines):
    """生成一段按 UTF-8 写出给定行的探针脚本正文。

    直接 print 中文在老机器上会因默认编码是 ASCII 而抛 UnicodeEncodeError，
    阶段脚本本身会调 ensure_utf8_streams，探针得自己保证编码。
    """
    body = "import sys\n"
    for line in lines:
        body += "sys.stdout.buffer.write({!r}.encode('utf-8'))\n".format(
            line + "\n")
    return body


def log_of(cfg, name):
    handle = open(stage.log_path(cfg, name), encoding="utf-8")
    try:
        return handle.read()
    finally:
        handle.close()


class TestLog:
    def test_no_log_dir_means_no_path(self):
        assert "" == stage.log_path(Stub(log_dir=""), "build")

    def test_truncate_then_append(self, tmp_path):
        config = Stub(log_dir=str(tmp_path))
        stage.truncate_log(config, "build")
        stage.append_log(config, "build", "第一段\n")
        stage.append_log(config, "build", "第二段\n")
        assert "第一段\n第二段\n" == log_of(config, "build")

    def test_truncate_clears_previous_run(self, tmp_path):
        config = Stub(log_dir=str(tmp_path))
        stage.append_log(config, "build", "上一轮\n")
        stage.truncate_log(config, "build")
        assert "" == log_of(config, "build")

    def test_write_log_appends_stdout_and_stderr(self, tmp_path):
        config = Stub(log_dir=str(tmp_path))
        stage.truncate_log(config, "build")
        stage.append_log(config, "build", "噪声\n")
        stage.write_log(config, make_result(name="build", out="普通输出",
                                           err="错误输出"))
        text = log_of(config, "build")
        assert "噪声" in text
        assert "普通输出" in text
        assert "错误输出" in text


class TestNoise:
    def test_silent_by_default_but_logged(self, tmp_path, monkeypatch):
        monkeypatch.delenv(stage.VERBOSE_ENV, raising=False)
        config = Stub(log_dir=str(tmp_path))
        _, out, _ = capture(stage.noise, config, "build", "make 噪声")
        assert "" == out
        assert "make 噪声" in log_of(config, "build")

    def test_verbose_passes_through(self, tmp_path, monkeypatch):
        monkeypatch.setenv(stage.VERBOSE_ENV, "1")
        config = Stub(log_dir=str(tmp_path))
        _, out, _ = capture(stage.noise, config, "build", "make 噪声")
        assert "make 噪声" in out
        assert "make 噪声" in log_of(config, "build")

    def test_empty_text_is_a_noop(self, tmp_path, monkeypatch):
        monkeypatch.delenv(stage.VERBOSE_ENV, raising=False)
        config = Stub(log_dir=str(tmp_path))
        stage.noise(config, "build", "")
        assert not os.path.exists(stage.log_path(config, "build"))

    def test_set_verbose_never_turns_it_off(self, monkeypatch):
        """入口已经把开关设进环境时，阶段脚本不能因为自己的缺省而关掉它。"""
        monkeypatch.setenv(stage.VERBOSE_ENV, "1")
        stage.set_verbose(False)
        assert stage.verbose()


class TestRunQuiet:
    def test_diagnostics_are_protocol_lines(self, tmp_path, monkeypatch):
        monkeypatch.delenv(stage.VERBOSE_ENV, raising=False)
        config = Stub(log_dir=str(tmp_path))
        cmd = [sys.executable, "-c",
               probe(["src/a.cpp:3:1: warning: 未使用", "make: 已是最新"])]
        _, out, _ = capture(stage.run_quiet, cmd, str(tmp_path), config,
                            "build")
        assert "#diag warning: src/a.cpp:3:1: 未使用" in out
        assert "已是最新" not in out

    def test_non_diagnostic_output_goes_to_the_log(self, tmp_path, monkeypatch):
        monkeypatch.delenv(stage.VERBOSE_ENV, raising=False)
        config = Stub(log_dir=str(tmp_path))
        cmd = [sys.executable, "-c", probe(["make: 已是最新"])]
        stage.run_quiet(cmd, str(tmp_path), config, "build")
        text = log_of(config, "build")
        assert "make: 已是最新" in text
        assert "$ " in text

    def test_exit_code_is_returned(self, tmp_path, monkeypatch):
        monkeypatch.delenv(stage.VERBOSE_ENV, raising=False)
        config = Stub(log_dir=str(tmp_path))
        cmd = [sys.executable, "-c", "raise SystemExit(3)"]
        assert 3 == stage.run_quiet(cmd, str(tmp_path), config, "build")


class TestRunStage:
    def script(self, tmp_path, body):
        return write(tmp_path / "stage.py", body)

    def test_captures_output_and_composes_code(self, tmp_path):
        config = Stub(log_dir=str(tmp_path))
        script = self.script(tmp_path, probe(["阶段输出"]))
        result = stage.run_stage(config, "build", script)
        assert 0 == result.code
        assert "阶段输出" in result.out
        assert not result.timed_out

    def test_nonzero_exit_becomes_an_error_code(self, tmp_path):
        config = Stub(log_dir=str(tmp_path))
        script = self.script(tmp_path, "raise SystemExit(3)\n")
        result = stage.run_stage(config, "build", script)
        assert 3 == errors.status(result.code)

    def test_log_is_truncated_before_the_stage_runs(self, tmp_path):
        config = Stub(log_dir=str(tmp_path))
        stage.append_log(config, "build", "上一次的残留\n")
        script = self.script(tmp_path, probe(["本次输出"]))
        result = stage.run_stage(config, "build", script)
        stage.write_log(config, result)
        text = log_of(config, "build")
        assert "上一次的残留" not in text
        assert "本次输出" in text

    def test_extra_args_are_forwarded(self, tmp_path):
        config = Stub(log_dir=str(tmp_path))
        script = self.script(tmp_path,
                             "import sys\nprint(sys.argv[1:])\n")
        result = stage.run_stage(config, "build", script, ["--jobs", "1"])
        assert "--jobs" in result.out

    def test_timeout_is_marked_and_killed(self, tmp_path):
        config = Stub(log_dir=str(tmp_path))
        script = self.script(tmp_path, "import time\ntime.sleep(30)\n")
        result = stage.run_stage(config, "build", script, (), False, 1)
        assert result.timed_out
        assert errors.STAGE_TIMEOUT == result.code


class FakeConfig(object):
    """emit 需要的配置字段。"""

    def __init__(self, root, log_dir, level="warning", sources="project",
                 dedup=True, fail_output="summary"):
        self.root = str(root)
        self.scope = {"project": [str(root)], "excluded": []}
        self.whitelist = [str(root)]
        self.blacklist = []
        self.fail_output = fail_output
        self.build_dir = str(root)
        self.test_build_dir = str(root)
        self.log_dir = str(log_dir)
        self.diagnostic_level = level
        self.diagnostic_sources = sources
        self.diagnostic_ignore = []
        self.diagnostic_dedup = dedup


class TestEmitLevels:
    def test_quiet_success_is_silent(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path)
        _, out, err = capture(stage.emit, config,
                              make_result(code=0, out="日志"), "quiet")
        assert "" == out

    def test_normal_shows_protocol_diagnostics_only(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path)
        result = make_result(code=0, out=protocol.diag(
            "warning", str(tmp_path / "src" / "a.cpp"), 3, 1, "未使用变量")
            + "\n普通日志")
        _, out, _ = capture(stage.emit, config, result, "normal")
        assert "未使用变量" in out
        assert "普通日志" not in out

    def test_normal_shows_summary_lines(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path)
        result = make_result(code=0, out=protocol.summary("共 34 例（3 套件），失败 0 例"))
        _, out, _ = capture(stage.emit, config, result, "normal")
        assert "共 34 例（3 套件），失败 0 例" in out

    def test_full_shows_everything_even_on_failure(self, tmp_path):
        """交互入口不筛内容：差异清单本身就是结论。"""
        config = FakeConfig(tmp_path, tmp_path)
        result = make_result(code=1, out="--- a.cpp\n+++ a.cpp\n-旧\n+新")
        _, out, _ = capture(stage.emit, config, result, "full")
        assert "-旧" in out
        assert "FAILED" in out

    def test_verbose_shows_everything_on_success(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path)
        result = make_result(code=0, out="阶段全量输出")
        _, out, _ = capture(stage.emit, config, result, "verbose")
        assert "阶段全量输出" in out

    def test_failure_lists_protocol_failures_first(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path)
        result = make_result(code=1, out=protocol.fail("A.b", "断言不成立"))
        _, out, _ = capture(stage.emit, config, result, "normal")
        assert "A.b" in out
        assert "1 例失败" in out


class TestProtocolStripping:
    """协议前缀是阶段与驱动之间的内部通道，不该出现在面向人的输出里。"""

    def test_diag_line_becomes_a_compiler_style_line(self):
        assert "/a/b.cpp:3:16: error: msg [check]" == protocol.plain(
            "#diag error: /a/b.cpp:3:16: msg [check]")

    def test_fail_line_loses_the_prefix(self):
        assert "A.b boom" == protocol.plain("#fail: A.b boom")

    def test_sum_line_loses_the_prefix(self):
        assert "共 34 例，失败 0 例" == protocol.plain("#sum: 共 34 例，失败 0 例")

    def test_ordinary_text_is_untouched(self):
        assert "普通文本" == protocol.plain("普通文本")

    def test_plain_text_maps_every_line(self):
        text = "#sum: 第一行\n普通\n#fail: A.b x"
        assert "第一行\n普通\nA.b x" == protocol.plain_text(text)

    def test_full_level_shows_no_bare_prefix(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path)
        result = make_result(code=1, out="#sum: 结论\n#fail: A.b boom\n普通日志")
        _, out, _ = capture(stage.emit, config, result, "full")
        assert "#sum:" not in out
        assert "#fail:" not in out
        assert "结论" in out
        assert "A.b boom" in out
        assert "普通日志" in out

    def test_verbose_level_shows_no_bare_prefix(self, tmp_path):
        config = FakeConfig(tmp_path, tmp_path)
        result = make_result(code=0, out="#diag warning: /a.cpp:1:1: x")
        _, out, _ = capture(stage.emit, config, result, "verbose")
        assert "#diag" not in out
        assert "/a.cpp:1:1: warning: x" in out

    def test_log_keeps_the_raw_protocol_lines(self, tmp_path):
        """人看的是还原后的文本，日志留原始协议行便于逐字核对。"""
        config = FakeConfig(tmp_path, tmp_path)
        result = make_result(code=0, out="#diag warning: /a.cpp:1:1: x")
        capture(stage.emit, config, result, "verbose")
        assert "#diag warning: /a.cpp:1:1: x" in log_of(config, "build")
