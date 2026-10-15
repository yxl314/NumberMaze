"""输出与过程噪声。

本项目输出**人读文本**：结论行、诊断、以及"下一步该做什么"的指引。协议行
（`#diag` 那套）只存在于门禁项目内部，这里不参与。

过程噪声（工具自身的进度、`make: 已是最新`）默认只落盘到
`CI_LOG_DIR/<阶段>.log`，`--verbose` 才透传到终端：交互入口的终端只该出现
结论，噪声留在日志里可查。
"""

import os
import sys
from pathlib import Path

VERBOSE_ENV = "CXX_STANDARDIZE_VERBOSE"

_VERBOSE = False


def set_verbose(on=True):
    """打开透传。只开不关：驱动层用环境变量打开后，子脚本不该把它关掉。"""
    global _VERBOSE
    if on:
        _VERBOSE = True


def verbose():
    """当前是否透传过程噪声。"""
    return _VERBOSE or os.environ.get(VERBOSE_ENV) == "1"


def ensure_utf8_streams():
    """让标准输出能写中文。

    区域设置为 C 时默认编码是 ASCII，直接 print 中文会抛 UnicodeEncodeError。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                # 改不了编码的流（已被包装、已关闭）只能用它的原编码；
                # 在这里报错会盖住真正的失败，得不偿失
                pass


def out(text=""):
    """常规输出。"""
    sys.stdout.write(str(text) + "\n")


def err(text=""):
    """诊断输出。"""
    sys.stderr.write(str(text) + "\n")


def log_path(config, stage):
    """阶段日志的路径；没有日志目录时返回空。"""
    directory = getattr(config, "log_dir", None)
    if not directory:
        return ""
    return str(Path(directory) / f"{stage}.log")


def truncate_log(config, stage):
    """开跑前清空该阶段的日志，避免上一次运行的内容混进来。"""
    path = log_path(config, stage)
    if not path:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("", encoding="utf-8")


def append_log(config, stage, text):
    """追加到阶段日志。"""
    path = log_path(config, stage)
    if not path or not text:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")


def noise(config, stage, text):
    """过程噪声：默认只落盘，`--verbose` 才上屏。"""
    if not text:
        return
    append_log(config, stage, text)
    if verbose():
        out(text)
