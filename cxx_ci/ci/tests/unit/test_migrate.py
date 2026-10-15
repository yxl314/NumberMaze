#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate-config：旧配置项一次性改干净。

只保留"运行到就提示一次"的做法，结果是每个工程自己决定什么时候改、改一半的
配置留在仓库里。这里让升级配置变成可执行动作：dry-run 出 diff、落盘前写 .bak、
改完点名要人工复核的项。

关键约束与 source.py 同一条纪律：**规则有歧义就不写**。WHITELIST/BLACKLIST
改名成 SCOPE 需要改取值形状（两个列表 → 一个字典），只在两行都是单行列表、
且彼此相邻时自动合并；形状不齐就只报告不动手——"改名之后配置反而加载不了"
比不迁移更糟。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ci"))

from lib import migrate  # noqa: E402

LEGACY = """from pathlib import Path

ROOT = Path("/tmp/x")
{body}
"""


def read(path):
    """Python 3.4 没有 Path.read_text：ci 侧连测试也守这条线。"""
    handle = open(str(path), "r", encoding="utf-8")
    try:
        return handle.read()
    finally:
        handle.close()


def write(tmp_path, body):
    path = tmp_path / "ci.py"
    handle = open(str(path), "w", encoding="utf-8")
    try:
        handle.write(LEGACY.format(body=body))
    finally:
        handle.close()
    return str(path)


def read_bytes(path):
    handle = open(str(path), "rb")
    try:
        return handle.read()
    finally:
        handle.close()


def test_adjacent_lists_are_merged_into_scope(tmp_path):
    path = write(tmp_path, 'WHITELIST = [ROOT / "src"]\n'
                           'BLACKLIST = [ROOT / "build"]\n')
    code, text = migrate.migrate(path, dry_run=False)
    assert 1 == code, text
    loaded = read(path)
    assert 'SCOPE = {"project": [ROOT / "src"], "excluded": [ROOT / "build"]}' \
        in loaded
    assert "WHITELIST" not in loaded and "BLACKLIST" not in loaded
    # 落盘前必须留下备份
    assert Path(path + ".bak").is_file()
    assert "WHITELIST" in read(path + ".bak")


def test_dry_run_writes_nothing(tmp_path):
    path = write(tmp_path, 'WHITELIST = [ROOT / "src"]\n'
                           'BLACKLIST = [ROOT / "build"]\n')
    before = read_bytes(path)
    code, text = migrate.migrate(path, dry_run=True)
    assert 1 == code
    assert "dry-run" in text
    assert before == read_bytes(path)
    assert not Path(path + ".bak").exists()


def test_only_one_side_present_is_reported_not_rewritten(tmp_path):
    """形状不齐时不写：配置被改坏比不迁移更糟。"""
    path = write(tmp_path, 'WHITELIST = [ROOT / "src"]\n')
    before = read_bytes(path)
    code, text = migrate.migrate(path, dry_run=False)
    assert 1 == code
    assert "手工合并" in text
    assert before == read_bytes(path)
    assert not Path(path + ".bak").exists()


def test_multi_line_list_is_not_guessed_at(tmp_path):
    path = write(tmp_path, 'WHITELIST = [\n    ROOT / "src",\n]\n'
                           'BLACKLIST = [ROOT / "build"]\n')
    before = read_bytes(path)
    code, text = migrate.migrate(path, dry_run=False)
    assert 1 == code
    assert before == read_bytes(path)


def test_nothing_to_do(tmp_path):
    path = write(tmp_path, 'SCOPE = {"project": [], "excluded": []}\n')
    code, text = migrate.migrate(path, dry_run=False)
    assert 0 == code
    assert "没有需要迁移" in text


class TestRenamingARule:
    """改名类规则要真的改，而不是只报一句"请手工处理"。

    RULES 里的 new 一直被跳过，于是工具只会合并 WHITELIST/BLACKLIST，
    所有改名条目形同虚设——使用者按提示去改，工具自己却不动手。
    """

    def test_a_plain_rename_is_rewritten(self):
        hits, _notes = migrate.plan('LOG_DIR = ROOT / "logs"\n')
        assert 1 == len(hits), hits
        _number, old, new, name = hits[0]
        assert 'LOG_DIR = ROOT / "logs"' == old
        assert 'CI_LOG_DIR = ROOT / "logs"' == new
        assert "CI_LOG_DIR" == name

    def test_a_shape_changing_rule_is_not_renamed(self):
        """WHITELIST 单独出现时不能改成 SCOPE = [...]. 那是加载不了的配置。"""
        hits, notes = migrate.plan('WHITELIST = [ROOT / "src"]\n')
        assert [] == hits, hits
        assert any("SCOPE" in note for note in notes), notes

    def test_a_merge_whose_target_exists_is_only_reported(self):
        """两行都在、但 SCOPE 已经存在时不能照合并——那会写出两处赋值。"""
        text = ('SCOPE = {"project": [], "excluded": []}\n'
                'WHITELIST = [ROOT / "src"]\n'
                'BLACKLIST = [ROOT / "build"]\n')
        hits, notes = migrate.plan(text)
        assert [] == hits, hits
        assert any("已经有 SCOPE" in note for note in notes), notes

    def test_one_side_alone_is_only_reported(self):
        hits, notes = migrate.plan('WHITELIST = [ROOT / "src"]\n')
        assert [] == hits, hits
        assert any("形状不齐" in note for note in notes), notes

    def test_rename_keeps_the_value_untouched(self):
        hits, _notes = migrate.plan(
            'LOG_DIR = ROOT / ".cxx_ci" / "logs"  # 注意保留行尾注释\n')
        assert 1 == len(hits), hits
        assert hits[0][2].endswith('# 注意保留行尾注释'), hits[0][2]
