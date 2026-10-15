#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""style 三入口共享前置（F2）的回归测试。

钉住三件事：

① 同一 fixture 下 check / record / apply 看到**同一份**待分析集合——任一入口
   不生成派生文件时，那些 TU 的诊断会漏登记，随后被 check 报成"新增违规"，
   用户没改代码门禁却红了；
② "有编译数据库、没有派生文件"时 `--record` 会先去生成派生文件；
③ 前置步骤每个入口只跑一次，防回潮。

另外把唯一允许的行为变化钉死：派生文件没就绪时 `--apply`/`--record` 必须在结论
里说"结果不完整"，`--strict`（XFAIL_STRICT）下判失败。
"""

from cxx_standardize import style
from cxx_standardize.errors import Code

from std_support import CONFIG_TEMPLATE
from test_flows import style_env


class _Checklist:
    """xfail 清单的最小替身：_classify 与 verdict 只调这两个谓词。"""

    def style(self, *args):
        return None

    def stale(self, considered=None):
        return []

    def expired(self, today=""):
        return []


class _Config:
    """_prepare_run 只读这几个字段，其余交给被 monkeypatch 的桩。"""

    tidy_prepare = True
    clang_tidy_config = ""
    xfail_strict = False
    verify_command = ["true"]
    incremental = False
    jobs = 0

    def __init__(self, root):
        self.root = root


class _Tools:
    clang_tidy = "/fake/clang-tidy"
    clang_format = ""


def test_three_entrypoints_see_the_same_files(project, monkeypatch):
    """三个入口的前置准备只有一份，待分析集合因此完全一致。"""
    target = project.write("src/a.cpp", "int main() { return 0; }\n")
    header = project.write("src/a.h", "int f();\n")
    seen = []
    monkeypatch.setattr(style, "prepare", lambda config: (True, ""))
    monkeypatch.setattr(style, "collect_files",
                        lambda config, options: [target, header])
    monkeypatch.setattr(style, "ensure_database_with_build",
                        lambda config: (str(project.path / "build"
                                            / "compile_commands.json"),
                                        Code.OK, ""))
    monkeypatch.setattr(style.xfail, "load_checklist",
                        lambda config: (_Checklist(), Code.OK, ""))

    def fake_scan(binary, config, config_file, files, workdir, database_dir,
                  jobs):
        seen.append(list(files))
        return []

    def fake_export(tools, config, config_file, files, workdir, database_dir,
                    jobs):
        seen.append(list(files))
        return [], Code.OK, ""

    monkeypatch.setattr(style, "_scan", fake_scan)
    monkeypatch.setattr(style, "_export", fake_export)
    config = _Config(project.path)
    options = style.Options()

    style.check(config, options, _Tools())
    style.record(config, options, _Tools())
    style.apply(config, options, _Tools())

    assert [[target, header]] * 3 == seen


def test_preamble_steps_run_once_per_entry(project, monkeypatch):
    """每个入口的前置步骤各跑一次：抽出来就不能再被复制回入口里。"""
    calls = []
    files = [project.path / "src" / "a.cpp"]

    def mark(name, value):
        calls.append(name)
        return value

    monkeypatch.setattr(style, "prepare",
                        lambda config: mark("prepare", (True, "")))
    monkeypatch.setattr(style, "collect_files",
                        lambda config, options: mark("collect", list(files)))
    monkeypatch.setattr(style, "ensure_database_with_build",
                        lambda config: mark("database", (
                            str(project.path / "build"
                                / "compile_commands.json"), Code.OK, "")))
    monkeypatch.setattr(style, "incremental_files",
                        lambda config, options, found, database_dir=None:
                        mark("incremental", (list(found), [])))
    monkeypatch.setattr(style.xfail, "load_checklist",
                        lambda config: (_Checklist(), Code.OK, ""))
    monkeypatch.setattr(style, "_scan", lambda *a, **k: [])
    monkeypatch.setattr(style, "_export", lambda *a, **k: ([], Code.OK, ""))
    config = _Config(project.path)
    options = style.Options()

    for entry, incremental in ((style.check, True),
                               (style.record, False),
                               (style.apply, True)):
        del calls[:]
        entry(config, options, _Tools())
        assert 1 == calls.count("prepare"), calls
        assert 1 == calls.count("collect"), calls
        assert 1 == calls.count("database"), calls
        assert (1 if incremental else 0) == calls.count("incremental"), calls


def test_record_generates_derived_files_first(project):
    """有编译数据库、没有派生文件时，--record 也要先 prepare。

    不调 prepare 时含 #include "X.moc" 的 TU 一个都分析不了，那些诊断会整片
    漏登记，随后被 check 报成"新增违规"。
    """
    marker = project.path / "moc-generated.txt"
    project.write("build/Makefile",
                  "moc_thing.cpp:\n\t@touch \"$$MARKER_FILE\"\n")
    source = project.write("src/a.cpp",
                           "int main() { int Bad = 1; return Bad; }\n")
    _directory, env, body = style_env(project, [
        (str(source), 12, "readability-identifier-naming")])
    env["FIXES_BODY"] = body
    env["MARKER_FILE"] = str(marker)
    code, out, err = project.run(["style", "--record"], env=env)
    assert marker.is_file(), (out, err)


class TestIncompleteIsNotEmpty:
    """派生文件未就绪时不许"0 个 TU 被分析"与"全部 TU 干净"给同一输出。"""

    def _prepare_failure(self, project):
        project.write("build/Makefile", "moc_thing.cpp:\n\t@false\n")

    def test_apply_says_incomplete(self, project):
        self._prepare_failure(project)
        project.write("src/a.cpp", "int main() { return 0; }\n")
        _directory, env, body = style_env(project, [])
        env["FIXES_BODY"] = body
        code, out, err = project.run(["style", "--apply"], env=env)
        assert 0 == code, (out, err)
        assert "派生文件未就绪，结果不完整" in err, (out, err)
        assert "全部文件已符合风格" not in out

    def test_apply_strict_fails(self, project):
        self._prepare_failure(project)
        project.write("src/a.cpp", "int main() { return 0; }\n")
        project.configure(project.read("std/config/config.py")
                          + "\nXFAIL_STRICT = True\n")
        _directory, env, body = style_env(project, [])
        env["FIXES_BODY"] = body
        code, out, err = project.run(["style", "--apply"], env=env)
        assert 1 == code, (out, err)
        assert "派生文件未就绪，结果不完整" in err, (out, err)

    def test_record_says_incomplete(self, project):
        self._prepare_failure(project)
        project.write("src/a.cpp", "int main() { return 0; }\n")
        _directory, env, body = style_env(project, [])
        env["FIXES_BODY"] = body
        code, out, err = project.run(["style", "--record"], env=env)
        assert "派生文件未就绪，结果不完整" in err, (out, err)
        assert "没有需要登记的诊断" in out, (out, err)


def test_unfinished_apply_is_recovered_on_the_next_run(project):
    """上一次没跑完，下一次运行先还原。

    快照只放在内存里时，进程被 Ctrl-C 或 SIGKILL 打断就什么都不剩：源码停在
    改了一半、还没判卷的状态，而下一次运行会把这份状态当成"本来就这样"照单
    判绿——一份没人验过的状态被盖章通过。
    """
    from cxx_standardize import config as config_module
    from cxx_standardize import style as style_mod

    project.configure(CONFIG_TEMPLATE
                      + "\nVERIFY_COMMAND = [\"/bin/true\"]\n")
    project.write("src/a.cpp", "int f() {\n    int *p = 0;\n    return p ? 1 : 0;\n}\n")
    config = config_module.load_config(str(project.path / "std" / "config"
                                          / "config.py"))[0]

    # 手工造一份"上次没跑完"的现场：日志在、源码是改过的样子
    good = {config.root / "src" / "a.cpp": b"int f() {\n    int *p = 0;\n    return p ? 1 : 0;\n}\n"}
    assert style_mod._journal_write(config, good)
    project.write("src/a.cpp", "int f() {\n    int *p = nullptr;\n    return p ? 1 : 0;\n}\n")

    assert style_mod._recover_unfinished(config)
    assert "0;" in project.read("src/a.cpp"), project.read("src/a.cpp")
    assert not style_mod._journal_read(config), "还原后必须清掉日志"


def test_a_finished_apply_leaves_no_journal(project):
    """跑完（判卷通过）就不该留下日志，否则下一次运行会把正常结果又还原回去。"""
    from cxx_standardize import config as config_module
    from cxx_standardize import style as style_mod

    project.configure(CONFIG_TEMPLATE
                      + "\nVERIFY_COMMAND = [\"/bin/true\"]\n")
    # 窄规则集：Checks: "*" 会给出互相冲突的修复，工具会（正确地）拒跑
    project.write(".clang-tidy", "Checks: '-*,modernize-use-nullptr'\n")
    project.write("src/a.cpp", "int f() {\n    int *p = 0;\n    return p ? 1 : 0;\n}\n")
    code, out, err = project.run(["style", "--apply"])
    assert 0 == code, (out, err)
    config = config_module.load_config(str(project.path / "std" / "config"
                                          / "config.py"))[0]
    assert not style_mod._journal_read(config)
