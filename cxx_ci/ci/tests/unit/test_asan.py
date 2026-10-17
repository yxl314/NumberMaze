#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""asan 双通道路线的单元测试。

不真跑构建与净化器：命令构造、环境与判定都是纯函数的产物，用一份临时配置
加文本夹具覆盖即可，因此本文件在没有 g++、libasan 或 valgrind 的机器上也能
跑绿。"不跑二进制"这类断言用 monkeypatch 记下调用，而不是指望机器上恰好
没有工具。
"""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import asan, errors, xfail  # noqa: E402
from lib.config import (DEFAULTS, TOOL_ROOT, example_path,  # noqa: E402
                        load_config)


# 最小可用配置：路径都落在临时项目根下。构建文件不必真实存在——配置只校验
# 路径形状与构建工具是否匹配，不校验存在性。
CONFIG_TEMPLATE = """\
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD_TOOL = "{tool}"
BUILD_TOOL_BIN = ""
BUILD_FILE = ROOT / "{build_file}"
BUILD_DIR = ROOT / "build"
CXX_COMPILER = "g++"
CXX_STANDARD = "c++98"
TEST_BUILD_FILE = ROOT / "build" / "tests" / "{test_build_file}"
TEST_BUILD_DIR = ROOT / "build" / "tests"
TEST_COMMAND = [TEST_BUILD_DIR / "run_tests", "--brief"]
TEST_WORKDIR = TEST_BUILD_DIR
MEMCHECK_COMMAND = TEST_COMMAND
SCOPE = {{"project": [ROOT / "src"], "excluded": [ROOT / "build"]}}
"""

# 构建工具到 (主构建文件, 测试构建文件) 的映射，文件名必须与工具匹配
BUILD_FILES = {
    "qmake6": ("project.pro", "tests.pro"),
    "cmake": ("CMakeLists.txt", "CMakeLists.txt"),
}

# regex 版逐例失败行；asis 通道的判定只看适配器解析出的用例名
QT_FAIL = "FAIL!  : {} Compared values are not the same\n"
QT_TOTALS = "Totals: 0 passed, 1 failed, 0 skipped\n"
REGEX_CONFIG = ('TEST_REPORT_ADAPTER = "regex"\n'
                'TEST_FAIL_PATTERN = r"^FAIL!\\s+:\\s*(?P<case>\\S+)"\n')

# 一段真实的 asan 报告：指纹与定位行都在
ASAN_REPORT = (
    "==1==ERROR: AddressSanitizer: heap-use-after-free on address 0x1234\n"
    "    #0 0x1 in Widget::~Widget() /proj/src/widget.cpp:12\n"
    "==1==SUMMARY: AddressSanitizer: heap-use-after-free /proj/src/widget.cpp\n"
)


class Args(object):
    """阶段脚本统一收到的共享参数。"""

    build_type = "debug"
    build_dir = "build"
    jobs = 0


def configured(extra="", tool="qmake6"):
    """写一份最小配置并加载，返回 (配置, 项目根)。"""
    directory = tempfile.mkdtemp(prefix="cxx_ci_asan_")
    build_file, test_build_file = BUILD_FILES[tool]
    text = CONFIG_TEMPLATE.format(tool=tool, build_file=build_file,
                                  test_build_file=test_build_file) + extra
    path = Path(directory) / "config.py"
    with open(str(path), "w", encoding="utf-8") as handle:
        handle.write(text)
    config, code, message = load_config(str(path))
    assert config is not None, message
    return config, directory


def checklist(*cases):
    """只登记 test 类的清单。"""
    entries = [xfail.Entry("test", case, "", 0) for case in cases]
    return xfail.Checklist(xfail.Registry("", "", entries))


def write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def load_stage(name):
    """按路径加载阶段脚本。

    阶段脚本不在包里：写成 import 语句会被判成引用本项目之外的模块，因此
    用加载器按文件路径载入。Python 3.4 没有 module_from_spec 时退回旧接口。
    """
    path = str(Path(TOOL_ROOT) / "stages" / (name + ".py"))
    loader = importlib.machinery.SourceFileLoader("ci_stage_" + name, path)
    if hasattr(importlib.util, "module_from_spec"):
        spec = importlib.util.spec_from_file_location(
            "ci_stage_" + name, path, loader=loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return loader.load_module()


class TestBuildInjection:
    """qmake 与 cmake 的注入形式不同，但都要让 asan 库排在链接参数尾部。"""

    def test_qmake_puts_the_link_flag_at_the_tail(self):
        config, root = configured()
        argv = asan.configure_command(config, "qmake",
                                      config.test_build_file, "debug")
        assert "QMAKE_CXXFLAGS+=-fsanitize=address -fno-omit-frame-pointer" \
            == argv[-2]
        assert "LIBS+=-fsanitize=address" == argv[-1]
        # 链接项必须是命令行最后一项：qmake 按赋值顺序追加，asan 运行库才会
        # 落在 LIBS 尾部，排前面会被别的库抢先解析
        assert argv.index("LIBS+=-fsanitize=address") == len(argv) - 1

    def test_qmake_joins_all_link_flags_into_one_item(self):
        config, root = configured(
            'ASAN_LDFLAGS = ["-fsanitize=address", "-lasan"]\n')
        argv = asan.configure_command(config, "qmake",
                                      config.test_build_file, "debug")
        assert "LIBS+=-fsanitize=address -lasan" == argv[-1]

    def test_cmake_uses_the_equivalent_define_variables(self):
        config, root = configured(tool="cmake")
        argv = asan.configure_command(config, "cmake",
                                      config.test_build_file, "debug")
        assert "-DCMAKE_CXX_FLAGS=-fsanitize=address -fno-omit-frame-pointer" \
            in argv
        assert "-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address" in argv


class TestChannelEnvironment:
    def test_error_channel_preloads_libasan_and_disables_leaks(self):
        config, root = configured()
        library = os.path.join(root, "libasan.so")
        env = asan.environment(config, asan.CHANNEL_ERROR, library)
        assert library == env["LD_PRELOAD"].split()[0]
        assert "detect_leaks=0" in env["ASAN_OPTIONS"]
        # 通道 A 不做收尾泄漏扫描，就不该看到抑制清单
        assert "LSAN_OPTIONS" not in env

    def test_leak_channel_enables_leaks_and_points_at_suppressions(self):
        config, root = configured('ASAN_SUPPRESSIONS = ROOT / "lsan.supp"\n')
        env = asan.environment(config, asan.CHANNEL_LEAK, "/usr/lib/libasan.so")
        assert "detect_leaks=1" in env["ASAN_OPTIONS"]
        assert "suppressions=" + os.path.join(root, "lsan.supp") \
            == env["LSAN_OPTIONS"]

    def test_existing_preload_entry_is_kept_behind_libasan(self):
        """环境注入库不能丢：丢掉它，运行环境就与平时不一致了。"""
        config, root = configured(
            'TEST_ENV = {"LD_PRELOAD": "/opt/inject.so"}\n')
        env = asan.environment(config, asan.CHANNEL_ERROR, "/usr/lib/libasan.so")
        assert ["/usr/lib/libasan.so", "/opt/inject.so"] \
            == env["LD_PRELOAD"].split()[:2]

    def test_detect_leaks_is_replaced_instead_of_duplicated(self):
        config, root = configured(
            'TEST_ENV = {"ASAN_OPTIONS": "detect_leaks=0:abort_on_error=1"}\n')
        env = asan.environment(config, asan.CHANNEL_LEAK, "/usr/lib/libasan.so")
        assert "detect_leaks=1" in env["ASAN_OPTIONS"]
        assert "detect_leaks=0" not in env["ASAN_OPTIONS"]
        assert "abort_on_error=1" in env["ASAN_OPTIONS"]


class TestCompilerCandidates:
    """CXX_COMPILER 留空是"由构建工具决定"，不是"没有编译器"。

    实测：留空时拿空串去跑 `-print-file-name=libasan.so`，报出来是
    "找不到 ASan 运行库（ -print-file-name=libasan.so）"——括号里那个空位
    几乎没人读得出来是"我没写编译器"，而机器上 libasan 其实装着。
    """

    def test_empty_compiler_yields_candidates(self):
        config, _root = configured()
        config.cxx_compiler = ""
        found = asan.compiler_candidates(config)
        assert found
        assert "c++" in found
        assert "" not in found

    def test_explicit_compiler_comes_first(self):
        config, _root = configured()
        config.cxx_compiler = "g++-15"
        assert "g++-15" == asan.compiler_candidates(config)[0]

    def test_runtime_library_reports_what_it_tried(self):
        config, _root = configured()
        config.cxx_compiler = ""
        library, tried = asan.find_runtime_library(config)
        assert tried and all("-print-file-name=" in item for item in tried)
        if not library:
            # 机器上确实没有 asan 时，至少要能看到试过哪些完整命令
            assert len(tried) == len(asan.compiler_candidates(config))


class TestShadowCommandFromTestTree:
    """测试构建目录与主构建目录分开时，影子指令要落在影子树上。

    qmake 工程必须把两者分开（否则测试工程按相对路径引用主构建的静态库会
    解析错）。只认 BUILD_DIR 的推导会指向**普通测试树**——那棵树在 clean 之后
    是空的，报出来是"No rule to make target"，与代码问题毫无关系。
    """

    def test_separate_test_tree_is_retargeted(self):
        config, _root = configured()
        config.test_build_dir = os.path.join(os.path.dirname(config.build_dir),
                                            "tests")
        config.memcheck_command = [os.path.join(config.test_build_dir,
                                                "run_tests"), "--brief"]
        line = asan.command(config)
        shadow = asan.build_dir(config)
        assert shadow in line[0]
        assert config.test_build_dir not in line[0]
        assert "/tests/" not in line[0] and line[0].endswith("run_tests")
        assert ["--brief"] == line[1:]

    def test_underivable_command_is_reported(self):
        """推导不出来就不给指令，让调用方点名报错，不静默沿用原指令。"""
        config, _root = configured()
        config.memcheck_command = ["/somewhere/else/run_tests"]
        assert [] == asan.command(config)


class TestSuppressionsRequired:
    def test_empty_configuration_is_reported(self):
        config, root = configured()
        path, code, message = asan.require_suppressions(config)
        assert errors.ASAN_SUPPRESSIONS_MISSING == code

    def test_nonexistent_file_is_reported(self):
        config, root = configured('ASAN_SUPPRESSIONS = ROOT / "gone.supp"\n')
        path, code, message = asan.require_suppressions(config)
        assert errors.ASAN_SUPPRESSIONS_MISSING == code
        assert "不存在" in message

    def test_existing_file_passes(self):
        config, root = configured('ASAN_SUPPRESSIONS = ROOT / "lsan.supp"\n')
        path = write(os.path.join(root, "lsan.supp"), "leak:QObject\n")
        assert path == asan.require_suppressions(config)[0]
        assert errors.OK == asan.require_suppressions(config)[1]

    def test_stage_stops_before_building_or_running(self, monkeypatch):
        config, root = configured('MEMCHECK_TOOL = "asan"\n')
        os.makedirs(config.test_workdir)
        calls = []
        monkeypatch.setattr(
            asan, "shadow_build",
            lambda *a, **k: calls.append("build") or ("", errors.OK))
        monkeypatch.setattr(
            asan, "run_channel",
            lambda *a, **k: calls.append("run") or (0, "", errors.OK, ""))
        assert errors.ASAN_SUPPRESSIONS_MISSING == asan.run(config, Args())
        assert [] == calls


class TestRuntimeLibrary:
    def test_absolute_existing_library_is_returned(self, monkeypatch):
        config, root = configured()
        library = write(os.path.join(root, "libasan.so"), "")
        monkeypatch.setattr(asan.tools, "capture",
                            lambda *a, **k: (0, library.encode("utf-8"), b""))
        assert library == asan.runtime_library("g++")

    def test_compiler_echoing_the_name_back_is_not_trusted(self, monkeypatch):
        """找不到时编译器会把文件名原样打回，那不是可用路径。"""
        monkeypatch.setattr(asan.tools, "capture",
                            lambda *a, **k: (0, b"libasan.so\n", b""))
        assert "" == asan.runtime_library("g++")

    def test_missing_runtime_marks_the_stage_unavailable(self, monkeypatch):
        config, root = configured('MEMCHECK_TOOL = "asan"\n'
                                  'ASAN_SUPPRESSIONS = ROOT / "lsan.supp"\n')
        os.makedirs(config.test_workdir)
        write(os.path.join(root, "lsan.supp"), "leak:QObject\n")
        calls = []
        monkeypatch.setattr(asan, "runtime_library", lambda compiler: "")
        monkeypatch.setattr(
            asan, "shadow_build",
            lambda *a, **k: calls.append("build") or ("", errors.OK))
        assert errors.ASAN_TOOL_MISSING == asan.run(config, Args())
        assert [] == calls


class TestMemoryFingerprintBeatsXfail:
    """指纹优先于登记：业务失败可豁免，内存错误永远不可。"""

    def judge(self, text, *cases, **kwargs):
        config, root = configured(REGEX_CONFIG)
        status = kwargs.get("status", 1)
        return asan.judge(config, checklist(*cases), text, status)

    def test_registered_case_with_fingerprint_still_fails(self):
        text = QT_FAIL.format("Case::broken()") + ASAN_REPORT
        result = self.judge(text, "Case::broken()")
        assert errors.MEMCHECK_FAILED == result.code
        assert "ERROR: AddressSanitizer" in result.marks
        # 该用例确实命中了清单，但指纹优先，仍判失败
        assert ["Case::broken()"] == [item.case for item in result.known]

    def test_registered_business_failure_passes(self):
        text = QT_FAIL.format("Case::broken()") + QT_TOTALS
        result = self.judge(text, "Case::broken()")
        assert errors.OK == result.code
        assert [] == result.failures
        assert ["Case::broken()"] == [item.case for item in result.known]

    def test_fingerprint_without_per_case_result_fails(self):
        result = self.judge(ASAN_REPORT)
        assert errors.MEMCHECK_FAILED == result.code
        assert result.marks

    def test_nonzero_exit_without_results_fails(self):
        """没有逐例结果就无法归属，不能当已登记放过。"""
        result = self.judge("clean run, nothing to parse\n", status=2)
        assert errors.MEMCHECK_FAILED == result.code


class TestFailuresMustBeASubset:
    def test_unregistered_failure_is_reported(self):
        config, root = configured(REGEX_CONFIG)
        text = (QT_FAIL.format("Case::known()")
                + QT_FAIL.format("Case::fresh()") + QT_TOTALS)
        result = asan.judge(config, checklist("Case::known()"), text, 1)
        assert errors.MEMCHECK_FAILED == result.code
        assert ["Case::fresh()"] == [item.case for item in result.failures]
        assert ["Case::known()"] == [item.case for item in result.known]

    def test_every_registered_failure_passes(self):
        config, root = configured(REGEX_CONFIG)
        text = (QT_FAIL.format("Case::one()") + QT_FAIL.format("Case::two()")
                + QT_TOTALS)
        result = asan.judge(config, checklist("Case::one()", "Case::two()"),
                            text, 1)
        assert errors.OK == result.code
        assert [] == result.failures


class TestShadowLayout:
    def test_shadow_dir_defaults_under_build_dir(self):
        config, root = configured()
        assert os.path.join(config.build_dir, "tests_asan") \
            == asan.build_dir(config)

    def test_explicit_shadow_dir_wins(self):
        config, root = configured('ASAN_BUILD_DIR = ROOT / "build" / "asan2"\n')
        assert os.path.join(root, "build", "asan2") == asan.build_dir(config)

    def test_command_defaults_to_the_retargeted_test_command(self):
        config, root = configured()
        # 换掉的是 BUILD_DIR 前缀而不是 TEST_BUILD_DIR：TEST_BUILD_DIR 位于
        # BUILD_DIR 之下，于是整棵测试子树搬到影子目录里
        expected = [os.path.join(config.build_dir, "tests_asan", "tests",
                                 "run_tests"), "--brief"]
        assert expected == asan.command(config)

    def test_explicit_command_wins(self):
        config, root = configured(
            'ASAN_COMMAND = [ROOT / "probe", "--leak-check"]\n')
        assert [os.path.join(root, "probe"), "--leak-check"] \
            == asan.command(config)

    def test_retarget_leaves_sibling_paths_alone(self):
        """BUILD_DIR 是另一个目录名的前半段时不能误改。"""
        assert ["/out/asan2/run_tests"] == asan.retarget(
            ["/out/asan2/run_tests"], "/out/asan", "/out/asan/asan")

    def test_leak_channel_appends_its_extra_arguments(self):
        config, root = configured('ASAN_LEAK_ARGS = ["--no-gui"]\n')
        binary = os.path.join(config.build_dir, "tests_asan", "tests",
                              "run_tests")
        assert [binary, "--no-gui", "--brief"] == asan.channel_command(
            config, asan.CHANNEL_LEAK)
        # 通道 A 跑全量用例，不带泄漏通道的参数
        assert [binary, "--brief"] == asan.channel_command(
            config, asan.CHANNEL_ERROR)


class TestShippedConfig:
    """仓库自带的配置模板必须给出可用的 asan 段。"""

    def template(self):
        config, code, message = load_config(
            str(example_path()))
        assert config is not None, message
        return config

    def test_asan_keys_load_with_their_defaults(self):
        config = self.template()
        assert "valgrind" == config.memcheck_tool
        # 两项都留空：影子目录与测试指令都从现有配置推导
        assert not config.asan_build_dir
        assert config.asan_command is None

    def test_template_marks_cover_the_builtin_set(self):
        """示例是接入的起点，不能悄悄少列指纹，否则照抄就漏检。"""
        config = self.template()
        for mark in DEFAULTS["ASAN_ERROR_MARKS"]:
            assert mark in config.asan_error_marks


class TestStageDispatch:
    def routes(self, monkeypatch, tool):
        memcheck = load_stage("memcheck")
        calls = []
        monkeypatch.setattr(
            memcheck, "valgrind_run",
            lambda config, args: calls.append("valgrind") or errors.OK)
        monkeypatch.setattr(
            memcheck.asan, "run",
            lambda config, args: calls.append("asan") or errors.OK)
        config, root = configured(
            'MEMCHECK_TOOL = "{}"\n'.format(tool))
        return memcheck.run(config, Args()), calls

    def test_valgrind_tool_keeps_the_existing_route(self, monkeypatch):
        assert (errors.OK, ["valgrind"]) == self.routes(monkeypatch, "valgrind")

    def test_asan_tool_routes_to_the_asan_module(self, monkeypatch):
        assert (errors.OK, ["asan"]) == self.routes(monkeypatch, "asan")
