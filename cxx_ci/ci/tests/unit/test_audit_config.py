#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置审计的单元测试。

各条判据各测正反两面：死配置、配置旁路（含 docstring 排除）、编译标准锁定
（含注释里的 -std=）、裸写文本、自行前缀比较。构建文件全用临时文件，不依赖
机器上装没装 qmake/cmake。
"""

import tempfile
from pathlib import Path

from lib import audit, errors
from ci_support import Stub


def box(prefix="cxx_ci_audit_"):
    """建一个临时目录，由测试夹具在结束后删除。"""
    return Path(tempfile.mkdtemp(prefix=prefix))


def write(path, text):
    """写一份文本文件，显式指定编码；父目录必要时逐级建出。"""
    parent = path.parent
    if not parent.is_dir():
        parent.mkdir(parents=True)
    handle = open(str(path), "w", encoding="utf-8")
    try:
        handle.write(text)
    finally:
        handle.close()
    return path


def referencing_source(directory, keys, name="probe.py"):
    """造一份"每个键都以 cfg.<键> 形式被引用"的源码，避免死配置干扰别的判据。"""
    lines = ["def probe(cfg):"]
    for key in keys:
        lines.append("    value = cfg." + key)
    lines.append("    return value")
    return write(directory / name, "\n".join(lines) + "\n")


def joined(messages):
    return "\n".join(messages)


# ---------------------------------------------------------------- 检查 1: 死配置

def test_dead_config_key_is_reported():
    directory = box()
    target = write(directory / "script.py",
                   "def probe(cfg):\n    return cfg.alpha\n")
    code, messages = audit.run(Stub(alpha="A", beta="B"), [target])
    assert errors.AUDIT_DEAD_KEY == code
    assert "beta" in joined(messages)
    assert "alpha" not in joined(messages)


def test_referenced_config_key_is_not_reported():
    directory = box()
    target = write(directory / "script.py",
                   "def probe(cfg):\n    return cfg.alpha, cfg.beta\n")
    code, messages = audit.run(Stub(alpha="A", beta="B"), [target])
    assert errors.OK == code
    assert [] == messages


def test_getattr_string_counts_as_reference():
    """可选读法 getattr(cfg, "键", 默认) 也算有人读。"""
    directory = box()
    target = write(
        directory / "script.py",
        "def probe(cfg):\n    return getattr(cfg, \"alpha\", None)\n")
    code, messages = audit.run(Stub(alpha="A"), [target])
    assert errors.OK == code, messages


# -------------------------------------------------------------- 检查 2: 配置旁路

def test_bypass_literal_points_at_file_and_line():
    directory = box()
    target = write(
        directory / "script.py",
        "def probe(cfg):\n    return cfg.out_dir == \"build/out\"\n")
    code, messages = audit.run(Stub(out_dir="build/out"), [target])
    assert errors.AUDIT_BYPASS_LITERAL == code
    assert "{}:2".format(target) in joined(messages)
    assert "build/out" in joined(messages)
    assert "out_dir" in joined(messages)


def test_docstring_literal_is_not_a_bypass():
    """模块、函数、类体首表达式的常量都是说明文字，不算取值。"""
    directory = box()
    target = write(directory / "script.py",
                   '"""build/out"""\n'
                   "\n"
                   "\n"
                   "def probe(cfg):\n"
                   '    """build/out"""\n'
                   "    return cfg.out_dir\n"
                   "\n"
                   "\n"
                   "class Holder:\n"
                   '    """build/out"""\n')
    code, messages = audit.run(Stub(out_dir="build/out"), [target])
    assert errors.OK == code, messages
    assert [] == messages


def test_container_values_expand_to_elements():
    """列表与字典的元素同样参与旁路比对。"""
    directory = box()
    target = write(directory / "script.py",
                   "def probe(cfg):\n"
                   "    return cfg.flags == \"-O2\" or cfg.env\n")
    code, messages = audit.run(
        Stub(flags=["-O1", "-O2"], env={"MODE": "-O2"}), [target])
    assert errors.AUDIT_BYPASS_LITERAL == code, messages
    assert "-O2" in joined(messages)


# ---------------------------------------------------------- 检查 3: 编译标准锁定

def qmake_config(directory, pin="-std=gnu++0x"):
    """构造一个 qmake 项目的配置桩，并把它的键都引用上。"""
    pro = write(directory / "project.pro", "")
    config = Stub(build_file=str(pro), root=str(directory),
                  std_vars=["QMAKE_CXXFLAGS_CXX11"], std_pin=pin)
    target = referencing_source(directory, ["build_file", "root", "std_vars",
                                            "std_pin"])
    return config, target, pro


def test_qmake_correct_pin_passes():
    directory = box()
    config, target, pro = qmake_config(directory)
    write(pro, "QMAKE_CXXFLAGS_CXX11 = -std=gnu++0x\n")
    code, messages = audit.run(config, [target])
    assert errors.OK == code, messages


def test_qmake_pin_may_name_the_carrier():
    """锁定值把承载变量名一起写下来时，mkspec 的二选一由配置当场定死。"""
    directory = box()
    pro = write(directory / "project.pro",
                "QMAKE_CXXFLAGS_CXX11 = -std=gnu++0x\n")
    config = Stub(build_file=str(pro), root=str(directory),
                  std_vars=["QMAKE_CXXFLAGS_CXX11", "QMAKE_CXXFLAGS_GNUCXX11"],
                  std_pin="QMAKE_CXXFLAGS_CXX11=-std=gnu++0x")
    target = referencing_source(directory, ["build_file", "root", "std_vars",
                                            "std_pin"])
    code, messages = audit.run(config, [target])
    assert errors.OK == code, messages


def test_qmake_missing_carrier_is_reported():
    directory = box()
    config, target, pro = qmake_config(directory)
    write(pro, "SOURCES += main.cpp\n")
    code, messages = audit.run(config, [target])
    assert errors.AUDIT_STD_DRIFT == code
    assert "QMAKE_CXXFLAGS_CXX11" in joined(messages)


def test_qmake_wrong_pin_value_is_reported():
    directory = box()
    config, target, pro = qmake_config(directory)
    write(pro, "QMAKE_CXXFLAGS_CXX11 = -std=c++11\n")
    code, messages = audit.run(config, [target])
    assert errors.AUDIT_STD_DRIFT == code
    assert "c++11" in joined(messages)


def test_qmake_comment_std_example_is_reported():
    """注释里的示例也会被 qmake 当真，因此同样判红。"""
    directory = box()
    config, target, pro = qmake_config(directory)
    write(pro, "# 示例: -std=c++11\nQMAKE_CXXFLAGS_CXX11 = -std=gnu++0x\n")
    code, messages = audit.run(config, [target])
    assert errors.AUDIT_STD_DRIFT == code
    assert "c++11" in joined(messages)


def cmake_config(directory, pin="14"):
    """构造一个 cmake 项目的配置桩，并把它的键都引用上。"""
    build = write(directory / "CMakeLists.txt", "")
    config = Stub(build_file=str(build), root=str(directory),
                  std_vars=[], std_pin=pin)
    target = referencing_source(directory, ["build_file", "root", "std_vars",
                                            "std_pin"])
    return config, target, build


def test_cmake_correct_standard_passes():
    directory = box()
    config, target, build = cmake_config(directory)
    write(build, "set(CMAKE_CXX_STANDARD 14)\n")
    code, messages = audit.run(config, [target])
    assert errors.OK == code, messages


def test_cmake_missing_standard_is_reported():
    directory = box()
    config, target, build = cmake_config(directory)
    write(build, "project(demo)\n")
    code, messages = audit.run(config, [target])
    assert errors.AUDIT_STD_DRIFT == code
    assert "CMAKE_CXX_STANDARD" in joined(messages)


def test_cmake_other_standard_is_reported():
    directory = box()
    config, target, build = cmake_config(directory)
    write(build, "set(CMAKE_CXX_STANDARD 11)\n")
    code, messages = audit.run(config, [target])
    assert errors.AUDIT_STD_DRIFT == code


def test_std_pin_unset_skips_std_check():
    """没锁标准时无从判漂移，构建文件里写什么都不该报。"""
    directory = box()
    build = write(directory / "CMakeLists.txt", "# -std=c++11\n")
    config = Stub(build_file=str(build), root=str(directory), std_pin="")
    target = referencing_source(directory, ["build_file", "root", "std_pin"])
    code, messages = audit.run(config, [target])
    assert errors.OK == code, messages


# ------------------------------------------------------------------ 对象与出口

def test_targets_recurse_and_keep_only_python():
    """目录递归取 *.py；非 py 文件既不算引用，也不参与旁路比对。"""
    directory = box()
    write(directory / "pkg" / "deep" / "a.py",
          "def probe(cfg):\n    return cfg.secret\n")
    write(directory / "pkg" / "deep" / "note.txt", 'X = "S3CR3T"\n')
    code, messages = audit.run(Stub(secret="S3CR3T"), [directory])
    assert errors.OK == code, messages
    assert [] == messages


def test_default_targets_covers_tool_and_globs():
    """缺省对象包含本工具自身的实现代码与 AUDIT_FILES_GLOBS。"""
    directory = box()
    write(directory / "project" / "a.py", "VALUE = 1\n")
    config = Stub(root=str(directory), audit_files_globs=["project/*.py"])
    targets = [str(item) for item in audit.default_targets(config)]
    assert any(item.endswith("ci.py") for item in targets)
    assert any(item.endswith("lib") for item in targets)
    assert any(item.endswith("stages") for item in targets)
    assert any(item.endswith("a.py") for item in targets)
    # 目录交给 collect_files 递归取 py，实现文件因此都在审计对象里
    files = [str(item) for item in audit.collect_files(
        audit.default_targets(config))]
    assert any(item.endswith("audit.py") for item in files)


def test_recursive_glob_pattern_is_expanded():
    """配置里的 Source/**/*.py 要能跨目录命中，不能一声不响地扫空。"""
    directory = box()
    write(directory / "src" / "deep" / "a.py", "VALUE = 1\n")
    config = Stub(root=str(directory), audit_files_globs=["src/**/*.py"])
    files = [str(item) for item in
             audit.collect_files(audit.default_targets(config))]
    assert any(item.endswith("a.py") for item in files)


# -------------------------------------------------------- 检查 5: 自行前缀比较

# 违反写法由片段拼出，免得本测试文件自己也被这条检查命中
_PREFIX_VIOLATION = ("def probe(p, root):\n"
                     "    return p." + "startswith" + "(root + "
                     + "os" + "." + "sep" + ")\n")


def tool_source_tree(monkeypatch, name, text):
    """把临时目录伪装成工具源码树，使目标文件被当作"自身实现"。"""
    directory = box()
    target = write(directory / "lib" / name, text)
    monkeypatch.setattr(audit, "TOOL_ROOT", directory)
    return target


def test_self_made_prefix_compare_is_reported(monkeypatch):
    target = tool_source_tree(monkeypatch, "probe.py", _PREFIX_VIOLATION)
    code, messages = audit.run(Stub(), [target])
    assert errors.AUDIT_PREFIX_DRIFT == code
    assert "probe.py" in joined(messages)


def test_paths_py_is_whitelisted(monkeypatch):
    """paths.py 就是那个唯一实现，它自己写前缀比较不算违规。"""
    target = tool_source_tree(monkeypatch, "paths.py", _PREFIX_VIOLATION)
    code, messages = audit.run(Stub(), [target])
    assert errors.OK == code, messages
    assert [] == messages


def test_prefix_compare_in_project_script_is_ignored(monkeypatch):
    """项目脚本不在范围：只拦工具自身的实现代码。"""
    directory = box()
    target = write(directory / "probe.py", _PREFIX_VIOLATION)
    code, messages = audit.run(Stub(), [target])
    assert errors.OK == code, messages


def test_no_audit_switch_exists():
    """--no-audit 是入口的调试开关。"""
    import ci
    parser = ci.build_parser(Stub(build_dir="/tmp", root="/tmp"))
    assert parser.parse_args(["--no-audit"]).no_audit is True
    assert parser.parse_args([]).no_audit is False
