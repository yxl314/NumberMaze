"""命令行入口。

用法：

    cxx-standardize check                 # 风格 + 排版门禁（预览）
    cxx-standardize style [--apply]       # 只做风格
    cxx-standardize format [--apply]      # 只做排版
    cxx-standardize format --accept-baseline --yes   # 一次性切换
    cxx-standardize init [--project-root PATH] [--out PATH]   # 探测生成配置

退出码（见 CONTRACT.md）：0 无需改动；1 有待改动或检查未通过；2 工具或配置错误。
"""

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import fmt, log, probe
from .version import version_line
from . import audit as audit_mod
from . import naming_meta, platform
from . import config as config_module
from . import format as format_mod
from . import report as report_module
from . import style as style_mod
from .errors import Code, exit_code
from .models import ClangTools
from .style import Options

# 两个动作与它们的执行顺序：风格整理可能改动语义，排在排版之前，由排版收敛
# 最终格式
ACTIONS = ("style", "format")

# 一条命令跑完全部三件事：排版、风格、命名。命名放在风格那一趟里（同一个
# clang-tidy 进程），排版放最后收敛最终格式——改名会改变标识符长度，
# AlignConsecutive* 的列对齐要重排
ALL_ACTIONS = ("style", "format")

# init 生成的配置在工程里的位置（相对项目根）
INIT_OUTPUT = "config/standardize.py"


@dataclass
class Request:
    """一次调用的全部输入。"""

    action: str
    options: Options
    accept_baseline: bool = False
    yes: bool = False


# 顶层动作的权威清单：parser 的 choices 与 standardize.py 包装脚本都从这里取。
# 两处各写一个元组就会漏——doctor 加进来时包装脚本不知道，于是把 "doctor"
# 当成"没给动作"，插了个 all 变成 "all doctor"，报的却是 unrecognized arguments
KNOWN_ACTIONS = ("check", "style", "format", "all", "init", "doctor",
                 "migrate-config")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="cxx-standardize",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=KNOWN_ACTIONS,
        help="check 跑风格与排版；style / format 只跑其中一个；"
             "init 探测当前工程并生成配置；doctor 只读自检；"
             "migrate-config 把旧配置项改名",
    )
    parser.add_argument("--project-root", default="",
                        help="init 探测的工程根，缺省取当前目录")
    parser.add_argument("--out", default="",
                        help="init 生成配置的位置，缺省 <工程>/config/standardize.py")
    parser.add_argument("--record", action="store_true",
                        help="把当前未登记的诊断登记成基线，只对新增违规判红")
    parser.add_argument("--apply-naming", action="store_true",
                        help="扩大 --apply 的范围：把命名也纳入自动修复"
                             "（需与 --apply 同用；all --apply 默认包含）")
    parser.add_argument("--apply", action="store_true",
                        help="写入改动，缺省仅预览")
    parser.add_argument("--accept-baseline", action="store_true",
                        help="一次性切换：确认后整体重排并记下排版基线指纹")
    parser.add_argument("--yes", action="store_true",
                        help="跳过二次确认；非交互环境执行 --accept-baseline 必须给出")
    parser.add_argument("--paths", default="",
                        help="处理路径，逗号分隔，相对路径以项目根为基准")
    parser.add_argument("--exclude", default="",
                        help="排除路径，逗号分隔，优先级高于 --paths")
    parser.add_argument("--config-file", default="",
                        help="工具自身配置文件的位置，缺省取配置里的 .clang-format / .clang-tidy")
    parser.add_argument("--changed-only", action="store_true",
                        help="只处理改动过的文件")
    parser.add_argument("--base", default="",
                        help="增量取集的基准引用，如 origin/main")
    parser.add_argument("--full", action="store_true",
                        help="强制全量处理，覆盖配置里的增量开关")
    parser.add_argument("--no-prepare", action="store_true",
                        help="跳过分析前的派生文件生成步")
    parser.add_argument("--jobs", type=int, default=0,
                        help="并行任务数，0 表示按 CPU 核数")
    parser.add_argument("--verbose", action="store_true",
                        help="透传工具自身的进度等过程噪声（缺省只落盘到日志）")
    parser.add_argument("--config", default="",
                        help="项目配置的位置；缺省取环境变量 "
                             "CXX_STANDARDIZE_CONFIG 或工具自带的 config/config.py")
    parser.add_argument("--no-audit", action="store_true",
                        help="跳过配置审计，仅调试用")
    parser.add_argument(
        "--report",
        nargs="?",
        const="-",
        default=None,
        help="写机器可读报告：--report=PATH 写文件，--report 或 --report=- 打到标准输出",
    )
    parser.add_argument(
        "--report-format",
        default="json",
        help="报告格式 json|junit|sarif，逗号分隔可并列（多格式写文件时按格式加后缀）",
    )
    parser.add_argument("--migrate-config", dest="migrate_config",
                        action="store_true",
                        help="把旧配置项改名（配 --config 指目标；加 --dry-run 只出 diff）")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="只打印将要做的改动，不写文件")
    parser.add_argument("--version", action="store_true", help="打印版本与环境后退出")
    return parser


def _split(value):
    return [Path(item.strip()) for item in value.split(",") if item.strip()]


def to_request(args):
    """把命令行参数转成一次调用的输入。"""
    options = Options(
        paths=_split(args.paths),
        exclude=_split(args.exclude),
        config_file=args.config_file,
        changed_only=args.changed_only,
        base=args.base,
        full=args.full,
        apply=args.apply,
        apply_naming=args.apply_naming,
        record=args.record,
        no_prepare=args.no_prepare,
        jobs=args.jobs,
    )
    return Request(args.action, options, args.accept_baseline, args.yes)


def resolve_tools(config, need):
    """解析需要的 clang 工具并校验版本，返回 (ClangTools, 错误码, 说明)。

    need 里的名字可以带 `?` 前缀，表示"有就用、没有不算错"（收尾排版用的
    clang-format 就是这样：缺了风格修复照样能落地，只是不帮忙重排）。
    """
    tools = ClangTools()
    for item in need:
        optional = item.startswith("?")
        name = item[1:] if optional else item
        explicit = {"clang-format": config.clang_format_bin,
                    "clang-tidy": config.clang_tidy_bin}.get(name, "")
        if explicit:
            binary, code, message = fmt.resolve_explicit(explicit, name,
                                                         config.clang_major)
        else:
            binary, code, message = fmt.resolve(name, config.clang_major,
                                                 config)
        if code != Code.OK or not binary:
            if optional:
                log.err("找不到 {}，跳过收尾排版（风格改动仍然有效）".format(name))
                continue
            return None, code, message
        code, message = fmt.require(binary, name)
        if code != Code.OK:
            if optional:
                log.err("{} 不可用（{}），跳过收尾排版".format(name, message))
                continue
            return None, code, message
        if name == "clang-format":
            tools.clang_format = binary
        else:
            tools.clang_tidy = binary
    return tools, Code.OK, ""


def needed_tools(request):
    """这次调用需要哪些 clang 工具。"""
    if request.action == "format" or request.accept_baseline:
        return ["clang-format"]
    # 自动修复自己按字节偏移直写替换，不再需要 clang-apply-replacements：
    # 少一个外部二进制，也不会再出现官方应用器把重叠 fix-it 叠成编译不过文本的问题
    if request.action == "style":
        # --apply 结束时要用 clang-format 做收尾排版：clang-tidy 的 fix-it 只管
        # 语法对，不管排版（补大括号得到的是 K&R 位置、闭括号掉回第 0 列）。
        # 问号前缀表示"有就用、没有不算错"
        if request.options.apply:
            return ["clang-tidy", "?clang-format"]
        return ["clang-tidy"]
    return ["clang-format", "clang-tidy"]


def actions_for(request):
    """这次调用要跑哪些动作。"""
    if request.accept_baseline:
        # 一次性切换只对排版有意义：风格阶段没有基线概念
        return ("format",)
    if request.action == "check":
        return ACTIONS
    if request.action == "all":
        return ALL_ACTIONS
    return (request.action,)


def run_request(config, request, report=None):
    """按请求执行，返回错误码。

    report 是报告收集器（可为 None）：各动作把结构化结论投喂给它，阶段状态
    在这里按错误码收口——报告不重新判卷，只搬运。
    """
    if request.accept_baseline and request.action == "style":
        log.err("--accept-baseline 只对排版有意义，不能与 style 同时使用")
        return Code.CONFIG_BAD_VALUE
    if request.yes and not request.accept_baseline:
        log.err("--yes 只用于 --accept-baseline 的二次确认")
        return Code.CONFIG_BAD_VALUE
    if request.options.apply_naming and not request.options.apply:
        # 命名只是 --apply 的范围开关。单用它是空操作——实测使用者会把它读成
        # "命名改不动"，进而怀疑整个命名功能。无意义的开关组合必须出声，
        # 与上面两条同一条纪律
        log.err("--apply-naming 只是 --apply 的范围开关，必须与 --apply 同用；"
                "想连命名一起自动改：`style --apply --apply-naming`，"
                "或 `all --apply`（默认包含命名）")
        return Code.CONFIG_BAD_VALUE

    tools, code, message = resolve_tools(config, needed_tools(request))
    if code != Code.OK:
        log.err(message)
        return code
    if report is not None:
        report.tools = tools
    for line in tools.banner():
        log.out(line)

    first = Code.OK
    for name in actions_for(request):
        log.out(f"== {name} ==")
        log.truncate_log(config, name)
        started = time.monotonic()
        if name == "style":
            if request.action == "all" and request.options.apply:
                # all --apply 的语义就是"三件事一次做完"，命名不再要求额外开关
                request.options.apply_naming = True
            if request.options.record:
                code = style_mod.record(config, request.options, tools, report)
            elif request.options.apply:
                code = style_mod.apply(config, request.options, tools, report)
            else:
                code = style_mod.check(config, request.options, tools, report)
        elif request.accept_baseline:
            code = format_mod.accept_baseline(config, request.options,
                                               tools, request.yes, report)
        else:
            code = format_mod.check(config, request.options, tools, report)
        if report is not None:
            report.close(name, code, time.monotonic() - started)
        if code != Code.OK:
            log.err(f"{name} 未通过")
            return code
        if first == Code.OK:
            first = code
    return first


def publish_report(config, args, collector):
    """写机器可读报告。

    报告是旁路产物：它的结论与终端同源（同一份收集器），写报告自身失败（目录
    不可写、格式非法）只降级为警告，绝不改变门禁结论与退出码。
    """
    target = getattr(args, "report", None)
    if not target:
        return
    formats = [item.strip() for item in (args.report_format or "").split(",")
               if item.strip()] or ["json"]
    unknown = [name for name in formats if name not in report_module.FORMATS]
    if unknown:
        log.err("未知报告格式: " + "、".join(unknown)
                + "（可用: " + "、".join(report_module.FORMATS) + "）")
        return
    try:
        document = report_module.build(config, collector)
        written, problems = report_module.write(document, target, formats)
    except (OSError, ValueError, TypeError) as exc:
        log.err("生成报告失败（不影响判定）: {}".format(exc))
        return
    for problem in problems:
        log.err("警告: " + problem)
    for path in written:
        if path != "<stdout>":
            log.out("报告已写入: " + path)


def init_command(args):
    """探测工程并生成配置，返回错误码。

    init 与 --version 一样要在加载配置之前短路：它正是用来产生配置的，因此
    不接受 --config，也不依赖任何已有配置。
    """
    root = Path(args.project_root).resolve() if args.project_root else Path.cwd()
    if not root.is_dir():
        log.err("项目根不存在: {}".format(root))
        return Code.CONFIG_ROOT_MISSING
    target = Path(args.out) if args.out else root / INIT_OUTPUT
    if not target.is_absolute():
        target = root / target
    if target.exists():
        log.err("目标已存在，不覆盖: {}（先备份或换个 --out）".format(target))
        return Code.CONFIG_BAD_VALUE
    created, template_errors = probe.ensure_config_files(root)
    if template_errors:
        for line in template_errors:
            log.err("写入 clang 配置模板失败: " + line)
        return Code.CONFIG_BAD_VALUE
    plan = probe.probe(root, created)
    target.parent.mkdir(parents=True, exist_ok=True)
    code, message = probe.write_config(target, probe.render(plan))
    if code:
        log.err(message)
        return Code.CONFIG_BAD_VALUE
    for line in probe.summary(plan, target, created):
        log.out(line)
    return Code.OK


def doctor_command(args):
    """doctor：只读自检，四段。配置建不起来时也照样给环境段与原因。

    与 ci 侧同形：两侧各一份，措辞一致。
    """
    from . import doctor

    config = None
    reason = ""
    if args.config:
        config, code, message = config_module.load_config(args.config)
        if code != Code.OK:
            reason = message
    for title, lines in doctor.sections(config):
        log.out(title)
        for line in lines:
            log.out("  " + line if not line.startswith("  ") else line)
    if reason:
        log.err("配置未生效，以下判断不含配置相关项: " + reason)
        return int(exit_code(Code.CONFIG_UNLOADABLE))
    return int(exit_code(Code.OK))


def migrate_command(args):
    """migrate-config：与 ci 侧同形状（`--config` 指目标，`--dry-run` 预演）。"""
    from . import migrate

    target = str(args.config or "")
    if not target or not Path(target).is_file():
        log.err("找不到配置文件: " + (target or "（未指定 --config）"))
        return 2
    code, text = migrate.migrate(target, dry_run=bool(args.dry_run))
    log.out(text)
    return code


def main(argv=None):
    log.ensure_utf8_streams()
    # 平台守卫：与 ci.py 同一判据。排版与风格在 macOS/BSD 可用（limited 时放行），
    # 只有明确不支持的系统才当场退出
    if platform.guard():
        return 2
    args = build_parser().parse_args(argv)
    if "doctor" == getattr(args, "action", ""):
        return doctor_command(args)
    # 子命令形状与 ci 侧一致（`cxx-standardize migrate-config`）；--migrate-config
    # 保留为等价别名，老脚本不至于失效
    if "migrate-config" == getattr(args, "action", "") or \
            getattr(args, "migrate_config", False):
        return migrate_command(args)
    if args.version:
        # 统一形状，与 ci.py --version 一致：<项目> <版本> contract <N> python >=<下限>
        log.out(version_line())
        return int(exit_code(Code.OK))
    # init 在配置加载之前：它正是用来产生配置的
    if args.action == "init":
        return int(exit_code(init_command(args)))
    if not args.action:
        build_parser().error(
            "必须给出动作: all / check / style / format / init（all 一次跑完"
            "排版、风格与命名）")
    if args.verbose:
        log.set_verbose(True)

    config, code, message = config_module.load_config(args.config or None)
    if config is None:
        log.err(message)
        return int(exit_code(code))
    for note in getattr(config, "notes", None) or ():
        log.err(note)
    # 互斥：两份工具同时跑同一个工程会互相破坏——一份清构建树时另一份正在用它，
    # 阶段日志与报告也会被交替写成混合文件。init/doctor 这类只读入口不占锁
    holder, why = platform.acquire_lock(getattr(config, "log_dir", ""))
    if holder is None and why:
        log.err(why)
        return int(exit_code(Code.ENV_BUSY))
    try:
        return _run_locked(config, args, code)
    finally:
        platform.release_lock(holder)


def _run_locked(config, args, code):
        """拿到互斥锁之后真正干活（锁由调用方释放）。"""
        collector = report_module.Collector()
        # 配置审计先于任何动作：纪律违规没必要再花时间调 clang。
        # --no-audit 只用于调试审计本身。
        if not args.no_audit:
            code, messages = audit_mod.run(config)
            if code != Code.OK:
                for line in messages:
                    log.err(line)
                publish_report(config, args, collector)
                return int(exit_code(code))
        # 命名口径元检查：毫秒级，放在调 clang 之前。口径被悄悄削弱（例如把 s_ 改回
        # g_）时，clang-tidy 会"合规地绿"，只有这一层拦得住
        code, messages = naming_meta.check(config)
        if code != Code.OK:
            for line in messages:
                log.err(line)
            log.err("命名口径与配置不一致；口径表 NAMING_* 是唯一事实源，"
                    "请改口径表或修正 .clang-tidy")
            publish_report(config, args, collector)
            return int(exit_code(code))
        code = run_request(config, to_request(args), collector)
        publish_report(config, args, collector)
        return int(exit_code(code))


def entrypoint():
    """console_scripts 的入口。"""
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.err("已中断，产物保留")
        sys.exit(int(exit_code(Code.INTERRUPTED)))


if __name__ == "__main__":
    entrypoint()
