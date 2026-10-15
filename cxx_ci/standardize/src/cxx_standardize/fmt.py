"""clang-format 的调用封装。

只做三件事：把内容喂给 clang-format、把结果拿回来、把版本问清楚。判定与
策略（幂等自检、注释保护、基线）在上层，这里不掺和。

内容走标准输入，只把文件名交给 `--assume-filename`：既不必在源码树里落临时
文件（崩在中途就会留下垃圾），也不影响样式查找与语言判定。
"""

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

from .errors import Code

# 排版结果随 clang-format 主版本变化，低于这个版本的结果与其他版本不一致
CLANG_MINIMUM = 17


def find_tool(names):
    """在 PATH 上按顺序找到第一个可用的可执行文件。"""
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found), Code.OK, ""
    return None, Code.TOOL_MISSING, "找不到可执行文件: " + "、".join(names)


# 版本号形状：主.次。用它挑出"哪一行才是版本行"，而不是默认第一行
_VERSION_TOKEN = re.compile(r"\b\d+\.\d+")


def version_text(binary):
    """工具自报的版本行；识别不了返回空串。

    不能只看第一行：发行版把 clang-tidy 的版本印在首行（`Ubuntu LLVM version
    17.0.6`），上游与 pip wheel 的构建印在第二行（首行是 `LLVM (http://llvm.org/):`）。
    取第一行含版本号的那一行，两边都对。
    """
    result = _run([str(binary), "--version"])
    if result is None:
        return ""
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    for line in lines:
        if _VERSION_TOKEN.search(line):
            return line
    return lines[0] if lines else ""


def version(binary):
    """解析主版本号，返回 (版本, 错误码, 说明)。

    三种情况要分开说：进程起不来、起来了但动态库加载失败、正常输出但版本串
    形状陌生。第二种最容易被误报成第三种——自己装到非标准前缀的 clang 就是
    这样：加载器把 "cannot open shared object file" 打在 stderr 上，stdout
    一个字都没有，看起来像"认不出版本"，实际上要配的是 LD_LIBRARY_PATH。
    """
    result = _run([str(binary), "--version"])
    if result is None:
        return None, Code.COMMAND_UNRUNNABLE, (
            f"无法执行 {binary}；若是自己装的 clang，确认它的动态库在 "
            f"LD_LIBRARY_PATH 上")
    for token in result.stdout.replace("-", ".").split():
        head = token.split(".")[0]
        if head.isdigit():
            return int(head), Code.OK, ""
    stderr = (result.stderr or "").strip().splitlines()
    if not result.stdout.strip() and stderr:
        # stdout 全空、stderr 有话说：多半是加载器拦下的，把原话带上
        return None, Code.COMMAND_UNRUNNABLE, (
            f"{binary} 起来了但没能运行：{stderr[0]}；"
            f"若是自己装的 clang，确认它的动态库在 LD_LIBRARY_PATH 上")
    return None, Code.TOOL_MISSING, f"无法识别版本: {binary}"


def require(binary, name, minimum=CLANG_MINIMUM):
    """要求版本不低于 minimum，返回 (错误码, 说明)。

    只回答"能不能用"：版本号与路径由 resolve 给出，这里再回一个值只会让调用方
    去猜该看哪一个。
    """
    found, code, _ = version(binary)
    if code != Code.OK:
        return code, f"无法识别 {name} 的版本: {binary}"
    if found < minimum:
        return Code.TOOL_TOO_OLD, (
            f"{name} 版本 {found} 低于要求的 {minimum}，"
            f"排版结果会与其他版本不一致")
    return Code.OK, ""


def candidates(name, major=None):
    """按钉定版本给出候选可执行文件名，顺序即优先级。"""
    if major:
        return [f"{name}-{major}", name]
    return [name]


def wheel_binary(name):
    """pip wheel 自带的二进制，找不到返回 None。

    `clang-format` 与 `clang-tidy` 两个包都把二进制放在 `<包>/data/bin/` 下，
    版本由包自己钉定——比在 PATH 上碰运气可靠得多，也是版本可复现的前提。
    只定位包的位置、不 import 它：这两个包的 `__init__` 会拖进 `pkg_resources`
    之类的历史包袱，在只装二进制时没必要背。
    """
    module = name.replace("-", "_")
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin:
        return None
    binary = Path(spec.origin).parent / "data" / "bin" / name
    return binary if binary.is_file() else None


# pip wheel 与系统包的取舍：缺省 wheel 优先（装了对应的包就不必管 PATH 上是
# 哪个版本），但这件事必须**可显式指定**——团队里装了 wheel 的机器与没装的机器
# 排版结果不同，而主版本号相同不代表来源相同。取值：wheel / system / error
PREFER_WHEEL = "wheel"
PREFER_SYSTEM = "system"
PREFER_ERROR = "error"
PREFER_CHOICES = (PREFER_WHEEL, PREFER_SYSTEM, PREFER_ERROR)


def prefer_of(config):
    """从配置读 CLANG_PREFER，取值非法时按缺省并说明。"""
    value = str(getattr(config, "clang_prefer", "") or PREFER_WHEEL).strip().lower()
    if value not in PREFER_CHOICES:
        return PREFER_WHEEL, ("CLANG_PREFER 取值 {} 不认识（可选 {}），"
                              "按缺省的 wheel 处理".format(
                                  value, " / ".join(PREFER_CHOICES)))
    return value, ""


def resolve(name, major=None, config=None):
    """查找 clang 工具并校验版本，返回 (路径, 错误码, 说明)。

    多版本共存时取到哪个只看 PATH，而结果随版本变化，因此允许钉定主版本：
    先找带该后缀的名字，再退回裸名但校验版本；不匹配就报错并列出 PATH 上的
    候选与各自版本，而不是悄悄用错的那个。

    `config.clang_prefer` 决定 pip wheel 与系统 clang 谁优先：
      wheel（缺省）—— 装了对应的包就用它；
      system        —— 只认 PATH 上的，忽略 wheel；
      error         —— 两者都在时直接报错，让人明确挑一个。
    """
    prefer, note = prefer_of(config) if config is not None else (PREFER_WHEEL, "")
    bundled = None if prefer == PREFER_SYSTEM else wheel_binary(name)
    system = _system_binary(name, major)
    if prefer == PREFER_ERROR and bundled and system:
        return None, Code.CONFIG_BAD_VALUE, (
            "CLANG_PREFER=error，而 pip 包与系统 PATH 上各有一个 {}：\n"
            "  pip 包   {}\n  PATH 上  {}\n"
            "请用 CLANG_FORMAT_BIN / CLANG_TIDY_BIN 指定，"
            "或把 CLANG_PREFER 改成 wheel / system".format(
                name, bundled, system))
    if bundled is not None:
        found, vcode, _ = version(bundled)
        if vcode == Code.OK and (not major or found == major):
            return bundled, Code.OK, note
    if system is not None:
        return system, Code.OK, note
    if not major:
        binary, code, _ = find_tool([name])
        if code != Code.OK:
            return None, code, (
                f"找不到 {name}，请先安装：pip install {name}，"
                f"或用系统包管理器装 clang-tools")
        return binary, Code.OK, ""
    lines = [(f"找不到 {name} 的主版本 {major}。可 pip install "
              f"{name}=={major}.*，或让它出现在 PATH 上。PATH 上的候选：")]
    for path in all_binaries(name):
        found, code, _ = version(path)
        lines.append(f"  {path} 版本 {found if code == Code.OK else '无法识别'}")
    if len(lines) == 1:
        lines.append(f"  （没有找到任何 {name}）")
    return None, Code.TOOL_MISSING, "\n".join(lines)


def resolve_explicit(path, name, major=None):
    """用配置里显式指定的路径解析工具，返回 (路径, 错误码, 说明)。

    与 resolve() 的区别是**不退回 PATH 搜索**：写了就必须是它。需要精确复现
    时（基线指纹含工具真实路径），"随手找一个同主版本的"正是要避免的事。
    找不到时报错并列出 PATH 上的候选，方便对照着改。
    """
    binary = Path(str(path))
    if not binary.is_file():
        lines = ["{} 指向的文件不存在: {}".format(
            "CLANG_FORMAT_BIN" if name == "clang-format" else "CLANG_TIDY_BIN",
            path)]
        lines.append("PATH 上的候选：")
        for item in all_binaries(name):
            found, code, _ = version(item)
            lines.append("  {} 版本 {}".format(
                item, found if code == Code.OK else "无法识别"))
        return None, Code.TOOL_MISSING, "\n".join(lines)
    found, code, message = version(binary)
    if code != Code.OK:
        return None, code, "无法识别 {} 的版本: {}（{}）".format(
            name, binary, message)
    if major and found != major:
        return None, Code.TOOL_MISSING, (
            "{} 指向的 {} 主版本是 {}，配置要求 {}".format(
                "CLANG_FORMAT_BIN" if name == "clang-format"
                else "CLANG_TIDY_BIN", binary, found, major))
    return binary, Code.OK, ""


def _system_binary(name, major):
    """PATH 上满足主版本要求的第一个候选，找不到返回 None。"""
    for candidate in candidates(name, major):
        binary, code, _ = find_tool([candidate])
        if code != Code.OK or binary is None:
            continue
        found, vcode, _ = version(binary)
        if vcode != Code.OK:
            continue
        if major and found != major:
            continue
        return binary
    return None


def all_binaries(name):
    """PATH 上所有同名或带数字后缀的候选，供报错时列清单。"""
    import os
    import re

    pattern = re.compile(r"^" + re.escape(name) + r"-\d+$")
    found, unreadable = [], []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not Path(entry).is_dir():
            continue
        try:
            names = sorted(os.listdir(entry))
        except OSError:
            # 读不了的目录要列出来：否则"一个都没找到"这句话可能是假的
            unreadable.append(entry)
            continue
        for item in names:
            # 只认裸名与数字后缀，clang-tidy-diff-21.py 这类派生脚本不算
            if item != name and not pattern.match(item):
                continue
            path = Path(entry) / item
            if path.is_file() and path not in found:
                found.append(path)
    if unreadable:
        # 附在候选清单里，让"没找到"与"没看到"能分辨
        found.append(Path("（以下目录无法读取，可能有候选被漏掉: "
                          + "、".join(unreadable) + "）"))
    return found


def style_spec(config, config_file=None):
    """决定传给 clang-format 的样式，返回 (样式串, 参与指纹的配置文件, 码, 说明)。

    配置里点了名却不存在时直接失败，不退成 `--style=file`：后者会让
    clang-format 逐级向上查找，用上哪一份完全取决于调用者的当前目录；而基线
    指纹记的是"没有配置文件"，那份配置日后被改，基线也察觉不到。排版结果
    可复现、可指纹化是这套基线的前提，不能靠一句警告糊过去。
    """
    chosen = config_file or config.clang_format_config
    if not chosen:
        return "file", "", Code.OK, ""
    path = Path(chosen)
    if not path.is_file():
        return "", "", Code.CONFIG_NOT_FOUND, (
            f"排版配置文件不存在: {path}。请创建它，或在 CLANG_FORMAT_CONFIG / "
            f"--config-file 里指向真实文件（只想用内置样式就写一份只含 "
            f"BasedOnStyle 的配置）")
    return f"file:{path}", str(path), Code.OK, ""


def format_text(binary, style, filename, text):
    """把文本交给 clang-format，返回 (结果文本, 错误码, 说明)。"""
    result = _run([str(binary), f"--style={style}",
                   f"--assume-filename={filename}"], stdin=text)
    if result is None:
        return None, Code.COMMAND_UNRUNNABLE, f"无法执行 clang-format: {binary}"
    if result.returncode != 0:
        detail = result.stderr.strip() or f"clang-format 处理失败: {filename}"
        return None, Code.FORMAT_FAILED, detail
    return result.stdout, Code.OK, ""


def _run(command, stdin=None, cwd=None, env=None):
    """执行命令并捕获输出；执行不了返回 None。"""
    try:
        return subprocess.run(
            command, input=stdin, cwd=cwd, env=env,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=False)
    except (OSError, subprocess.SubprocessError):
        return None
