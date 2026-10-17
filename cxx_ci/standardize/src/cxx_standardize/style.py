"""clang-tidy 风格检查与整理。

预览模式把警告视为错误，存在问题时返回 PENDING；`--apply` 逐文件修复。

修复由 clang-tidy 导出、clang-apply-replacements 应用：改名会跨文件生效，
官方的应用器会一并处理头文件里的替换，自己实现替换合并只会漏掉这些。即便如此，
改名类修复仍可能只改声明处而让别的文件编译不过，因此每次应用后都跑一次
`VERIFY_COMMAND`；失败就整体回到最近一次通过的状态，并把该文件转人工。
"""

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import (compile_db, log, paths, prefix,
               source as source_mod, tidy, xfail)
from .errors import Code
from .models import Finding

# 分析前需要生成的派生文件目标前缀。qmake 把 moc_xxx.cpp 做成独立目标，
# 缺了这些文件，clang-tidy 会把 #include "X.moc" 的 TU 报成编译错误，
# 并对该 TU 抑制全部 fix-it。
GENERATED_PREFIXES = ("moc_", "ui_", "qrc_")

HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx", ".h++", ".inl", ".ipp")
SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".c++", ".cu", ".cuh")
ALL_SUFFIXES = SOURCE_SUFFIXES + HEADER_SUFFIXES


@dataclass
class Options:
    """一次运行的处理范围与开关。"""

    paths: list[Path] = field(default_factory=list)
    exclude: list[Path] = field(default_factory=list)
    config_file: str = ""
    apply: bool = False
    # 命名改名默认不做：clang-tidy 只改它分析得到的引用，声明在头文件里时甚至
    # 不产出修复。打开后改用"只做加前缀"的派生配置
    apply_naming: bool = False
    # 登记基线：把当前未登记的诊断写进清单，让门禁只对新增违规判红
    record: bool = False
    no_prepare: bool = False
    jobs: int = 0
    # guard 动作：自动补/改头文件宏守卫
    fix: bool = False


def _jobs(config, options):
    return options.jobs or config.jobs


def collect_files(config, options, suffixes=SOURCE_SUFFIXES):
    """按处理范围收集待处理文件。

    路径缺省取配置白名单，排除项缺省取黑名单；排除优先于包含。

    缺省只收**源文件**：头文件的诊断由 clang-tidy 按项目 .clang-tidy 的
    HeaderFilterRegex 在分析源文件时一并给出，直接再分析一遍头文件既重复又
    要多花一倍时间。显式点名某个头文件时照收（调用方要的就是它）。
    """
    roots = [Path(item) for item in (options.paths or config.whitelist)]
    excluded = [Path(item) for item in (options.exclude or config.blacklist)]
    found = []
    for root in roots:
        if root.is_file():
            found.append(root)
            continue
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            if any(paths.is_within(path, item) for item in excluded):
                continue
            found.append(path)
    # 源文件排在头文件之前：改名修复要在同一个翻译单元里先看声明处
    return sorted(set(found),
                  key=lambda p: (p.suffix in HEADER_SUFFIXES, str(p)))


def prepare(config):
    """生成派生文件（moc_/ui_/qrc_），返回 (是否完成, 说明)。

    缺了它们，含 `#include "X.moc"` 的 TU 会被 clang-tidy 判成编译错误，
    该 TU 的 fix-it 也一并被抑制——不是"分析结果差一点"，而是完全不可用。
    """
    build_dir = Path(config.build_dir)
    makefile = build_dir / "Makefile"
    if not makefile.is_file():
        return False, ""
    targets = []
    try:
        text = makefile.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, f"无法读取构建文件，跳过派生文件生成: {exc}"
    for line in text.splitlines():
        name = line.split(":", 1)[0].strip()
        if name and name not in targets and (
                name.startswith(GENERATED_PREFIXES)
                or name.endswith(GENERATED_PREFIXES)):
            targets.append(name)
    if not targets:
        return False, ""
    import subprocess

    log.out(f"生成 {len(targets)} 个派生文件目标")
    try:
        result = subprocess.run(["make"] + targets, cwd=str(build_dir),
                                capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"派生文件生成失败，继续分析: {exc}"
    log.noise(config, "style", (result.stdout or "") + (result.stderr or ""))
    if result.returncode != 0:
        # 这不是"差一点"：缺了 moc_/ui_ 的翻译单元会被判成编译错误，该单元的
        # fix-it 也一并被抑制，因此必须说出来而不是只落盘
        return False, (f"派生文件生成失败（退出码 {result.returncode}），"
                       f"继续分析；含 #include \"X.moc\" 的翻译单元可能被判成"
                       f"编译错误")
    return True, ""


def ensure_database_with_build(config):
    """取编译数据库；缺了或过期就先按 BUILD_COMMAND 构建一次，再取一遍。

    编译数据库是构建的产物。构建树被清理过、或只跑过部分构建时，库里的
    directory 会指向不存在的路径——clang-tidy 碰到这种条目会**直接中止**
    （LLVM ERROR: Cannot chdir into ...），一个翻译单元都分析不了。

    BUILD_COMMAND 由项目自己声明（例如跑 ci 的 build 与 build_test）。工具不
    猜该怎么构建：没配就维持原样报错并说明。
    """
    database, code, message = ensure_database(config)
    if code == Code.OK:
        return database, code, message
    if code not in (Code.DB_MISSING, Code.DB_STALE_DIRECTORY):
        return database, code, message
    command = list(getattr(config, "build_command", []) or [])
    if not command:
        return database, code, message
    reason = message.split("；")[0].split("。")[0]
    log.out("编译数据库不可用（{}），先按 BUILD_COMMAND 构建一次".format(reason))
    result = subprocess.run([str(item) for item in command],
                            cwd=str(config.root))
    if result.returncode != 0:
        return None, Code.BUILD_COMMAND_FAILED, (
            "BUILD_COMMAND 失败（退出码 {}），编译数据库仍不可用"
            .format(result.returncode))
    again, acode, amessage = ensure_database(config)
    if acode != Code.OK:
        return None, acode, "构建后编译数据库仍不可用: " + amessage
    return again, acode, amessage


def ensure_database(config):
    """按需自愈编译数据库，返回 (数据库路径, 错误码, 一行说明)。

    主工程与测试是两套构建：测试的编译参数里有 QtTest 之类的包含路径，只用
    主库分析测试源会直接判成"无法分析"。两套都在时合并成一份——clang-tidy
    的 -p 只读一个数据库、按文件路径匹配条目。
    """
    return compile_db.ensure_database(config, config.build_dirs)


# 派生文件未就绪时的说明。record 与 apply 都在结论里说出来：不允许"0 个 TU 被
# 分析"与"全部 TU 干净"给出相同输出，与既有原则"未跑≠干净、跳过≠通过"同一条纪律
INCOMPLETE_NOTE = ("派生文件未就绪，结果不完整；含 #include \"X.moc\" 的翻译"
                   "单元可能未被完整分析")


@dataclass
class _Run:
    """一次运行的前置产物。

    check / record / apply 只在 `_prepare_run` 里做前置准备，之后各自只保留判定
    与写回。失败时 code 非 OK、message 说明原因；没有待处理文件时 early 为真，
    message 就是给使用者看的那句话。
    """

    files: list = field(default_factory=list)
    database_dir: Path | None = None
    config_file: str = ""
    checklist: object = None
    code: int = Code.OK
    message: str = ""
    early: bool = False
    prepare_failed: bool = False
    prepare_note: str = ""


def _prepare_run(config, options, need_database=True, need_checklist=True):
    """三入口共享的前置准备，只做一遍。

    顺序：生成派生文件 → 收集待处理文件 → 取编译数据库 → 校验风格配置文件 →
    读登记清单。任何一步失败都把错误码与说明放进返回值，由调用方决定
    怎么报。派生文件（moc_/ui_/qrc_）缺了会让含 #include "X.moc" 的翻译单元被判成
    编译错误、该 TU 的 fix-it 也一并被抑制，因此生成失败一律走 stderr，并把
    prepare_failed 带出去，供 record/apply 在结论里说明"结果不完整"。
    """
    run = _Run()
    if config.tidy_prepare and not options.no_prepare:
        done, note = prepare(config)
        if note:
            if done:
                log.noise(config, "style", note)
            else:
                # 生成失败时走 stderr：分析结果可能因此不完整，不该只落在日志里
                log.err(note)
                run.prepare_failed = True
                run.prepare_note = note

    files = collect_files(config, options)
    if not files:
        run.early = True
        run.message = "没有需要处理的文件"
        return run

    if need_database:
        database, code, message = ensure_database_with_build(config)
        if code != Code.OK:
            run.code = code
            run.message = message
            return run
        run.database_dir = Path(database).parent


    config_file = options.config_file or config.clang_tidy_config
    if config_file and not Path(config_file).is_file():
        run.code = Code.CONFIG_NOT_FOUND
        run.message = (f"风格配置文件不存在: {config_file}。请创建它，或在 "
                       f"CLANG_TIDY_CONFIG / --config-file 里指向真实文件")
        return run
    run.config_file = config_file

    if need_checklist:
        checklist, code, message = xfail.load_checklist(config)
        if code != Code.OK:
            run.code = code
            run.message = message
            return run
        run.checklist = checklist

    run.files = files
    return run


def _incomplete(config, run):
    """派生文件未就绪时，--strict（XFAIL_STRICT）下判失败；否则只出声、不影响退出码。

    "出声"由调用方在结论里打印 INCOMPLETE_NOTE，这一处只管退出码。
    """
    return bool(run.prepare_failed and config.xfail_strict)


def inspect(binary, config, config_file, path, fixes_path, database_dir=None,
            checks=None):
    """跑一次 clang-tidy，返回 (退出码, 输出, 诊断, 错误码, 说明)。"""
    code, output = tidy.run(tidy.command(binary, config, config_file, path,
                                         fixes_path, database_dir, checks))
    found, fcode, fmessage = tidy.read_fixes(fixes_path, config.root)
    return code, output, found, fcode, fmessage


def _classify(config, findings, checklist):
    """按登记清单把诊断分成已登记与新增，返回 (新增条数, 已登记条数)。"""
    fresh, known = 0, 0
    # 归属只算一次：它只取决于配置，与具体是哪条诊断无关
    project, excluded = paths.scope_roots(config)
    for item in findings:
        hit = checklist.style(item.file, item.line, item.check)
        if hit is not None:
            known += 1
            log.out(f"{item.describe()} (xfail)")
        else:
            fresh += 1
            log.out(item.describe())
    return fresh, known


def _is_header(path):
    return Path(path).suffix in HEADER_SUFFIXES


def _tally(config, findings, checklist, silent):
    """按文件分类诊断并按登记清单计数，返回 (待改文件数, 分文件)。"""
    pending = 0
    by_file: dict[Path, list[Finding]] = {}
    for item in findings:
        by_file.setdefault(item.file, []).append(item)
    for path, items in by_file.items():
        fresh, known = _classify(config, items, checklist)
        if known:
            log.out(f"{path}: 已登记 {known} 条（xfail），新增 {fresh} 条")
        if fresh:
            pending += 1
    pending += len(silent)
    return pending, by_file


def _announce(findings, by_file, skipped, pending):
    """报诊断条数、无法分析的文件。

    条数与文件数分开报：头文件里的诊断可能很多，只看文件数看不出量级；去重也要
    说出来——同一处被 N 个翻译单元包含时只计一次，否则条数会随"谁包含了这个头
    文件"而变，看起来像回归。
    """
    touched = set(by_file)
    headers = {item for item in touched if _is_header(item)}
    total = len(findings)
    header_hits = sum(1 for item in findings if _is_header(item.file))
    if total:
        log.out(f"风格诊断 {total} 条（去重后；源文件 {total - header_hits} / "
                f"头文件 {header_hits}），涉及 {len(touched)} 个文件"
                f"（源 {len(touched) - len(headers)} / 头 {len(headers)}）")
    if skipped:
        log.err(f"{len(skipped)} 个文件无法分析（编译错误，多来自第三方头文件），"
                f"另有 {pending} 个文件存在风格问题")
        for path in skipped:
            log.out(f"    {path}")


def check(config, options, tools):
    """预览：检查风格并给出结论，返回错误码。"""
    run = _prepare_run(config, options)
    if run.early:
        log.out(run.message)
        return Code.OK
    if run.code != Code.OK:
        log.err(run.message)
        return run.code

    workdir = Path(tempfile.mkdtemp(prefix="cxx_standardize_tidy_"))
    try:
        results = _scan(tools.clang_tidy, config, run.config_file, run.files,
                        workdir, run.database_dir, _jobs(config, options))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    findings, skipped, silent = _collect_results(results)
    for path, output in silent:
        log.out(output)
    pending, by_file = _tally(config, findings, run.checklist, silent)
    _announce(findings, by_file, skipped, pending)

    considered = [path for path in run.files if path not in skipped]
    code, notes = xfail.verdict(run.checklist, considered, config.xfail_strict)
    for note in notes:
        log.out(note)
    if code != Code.OK:
        log.err("xfail 清单有已修复的登记项，请清理后重跑")
        return code
    if pending:
        log.err(f"{pending} 个文件存在风格问题")
        log.out("（预览：未写入任何文件；写入请加 --apply）")
        return Code.STYLE_PENDING
    if skipped:
        return Code.STYLE_FAILED
    log.out("全部文件已符合风格")
    return Code.OK


def _collect_results(results):
    """把各翻译单元的诊断去重汇总，返回 (诊断, 无法分析的文件, 无结构诊断的输出)。

    同一个头文件被 N 个翻译单元包含，clang-tidy 会给出 N 条完全相同的诊断。按
    (文件, 行, 列, 消息) 去重后再计数，否则一个头文件的问题会被包含它的翻译单元
    数量放大，条数既不可比也不好读。
    """
    findings, seen, skipped, silent = [], set(), [], []
    for path, code, output, found, fcode, fmessage in results:
        if fcode != Code.OK:
            return None, [], [(path, fmessage)]
        state = tidy.classify(code, output)
        if state == "unanalyzable":
            skipped.append(path)
            continue
        if not found:
            if state == "pending":
                # 退出码非零却没有任何结构化诊断：把原始输出给出来，免得
                # 使用者面对一个结论却看不到任何线索
                silent.append((path, output.strip()))
            continue
        for item in found:
            key = (item.file, item.line, item.column, item.message)
            if key in seen:
                continue
            seen.add(key)
            findings.append(item)
    return findings, skipped, silent


def _scan(binary, config, config_file, files, workdir, database_dir, jobs):
    """并行跑 clang-tidy，结果按输入顺序返回。

    并行是子进程阻塞等待型的活；结果的第一位必须是路径，它接着要进 skipped、
    进 xfail 的"已考虑文件"集合。
    """
    from concurrent.futures import ThreadPoolExecutor

    def one(item):
        index, path = item
        fixes = workdir / f"{index:05d}.yaml"
        return (path,) + inspect(binary, config, config_file, path, fixes,
                                 database_dir)

    items = list(enumerate(files))
    limit = max(1, jobs or os.cpu_count() or 1)
    if limit <= 1 or len(items) <= 1:
        return [one(item) for item in items]
    with ThreadPoolExecutor(max_workers=limit) as pool:
        return list(pool.map(one, items))


def _verify(config):
    """跑一次验证指令，判断当前代码还能不能构建。

    clang-tidy 的诊断所用编译参数与真实构建不完全一致，看不到的错误照样存在，
    只有真跑构建才可靠。
    """
    command = list(config.verify_command)
    if not command:
        return None
    import subprocess

    try:
        result = subprocess.run(command, cwd=str(config.root),
                                capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        log.err(f"验证指令无法执行: {exc}")
        return False
    log.noise(config, "style", (result.stdout or "") + (result.stderr or ""))
    return result.returncode == 0


def record(config, options, tools):
    """把当前未登记的诊断登记成基线，返回错误码。

    用途是"接入即绿"：存量违规冻结成已知项，此后只有**新增**违规判红。它不
    修改代码，也不代表命名被改对了——真正清存量靠 `--apply`（默认包含命名）。
    """
    run = _prepare_run(config, options)
    if run.early:
        log.out(run.message)
        return Code.OK
    if run.code != Code.OK:
        log.err(run.message)
        return run.code

    workdir = Path(tempfile.mkdtemp(prefix="cxx_standardize_record_"))
    try:
        results = _scan(tools.clang_tidy, config, run.config_file, run.files,
                        workdir, run.database_dir, _jobs(config, options))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    findings, _skipped, _silent = _collect_results(results)
    fresh = [item for item in findings
             if run.checklist.style(item.file, item.line, item.check) is None]
    if run.prepare_failed:
        # 派生文件没生成时可能整片 TU 没被分析，登记结果因此不完整——说出来，
        # 否则"没有需要登记的诊断"会被读成"全都登记过了"
        log.err(INCOMPLETE_NOTE)
    if not fresh:
        log.out("没有需要登记的诊断")
        return Code.STYLE_FAILED if _incomplete(config, run) else Code.OK
    target = str(getattr(config, "xfail_file", "") or "")
    if not target:
        log.err("未配置 XFAIL_FILE，无处登记")
        return Code.CONFIG_MISSING_ITEM
    entries = [xfail.Entry("style", _relative(config, item.file), item.check)
               for item in fresh]
    code, message = xfail.write(target, entries, config)
    if code != Code.OK:
        log.err(message)
        return code
    log.out("已登记 {} 条基线到 {}".format(len(entries), target))
    return Code.STYLE_FAILED if _incomplete(config, run) else Code.OK


def _relative(config, path):
    """诊断文件相对项目根的路径：清单里写绝对路径换台机器就失效。"""
    try:
        return str(Path(path).resolve().relative_to(Path(config.root).resolve()))
    except ValueError:
        return str(path)


def apply(config, options, tools):
    """逐文件修复，只保留验证仍通过的结果。"""
    run = _prepare_run(config, options, need_checklist=False)
    if run.early:
        log.out(run.message)
        return Code.OK
    if run.code != Code.OK:
        log.err(run.message)
        return run.code
    if not config.verify_command:
        # 不是提醒而是拒绝：修复会真的改代码。重叠的 fix-it 会被叠在一起，写出
        # `static_cast<std::size_t>(1024 * )1024` 这类编译不过的文本，而没有构建
        # 验证就无从知道改坏没改坏，宁可不改
        log.err("未配置 VERIFY_COMMAND：自动修复会改代码，没有构建验证就不知道"
                "改坏没改坏。请先配好验证指令（例如跑一次 ci 的 build），"
                "或改为手工修改")
        return Code.CONFIG_MISSING_ITEM

    workdir = Path(tempfile.mkdtemp(prefix="cxx_standardize_tidy_"))
    try:
        fixed, manual, skipped, blocked, unfixable_paths = _apply_files(
            tools, config, run.config_file, run.files, workdir,
            run.database_dir, options.apply_naming, _jobs(config, options))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if run.prepare_failed:
        # 派生文件没生成可能是"一个 TU 都没分析"，不能与"全部 TU 干净"给同一结论
        log.err(INCOMPLETE_NOTE)
    # 需人工处理的清单要先打：回滚路径会在下面提前返回，而"哪些文件的自动修复
    # 会破坏编译"恰恰是那种时刻唯一有用的信息——只给一个计数等于什么都没说，
    # 机器报告里的 failures 也会是空的（契约要求这份清单承载在 failures 上）
    if manual:
        log.err(f"{len(manual)} 个文件需人工处理，自动修复会破坏编译:")
        for path in manual:
            log.out(f"    {path}")
    if fixed is None:
        # 只有"红线被触碰"才报 FROZEN_TOUCHED；读不出修复文件是另一回事
        return Code.FROZEN_TOUCHED if blocked else Code.STYLE_FAILED
    if fixed:
        log.out(f"已自动修复 {len(fixed)} 个文件")
    if skipped:
        log.err(f"{len(skipped)} 个文件无法分析（编译错误，多来自第三方头文件）")
        for path in skipped:
            log.out(f"    {path}")
    unfixable = len(unfixable_paths)
    remaining = 0
    if unfixable:
        # 已登记基线的算已知项，不再拦——门禁是同一口径，两边不能各判各的
        checklist, ccode, cmessage = xfail.load_checklist(config)
        if checklist is None:
            log.err(cmessage)
            return ccode
        remaining = sum(1 for path, check in unfixable_paths
                        if checklist.style(path, 0, check) is None)
        if remaining:
            # 只报告不修复的检查：说出来，而且不能算通过——"改不动"不等于"没问题"
            log.err("{} 条诊断 clang-tidy 不提供修复，需要人工处理或登记基线"
                    "（cxx-standardize style --record）；"
                    "其中 {} 条已登记、{} 条未登记".format(
                        len(unfixable_paths), len(unfixable_paths) - remaining,
                        remaining))
    if not fixed and not manual and not skipped and not unfixable:
        if run.prepare_failed:
            # 派生文件未就绪意味着可能一个 TU 都没分析：不许它与"全部干净"同结论。
            # 非严格只影响退出码；"结果不完整"已在上面走过 stderr
            return Code.STYLE_FAILED if _incomplete(config, run) else Code.OK
        log.out("全部文件已符合风格")
        return Code.OK
    if unfixable and remaining:
        return Code.STYLE_PENDING
    if _incomplete(config, run):
        return Code.STYLE_FAILED
    return Code.OK


def watched_files(config):
    """处理范围内全部文件，含头文件。

    字符串字面量的扫描要覆盖整个工程：改名改的是符号，而提到这个名字的字符串
    可能在任何被分析或未被分析的文件里。
    """
    return collect_files(config, Options(paths=list(config.whitelist),
                                         exclude=list(config.blacklist)),
                         ALL_SUFFIXES)


def _text_at(key, value):
    """替换块在源文件里覆盖的那段原文（旧名）。"""
    target, offset = key
    length = value[0]
    try:
        data = Path(target).read_bytes()
    except OSError:
        return ""
    return data[offset:offset + length].decode("utf-8", "replace")


def _is_frozen(path, frozen):
    """路径是否落在禁改区（含禁改区自身）。

    委托 paths.is_frozen（它又委托 is_within）：包含判定只留一个实现。
    自行用 Path.resolve + Path.parents 再写一遍，口径会与它分叉且审计拦不住。
    """
    return paths.is_frozen(path, frozen)


def snapshot(paths):
    """这些路径下源码文件的字节快照，用于判卷不过时整体回滚。

    比 mtime 更可靠：粗粒度文件系统上，同秒内的写入会让时间戳看不出变化。
    """
    taken = {}
    for item in paths:
        base = Path(item)
        candidates = [base] if base.is_file() else (
            sorted(base.rglob("*")) if base.is_dir() else [])
        for path in candidates:
            if path.is_file() and path.suffix in ALL_SUFFIXES:
                taken[path] = path.read_bytes()
    return taken


def restore(taken, paths):
    """把 paths 里的文件按快照还原。"""
    for path in paths:
        if path in taken:
            _code, message = source_mod.write_bytes(path, taken[path])
            if _code != 0:
                log.err(message)


def _export(tools, config, config_file, files, workdir, database_dir, jobs):
    """第一趟：逐个翻译单元只读导出修复，返回 [(文件, 修复文件路径)]。

    **期间绝不改源树**——所有翻译单元看的是同一棵干净的树，因此同一处声明在
    多个 TU 的导出里必然给出逐字节相同的替换，合并去重才成立。

    只跑源文件：声明在头文件里的改名由包含它的 TU 经 --header-filter 带出来。
    把头文件单独当主文件跑，clang-tidy 往往一条替换都不给。
    """
    exports = []
    for index, path in enumerate(files):
        fixes = workdir / f"{index:05d}.yaml"
        code, output, found, fcode, fmessage = inspect(
            tools.clang_tidy, config, config_file, path, fixes, database_dir)
        if fcode != Code.OK:
            return None, fcode, fmessage
        if tidy.classify(code, output) == "unanalyzable":
            continue
        if found:
            exports.append((path, fixes))
    return exports, Code.OK, ""


def format_touched(tools, config, touched):
    """修复之后给改过的文件收尾排版。

    clang-tidy 的 fix-it 只管把语法改对，不管排版：给 `if` 补大括号得到的是
    K&R 位置、闭括号掉回第 0 列、行尾注释列被拉歪（实测）。改名同理——标识符
    长度变了，而 AlignConsecutive* 按最长项补齐列，不重排类体与枚举的对齐就散了。
    所以两种修复路径都要在末尾跑一遍排版，否则风格修复自己制造排版违规。

    传的是 `--style=` 的样式串（`file:<路径>` 或一段内联配置），不是配置文件路径：
    直接给路径 clang-format 会判 "Invalid value for -style"，收尾排版等于从不生效。
    """
    if not tools.clang_format:
        return
    # 延迟导入：format 需要 style 里的文件收集，两模块互相导入会随导入顺序
    # 成败不定（先导入 style 就炸），这里按需导入把环断开
    from . import format as format_mod
    from . import fmt as fmt_mod

    style, _config_file, code, message = fmt_mod.style_spec(config)
    if code != Code.OK:
        log.err("风格已改，收尾排版未完成（样式无法确定）: " + message)
        return
    for path in touched:
        protect = config.format_comments != "none"
        data, code, message = format_mod.formatted(
            tools.clang_format, style, path, protect, config)
        if code != Code.OK:
            # 不回滚风格改动：改动本身已通过构建判卷。但必须说清"没做完"，
            # 别让人以为一切就绪
            log.err("风格已改，收尾排版未完成: {}: {}".format(path, message))
            continue
        _code, message = source_mod.write_bytes(path, data)
        if _code != 0:
            log.err(message)


def _derive_config(config_file, workdir, apply_naming):
    """修复用的 .clang-tidy：命名那一趟用项目原配置，其余换成"关掉命名检查"。

    命名修复不派生配置——检测规则要原样保留，否则"局部变量挂 v_"这类违规会被
    派生配置的豁免吃掉，根本报不出来。前缀怎么改由 prefix.retarget 按口径表算，
    不靠改配置。都不走命令行的 --checks：那是**覆盖**而不是追加，必须复现
    clang-tidy 的完整生效列表，漏一项或错一个空格就一条诊断都不报。
    """
    if apply_naming:
        return config_file
    derived = tidy.without_naming_config(config_file,
                                         workdir / "no-naming.clang-tidy")
    if derived:
        return derived
    # 读不出项目配置就不加覆盖：没有 .clang-tidy 时 clang-tidy 只用它自己的缺省
    # 检查集（只有编译器诊断），里面没有命名检查，照原样跑是安全的
    log.out("提示: 读不出风格配置，自动修复不做检查集覆盖")
    return config_file


def _rename_pairs(replacements):
    """(旧名, 新名) 列表。

    用列表而不是字典：同一个基名可能映射到多个目标，字典会把这种冲突悄悄抹掉
    （后写覆盖先写），同步字符串时就成了瞎猜。
    """
    renames = []
    for key in sorted(replacements):
        old = _text_at(key, replacements[key])
        new = replacements[key][1]
        if old and new and old != new:
            renames.append((old, new))
    return renames


def _snapshot_files(config, targets, protected):
    """改前快照：覆盖全部落点与会被同步字符串的测试文件，供判卷不过时整体回滚。

    快照要连"会被同步字符串的测试文件"一起覆盖：改名会连带改测试里的期望字符串，
    漏了它们，回滚就退不干净，下一轮从半改状态出发必然继续失败。
    """
    protected_paths = [base for base, _ in protected]
    string_files = [path for path in watched_files(config)
                    if {part.lower() for part in Path(path).parts}
                    .intersection(tidy.TEST_DIRS)]
    watched = sorted(set(targets) | set(protected_paths) | set(string_files))
    return snapshot(watched), watched


def _write_fixes(tools, config, replacements, renames, swap_prefixes=True):
    """应用 fix-it：按字节偏移直写替换、同步测试字符串、交换前缀中间态、收尾排版。

    swap_prefixes 为假用于判卷不过后的重试路径——它历来不做前缀整词交换，这里
    保持原样（重试的替换集已被 anchored 过滤过）。
    """
    touched = tidy.apply_by_offset(replacements, config)
    touched += tidy.rename_in_test_strings(renames, watched_files(config),
                                           config)
    if swap_prefixes:
        _swap_prefixes(config, touched)
    format_touched(tools, config, touched)
    return touched


def _rollback(good, watched):
    """回滚：把落点还原到改前快照。"""
    restore(good, watched)


def _apply_files(tools, config, config_file, files, workdir, database_dir,
                 apply_naming=False, jobs=0):
    """原子修复：每 TU 只读导出 → 合并去重 → 一次性应用 → 末尾判卷 → 整体回滚。

    返回 (已修复, 需人工, 无法分析, 被拦下的禁改区文件, 无自动修复的诊断)。整体
    拒绝时第一项为 None，被拦下的文件在第四项里——调用方据此区分"修复失败"与
    "红线被触碰"。

    四步各自成一个函数：改前快照 `_fix_snapshot`、应用 fix-it `_write_fixes`、
    复扫验证 `_verify`、回滚 `_rollback`。回滚只发生在末尾判卷，中途不回滚——中途
    回滚正是"改了又变回去"错觉的来源。
    """
    config_file = _derive_config(config_file, workdir, apply_naming)
    exports, code, message = _export(tools, config, config_file, files, workdir,
                                     database_dir, jobs)
    if code != Code.OK:
        log.err(message)
        return None, None, None, [], []
    if not exports:
        return [], [], [], [], []
    # 报了但改不动的检查：如实说出来，别让"跑完了还剩几条"看起来像工具没干活
    stuck = tidy.unfixable([item[1] for item in exports])
    if stuck:
        log.out("以下检查 clang-tidy 只报告、不提供修复块，改不动"
                "（请人工处理或登记基线）: " + "、".join(stuck))
    unfixable = tidy.unfixed_items([item[1] for item in exports])

    try:
        replacements = tidy.raw_replacements([item[1] for item in exports])
    except tidy.DeterministicConflict as exc:
        # 同一处替换在不同 TU 给出不同文本 = 配置或环境不确定。拒跑，绝不猜
        log.err("同一处替换在不同翻译单元里不一致，已拒跑: " + str(exc))
        return None, None, None, [], []
    if not replacements:
        return [], [], [], [], unfixable
    # clang-tidy 只会拼前缀不会剥，遇到"名字上已经挂着别的前缀"时给的是叠加结果
    # （g_digits → s_g_digits）。位置由它提供、文本由我们算：按口径表算出正确目标名
    replacements, refused, rewritten = prefix.retarget(
        replacements, config, prefix.declared_prefixes(config))
    if rewritten:
        pairs = sorted({"{0} → {1}".format(old, new)
                        for old, new in rewritten})
        log.out("按口径表改正前缀: " + "、".join(pairs[:8])
                + ("（共 {} 处）".format(len(pairs)) if len(pairs) > 8 else ""))
    if refused:
        log.err("以下名字算不出正确的目标名，未自动改（需要人工）: "
                + "、".join(sorted(set(refused))))
    if not replacements:
        return [], [], [], [], unfixable
    # 改名要连带同步测试文件里的字符串：断言消息常把标识符串成文本
    # （CHECK(input > 0) 生成的 `条件不成立: input > 0`），只改标识符不改字符串，
    # 构建照样过、测试却挂了
    renames = _rename_pairs(replacements)

    # 两张表都不许写：黑名单是"不必分析"，FROZEN 是"绝不可写"，但"替换落到没让你
    # 管的地方"都是同一类越界写入。两者分列是为了语义清楚，保护范围一致
    protected = [(item, "禁改区") for item in (config.frozen or [])]
    protected += [(item, "黑名单") for item in (config.blacklist or [])]
    targets = sorted({Path(target) for target, _offset in replacements})
    blocked = []
    for item in targets:
        for base, name in protected:
            if _is_frozen(item, [base]):
                blocked.append((item, name))
                break
        else:
            # 禁改区之外还要看真实落点：工程内的软链指向工程外时，按链接路径
            # 看它在范围里，实际被改写的却是外面那个文件
            hit, why = paths.write_blocked(config, item)
            if hit:
                blocked.append((item, why))
    if blocked:
        log.err("修复落点落在不该写的地方，整体拒绝: "
                + "、".join("{}（{}）".format(path, name)
                            for path, name in blocked))
        return None, None, None, [path for path, _ in blocked], []

    good, watched = _snapshot_files(config, targets, protected)
    try:
        touched = _apply_and_verify(tools, config, replacements, renames,
                                    good, watched, unfixable)
    except BaseException:
        # 中断（Ctrl-C/SIGTERM）与任何异常都不能把未判卷的改写留在工作区：
        # 停下之前先还原，再让异常照常往上走
        _rollback(good, watched)
        raise
    return touched


def _apply_and_verify(tools, config, replacements, renames, good, watched,
                      unfixable):
    """写入 → 判卷 → 必要时回滚重试。抽出来是为了让中断能统一兜住。"""
    touched = _write_fixes(tools, config, replacements, renames)
    if _verify(config) is not False:
        return sorted(touched), [], [], [], unfixable
    # 判卷不过：先整体回滚，再退一步重试。断言消息里有的字符串由标识符派生、
    # 有的只是恰好同名，静态分不出来——那就"先试、再退"：把测试文件字符串里
    # 提到的名字排除后重来一次。仍然不过就认输，整体回滚
    _rollback(good, watched)
    log.err("修复后验证不通过，已整体回滚（{} 个文件）；"
            "排除测试字符串里提到的名字后重试".format(len(touched)))
    anchored = tidy.anchored_tokens(watched_files(config))
    retry = {key: value for key, value in replacements.items()
             if _text_at(key, value) not in anchored}
    if not retry or len(retry) == len(replacements):
        return None, sorted(touched), [], [], []
    # 重试路径沿用原来的 rename 表表达式（{旧名: 新名}）交给同步函数
    touched = _write_fixes(
        tools, config, retry,
        {_text_at(key, value): value[1] for key, value in retry.items()},
        swap_prefixes=False)
    if _verify(config) is False:
        log.err("排除后仍然验证不通过，已整体回滚（{} 个文件）"
                .format(len(touched)))
        _rollback(good, watched)
        return None, sorted(touched), [], [], []
    log.out("已跳过 {} 个在库里另有同名字面量的名字（改了会改挂断言）"
            .format(len(replacements) - len(retry)))
    return sorted(touched), [], [], [], unfixable


def _swap_prefixes(config, touched):
    """前缀中间态整词交换。

    clang-tidy 只加不脱，`m_x` 迁到 `t_` 必经中间态 `t_m_*`。这类 token 是全局
    唯一签名（字符串字面量里绝无 `t_m_`），整词直替零歧义。交换表来自配置，
    默认空——没有迁移需求就什么都不动。
    """
    swaps = list(config.naming_fix_prefix_swaps or ())
    if not swaps:
        return
    for path in touched:
        text, info = source_mod.read_source(path, config)
        if text is None:
            log.err("编码无法确定（{}）: {}，前缀交换已跳过"
                    .format(info.reason, path))
            continue
        updated = text
        for middle, target in swaps:
            updated = re.sub(r"\b" + re.escape(middle) + r"([a-z0-9_]+)\b",
                             target + r"\1", updated)
        if updated != text:
            code, message = source_mod.write_source(path, updated, info)
            if code != 0:
                log.err(message)
