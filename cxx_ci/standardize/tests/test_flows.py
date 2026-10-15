"""风格与排版的完整流程。

用假 clang 工具驱动，断言的是**流程与判定**：登记清单命中不计失败、口径行给出
源/头文件分布、一次性切换要确认、基线指纹变化要重新确认、修复破坏构建要回退。
"""

import shutil
import subprocess

import pytest
from std_support import Project


def _clang_format_ok():
    binary = shutil.which("clang-format")
    if not binary:
        return False
    try:
        text = subprocess.run([binary, "--version"], capture_output=True,
                              text=True, check=False).stdout
    except OSError:
        return False
    for token in text.replace("-", ".").split():
        head = token.split(".")[0]
        if head.isdigit():
            return int(head) >= 17
    return False


NEEDS_CLANG_FORMAT = pytest.mark.skipif(
    not _clang_format_ok(), reason="需要 clang-format 17 以上")

TIDY_BODY = """#!/bin/sh
for arg in "$@"; do
  case "$arg" in
    --export-fixes=*) fixes="${arg#--export-fixes=}" ;;
  esac
done
echo 'LLVM version 17.0.6'
printf '%s' "$FIXES_BODY" > "$fixes"
exit 1
"""


def findings_yaml(entries, root):
    """造一份 --export-fixes 结果。"""
    rows = []
    for path, offset, name in entries:
        rows.append(
            f"  - DiagnosticName:  {name}\n"
            "    DiagnosticMessage:\n"
            "      Message:         invalid case style\n"
            f"      FilePath:        {path}\n"
            f"      FileOffset:      {offset}\n"
            "      Replacements:    []\n"
            "    Level:           Warning\n"
            f"    BuildDirectory:  {root}\n")
    return "---\nMainSourceFile:  x\nDiagnostics:\n" + "".join(rows) + "...\n"


def fixes_yaml(entries, root):
    """造一份带真实替换的 --export-fixes 结果。

    新的原子流程直接按字节偏移应用替换，因此只报诊断、不带 Replacements 的
    结果等于"没有可改的东西"。
    """
    rows = []
    for path, offset, length, text, name in entries:
        rows.append(
            f"  - DiagnosticName:  {name}\n"
            "    DiagnosticMessage:\n"
            "      Message:         invalid case style\n"
            f"      FilePath:        {path}\n"
            f"      FileOffset:      {offset}\n"
            "      Replacements:\n"
            f"        - FilePath:        {path}\n"
            f"          Offset:          {offset}\n"
            f"          Length:          {length}\n"
            f"          ReplacementText: {text}\n"
            "    Level:           Warning\n"
            f"    BuildDirectory:  {root}\n")
    return "---\nMainSourceFile:  x\nDiagnostics:\n" + "".join(rows) + "...\n"




def style_env(project, entries):
    directory = project.fake_tool("clang-tidy", TIDY_BODY)
    return directory, project.env(directory), findings_yaml(entries,
                                                            project.path)


def test_style_reports_scope_and_counts(project):
    source = project.write("src/a.cpp", "int main() { int Bad = 1; return Bad; }\n")
    header = project.write("src/a.h", "struct Holder { int Bad; };\n")
    _directory, env, _ = style_env(project, [
        (str(source), 12, "readability-identifier-naming"),
        (str(header), 18, "readability-identifier-naming"),
    ])
    env["FIXES_BODY"] = findings_yaml(
        [(str(source), 12, "readability-identifier-naming"),
         (str(header), 18, "readability-identifier-naming")], project.path)
    # 只处理一个文件：假 clang-tidy 对每次调用回同一份结果，多跑几个文件会
    # 把同一条诊断重复计上
    code, out, err = project.run(["style", "--paths", "src/a.cpp"], env=env)
    assert 1 == code, (out, err)
    assert "风格诊断 2 条（去重后；源文件 1 / 头文件 1）" in out
    assert "涉及 2 个文件（源 1 / 头 1）" in out


def test_registered_finding_passes_but_is_shown(project):
    source = project.write("src/a.cpp", "int main() { int Bad = 1; return Bad; }\n")
    project.write("standardize.xfail",
                  "[style]\nsrc/a.cpp:readability-identifier-naming\n")
    _directory, env, body = style_env(project, [
        (str(source), 12, "readability-identifier-naming")])
    env["FIXES_BODY"] = body
    code, out, err = project.run(["style"], env=env)
    assert 0 == code, (out, err)
    assert "(xfail)" in out
    assert "全部文件已符合风格" in out


def test_unregistered_finding_still_fails(project):
    source = project.write("src/a.cpp", "int main() { int Bad = 1; return Bad; }\n")
    project.write("standardize.xfail",
                  "[style]\nsrc/a.cpp:bugprone-branch-clone\n")
    _directory, env, body = style_env(project, [
        (str(source), 12, "readability-identifier-naming")])
    env["FIXES_BODY"] = body
    code, _out, _err = project.run(["style"], env=env)
    assert 1 == code


def test_strict_mode_rejects_a_stale_entry(project):
    source = project.write("src/a.cpp", "int main() { return 0; }\n")
    project.write("standardize.xfail",
                  "[style]\nsrc/a.cpp:bugprone-branch-clone\n")
    _directory, env, body = style_env(project, [
        (str(source), 12, "readability-identifier-naming")])
    env["FIXES_BODY"] = body
    project.configure(
        project.read("std/config/config.py") + "\nXFAIL_STRICT = True\n")
    code, out, err = project.run(["style"], env=env)
    assert 1 == code
    assert "可移除" in out or "可移除" in err


@NEEDS_CLANG_FORMAT
def test_format_preview_then_accept_baseline(project):
    """用真实 clang-format：排版本来就是本项目的硬依赖，假工具验不了这一层。"""
    project.write("src/a.cpp", "int main() { return 0; }\n")
    code, out, err = project.run(["format", "--accept-baseline", "--yes"])
    assert 0 == code, (out, err)

    project.write("src/a.cpp", "int   main()   { return 0; }\n")
    code, out, err = project.run(["format"])
    assert 1 == code, (out, err)
    assert "需要排版" in err or "需要排版" in out

    code, out, err = project.run(["format", "--apply"])
    assert 0 == code, (out, err)
    assert project.read("src/a.cpp") == "int main() { return 0; }\n"


@NEEDS_CLANG_FORMAT
def test_baseline_change_requires_reconfirmation(project):
    """工具版本、注释模式、配置内容任一变化都要重新确认，而不是当成代码问题。"""
    code, out, err = project.run(["format", "--accept-baseline", "--yes"])
    assert 0 == code, (out, err)
    project.configure(project.read("std/config/config.py").replace(
        'FORMAT_COMMENTS = "placeholder"', 'FORMAT_COMMENTS = "protect-column"'))
    code, out, err = project.run(["format"])
    assert 2 == code
    assert "基线已变" in err


@NEEDS_CLANG_FORMAT
def test_missing_format_config_file_fails_loudly(project):
    """配置点名却不存在时明确失败，不退回 clang-format 的向上查找。"""
    code, _out, err = project.run(
        ["format", "--config-file", "/nonexistent/.clang-format"])
    assert 2 == code
    assert "排版配置文件不存在" in err


def test_style_apply_rolls_back_when_verification_fails(project):
    """修复破坏了构建就整体回退到处理前的字节快照。"""
    source = project.write("src/a.cpp", "int main() { int Bad = 1; return Bad; }\n")
    before = source.read_text(encoding="utf-8")
    _directory, env, _ = style_env(project, [
        (str(source), 12, "readability-identifier-naming")])
    env["FIXES_BODY"] = fixes_yaml(
        [(str(source), 12, 3, "t_Bad", "readability-identifier-naming")],
        project.path)
    # 验证指令必然失败
    project.configure(project.read("std/config/config.py").replace(
        'VERIFY_COMMAND = ["true"]', 'VERIFY_COMMAND = ["false"]'))
    code, out, err = project.run(["style", "--apply"], env=env)
    # 修复没成功就是没成功，不能报 0：与"跳过不等于通过"同一条纪律
    assert 1 == code, (out, err)
    assert "整体回滚" in err
    assert before == source.read_text(encoding="utf-8")


def test_prepare_failure_is_reported_not_swallowed(project):
    """派生文件生成失败要走 stderr：缺了 moc_/ui_，含 .moc 的翻译单元会被判成
    编译错误，分析结果因此不完整，不能只落在日志里。"""
    project.write("build/Makefile", "moc_thing.cpp:\n\t@false\n")
    source = project.write("src/a.cpp", "int main() { return 0; }\n")
    _directory, env, body = style_env(project, [
        (str(source), 12, "readability-identifier-naming")])
    env["FIXES_BODY"] = body
    _code, out, err = project.run(["style", "--paths", "src/a.cpp"], env=env)
    assert "派生文件生成失败" in err, (out, err)


class TestToolVersion:
    """版本行不能只看第一行：发行版与上游构建印的位置不同。"""

    def _fake(self, project, name, output):
        directory = project.fake_tool(name, (
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "if '--version' in sys.argv:\n"
            "    print(" + repr(output) + ")\n"
            "else:\n"
            "    sys.stdout.write(sys.stdin.read())\n"))
        return directory / name

    def test_distro_layout_reads_first_line(self, project):
        from cxx_standardize import fmt
        binary = self._fake(project, "clang-tidy",
                            "Ubuntu LLVM version 17.0.6\n  Optimized build.")
        assert (17, ) == fmt.version(binary)[:1]
        assert "17.0.6" in fmt.version_text(binary)

    def test_upstream_layout_reads_second_line(self, project):
        from cxx_standardize import fmt
        binary = self._fake(project, "clang-tidy",
                            "LLVM (http://llvm.org/):\n  LLVM version 17.0.1\n")
        assert (17, ) == fmt.version(binary)[:1]
        assert "17.0.1" in fmt.version_text(binary)

    def test_unrunnable_binary_says_so(self, project):
        """跑不起来与"认不出版本"要分开报：前者多半是缺动态库。"""
        from cxx_standardize import fmt
        binary = project.path / "missing" / "clang-format"
        code, message = fmt.version(binary)[1:]
        assert fmt.Code.COMMAND_UNRUNNABLE == code
        assert "无法执行" in message


def test_format_covers_headers_too(project):
    """clang-format 没有 HeaderFilterRegex 那样的机制。

    风格侧的头文件由 clang-tidy 顺带处理，排版侧不直接收就永远不会被排到。
    """
    project.write("src/a.h", "int f();\n")
    project.write("src/a.cpp", "int f() { return 0; }\n")
    log = project.path / "formatted.txt"
    directory = project.fake_tool("clang-format", (
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('clang-format version 17.0.6')\n"
        "    raise SystemExit(0)\n"
        "for arg in sys.argv[1:]:\n"
        "    if arg.startswith('--assume-filename='):\n"
        "        with open(os.environ['FORMAT_LOG'], 'a', encoding='utf-8') as log:\n"
        "            log.write(os.path.basename(arg.split('=', 1)[1]) + '\\n')\n"
        "sys.stdout.write(sys.stdin.read())\n"))
    env = project.env(directory)
    env["FORMAT_LOG"] = str(log)
    code, out, err = project.run(["format"], env=env)
    assert 0 == code, (out, err)
    # 每个文件会被排两遍（幂等自检），因此按去重后的集合断言
    formatted = sorted(set(log.read_text(encoding="utf-8").split()))
    assert ["a.cpp", "a.h"] == formatted, formatted


class TestFrozenProtection:
    """禁改区写保护：黑名单只管"分析谁"，不管"写到哪"。

    只按命令行目标判断拦不住间接写入——目标在界内、fix-it 落点却穿透到界外的
    头文件。三件套：写前查落点、写前快照、写后比对。
    """

    def _setup(self, project, declare_target):
        frozen = project.write("frozen/lib.h", "int f;\n")
        source = project.write("src/a.cpp", "int main() { return 0; }\n")
        # 未声明落点时给一处**真替换**（落在源文件自己身上）：这个用例要验的是
        # "没有禁改区时修复能落地"，给空替换会让它变成"有诊断但改不动"，
        # 那是另一条判定（不提供修复块 = 不算通过）
        replacements = ("      Replacements:\n"
                        "        - FilePath:        {}\n"
                        "          Offset:          0\n"
                        "          Length:          1\n"
                        "          ReplacementText: b\n".format(frozen)
                        if declare_target else
                        "      Replacements:\n"
                        "        - FilePath:        {}\n"
                        "          Offset:          0\n"
                        "          Length:          1\n"
                        "          ReplacementText: i\n".format(source))
        body = ("---\nMainSourceFile:  x\nDiagnostics:\n"
                "  - DiagnosticName:  readability-identifier-naming\n"
                "    DiagnosticMessage:\n"
                "      Message:         m\n"
                "      FilePath:        {}\n"
                "      FileOffset:      0\n"
                "{}"
                "    Level:           Warning\n"
                "    BuildDirectory:  {}\n".format(source, replacements,
                                                     project.path))
        tidy_dir = project.fake_tool("clang-tidy", TIDY_BODY)
        env = project.env(tidy_dir)
        env["FIXES_BODY"] = body
        project.configure(project.read("std/config/config.py").replace(
            "FROZEN = []", 'FROZEN = [ROOT / "frozen"]'))
        return frozen, source, env

    def test_declared_target_in_frozen_is_refused_before_writing(self, project):
        frozen, source, env = self._setup(project, declare_target=True)
        before = frozen.read_bytes()
        code, out, err = project.run(["style", "--apply"], env=env)
        assert 1 == code, (out, err)
        assert "不该写的地方" in err and "禁改区" in err
        assert before == frozen.read_bytes()

    # 注：替换由本工具按字节偏移直写，落点只能是替换块声明的那些文件，
    # 写不到别处去，因此不需要"写后快照比对"来兜"写到未声明文件"这类问题。

    def test_blacklisted_target_is_refused_too(self, project):
        """黑名单是"不必分析"，但 fix-it 落到那里同样是越界写入。"""
        frozen, source, env = self._setup(project, declare_target=True)
        project.configure(project.read("std/config/config.py").replace(
            'FROZEN = [ROOT / "frozen"]', "FROZEN = []").replace(
            "BLACKLIST = []", 'BLACKLIST = [ROOT / "frozen"]'))
        before = frozen.read_bytes()
        code, out, err = project.run(["style", "--apply"], env=env)
        assert 1 == code, (out, err)
        assert "黑名单" in err
        assert before == frozen.read_bytes()

    def test_without_frozen_the_apply_goes_through(self, project):
        frozen, source, env = self._setup(project, declare_target=False)
        project.configure(project.read("std/config/config.py").replace(
            'FROZEN = [ROOT / "frozen"]', "FROZEN = []"))
        code, out, err = project.run(["style", "--apply"], env=env)
        assert 0 == code, (out, err)


class TestCommentRestoreShape:
    """行尾注释 + 独立左括号：排版还原不得把代码变成注释的一部分。

    要盖住的形状：构造函数带初始化列表、那一行有行尾注释、函数体左括号独占
    一行。占位符如果统一用块注释，clang-format 会把下一行的 `{` 挪到占位符
    右边，还原后就成了 `... // 单位:毫秒 {}`——左括号被注释吃掉，再排一次
    结果又变，于是被幂等自检判成"基线不可复现"而永久拒绝。
    """

    SHAPE = """#ifndef INCLUDE_CALC_H
#define INCLUDE_CALC_H

class Calc
{
public:
    explicit Calc(int v) : m_v(v)      // 单位:毫秒
    {
    }

private:
    int m_v;
};

#endif  // INCLUDE_CALC_H
"""

    def _project(self, project, mode):
        project.write("include/Calc.h", self.SHAPE)
        project.write(".clang-format",
                      "BasedOnStyle: LLVM\nIndentWidth: 4\n"
                      "BreakBeforeBraces: Allman\n"
                      "AlignTrailingComments: true\n")
        project.configure(project.read("std/config/config.py").replace(
            'FORMAT_COMMENTS = "protect-column"',
            'FORMAT_COMMENTS = "{}"'.format(mode)))
        return project.path / "include" / "Calc.h"

    @NEEDS_CLANG_FORMAT
    def test_apply_succeeds_and_keeps_the_brace_on_its_own_line(self, project):
        target = self._project(project, "protect-column")
        code, out, err = project.run(["format", "--apply"])
        assert 0 == code, (out, err)
        text = target.read_text(encoding="utf-8")
        # 左括号必须在注释之后另起一行，而不是被注释吃掉
        assert "// 单位:毫秒" in text
        assert "// 单位:毫秒 {}" not in text
        line = [item for item in text.splitlines()
                if "单位:毫秒" in item][0]
        assert line.rstrip().endswith("// 单位:毫秒")
        assert [item.strip() for item in text.splitlines()
                if item.strip() == "{"]

    @NEEDS_CLANG_FORMAT
    def test_second_run_changes_nothing(self, project):
        target = self._project(project, "protect-column")
        code, out, err = project.run(["format", "--apply"])
        assert 0 == code, (out, err)
        first = target.read_bytes()
        code, out, err = project.run(["format", "--apply"])
        assert 0 == code, (out, err)
        assert first == target.read_bytes()
        code, out, err = project.run(["format"])
        assert 0 == code, (out, err)
        assert "全部文件已符合排版" in out

    @NEEDS_CLANG_FORMAT
    def test_comment_column_is_preserved(self, project):
        target = self._project(project, "protect-column")
        before = [item for item in self.SHAPE.splitlines()
                  if "单位:毫秒" in item][0].index("//")
        code, out, err = project.run(["format", "--apply"])
        assert 0 == code, (out, err)
        after = [item for item in target.read_text(
            encoding="utf-8").splitlines() if "单位:毫秒" in item][0].index("//")
        assert before == after

    @NEEDS_CLANG_FORMAT
    def test_accept_baseline_works_on_this_shape(self, project):
        """一次性切换入口不能被同一个形状挡死。"""
        self._project(project, "protect-column")
        code, out, err = project.run(["format", "--accept-baseline", "--yes"])
        assert 0 == code, (out, err)
        code, out, err = project.run(["format"])
        assert 0 == code, (out, err)

    @NEEDS_CLANG_FORMAT
    def test_none_mode_trusts_clang_format(self, project):
        """none 完全信任 clang-format：不还原列号，也不因列号变化拒绝。

        这是唯一会动注释列的模式，文档明说；这里钉住的是"它不会因为动列而
        拒绝文件"，以及它同样不会让代码落到注释后面（行注释本身就挡住了）。
        """
        target = self._project(project, "none")
        code, out, err = project.run(["format", "--apply"])
        assert 0 == code, (out, err)
        text = target.read_text(encoding="utf-8")
        assert "单位:毫秒" in text
        line = [item for item in text.splitlines() if "单位:毫秒" in item][0]
        assert line.rstrip().endswith("// 单位:毫秒")


class TestStyleCleanupFormatting:
    """风格修复的产出必须同时合项目排版。

    clang-tidy 的 fix-it 只管语法对：给 `if` 补大括号得到的是 K&R 位置、闭括号
    掉回第 0 列。工程用的是 Allman 花括号时，这段文本立刻被排版检查判红——风格
    修复自己制造了排版违规。收尾排版就是为这一步存在的。
    """

    SOURCE = """int s_counter = 0;

int probe(int bad_local)
{
    if (bad_local > 0)
        return bad_local + s_counter;                       // 注释
    return bad_local;
}
"""

    def _project(self, project):
        project.write("src/probe.cpp", self.SOURCE)
        project.write(".clang-format",
                      "BasedOnStyle: LLVM\nIndentWidth: 4\nColumnLimit: 120\n"
                      "BreakBeforeBraces: Allman\nAlignTrailingComments: true\n")
        project.write(".clang-tidy",
                      "Checks: 'readability-braces-around-statements'\n"
                      "CheckOptions:\n"
                      "  - key: readability-braces-around-statements."
                      "ShortStatementLines\n    value: '0'\n")
        return project.path / "src" / "probe.cpp"

    @NEEDS_CLANG_FORMAT
    def test_style_apply_leaves_code_formatted(self, project):
        target = self._project(project)
        code, out, err = project.run(["style", "--apply"])
        assert 0 == code, (out, err)
        text = target.read_text(encoding="utf-8")
        # 补上的大括号必须独占一行（Allman），闭合括号缩进对齐
        assert "    if (bad_local > 0)\n    {\n" in text
        assert "\n}\n    return bad_local;" not in text
        # 行尾注释仍在原列，且没有被代码挤走
        line = [item for item in text.splitlines() if "注释" in item][0]
        assert line.rstrip().endswith("// 注释")
        before = [item for item in self.SOURCE.splitlines()
                  if "注释" in item][0].index("//")
        assert before == line.index("//")


class TestHeaderFilter:
    def test_injected_on_the_command_line(self, project):
        """命令行优先于项目 .clang-tidy：不强制就能被改宽或改窄。"""
        from cxx_standardize import tidy

        class Stub:
            build_dir = str(project.path / "build")
            clang_tidy_extra_args = []
            tidy_header_filter = "include/proj/.*"

        cmd = tidy.command("/usr/bin/clang-tidy", Stub(), "", "a.cpp", "f.yaml")
        assert "--header-filter=include/proj/.*" in cmd

    def test_absent_when_not_configured(self, project):
        from cxx_standardize import tidy

        class Stub:
            build_dir = str(project.path / "build")
            clang_tidy_extra_args = []
            tidy_header_filter = ""

        cmd = tidy.command("/usr/bin/clang-tidy", Stub(), "", "a.cpp", "f.yaml")
        assert not any(item.startswith("--header-filter") for item in cmd)

    def test_targets_are_read_from_the_fixes_file(self, project):
        """落点取自替换块（DiagnosticMessage.Replacements[].FilePath）。"""
        from cxx_standardize import tidy
        path = project.write("fixes.yaml", """\
MainSourceFile:  x
Diagnostics:
  - DiagnosticName:  readability-identifier-naming
    DiagnosticMessage:
      Message:         m
      FilePath:        /proj/a.cpp
      FileOffset:      0
      Replacements:
        - FilePath:        /proj/a.cpp
          Offset:          0
          Length:          1
          ReplacementText: b
        - FilePath:        /proj/b.h
          Offset:          3
          Length:          1
          ReplacementText: c
    Level:           Warning
""")
        found, code, message = tidy.read_fixes(path)
        assert 0 == code, message
        assert {"/proj/a.cpp", "/proj/b.h"} == {str(item)
                                               for item in found[0].targets}


