"""排版基线指纹的单元测试。

基线要回答的问题只有一个："这次的排版结果该跟哪一次比"。工具版本、注释
模式、排版配置任一变化都会让结果不同，因此三者都必须进指纹，只记版本号
不够；记录读不出来、写不下去时也要给出各自的错误码，不能与"指纹变了"混为
一谈。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cxx_standardize import baseline
from cxx_standardize.errors import Code


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def record_of(text: str) -> dict:
    """指纹是记录的 JSON 文本，按字段查看时解析一次。"""
    return json.loads(text)


class TestDigest:
    def test_same_content_same_digest(self, tmp_path):
        first = tmp_path / "a.clang-format"
        second = tmp_path / "b.clang-format"
        write(first, "BasedOnStyle: LLVM\n")
        write(second, "BasedOnStyle: LLVM\n")
        assert baseline.config_digest(first) == baseline.config_digest(second)

    def test_digest_accepts_str_path(self, tmp_path):
        path = tmp_path / ".clang-format"
        write(path, "BasedOnStyle: LLVM\n")
        assert baseline.config_digest(path) == baseline.config_digest(str(path))

    def test_content_change_changes_digest(self, tmp_path):
        path = tmp_path / ".clang-format"
        write(path, "IndentWidth: 2\n")
        before = baseline.config_digest(path)
        write(path, "IndentWidth: 4\n")
        assert before != baseline.config_digest(path)

    def test_missing_file_is_marked_builtin(self):
        assert baseline.BUILTIN == baseline.config_digest(None)
        assert baseline.BUILTIN == baseline.config_digest("")
        assert baseline.BUILTIN == baseline.config_digest("/nonexistent/.clang-format")

    def test_directory_is_marked_builtin(self, tmp_path):
        assert baseline.BUILTIN == baseline.config_digest(tmp_path)


class TestFingerprint:
    def config(self, tmp_path) -> Path:
        path = tmp_path / ".clang-format"
        write(path, "BasedOnStyle: LLVM\n")
        return path

    def test_fingerprint_is_serialized_text(self, tmp_path):
        text = baseline.fingerprint("clang-format", "17.0.6", "placeholder",
                                    self.config(tmp_path))
        assert isinstance(text, str)
        parsed = record_of(text)
        assert parsed["tool"] == "clang-format"
        assert parsed["version"] == "17.0.6"
        assert parsed["comments"] == "placeholder"

    def test_version_change_changes_fingerprint(self, tmp_path):
        config = self.config(tmp_path)
        first = baseline.fingerprint(
            "clang-format", "clang-format version 17.0.6", "placeholder", config)
        second = baseline.fingerprint(
            "clang-format", "clang-format version 22.1.0", "placeholder", config)
        assert first != second
        assert record_of(first)["fingerprint"] != record_of(second)["fingerprint"]

    def test_comment_mode_change_changes_fingerprint(self, tmp_path):
        config = self.config(tmp_path)
        first = baseline.fingerprint(
            "clang-format", "17.0.6", "placeholder", config)
        second = baseline.fingerprint(
            "clang-format", "17.0.6", "protect-column", config)
        assert first != second

    def test_config_change_changes_fingerprint(self, tmp_path):
        config = self.config(tmp_path)
        first = baseline.fingerprint(
            "clang-format", "17.0.6", "placeholder", config)
        write(config, "IndentWidth: 4\n")
        second = baseline.fingerprint(
            "clang-format", "17.0.6", "placeholder", config)
        assert first != second

    def test_identical_inputs_are_stable(self, tmp_path):
        config = self.config(tmp_path)
        first = baseline.fingerprint(
            "clang-format", "17.0.6", "placeholder", config)
        second = baseline.fingerprint(
            "clang-format", "17.0.6", "placeholder", config)
        assert first == second

    def test_record_keeps_the_parts_for_diagnosis(self, tmp_path):
        config = self.config(tmp_path)
        record = record_of(baseline.fingerprint(
            "clang-format", "17.0.6", "placeholder", config))
        assert record["version"] == "17.0.6"
        assert record["comments"] == "placeholder"
        assert record["config"] == str(config)
        assert record["config_digest"] == baseline.config_digest(config)

    def test_missing_config_only_uses_version_and_mode(self):
        first = baseline.fingerprint("clang-format", "17.0.6", "placeholder", "")
        second = baseline.fingerprint("clang-format", "17.0.6", "placeholder", "")
        assert first == second
        assert baseline.BUILTIN == record_of(first)["config_digest"]


class TestStore:
    def test_round_trip(self, tmp_path):
        current = baseline.fingerprint(
            "clang-format", "17.0.6", "placeholder", None)
        path, code, message = baseline.write(tmp_path, current)
        assert Code.OK == code, message
        assert path == tmp_path / baseline.BASELINE_NAME
        loaded, code, message = baseline.read(tmp_path)
        assert Code.OK == code, message
        assert record_of(current) == loaded

    def test_record_path_is_under_log_dir(self, tmp_path):
        assert baseline.record_path(tmp_path) == tmp_path / baseline.BASELINE_NAME

    def test_missing_record_is_not_an_error(self, tmp_path):
        record, code, _ = baseline.read(tmp_path)
        assert Code.OK == code
        assert record is None

    def test_unconfigured_log_dir_is_reported(self):
        assert baseline.record_path(None) is None
        _, code, message = baseline.write(None, "{}")
        assert Code.BASELINE_UNWRITABLE == code
        assert "日志目录" in message

    def test_corrupt_record_is_reported(self, tmp_path):
        write(tmp_path / baseline.BASELINE_NAME, "{ not json")
        _, code, message = baseline.read(tmp_path)
        assert Code.BASELINE_BAD_FILE == code
        assert "损坏" in message

    def test_record_without_fingerprint_is_reported(self, tmp_path):
        write(tmp_path / baseline.BASELINE_NAME, json.dumps({"tool": "x"}))
        _, code, message = baseline.read(tmp_path)
        assert Code.BASELINE_BAD_FILE == code
        assert "指纹" in message

    def test_non_mapping_record_is_reported(self, tmp_path):
        write(tmp_path / baseline.BASELINE_NAME, json.dumps(["x"]))
        _, code, _ = baseline.read(tmp_path)
        assert Code.BASELINE_BAD_FILE == code

    def test_unwritable_directory_is_reported(self, tmp_path):
        target = tmp_path / "file"
        write(target, "x")
        # 把日志目录指向一个普通文件，创建目录必然失败
        _, code, message = baseline.write(target, "{}")
        assert Code.BASELINE_UNWRITABLE == code
        assert "无法写入" in message

    def test_write_creates_missing_directories(self, tmp_path):
        current = baseline.fingerprint("clang-format", "17.0.6", "none", "")
        nested = tmp_path / "build" / "logs"
        path, code, message = baseline.write(nested, current)
        assert Code.OK == code, message
        assert path is not None and path.is_file()

    def test_write_accepts_a_parsed_record(self, tmp_path):
        """已经解析过的字典也能写：调用方可能从别处取回记录。"""
        current = baseline.fingerprint(
            "clang-format", "17.0.6", "placeholder", None)
        _, code, message = baseline.write(tmp_path, record_of(current))
        assert Code.OK == code, message
        loaded, code, _ = baseline.read(tmp_path)
        assert Code.OK == code
        assert record_of(current) == loaded

    def test_write_rejects_unparsable_text(self, tmp_path):
        _, code, message = baseline.write(tmp_path, "{ 不是记录")
        assert Code.BASELINE_UNWRITABLE == code
        assert "记录" in message


class TestCompare:
    def test_no_baseline_passes(self):
        """没有基线时是否做一次性切换由使用者决定，工具不替他拒绝。"""
        current = baseline.fingerprint("clang-format", "17.0.6", "placeholder", "")
        code, message = baseline.compare(None, current)
        assert Code.OK == code
        assert "" == message

    def test_same_fingerprint_passes(self):
        current = baseline.fingerprint("clang-format", "17.0.6", "placeholder", "")
        code, _ = baseline.compare(record_of(current), current)
        assert Code.OK == code

    def test_compare_accepts_fingerprint_text_as_record(self):
        """记录是文本还是字典都该能比对，调用方不必先做转换。"""
        current = baseline.fingerprint("clang-format", "17.0.6", "placeholder", "")
        code, _ = baseline.compare(current, current)
        assert Code.OK == code

    def test_compare_reports_corrupt_record(self):
        current = baseline.fingerprint("clang-format", "17.0.6", "placeholder", "")
        code, message = baseline.compare("{ 不是记录", current)
        assert Code.BASELINE_BAD_FILE == code
        assert "损坏" in message

    def test_version_drift_is_rejected_and_named(self):
        record = baseline.fingerprint("clang-format", "17.0.6", "placeholder", "")
        current = baseline.fingerprint("clang-format", "22.1.0", "placeholder", "")
        code, message = baseline.compare(record_of(record), current)
        assert Code.BASELINE_CHANGED == code
        assert "clang-format 版本" in message
        assert "--accept-baseline" in message

    def test_comment_mode_drift_is_rejected_and_named(self):
        record = baseline.fingerprint("clang-format", "17.0.6", "placeholder", "")
        current = baseline.fingerprint("clang-format", "17.0.6", "none", "")
        code, message = baseline.compare(record_of(record), current)
        assert Code.BASELINE_CHANGED == code
        assert "注释模式" in message

    def test_config_drift_is_rejected_and_named(self, tmp_path):
        config = tmp_path / ".clang-format"
        write(config, "IndentWidth: 2\n")
        record = baseline.fingerprint("clang-format", "17.0.6", "placeholder", config)
        write(config, "IndentWidth: 4\n")
        current = baseline.fingerprint("clang-format", "17.0.6", "placeholder", config)
        code, message = baseline.compare(record_of(record), current)
        assert Code.BASELINE_CHANGED == code
        assert "排版配置" in message

    def test_compare_reports_old_and_new(self):
        record = baseline.fingerprint("clang-format", "17.0.6", "placeholder", "")
        current = baseline.fingerprint("clang-format", "22.1.0", "placeholder", "")
        _, message = baseline.compare(record_of(record), current)
        assert "旧:" in message
        assert "新:" in message

    def test_drift_after_round_trip_is_detected(self, tmp_path):
        """从磁盘读回来的记录与当前指纹仍能正确比对。"""
        baseline.write(
            tmp_path,
            baseline.fingerprint("clang-format", "17.0.6", "placeholder", None))
        loaded, code, _ = baseline.read(tmp_path)
        assert Code.OK == code
        current = baseline.fingerprint("clang-format", "17.0.6", "none", None)
        code, message = baseline.compare(loaded, current)
        assert Code.BASELINE_CHANGED == code
        assert "注释模式" in message
