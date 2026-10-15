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

from pathlib import Path

from cxx_standardize import migrate

LEGACY = """from pathlib import Path

ROOT = Path("/tmp/x")
{body}
"""


def write(tmp_path, body):
    path = tmp_path / "ci.py"
    path.write_text(LEGACY.format(body=body), encoding="utf-8")
    return str(path)


def test_adjacent_lists_are_merged_into_scope(tmp_path):
    path = write(tmp_path, 'WHITELIST = [ROOT / "src"]\n'
                           'BLACKLIST = [ROOT / "build"]\n')
    code, text = migrate.migrate(path, dry_run=False)
    assert 1 == code, text
    loaded = Path(path).read_text(encoding="utf-8")
    assert 'SCOPE = {"project": [ROOT / "src"], "excluded": [ROOT / "build"]}' \
        in loaded
    assert "WHITELIST" not in loaded and "BLACKLIST" not in loaded
    # 落盘前必须留下备份
    assert Path(path + ".bak").is_file()
    assert "WHITELIST" in Path(path + ".bak").read_text(encoding="utf-8")


def test_dry_run_writes_nothing(tmp_path):
    path = write(tmp_path, 'WHITELIST = [ROOT / "src"]\n'
                           'BLACKLIST = [ROOT / "build"]\n')
    before = Path(path).read_bytes()
    code, text = migrate.migrate(path, dry_run=True)
    assert 1 == code
    assert "dry-run" in text
    assert before == Path(path).read_bytes()
    assert not Path(path + ".bak").exists()


def test_only_one_side_present_is_reported_not_rewritten(tmp_path):
    """形状不齐时不写：配置被改坏比不迁移更糟。"""
    path = write(tmp_path, 'WHITELIST = [ROOT / "src"]\n')
    before = Path(path).read_bytes()
    code, text = migrate.migrate(path, dry_run=False)
    assert 1 == code
    assert "手工合并" in text
    assert before == Path(path).read_bytes()
    assert not Path(path + ".bak").exists()


def test_multi_line_list_is_not_guessed_at(tmp_path):
    path = write(tmp_path, 'WHITELIST = [\n    ROOT / "src",\n]\n'
                           'BLACKLIST = [ROOT / "build"]\n')
    before = Path(path).read_bytes()
    code, text = migrate.migrate(path, dry_run=False)
    assert 1 == code
    assert before == Path(path).read_bytes()


def test_nothing_to_do(tmp_path):
    path = write(tmp_path, 'SCOPE = {"project": [], "excluded": []}\n')
    code, text = migrate.migrate(path, dry_run=False)
    assert 0 == code
    assert "没有需要迁移" in text
