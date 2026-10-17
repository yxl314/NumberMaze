"""代码守卫：注释剥除、detail:: 归属、头文件宏守卫。

要守住三件事：剥注释不能把代码也剥掉（否则 detail:: 越界会漏报），也不能把
注释里的 detail:: 算成越界；宏守卫名由路径决定，改名与残缺都要认出来。
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cxx_standardize import guards  # noqa: E402


def put(path, text):
    path = str(path)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def read(path):
    with open(str(path), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture
def workdir():
    directory = tempfile.mkdtemp(prefix="cxx_std_bnd_")
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


HEADER = """\
#ifndef OLD_NAME
#define OLD_NAME

int value;

#endif  // OLD_NAME
"""


class TestStripComments:
    def test_lines_and_columns_are_preserved(self):
        text = "int a = 1;  // 说明\n/* 块\n   注释 */\nint b = 2;\n"
        stripped = guards.strip_comments(text)
        assert len(stripped.splitlines()) == len(text.splitlines())
        assert stripped.splitlines()[0].startswith("int a = 1;")
        assert len(stripped.splitlines()[0]) == len(text.splitlines()[0])

    def test_comment_markers_inside_literals_do_not_open_a_comment(self):
        text = 'const char *s = "// 不是注释";\nint b = 2;  // 真注释\n'
        stripped = guards.strip_comments(text)
        assert '"// 不是注释"' in stripped
        assert "真注释" not in stripped

    def test_raw_string_is_skipped(self):
        text = 'auto r = R"x(/* 不是注释 */)x";\nint b = 2;\n'
        stripped = guards.strip_comments(text)
        assert "/* 不是注释 */" in stripped
        assert "int b = 2;" in stripped

    def test_multiline_comment_keeps_line_count(self):
        text = "/*\n\n\n*/\nint a;\n"
        assert 5 == len(guards.strip_comments(text).splitlines())

    def test_detail_mark_in_comments_survives_stripping_only_as_code(self):
        text = ("// 注释里提到 Notify::detail::x\n"
                "/* 块注释里也有 detail:: */\n"
                "int x = Notify::detail::capture();\n")
        stripped = guards.strip_comments(text)
        hits = [line for line in stripped.splitlines() if "detail::" in line]
        assert 1 == len(hits)
        assert "capture" in hits[0]


class TestDetailLeaks:
    def test_leak_outside_the_module_directory_is_reported(self, workdir):
        module = os.path.join(workdir, "Tools", "Notify")
        os.makedirs(module)
        outside = put(os.path.join(workdir, "App", "main.cpp"),
                      "int x = Notify::detail::capture();\n")
        leaks = guards.find_detail_leaks([outside], [module], "detail::")
        assert [(outside, 1)] == [(path, number) for path, number, _ in leaks]

    def test_use_inside_the_module_directory_is_allowed(self, workdir):
        module = os.path.join(workdir, "Tools", "Notify")
        inside = put(os.path.join(module, "Notify.cpp"),
                     "int x = detail::capture();\n")
        assert [] == guards.find_detail_leaks([inside], [module], "detail::")

    def test_mention_in_a_comment_is_not_a_leak(self, workdir):
        outside = put(os.path.join(workdir, "App", "main.cpp"),
                      "// 内部实现见 Notify::detail::capture\n")
        assert [] == guards.find_detail_leaks([outside], [], "detail::")


class TestGuardNames:
    def test_name_comes_from_the_path(self):
        assert "SOURCE_CDBURN_TOOLS_NOTIFY_NOTIFY_H" == guards.expected_guard(
            "/repo/Source/CDBurn/Tools/Notify/Notify.h", "/repo")

    def test_dashes_and_dots_become_underscores(self):
        assert "SRC_A_B_HPP" == guards.expected_guard("/repo/src/a-b.hpp",
                                                     "/repo")

    def test_file_outside_the_root_uses_its_absolute_path(self):
        name = guards.expected_guard("/elsewhere/x.h", "/repo")
        assert name.startswith("_ELSEWHERE_X_H")

    def test_reads_the_declared_guard(self):
        assert ("OLD_NAME", False) == guards.actual_guard(HEADER)

    def test_ifndef_without_matching_define_is_broken(self):
        assert ("ONLY_IFNDEF", True) == guards.actual_guard(
            "#ifndef ONLY_IFNDEF\nint x;\n")

    def test_no_guard_at_all(self):
        assert ("", False) == guards.actual_guard("int x;\n")


class TestGuardReport:
    def _write(self, workdir, name, text):
        return put(os.path.join(workdir, name), text)

    def test_missing_misnamed_and_broken_are_told_apart(self, workdir):
        missing = self._write(workdir, "a.h", "int a;\n")
        misnamed = self._write(workdir, "b.h", HEADER)
        broken = self._write(workdir, "c.h", "#ifndef ONLY\nint c;\n")
        report = guards.guard_report([missing, misnamed, broken],
                                     workdir, (".h",))
        assert ["missing", "misnamed", "broken"] == [item[3] for item in report]

    def test_correct_guard_is_not_reported(self, workdir):
        name = guards.expected_guard(os.path.join(workdir, "d.h"), workdir)
        path = self._write(workdir, "d.h",
                           "#ifndef {}\n#define {}\n#endif\n".format(name, name))
        assert [] == guards.guard_report([path], workdir, (".h",))

    def test_non_header_files_are_skipped(self, workdir):
        path = self._write(workdir, "a.cpp", "int a;\n")
        assert [] == guards.guard_report([path], workdir, (".h",))


class TestFixGuards:
    def _write(self, workdir, name, text):
        return put(os.path.join(workdir, name), text)

    def test_missing_guard_is_inserted_after_the_leading_comment(self, workdir):
        path = self._write(workdir, "a.h", "// 版权\n// 说明\n\nint a;\n")
        want = guards.expected_guard(path, workdir)
        changed = guards.fix_guards(guards.guard_report([path], workdir,
                                                        (".h",)))
        assert [path] == changed
        text = read(path)
        assert text.startswith("// 版权\n// 说明\n\n#ifndef {}\n".format(want))
        assert text.rstrip().endswith("#endif  // {}".format(want))
        assert [] == guards.guard_report([path], workdir, (".h",))

    def test_misnamed_guard_is_renamed_in_place(self, workdir):
        path = self._write(workdir, "b.h", HEADER)
        want = guards.expected_guard(path, workdir)
        guards.fix_guards(guards.guard_report([path], workdir, (".h",)))
        text = read(path)
        assert "OLD_NAME" not in text
        assert "#ifndef {}".format(want) in text
        assert "#define {}".format(want) in text
        assert "#endif  // {}".format(want) in text

    def test_broken_guard_gets_a_matching_define(self, workdir):
        path = self._write(workdir, "c.h", "#ifndef ONLY\nint c;\n")
        guards.fix_guards(guards.guard_report([path], workdir, (".h",)))
        text = read(path)
        assert "#ifndef ONLY\n#define ONLY\n" in text
        assert "#endif  // ONLY" in text

    def test_fix_is_idempotent(self, workdir):
        path = self._write(workdir, "d.h", "int d;\n")
        for _ in range(2):
            guards.fix_guards(guards.guard_report([path], workdir, (".h",)))
        assert [] == guards.guard_report([path], workdir, (".h",))


class TestSourceFiles:
    def test_collects_by_extension_and_sorts(self, workdir):
        for name in ("b.h", "a.h", "c.cpp", "sub/d.hpp"):
            put(os.path.join(workdir, name), "int x;\n")
        found = guards.source_files([workdir], (".h", ".hpp"))
        assert ["a.h", "b.h", "d.hpp"] == [os.path.basename(item)
                                           for item in found]

    def test_missing_directory_is_ignored(self, workdir):
        assert [] == guards.source_files([os.path.join(workdir, "nope")],
                                         (".h",))
