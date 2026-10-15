"""init 子命令：探测生成配置。

覆盖探测的判定（构建工具、header-filter、禁改区、范围）、模板落盘、以及生成
配置能被 config.load_config 加载。探测逻辑在 probe 里是纯函数，可以脱离命令行
单独断言；落盘那一步才走 CLI。
"""

import subprocess

import pytest
from cxx_standardize import config as config_module
from cxx_standardize import fmt, probe
from cxx_standardize.errors import Code
from std_support import Project


def make_project(root, files):
    """在 root 下按 {相对路径: 内容} 造文件；值为 None 表示目录。"""
    for relative, text in files.items():
        path = root / relative
        if text is None:
            path.mkdir(parents=True, exist_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


# ------------------------------------------------------------------ 探测

class TestDetectBuild:
    def test_cmake_only(self, tmp_path):
        make_project(tmp_path, {"CMakeLists.txt": "project(x)\n"})
        files, _dirs = probe.scan(tmp_path)
        tool, build_file, note = probe.detect_build(files)
        assert "cmake" == tool
        assert "CMakeLists.txt" == build_file
        assert "CMakeLists.txt" in note

    def test_qmake_only(self, tmp_path):
        make_project(tmp_path, {"app.pro": "TEMPLATE = app\n"})
        files, _dirs = probe.scan(tmp_path)
        tool, build_file, _note = probe.detect_build(files)
        assert "qmake" == tool
        assert "app.pro" == build_file

    def test_ambiguous_leaves_todo(self, tmp_path):
        make_project(tmp_path, {"CMakeLists.txt": "project(x)\n",
                                "app.pro": "TEMPLATE = app\n"})
        files, _dirs = probe.scan(tmp_path)
        tool, build_file, note = probe.detect_build(files)
        assert tool is None and build_file is None
        assert "二选一" in note


class TestDetectHeaderFilter:
    def test_single_subdir_under_include(self, tmp_path):
        make_project(tmp_path, {"include/proj/api.h": "#pragma once\n"})
        files, _dirs = probe.scan(tmp_path)
        pattern, note = probe.detect_header_filter(tmp_path, files)
        assert r"include/proj/.*" == pattern
        assert "proj" in note

    def test_headers_directly_under_include_stays_unset(self, tmp_path):
        """头文件直接躺在 include/ 下时没有可锚定的工程段。

        写 include/.* 会连 /usr/include/stdio.h 一起放行——正是配置审计要拦的
        那条。猜不出安全值就如实留空让人来定，不生成一个自己会拒的配置。
        """
        make_project(tmp_path, {"include/api.h": "#pragma once\n"})
        files, _dirs = probe.scan(tmp_path)
        pattern, note = probe.detect_header_filter(tmp_path, files)
        assert "" == pattern
        assert "工程段" in note, note

    def test_headers_under_a_named_subdir_are_anchored_on_it(self, tmp_path):
        make_project(tmp_path, {"include/demo/api.h": "#pragma once\n"})
        files, _dirs = probe.scan(tmp_path)
        pattern, _note = probe.detect_header_filter(tmp_path, files)
        assert r"include/demo/.*" == pattern

    def test_no_headers_leaves_empty(self, tmp_path):
        make_project(tmp_path, {"src/a.cpp": "int f();\n"})
        files, _dirs = probe.scan(tmp_path)
        pattern, note = probe.detect_header_filter(tmp_path, files)
        assert "" == pattern
        assert "没探到" in note

    def test_falls_back_to_src(self, tmp_path):
        make_project(tmp_path, {"src/api.h": "#pragma once\n"})
        files, _dirs = probe.scan(tmp_path)
        pattern, note = probe.detect_header_filter(tmp_path, files)
        assert r"src/.*" == pattern
        assert "未发现 include" in note


class TestDetectFrozen:
    def test_by_name(self, tmp_path):
        make_project(tmp_path, {"third_party/lib/x.cpp": "int g();\n"})
        files, _dirs = probe.scan(tmp_path)
        dirs = probe.all_dirs(tmp_path)
        frozen, note = probe.detect_frozen(tmp_path, files, dirs)
        assert ["third_party"] == frozen
        assert "third_party" in note

    def test_by_license_without_build_file(self, tmp_path):
        make_project(tmp_path, {"deps/foo/LICENSE": "MIT\n",
                                "deps/foo/x.cpp": "int g();\n"})
        files, _dirs = probe.scan(tmp_path)
        dirs = probe.all_dirs(tmp_path)
        frozen, _note = probe.detect_frozen(tmp_path, files, dirs)
        assert "deps/foo" in frozen

    def test_own_dir_with_license_and_build_file_is_not_frozen(self, tmp_path):
        """含 LICENSE 但有本工程构建文件的目录不算 vendored。"""
        make_project(tmp_path, {"libs/mine/LICENSE": "MIT\n",
                                "libs/mine/CMakeLists.txt": "project(m)\n"})
        files, _dirs = probe.scan(tmp_path)
        dirs = probe.all_dirs(tmp_path)
        frozen, _note = probe.detect_frozen(tmp_path, files, dirs)
        assert "libs/mine" not in frozen

    def test_none(self, tmp_path):
        make_project(tmp_path, {"src/a.cpp": "int f();\n"})
        files, _dirs = probe.scan(tmp_path)
        dirs = probe.all_dirs(tmp_path)
        frozen, note = probe.detect_frozen(tmp_path, files, dirs)
        assert [] == frozen
        assert "没探到" in note


class TestDetectScope:
    def test_fallback_naming(self, tmp_path):
        make_project(tmp_path, {"src/a.cpp": "int f();\n",
                                "include/api.h": "#pragma once\n"})
        files, _dirs = probe.scan(tmp_path)
        project, excluded, note = probe.detect_scope(tmp_path, files, [])
        assert ["include", "src"] == project
        assert "命名惯例" in note

    def test_git_tracked(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
        subprocess.run(["git", "config", "user.email", "t@e.com"],
                       cwd=str(tmp_path), check=True)
        subprocess.run(["git", "config", "user.name", "t"],
                       cwd=str(tmp_path), check=True)
        make_project(tmp_path, {"src/a.cpp": "int f();\n",
                                "third_party/lib/x.cpp": "int g();\n"})
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "i"], cwd=str(tmp_path),
                       check=True)
        files, _dirs = probe.scan(tmp_path)
        project, excluded, note = probe.detect_scope(tmp_path, files, [])
        assert "src" in project
        assert "third_party" in excluded
        assert "git" in note


class TestRender:
    def test_generated_config_loads(self, tmp_path):
        """渲染出来的配置必须能被 config.load_config 接住。"""
        make_project(tmp_path, {"CMakeLists.txt": "project(x)\n",
                                "src/a.cpp": "int f();\n",
                                "include/x/api.h": "#pragma once\n"})
        created, errors = probe.ensure_config_files(tmp_path)
        assert [] == errors
        plan = probe.probe(tmp_path, created)
        target = tmp_path / "config" / "standardize.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        probe.write_config(target, probe.render(plan))

        loaded, code, message = config_module.load_config(str(target))
        assert 0 == code, message
        assert loaded.root == tmp_path.resolve()
        assert loaded.clang_format_config.name == ".clang-format"
        assert r"include/x/.*" == loaded.tidy_header_filter

    def test_header_records_contract(self, tmp_path):
        plan = probe.probe(tmp_path)
        text = probe.render(plan)
        assert "CONTRACT_VERSION = 3" in text
        assert "ROOT = Path(__file__).resolve().parents[1]" in text


class TestEnsureConfigFiles:
    def test_creates_missing_templates(self, tmp_path):
        created, errors = probe.ensure_config_files(tmp_path)
        assert [] == errors
        assert [".clang-format", ".clang-tidy"] == created
        assert (tmp_path / ".clang-format").is_file()
        assert (tmp_path / ".clang-tidy").is_file()

    def test_keeps_existing_files(self, tmp_path):
        make_project(tmp_path, {".clang-format": "BasedOnStyle: Google\n"})
        created, errors = probe.ensure_config_files(tmp_path)
        assert [] == errors
        assert [".clang-tidy"] == created
        kept = (tmp_path / ".clang-format").read_text(encoding="utf-8")
        assert "Google" in kept

    def test_probe_notes_created_templates(self, tmp_path):
        make_project(tmp_path, {"CMakeLists.txt": "project(x)\n"})
        created, _errors = probe.ensure_config_files(tmp_path)
        plan = probe.probe(tmp_path, created)
        text = probe.render(plan)
        assert "已按最小模板生成" in text


# ------------------------------------------------------------------ 命令行

@pytest.fixture
def project():
    box = Project()
    yield box
    box.dispose()


def test_init_writes_config_and_templates(project):
    (project.path / ".clang-format").unlink()
    (project.path / ".clang-tidy").unlink()
    code, out, _err = project.run(["init"])
    assert 0 == code, out
    target = project.path / "config" / "standardize.py"
    assert target.is_file()
    assert (project.path / ".clang-format").is_file()
    assert (project.path / ".clang-tidy").is_file()
    assert "已生成最小配置模板" in out


def test_init_generated_config_is_usable(project):
    code, _out, _err = project.run(["init"])
    assert 0 == code
    target = project.path / "config" / "standardize.py"
    loaded, lcode, message = config_module.load_config(str(target))
    assert 0 == lcode, message
    assert loaded.root.resolve() == project.path.resolve()


def test_init_does_not_overwrite_existing_config(project):
    target = project.write("config/standardize.py", "# 已有\n")
    code, _out, err = project.run(["init"])
    assert 2 == code
    assert "不覆盖" in err
    assert "# 已有" in target.read_text(encoding="utf-8")


def test_init_honours_out_and_project_root(project, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    code, _out, _err = project.run(
        ["init", "--project-root", str(other), "--out", "cfg.py"])
    assert 0 == code
    assert (other / "cfg.py").is_file()
    assert not (project.path / "config" / "standardize.py").exists()


class TestClangVersionDiagnosis:
    """clang 探不到主版本时，要说清是哪一种探不到。

    自己装到非标准前缀的 clang 最常见的失败不是"版本串形状陌生"，而是动态库
    加载失败：加载器把 "cannot open shared object file" 打在 stderr 上，stdout
    一个字都没有。笼统报成"认不出版本"，使用者会去查版本格式，而该配的是
    LD_LIBRARY_PATH。
    """

    def test_loader_failure_is_reported_with_the_original_message(self, tmp_path):
        fake = tmp_path / "clang-format"
        fake.write_text(
            "#!/bin/sh\n"
            "echo 'clang-format: error while loading shared libraries: "
            "libLLVM-17.so.1: cannot open shared object file' >&2\n"
            "exit 127\n", encoding="utf-8")
        fake.chmod(0o755)
        found, code, detail = fmt.version(fake)
        assert found is None
        assert Code.COMMAND_UNRUNNABLE == code, code
        assert "cannot open shared object file" in detail, detail
        assert "LD_LIBRARY_PATH" in detail, detail
