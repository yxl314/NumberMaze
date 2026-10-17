#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置加载的单元测试。"""

import os
import sys

import pytest
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lib import errors, paths
from lib import config as config_module
from lib.config import (BUILD_TOOLS, TOOL_ROOT,
                        load_config)
from ci_support import load


VALID = """\
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_TOOL = "qmake6"
BUILD_TOOL_BIN = ""
BUILD_FILE = ROOT / "project.pro"
BUILD_DIR = ROOT / "out"
CXX_COMPILER = "g++"
CXX_STANDARD = "c++98"
TEST_BUILD_FILE = ROOT / "test" / "test.pro"
TEST_BUILD_DIR = ROOT / "out" / "test"
TEST_COMMAND = [ROOT / "out" / "test" / "run_tests", "--brief"]
TEST_WORKDIR = ROOT / "out" / "test"
MEMCHECK_COMMAND = TEST_COMMAND
CLANG_FORMAT_CONFIG = ROOT / ".clang-format"
CLANG_TIDY_CONFIG = ROOT / ".clang-tidy"
CLANG_TIDY_EXTRA_ARGS = []
WHITELIST = [ROOT / "src", ROOT / "include"]
BLACKLIST = [ROOT / "third_party"]
MEMCHECK_SUPPRESSIONS = ROOT / "config" / "valgrind.supp"
DIAGNOSTIC_LEVEL = "error"
DIAGNOSTIC_SOURCES = "project"
DIAGNOSTIC_IGNORE = []
DIAGNOSTIC_DEDUP = True
LOG_DIR = ROOT / ".cxx_ci" / "logs"
"""


# 只有两项必需：项目根与构建工具。其余按构建工具探测或缺省
MINIMAL = """\
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD_TOOL = "cmake"
SCOPE = {"project": [ROOT / "src"], "excluded": [ROOT / "build"]}
"""


class TestDiscovery:
    """移植到新项目时先把必需项填上就能跑：其余能推的就推，推不出来才报错。"""

    def write(self, text, extra=()):
        directory = tempfile.mkdtemp(prefix="cxx_ci_disc_")
        for name in extra:
            with open(os.path.join(directory, name), "w") as handle:
                handle.write("")
        path = Path(directory) / "config.py"
        with open(str(path), "w") as handle:
            handle.write(text)
        return str(path), directory

    def test_build_file_is_discovered(self):
        path, root = self.write(MINIMAL, extra=("CMakeLists.txt",))
        config, code, message = load_config(path)
        assert config is not None, message
        assert os.path.join(root, "CMakeLists.txt") == config.build_file
        assert any("自动探测到构建文件" in item for item in config.warnings)

    def test_missing_build_file_is_reported_not_guessed(self):
        path, root = self.write(MINIMAL)
        config, code, message = load_config(path)
        assert config is None
        assert errors.CONFIG_MISSING_ITEM == code
        assert "BUILD_FILE" in message

    def test_build_dir_and_test_paths_fall_back_to_the_main_ones(self):
        path, root = self.write(MINIMAL, extra=("CMakeLists.txt",))
        config, code, message = load_config(path)
        assert config is not None, message
        assert os.path.join(root, "build") == config.build_dir
        assert config.build_file == config.test_build_file
        assert config.build_dir == config.test_build_dir

    def test_memcheck_command_falls_back_to_the_test_command(self):
        text = MINIMAL + 'TEST_COMMAND = ["ctest"]\n'
        path, root = self.write(text, extra=("CMakeLists.txt",))
        config, code, message = load_config(path)
        assert config is not None, message
        assert ["ctest"] == config.memcheck_command

    def test_qmake_project_file_is_discovered(self):
        text = MINIMAL.replace('"cmake"', '"qmake"')
        path, root = self.write(text, extra=("app.pro",))
        config, code, message = load_config(path)
        assert config is not None, message
        assert os.path.join(root, "app.pro") == config.build_file

    def test_explicit_values_win_over_discovery(self):
        text = MINIMAL + 'BUILD_FILE = ROOT / "custom" / "CMakeLists.txt"\n'
        path, root = self.write(text, extra=("CMakeLists.txt",))
        config, code, message = load_config(path)
        assert config is not None, message
        assert os.path.join(root, "custom", "CMakeLists.txt") == config.build_file
        assert [] == [item for item in config.warnings if "探测" in item]


class TestConfigLocation:
    """配置位置：显式路径 > 环境变量 > 工具自带。"""

    def test_argument_wins(self):
        assert "/tmp/a.py" == str(config_module.config_path("/tmp/a.py"))

    def test_environment_variable_is_used(self):
        old = os.environ.get(config_module.CONFIG_ENV)
        os.environ[config_module.CONFIG_ENV] = "/tmp/from-env.py"
        try:
            assert "/tmp/from-env.py" == str(config_module.config_path())
        finally:
            if old is None:
                del os.environ[config_module.CONFIG_ENV]
            else:
                os.environ[config_module.CONFIG_ENV] = old

    def test_tool_default_is_the_last_resort(self):
        old = os.environ.pop(config_module.CONFIG_ENV, None)
        try:
            assert config_module.CONFIG_FILE == config_module.config_path()
        finally:
            if old is not None:
                os.environ[config_module.CONFIG_ENV] = old


class TestLoadConfig:
    def write(self, text):
        directory = tempfile.mkdtemp(prefix="cxx_ci_cfg_")
        path = Path(directory) / "config.py"
        with open(str(path), "w") as handle:
            handle.write(text)
        return str(path), str(Path(directory).resolve().parent)

    def test_reads_all_items(self):
        path, root = self.write(VALID)
        config = load(path)
        assert "qmake6" == config.build_tool
        assert os.path.join(root, "project.pro") == config.build_file
        assert os.path.join(root, "out") == config.build_dir
        assert os.path.join(root, 'test', 'test.pro') == config.test_build_file
        assert os.path.join(root, "out", "test") == config.test_build_dir
        assert [os.path.join(root, 'out', 'test', 'run_tests'), '--brief'] == config.test_command
        assert [os.path.join(root, 'src'), os.path.join(root, 'include')] == config.whitelist
        assert [os.path.join(root, "third_party")] == config.blacklist
    def test_every_path_is_absolute(self):
        path, root = self.write(VALID)
        config = load(path)
        for value in ([config.build_file, config.build_dir,
                       config.test_build_file, config.test_build_dir]
                      + config.whitelist + config.blacklist):
            assert os.path.isabs(value), value
    def test_missing_file_raises(self):
        config, code, message = load_config("/nonexistent/config.py")
        assert config is None
        assert errors.CONFIG_NOT_FOUND == code

    def test_syntax_error_reports_config_error(self):
        path, root = self.write("(\n")
        config, code, message = load_config(path)
        assert config is None
        assert errors.CONFIG_UNLOADABLE == code

    def test_rejects_relative_path(self):
        # BUILD_DIR 变相对路径后，依赖它的 LOG_DIR 也要一并换掉，
        # 否则模块执行本身就会出错，测不到路径校验
        broken = VALID.replace('\nBUILD_DIR = ROOT / "out"\n',
                               '\nBUILD_DIR = "out"\n')
        broken = broken.replace('LOG_DIR = ROOT / ".cxx_ci" / "logs"',
                                'LOG_DIR = "logs"')
        path, root = self.write(broken)
        config, code, message = load_config(path)
        assert config is None
        assert errors.CONFIG_BAD_VALUE == code

    def test_rejects_unknown_tool(self):
        path, root = self.write(VALID.replace("qmake6", "make"))
        config, code, message = load_config(path)
        assert config is None
        assert errors.CONFIG_BAD_VALUE == code

    def test_rejects_missing_root_and_build_tool(self):
        """只有"猜不出来"的两项是必需：项目根与构建工具。"""
        for text, name in (
                ('BUILD_TOOL = "cmake"\n', "ROOT"),
                ('from pathlib import Path\nROOT = Path("/tmp")\n',
                 "BUILD_TOOL")):
            path, root = self.write(text)
            config, code, message = load_config(path)
            assert config is None
            assert errors.CONFIG_MISSING_ITEM == code, (name, message)
            assert name in message

    def test_test_keys_may_be_absent(self):
        """没有测试的工程不必填测试项：缺了由阶段自己说明。"""
        lines = [line for line in VALID.splitlines(True)
                 if not line.startswith(("TEST_", "MEMCHECK_COMMAND"))]
        path, root = self.write("".join(lines))
        config, code, message = load_config(path)
        assert config is not None, message
        assert [] == config.test_command
        assert [] == config.memcheck_command
        assert "" == config.test_workdir

    def test_rejects_cmakelists_for_qmake(self):
        path, root = self.write(
            VALID.replace('BUILD_FILE = ROOT / "project.pro"',
                          'BUILD_FILE = ROOT / "CMakeLists.txt"'))
        config, code, message = load_config(path)
        assert config is None
        assert errors.CONFIG_BAD_VALUE == code

    def test_rejects_pro_file_for_cmake(self):
        path, root = self.write(
            VALID.replace("qmake6", "cmake"))
        config, code, message = load_config(path)
        assert config is None
        assert errors.CONFIG_BAD_VALUE == code

    def test_accepts_cmakelists_for_cmake(self):
        path, root = self.write(
            VALID.replace("qmake6", "cmake")
                 .replace('ROOT / "project.pro"', 'ROOT / "CMakeLists.txt"')
                 .replace('ROOT / "test" / "test.pro"',
                          'ROOT / "test" / "CMakeLists.txt"'))
        config = load(path)
        assert config.build_file.endswith("CMakeLists.txt")
    def test_empty_command_means_not_configured(self):
        """空列表与没写是同一件事：这个阶段没配。"""
        path, root = self.write(
            VALID.replace(
                'TEST_COMMAND = [ROOT / "out" / "test" / "run_tests", "--brief"]',
                "TEST_COMMAND = []"))
        config, code, message = load_config(path)
        assert config is not None, message
        assert [] == config.test_command

    def test_rejects_command_with_empty_item(self):
        path, root = self.write(
            VALID.replace(
                'TEST_COMMAND = [ROOT / "out" / "test" / "run_tests", "--brief"]',
                'TEST_COMMAND = [ROOT / "out" / "test" / "run_tests", ""]'))
        config, code, message = load_config(path)
        assert config is None
        assert errors.CONFIG_BAD_VALUE == code

    def test_rejects_non_list_command(self):
        path, root = self.write(
            VALID.replace(
                'TEST_COMMAND = [ROOT / "out" / "test" / "run_tests", "--brief"]',
                'TEST_COMMAND = "run_tests"'))
        config, code, message = load_config(path)
        assert config is None
        assert errors.CONFIG_BAD_VALUE == code

    def test_empty_whitelist_yields_empty_list(self):
        path, root = self.write(
            VALID.replace('WHITELIST = [ROOT / "src", ROOT / "include"]',
                          "WHITELIST = []"))
        assert [] == load(path).whitelist
    def test_reads_toolchain_and_tool_choices(self):
        path, root = self.write(VALID)
        config = load(path)
        assert "g++" == config.cxx_compiler
        assert "c++98" == config.cxx_standard
        assert "" == config.build_tool_bin
        assert os.path.join(root, 'config', 'valgrind.supp') == config.memcheck_suppressions
        assert os.path.join(root, "out", "test") == config.test_workdir
    def test_empty_compiler_and_standard_mean_let_the_tool_decide(self):
        """留空表示不传 -std= / 不指定编译器，由构建工具自己的缺省说了算。"""
        for old, new in (('CXX_COMPILER = "g++"', 'CXX_COMPILER = ""'),
                         ('CXX_STANDARD = "c++98"', 'CXX_STANDARD = ""')):
            path, root = self.write(VALID.replace(old, new))
            config, code, message = load_config(path)
            assert config is not None, message
            assert "" == (config.cxx_compiler if "COMPILER" in old
                          else config.cxx_standard)

    def test_rejects_relative_test_workdir(self):
        path, root = self.write(
              VALID.replace('TEST_WORKDIR = ROOT / "out" / "test"',
                            'TEST_WORKDIR = "out"'))
        config, code, message = load_config(path)
        assert config is None
        assert errors.CONFIG_BAD_VALUE == code

    def test_reload_picks_up_changes(self):
        """同一路径改动后必须重新生效，不能被模块缓存挡住。"""
        path, root = self.write(VALID)
        assert "qmake6" == load(path).build_tool
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(VALID.replace("qmake6", "qmake"))
        assert "qmake" == load(path).build_tool
class TestResolvePath:
    def test_relative_resolves_against_given_root(self):
        assert "/root/src" == paths.resolve_path("src", "/root")
    def test_absolute_is_kept(self):
        assert "/tmp/abs" == paths.resolve_path("/tmp/abs", "/root")
# 仓库自带的配置模板，测试直接用它，不依赖本地 config.py
TEMPLATE = config_module.example_path()


class TestShippedConfig:
    """仓库自带的配置模板必须可用。

    模板是版本管理的一部分，本地 config.py 是个人的、易变的，
    测试不应依赖它是否存在。
    """

    def test_template_is_valid(self):
        """模板必须能加载，且路径都落在项目根内。"""
        config = load(str(TEMPLATE))
        assert config.build_tool in BUILD_TOOLS
        assert config.test_command
        # 抑制文件已改为可选精修，未配置时为空串
        if config.memcheck_suppressions:
            assert os.path.isabs(config.memcheck_suppressions)
        for value in ([config.root, config.build_file, config.build_dir,
                       config.test_build_file, config.test_build_dir,
                       config.test_workdir, config.log_dir]
                      + config.scope["project"] + config.scope["excluded"]):
            assert os.path.isabs(value), value
            assert value.startswith(config.root), value

    def test_project_root_is_not_the_tool(self):
        """项目根不能是 cxx_ci 自身。

        工具既可与项目同级，也可放在项目内部，两种布局都允许。
        """
        config = load(str(TEMPLATE))
        assert TOOL_ROOT != config.root


class TestPathLikeItems:
    """同为"路径列表"的配置项，Path 与 str 必须等价。

    某一项只认 str 时，使用者要记住哪个是特例，零文件接入的体验就破了。
    """

    def write(self, text):
        directory = tempfile.mkdtemp(prefix="cxx_ci_cfg_")
        path = Path(directory) / "config.py"
        with open(str(path), "w") as handle:
            handle.write(text)
        return str(path), str(Path(directory).resolve().parent)

    def append(self, extra):
        return VALID + extra + "\n"

    def test_skip_targets_accepts_path(self):
        path, root = self.write(self.append(
            'MEMCHECK_SKIP_TARGETS = [ROOT / "out" / "test" / "run_tests"]'))
        config = load(path)
        assert [os.path.join(root, "out", "test", "run_tests")] == \
            config.memcheck_skip_targets

    def test_skip_targets_accepts_string(self):
        path, root = self.write(self.append(
            'MEMCHECK_SKIP_TARGETS = ["run_tests"]'))
        config = load(path)
        assert ["run_tests"] == config.memcheck_skip_targets

    def test_ignore_objects_accepts_both_forms(self):
        path, root = self.write(self.append(
            'MEMCHECK_IGNORE_OBJECTS = [ROOT / "out" / "libvendor.so", '
            '"/opt/vendor/libother.so"]'))
        config = load(path)
        assert [os.path.join(root, "out", "libvendor.so"),
                "/opt/vendor/libother.so"] == config.memcheck_ignore_objects

    def test_other_types_are_rejected(self):
        path, root = self.write(self.append(
            'MEMCHECK_SKIP_TARGETS = [123]'))
        config, code, message = load_config(path)
        assert config is None
        assert errors.CONFIG_BAD_VALUE == code
        assert "路径或字符串" in message


class TestXfailConfig:
    def write(self, text):
        directory = tempfile.mkdtemp(prefix="cxx_ci_cfg_")
        path = Path(directory) / "config.py"
        with open(str(path), "w") as handle:
            handle.write(text)
        return str(path), str(Path(directory).resolve().parent)

    def test_defaults_point_at_project_root(self):
        path, root = self.write(VALID)
        config = load(path)
        assert os.path.join(root, "ci.xfail") == config.xfail_file
        assert not config.xfail_strict

    def test_explicit_file_and_strict(self):
        path, root = self.write(VALID + '\nXFAIL_FILE = ROOT / "cfg" / "xf"\n'
                                       'XFAIL_STRICT = True\n')
        config = load(path)
        assert os.path.join(root, "cfg", "xf") == config.xfail_file
        assert config.xfail_strict

    def test_empty_or_none_falls_back_to_the_default(self):
        """空值表示"用缺省位置"：清单文件不存在本身就等于没有清单。"""
        for text in ('\nXFAIL_FILE = ""\n', '\nXFAIL_FILE = None\n'):
            path, root = self.write(VALID + text)
            config = load(path)
            assert os.path.join(root, "ci.xfail") == config.xfail_file

    def test_relative_file_is_rejected(self):
        path, root = self.write(VALID + '\nXFAIL_FILE = "ci.xfail"\n')
        config, code, message = load_config(path)
        assert config is None
        assert errors.CONFIG_BAD_VALUE == code

    def test_strict_must_be_bool(self):
        path, root = self.write(VALID + '\nXFAIL_STRICT = "yes"\n')
        config, code, message = load_config(path)
        assert config is None
        assert errors.CONFIG_BAD_VALUE == code


class TestIgnoreSymbolsConfig:
    def write(self, text):
        directory = tempfile.mkdtemp(prefix="cxx_ci_cfg_")
        path = Path(directory) / "config.py"
        with open(str(path), "w") as handle:
            handle.write(text)
        return str(path), str(Path(directory).resolve().parent)

    def test_default_is_empty(self):
        path, root = self.write(VALID)
        assert [] == load(path).memcheck_ignore_symbols

    def test_symbols_are_read(self):
        path, root = self.write(VALID + '\nMEMCHECK_IGNORE_SYMBOLS = '
                                       '["NormalButton::", "QMetaObject::"]\n')
        assert ["NormalButton::", "QMetaObject::"] == \
            load(path).memcheck_ignore_symbols


class TestContractVersion:
    """跨项目契约版本：由工具实现声明，项目配置可选覆盖，编排脚本比对。"""

    def write(self, text):
        directory = tempfile.mkdtemp(prefix="cxx_ci_contract_")
        path = Path(directory) / "config.py"
        with open(str(path), "w") as handle:
            handle.write(text)
        return str(path)

    def read(self, text):
        module, code, _ = config_module.load_module(self.write(text))
        assert errors.OK == code
        return config_module.contract_version(module)

    def test_declared_version_is_read(self):
        assert 3 == self.read("CONTRACT_VERSION = 3\n")

    def test_missing_declaration_falls_back_to_the_tool_version(self):
        """配置不写就是"跟随工具实现"，不是"按 1 算"。"""
        assert config_module.CONTRACT_VERSION == self.read("ROOT = 1\n")

    def test_non_integer_declaration_is_not_trusted(self):
        assert (config_module.CONTRACT_VERSION
                == self.read("CONTRACT_VERSION = 'v1'\n"))

    def test_example_config_may_declare_the_contract_version(self):
        """示例配置可以声明它（钉住自己写的契约），也可以不写。

        钉住的是"示例声明的版本必须与工具实现当前版本一致"——示例是接入时照抄的
        模板，它落后于实现等于把新工程直接引到旧契约上。
        """
        module, code, _ = config_module.load_module(
            config_module.example_path())
        assert errors.OK == code
        assert config_module.CONTRACT_VERSION == config_module.contract_version(
            module)

    def test_check_contract_rejects_a_foreign_generation(self):
        """配置声明的契约版本与工具不一致：拒绝，投影成环境类（退出码 2）。"""
        module, code, _ = config_module.load_module(
            self.write("CONTRACT_VERSION = 1\n"))
        assert errors.OK == code
        code, message = config_module.check_contract(module)
        assert errors.CONTRACT_MISMATCH == code
        assert errors.KIND_ENVIRONMENT == errors.kind(code)
        assert "契约版本" in message

    def test_check_contract_passes_when_matching_or_absent(self):
        """声明一致、或压根没写，才静默通过。"""
        for text in ("CONTRACT_VERSION = 3\n", "ROOT = 1\n"):
            module, code, _ = config_module.load_module(self.write(text))
            assert errors.OK == code, text
            assert errors.OK == config_module.check_contract(module)[0], text

    def test_a_declaration_of_the_wrong_type_is_reported(self):
        """写成 '2' 或 2.0 不能被当成"没写"。

        契约写的是"一旦写了就必须与本工具实现一致"。当成没写静默放行时，
        配置和工具取自不同代却一路跑到底，报出来的还是别的问题。
        """
        for text in ("CONTRACT_VERSION = 'v1'\n", "CONTRACT_VERSION = 2.0\n",
                     "CONTRACT_VERSION = '2'\n", "CONTRACT_VERSION = True\n"):
            module, code, _ = config_module.load_module(self.write(text))
            assert errors.OK == code, text
            assert errors.CONTRACT_MISMATCH == \
                config_module.check_contract(module)[0], text

    def test_load_config_refuses_a_mismatched_contract(self):
        """加载配置时就把关，不必等编排脚本比对。"""
        text = ("from pathlib import Path\n"
                "ROOT = Path(__file__).resolve().parent\n"
                'BUILD_TOOL = "cmake"\n'
                "CONTRACT_VERSION = 1\n")
        config, code, message = config_module.load_config(self.write(text))
        assert config is None
        assert errors.CONTRACT_MISMATCH == code
        assert "契约版本" in message


class TestBadConfigValuesGiveDiagnosticsNotTracebacks:
    """配置写错要给出"哪一项、该写成什么样"，不能是 Python 栈。

    路径项以前直接进 pathlib：写个整数就抛 TypeError。使用者拿到的是一段栈，
    既不知道是哪一项，也不知道该怎么改。
    """

    def _load(self, tmp_path, name, value):
        config = tmp_path / "config.py"
        handle = open(str(config), "w", encoding="utf-8")
        try:
            handle.write(
                "from pathlib import Path\n"
                "ROOT = Path({!r})\n"
                "BUILD_TOOL = \"cmake\"\n"
                "BUILD_FILE = ROOT / \"CMakeLists.txt\"\n"
                "BUILD_DIR = ROOT / \"build\"\n"
                "SCOPE = {{\"project\": [ROOT], \"excluded\": []}}\n"
                "{} = {}\n".format(str(tmp_path), name, value))
        finally:
            handle.close()
        return config_module.load_config(str(config))

    @pytest.mark.parametrize("name", ["BUILD_DIR", "TEST_BUILD_DIR",
                                      "TEST_BUILD_FILE", "TEST_WORKDIR",
                                      "BUILD_FILE", "CI_LOG_DIR"])
    def test_non_path_value_is_rejected(self, tmp_path, name):
        _config, code, message = self._load(tmp_path, name, "12345")
        assert config_module.CONFIG_BAD_VALUE == code, (code, message)
        assert name in message, message
        assert "必须是路径" in message, message

    @pytest.mark.parametrize("name", ["FROZEN"])
    def test_non_list_value_is_rejected(self, tmp_path, name):
        _config, code, message = self._load(tmp_path, name, "12345")
        assert config_module.CONFIG_BAD_VALUE == code, (code, message)
        assert name in message, message

    def test_relative_path_in_a_list_is_rejected(self, tmp_path):
        _config, code, message = self._load(tmp_path, "FROZEN", '["src"]')
        assert config_module.CONFIG_BAD_VALUE == code, (code, message)
        assert "绝对路径" in message, message


class TestValidationHappensBeforeTheFailureCheck:
    """读取器记下的失败必须真的被报出来。

    失败检查放在函数中间时，它后面的校验（源码编码这类）记下的失败没人再看，
    配置写错却一路跑到缺省值上——最后表现为"这功能没反应"。
    """

    def _load(self, tmp_path, extra):
        config = tmp_path / "config.py"
        handle = open(str(config), "w", encoding="utf-8")
        try:
            handle.write(
                "from pathlib import Path\n"
                "ROOT = Path({!r})\n"
                "BUILD_TOOL = \"cmake\"\n"
                "BUILD_FILE = ROOT / \"CMakeLists.txt\"\n"
                "BUILD_DIR = ROOT / \"build\"\n"
                "SCOPE = {{\"project\": [ROOT], \"excluded\": []}}\n"
                "{}".format(str(tmp_path), extra))
        finally:
            handle.close()
        return config_module.load_config(str(config))

    @pytest.mark.parametrize("extra", [
        "SOURCE_ENCODING = [1, 2, 3]\n",
        "SOURCE_ENCODING_CANDIDATES = [1, 2, 3]\n",
        "SOURCE_ENCODING_CANDIDATES = 12345\n",
    ])
    def test_source_encoding_failures_are_reported(self, tmp_path, extra):
        _config, code, message = self._load(tmp_path, extra)
        assert config_module.CONFIG_BAD_VALUE == code, (code, message)
        assert "SOURCE_ENCODING" in message, message


class TestUnknownKeys:
    """写错的键名要出声。

    工具的审计能查"认识的键没生效"，却查不出"根本不认识"——使用者改了配置、
    工具照旧按缺省跑，谁也不知道那份配置没被读进去。
    """

    def _load(self, tmp_path, extra):
        config = tmp_path / "config.py"
        handle = open(str(config), "w", encoding="utf-8")
        try:
            handle.write(
                "from pathlib import Path\n"
                "ROOT = Path({!r})\n"
                "BUILD_TOOL = \"cmake\"\n"
                "BUILD_FILE = ROOT / \"CMakeLists.txt\"\n"
                "BUILD_DIR = ROOT / \"build\"\n"
                "SCOPE = {{\"project\": [ROOT], \"excluded\": []}}\n"
                "{}".format(str(tmp_path), extra))
        finally:
            handle.close()
        return config_module.load_config(str(config))

    def test_unknown_keys_are_recorded(self, tmp_path):
        config, code, _message = self._load(
            tmp_path, "LOG_DIR = ROOT / \"logs\"\nCLANG_TIDY_CONFIG = 1\n")
        assert config_module.OK == code
        assert "LOG_DIR" in config.unknown_keys
        assert "CLANG_TIDY_CONFIG" in config.unknown_keys
