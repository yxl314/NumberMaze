"""编译数据库的单元测试。

夹具落在临时目录里，用真实的 Makefile 与假的可执行文件驱动，不 mock 文件
读取：要验证的正是"按构建系统取一份库"这条路径。compiledb 是否安装与测试
无关——需要它的用例把假的可执行文件塞进 PATH，随时可跑。

假工具用当前解释器作 shebang，不依赖 PATH 里的 sh / cat / cp：这样"把 PATH
换成空目录"的用例才真的能模拟工具缺失，而不是被外壳程序本身的缺失搅乱。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

# 本包只要求 pip 可装，但测试要在未安装的源码树上直接跑得起来
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cxx_standardize import compile_db
from cxx_standardize.errors import Code
from cxx_standardize.models import Entry


def put(path, text: str) -> str:
    """写文本文件，显式指定编码（区域设置为 C 时默认编码是 ASCII）。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")
    return str(path)


def make_dir(prefix: str = "cxx_std_db_") -> str:
    return tempfile.mkdtemp(prefix=prefix)


def fake_tool(directory: str, name: str, body: str) -> str:
    """放一个用当前解释器执行的假工具，供 PATH 命中。

    直接用解释器的绝对路径作 shebang，脚本体内不调用任何外部命令，因此把
    PATH 限制成一个空目录时，测试仍然能精确地只让目标工具"存在"或"不存在"。
    """
    path = os.path.join(directory, name)
    put(path, f"#!{sys.executable}\n{body}")
    os.chmod(path, 0o755)
    return path


def only_path(monkeypatch, directory: str) -> None:
    """把 PATH 限制成单个目录，模拟工具缺失/只装了一个工具。"""
    monkeypatch.setenv("PATH", directory)


def with_fake_tools(monkeypatch, directory: str) -> None:
    """把假工具目录插到 PATH 最前面，其余环境保持原样。"""
    monkeypatch.setenv(
        "PATH", directory + os.pathsep + os.environ.get("PATH", ""))


def read_json(path) -> list:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def files_of(entries) -> list[str]:
    return sorted(str(item["file"]) for item in entries)


def command_of(entry: dict) -> str:
    """条目可能是 command 字符串，也可能是 arguments 列表，统一成一行。"""
    if "command" in entry:
        return entry["command"]
    return " ".join(entry.get("arguments", []))


def config(tmp: str, **overrides) -> SimpleNamespace:
    """造一个配置对象；字段与真实配置同名，本模块只读不改。"""
    values = {
        "root": tmp,
        "build_dir": "",
        "test_build_dir": "",
        "build_file": "",
        "test_build_file": "",
        "build_tool": "",
        "log_dir": os.path.join(tmp, "logs"),
        "cxx_compiler": "",
        "cxx_standard": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)





# 假 compiledb：按"当前工作目录"查表写数据库。compiledb 的 cwd 就是构建目录，
# 因此一张 {构建目录: 条目} 的表就能模拟多套构建各自产出不同的库。
COMPILEDB_TABLE_FAKE = (
    "import json, os, sys\n"
    "argv = sys.argv[1:]\n"
    "out = argv[argv.index('-o') + 1]\n"
    "table = json.loads(os.environ['FAKE_COMPILEDB_TABLE'])\n"
    "json.dump(table.get(os.getcwd(), []), open(out, 'w', encoding='utf-8'))\n"
)


def with_compiledb(monkeypatch, table):
    """装一个假 compiledb，按表产出数据库。"""
    tmp = make_dir()
    fake_tool(tmp, "compiledb", COMPILEDB_TABLE_FAKE)
    with_fake_tools(monkeypatch, tmp)
    monkeypatch.setenv("FAKE_COMPILEDB_TABLE", json.dumps(table))


def entries_for(root, relative, command):
    """造一条指向 root/relative 的编译条目。"""
    return {"directory": root, "command": command,
            "file": os.path.join(root, relative)}


# ------------------------------------------------------------ 取库方式

class TestSourceSelection:
    """取库方式由构建系统决定，没有候选链。"""

    def test_cmake_project_uses_cmakes_own_export(self):
        build = make_dir()
        put(os.path.join(build, "CMakeCache.txt"), "# fixture\n")
        put(os.path.join(build, compile_db.DB_NAME),
            json.dumps([{"directory": build, "command": "g++ -c a.cpp",
                         "file": "a.cpp"}]))
        _path, code, message = compile_db.ensure_database(
            config(build, build_tool="cmake"), [Path(build)])
        assert Code.OK == code, message
        assert "cmake" in message

    def test_qmake_project_uses_compiledb(self, monkeypatch):
        build = make_dir()
        put(os.path.join(build, "Makefile"), "all:\n")
        tmp = make_dir()
        fake_tool(tmp, "compiledb", (
            "import sys\n"
            "argv = sys.argv[1:]\n"
            "out = argv[argv.index('-o') + 1]\n"
            "open(out, 'w', encoding='utf-8').write("
            "'[{\"directory\": \".\", \"command\": \"g++ -c a.cpp\", "
            "\"file\": \"a.cpp\"}]')\n"))
        with_fake_tools(monkeypatch, tmp)
        _path, code, message = compile_db.ensure_database(
            config(build, build_tool="qmake"), [Path(build)])
        assert Code.OK == code, message
        assert "compiledb" in message

    def test_unknown_build_tool_is_rejected(self):
        build = make_dir()
        path, code, message = compile_db.ensure_database(
            config(build, build_tool="meson"), [Path(build)])
        assert path is None
        assert Code.CONFIG_BAD_VALUE == code
        assert "meson" in message


class TestCmakeSource:
    def test_nested_database_is_adopted_into_build_root(self):
        build = make_dir()
        nested = os.path.join(build, "cmake")
        entries = [{"directory": nested, "command": "g++ -c a.cpp",
                    "file": "a.cpp"}]
        put(os.path.join(nested, compile_db.DB_NAME), json.dumps(entries))
        put(os.path.join(nested, "CMakeCache.txt"), "# fixture\n")
        path, code, message = compile_db.ensure_database(
            config(build, build_tool="cmake"), [Path(build)])
        assert Code.OK == code, message
        assert Path(build) / compile_db.DB_NAME == path
        assert entries == read_json(path)

    def test_database_without_cmake_cache_is_used_with_a_note(self, capsys):
        """清过构建目录的数据库仍可用：里面路径与编译命令都是完整的。

        编排里 ci 跑完会只留编译数据库（那是另一个项目的输入），缓存已不在。
        直接拒绝等于把正常流程判死，因此照用并提示。
        """
        build = make_dir()
        put(os.path.join(build, compile_db.DB_NAME),
            json.dumps([{"directory": build, "command": "g++ -c a.cpp",
                         "file": "a.cpp"}]))
        path, code, message = compile_db.ensure_database(
            config(build, build_tool="cmake"), [Path(build)])
        assert Code.OK == code, message
        assert path is not None
        assert "CMakeCache.txt" in capsys.readouterr().err

    def test_missing_database_tells_the_next_step(self):
        build = make_dir()
        put(os.path.join(build, "CMakeCache.txt"), "# fixture\n")
        path, code, message = compile_db.ensure_database(
            config(build, build_tool="cmake"), [Path(build)])
        assert path is None
        assert Code.DB_MISSING == code
        # 取不到就说清"先构建"，而不是退回去自己解析
        assert "先跑一次构建" in message
        assert "CMAKE_EXPORT_COMPILE_COMMANDS" in message


class TestCompiledbSource:
    def test_missing_tool_tells_how_to_install(self, monkeypatch):
        build = make_dir()
        put(os.path.join(build, "Makefile"), "all:\n")
        only_path(monkeypatch, make_dir())
        path, code, message = compile_db.ensure_database(
            config(build, build_tool="qmake"), [Path(build)])
        assert path is None
        assert Code.TOOL_MISSING == code
        assert "pip install compiledb" in message

    def test_unconfigured_build_dir_tells_the_next_step(self, monkeypatch):
        """没有 Makefile 时不去替它配置：那是构建的活，不是这里的活。"""
        build = make_dir()
        tmp = make_dir()
        fake_tool(tmp, "compiledb", "raise SystemExit(0)\n")
        with_fake_tools(monkeypatch, tmp)
        path, code, message = compile_db.ensure_database(
            config(build, build_tool="qmake"), [Path(build)])
        assert path is None
        assert Code.DB_MISSING == code
        assert "先跑一次构建" in message

    def test_absolute_dash_o_and_dash_d_are_passed(self, monkeypatch):
        build = make_dir()
        tmp = make_dir()
        put(os.path.join(build, "Makefile"), "all:\n")
        log = os.path.join(tmp, "args.txt")
        fake_tool(tmp, "compiledb", (
            "import json, os, sys\n"
            "argv = sys.argv[1:]\n"
            "with open(os.environ['COMPILEDB_ARGS'], 'w', encoding='utf-8') as log:\n"
            "    log.write('\\n'.join(argv))\n"
            "out = argv[argv.index('-o') + 1]\n"
            "json.dump([{'directory': '.', 'command': 'g++ -c a.cpp',\n"
            "           'file': 'a.cpp'}], open(out, 'w', encoding='utf-8'))\n"))
        with_fake_tools(monkeypatch, tmp)
        monkeypatch.setenv("COMPILEDB_ARGS", log)
        path, code, message = compile_db.ensure_database(
            config(build, build_tool="qmake"), [Path(build)])
        assert Code.OK == code, message
        assert Path(build).resolve() / compile_db.DB_NAME == path
        args = Path(log).read_text(encoding="utf-8").splitlines()
        assert os.path.isabs(args[args.index("-o") + 1])
        assert os.path.isabs(args[args.index("-d") + 1])
        assert "-n" in args
        assert args[-1] == "make"

    def test_tool_failure_reports_reason(self, monkeypatch):
        build = make_dir()
        tmp = make_dir()
        put(os.path.join(build, "Makefile"), "all:\n")
        fake_tool(tmp, "compiledb", "raise SystemExit(3)\n")
        with_fake_tools(monkeypatch, tmp)
        path, code, message = compile_db.ensure_database(
            config(build, build_tool="qmake"), [Path(build)])
        assert path is None
        assert Code.COMMAND_UNRUNNABLE == code
        assert "compiledb 退出码 3" in message

    def test_broken_dry_run_is_rejected_instead_of_partially_accepted(
            self, monkeypatch):
        """dry-run 中途失败会产出一份"能读但残缺"的数据库。

        半份结果比失败更难发现：条目数少了一截，但每一处看起来都正常。"""
        build = make_dir()
        tmp = make_dir()
        put(os.path.join(build, "Makefile"), "all:\n")
        fake_tool(tmp, "compiledb", (
            "import sys\n"
            "print(\"/bin/sh: 1: cd: can't cd to src/\", file=sys.stderr)\n"
            "argv = sys.argv[1:]\n"
            "out = argv[argv.index('-o') + 1]\n"
            "open(out, 'w', encoding='utf-8').write("
            "'[{\"directory\": \".\", \"command\": \"g++ -c a.cpp\", "
            "\"file\": \"a.cpp\"}]')\n"))
        with_fake_tools(monkeypatch, tmp)
        path, code, message = compile_db.ensure_database(
            config(build, build_tool="qmake"), [Path(build)])
        assert path is None
        assert Code.DB_MISSING == code
        assert "dry-run 未走通" in message


# ------------------------------------------------------------ 双构建目录

class TestDualBuildDirectories:
    """主工程与测试是两套构建，两份库要合并成一份。"""

    def _layout(self, monkeypatch, extra=()) -> tuple[str, str, str, str]:
        root = make_dir("cxx_std_dual_")
        main_build = os.path.join(root, "build", "app")
        test_build = os.path.join(root, "build", "tests")
        os.makedirs(main_build)
        os.makedirs(test_build)
        for name in ("src/task.cpp", "src/task_store.cpp",
                     "tests/test_task.cpp"):
            put(os.path.join(root, name), "// fixture\n")
        put(os.path.join(main_build, "Makefile"), "all:\n")
        put(os.path.join(test_build, "Makefile"), "all:\n")
        main = [entries_for(root, "src/task.cpp", "g++ -std=c++11 -c"),
                entries_for(root, "src/task_store.cpp", "g++ -std=c++11 -c")]
        main.extend(extra)
        test = [entries_for(root, "tests/test_task.cpp",
                            "g++ -std=c++14 -I/opt/qt/include/QtTest -c")]
        with_compiledb(monkeypatch, {os.path.realpath(main_build): main,
                                     os.path.realpath(test_build): test})
        log_dir = os.path.join(root, "logs")
        return root, main_build, test_build, log_dir

    def test_two_databases_are_merged_into_log_dir(self, monkeypatch):
        root, main_build, test_build, log_dir = self._layout(monkeypatch)
        cfg = config(root, build_tool="qmake", build_dir=main_build,
                     test_build_dir=test_build, log_dir=log_dir)
        path, code, message = compile_db.ensure_database(
            cfg, [Path(main_build), Path(test_build)])
        assert Code.OK == code, message
        assert Path(log_dir) / compile_db.DB_NAME == path
        entries = read_json(path)
        # 两份的条目都在同一份里，否则测试目录会被判成无法分析
        assert [os.path.join(root, "src", "task.cpp"),
                os.path.join(root, "src", "task_store.cpp"),
                os.path.join(root, "tests", "test_task.cpp")] == files_of(entries)
        # 各自的参数不能串味
        by_file = {item["file"]: command_of(item) for item in entries}
        assert "-std=c++11" in by_file[os.path.join(root, "src", "task.cpp")]
        assert "-std=c++14" in by_file[os.path.join(root, "tests",
                                                    "test_task.cpp")]
        assert "QtTest" in by_file[os.path.join(root, "tests", "test_task.cpp")]

    def test_merge_keeps_one_copy_of_a_repeated_file(self, monkeypatch):
        root, main_build, test_build, log_dir = self._layout(monkeypatch)
        table = {os.path.realpath(main_build): [
            entries_for(root, "src/task.cpp", "g++ -std=c++11 -c")],
            os.path.realpath(test_build): [
                entries_for(root, "src/task.cpp", "g++ -std=c++14 -c"),
                entries_for(root, "tests/test_task.cpp", "g++ -std=c++14 -c")]}
        with_compiledb(monkeypatch, table)
        cfg = config(root, build_tool="qmake", build_dir=main_build,
                     test_build_dir=test_build, log_dir=log_dir)
        path, code, message = compile_db.ensure_database(
            cfg, [Path(main_build), Path(test_build)])
        assert Code.OK == code, message
        files = files_of(read_json(path))
        assert files.count(os.path.join(root, "src", "task.cpp")) == 1

    def test_single_successful_database_is_returned_directly(self, monkeypatch):
        root, main_build, test_build, log_dir = self._layout(monkeypatch)
        # 测试构建目录取不到：只降级，仍用主库那一份
        for name in os.listdir(test_build):
            os.remove(os.path.join(test_build, name))
        cfg = config(root, build_tool="qmake", build_dir=main_build,
                     test_build_dir=test_build, log_dir=log_dir)
        path, code, message = compile_db.ensure_database(
            cfg, [Path(main_build), Path(test_build)])
        assert Code.OK == code, message
        assert Path(main_build) / compile_db.DB_NAME == path

    def test_missing_test_database_prints_hint(self, capsys, monkeypatch):
        root, main_build, test_build, log_dir = self._layout(monkeypatch)
        for name in os.listdir(test_build):
            os.remove(os.path.join(test_build, name))
        cfg = config(root, build_tool="qmake", build_dir=main_build,
                     test_build_dir=test_build, log_dir=log_dir)
        _path, code, message = compile_db.ensure_database(
            cfg, [Path(main_build), Path(test_build)])
        assert Code.OK == code, message
        assert "未取到编译数据库" in capsys.readouterr().err

    def test_missing_main_database_is_db_missing(self, capsys, monkeypatch):
        root, main_build, test_build, log_dir = self._layout(monkeypatch)
        for name in os.listdir(main_build):
            os.remove(os.path.join(main_build, name))
        cfg = config(root, build_tool="qmake", build_dir=main_build,
                     test_build_dir=test_build, log_dir=log_dir)
        path, code, _message = compile_db.ensure_database(
            cfg, [Path(main_build), Path(test_build)])
        assert path is None
        assert Code.DB_MISSING == code


class TestMergeDatabases:
    def test_merges_arguments_and_command_shapes(self):
        root = make_dir()
        first = os.path.join(root, "a.json")
        second = os.path.join(root, "b.json")
        put(first, json.dumps([{"directory": root, "file": "a.cpp",
                                "arguments": ["g++", "-c", "a.cpp"]}]))
        put(second, json.dumps([{"directory": root, "file": "b.cpp",
                                 "command": "g++ -std=c++14 -c b.cpp"}]))
        target = os.path.join(root, "merged.json")
        path, code, message = compile_db.merge_databases(
            [Path(first), Path(second)], Path(target))
        assert Code.OK == code, message
        assert ["a.cpp", "b.cpp"] == [Path(f).name for f in files_of(read_json(path))]

    def test_empty_sources_report_db_missing(self):
        root = make_dir()
        path, code, _message = compile_db.merge_databases(
            [], Path(root) / "merged.json")
        assert path is None
        assert Code.DB_MISSING == code


class TestReadEntries:
    def test_reads_both_shapes_into_entries(self):
        root = make_dir()
        path = os.path.join(root, compile_db.DB_NAME)
        put(path, json.dumps([
            {"directory": root, "file": "a.cpp", "arguments": ["g++", "-c"]},
            {"directory": root, "file": "b.cpp", "command": "g++ -c b.cpp"},
        ]))
        entries = compile_db.read_entries(Path(path))
        assert 2 == len(entries)
        assert ["g++", "-c"] == list(entries[0].arguments)
        assert ["g++", "-c", "b.cpp"] == list(entries[1].arguments)

    def test_missing_file_gives_empty_list(self, capsys):
        assert [] == compile_db.read_entries(Path("/nonexistent/db.json"))
        assert "警告" in capsys.readouterr().err

    def test_broken_json_gives_empty_list(self, capsys):
        root = make_dir()
        path = os.path.join(root, compile_db.DB_NAME)
        put(path, "{not json")
        assert [] == compile_db.read_entries(Path(path))
        assert "警告" in capsys.readouterr().err


class TestCache:
    def test_cache_is_reused_without_regenerating(self, monkeypatch):
        build = make_dir()
        put(os.path.join(build, "Makefile"), "all:\n")
        with_compiledb(monkeypatch, {os.path.realpath(build): [
            entries_for(build, "a.cpp", "g++ -c")]})
        cfg = config(build, build_tool="qmake")
        path, code, message = compile_db.ensure_database(cfg, [Path(build)])
        assert Code.OK == code, message
        assert os.path.isfile(os.path.join(build, compile_db.DB_CACHE))
        # 用哨兵覆盖数据库：缓存命中时它必须原样保留，说明没有重新生成
        # directory 用真实存在的目录：不存在的构建目录会让 clang-tidy 直接中止，
        # 那是另一条检查要拦的，不该在这里干扰"缓存命中"的验证
        sentinel = [{"directory": build, "command": "true",
                     "file": "/sentinel.cpp"}]
        put(path, json.dumps(sentinel))
        again, code, message = compile_db.ensure_database(cfg, [Path(build)])
        assert Code.OK == code, message
        assert "缓存" in message
        assert sentinel == read_json(again)

    def test_cache_invalidated_by_makefile_change(self, monkeypatch):
        build = make_dir()
        put(os.path.join(build, "Makefile"), "all:\n")
        with_compiledb(monkeypatch, {os.path.realpath(build): [
            entries_for(build, "a.cpp", "g++ -c")]})
        cfg = config(build, build_tool="qmake")
        assert Code.OK == compile_db.ensure_database(cfg, [Path(build)])[1]
        put(os.path.join(build, "Makefile"), "all:\n\t@true\n")
        later = time.time() + 50
        os.utime(os.path.join(build, "Makefile"), (later, later))
        _path, code, message = compile_db.ensure_database(cfg, [Path(build)])
        assert Code.OK == code, message
        assert "缓存" not in message

    def test_cache_stamp_covers_subdirectory_makefile(self, monkeypatch):
        """子目录 Makefile 变了也算构建文件变化，缓存必须失效。"""
        build = make_dir()
        put(os.path.join(build, "Makefile"), "all:\n")
        put(os.path.join(build, "tests", "Makefile"), "all:\n")
        with_compiledb(monkeypatch, {os.path.realpath(build): [
            entries_for(build, "a.cpp", "g++ -c")]})
        cfg = config(build, build_tool="qmake")
        assert Code.OK == compile_db.ensure_database(cfg, [Path(build)])[1]
        sub_makefile = os.path.join(build, "tests", "Makefile")
        put(sub_makefile, "all:\n\t@true\n")
        later = time.time() + 50
        os.utime(sub_makefile, (later, later))
        _path, code, message = compile_db.ensure_database(cfg, [Path(build)])
        assert Code.OK == code, message
        assert "缓存" not in message


class TestDependencies:
    """改动的头文件到受影响翻译单元的映射，用编译器自己的 -MM 取。"""

    def test_dependency_command_swaps_dash_c_for_dash_mm(self):
        entry = Entry(directory=Path("/b"), file=Path("a.cpp"),
                      arguments=("g++", "-std=c++11", "-o", "a.o", "-c",
                                 "a.cpp"))
        command = compile_db.dependency_command(entry)
        assert "-MM" in command
        assert "-c" not in command
        assert "-o" not in command

    def test_missing_entry_reports_db_missing(self):
        root = make_dir()
        put(os.path.join(root, compile_db.DB_NAME), "[]")
        found, code, _message = compile_db.dependencies(
            Path(root) / "a.cpp", Path(root))
        assert set() == found
        assert Code.DB_MISSING == code
