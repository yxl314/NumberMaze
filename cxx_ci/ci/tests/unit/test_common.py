#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享工具的单元测试。"""

import io
import os
import sys
import tempfile
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "test"))

from lib import build, cli, errors, output, paths, tools
from ci_support import Stub, capture, parse


class TestSharedArgs:
    def test_defaults(self):
        args = parse([])
        assert "debug" == args.build_type
        assert "build" == args.build_dir
        assert 0 == args.jobs
    def test_override(self):
        args = parse(["--build-type", "release", "--build-dir", "/tmp/x",
                      "--jobs", "4"])
        assert "release" == args.build_type
        assert "/tmp/x" == args.build_dir
        assert 4 == args.jobs
    def test_rejects_unknown_build_type(self):
        err = io.StringIO()
        real_err = sys.stderr
        sys.stderr = err
        try:
            with pytest.raises(SystemExit):
                parse(["--build-type", "fast"])
        finally:
            sys.stderr = real_err
        assert "invalid choice" in err.getvalue()
class TestForwardArgs:
    def test_expected_sequence(self):
        args = parse(["--build-type", "release", "--jobs", "3"])
        assert ['--build-type', 'release', '--build-dir', 'build', '--jobs', '3'] == cli.forward_args(args)

    def test_forwarded_args_can_be_reparsed(self):
        """透传给阶段脚本的片段必须能被同一套参数重新解析。"""
        args = parse(["--build-type", "release", "--jobs", "3"])
        again = parse(cli.forward_args(args))
        assert args.build_type == again.build_type
        assert args.build_dir == again.build_dir
        assert args.jobs == again.jobs
class TestDecode:
    def test_utf8_text(self):
        assert "中文" == output.decode("中文".encode("utf-8"))
    def test_invalid_bytes_are_replaced(self):
        assert "a\ufffdb" == output.decode(b"a\xffb")
class TestOutput:
    def test_out_and_err_write_one_line_each(self):
        _, out, err = capture(output.out, "普通")
        _, err_out, err_err = capture(output.err, "诊断")
        assert "普通\n" == out
        assert "诊断\n" == err_err
class TestFindTool:
    def test_finds_existing_executable(self):
        found, code, message = tools.find_tool(("sh",))
        assert code == errors.OK
        assert found
    def test_reports_missing_tool(self):
        found, code, message = tools.find_tool(("no-such-tool-cxx-ci",))
        assert found is None
        assert errors.TOOL_MISSING == code
class TestJobCount:
    def test_explicit_value_is_kept(self):
        assert 4 == tools.job_count(4)
    def test_zero_and_negative_mean_auto(self):
        assert tools.job_count(0) >= 1
        assert tools.job_count(-1) >= 1
class TestSplitStandard:
    def test_plain_standard(self):
        assert ("98", False) == build.split_standard("c++98")
    def test_gnu_standard_enables_extensions(self):
        assert ("98", True) == build.split_standard("gnu++98")
    def test_modern_standard(self):
        assert ("14", False) == build.split_standard("c++14")
class TestBuildToolBin:
    def test_config_value_wins(self):
        config = Stub(build_tool="cmake", build_tool_bin="/custom/cmake")
        assert "/custom/cmake" == build.build_tool_bin(config)
    def test_empty_falls_back_to_detection(self):
        config = Stub(build_tool="cmake", build_tool_bin="")
        found = build.build_tool_bin(config)
        assert found is None or "cmake" in found
class TestMemcheckCommand:
    def test_base_options_and_test_command(self):
        config = Stub(memcheck_suppressions="/nonexistent.supp",
                      memcheck_command=["/bin/true"])
        assert ['/usr/bin/valgrind', '--leak-check=full', '--error-exitcode=1', '/bin/true'] == tools.memcheck_command('/usr/bin/valgrind', config)

    def test_suppressions_added_only_when_file_exists(self):
        config = Stub(memcheck_suppressions=sys.executable,
                      memcheck_command=["/bin/true"])
        assert '--suppressions=' + sys.executable in tools.memcheck_command('valgrind', config)


class TestResolvePaths:
    def test_splits_and_absolutizes(self):
        assert ["/r/a" == "/r/b"], paths.resolve_paths("a, b", "/r")
    def test_ignores_empty_items(self):
        assert [] == paths.resolve_paths(" , ", "/r")
class TestCollectFiles:
    def test_collects_cxx_files_and_skips_excluded(self):
        root = tempfile.mkdtemp(prefix="cxx_ci_files_")
        os.makedirs(os.path.join(root, "src"))
        os.makedirs(os.path.join(root, "vendor"))
        for name in ("src/a.cpp", "src/b.h", "src/c.txt", "vendor/d.cpp"):
            with open(os.path.join(root, name), "w") as handle:
                handle.write("x")
        found = paths.collect_files([root], [os.path.join(root, "vendor")])
        assert [os.path.join(root, 'src', 'a.cpp'), os.path.join(root, 'src', 'b.h')] == found

    def test_suffix_filter_keeps_only_sources(self):
        root = tempfile.mkdtemp(prefix="cxx_ci_files_")
        for name in ("a.cpp", "b.h"):
            with open(os.path.join(root, name), "w") as handle:
                handle.write("x")
        found = paths.collect_files([root], [], paths.SOURCE_SUFFIXES)
        assert [os.path.join(root, "a.cpp")] == found
