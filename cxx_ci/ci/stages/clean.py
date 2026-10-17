#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删除构建输出目录，幂等。

删除项目与测试的构建目录；目录不存在时同样返回成功。
契约见 ci.py 的 docstring，共享参数见 lib/。
"""

import os
import shutil
import os
import sys

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

from lib import platform, cli, errors, output, paths



def remove_contents(target, keep=(), frozen=()):
    """删除目录内容，保留编译数据库、日志目录与禁改区；返回跳过项数。

    编译数据库描述的是编译参数而非构建产物，风格检查依赖它；日志是诊断
    输出，摘要会给出它的路径。两者都不随构建产物一起清除，否则失败后
    回头查不到证据，摘要里的路径也成了死链。

    禁改区（vendored 冻结库）的产物参与主工程链接，删了就构建不起来；
    standardize 侧花力气守住"绝不可写"，清理这一侧不能反手把它删掉。
    """
    skipped = 0
    try:
        names = os.listdir(target)
    except OSError as exc:
        # docstring 说清理是幂等的：目录不存在（没构建过、被别的进程清过）不是
        # 错误，"清一个本来就干净的树"与"清完的树"结果一样
        output.out("无需清理 {}（{}）".format(target, exc))
        return 0
    for name in names:
        if name == paths.COMPILE_DATABASE or name in keep:
            continue
        path = os.path.join(target, name)
        if paths.is_frozen(path, frozen):
            output.out("禁改区内的产物，已跳过: " + path)
            skipped += 1
            continue
        # islink 必须先判：isdir 会跟随软链，把"指向目录的软链"当成目录交给
        # rmtree，而 shutil 拒绝在软链上执行、直接抛异常。要摘的是链本身
        if os.path.islink(path) or not os.path.isdir(path):
            try:
                os.unlink(path)
            except OSError as exc:
                # 单项删不掉（权限、被占用、并发已经删掉）不该让整轮清理停下；
                # 说清是哪一个，剩下的继续删
                output.err("删不掉 {}: {}".format(path, exc))
                skipped += 1
            continue
        try:
            shutil.rmtree(path)
        except OSError as exc:
            output.err("删不掉 {}: {}".format(path, exc))
            skipped += 1
    return skipped


def kept_names(config, target):
    """构建目录里要保留下来的子项名。

    日志目录可以配在构建树的**任意深度**（CI_LOG_DIR 是一项自由配置），只保留
    "恰好是直接子项"那一种布局时，别的布局整棵日志树会被清掉：诊断证据没了，
    摘要里给出的日志路径变成死链，连互斥锁文件一起被删——锁挂在已 unlink 的
    inode 上，互斥名存实亡。
    """
    keep = [platform.LOCK_NAME]
    log_dir = os.path.abspath(config.log_dir or "")
    target_abs = os.path.abspath(target)
    if log_dir and paths.is_within(log_dir, target_abs):
        relative = os.path.relpath(log_dir, target_abs)
        head = relative.split(os.sep)[0]
        if head and head != os.curdir:
            keep.append(head)
    return tuple(keep)


def run(config, args):
    frozen = getattr(config, "frozen", []) or []
    # 构建目录本身落在禁改区里等于把红线整个绕开：那不是"跳过几项"，是配置错
    for target in (config.build_dir, config.test_build_dir):
        if paths.is_inside_frozen(target, frozen):
            output.err("构建目录落在禁改区内，配置不成立: " + target
                       + "（BUILD_DIR / TEST_BUILD_DIR 不能与 FROZEN 重叠）")
            return errors.CONFIG_BAD_VALUE
    # --build-dir 是共享参数，且 build/build_test 都认它；clean 只认配置里的
    # BUILD_DIR 就会"点名清 A、实际清 B"，还把 A 报成已清理
    build_dir = getattr(args, "build_dir", "") or config.build_dir
    test_build_dir = getattr(args, "test_build_dir", "") or config.test_build_dir
    skipped = 0
    for target in (build_dir, test_build_dir):
        if not paths.is_within(target, config.root, strict=True):
            output.err("拒绝删除项目根之外的路径: " + target)
            return errors.UNSAFE_DELETE
        if os.path.isdir(target):
            skipped += remove_contents(target, kept_names(config, target),
                                       frozen)
            output.out("已清理 " + target)
    if skipped:
        # 跳过不等于通过：摘要里单列出来，别让人以为清干净了
        output.out("因禁改区跳过 {} 项".format(skipped))
    return errors.OK


if __name__ == "__main__":
    sys.exit(cli.stage_main(__doc__, run))
