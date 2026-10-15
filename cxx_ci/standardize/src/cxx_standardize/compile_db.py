"""编译数据库：按构建系统取一份，不做候选链。

    cmake 项目  → cmake 自己导出的 compile_commands.json
    qmake 项目  → compiledb（pip install compiledb）

来源由构建系统决定，不存在"哪个工具先成用哪个"：链上留一个备选，那条备选
路径的代码就永远不会被删掉，"用现成工具"这件事也就从未真正发生。同理，工具
缺失或产出不成立时直接失败并说明该装什么、该先做什么，不退回自建解析。

这里不依赖 `ci` 项目：两侧只通过构建目录里的文件协作（见仓库根 CONTRACT.md），
配置对象由调用方传入，本模块只读其中与构建有关的字段。

一律返回 `(路径, Code, 说明)`，不使用异常做控制流；只在调用外部进程与解析
JSON 的边界处捕获异常并转成错误码。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from .errors import Code
from .models import Entry

# 编译数据库在构建目录里的固定名字，clang 工具链与外部使用方都只认它
DB_NAME = "compile_commands.json"

# 缓存文件放在构建目录内：它随构建产物一起被清理，不会跨构建串味
DB_CACHE = ".cxx_ci_db_cache.json"

# 缓存依赖的构建文件：任一 mtime 变化都说明编译参数可能变了
_CACHE_INPUTS = ("Makefile", "makefile", "GNUmakefile", "build.ninja",
                 "CMakeCache.txt")

# 构建目录里可能的 Makefile 名，make 自身的查找顺序
_MAKEFILE_NAMES = ("GNUmakefile", "makefile", "Makefile")

# 外部命令的等待上限。compiledb 只解析 make 的 dry-run 输出，正常在秒级完成；
# 留足余量是因为首次运行要预热的构建目录可能很大。
_PROVIDER_TIMEOUT = 600

# 递归结构下子目录 Makefile 也要纳入缓存判定，否则子目录变化（或后来才
# 生成）不会让缓存失效，会一直复用旧库
_STAMP_MAX_DEPTH = 4

# 取不到数据库时统一的下一步动作：它是构建的产物，不是另跑一个工具就能凭空
# 变出来的东西
_BUILD_FIRST = "请先跑一次构建（python3 ci/ci.py --only build），编译数据库由构建产出"


# --------------------------------------------------------------- 通用小工具

def _database_path(build_dir: Path) -> Path:
    """数据库在构建根下的固定位置。使用方只认这一处。"""
    return Path(build_dir) / DB_NAME


def c_locale_env(extra=None) -> dict:
    """固定工具输出语言的环境变量。

    固定为 C 区域设置：编译器与 make 会把级别词本地化（make 的
    "Entering directory" 会变，编译器输出的 "警告：" 也不再是 "warning:"），
    而按文本匹配的解析依赖固定版式。

    与 ci 侧同名同实现——同一个概念在两侧用同一个名字。
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    for key, value in (extra or {}).items():
        env[str(key)] = str(value)
    return env


def _run(command: Sequence[object], cwd: Path | None = None,
         env: dict | None = None,
         timeout: int = _PROVIDER_TIMEOUT) -> tuple[int | None, str, str]:
    """执行外部命令，返回 (退出码, 标准输出, 标准错误)。

    退出码为 None 表示进程根本没跑起来（可执行文件缺失、超时、无权限），
    调用方据此区分"工具不可用"与"工具跑了但失败"。
    """
    argv = [str(part) for part in command]
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "", f"命令超过 {timeout} 秒未结束: {' '.join(argv)}"
    except OSError as exc:
        # 程序不存在或不可执行：在边界处转成错误码，不向上抛
        return None, "", f"无法执行 {argv[0]}: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def _find_tool(names: Iterable[str]) -> tuple[Path | None, Code, str]:
    """在 PATH 上按顺序找第一个可用的可执行文件。"""
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found), Code.OK, ""
    return None, Code.TOOL_MISSING, "找不到可执行文件: " + "、".join(names)


def _load_text(path: Path) -> tuple[str, Code, str]:
    """读取文本文件，返回 (内容, 错误码, 说明)。"""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace"), \
            Code.OK, ""
    except OSError as exc:
        return "", Code.COMMAND_UNRUNNABLE, f"读取失败 {path}: {exc}"


def _write_text(target: Path, text: str) -> tuple[Path | None, Code, str]:
    """写文本文件，必要时补出父目录。"""
    target = Path(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        return None, Code.COMMAND_UNRUNNABLE, f"无法写入 {target}: {exc}"
    return target, Code.OK, ""


def _write_entries(target: Path, entries: list[dict]) -> tuple[Path | None, Code, str]:
    """把原始条目写成编译数据库。

    缩进与 JSON 版式只求稳定可读，便于人工比对两份产出。
    """
    try:
        text = json.dumps(entries, indent=1)
    except (TypeError, ValueError) as exc:
        return None, Code.COMMAND_UNRUNNABLE, f"编译条目无法序列化: {exc}"
    return _write_text(target, text)


def _read_json_list(path: Path) -> tuple[list, Code, str]:
    """读一个顶层为列表的 JSON 文件，返回 (数据, 错误码, 说明)。"""
    text, code, message = _load_text(path)
    if code != Code.OK:
        return [], code, message
    try:
        data = json.loads(text)
    except ValueError as exc:
        return [], Code.DB_MISSING, f"不是合法 JSON: {exc}"
    if not isinstance(data, list):
        return [], Code.DB_MISSING, "顶层不是列表"
    return data, Code.OK, ""


def _entry_of(item: object) -> Entry | None:
    """把数据库里的一条原始记录转成 Entry，形状不对时返回空。

    既有只写 `command` 的产出（cmake、手写夹具），也有只写 `arguments` 的产出；
    两者都认，并统一折成参数列表，合并时才能比较。
    """
    if not isinstance(item, dict):
        return None
    raw_file = item.get("file")
    if not raw_file:
        return None
    arguments = item.get("arguments")
    if isinstance(arguments, list) and all(isinstance(a, str)
                                           for a in arguments):
        args = tuple(arguments)
    else:
        command = item.get("command")
        args = tuple(shlex.split(command)) if isinstance(command, str) and \
            command.strip() else ()
    return Entry(
        directory=Path(item.get("directory") or "."),
        file=Path(str(raw_file)),
        arguments=args,
    )


def _entries_of(path: Path) -> list[Entry]:
    """内部读取入口：不打印告警，供产出方自身校验。"""
    raw, code, _ = _read_json_list(path)
    if code != Code.OK:
        return []
    return [entry for entry in (_entry_of(item) for item in raw)
            if entry is not None]


# 依赖生成时要去掉的选项：它们要么与 -MM 冲突，要么指定了不想要的输出
_DEPENDENCY_DROP = ("-o", "-MF", "-MT", "-MQ", "-MD", "-MMD")
# 依赖生成会写成多行续行；去掉续行再按冒号切开
_DEPENDENCY_LINE = re.compile(r"\s*\\\n")


def dependency_command(entry: Entry) -> list[str] | None:
    """把一条编译命令改成"只生成依赖"的命令。

    `-MM` 是编译器自己的依赖输出（不含系统头），因此拿到的是**精确**的包含
    关系：间接包含、同名头文件、注释里的 #include 都不会骗到它。
    """
    arguments = [str(item) for item in entry.arguments]
    if not arguments:
        return None
    result, index = [], 0
    while index < len(arguments):
        token = arguments[index]
        if token in _DEPENDENCY_DROP:
            index += 2
            continue
        if token == "-c":
            result.append("-MM")
            index += 1
            continue
        result.append(token)
        index += 1
    return result


def _entry_for(path: Path, entries: list[Entry]) -> Entry | None:
    """找到某个文件对应的编译条目。"""
    target = Path(path).resolve()
    for entry in entries:
        candidate = Path(entry.file)
        if not candidate.is_absolute():
            candidate = Path(entry.directory) / candidate
        if candidate.resolve() == target:
            return entry
    return None


def dependencies(path: Path, database_dir: Path) -> tuple[set[Path], Code, str]:
    """该文件包含了哪些头文件（含间接包含），用编译器自己的 `-MM` 取。

    返回 (包含的头文件集合, 错误码, 说明)。集合里只有落在编译命令行里出现过
    的路径，且已归一化成绝对路径。
    """
    entries = read_entries(Path(database_dir) / DB_NAME)
    entry = _entry_for(Path(path), entries)
    if entry is None:
        return set(), Code.DB_MISSING, f"编译数据库里没有 {path} 的条目"
    command = dependency_command(entry)
    if command is None:
        return set(), Code.DB_MISSING, f"{path} 的编译条目没有可用的命令行"
    code, stdout, stderr = _run(command, cwd=Path(entry.directory),
                                env=c_locale_env(), timeout=_PROVIDER_TIMEOUT)
    if code is None:
        return set(), Code.COMMAND_UNRUNNABLE, stderr
    if code != 0:
        return set(), Code.COMMAND_UNRUNNABLE, \
            f"{path} 的依赖生成失败（退出码 {code}）: {stderr.strip()[:200]}"
    text = _DEPENDENCY_LINE.sub(" ", stdout)
    _, _, tail = text.partition(":")
    found = set()
    for token in tail.split():
        candidate = Path(token)
        if not candidate.is_absolute():
            candidate = Path(entry.directory) / candidate
        found.add(Path(os.path.normpath(candidate)))
    found.discard(Path(os.path.normpath(Path(path))))
    return found, Code.OK, ""


def read_entries(path: Path) -> list[Entry]:
    """读出编译数据库里的条目；读不出来时给空列表并提示。

    给空列表而不是抛异常，是因为调用方（合并、缓存、诊断）在缺一份数据库时
    都有一条明确的降级路径，逐层传播异常只会让降级点变得难以追踪。
    """
    path = Path(path)
    raw, code, message = _read_json_list(path)
    if code != Code.OK:
        print(f"警告: 编译数据库不可用，按空处理 {path}: {message}",
              file=sys.stderr)
        return []
    return [entry for entry in (_entry_of(item) for item in raw)
            if entry is not None]


def merge_databases(paths: Sequence[Path],
                    target: Path) -> tuple[Path | None, Code, str]:
    """把多份数据库合成一份写到 target，返回 (路径, 错误码, 说明)。

    主工程与测试是两套构建，编译参数不同（测试通常要多一套包含路径）。
    clang-tidy 的 -p 只读一个数据库、按文件路径匹配条目，只给主库会让整个
    测试目录判成"无法分析"，因此两份都要进同一个文件；同一文件出现多次时
    保留先出现的那条，避免后一份的参数覆盖前一份。
    """
    merged: list[Entry] = []
    seen: set[str] = set()
    sources = [Path(path) for path in paths if str(path)]
    for path in sources:
        for entry in read_entries(path):
            key = os.path.normpath(str(entry.file))
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
    if not merged:
        return None, Code.DB_MISSING, "合并后的编译数据库里没有条目"
    target = Path(target)
    written, code, message = _write_entries(
        target, [entry.to_json() for entry in merged])
    if code != Code.OK:
        return None, code, message
    return written, Code.OK, (
        f"已合并 {len(sources)} 份编译数据库，共 {len(merged)} 条")


# ------------------------------------------------------------ 两种构建系统

def _adopt(path: Path, build_dir: Path) -> tuple[Path | None, Code, str]:
    """把别处的数据库收拢到构建根。

    cmake 把数据库写在顶层构建目录，套一层子目录的项目就与使用方约定的位置
    不一致；两者不同时拷一份原文，而不是顺手改写内容——原文由构建系统产出，
    保持原样才能让"是不是 cmake 的产出"这类判断继续有效。
    """
    target = _database_path(build_dir)
    if os.path.abspath(path) == os.path.abspath(target):
        return target, Code.OK, ""
    text, code, message = _load_text(path)
    if code != Code.OK:
        return None, code, message
    return _write_text(target, text)


def _cmake_source(build_dir: Path) -> tuple[Path | None, Code, str]:
    """cmake 自己导出的那一份。

    cmake 会在二进制目录留下 CMakeCache.txt，同目录有它的数据库最可信。但
    **不能要求它必须在**：编排里 ci 跑完会清构建目录、只留编译数据库（那是
    另一个项目的输入），此时缓存已经不在，而数据库本身仍然可用——里面的
    绝对路径与编译命令都是完整的。缺缓存时照用并给出提示，由使用者判断要不要
    重跑一次构建，而不是直接拒绝。
    """
    build_dir = Path(build_dir)
    direct = _database_path(build_dir)
    candidates: list[Path] = []
    if direct.is_file():
        candidates.append(direct)
    if build_dir.is_dir():
        # 有的项目在构建目录里再套一层真正的 cmake 构建目录，往下找一层
        for root, dirs, names in os.walk(build_dir):
            dirs.sort()
            if DB_NAME in names:
                path = Path(root) / DB_NAME
                if path != direct:
                    candidates.append(path)
    for path in candidates:
        if (path.parent / "CMakeCache.txt").is_file():
            return _adopt(path, build_dir)
    if candidates:
        adopted, code, message = _adopt(candidates[0], build_dir)
        if code != Code.OK:
            return adopted, code, message
        # 走 stderr 而不是改写返回的说明：那条说明是"数据库来自哪里"，
        # 由调用方原样展示；提示是旁注，与本函数已有的旁注同一路
        print(f"提示: {DB_NAME} 同目录没有 CMakeCache.txt"
              f"（构建目录被清理过？）；数据库本身可用，结果可疑时{_BUILD_FIRST}",
              file=sys.stderr)
        return adopted, Code.OK, message
    if not build_dir.is_dir():
        return None, Code.DB_MISSING, f"构建目录不存在: {build_dir}；{_BUILD_FIRST}"
    return None, Code.DB_MISSING, (
        f"未找到 {DB_NAME}（cmake 需加 -DCMAKE_EXPORT_COMPILE_COMMANDS=ON）；"
        f"{_BUILD_FIRST}")


# dry-run 走不通的迹象：进不去子目录、缺目标、命令不存在
_DRY_RUN_ERRORS = ("can't cd to", "No such file or directory",
                   "No rule to make target", "command not found")


def _dry_run_breakage(output: str) -> str:
    """dry-run 输出里是否出现"没走通"的迹象，出现则返回命中的那一句。"""
    for line in output.splitlines():
        for token in _DRY_RUN_ERRORS:
            if token in line:
                return line.strip()[:120]
    return ""


def _find_makefile(directory: Path) -> Path | None:
    """按 make 自身的查找顺序找 Makefile，找不到返回空。"""
    for name in _MAKEFILE_NAMES:
        candidate = Path(directory) / name
        if candidate.is_file():
            return candidate
    return None


def _compiledb_source(build_dir: Path) -> tuple[Path | None, Code, str]:
    """compiledb 解析 make 的输出，不真跑构建。

    -o 与 -d 必须给绝对路径：compiledb 在自己的进程里解析这两个值，相对路径
    会以它的工作目录为基准，结果被写到别处而我们还在原地等一份不存在的文件。
    -n 表示只解析不执行构建，编译数据库要的是编译参数而不是产物。
    """
    binary, code, _ = _find_tool(("compiledb",))
    if code != Code.OK:
        return None, Code.TOOL_MISSING, (
            "qmake 工程的编译数据库由 compiledb 产出，当前找不到它："
            "pip install compiledb")
    build_dir = Path(build_dir)
    if not _find_makefile(build_dir):
        return None, Code.DB_MISSING, (
            f"构建目录内没有 Makefile，compiledb 无从解析；{_BUILD_FIRST}")
    root = build_dir.resolve()
    target = root / DB_NAME
    code_value, stdout, stderr = _run(
        [binary, "-n", "-S", "-d", root, "-f", "-o", target, "make"],
        cwd=root, env=c_locale_env(), timeout=_PROVIDER_TIMEOUT)
    if code_value is None:
        return None, Code.COMMAND_UNRUNNABLE, stderr
    if code_value != 0:
        return None, Code.COMMAND_UNRUNNABLE, \
            f"compiledb 退出码 {code_value}: {stderr.strip()}"
    if not target.is_file():
        return None, Code.DB_MISSING, "compiledb 没有写出编译数据库"
    if not _entries_of(target):
        return None, Code.DB_MISSING, "compiledb 产出的数据库里没有条目"
    # dry-run 中途失败会产出一份"能读但残缺"的数据库：make 进不去某个子目录时
    # 只打印一行错误继续往下走，条目数却少了一截。半份结果比失败更难发现，
    # 因此在这里就判为不可用
    broken = _dry_run_breakage(stdout + stderr)
    if broken:
        return None, Code.DB_MISSING, (
            f"compiledb 的 dry-run 未走通（{broken}），产出的数据库不完整；"
            f"{_BUILD_FIRST}")
    return target, Code.OK, ""


# 构建系统到取库方式的映射。没有"哪个先成用哪个"的余地：项目用哪种构建系统
# 是配置里写死的，取库方式随之确定
_SOURCES: dict[str, tuple[str, Callable[[Path], tuple[Path | None, Code, str]]]] = {
    "cmake": ("cmake", _cmake_source),
    "qmake": ("compiledb", _compiledb_source),
    "qmake6": ("compiledb", _compiledb_source),
}


def _source_for(config) -> tuple[str, Callable, Code, str]:
    """按构建系统给出取库方式，返回 (名字, 取库函数, 错误码, 说明)。"""
    tool = getattr(config, "build_tool", "") or ""
    found = _SOURCES.get(tool)
    if found is None:
        return "", _cmake_source, Code.CONFIG_BAD_VALUE, (
            f"未知构建工具 {tool or '(空)'}，无法确定编译数据库的来源")
    return found[0], found[1], Code.OK, ""


# ------------------------------------------------------------------ 缓存

def _stamp(build_dir: Path) -> dict:
    """记录缓存依赖的构建文件及其 mtime，含子目录里的。

    递归 Makefile 工程里真正决定参数的文件在子目录，只看顶层会让子目录的
    改动（或后来才生成的 Makefile）无法让缓存失效。深度设上限是为了不跟着
    构建产物目录一路走下去。
    """
    build_dir = Path(build_dir)
    result: dict[str, float] = {}
    for name in _CACHE_INPUTS:
        path = build_dir / name
        if path.is_file():
            result[name] = _mtime(path)
    if not build_dir.is_dir():
        return result
    base = build_dir.resolve()
    for root, dirs, names in os.walk(base):
        depth = len(Path(root).relative_to(base).parts)
        if depth >= _STAMP_MAX_DEPTH:
            dirs[:] = []
            continue
        dirs[:] = [item for item in dirs if not item.startswith(".")]
        for name in names:
            if name not in _CACHE_INPUTS:
                continue
            path = Path(root) / name
            key = str(path.relative_to(base))
            if key not in result:
                result[key] = _mtime(path)
    return result


def _mtime(path: Path) -> float:
    """取 mtime；文件在遍历途中被删掉时给 0，让缓存自然失效。"""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _cache_path(build_dir: Path) -> Path:
    return Path(build_dir) / DB_CACHE


def _cache_read(build_dir: Path) -> tuple[dict | None, Code, str]:
    """读缓存，返回 (数据, 错误码, 说明)。"""
    path = _cache_path(build_dir)
    if not path.is_file():
        return None, Code.DB_MISSING, "没有缓存"
    text, code, message = _load_text(path)
    if code != Code.OK:
        return None, code, message
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, Code.DB_MISSING, f"缓存损坏: {exc}"
    if not isinstance(data, dict):
        return None, Code.DB_MISSING, "缓存格式不是对象"
    return data, Code.OK, ""


def _cache_fresh(build_dir: Path, source: str) -> tuple[bool, str, str]:
    """判断缓存能否复用，返回 (是否可用, 记录里的来源, 原因)。

    来源不同也不复用：换了取库方式说明产出形状可能变了，重取一次比猜便宜。
    """
    if not _database_path(build_dir).is_file():
        return False, "", "数据库文件不存在"
    data, code, message = _cache_read(build_dir)
    if code != Code.OK or data is None:
        return False, "", message
    provider = str(data.get("provider", ""))
    if provider != source:
        return False, provider, f"缓存由 {provider or '(未知)'} 产出"
    if data.get("stamp") != _stamp(build_dir):
        return False, provider, "构建文件已变化"
    return True, provider, ""


def _cache_write(build_dir: Path, source: str, entries: Iterable[Entry]) -> None:
    """记录生成结果，供下次直接复用。

    缓存只是加速手段，写不进去不影响本次结果，因此这里没有返回值。
    """
    data = {
        "provider": source,
        "created": time.time(),
        "stamp": _stamp(build_dir),
        "files": sorted({str(entry.file) for entry in entries}),
    }
    _write_text(_cache_path(build_dir), json.dumps(data, indent=1))


# ------------------------------------------------------------------ 入口

def _database_for(config, build_dir: Path) -> tuple[Path | None, Code, str]:
    """为单个构建目录取得一份编译数据库。

    顺序是"先看缓存、再按构建系统取一次"：缓存命中省掉一次外部调用。
    """
    build_dir = Path(build_dir)
    source, fetch, code, message = _source_for(config)
    if code != Code.OK:
        return None, code, message
    fresh, cached, _ = _cache_fresh(build_dir, source)
    if fresh:
        return _database_path(build_dir), Code.OK, (
            f"编译数据库来自缓存（{cached}），无需重新生成")
    path, code, message = fetch(build_dir)
    if code != Code.OK:
        return None, code, message
    entries = _entries_of(path)
    if not entries:
        return None, Code.DB_MISSING, f"{source} 的产出里没有可用的编译条目"
    _cache_write(build_dir, source, entries)
    return path, Code.OK, f"编译数据库由 {source} 提供: {path}"


def _check_directories(path: Path) -> tuple[Code, str]:
    """编译数据库里每条记录的 directory 是否还在。

    clang-tidy 进不去这个目录时会**直接中止**（LLVM ERROR: Cannot chdir into
    ...），一个翻译单元都分析不了，报出来的是一段崩溃回溯，跟真正的原因
    （"先跑一次完整构建"）隔了十万八千里。这里提前说清。

    实测触发场景：只跑过 ci 的 --only build（没建测试目录），数据库里测试源
    的 directory 指向不存在的 build/tests。
    """
    entries = _entries_of(path)
    missing = sorted({str(item.directory) for item in entries
                      if str(item.directory) and not item.directory.is_dir()})
    if not missing:
        return Code.OK, ""
    return Code.DB_STALE_DIRECTORY, (
        "编译数据库里有 {} 条记录的构建目录不存在，clang-tidy 会直接中止: "
        "{}。先跑一次完整构建（含测试），或用 --changed-only 缩小范围"
        .format(len(missing), "、".join(missing[:3])))


def ensure_database(config,
                    build_dirs: Sequence[Path]) -> tuple[Path | None, Code, str]:
    """为每个构建目录取得编译数据库，多份则合并成一份。

    build_dirs 的顺序即主次：第一个是主构建目录，其余是测试等附属构建目录。
    每个目录都要有一份——测试构建多带一套包含路径，缺了它 clang-tidy 会把整个
    测试目录判成无法分析。两份都在时合并到日志目录下的一份数据库，因为 -p
    只读一个文件、按文件路径匹配条目。

    主构建目录必须有产出，否则按它的真实原因失败（工具缺失就说工具缺失，
    配置不对就说配置不对，别一律说成"没有数据库"）；附属构建目录只降级：给出
    提示后继续用已有的那份，避免测试目录的问题连累整条风格检查。
    """
    dirs = [Path(item) for item in build_dirs if str(item)]
    if not dirs:
        return None, Code.DB_MISSING, "没有可用的构建目录"

    results = [(build_dir,) + _database_for(config, build_dir)
               for build_dir in dirs]
    _, main_path, main_code, main_message = results[0]
    if main_code != Code.OK or main_path is None:
        return None, main_code, main_message

    for extra_dir, _, extra_code, extra_message in results[1:]:
        if extra_code != Code.OK:
            print(f"提示: 构建目录 {extra_dir} 未取到编译数据库，"
                  f"继续使用已有的那份: {extra_message}", file=sys.stderr)

    available = [item for item in results if item[2] == Code.OK]
    if len(available) == 1:
        code, message = _check_directories(available[0][1])
        if code != Code.OK:
            return None, code, message
        return available[0][1], Code.OK, available[0][3]

    log_dir = getattr(config, "log_dir", "") or ""
    if not log_dir:
        # 没有日志目录就无处安放合并结果：先用主库那一份，别让调用方拿到空
        return available[0][1], Code.OK, available[0][3]
    target = Path(log_dir) / DB_NAME
    sources = [item[1] for item in available if item[1] is not None]
    merged, code, message = merge_databases(sources, target)
    if code != Code.OK:
        return None, code, message
    return merged, Code.OK, message
