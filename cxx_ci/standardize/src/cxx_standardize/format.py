"""clang-format 排版。

排版不触碰注释：注释先换成占位符交给 clang-format，排版后按原列号还原；代码
越过原列时注释退一格。clang-format 没有**保列号**的等价能力——
`AlignTrailingComments: true`（默认）会把手工对齐的行尾注释全部重排到最长代码
行，`false` 更糟（全部塌成一个空格），`{Kind: Leave}` 保的是间隔而不是列号。

排版结果必须可复现：同一份输入连跑两次结果不一致说明基线不成立，拒绝应用。
基线指纹（工具版本 + 注释模式 + 配置内容摘要）变化时要求重新确认，而不是把
差异当成代码问题。
"""

import difflib
from dataclasses import dataclass
from pathlib import Path

from . import baseline, comments, fmt, log, paths
from .errors import Code
from . import source as source_mod
from .style import ALL_SUFFIXES, collect_files, incremental_files

# 一次性切换用的排版模式：protect-column 的拒绝正是这次切换要解决的问题，
# 因此切换本身按注释允许移动的模式来排
ACCEPT_MODE = "placeholder"

GUIDANCE = ("若这是首次接入：`format`（不带 --apply）只看漂移不改文件；"
            "`format --accept-baseline --yes` 按注释允许移动的模式一次性重排并"
            "记下基线；`standardize.py --apply` 则把排版、风格、命名一次修完，"
            "并在没有基线时顺手把当前排版记成基线")


@dataclass
class Prepared:
    """一次排版准备的结果。"""

    style: str
    config_file: str
    fingerprint: str
    files: list[Path]


def format_once(binary, style, path, source, mode, moved=None, broken=None):
    """按给定模式排版一次，返回 (结果文本, 错误码, 说明)。

    none 完全信任 .clang-format；placeholder 与 protect-column 都先把注释换成
    占位符，区别在于后者把"注释列号变了"视为基线漂移。
    """
    if mode == "none":
        return fmt.format_text(binary, style, path, source)
    found = comments.scan_comments(source)
    prefix = comments.marker_for(source, 0)
    text, code, message = fmt.format_text(
        binary, style, path, comments.strip_comments(source, prefix))
    if code != Code.OK:
        return None, code, message
    restored = comments.restore_comments(text, found, prefix, moved, broken)
    if broken:
        # 行注释后面挂上了代码 = 把代码变成了注释的一部分。这种产物能编译但
        # 语义已经变了，必须报错而不是写盘
        return None, Code.COMMENT_RESTORE_FAILED, (
            "注释还原失败：第 {} 行的行尾注释后面还有代码；"
            "请把该处注释单独成行，或改 FORMAT_COMMENTS=none".format(
                "、".join(str(item[1]) for item in broken[:3])))
    return restored, Code.OK, ""


def formatted(binary, style, path, mode="placeholder", config=None):
    """返回排版后的字节，失败返回 (None, 错误码, 说明)。

    幂等自检比的是**剥掉注释后那份文本**的两次 clang-format 结果——它度量的是
    "clang-format 本身稳不稳定"，这正是基线机制成立的前提。比"还原注释后的产物"
    是量错了对象：那里面还叠着自家的还原步骤，还原一有偏差就被判成"基线不可
    复现"，而真正的还原错误该由 format_once 的结构不变量（行注释后面不得再有
    代码）来报。量错对象的代价很直接：一个"初始化列表 + 行尾注释 + 独立左括号"
    的普通文件会被永久拒绝，连 --accept-baseline 都进不去。
    """
    config = config or _fallback_config()
    source, info, code, message = _read(path, config)
    if source is None:
        return None, code, message
    moved: list = []
    broken: list = []
    if mode != "none":
        if not _stable(binary, style, path, source):
            return None, Code.FORMAT_UNSTABLE, (
                f"排版结果不稳定，再跑一次仍会变化，拒绝应用: {path}")
    once, code, message = format_once(binary, style, path, source, mode, moved,
                                      broken)
    if code != Code.OK:
        return None, code, message
    if mode == "protect-column" and moved:
        return None, Code.BASELINE_REJECTED, (
            f"{len(moved)} 处行尾注释列号发生变化，注释优先的基线不成立")
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


def _stable(binary, style, path, source):
    """clang-format 对"剥掉注释后的文本"是否幂等。"""
    prefix = comments.marker_for(source, 0)
    peeled = comments.strip_comments(source, prefix)
    once, code, _message = fmt.format_text(binary, style, path, peeled)
    if code != Code.OK:
        return False
    again, code, _message = fmt.format_text(binary, style, path, once)
    if code != Code.OK:
        return False
    return again == once


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


def _scan(binary, style, files, mode, jobs, config):
    """并行排版一批文件，按输入顺序返回 (路径, 原始, 结果, 错误码, 说明)。

    排版是"起子进程再等"的活，串行跑会让 --jobs 空转；每个文件还要跑两遍
    clang-format（幂等自检）加一次去注释/还原，文件一多就是主要耗时。
    """
    from concurrent.futures import ThreadPoolExecutor

    def one(path):
        before = Path(path).read_bytes()
        after, code, message = formatted(binary, style, path, mode, config)
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

    # 首次应用（还没有基线）用"注释允许移动"的模式：protect-column 是拿**既有**
    # 列位置当基准的，没有基线时这个基准并不存在，硬用会把"某一处行尾注释放不回
    # 原列"的文件整个拒掉，接入流程就断在第一次 --apply 上。排完之后记下基线，
    # 从下一次起才按 protect-column 守
    mode = config.format_comments
    if options.apply and record is None:
        mode = ACCEPT_MODE
        log.out("还没有排版基线，首次应用按注释允许移动的模式重排")
    results = _scan(tools.clang_format, prepared.style, prepared.files,
                    mode, _jobs(config, options), config)
    changed = 0
    changed_paths = []
    problems = []
    for path, before, after, rcode, rmessage in results:
        if after is None:
            log.err(rmessage)
            if rcode == Code.BASELINE_REJECTED:
                log.out(GUIDANCE)
            return rcode
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
        if problems:
            # 有文件没能写入时不能报成功：它就还没排版，下一次照样会判红
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


def drift(binary, style, files, mode, jobs, config):
    """一次性切换会影响哪些文件，返回 ([(路径, 结果, 差异)], 错误码, 说明)。

    连结果一起返回：调用方接着就要写这些字节，再排一次纯属重复劳动。
    """
    changed = []
    for path, before, after, code, message in _scan(binary, style, files, mode,
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
                                   prepared.files, ACCEPT_MODE,
                                   _jobs(config, options), config)
    if code != Code.OK:
        log.err(message)
        return code
    if stage is not None:
        stage.summary = f"一次性重排 {len(changed)} 个文件"
    log.out(f"一次性切换将以 {ACCEPT_MODE} 模式重排 {len(changed)} 个文件，"
            f"行尾注释可能移动列")
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
