#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标准输出、标准错误与编码处理。"""
import sys
if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")
import io
import sys


def out(text):
    """向标准输出写一行。立即刷新，保证与子进程输出顺序一致。"""
    sys.stdout.write(text + "\n")
    sys.stdout.flush()

def err(text):
    """向标准错误写一行。立即刷新，保证与子进程输出顺序一致。"""
    sys.stderr.write(text + "\n")
    sys.stderr.flush()

def ensure_utf8_streams():
    """把标准输出与错误切到 UTF-8，避免非 UTF-8 区域设置下输出中文报错。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        buffer = getattr(stream, "buffer", None)
        encoding = (getattr(stream, "encoding", None) or "").lower()
        if buffer is None or encoding.replace("-", "") == "utf8":
            continue
        # 先脱离原包装器，避免其析构时关闭底层缓冲
        if hasattr(stream, "detach"):
            stream.detach()
        setattr(sys, name, io.TextIOWrapper(
            buffer, encoding="utf-8", errors="replace", line_buffering=True))

def decode(data):
    """把子进程输出按 UTF-8 解码，非法字节替换，避免受区域设置影响。"""
    return data.decode("utf-8", "replace")
