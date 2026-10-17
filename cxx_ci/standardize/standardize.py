#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排版、风格、命名一条命令跑完。交互入口，带 --apply。

与 `ci/ci.py` 是一对：`ci.py` 是门禁（只读，不改工作区），`standardize.py` 是
整理（能改写工作区）。两侧只靠文件系统与命令行耦合，见仓库根的 CONTRACT.md。

    # 只检查，不改任何文件
    python3 standardize/standardize.py --config config/standardize.py

    # 一次修完：排版 + 风格 + 命名
    python3 standardize/standardize.py --config config/standardize.py --apply

    # 首次接入：排版要先建立基线（注释优先的基线必须人确认一次）
    python3 standardize/standardize.py --accept-baseline --yes

没装包也能直接用：脚本自己把 `src/` 加进搜索路径，拷过去配好就能跑，和
`ci/ci.py` 一样。装过包的话 `cxx-standardize all` 是同一个动作。

顺序是刻意的：
  1. 风格与命名（clang-tidy）—— 改名会改变标识符长度；
  2. 排版（clang-format）—— 放最后收敛列对齐（AlignConsecutive* 按最长项补齐）。

安全性：风格修复走"每翻译单元只读导出 → 合并去重 → 一次性原子应用 → 末尾构建
判卷 → 整体回滚"，判卷用配置里的 VERIFY_COMMAND。它没过就整体回滚，工作区保持
原样，不会留下半改状态。
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(ROOT, "src")
if os.path.isdir(SOURCE) and SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

try:
    from cxx_standardize.cli import KNOWN_ACTIONS, main
    from cxx_standardize.errors import Code, exit_code
    from cxx_standardize import log
except ImportError as exc:  # pragma: no cover - 只在装坏时走到
    sys.stderr.write(
        "导入 cxx_standardize 失败: {}\n"
        "缺依赖时先装：pipx install \"{}\"\n".format(exc, ROOT))
    raise SystemExit(2)


def _positional(args):
    """argv 里的位置参数（跳过选项，以及形如 --x v 的选项取值）。"""
    found = []
    index = 0
    while index < len(args):
        item = args[index]
        if item.startswith("-"):
            if "=" not in item and index + 1 < len(args) \
                    and not args[index + 1].startswith("-"):
                index += 2
                continue
            index += 1
            continue
        found.append(item)
        index += 1
    return found


def entrypoint(argv=None):
    """缺省动作取 all：不带动作就是"三件事都跑一遍"。"""
    args = list(sys.argv[1:] if argv is None else argv)
    # 只认**位置参数**里的动作名。按"任意位置出现动作名"来判时，某个选项的
    # **取值**恰好等于动作名（`--paths check`、`--exclude format`）就会让包装
    # 脚本以为动作已经给了，于是不插 all，最后报"必须给出动作"
    positional = _positional(args)
    # 动作表从 CLI 取，不在这里另写一份：漏一个动作的后果是"把动作当成没给"，
    # 由包装脚本插一个 all，最后由 argparse 报 unrecognized arguments——报错
    # 位置离原因很远（doctor 加进来时就踩过）
    if not any(item in KNOWN_ACTIONS for item in positional) and not any(
            item in ("-h", "--help", "--version") for item in args):
        args.insert(0, "all")
    # Ctrl-C 要给人话与一个明确的退出码，而不是一段 Python 栈：这个脚本与装出来的
    # 控制台脚本是同一层，两者都绕开了 cli.__main__ 里那个处理
    try:
        return main(args)
    except KeyboardInterrupt:
        log.err("已中断，产物保留")
        return int(exit_code(Code.INTERRUPTED))


if __name__ == "__main__":
    sys.exit(int(entrypoint()))
