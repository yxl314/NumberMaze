#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段脚本的集成测试：clean、build、build_test、run_test。"""

import os
import shutil
import subprocess
import sys
import tempfile
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helper import CONFIG_TEMPLATE, SandboxCase

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lib import errors

HAS_CMAKE = (shutil.which("cmake") or shutil.which("cmake3")) is not None
HAS_CXX = (shutil.which("g++") or shutil.which("c++")) is not None
HAS_VALGRIND = shutil.which("valgrind") is not None
NEEDS_TOOLCHAIN = pytest.mark.skipif(
    not (HAS_CMAKE and HAS_CXX), reason="需要 cmake 与 C++ 编译器")
NEEDS_VALGRIND = pytest.mark.skipif(
    not HAS_VALGRIND, reason="需要 valgrind")
QMAKE_TOOL = ("qmake6" if shutil.which("qmake6")
              else "qmake" if (shutil.which("qmake-qt5")
                               or shutil.which("qmake")) else None)
NEEDS_QMAKE = pytest.mark.skipif(
    not QMAKE_TOOL, reason="需要 qmake")
NEEDS_MAKE = pytest.mark.skipif(
    shutil.which("make") is None, reason="需要 make")

# 最小可构建项目：产出一个总是成功的 run_tests
CMAKE = """\
cmake_minimum_required(VERSION 3.5)
project(probe CXX)
add_executable(run_tests main.cpp)
"""

# 等价的最小 qmake 工程，不依赖 Qt 库
PRO = """\
TEMPLATE = app
TARGET = run_tests
CONFIG -= qt
QT -= core gui
SOURCES += main.cpp
"""

MAIN = "int main() { return 0; }\n"


class TestStage(SandboxCase):
    def setUp(self):
        self.box.reset()
        self.box.configure(CONFIG_TEMPLATE)

    def write(self, relative, text):
        path = self.box.path / relative
        if not path.parent.is_dir():
            path.parent.mkdir(parents=True)
        with open(str(path), "w") as handle:
            handle.write(text)
        return path

    def make_project(self):
        """在沙箱内造一个主项目与测试项目。"""
        self.write("CMakeLists.txt", CMAKE)
        self.write("main.cpp", MAIN)
        self.write("test/CMakeLists.txt", CMAKE)
        self.write("test/main.cpp", MAIN)

    def test_clean_removes_build_contents(self):
        self.write("build/test/artifact", "x")
        code, out, err = self.box.run("stages/clean.py", [])
        assert 0 == code
        assert not (self.box.path / "build" / "test").exists()

    def test_clean_keeps_compilation_database(self):
        database = self.write("build/compile_commands.json", "[]")
        self.write("build/artifact", "x")
        code, out, err = self.box.run("stages/clean.py", [])
        assert 0 == code
        assert database.is_file()
        assert not (self.box.path / "build" / "artifact").exists()

    def test_clean_is_idempotent(self):
        code, out, err = self.box.run("stages/clean.py", [])
        assert 0 == code

    def test_clean_refuses_path_outside_project(self):
        outside = tempfile.mkdtemp(prefix="cxx_ci_outside_")
        self.box.configure(CONFIG_TEMPLATE.replace(
            '\nBUILD_DIR = ROOT / "build"\n',
            '\nBUILD_DIR = Path("{}")\n'.format(outside)))
        code, out, err = self.box.run("stages/clean.py", [])
        assert code != 0
        assert '拒绝删除' in err
        assert os.path.isdir(outside)

    def test_build_reports_missing_build_file(self):
        code, out, err = self.box.run("stages/build.py", [])
        assert code != 0
        assert '构建文件不存在' in err

    def make_qmake_project(self):
        """在沙箱内造一个主项目与测试项目（qmake 版）。"""
        self.write("app.pro", PRO)
        self.write("main.cpp", MAIN)
        self.write("test/test.pro", PRO)
        self.write("test/main.cpp", MAIN)

    @NEEDS_QMAKE
    def test_qmake_project_builds_and_runs(self):
        self.make_qmake_project()
        self.box.configure(CONFIG_TEMPLATE
                           .replace('BUILD_TOOL = "cmake"',
                                    'BUILD_TOOL = "{}"'.format(QMAKE_TOOL))
                           .replace('\nBUILD_FILE = ROOT / "CMakeLists.txt"\n',
                                    '\nBUILD_FILE = ROOT / "app.pro"\n')
                           .replace('\nTEST_BUILD_FILE = ROOT / "test" / "CMakeLists.txt"\n',
                                    '\nTEST_BUILD_FILE = ROOT / "test" / "test.pro"\n'))
        code, out, err = self.box.run("stages/build_test.py", [])
        assert 0 == code
        binary = self.box.path / "build" / "test" / "run_tests"
        assert binary.is_file()
        code, out, err = self.box.run("stages/run_test.py", [])
        assert 0 == code

    @NEEDS_TOOLCHAIN
    def test_build_produces_build_directory(self):
        self.make_project()
        code, out, err = self.box.run("stages/build.py", [])
        assert 0 == code
        assert (self.box.path / 'build').is_dir()

    @NEEDS_TOOLCHAIN
    def test_build_test_then_run_test(self):
        self.make_project()
        code, out, err = self.box.run("stages/build_test.py", [])
        assert 0 == code
        binary = self.box.path / "build" / "test" / "run_tests"
        assert binary.is_file()
        code, out, err = self.box.run("stages/run_test.py", [])
        assert 0 == code
        assert 'run_tests' in out

    def make_valgrind_probe(self, source):
        """编译一个探针程序，并让测试指令指向它。"""
        workdir = self.box.path / "build" / "test"
        self.make_memcheck_target(workdir)
        src = self.write("probe.cpp", source)
        binary = workdir / "probe"
        compiler = shutil.which("g++") or shutil.which("c++")
        assert 0 == subprocess.call([compiler, '-g', '-O0', str(src), '-o', str(binary)])
        self.box.configure(CONFIG_TEMPLATE.replace(
            'TEST_COMMAND = [TEST_BUILD_DIR / "run_tests"]',
            'TEST_COMMAND = ["{}"]'.format(binary)))

    def test_memcheck_reports_missing_workdir(self):
        code, out, err = self.box.run("stages/memcheck.py", [])
        assert code != 0
        assert '测试工作目录不存在' in err

    def test_memcheck_reports_missing_executable(self):
        """内存检查指向的可执行文件不存在时，要说清是"先跑 build"。

        不这么做的后果是：valgrind 跑了一个不存在的文件，报出来的是一句
        "未产出结构化报告"，跟真正的原因隔了好几层。
        """
        workdir = self.box.path / "build" / "test"
        workdir.mkdir(parents=True)
        code, out, err = self.box.run("stages/memcheck.py", [])
        assert code != 0
        assert "内存检查的可执行文件不存在" in err
        assert "MEMCHECK_COMMAND" in err

    def test_memcheck_reports_missing_valgrind(self):
        workdir = self.box.path / "build" / "test"
        self.make_memcheck_target(workdir)
        self.make_memcheck_target(workdir)
        code, out, err = self.box.run("stages/memcheck.py", [],
                                      env={"PATH": "/nonexistent"})
        assert code != 0
        assert '找不到 valgrind' in err

    @NEEDS_VALGRIND
    def test_memcheck_passes_clean_program(self):
        self.make_valgrind_probe("int main() { return 0; }\n")
        code, out, err = self.box.run("stages/memcheck.py", [])
        assert 0 == code
        # 出了 XML 就没有 valgrind 的文本汇总，判定看归因摘要
        assert '域内问题 0 处' in out
        assert 'memcheck.xml' in out

    @NEEDS_VALGRIND
    def test_memcheck_catches_leak(self):
        self.make_valgrind_probe("int main() { new int[10]; return 0; }\n")
        code, out, err = self.box.run("stages/memcheck.py", [])
        assert code != 0
        assert 'definitely lost' in out

    def fake_tool(self, name, version):
        """造一个只报告指定版本号的假工具，返回其所在目录。"""
        root = tempfile.mkdtemp(prefix="cxx_ci_fake_")
        path = os.path.join(root, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("#!{}\nprint('LLVM version {}')\n".format(
                sys.executable, version))
        os.chmod(path, 0o755)
        return root

    def test_run_test_reports_missing_workdir(self):
        code, out, err = self.box.run("stages/run_test.py", [])
        assert code != 0
        assert '测试工作目录不存在' in err

    def test_run_test_uses_configured_workdir(self):
        workdir = self.box.path / "build" / "test"
        self.make_memcheck_target(workdir)
        self.box.configure(CONFIG_TEMPLATE.replace(
            'TEST_COMMAND = [TEST_BUILD_DIR / "run_tests"]',
            'TEST_COMMAND = ["{}", "-c", "import os; print(os.getcwd())"]'.format(
                sys.executable)))
        code, out, err = self.box.run("stages/run_test.py", [])
        assert 0 == code
        assert os.path.realpath(str(workdir)) in out

    def test_run_test_accepts_command_from_path(self):
        """测试指令可以是 PATH 上的命令，不必是当前目录下的文件。"""
        workdir = self.box.path / "build" / "test"
        self.make_memcheck_target(workdir)
        self.box.configure(CONFIG_TEMPLATE.replace(
            'TEST_COMMAND = [TEST_BUILD_DIR / "run_tests"]',
            'TEST_COMMAND = ["true"]'))
        code, out, err = self.box.run("stages/run_test.py", [])
        assert 0 == code, err

    def test_run_test_propagates_failure(self):
        workdir = self.box.path / "build" / "test"
        self.make_memcheck_target(workdir)
        self.box.configure(CONFIG_TEMPLATE.replace(
            'TEST_COMMAND = [TEST_BUILD_DIR / "run_tests"]',
            'TEST_COMMAND = ["{}", "-c", "raise SystemExit(7)"]'.format(
                sys.executable)))
        code, out, err = self.box.run("stages/run_test.py", [])
        # 子进程的退出状态由具体错误码取代，不再原样透传
        assert errors.status(errors.TEST_FAILED) == code




class TestCleanFrozen(SandboxCase):
    """禁改区贯通到 ci 侧：冻结库的产物不能被 clean 删掉。

    standardize 侧有 FROZEN（绝不可写），ci 侧原本没有对应概念——清理阶段
    不设防，vendored 冻结库的构建产物会被一并清掉，而那正是参与主工程链接的
    东西。两侧共用同名同语义的配置项（契约版本 +1）。
    """

    def test_frozen_products_survive_clean(self):
        """冻结库的产物常常就落在构建树里（vendored 依赖就地构建）。

        这种才是真会被 clean 吃掉的形态：目录本身在构建树内，删构建树时
        连它一起删。禁改区判定必须按**路径前缀**生效，而不是只看顶层。
        """
        keep = self.box.path / "build" / "third_party" / "frozen"
        keep.mkdir(parents=True)
        handle = open(str(keep / "keep-me.o"), "w")
        try:
            handle.write("x")
        finally:
            handle.close()
        self.box.configure(CONFIG_TEMPLATE.replace(
            'BLACKLIST = [ROOT / "third_party"]',
            'BLACKLIST = [ROOT / "third_party"]\n'
            'FROZEN = [ROOT / "build" / "third_party" / "frozen"]'))
        code, out, err = self.box.run("stages/clean.py", [])
        assert 0 == code, (out, err)
        assert (keep / "keep-me.o").is_file()
        assert "跳过" in out

    def test_build_dir_inside_frozen_is_a_config_error(self):
        """构建目录落在禁改区里等于把红线整个绕开，直接判配置错。"""
        self.box.configure(CONFIG_TEMPLATE.replace(
            'BLACKLIST = [ROOT / "third_party"]',
            'BLACKLIST = [ROOT / "third_party"]\n'
            'FROZEN = [ROOT / "build"]'))
        code, out, err = self.box.run("stages/clean.py", [])
        assert 0 != code
        assert "禁改区" in err


class TestMemcheckRepeat(SandboxCase):
    """memcheck 的判定收敛：重复运行取交集，聚合行进控制台。

    含定时器或异步事件循环的测试里，valgrind 的泄漏分类随时序漂移，同一份
    二进制两次运行的域内条数能差两三成。门禁要的是"每次都成立"的失败，
    因此用重复运行的交集换掉单次快照。
    """

    # 假 valgrind：按 --xml-file= 写出本次报告，内容由环境里的 run1/run2 决定
    FAKE = """#!/bin/sh
for arg in "$@"; do
  case "$arg" in
    --xml-file=*) out="${arg#--xml-file=}" ;;
  esac
done
count_file="{root}/run.count"
run=0
[ -f "$count_file" ] && run=$(cat "$count_file")
run=$((run + 1))
echo "$run" > "$count_file"
case "$run" in
  1) body="$BODY1" ;;
  2) body="$BODY2" ;;
  *) body="$BODY1" ;;
esac
printf '%s' "$body" > "$out"
exit 0
"""

    def write(self, relative, text):
        path = self.box.path / relative
        if not path.parent.is_dir():
            path.parent.mkdir(parents=True)
        with open(str(path), "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def error(self, signature, function):
        """一条域内泄漏：归属对象是被测程序自己，符号名用于聚合。"""
        program = self.box.path / "build" / "test" / "run_tests"
        return ("  <error>\n"
                "    <kind>Leak_DefinitelyLost</kind>\n"
                "    <xwhat><text>{sig} bytes in 1 blocks are definitely "
                "lost</text></xwhat>\n"
                "    <stack>\n"
                "      <frame><ip>0x1</ip><obj>{obj}</obj><fn>{fn}</fn>"
                "</frame>\n"
                "    </stack>\n"
                "  </error>\n").format(sig=signature, obj=program,
                                          fn=function)

    def xml(self, items):
        return ('<?xml version="1.0" encoding="UTF-8"?>\n<valgrindoutput>\n'
                + "".join(self.error(*item) for item in items)
                + "</valgrindoutput>\n")

    def make_ready(self, first, second, repeat=2):
        workdir = self.box.path / "build" / "test"
        self.make_memcheck_target(workdir)
        directory = tempfile.mkdtemp(prefix="cxx_ci_fakevg_")
        tool = os.path.join(directory, "valgrind")
        with open(tool, "w", encoding="utf-8") as handle:
            # 用替换而不是 format：脚本里有 ${arg#...} 这类花括号
            handle.write(self.FAKE.replace("{root}", str(self.box.path)))
        os.chmod(tool, 0o755)
        text = CONFIG_TEMPLATE + (
            "\nMEMCHECK_REPEAT = {}\n"
            'MEMCHECK_SCOPE = "project"\n'
            'MEMCHECK_ENV = {{"BODY1": {!r}, "BODY2": {!r}}}\n'
        ).format(repeat, self.xml(first), self.xml(second))
        self.box.configure(text)
        return {"PATH": directory + os.pathsep + os.environ.get("PATH", "")}

    A = (24, "Menu::make_menus()")
    B = (40, "NormalButton::NormalButton_init()")
    C = (64, "TextMessageBox::tr_set_message_info()")

    def test_repeat_one_keeps_the_single_snapshot(self):
        env = self.make_ready([self.A, self.B], [self.B], repeat=1)
        code, out, err = self.box.run("stages/memcheck.py", [], env=env)
        assert errors.status(errors.MEMCHECK_FAILED) == code
        assert "域内问题 2 处" in out

    def test_intersection_drops_unreproduced_failures(self):
        env = self.make_ready([self.A, self.B], [self.B])
        code, out, err = self.box.run("stages/memcheck.py", [], env=env)
        assert errors.status(errors.MEMCHECK_FAILED) == code
        # 两次都判域内的那条才算失败，第一次独有的那条不进交集
        assert "域内问题 1 处" in out
        assert "交集 1" in out

    def test_disjoint_runs_pass(self):
        """两次各报一条、互不重叠：没有可复现的失败，门禁不该翻脸。"""
        env = self.make_ready([self.A], [self.B])
        code, out, err = self.box.run("stages/memcheck.py", [], env=env)
        assert 0 == code, err
        assert "交集 0" in out

    def test_top_aggregation_is_visible_on_stdout(self):
        """聚合行必须在控制台看得见：它决定该往配置里填哪些符号前缀。"""
        env = self.make_ready([self.A, self.B, self.C], [self.A, self.B, self.C])
        code, out, err = self.box.run("stages/memcheck.py", [], env=env)
        assert errors.status(errors.MEMCHECK_FAILED) == code
        assert "按符号前缀聚合 Top" in out
        assert "Menu:: 1" in out

    def test_kind_line_is_visible_on_stdout(self):
        env = self.make_ready([self.A, self.B], [self.A, self.B])
        code, out, err = self.box.run("stages/memcheck.py", [], env=env)
        assert "按类别" in out
        assert "definitely-lost 2" in out

    def test_aggregation_absent_for_a_single_failure(self):
        env = self.make_ready([self.A], [self.A])
        code, out, err = self.box.run("stages/memcheck.py", [], env=env)
        assert errors.status(errors.MEMCHECK_FAILED) == code
        assert "聚合" not in out


class TestBoundaryEncodingAtStageLevel(SandboxCase):
    """编码判定不出的文件走**完整阶段**时退出码必须非 0。

    单元测试只断言了 fix_guards 返回空清单与文件没被改动——那是"跳过"的证据，
    不是"退出码非 0"的证据。而"跳过不等于通过"这条纪律要求的正是后者：脚本
    看到 0 会当成功，用户的中文却从头到尾没被检查过。
    """

    def _with_boundary_scope(self):
        """把 BOUNDARY_DIRS 指向 src：boundary 只看这些目录。"""
        self.box.configure(CONFIG_TEMPLATE.replace(
            "SCOPE = {", 'BOUNDARY_DIRS = [ROOT / "src"]\nSCOPE = {')
            if "SCOPE = {" in CONFIG_TEMPLATE else
            CONFIG_TEMPLATE + '\nBOUNDARY_DIRS = [ROOT / "src"]\n')

    def test_gbk_file_makes_the_stage_exit_nonzero(self):
        gbk = (u"#ifndef A_H\n#define A_H\n// \u4e2d\u6587\u6ce8\u91ca\n"
               u"int value;\n#endif\n").encode("gb18030")
        target = self.box.path / "src" / "a.h"
        # ci 侧连测试也守 3.4：mkdir(exist_ok=) 是 3.2 有的，但 Path.mkdir 的
        # 关键字写法在 3.4 上仍受限，用 os.makedirs 更稳
        if not target.parent.is_dir():
            os.makedirs(str(target.parent))
        with open(str(target), "wb") as handle:
            handle.write(gbk)
        self._with_boundary_scope()
        code, out, err = self.box.run("stages/boundary.py", ["--fix"])
        assert 0 != code, (out, err)
        assert "编码无法确定" in (out + err)
        # 文件必须一个字节都没动
        with open(str(target), "rb") as handle:
            assert gbk == handle.read()

    def test_crlf_survives_the_real_rewrite_path(self):
        """CRLF 文件走真实改写路径后仍是 CRLF（单元测试只验了读写往返）。"""
        text = (u"#ifndef A_H\n#define A_H\nint value;\n#endif\n"
                ).replace("\n", "\r\n")
        target = self.box.path / "src" / "a.h"
        if not target.parent.is_dir():
            os.makedirs(str(target.parent))
        with open(str(target), "wb") as handle:
            handle.write(text.encode("utf-8"))
        self._with_boundary_scope()
        code, out, err = self.box.run("stages/boundary.py", ["--fix"])
        assert 0 == code, (out, err)
        with open(str(target), "rb") as handle:
            data = handle.read()
        bare = data.count(b"\n") - data.count(b"\r\n")
        assert 0 == bare, "改写后出现裸 LF，换行风格被改了"


class TestCleanToleratesAwkwardEntries(SandboxCase):
    """清理构建树时遇到"删不掉/不是普通目录"的条目不该让整轮停下。

    以前两条都会抛 Python 栈、退出 1，而且清理停在中途：指向目录的软链被
    shutil.rmtree 拒绝（Cannot call rmtree on a symbolic link），没权限的条目
    也让整段清理中断。docstring 自称幂等，那就连"目录不存在"也不该报错。
    """

    def _build_dir(self, names=()):
        for name in names:
            path = self.box.path / "build" / name
            if not path.parent.is_dir():
                os.makedirs(str(path.parent))
            path.write_bytes(b"x")

    def test_a_symlinked_directory_is_unlinked_not_removed(self):
        outside = self.box.path / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_bytes(b"important")
        if not (self.box.path / "build").is_dir():
            os.makedirs(str(self.box.path / "build"))
        link = self.box.path / "build" / "zzz_link"
        os.symlink(str(outside), str(link))
        code, _out, err = self.box.run("stages/clean.py", [])
        assert 0 == code, err
        assert not os.path.lexists(str(link)), "软链本身要摘掉"
        assert (outside / "keep.txt").is_file(), "链接目标不该动"

    def test_an_undeletable_entry_does_not_stop_the_rest(self):
        self._build_dir(["b.o"])
        locked = self.box.path / "build" / "locked"
        locked.mkdir()
        (locked / "a.o").write_bytes(b"x")
        os.chmod(str(locked), 0o555)
        try:
            code, _out, err = self.box.run("stages/clean.py", [])
        finally:
            os.chmod(str(locked), 0o755)
        assert 0 == code, err
        assert not (self.box.path / "build" / "b.o").exists(), "别的条目照常清理"

    def test_a_missing_build_dir_is_not_an_error(self):
        code, _out, err = self.box.run("stages/clean.py", [])
        assert 0 == code, err
