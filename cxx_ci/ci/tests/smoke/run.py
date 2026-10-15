#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编译并运行 googletest 1.8.1 冒烟工程。

1.8.1 是最后一个支持 C++98 的版本，因此在只支持 C++98 的老编译器上
也能跑通。

用法: python3 ci/tests/smoke/run.py [--std c++98] [--cxx g++]
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from lib import output  # noqa: E402

GTEST = HERE / "third_party" / "googletest"


def compile_and_run(cxx, standard, binary):
    """编译冒烟工程并执行，返回 (退出码, 输出)。"""
    cmd = [cxx, "-std=" + standard]
    if standard == "c++98":
        cmd.append("-DSMOKE_REQUIRE_CXX98=1")
    cmd += [
        "-I", str(GTEST / "include"),
        "-I", str(GTEST),
        str(GTEST / "src" / "gtest-all.cc"),
        str(GTEST / "src" / "gtest_main.cc"),
        str(HERE / "smoke_test.cpp"),
        "-pthread", "-o", binary,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.communicate()[0].decode("utf-8", "replace")
    if proc.returncode != 0:
        return proc.returncode, "编译失败:\n" + out
    run = subprocess.Popen([binary], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = run.communicate()[0].decode("utf-8", "replace")
    return run.returncode, output


def main():
    output.ensure_utf8_streams()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--std", default="c++98", help="C++ 标准，缺省 c++98")
    parser.add_argument("--cxx", default="g++", help="C++ 编译器，缺省 g++")
    args = parser.parse_args()

    if not (GTEST / "src" / "gtest-all.cc").exists():
        sys.exit("缺少 vendored googletest: " + str(GTEST))

    workdir = Path(tempfile.mkdtemp(prefix="cxx_ci_smoke_"))
    try:
        code, out = compile_and_run(args.cxx, args.std, str(workdir / "smoke_test"))
    finally:
        shutil.rmtree(str(workdir), ignore_errors=True)

    sys.stdout.write(out)
    if code != 0:
        output.err("冒烟测试失败 ({} / {})".format(args.cxx, args.std))
        return 1
    output.out("冒烟测试通过 ({} / {})".format(args.cxx, args.std))
    return 0


if __name__ == "__main__":
    sys.exit(main())
