#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置元数据的一致性测试。

元数据是唯一事实源：example 与 --help-config 都由它生成。这里把三者钉在一起
——新增配置项却没登记元数据、或手改了 example 却没改元数据，测试当场变红，
文档漂移因此不可能发生。
"""

import os
import tempfile
from pathlib import Path

from lib import config as config_module


def write_config(text):
    """把一段配置文本写进临时目录，返回文件路径。"""
    directory = tempfile.mkdtemp(prefix="cxx_ci_meta_")
    path = Path(directory) / "config.py"
    with open(str(path), "w", encoding="utf-8") as handle:
        handle.write(text)
    return str(path)


def load_from_text(text):
    return config_module.load_config(write_config(text))


class TestMetadataCoversEveryKey:
    def test_metadata_keys_match_the_writable_keys(self):
        """登记了元数据的键 == 可写的配置键；少一项即测试红。"""
        assert set(config_module.CONFIG_METADATA) == config_module.config_keys()

    def test_every_key_is_listed_once_in_sections(self):
        order = config_module.ordered_keys()
        assert len(order) == len(set(order))
        assert set(order) == config_module.config_keys()

    def test_every_entry_is_complete(self):
        for name, item in config_module.CONFIG_METADATA.items():
            assert item["level"] in config_module.LEVELS, name
            for field in ("summary", "derive", "when"):
                assert item[field].strip(), (name, field)


class TestExampleIsGenerated:
    """磁盘上的 example 必须逐字等于生成结果。"""

    def disk_text(self):
        handle = open(str(config_module.example_path()), "r", encoding="utf-8")
        try:
            return handle.read()
        finally:
            handle.close()

    def test_example_on_disk_equals_the_generated_text(self):
        assert self.disk_text() == config_module.render_example()

    def test_generated_example_loads(self):
        """生成物必须是能直接用的合法配置，而不是只看得懂的文档。"""
        text = config_module.render_example()
        assert "CONTRACT_VERSION = {}".format(
            config_module.CONTRACT_VERSION) in text
        config, _code, message = load_from_text(text)
        assert config is not None, message
        assert config.build_file == os.path.join(str(config.root),
                                                 "CMakeLists.txt")
        assert config.test_command


class TestHelpConfig:
    """--help-config 与元数据同源，三层都要有，且每项都出现。"""

    def test_three_layers_and_every_key_are_present(self):
        text = config_module.help_config_text()
        for level in config_module.LEVELS:
            assert "== {} ==".format(level) in text
        for name in config_module.config_keys():
            assert "\n" + name + "\n" in text, name


BASE = """\
from pathlib import Path

ROOT = Path({root!r})
BUILD_TOOL = "cmake"
BUILD_FILE = ROOT / "CMakeLists.txt"
BUILD_DIR = ROOT / "build"
SCOPE = {{"project": [ROOT / "src"], "excluded": [{excluded}]}}
TEST_COMMAND = ["ctest"]
"""


class TestAdvise:
    """高危项就地提醒：只提醒、不判红，且必须"风险确实存在"才出声。"""

    def make(self, excluded='ROOT / "build"', extra="", build_dir=True):
        directory = tempfile.mkdtemp(prefix="cxx_ci_advise_")
        root = Path(directory)
        if build_dir:
            (root / "build").mkdir()
        text = BASE.format(root=str(root), excluded=excluded) + extra
        config, code, message = config_module.load_config(write_config(text))
        assert config is not None, message
        return config

    def test_build_dir_outside_scope_is_flagged(self):
        config = self.make(excluded="")
        notes = config_module.advise(config)
        assert any("SCOPE.excluded" in note for note in notes)

    def test_build_dir_inside_scope_is_quiet(self):
        config = self.make()
        assert [] == [note for note in config_module.advise(config)
                      if "SCOPE.excluded" in note]

    def test_missing_build_dir_is_not_flagged(self):
        """目录都还没生成时谈不上"构建产物被当成项目代码"。"""
        config = self.make(excluded="", build_dir=False)
        assert [] == [note for note in config_module.advise(config)
                      if "SCOPE.excluded" in note]

    def test_all_scope_without_ignore_sets_is_flagged(self):
        config = self.make(extra='MEMCHECK_SCOPE = "all"\n')
        assert any("MEMCHECK_SCOPE=all" in note
                   for note in config_module.advise(config))

    def test_all_scope_with_ignore_symbols_is_quiet(self):
        config = self.make(extra='MEMCHECK_SCOPE = "all"\n'
                                 'MEMCHECK_IGNORE_SYMBOLS = ["Vendor::"]\n')
        assert [] == [note for note in config_module.advise(config)
                      if "MEMCHECK_SCOPE=all" in note]

    def test_driver_command_is_flagged(self):
        """MEMCHECK_COMMAND 回落到 ctest：valgrind 只会检查驱动本身。"""
        notes = config_module.advise(self.make())
        assert any("ctest" in note for note in notes)
