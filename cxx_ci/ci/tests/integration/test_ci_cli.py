#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ci.py 命令行行为的集成测试。"""

import json
import signal
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helper import CONFIG_TEMPLATE, SandboxCase

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lib import errors

ECHO_ARGV = "import sys\nprint('ARGV ' + ' '.join(sys.argv[1:]))\n"
# 一条落在项目内的诊断协议行；相对路径按构建目录解析后落在 ROOT/src
PROBE_DIAG = ("import sys\n"
              "sys.stdout.buffer.write("
              "'#diag error: ../../src/a.cpp:3:1: 告警\\n'"
              ".encode('utf-8'))\n"
              "sys.stdout.buffer.write('普通日志\\n'.encode('utf-8'))\n")
# 写下自身 PID，供测试确认进程是否被清理
SLEEP_LONG = (
    "import os, time\n"
    "root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
    "open(os.path.join(root, 'stage.pid'), 'w').write(str(os.getpid()))\n"
    "time.sleep(30)\n")
FAIL = "import sys\nsys.exit(1)\n"
# 与模板唯一区别是构建目录，用于验证配置驱动的默认值
CONFIG = CONFIG_TEMPLATE.replace('\nBUILD_DIR = ROOT / "build"\n',
                                 '\nBUILD_DIR = ROOT / "out"\n')


class TestCiCli(SandboxCase):
    def test_skipping_every_selected_stage_is_reported(self):
        """筛完一个阶段都不剩时必须出声。

        `--only boundary --skip boundary` 什么都不跑；默默退出 0 等于告诉使用者
        "跑过了、没问题"，而实际上一段都没跑。
        """
        code, out, err = self.box.run(
            "ci.py", ["--only", "boundary", "--skip", "boundary"])
        assert 0 != code, (out, err)
        assert "没有可执行的阶段" in (out + err), (out, err)

    def test_report_path_with_comma_is_rejected(self):
        """--report 只收一个路径。

        逗号分隔的是 --report-format 的格式列表。照着带逗号的路径去建目录，
        既不报错也不提示，使用者只会看到一份"没写出来的报告"。
        """
        target = os.path.join(str(self.box.path), "a.json,b.json")
        code, out, err = self.box.run(
            "ci.py", ["--only", "boundary", "--report", target])
        assert 0 == code, (out, err)
        assert "只收一个路径" in err, err
        assert not os.path.isdir(target + ","), "不该造出名字带逗号的目录"

    def test_passes_shared_args_through(self):
        self.box.stage("build", ECHO_ARGV)
        code, out, err = self.box.run("ci.py", [
            "--only", "build", "--level", "verbose",
            "--build-type", "release", "--build-dir", "/tmp/bd", "--jobs", "6"])
        assert 0 == code
        assert 'ARGV --build-type release --build-dir /tmp/bd --jobs 6' in out

    def test_only_selects_stages(self):
        # 阶段脚本置空即成功的空操作，这里只验证编排，不验证阶段实现。
        # 置空会让这些阶段原本读的配置项变成"没人用"，配置审计因此会（正确地）
        # 报死配置——这一条测的是编排，与审计无关，跳过它
        self.box.stage("build", "")
        self.box.stage("run_test", "")
        code, out, err = self.box.run("ci.py",
                                      ["--only", "build,run_test", "--no-audit"])
        assert 0 == code
        assert 'build' in out
        assert 'memcheck' not in out

    def test_skip_drops_stages(self):
        self.box.stage("build", "")
        code, out, err = self.box.run(
            "ci.py", ["--skip", "memcheck,run_test,build_test"])
        assert 0 == code
        assert 'memcheck' not in out
        assert 'build' in out

    def test_config_outside_the_tool_is_used(self):
        """配置可以放在工具之外：CI 系统分开签出工具与项目时靠这个。"""
        elsewhere = self.box.path / "elsewhere"
        elsewhere.mkdir()
        path = elsewhere / "config.py"
        with open(str(path), "w", encoding="utf-8") as handle:
            handle.write(
            "from pathlib import Path\n"
            "ROOT = Path({!r})\n"
            "BUILD_TOOL = \"cmake\"\n"
            "BUILD_DIR = ROOT / \"out\"\n"
            "SCOPE = {{\"project\": [ROOT / \"src\"], "
            "\"excluded\": [ROOT / \"out\"]}}\n".format(str(self.box.path)))
        # 构建文件按 BUILD_TOOL 自动探测，因此只需它存在
        with open(str(self.box.path / "CMakeLists.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("project(p)\n")
        self.box.stage("build", ECHO_ARGV)
        code, out, err = self.box.run(
            "ci.py", ["--only", "build", "--no-audit", "--level", "verbose",
                      "--config", str(path)])
        assert 0 == code, (out, err)
        # 构建目录来自那份配置（工具自带的配置里是 build/），证明配置被采纳
        assert "ARGV --build-type debug --build-dir {}/out --jobs 0".format(
            self.box.path) in out

    def test_unknown_stage_exits_with_two(self):
        code, out, err = self.box.run("ci.py", ["--only", "nope"])
        assert errors.status(errors.UNKNOWN_STAGE) == code
        assert '未知阶段' in err

    def test_quiet_is_silent_on_success(self):
        self.box.stage("build", "")
        code, out, err = self.box.run("ci.py", ["--only", "build", "--level", "quiet"])
        assert 0 == code
        assert '' == out.strip()

    def test_quiet_still_reports_failure(self):
        self.box.stage("build", FAIL)
        code, out, err = self.box.run("ci.py", ["--only", "build", "--level", "quiet"])
        assert code != 0
        assert 'FAILED' in out

    def test_only_compiler_diagnostics_are_echoed(self):
        """业务输出里的级别词不算诊断，编译器版式才算。"""
        # 阶段跑在 LC_ALL=C 下，非 ASCII 要显式按 UTF-8 写出
        self.box.stage("build",
                       "import sys\n"
                       "sys.stdout.buffer.write("
                       "'2026-01-01 |ERROR| 业务日志\\n'.encode('utf-8'))\n"
                       "sys.stdout.buffer.write("
                       "'src/a.cpp:12:5: error: 真实的编译器诊断\\n'"
                       ".encode('utf-8'))\n")
        code, out, err = self.box.run("ci.py", ["--only", "build"])
        assert '真实的编译器诊断' in out
        assert '|ERROR|' not in out

    def test_full_output_is_kept_in_log(self):
        """控制台筛掉的输出必须能在日志里查到。"""
        self.box.stage("build",
                       "import sys\n"
                       "sys.stdout.buffer.write("
                       "'2026-01-01 |ERROR| 业务日志\\n'.encode('utf-8'))\n")
        code, out, err = self.box.run("ci.py", ["--only", "build"])
        assert '|ERROR|' not in out
        logs = list((self.box.path / "build" / "ci-logs").glob("*.log"))
        assert logs
        handle = open(str(logs[0]))
        try:
            written = handle.read()
        finally:
            handle.close()
        assert '|ERROR|' in written

    def probe_command(self, body, env=None, extra=""):
        """把 TEST_COMMAND 换成一段脚本，用真实阶段脚本跑。"""
        script = self.box.path / "probe.sh"
        with open(str(script), "w") as handle:
            handle.write("#!/bin/sh\n" + body)
        os.chmod(str(script), 0o755)
        text = CONFIG_TEMPLATE.replace(
            'TEST_COMMAND = [TEST_BUILD_DIR / "run_tests"]',
            'TEST_COMMAND = ["' + str(script) + '"]')
        text = text.replace('TEST_WORKDIR = TEST_BUILD_DIR',
                            'TEST_WORKDIR = ROOT')
        # 追加在配置末尾：模块级后赋值生效，也兼容模板里没有该项的情况
        if env:
            extra += "\nTEST_ENV = " + repr(env) + "\n"
        self.box.configure(text + extra)

    def test_run_test_fails_on_per_case_failure_with_zero_exit(self):
        """逐例失败但 runner 退出 0，必须判失败。

        这是最危险的一类静默漏报：驱动层只认退出码就会整阶段
        误判为通过。
        """
        self.probe_command("echo 'FAIL!  : A.b() boom'\nexit 0\n")
        code, out, err = self.box.run("ci.py", ["--only", "run_test"])
        assert 0 != code
        assert 'A.b' in out

    def test_run_test_clean_output_passes(self):
        self.probe_command("echo 'Totals: 2 passed, 0 failed'\nexit 0\n")
        code, out, err = self.box.run("ci.py", ["--only", "run_test"])
        assert 0 == code

    def test_run_test_failure_is_reported_before_full_log(self):
        self.probe_command("echo 'FAIL!  : A.b() boom'\nexit 1\n")
        code, out, err = self.box.run("ci.py", ["--only", "run_test"])
        assert 0 != code
        assert '完整输出见' in out

    def test_test_env_is_injected(self):
        """配置里的 TEST_ENV 要注入测试子进程。"""
        self.probe_command("echo \"SENTINEL=$CXX_CI_PROBE\"\n",
                           env={"CXX_CI_PROBE": "on"})
        code, out, err = self.box.run("ci.py", ["--only", "run_test"])
        assert 0 == code
        # 成功阶段不回显业务输出（这正是降噪的目的），完整输出在日志里
        logs = list((self.box.path / "build" / "ci-logs").glob("run_test.log"))
        assert logs
        with open(str(logs[0])) as handle:
            assert 'SENTINEL=on' in handle.read()

    def test_reports_summary(self):
        code, out, err = self.box.run("ci.py", ["--only", "build"])
        assert '汇总:' in out
        assert '总计' in out

    def test_timeout_kills_stage(self):
        self.box.stage("build", SLEEP_LONG)
        code, out, err = self.box.run("ci.py", ["--only", "build", "--timeout", "2"])
        assert code != 0
        assert 'TIMEOUT' in out
        time.sleep(0.5)
        assert not self.box.stage_alive()

    def test_sigint_exits_130_without_leftovers(self):
        if signal.getsignal(signal.SIGINT) == signal.SIG_IGN:
            self.skipTest("当前进程忽略了 SIGINT，无法测试中断处理")
        for extra in ([], ["--level", "verbose"]):
            self.box.stage("build", SLEEP_LONG)
            proc = self.box.spawn("ci.py", ["--only", "build"] + extra)
            time.sleep(1.5)
            proc.send_signal(signal.SIGINT)
            proc.wait()
            time.sleep(0.5)
            assert 130 == proc.returncode
            assert not self.box.stage_alive()

    def test_locale_c_keeps_stage_output_readable(self):
        """LC_ALL=C 下非 ASCII 的告警仍要能正确解码显示。"""
        self.box.stage("build",
                       "import sys\n"
                       "sys.stdout.buffer.write("
                       "'src/a.cpp:12:5: error: 中文诊断\\n'.encode('utf-8'))\n")
        code, out, err = self.box.run(
            "ci.py", ["--only", "build"], env={"LANG": "C", "LC_ALL": "C"})
        assert 0 == code
        assert '中文诊断' in out

    def test_stage_runs_under_fixed_locale(self):
        """阶段必须跑在 C 区域设置下。

        编译器会把级别词本地化（中文环境下 g++ 输出 "警告：" 而非
        "warning:"），诊断解析依赖固定版式，输出语言必须稳定。
        """
        self.box.stage("build",
                       "import os\n"
                       "print('src/a.cpp:1:1: error: LC=' + "
                       "os.environ.get('LC_ALL', ''))\n")
        code, out, err = self.box.run(
            "ci.py", ["--only", "build"], env={"LC_ALL": "zh_CN.UTF-8"})
        assert 0 == code
        assert 'LC=C' in out

    def test_help_readable_under_locale_c(self):
        code, out, err = self.box.run(
            "ci.py", ["--help"], env={"LANG": "C", "LC_ALL": "C"})
        assert 0 == code
        assert '阶段脚本位于' in out
        assert '\\u' not in out

    def test_version_line_without_config(self):
        """--version 与 cxx-standardize 同形状，且在配置加载之前就返回。

        指向一个不存在的配置也要能打出：没有配置的工程也得能问"这是哪一版"。
        """
        code, out, err = self.box.run(
            "ci.py", ["--version", "--config", str(self.box.path / "nope.py")])
        assert 0 == code
        assert out.strip().startswith("ci ")
        assert "contract " in out and "python >=3.4" in out

    def test_contract_mismatch_exits_2(self):
        """配置声明的契约版本与工具不一致：拒绝执行，退出码 2。"""
        self.box.configure(CONFIG_TEMPLATE + "CONTRACT_VERSION = 1\n")
        self.box.stage("build", "")
        code, out, err = self.box.run("ci.py", ["--only", "build"])
        assert 2 == code
        assert "契约版本" in err

    def test_contract_declaration_may_be_omitted(self):
        """不声明契约版本（模板就是这样）时静默通过，取工具缺省。"""
        self.box.stage("build", "")
        code, out, err = self.box.run("ci.py", ["--only", "build", "--no-audit"])
        assert 0 == code

    def test_build_dir_default_comes_from_config(self):
        self.box.configure(CONFIG)
        self.box.stage("build", ECHO_ARGV)
        code, out, err = self.box.run(
            "ci.py", ["--only", "build", "--level", "verbose"])
        assert 0 == code
        assert '--build-dir ' + str(self.box.path.resolve() / 'out') in out

    def test_cli_build_dir_overrides_config(self):
        self.box.configure(CONFIG)
        self.box.stage("build", ECHO_ARGV)
        code, out, err = self.box.run(
            "ci.py", ["--only", "build", "--level", "verbose",
                      "--build-dir", "custom"])
        assert 0 == code
        assert '--build-dir custom' in out

    def diagnostic_config(self, extra):
        """把模板改成 warning 级、只看项目自身。"""
        text = CONFIG_TEMPLATE.replace('DIAGNOSTIC_LEVEL = "error"',
                                       'DIAGNOSTIC_LEVEL = "warning"')
        self.box.configure(text + extra)

    def emit_diag(self, path):
        """让 build 阶段发一条指向给定路径的 #diag 协议行。"""
        line = "#diag warning: " + path + ":3:1: 项目自身的告警\n"
        self.box.stage("build",
                       "import sys\n"
                       "sys.stdout.buffer.write({!r}.encode('utf-8'))\n"
                       .format(line))

    def test_relative_diagnostic_is_attributed_to_project(self):
        """编译器给的是相对路径时，项目自身的告警不能被当域外静默。

        诊断行里的路径是相对构建目录的（如 ../../Source/CDBurn/…），
        未归一化就直接比 SCOPE 前缀，项目自己的告警会被判成域外而静默。
        """
        self.diagnostic_config("")
        # 相对测试构建目录（build/test）解析后正好落在 ROOT/src
        self.emit_diag("../../src/a.cpp")
        code, out, err = self.box.run("ci.py", ["--only", "build"])
        assert 0 == code
        assert '项目自身的告警' in out

    def test_absolute_project_diagnostic_is_attributed_to_project(self):
        self.diagnostic_config("")
        self.emit_diag(str(self.box.path.resolve() / "src" / "a.cpp"))
        code, out, err = self.box.run("ci.py", ["--only", "build"])
        assert 0 == code
        assert '项目自身的告警' in out

    def test_external_diagnostic_stays_silent(self):
        self.diagnostic_config("")
        self.emit_diag("/opt/qt/include/qvariant.h")
        code, out, err = self.box.run("ci.py", ["--only", "build"])
        assert 0 == code
        assert '项目自身的告警' not in out

    def test_heuristic_fallback_also_normalises(self):
        """没有协议行时退回解析编译器版式，同样要先归一化路径。"""
        self.diagnostic_config("")
        self.box.stage("build",
                       "import sys\n"
                       "sys.stdout.buffer.write("
                       "'../../src/a.cpp:3:1: warning: 启发式告警\\n'"
                       ".encode('utf-8'))\n")
        code, out, err = self.box.run("ci.py", ["--only", "build"])
        assert 0 == code
        assert '启发式告警' in out
        assert '(启发式)' in out

    def test_xfail_registry_turns_known_failure_green(self):
        """已登记的失败不计失败，但仍回显并标注 (xfail)。"""
        self.probe_command("echo 'FAIL!  : A.b() boom'\nexit 0\n")
        self.box.write_xfail("[test]\nA.b()\n")
        code, out, err = self.box.run("ci.py", ["--only", "run_test"])
        assert 0 == code
        assert 'A.b' in out
        assert '(xfail)' in out

    def test_unregistered_failure_still_fails(self):
        self.probe_command("echo 'FAIL!  : A.b() boom'\nexit 0\n")
        self.box.write_xfail("[test]\nA.other()\n")
        code, out, err = self.box.run("ci.py", ["--only", "run_test"])
        assert 0 != code

    def test_strict_mode_rejects_stale_entries(self):
        """登记项已修复却没删行时，strict 直接判红防腐化。"""
        body = "echo 'Totals: 2 passed, 0 failed'\nexit 0\n"
        self.probe_command(body)
        self.box.write_xfail("[test]\nA.b()\n")
        code, out, err = self.box.run("ci.py", ["--only", "run_test"])
        assert 0 == code
        assert '可移除' in out
        # 打开 strict 后同一份清单直接判红
        self.probe_command(body, extra="\nXFAIL_STRICT = True\n")
        code, out, err = self.box.run("ci.py", ["--only", "run_test"])
        assert errors.status(errors.XFAIL_STALE) == code

    def test_broken_registry_is_reported(self):
        self.probe_command("echo 'Totals: 2 passed, 0 failed'\nexit 0\n")
        self.box.write_xfail("[styel]\nA.b()\n")
        code, out, err = self.box.run("ci.py", ["--only", "run_test"])
        assert errors.status(errors.XFAIL_BAD_FILE) == code
        # 清单写错必须当场说清，否则使用者会以为门禁莫名其妙变红
        assert '分段头' in out

    def test_verbose_streams_without_protocol_prefixes(self):
        """透传档也要挡住协议前缀：它是内部通道，不是给人看的文本。"""
        self.box.stage("build", PROBE_DIAG)
        code, out, err = self.box.run("ci.py", ["--only", "build", "--level",
                                                "verbose"])
        assert 0 == code, (out, err)
        assert '#diag' not in out
        assert 'src/a.cpp:3:1: error: 告警' in out
        assert '普通日志' in out
        logs = list((self.box.path / "build" / "ci-logs").glob("build.log"))
        assert logs
        with open(str(logs[0])) as handle:
            assert '#diag error: ../../src/a.cpp:3:1: 告警' in handle.read()

    def test_normal_level_has_no_protocol_prefix_either(self):
        self.box.stage("build", PROBE_DIAG)
        code, out, err = self.box.run("ci.py", ["--only", "build"])
        assert 0 == code, (out, err)
        assert '#diag' not in out
        assert 'src/a.cpp:3:1: error: 告警' in out

    def test_multi_suite_totals_are_summed_in_summary(self):
        """多套件 runner 的用例总数必须是各段之和。"""
        self.probe_command(
            "echo 'Totals: 11 passed, 0 failed, 0 skipped, 0 blacklisted'\n"
            "echo 'Totals: 15 passed, 0 failed, 0 skipped, 0 blacklisted'\n"
            "echo 'Totals: 8 passed, 0 failed, 0 skipped, 0 blacklisted'\n"
            "exit 0\n")
        code, out, err = self.box.run("ci.py", ["--only", "run_test"])
        assert 0 == code
        logs = list((self.box.path / "build" / "ci-logs").glob("run_test.log"))
        assert logs
        with open(str(logs[0])) as handle:
            assert '共 34 例（3 套件），失败 0 例' in handle.read()


# 阶段脚本：退出码 10 = errors.status(STAGE_UNAVAILABLE)，驱动层据此标跳过
SKIP_STAGE = "import sys\nsys.exit(10)\n"


class TestReportCli(SandboxCase):
    """--report / --report-format 的端到端行为。

    报告是旁路产物：它必须与终端结论同源，且不改变退出码。
    """

    def _read(self, relative):
        path = self.box.path / relative
        with open(str(path), encoding="utf-8") as handle:
            return json.loads(handle.read())

    def test_json_report_written_and_same_source(self):
        self.box.stage("build", PROBE_DIAG)
        code, out, err = self.box.run(
            "ci.py", ["--only", "build", "--no-audit",
                      "--report=build/ci-report.json"])
        assert 0 == code, (out, err)
        document = self._read("build/ci-report.json")
        assert 1 == document["report_version"]
        assert 3 == document["tool"]["contract"]
        assert "build" == document["stages"][0]["name"]
        assert "passed" == document["stages"][0]["status"]
        # 诊断与终端同源：级别与消息都搬自同一条 #diag 协议行
        entry = document["stages"][0]["diagnostics"][0]
        assert "error" == entry["level"]
        assert "告警" == entry["message"]
        assert "src/a.cpp" in entry["file"]

    def test_report_to_stdout_is_clean_json_under_quiet(self):
        self.box.stage("build", "")
        code, out, err = self.box.run(
            "ci.py", ["--only", "build", "--no-audit", "--level", "quiet",
                      "--report"])
        assert 0 == code, (out, err)
        document = json.loads(out.strip())
        assert "build" == document["stages"][0]["name"]
        assert "passed" == document["stages"][0]["status"]

    def test_multiple_formats_get_suffixes(self):
        self.box.stage("build", "")
        code, out, err = self.box.run(
            "ci.py", ["--only", "build", "--no-audit", "--level", "quiet",
                      "--report=build/rep", "--report-format", "junit,sarif"])
        assert 0 == code, (out, err)
        assert (self.box.path / "build" / "rep.xml").is_file()
        assert (self.box.path / "build" / "rep.sarif").is_file()

    def test_skipped_stage_stays_skipped(self):
        self.box.stage("build", SKIP_STAGE)
        code, out, err = self.box.run(
            "ci.py", ["--only", "build", "--no-audit",
                      "--report=build/ci-report.json"])
        assert 0 == code, (out, err)
        document = self._read("build/ci-report.json")
        assert "skipped" == document["stages"][0]["status"]
        assert document["stages"][0]["skipped_reason"]

    def test_failing_stage_still_writes_report(self):
        self.box.stage("build", FAIL)
        code, out, err = self.box.run(
            "ci.py", ["--only", "build", "--no-audit",
                      "--report=build/ci-report.json"])
        assert 0 != code
        document = self._read("build/ci-report.json")
        assert "failed" == document["stages"][0]["status"]

    def test_unknown_format_only_warns(self):
        """格式写错只警告，不改退出码。"""
        self.box.stage("build", "")
        code, out, err = self.box.run(
            "ci.py", ["--only", "build", "--no-audit", "--level", "quiet",
                      "--report=build/x.json", "--report-format", "yaml"])
        assert 0 == code, (out, err)
        assert "未知报告格式" in err
        assert not (self.box.path / "build" / "x.json").is_file()


class TestInitCli(SandboxCase):
    """`ci.py init`：探测生成配置，不需要已有配置。"""

    def write_build_file(self, text="project(x)\nenable_testing()\n"):
        with open(str(self.box.path / "CMakeLists.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write(text)

    def test_writes_config_and_names_todos(self):
        self.write_build_file()
        code, out, err = self.box.run(
            "ci.py", ["init", "--project-root", str(self.box.path)])
        assert 0 == code, (out, err)
        assert (self.box.path / "config" / "ci.py").is_file()
        assert "已生成" in out

    def test_generated_config_is_usable(self):
        """生成物必须能被 load_config 接受，并被后续阶段采纳。"""
        self.write_build_file()
        self.box.stage("build", ECHO_ARGV)
        code, out, err = self.box.run(
            "ci.py", ["init", "--project-root", str(self.box.path)])
        assert 0 == code, (out, err)
        code, out, err = self.box.run(
            "ci.py", ["--only", "build", "--no-audit", "--level", "verbose",
                      "--config", str(self.box.path / "config" / "ci.py")])
        assert 0 == code, (out, err)
        assert "ARGV --build-type debug --build-dir {}/build --jobs 0".format(
            self.box.path) in out

    def test_does_not_overwrite_existing_config(self):
        target = self.box.path / "config" / "ci.py"
        with open(str(target), "w", encoding="utf-8") as handle:
            handle.write("# 人工写好的配置\n")
        code, out, err = self.box.run(
            "ci.py", ["init", "--project-root", str(self.box.path)])
        assert code != 0
        assert "不覆盖" in err

