"""命令行入口。

用法：

    cxx-standardize check                 # 风格 + 排版门禁（预览）
    cxx-standardize style [--apply]       # 只做风格
    cxx-standardize format [--apply]      # 只做排版
    cxx-standardize format --accept-baseline --yes   # 一次性切换

退出码（见 CONTRACT.md）：0 无需改动；1 有待改动或检查未通过；2 工具或配置错误。
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from . import fmt, guards, log
from .version import version_line
from . import platform
from . import config as config_module
from . import format as format_mod
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
KNOWN_ACTIONS = ("check", "style", "format", "all", "guard", "doctor")


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
             "guard 检查宏守卫与 detail:: 归属；doctor 只读自检",
    )
    parser.add_argument("--fix", action="store_true",
                        help="guard 时补/改头文件宏守卫")
    parser.add_argument("--record", action="store_true",
                        help="把当前未登记的诊断登记成基线，只对新增违规判红")
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
    parser.add_argument("--no-prepare", action="store_true",
                        help="跳过分析前的派生文件生成步")
    parser.add_argument("--jobs", type=int, default=0,
                        help="并行任务数，0 表示按 CPU 核数")
    parser.add_argument("--verbose", action="store_true",
                        help="透传工具自身的进度等过程噪声（缺省只落盘到日志）")
    parser.add_argument("--config", default="",
                        help="项目配置的位置；缺省取环境变量 "
                             "CXX_STANDARDIZE_CONFIG；示例见 config-example/standardize_config.py")
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
        apply=args.apply,
        apply_naming=False,
        fix=args.fix,
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


def run_request(config, request):
    """按请求执行，返回错误码。"""
    if request.accept_baseline and request.action == "style":
        log.err("--accept-baseline 只对排版有意义，不能与 style 同时使用")
        return Code.CONFIG_BAD_VALUE
    if request.yes and not request.accept_baseline:
        log.err("--yes 只用于 --accept-baseline 的二次确认")
        return Code.CONFIG_BAD_VALUE
    if request.action == "guard":
        return guards.run(config, fix=request.options.fix)

    tools, code, message = resolve_tools(config, needed_tools(request))
    if code != Code.OK:
        log.err(message)
        return code
    for line in tools.banner():
        log.out(line)

    first = Code.OK
    for name in actions_for(request):
        log.out(f"== {name} ==")
        log.truncate_log(config, name)
        if name == "style":
            if request.options.apply:
                # --apply 的语义包含命名：改完风格顺带把命名也规范了
                request.options.apply_naming = True
            if request.options.record:
                code = style_mod.record(config, request.options, tools)
            elif request.options.apply:
                code = style_mod.apply(config, request.options, tools)
            else:
                code = style_mod.check(config, request.options, tools)
        elif request.accept_baseline:
            code = format_mod.accept_baseline(config, request.options,
                                               tools, request.yes)
        else:
            code = format_mod.check(config, request.options, tools)
        if code != Code.OK:
            log.err(f"{name} 未通过")
            return code
        if first == Code.OK:
            first = code
    return first


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


def main(argv=None):
    log.ensure_utf8_streams()
    # 平台守卫：与 ci.py 同一判据。排版与风格在 macOS/BSD 可用（limited 时放行），
    # 只有明确不支持的系统才当场退出
    if platform.guard():
        return 2
    args = build_parser().parse_args(argv)
    if "doctor" == getattr(args, "action", ""):
        return doctor_command(args)
    if args.version:
        # 统一形状，与 ci.py --version 一致：<项目> <版本> contract <N> python >=<下限>
        log.out(version_line())
        return int(exit_code(Code.OK))
    if not args.action:
        build_parser().error(
            "必须给出动作: all / check / style / format（all 一次跑完排版、风格与命名）")
    if args.verbose:
        log.set_verbose(True)

    config, code, message = config_module.load_config(args.config or None)
    if config is None:
        log.err(message)
        return int(exit_code(code))
    # 互斥：两份工具同时跑同一个工程会互相破坏——一份清构建树时另一份正在用它，
    # 阶段日志与报告也会被交替写成混合文件。doctor 这类只读入口不占锁
    holder, why = platform.acquire_lock(getattr(config, "build_dir", ""))
    if holder is None and why:
        log.err(why)
        return int(exit_code(Code.ENV_BUSY))
    try:
        return _run_locked(config, args, code)
    finally:
        platform.release_lock(holder)


def _run_locked(config, args, code):
        """拿到互斥锁之后真正干活（锁由调用方释放）。"""
        return int(exit_code(run_request(config, to_request(args))))


def entrypoint():
    """console_scripts 的入口。"""
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.err("已中断，产物保留")
        sys.exit(int(exit_code(Code.INTERRUPTED)))


if __name__ == "__main__":
    entrypoint()
