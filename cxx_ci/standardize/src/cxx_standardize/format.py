"""clang-format 排版。

排版不触碰注释：先摘掉注释排代码，再按代码行把注释回填；注释文本与代码到
注释的间距原样保留。clang-format 没有**保列号**的等价能力——
`AlignTrailingComments: true`（默认）会把手工对齐的行尾注释全部重排，`false`
更糟（全部塌成一个空格）。因此这一层由本工具做。

排版结果必须可复现：基线指纹（工具版本 + 注释模式 + 配置内容摘要）变化时要求
重新确认，而不是把差异当成代码问题。
"""

import difflib
from dataclasses import dataclass
from pathlib import Path

from . import baseline, comments, fmt, log, paths
from .errors import Code
from . import source as source_mod
from .style import ALL_SUFFIXES, collect_files, incremental_files

GUIDANCE = ("若这是首次接入：`format`（不带 --apply）只看漂移不改文件；"
            "`format --accept-baseline --yes` 一次性重排并记下基线；"
            "`standardize.py --apply` 则把排版、风格、命名一次修完，"
            "并在没有基线时顺手把当前排版记成基线")


@dataclass
class Prepared:
    """一次排版准备的结果。"""

    style: str
    config_file: str
    fingerprint: str
    files: list[Path]


def format_once(binary, style, path, source, protect=True):
    """按给定模式排版一次，返回 (结果文本, 错误码, 说明)。

    protect 为假时完全信任 .clang-format；为真时先摘掉注释排代码，再按代码行
    把注释回填。
    """
    if not protect:
        return fmt.format_text(binary, style, path, source)
    code_text, found = comments.split(source)
    formatted_code, code, message = fmt.format_text(binary, style, path,
                                                    code_text)
    if code != Code.OK:
        return None, code, message
    formatted_code = comments.drop_namespace_comments(formatted_code)
    restored, unplaced = comments.restore_comments(formatted_code, found,
                                                   code_text)
    if unplaced:
        return None, Code.COMMENT_RESTORE_FAILED, (
            "注释回填失败：{} 条注释找不到对应代码行；"
            "请改 FORMAT_COMMENTS=none 或调整代码".format(len(unplaced)))
    return restored, Code.OK, ""


def formatted(binary, style, path, protect=True, config=None):
    """返回排版后的字节，失败返回 (None, 错误码, 说明)。"""
    config = config or _fallback_config()
    source, info, code, message = _read(path, config)
    if source is None:
        return None, code, message
    once, code, message = format_once(binary, style, path, source, protect)
    if code != Code.OK:
        return None, code, message
    # 按原编码与版式写回：编码、换行、BOM 全程不动。这一步是"工具不会吃掉
    # 用户代码"的落点，绝不能再用裸的 encode("utf-8")
    payload, message = source_mod.encode_source(once, info)
    if payload is None:
        return None, Code.ENCODING_UNWRITABLE, message
    return payload, Code.OK, ""


def _read(path, config):
    """读源码并判定编码；判定不出就不许改写这个文件。"""
    text, info = source_mod.read_source(path, config)
    if text is None:
        return None, info, Code.ENCODING_UNKNOWN, (
            "编码无法确定（{}）: {}，本文件不会被改写"
            .format(info.reason, path))
    return text, info, Code.OK, ""


class _Fallback:
    """没有配置时的最小替身：只认 UTF-8。"""

    source_encoding = source_mod.DEFAULT_ENCODING
    source_encoding_candidates: list = []
    declared_keys: tuple = ()


def _fallback_config():
    return _Fallback()


def diff_text(path, before, after):
    """生成统一 diff 文本。"""
    return "".join(difflib.unified_diff(
        before.decode("utf-8", "replace").splitlines(True),
        after.decode("utf-8", "replace").splitlines(True),
        str(path), str(path)))


def prepare(config, options, tools):
    """解析样式、收窄范围、算指纹，返回 (Prepared, 错误码, 说明)。"""
    style, config_file, code, message = fmt.style_spec(config,
                                                       options.config_file)
    if code != Code.OK:
        return None, code, message
    # 头文件也要收：clang-format 没有"按 HeaderFilterRegex 顺带处理"的机制，
    # 不直接收就永远不会被排到
    files = collect_files(config, options, ALL_SUFFIXES)
    if options.full:
        config.incremental = False
    # 排版不做头文件到翻译单元的映射：每个文件各自排版，改动的头文件本身就是
    # 待排版的文件。注释保护依赖行号，也不做行级过滤
    files, notes = incremental_files(config, options, files, None)
    for note in notes:
        log.out(note)
    current = baseline.fingerprint(
        tools.clang_format, fmt.version_text(tools.clang_format),
        config.format_comments, config_file)
    return Prepared(style, config_file, current, files), Code.OK, ""


def _scan(binary, style, files, protect, jobs, config):
    """并行排版一批文件，按输入顺序返回 (路径, 原始, 结果, 错误码, 说明)。

    排版是"起子进程再等"的活，串行跑会让 --jobs 空转。
    """
    from concurrent.futures import ThreadPoolExecutor

    def one(path):
        before = Path(path).read_bytes()
        after, code, message = formatted(binary, style, path, protect, config)
        return path, before, after, code, message

    items = list(files)
    limit = max(1, jobs or 1)
    if limit <= 1 or len(items) <= 1:
        return [one(path) for path in items]
    with ThreadPoolExecutor(max_workers=limit) as pool:
        return list(pool.map(one, items))


def check(config, options, tools, report=None):
    """预览：输出需要改动的差异，返回错误码。"""
    stage = report.stage("format") if report is not None else None
    prepared, code, message = prepare(config, options, tools)
    if code != Code.OK:
        log.err(message)
        return code
    if not prepared.files:
        log.out("没有需要排版的文件")
        return Code.OK
    record, code, message = baseline.read(config.log_dir)
    if code != Code.OK:
        log.err(message)
        return code
    code, message = baseline.compare(record, prepared.fingerprint)
    if code != Code.OK:
        log.err(message)
        return code

    protect = config.format_comments != comments.OFF
    results = _scan(tools.clang_format, prepared.style, prepared.files,
                    protect, _jobs(config, options), config)
    changed = 0
    changed_paths = []
    problems = []
    skipped = []
    for path, before, after, rcode, rmessage in results:
        if after is None:
            # 编码判定不出的文件只读跳过，**不能**中断整个阶段：契约写的是
            # "该文件只读，检查照报、改写跳过并说明原因，退出码按有文件未完成计"。
            # 一遇到它就 return 会让后面的文件一个都不处理，留下一棵"写了一半"
            # 的树，而使用者看到的是整轮失败
            log.err("{}: {}".format(path, rmessage))
            skipped.append((path, rmessage))
            continue
        if after == before:
            continue
        changed += 1
        changed_paths.append(path)
        if options.apply:
            hit, why = paths.write_blocked(config, path)
            if hit:
                log.err("拒绝改写 {}（{}）".format(path, why))
                problems.append(str(path))
                continue
            code, message = source_mod.write_bytes(path, after)
            if code != Code.OK:
                log.err(message)
                problems.append(str(path))
                continue
            log.out(f"已排版 {path}")
        else:
            log.out(diff_text(path, before, after).rstrip())
    if skipped:
        log.err("{} 个文件编码判定不出，保持只读（见上）".format(len(skipped)))
        for path, _reason in skipped:
            if stage is not None:
                stage.add_failure(str(path), "编码判定不出，未改写")
    if problems:
        log.err("{} 个文件没能写入（见上）".format(len(problems)))
    if options.apply:
        log.out(f"已排版 {changed - len(problems)} 个文件")
        if record is None:
            # 刚排完的这棵树就是新的基准：把指纹记下来，门禁从下一次起才比对得
            # 出漂移。不记的话，--apply 之后门禁仍然报"没有排版基线"并判红，
            # 接入流程就断在"改完了却过不了"这一步
            path, code, message = baseline.write(config.log_dir,
                                                 prepared.fingerprint)
            if code != Code.OK:
                log.err(message)
                return code
            log.out(f"已按当前排版建立基线指纹: {path}")
        if stage is not None:
            stage.summary = f"已排版 {changed - len(problems)} 个文件"
            for path in problems:
                stage.add_failure(str(path), "没能写入（见上）")
        if problems or skipped:
            # 有文件没写成、或根本没改写时不能报成功：它们就还没排版，
            # 下一次照样会判红
            return Code.FORMAT_PENDING
        return Code.OK
    if changed:
        log.err(f"{changed} 个文件需要排版")
        if record is None:
            log.out("本仓库还没有排版基线。" + GUIDANCE)
        if stage is not None:
            stage.summary = f"{changed} 个文件需要排版"
            for path in changed_paths:
                stage.add_failure(str(path), "需要排版")
        return Code.FORMAT_PENDING
    log.out("全部文件已符合排版")
    if stage is not None:
        stage.summary = "全部文件已符合排版"
    return Code.OK


def drift(binary, style, files, protect, jobs, config):
    """一次性切换会影响哪些文件，返回 ([(路径, 结果, 差异)], 错误码, 说明)。

    连结果一起返回：调用方接着就要写这些字节，再排一次纯属重复劳动。
    """
    changed = []
    for path, before, after, code, message in _scan(binary, style, files, protect,
                                                    jobs, config):
        if after is None:
            return None, code, message
        if after != before:
            changed.append((path, after, diff_text(path, before, after)))
    return changed, Code.OK, ""


def accept_baseline(config, options, tools, yes=False, report=None):
    """一次性切换：先给清单与样例，确认后整体重排并记下基线指纹。"""
    stage = report.stage("format") if report is not None else None
    prepared, code, message = prepare(config, options, tools)
    if code != Code.OK:
        log.err(message)
        return code
    if not prepared.files:
        log.out("没有需要排版的文件")
        return Code.OK
    changed, code, message = drift(tools.clang_format, prepared.style,
                                   prepared.files,
                                   config.format_comments != comments.OFF,
                                   _jobs(config, options), config)
    if code != Code.OK:
        log.err(message)
        return code
    if stage is not None:
        stage.summary = f"一次性重排 {len(changed)} 个文件"
    log.out(f"一次性切换将重排 {len(changed)} 个文件，注释原样保留")
    if changed:
        log.out(f"示例差异（其余 {len(changed) - 1} 个同类）:")
        log.out(changed[0][2].rstrip())
    if not yes and not _interactive():
        log.err("非交互环境执行 --accept-baseline 必须加 --yes 明确确认")
        return Code.CONFIG_BAD_VALUE
    if not _confirmed(yes, len(changed)):
        log.err("未确认，未改写任何文件")
        return Code.OK
    if stage is not None:
        for path, _after, _diff in changed:
            stage.add_failure(str(path), "一次性重排")
    written = 0
    for path, after, _ in changed:
        # 一次性重排同样是"改写用户源码"，要过同一套范围判定：这条入口以前没有
        # 任何检查，写穿软链改到工程外、改写禁改区都照做
        hit, why = paths.write_blocked(config, path)
        if hit:
            log.err(f"拒绝改写 {path}（{why}）")
            return None
        code, message = source_mod.write_bytes(path, after)
        if code != Code.OK:
            log.err(message)
            return None
        written += 1
    path, code, message = baseline.write(config.log_dir, prepared.fingerprint)
    if code != Code.OK:
        log.err(message)
        return code
    log.out(f"已重排 {written} 个文件，基线指纹写入 {path}")
    return Code.OK


def _interactive():
    import sys

    return sys.stdin.isatty()


def _confirmed(yes, count):
    """二次确认。非交互环境必须显式给 --yes，绝不默默改写。"""
    if yes:
        return True
    if not _interactive():
        return False
    log.out(f"将重排 {count} 个文件，输入 yes 继续（其它输入放弃）:")
    try:
        answer = input()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


def _jobs(config, options):
    return options.jobs or config.jobs
