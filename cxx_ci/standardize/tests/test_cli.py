"""命令行契约：退出码、配置解析、动作选择。

退出码是对外契约的一部分（见 CONTRACT.md）：0 无需改动、1 有待改动或检查未
通过、2 工具或配置错误。这三档必须稳，编排脚本按它决定后续动作。
"""

import pytest
from std_support import ECHO_FORMAT, Project


@pytest.fixture
def project():
    box = Project()
    yield box
    box.dispose()


def test_version_prints_unified_line(project):
    """--version 与 ci.py --version 同一形状：<项目> <版本> contract <N> python >=<下限>。

    不需要配置，因此要在加载配置之前就返回。
    """
    code, out, _err = project.run(["--version"])
    assert 0 == code
    assert "cxx-standardize" in out
    assert "contract " in out and "python >=" in out


def test_missing_config_is_a_config_error(project):
    """配置缺失是环境问题（2），不是"有待改动"（1）。"""
    code, _out, err = project.run(
        ["check", "--config", str(project.path / "nope" / "config.py")])
    assert 2 == code
    assert "找不到配置文件" in err


def test_unknown_action_is_rejected(project):
    code, _out, _err = project.run(["frobnicate"])
    assert 2 == code


def test_contract_mismatch_exits_2(project):
    """配置声明的契约版本与工具不一致：拒绝执行，退出码 2。

    契约校验下移到各侧入口后，只用 cxx-standardize 也能发现配置与工具取自不同代。
    """
    text = project.read("std/config/config.py").replace(
        "CONTRACT_VERSION = 3", "CONTRACT_VERSION = 1")
    project.configure(text)
    code, _out, err = project.run(["check"])
    assert 2 == code
    assert "契约版本" in err


def test_contract_declaration_may_be_omitted(project):
    """不声明契约版本时静默通过，取工具缺省（工具侧靠其他方式发现不同代）。"""
    text = "\n".join(
        line for line in project.read("std/config/config.py").splitlines()
        if not line.startswith("CONTRACT_VERSION"))
    project.configure(text + "\n")
    code, _out, err = project.run(["check"])
    # 只有契约声明被去掉，其余不变：不是"因为契约"失败即可
    assert "契约版本" not in err


def test_apply_naming_without_apply_is_rejected(project):
    """--apply-naming 是 --apply 的范围开关，单用是空操作，必须当场拒绝。

    不拒绝的代价是误导诊断方向：满屏诊断 + 非零退出码 + 一个字节没写，使用者
    会读成"命名改不动"，进而怀疑整个命名功能。
    """
    _code, out, err = project.run(["style", "--apply-naming"])
    assert 2 == _code, (out, err)
    assert "必须与 --apply 同用" in err


def test_all_apply_enables_naming_without_the_flag(project):
    """all --apply 的语义就是三件事一次做完，命名不需要额外开关。"""
    project.write("src/a.cpp", "int main() { return 0; }\n")
    _code, _out, err = project.run(["all", "--apply", "--paths", "src/a.cpp"])
    assert "必须与 --apply 同用" not in err


def test_yes_without_accept_baseline_is_rejected(project):
    code, _out, err = project.run(["format", "--yes"])
    assert 2 == code
    assert "--yes 只用于" in err


def test_accept_baseline_with_style_is_rejected(project):
    code, _out, err = project.run(["style", "--accept-baseline"])
    assert 2 == code
    assert "只对排版有意义" in err


def test_missing_tool_is_a_config_error(project):
    """缺 clang-format 是环境问题（2），不是"有待改动"。

    这里额外断言**确实收到过待排版文件**：文件集为空时流程会先返回 0，工具
    存在性检查根本没跑，"缺工具"就伪装成了"一切正常"。约束住这一点，这条
    断言在新解释器上依然可证伪。
    """
    project.write("src/a.cpp", "int main() { return 0; }\n")
    code, _out, err = project.run(["format"], env={"PATH": "/nonexistent"})
    assert 2 == code
    assert "clang-format" in err


def test_check_runs_both_actions(project):
    directory = project.fake_tool("clang-format", ECHO_FORMAT)
    tidy = directory / "clang-tidy"
    tidy.write_text("#!/bin/sh\necho 'LLVM version 17.0.6'\nexit 0\n",
                    encoding="utf-8")
    tidy.chmod(0o755)
    _code, out, _err = project.run(["check"], env=project.env(directory))
    assert "== style ==" in out
    assert "== format ==" in out
