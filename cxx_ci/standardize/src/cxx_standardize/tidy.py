"""clang-tidy 的结构化诊断与修复。

`--export-fixes` 是 clang-tidy 的机器接口：每条诊断给出检查名、级别、文件与
字节偏移，以及该诊断对应的替换。相比解析人读文本，这里有两点是决定性的：

  - 检查名是权威字段，不必从方括号里猜——真检查名后面还会跟
    `-warnings-as-errors` 这类别名，靠文本切分迟早出错；
  - 替换交给官方的 clang-apply-replacements 应用，跨文件的改名修复由它处理，
    不必自己实现替换合并。

YAML 由 PyYAML 解析：它是这条接口的既定读法，自造子集解析器只会再添一处需要
维护的格式知识。缺包时报错并说明装法，不静默退回文本解析。

clang-tidy 只在确实有诊断时才写出该文件，因此"文件不存在"等价于"没有诊断"。
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

from . import paths
from . import source as source_mod
from .errors import Code
from .models import Finding, Level

# clang-tidy 的级别名到本项目的级别
_LEVELS = {
    "error": Level.ERROR,
    "warning": Level.WARNING,
    "remark": Level.NOTE,
    "note": Level.NOTE,
}


# 命名改名一律人工。clang-tidy 17 的 identifier-naming 没有 FixIt 开关（实测
# 每个 kind 只有 Case/Prefix/Suffix/IgnoredRegexp），-fix 只要产出 fix-it 就会
# 执行改名，而且只对违规名盲目前缀叠加、从不脱前缀（v_foo → _v_foo、
# g_x → s_g_x 这类垃圾）。更麻烦的是声明与定义分处头文件与源文件：改名只落在
# 被分析的那一个 TU 里，实测把能编译的工程改成编译不过。因此风格修复整体关掉
# 本检查，命名由人工处理。
NAMING_CHECK = "readability-identifier-naming"

# 局部四类：规范要求裸名（`_` 前缀只是"临时值"惯例，人为维护），因此自动修复
# 只做大小写转换，不往局部变量上加前缀。派生配置里把它们的 Prefix 清空、
# 豁免收紧到只放行 `_` 惯例名
LOCAL_KINDS = ("LocalVariable", "LocalConstant", "LocalPointer",
               "LocalConstantPointer")

def command(binary, config, config_file, path, fixes_path, database_dir=None,
            checks=None):
    """构造 clang-tidy 命令。

    database_dir 是存放 compile_commands.json 的目录；主工程与测试两套构建的
    库会先合并再放进去，缺了就只能由 clang-tidy 自行推断编译参数。
    checks 给出时用 --checks 覆盖配置里的 Checks（命令行优先）。
    """
    cmd = [str(binary)]
    if checks:
        cmd.append("--checks=" + checks)
    if config_file and Path(config_file).is_file():
        cmd.append(f"--config-file={config_file}")
    # 命令行优先于项目 .clang-tidy 的 HeaderFilterRegex：不强制的话，把它改宽
    # 会让外部头噪音涌入（--apply 还可能写到那里），改窄会把本仓头文件的诊断
    # 静默藏掉。两条通道各管各的：Checks 仍走 --config-file
    header_filter = getattr(config, "tidy_header_filter", "")
    if header_filter:
        cmd.append(f"--header-filter={header_filter}")
    directory = Path(database_dir or config.build_dir)
    if (directory / "compile_commands.json").is_file():
        cmd += ["-p", str(directory)]
    # 项目可通过 CLANG_TIDY_EXTRA_ARGS 屏蔽第三方头文件的已知误报
    for extra in config.clang_tidy_extra_args:
        cmd.append(f"--extra-arg-before={extra}")
    cmd += [f"--export-fixes={fixes_path}", str(path)]
    # 两种模式共用同一套判定，否则 --apply 通过而随后的预览失败
    cmd.append("--warnings-as-errors=*")
    return cmd


class DeterministicConflict(Exception):
    """同一处替换在不同翻译单元里给出的文本不一致。

    同配置、同源码（导出期只读，所有翻译单元看的是同一棵干净的树）下，同一处
    声明被多个 TU 各报一次，替换文本必然逐字节相同。出现不一致只可能是配置或
    环境不确定——这时**拒跑**，绝不猜。
    """


# 诊断消息里的 kind："invalid case style for static constant 'x'" → StaticConstant。
# clang-tidy 只用这一种句式报命名问题，映射是机械的（首字母大写、去掉空格）
_KIND_OF_MESSAGE = re.compile(r"invalid case style for ([a-z ]+?) '")


def kind_of(message):
    """从诊断消息里取出 kind 名（与 .clang-tidy 的键名同形）。"""
    match = _KIND_OF_MESSAGE.search(str(message or ""))
    if not match:
        return ""
    return "".join(word.capitalize() for word in match.group(1).split())


def raw_replacements(fix_files):
    """把若干份 --export-fixes 合并成 {(文件, 字节偏移): (长度, 文本, kind)}。

    这是原子改名的输入：按**字节偏移**索引，而不是按行号或诊断对象。同一处
    替换会在多个 TU 的导出里重复出现（每个包含它的 .cpp 各给一次），合并即去重；
    不一致则抛 DeterministicConflict。
    """
    merged = {}
    for path in fix_files:
        for document in _documents(path):
            for item in document.get("Diagnostics") or []:
                message = item.get("DiagnosticMessage") or {}
                # 相对路径的基准是诊断记下的构建目录，不是运行目录：qmake 的
                # 编译数据库里 FilePath 就是 ../../src/x.cpp 这种形状
                base = str(item.get("BuildDirectory") or "")
                for replacement in message.get("Replacements") or []:
                    if not isinstance(replacement, dict):
                        continue
                    target = _resolved(replacement.get("FilePath"), base)
                    offset = replacement.get("Offset")
                    if not target or offset is None:
                        continue
                    key = (os.path.normpath(target), int(offset))
                    value = (int(replacement.get("Length") or 0),
                             str(replacement.get("ReplacementText") or ""),
                             kind_of(message.get("Message")))
                    if key in merged and merged[key] != value:
                        raise DeterministicConflict(
                            "{}@{}: {!r} vs {!r}".format(key[0], key[1],
                                                         merged[key], value))
                    merged[key] = value
    return merged


# 字符串字面量（含转义）
_LITERAL = re.compile(r'"(?:[^"\\]|\\.)*"')
# Qt 的 SIGNAL/SLOT 宏把参数**字符串化**：SIGNAL(taskAdded(int)) 展开成
# "2taskAdded(int)"，连接是运行时按名字查的。改了信号名而不改这里，连接会静默
# 失效——实测一个工程的信号测试就是这么挂的，而且编译期毫无提示
_SIGNAL_MACRO = re.compile(r"\b(?:SIGNAL|SLOT)\s*\(([^)]*)\)")

# 测试文件所在目录的名字。断言消息里常把标识符串成文本，改了标识符而字符串
# 没改，构建照样过、测试却挂了——实测一个工程就是这么挂掉的（测试断言
# `条件不成立: input > 0`，而 `input` 被改成了 `v_input`）
TEST_DIRS = ("test", "tests", "testing")


def _literals_of(path):
    """一个文件里全部字符串字面量。"""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"提示: 无法读取 {path}，字符串扫描已跳过: {exc}", file=sys.stderr)
        return []
    return [match.group(0) for match in _LITERAL.finditer(text)]


def anchored_tokens(paths):
    """在**非测试文件**的字符串字面量里出现过的标识符。

    判断依据：运行时的文本如果来自库里的字面量（`return "info";`、
    `"code=0x%08X"`、JSON 键 `"code"`），那这个文本跟标识符改不改名毫无关系——
    改了标识符，文本不变。这时若把测试里的期望字符串一起改掉，反而把本来能过的
    测试改挂（实测三个断言就是这么挂的）。
    反过来，文本若由标识符**派生**（宏把参数串成 `"条件不成立: input > 0"`），
    库里就找不到这个字面量，那才需要同步。
    """
    found = set()
    for path in paths:
        parts = {part.lower() for part in Path(path).parts}
        if parts.intersection(TEST_DIRS):
            continue
        for literal in _literals_of(path):
            found.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", literal))
    return found


def rename_in_test_strings(pairs, paths, config=None):
    """把改名同步到测试文件里的字符串字面量，返回改动过的文件。

    断言消息常把标识符串成文本（`CHECK(input > 0)` 生成的
    `条件不成立: input > 0`）。只改标识符不改这些字符串，构建照样过、测试却挂了
    ——实测一个工程就是这么挂掉的。因此改名时一并把测试文件字符串里的整词换掉。

    只扫测试文件：库里的日志文本普遍会提到某个词（`"message"`、`"name"`），
    按"字符串里出现过"一概改会误伤无关文本；真正需要同步的是断言里的期望字符串。
    """
    # 只同步"库里没有同名字面量"的名字：有字面量的说明运行时文本不随标识符变
    anchored = anchored_tokens(paths)
    # 同一个基名映射到多个目标（成员 tally → t_tally、参数 tally → v_tally）时，
    # 光看字符串分不出它指的是哪一个。这种不猜，交给验证与人工
    targets = {}
    for old, new in pairs:
        targets.setdefault(old, set()).add(new)
    ambiguous = sorted(old for old, values in targets.items()
                       if len(values) > 1)
    renames = {old: new for old, new in pairs
               if old not in anchored and old not in ambiguous}
    if ambiguous:
        print("提示: 以下名字本轮映射到多个目标，测试字符串不自动同步: "
              + "、".join(ambiguous), file=sys.stderr)
    if not renames:
        return []
    changed = []
    for path in paths:
        parts = {part.lower() for part in Path(path).parts}
        if not parts.intersection(TEST_DIRS):
            continue
        try:
            text, info = source_mod.read_source(path, config)
        except OSError as exc:
            print(f"提示: 无法读取 {path}，字符串同步已跳过: {exc}",
                  file=sys.stderr)
            continue
        if text is None:
            print(f"提示: 编码无法确定（{info.reason}）: {path}，"
                  "字符串同步已跳过", file=sys.stderr)
            continue
        updated = _LITERAL.sub(
            lambda match: _rename_literal(match.group(0), renames), text)
        updated = _SIGNAL_MACRO.sub(
            lambda match: "SIGNAL(" + _rename_words(match.group(1), renames)
            + ")" if match.group(0).lstrip().upper().startswith("SIGNAL")
            else "SLOT(" + _rename_words(match.group(1), renames) + ")",
            updated)
        if updated != text:
            code, message = source_mod.write_source(path, updated, info)
            if code != 0:
                print(f"提示: {message}", file=sys.stderr)
                continue
            changed.append(Path(path))
    return changed


def _rename_literal(literal, renames):
    """在一个字符串字面量里做整词替换。"""
    return _rename_words(literal, renames)


def _rename_words(text, renames):
    """对一段文本做整词替换。"""
    updated = text
    for old, new in renames.items():
        if old not in updated:
            continue
        updated = re.sub(r"(?<![A-Za-z0-9_])" + re.escape(old)
                         + r"(?![A-Za-z0-9_])", new, updated)
    return updated


def unfixed_items(fix_files):
    """报了诊断却没有修复块的 (文件, 检查名) 列表。

    调用方要拿它跟登记清单比对：已登记的算已知项，不该继续拦。
    """
    found = []
    for path in fix_files:
        for document in _documents(path):
            for item in document.get("Diagnostics") or []:
                message = item.get("DiagnosticMessage") or {}
                if message.get("Replacements"):
                    continue
                base = str(item.get("BuildDirectory") or "")
                target = _resolved(message.get("FilePath"), base)
                if target:
                    found.append((target,
                                  str(item.get("DiagnosticName") or "")))
    return found


def unfixable(fix_files):
    """报了诊断却没给修复块的检查名。

    clang-tidy 每个检查由作者决定要不要写机械改写规则：写了才有 fix-it，
    没写就只能报告（bugprone-empty-catch、clang-analyzer-* 都是这样）。这些
    改不动，必须让人知道——否则"跑完了还剩几条"看起来像工具没干活。
    """
    found = {}
    for path in fix_files:
        for document in _documents(path):
            for item in document.get("Diagnostics") or []:
                name = str(item.get("DiagnosticName") or "")
                message = item.get("DiagnosticMessage") or {}
                if not name:
                    continue
                has_fix = bool(message.get("Replacements"))
                found[name] = found.get(name, False) or has_fix
    return sorted(name for name, has_fix in found.items() if not has_fix)


def apply_by_offset(replacements, config=None):
    """按字节偏移降序直写，返回改动过的文件列表。

    绕开 clang-apply-replacements：同一个文件内从后往前改，先改后面的不会挪动
    前面还没用的偏移；Offset/Length 是字节语义，因此全程按 bytes 操作。
    官方应用器会把两处重叠的 fix-it 叠在一起，实测写出
    `static_cast<std::size_t>(1024 * )1024` 这种编译不过的文本——这里不存在
    那个中间步骤。

    换入的文本必须按**原文件的编码**编码：GBK 存量工程里塞进 UTF-8 字节，混编
    后的文件两种解码都读不出，等于把文件写坏。判定不出编码的文件整份跳过。
    """
    by_file = {}
    for (target, offset), record in replacements.items():
        by_file.setdefault(target, []).append((offset, record[0], record[1]))
    touched = []
    for target, items in sorted(by_file.items()):
        path = Path(target)
        try:
            data = path.read_bytes()
        except OSError as exc:
            print(f"提示: 无法读取 {target}，该文件的替换已跳过: {exc}",
                  file=sys.stderr)
            continue
        _text, info = source_mod.read_source(path, config)
        if not info.known:
            print(f"提示: 编码无法确定（{info.reason}）: {target}，"
                  "该文件的替换已跳过", file=sys.stderr)
            continue
        try:
            for offset, length, text in sorted(items, key=lambda item: -item[0]):
                data = (data[:offset] + text.encode(info.encoding)
                        + data[offset + length:])
        except UnicodeEncodeError as exc:
            print(f"提示: 原文是 {info.encoding}，替换文本有它表示不了的字符"
                  f"（{exc}）: {target}，该文件的替换已跳过", file=sys.stderr)
            continue
        hit, why = paths.write_blocked(config, path)
        if hit:
            log.err("拒绝改写 {}（{}）".format(path, why))
            continue
        _code, message = source_mod.write_bytes(path, data)
        if _code != 0:
            log.err(message)
        touched.append(path)
    return touched


def _documents(path):
    """一份修复文件里的全部 YAML 文档。"""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return []
    try:
        return [item for item in yaml.safe_load_all(raw.decode("utf-8", "replace"))
                if isinstance(item, dict)]
    except yaml.YAMLError:
        return []


def _config_text(config_file):
    """项目配置的文本；读不出来时返回空串。"""
    if not config_file:
        return ""
    path = Path(config_file)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def without_naming_config(config_file, target):
    """写一份"关掉命名检查"的派生配置，返回路径（写不出时返回空串）。

    不用命令行的 --checks：它是**覆盖**而不是追加，必须把 clang-tidy 的完整
    生效列表一字不差地复现出来——漏掉它自己补的 clang-diagnostic-*、或者多一个
    空格，整份列表就解析不出来，clang-tidy 一条诊断都不报，看上去像"全部符合"。
    改配置文本就绕开了这件事。
    """
    document = _config_document(config_file)
    if document is None:
        return ""
    checks = str(document.get("Checks") or "").strip()
    if checks and NAMING_CHECK not in checks:
        document["Checks"] = checks.rstrip().rstrip(",") + ", -" + NAMING_CHECK
    return _write_config(document, target)


def _config_document(config_file):
    """把项目配置读成映射；读不出来时返回 None。"""
    text = _config_text(config_file)
    if not text:
        return None
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return document if isinstance(document, dict) else None


# YAML 的默认折行宽度是 80：Checks 这类长标量会被**换行**写出去，而换行符会
# 变成取值的一部分，clang-tidy 的解析器读到的就不是一份检查列表了——实测一条
# 诊断都不报，看上去像"全部符合"。写配置时一律不折行
_NO_WRAP = 100000


def _write_config(document, target):
    try:
        with open(str(target), "w", encoding="utf-8") as handle:
            yaml.safe_dump(document, handle, allow_unicode=True, width=_NO_WRAP)
    except OSError:
        return ""
    return str(target)


def run(cmd):
    """执行 clang-tidy，返回 (退出码, 输出文本)。"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return Code.COMMAND_UNRUNNABLE, str(exc)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def classify(code, output):
    """判断一次 clang-tidy 的结果。

    返回 "clean"（无问题）、"pending"（有风格问题）或 "unanalyzable"
    （编译错误导致无法分析，多半是第三方头文件与当前 clang 不兼容）。后者
    不能算通过，否则会给出"全部符合风格"的假结论。
    """
    if "clang-diagnostic-error" in output:
        return "unanalyzable"
    return "pending" if code != 0 else "clean"


def location(data, offset):
    """把字节偏移换算成 1 基的行号与字符列号。

    FileOffset 是字节偏移，而列号是给人和给登记清单用的字符位置，因此先按
    换行切出该行再按 UTF-8 解码计数，避免多字节字符让列号偏移。
    """
    if offset < 0 or offset > len(data):
        return 0, 0
    line = data.count(b"\n", 0, offset) + 1
    start = data.rfind(b"\n", 0, offset) + 1
    column = len(data[start:offset].decode("utf-8", "replace")) + 1
    return line, column


def _resolved(raw, base):
    """把替换块里的路径归一化成绝对路径。

    给出的是相对路径时以诊断记下的构建目录为基准——那才是编译器的工作目录。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute() and base:
        path = Path(base) / path
    return str(Path(os.path.normpath(path)))


def _target(record, base):
    """诊断里的文件路径归一化成绝对路径。

    给出的是相对路径时以 clang-tidy 记录的构建目录为基准——它才是编译器的
    工作目录。
    """
    directory = str(record.get("BuildDirectory") or "") or str(base or "")
    text = _resolved(record.get("FilePath"), directory)
    return Path(text) if text else None


def _targets_of(message, base):
    """一条诊断的替换会写到哪些文件。

    替换嵌在 DiagnosticMessage 里（不是诊断层），字段名是 FilePath。落点通常
    就是诊断所在文件，但改名会跨文件——只按命令行目标判断"能改谁"会漏掉这种
    间接写入。
    """
    found = []
    for item in message.get("Replacements") or []:
        if not isinstance(item, dict):
            continue
        target = _target({"FilePath": item.get("FilePath")}, base)
        if target is not None and target not in found:
            found.append(target)
    return tuple(found)


def read_fixes(path, root=""):
    """读取一份 --export-fixes 结果，返回 (诊断列表, 错误码, 说明)。

    文件不存在等价于没有诊断：clang-tidy 只在确实有诊断时才写它。
    """
    if not path or not Path(path).is_file():
        return [], Code.OK, ""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, Code.TIDY_FIXES_BAD_FILE, f"无法读取 clang-tidy 修复文件: {path}: {exc}"
    try:
        document = yaml.safe_load(raw.decode("utf-8", "replace"))
    except yaml.YAMLError as exc:
        return None, Code.TIDY_FIXES_BAD_FILE, f"clang-tidy 修复文件结构损坏: {path}: {exc}"
    if not document:
        return [], Code.OK, ""
    if not isinstance(document, dict):
        return None, Code.TIDY_FIXES_BAD_FILE, f"clang-tidy 修复文件顶层不是映射: {path}"
    found = []
    for item in document.get("Diagnostics") or []:
        if not isinstance(item, dict):
            continue
        message = item.get("DiagnosticMessage") or {}
        if not isinstance(message, dict):
            continue
        base = str(item.get("BuildDirectory") or root or "").strip()
        target = _target(message, base)
        if target is None:
            continue
        line, column = 0, 0
        try:
            data = Path(target).read_bytes()
        except OSError:
            # 读不到源文件时保留诊断但无从定位，行号留 0 由展示层如实呈现
            pass
        else:
            line, column = location(data, int(message.get("FileOffset") or 0))
        level = _LEVELS.get(str(item.get("Level") or "").lower(), Level.WARNING)
        found.append(Finding(
            file=target, line=line, column=column, level=level,
            message=str(message.get("Message") or "").strip(),
            check=str(item.get("DiagnosticName") or "").strip(),
            targets=_targets_of(message, base)))
    return found, Code.OK, ""
