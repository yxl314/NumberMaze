#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按配置的构建工具配置并编译。"""
import sys
if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")
import os
from pathlib import Path
from .output import err, out
from .tools import find_tool, job_count, run_with_diagnostics
from .errors import (BUILD_COMPILE_FAILED, BUILD_CONFIGURE_FAILED,
                     BUILD_FILE_MISSING, OK, STAGE_UNAVAILABLE)


BUILD_TOOL_BINS = {
    "cmake": ("cmake3", "cmake"),
    "qmake": ("qmake-qt5", "qmake"),
    "qmake6": ("qmake6", "qmake"),
}

def split_standard(value):
    """把 c++98 / gnu++98 拆成 (标准号, 是否启用扩展)。"""
    if value.startswith("gnu++"):
        return value[5:], True
    if value.startswith("c++"):
        return value[3:], False
    return value, False

def build_tool_bin(config):
    """解析构建工具的可执行文件，配置留空时按候选名探测。

    探测结果会记录，避免用户以为在用 cmake 而实际用了 cmake3。
    """
    if config.build_tool_bin:
        return config.build_tool_bin
    found, code, message = find_tool(BUILD_TOOL_BINS.get(
        config.build_tool, (config.build_tool,)))
    if code == OK:
        out("自动探测到构建工具: " + found)
        return found
    return None


def _configure_then(configure, build, build_dir):
    """先在构建目录内执行配置命令，成功后再执行构建命令。"""
    if run_with_diagnostics(configure, build_dir) != 0:
        return BUILD_CONFIGURE_FAILED
    if run_with_diagnostics(build, build_dir) != 0:
        return BUILD_COMPILE_FAILED
    return OK


def _cmake_configure(binary, config, build_file, build_type):
    """cmake 配置命令。

    用「在构建目录内执行 cmake <源码目录>」这种老写法，兼容 CMake 3.6。
    """
    command = [
        binary, str(Path(build_file).parent),
        "-DCMAKE_BUILD_TYPE=" + ("Debug" if build_type == "debug" else "Release"),
    ]
    # 空值一律不传：写一个空的 -D 会把 cmake 自己的缺省覆盖成"没有编译器"，
    # 比不传更糟。留空的意思就是"让构建工具自己决定"
    if config.cxx_compiler:
        command.append("-DCMAKE_CXX_COMPILER=" + config.cxx_compiler)
    if config.cxx_standard:
        standard, extensions = split_standard(config.cxx_standard)
        command.append("-DCMAKE_CXX_STANDARD=" + standard)
        command.append("-DCMAKE_CXX_EXTENSIONS=" + ("ON" if extensions else "OFF"))
    command.append(
        # 导出编译命令。本阶段不用它，但排版/风格项目（另一个项目，
        # 见 CONTRACT.md）按约定从这里读；缺了它那边只能退到自建 provider
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON")
    return command


def _qmake_configure(binary, config, build_file, build_type):
    command = [
        binary, str(build_file),
        "CONFIG+=" + ("debug" if build_type == "debug" else "release"),
    ]
    if config.cxx_compiler:
        command.append("QMAKE_CXX=" + config.cxx_compiler)
    if config.cxx_standard:
        command.append("QMAKE_CXXFLAGS+=-std=" + config.cxx_standard)
    return command


def configure_command(config, binary, build_file, build_type, extra=None):
    """构造配置命令；extra 逐项追加在末尾，返回 argv。

    追加而不是插入是必须的：qmake 的命令行赋值按出现顺序生效，asan 这类
    净化器注入的 `LIBS+=` 必须排在末尾，运行库才会落在 LIBS 尾部；排在前
    面会被后写的库抢先解析，链接期直接报未定义符号。
    """
    if config.build_tool == "cmake":
        command = _cmake_configure(binary, config, build_file, build_type)
    else:
        command = _qmake_configure(binary, config, build_file, build_type)
    return command + [str(item) for item in extra or ()]


def build_command(config, binary, build_dir, jobs):
    """构造编译命令，返回 argv。

    cmake 的 -j 需经 -- 传给底层构建工具，CMake 3.12 之前不支持直接 -j。
    """
    if config.build_tool == "cmake":
        return [binary, "--build", build_dir, "--",
                "-j" + str(job_count(jobs))]
    return ["make", "-j" + str(job_count(jobs))]


def build_project(config, build_file, build_dir, build_type, jobs, extra=None):
    """按配置的构建工具配置并编译一个构建文件，返回退出码。

    extra 透传给配置命令，供需要注入编译/链接参数的路线使用（如 asan 的
    影子构建）；缺省为空，行为与不做注入时一致。
    """
    if not os.path.isfile(build_file):
        err("构建文件不存在: " + build_file)
        return BUILD_FILE_MISSING
    binary = build_tool_bin(config)
    if not binary:
        # 机器上没装构建工具是环境缺件：标不可用让上层跳过，而不是判红
        err("找不到构建工具 {}，可用 BUILD_TOOL_BIN 指定".format(
            config.build_tool))
        return STAGE_UNAVAILABLE
    if not os.path.isdir(build_dir):
        os.makedirs(build_dir)
    configure = configure_command(config, binary, build_file, build_type,
                                  extra)
    return _configure_then(configure,
                           build_command(config, binary, build_dir, jobs),
                           build_dir)
