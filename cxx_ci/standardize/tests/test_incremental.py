"""增量取集的单元测试。

门禁成本应与改动规模相关。非 git 环境必须能退回全量并给出原因，否则零文件
接入的原则就破了；改动到头部文件时必须带上包含它的源文件，否则增量会把
受影响的地方漏掉。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cxx_standardize import incremental
from cxx_standardize.errors import Code

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="需要 git")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", check=True)
    return done.stdout


@pytest.fixture
def repo(tmp_path):
    """一个只有一次提交的合成 git 仓。"""
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@local")
    git(tmp_path, "config", "user.name", "t")
    write(tmp_path / "a.cpp", "int a() { return 0; }\n")
    write(tmp_path / "b.cpp", "int b() { return 0; }\n")
    write(tmp_path / "shared.h", "#pragma once\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


class TestChangedPaths:
    def test_detects_modified_file(self, repo):
        write(repo / "a.cpp", "int a() { return 1; }\n")
        paths, code, message = incremental.changed_paths(repo)
        assert Code.OK == code, message
        assert [repo / "a.cpp"] == paths

    def test_paths_are_pathlib_objects(self, repo):
        write(repo / "a.cpp", "int a() { return 1; }\n")
        paths, code, _ = incremental.changed_paths(repo)
        assert Code.OK == code
        assert all(isinstance(item, Path) for item in paths)

    def test_clean_tree_is_empty(self, repo):
        paths, code, _ = incremental.changed_paths(repo)
        assert Code.OK == code
        assert [] == paths

    def test_deleted_files_are_not_selected(self, repo):
        (repo / "b.cpp").unlink()
        paths, code, _ = incremental.changed_paths(repo)
        assert Code.OK == code
        assert [] == paths

    def test_base_range_collects_committed_change(self, repo):
        first = git(repo, "rev-parse", "HEAD").strip()
        write(repo / "b.cpp", "int b() { return 1; }\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "second")
        paths, code, message = incremental.changed_paths(repo, base=first)
        assert Code.OK == code, message
        assert [repo / "b.cpp"] == paths

    def test_non_git_directory_reports_reason(self, tmp_path):
        paths, code, message = incremental.changed_paths(tmp_path)
        assert Code.NOT_A_REPOSITORY == code
        assert [] == paths
        assert "git" in message

    def test_is_repository(self, repo):
        assert incremental.is_repository(repo)

    def test_plain_directory_is_not_a_repository(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert not incremental.is_repository(plain)
        assert not incremental.is_repository(tmp_path / "missing")


class TestSelect:
    """改动集到待处理文件的映射（不含头文件到翻译单元的映射）。"""

    def _deps(self, mapping):
        def dependencies(path):
            return set(mapping.get(path, ())), Code.OK, ""
        return dependencies

    def test_changed_source_is_selected(self, tmp_path):
        source = tmp_path / "a.cpp"
        source.write_text("\n", encoding="utf-8")
        other = tmp_path / "b.cpp"
        other.write_text("\n", encoding="utf-8")
        selected, _ = incremental.select([source], [source, other])
        assert [source] == selected

    def test_unchanged_candidates_are_not_returned(self, tmp_path):
        source = tmp_path / "a.cpp"
        source.write_text("\n", encoding="utf-8")
        other = tmp_path / "b.cpp"
        other.write_text("\n", encoding="utf-8")
        selected, _ = incremental.select([source], [other, source])
        assert [source] == selected

    def test_changed_header_pulls_in_its_users(self, tmp_path):
        header = tmp_path / "b.h"
        header.write_text("\n", encoding="utf-8")
        user = tmp_path / "a.cpp"
        user.write_text('#include "b.h"\n', encoding="utf-8")
        stranger = tmp_path / "c.cpp"
        stranger.write_text("\n", encoding="utf-8")
        selected, _ = incremental.select(
            [header], [user, stranger], self._deps({user: [header]}))
        assert [user] == selected

    def test_result_keeps_candidate_order(self, tmp_path):
        header = tmp_path / "b.h"
        header.write_text("\n", encoding="utf-8")
        first = tmp_path / "a.cpp"
        second = tmp_path / "c.cpp"
        for item in (first, second):
            item.write_text("\n", encoding="utf-8")
        selected, _ = incremental.select(
            [header], [second, first],
            self._deps({first: [header], second: [header]}))
        assert [second, first] == selected

    def test_suffix_filter_drops_out_of_scope_paths(self, tmp_path):
        source = tmp_path / "a.cpp"
        source.write_text("\n", encoding="utf-8")
        other = tmp_path / "notes.md"
        other.write_text("\n", encoding="utf-8")
        selected, _ = incremental.select([other], [source, other],
                                         suffix=".cpp")
        assert [] == selected

    def test_string_paths_are_accepted_and_returned_as_path(self, tmp_path):
        source = tmp_path / "a.cpp"
        source.write_text("\n", encoding="utf-8")
        selected, _ = incremental.select([str(source)], [str(source)])
        assert [source] == selected


class TestResolver:
    """改动的头文件到受影响翻译单元的映射。

    只走编译器给出的包含关系（`-MM`）。解析不出来时把候选一并选中并说明，
    不做文本比对：多处理一个文件只是慢一点，漏掉受影响的翻译单元会给出
    "检查通过"的假结论。
    """

    def test_dependencies_decide_the_mapping(self, tmp_path):
        source = tmp_path / "a.cpp"
        source.write_text("// 什么都没包含\n", encoding="utf-8")
        header = tmp_path / "b.h"
        header.write_text("\n", encoding="utf-8")
        calls = []

        def dependencies(path):
            calls.append(path)
            return {header.resolve()}, Code.OK, ""

        selected, notes = incremental.select([header], [source], dependencies)
        assert [source] == selected
        assert [source] == calls
        assert [] == notes

    def test_unresolvable_candidate_is_selected_and_reported(self, tmp_path):
        source = tmp_path / "a.cpp"
        source.write_text("// 什么都没包含\n", encoding="utf-8")
        header = tmp_path / "b.h"
        header.write_text("\n", encoding="utf-8")

        def dependencies(path):
            return set(), Code.DB_MISSING, "没有条目"

        selected, notes = incremental.select([header], [source], dependencies)
        assert [source] == selected
        assert notes and "解析不出来" in notes[0]

    def test_without_dependencies_only_the_changed_files_are_selected(
            self, tmp_path):
        source = tmp_path / "a.cpp"
        source.write_text('#include "b.h"\n', encoding="utf-8")
        header = tmp_path / "b.h"
        header.write_text("\n", encoding="utf-8")
        # 没有解析器就不做映射：头文件不在候选里，于是什么都不选
        selected, notes = incremental.select([header], [source], None)
        assert [] == selected
        assert [] == notes
