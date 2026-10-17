"""配置审计的单元测试。

各条判据各测正反两面：死配置、配置旁路（含 docstring 排除）、编译标准锁定
（含注释里的 -std=）。构建文件全用临时文件，不依赖机器上装没装 cmake。
"""

from pathlib import Path
from types import SimpleNamespace

from cxx_standardize import audit
from cxx_standardize.errors import Code


def write(path: Path, text: str) -> Path:
    """写一份文本文件，父目录必要时逐级建出。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def referencing_source(directory: Path, keys, name="probe.py") -> Path:
    """造一份"每个键都以 cfg.<键> 形式被引用"的源码，避免死配置干扰别的判据。"""
    lines = ["def probe(cfg):"]
    lines.extend(f"    value = cfg.{key}" for key in keys)
    lines.append("    return value")
    return write(directory / name, "\n".join(lines) + "\n")


# ---------------------------------------------------------------- 检查 1: 死配置

def test_dead_config_key_is_reported(tmp_path):
    target = write(tmp_path / "script.py",
                   "def probe(cfg):\n    return cfg.alpha\n")
    code, messages = audit.run(SimpleNamespace(alpha="A", beta="B"), [target])
    assert Code.AUDIT_DEAD_KEY == code
    assert "beta" in "\n".join(messages)
    assert "alpha" not in "\n".join(messages)


def test_referenced_config_key_is_not_reported(tmp_path):
    target = write(tmp_path / "script.py",
                   "def probe(cfg):\n    return cfg.alpha, cfg.beta\n")
    code, messages = audit.run(SimpleNamespace(alpha="A", beta="B"), [target])
    assert Code.OK == code
    assert [] == messages


def test_getattr_string_counts_as_reference(tmp_path):
    """可选读法 getattr(cfg, "键", 默认) 也算有人读。"""
    target = write(
        tmp_path / "script.py",
        'def probe(cfg):\n    return getattr(cfg, "alpha", None)\n')
    code, messages = audit.run(SimpleNamespace(alpha="A"), [target])
    assert Code.OK == code, messages


# -------------------------------------------------------------- 检查 2: 配置旁路

def test_bypass_literal_points_at_file_and_line(tmp_path):
    target = write(tmp_path / "script.py",
                   'def probe(cfg):\n    return cfg.out_dir == "build/out"\n')
    code, messages = audit.run(SimpleNamespace(out_dir="build/out"), [target])
    assert Code.AUDIT_BYPASS_LITERAL == code
    assert f"{target}:2" in "\n".join(messages)
    assert "build/out" in "\n".join(messages)
    assert "out_dir" in "\n".join(messages)


def test_docstring_literal_is_not_a_bypass(tmp_path):
    """模块、函数、类体首表达式的常量都是说明文字，不算取值。"""
    target = write(tmp_path / "script.py",
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
    code, messages = audit.run(SimpleNamespace(out_dir="build/out"), [target])
    assert Code.OK == code, messages
    assert [] == messages


def test_container_values_expand_to_elements(tmp_path):
    """列表与字典的元素同样参与旁路比对。"""
    target = write(tmp_path / "script.py",
                   "def probe(cfg):\n"
                   '    return cfg.flags == "-O2" or cfg.env\n')
    config = SimpleNamespace(flags=["-O1", "-O2"], env={"MODE": "-O2"})
    code, messages = audit.run(config, [target])
    assert Code.AUDIT_BYPASS_LITERAL == code, messages
    assert "-O2" in "\n".join(messages)


def test_tool_source_is_not_a_bypass(tmp_path):
    """本包自身的实现代码不在检查 2 范围内：词汇表不等于旁路。"""
    package = Path(audit.__file__).parent
    target = write(tmp_path / "script.py", "VALUE = 1\n")
    config = SimpleNamespace(build_tool="cmake")
    code, messages = audit.run(config, [package, target])
    # 自身实现里处处是 cmake 这类词汇，但不该因此判旁路
    assert Code.OK == code, messages


# ---------------------------------------------------------- 检查 3: 编译标准锁定

def cmake_config(directory: Path, pin="14"):
    build = write(directory / "CMakeLists.txt", "")
    config = SimpleNamespace(cxx_standard=pin, root=str(directory),
                             build_file=str(build))
    target = referencing_source(directory,
                                ["cxx_standard", "root", "build_file"])
    return config, target, build


def test_cmake_correct_standard_passes(tmp_path):
    config, target, build = cmake_config(tmp_path)
    write(build, "set(CMAKE_CXX_STANDARD 14)\n")
    code, messages = audit.run(config, [target])
    assert Code.OK == code, messages


def test_cmake_missing_standard_is_reported(tmp_path):
    config, target, build = cmake_config(tmp_path)
    write(build, "project(demo)\n")
    code, messages = audit.run(config, [target])
    assert Code.AUDIT_STD_DRIFT == code
    assert "CMAKE_CXX_STANDARD" in "\n".join(messages)


def test_cmake_wrong_standard_is_reported(tmp_path):
    config, target, build = cmake_config(tmp_path)
    write(build, "set(CMAKE_CXX_STANDARD 11)\n")
    code, messages = audit.run(config, [target])
    assert Code.AUDIT_STD_DRIFT == code


def test_cmake_comment_std_example_is_reported(tmp_path):
    """注释里的示例也会被构建系统当真，因此同样判红。"""
    config, target, build = cmake_config(tmp_path)
    write(build, "# 示例: -std=c++11\nset(CMAKE_CXX_STANDARD 14)\n")
    code, messages = audit.run(config, [target])
    assert Code.AUDIT_STD_DRIFT == code
    assert "c++11" in "\n".join(messages)


def test_std_pin_normalisation_accepts_equivalent_forms(tmp_path):
    """c++11 与 11 是同一个标准，不该被当成漂移。"""
    config, target, build = cmake_config(tmp_path, pin="c++11")
    write(build, "set(CMAKE_CXX_STANDARD 11)\n")
    code, messages = audit.run(config, [target])
    assert Code.OK == code, messages


# ------------------------------------------------------------------ 对象与出口

def test_targets_recurse_and_keep_only_python(tmp_path):
    """目录递归取 *.py；非 py 文件既不算引用，也不参与旁路比对。"""
    write(tmp_path / "pkg" / "deep" / "a.py",
          "def probe(cfg):\n    return cfg.secret\n")
    write(tmp_path / "pkg" / "deep" / "note.txt", 'X = "S3CR3T"\n')
    code, messages = audit.run(SimpleNamespace(secret="S3CR3T"), [tmp_path])
    assert Code.OK == code, messages
    assert [] == messages


def test_default_targets_covers_package_and_config(tmp_path):
    """缺省对象包含本包自身的实现代码与项目配置。"""
    config = SimpleNamespace(root=str(tmp_path))
    files = [str(item) for item in
             audit.collect_files(audit.default_targets(config))]
    assert any(item.endswith("audit.py") for item in files)
    assert any(item.endswith("config.py") for item in files)


def test_no_audit_switch_exists():
    """--no-audit 是入口的调试开关。"""
    from cxx_standardize import cli
    parser = cli.build_parser()
    assert parser.parse_args(["--no-audit", "check"]).no_audit is True
    assert parser.parse_args(["check"]).no_audit is False
