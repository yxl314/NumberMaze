#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""集成测试夹具：把被测脚本复制到临时目录，避免污染仓库。"""

import os
import shutil
import signal
import subprocess

import pytest
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# 会被被测脚本调用的阶段脚本，测试可随意覆写
STAGE_NAMES = ("boundary", "clean", "build", "build_test", "run_test",
               "memcheck")

# 沙箱用的合法配置，其中 ROOT 指向沙箱自身。
# 日志目录不显式设置：缺省是 BUILD_DIR/ci-logs，测试按这个位置找日志。
CONFIG_TEMPLATE = """\
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_TOOL = "cmake"
BUILD_TOOL_BIN = ""
BUILD_FILE = ROOT / "CMakeLists.txt"
BUILD_DIR = ROOT / "build"
CXX_COMPILER = "g++"
CXX_STANDARD = "c++98"
TEST_BUILD_FILE = ROOT / "test" / "CMakeLists.txt"
TEST_BUILD_DIR = ROOT / "build" / "test"
TEST_COMMAND = [TEST_BUILD_DIR / "run_tests"]
TEST_WORKDIR = TEST_BUILD_DIR
MEMCHECK_COMMAND = TEST_COMMAND
WHITELIST = [ROOT / "src", ROOT / "include"]
BLACKLIST = [ROOT / "third_party"]

MEMCHECK_SUPPRESSIONS = ROOT / "config" / "valgrind.supp"
DIAGNOSTIC_LEVEL = "error"
DIAGNOSTIC_SOURCES = "project"
DIAGNOSTIC_IGNORE = []
DIAGNOSTIC_DEDUP = True
"""


class SandboxCase(object):
    """以沙箱为夹具的测试基类。

    沙箱由 conftest 的 box 夹具提供，每个测试前复位。
    """

    @pytest.fixture(autouse=True)
    def _sandbox(self, box):
        self.box = box
        self.box.reset()

    @staticmethod
    def make_memcheck_target(workdir):
        """造出 MEMCHECK_COMMAND 指的那个可执行文件。

        内存检查的前置条件是"build 已经产出它"。单独跑这一阶段的用例必须自己
        满足这个前置，否则测到的是"二进制不存在"这条检查，而不是它本意要测的
        那条路。
        """
        if not workdir.is_dir():
            workdir.mkdir(parents=True)
        target = workdir / "run_tests"
        handle = open(str(target), "w")
        try:
            handle.write("#!/bin/sh\nexit 0\n")
        finally:
            handle.close()
        target.chmod(0o755)


class Sandbox(object):
    """被测脚本的一份临时副本。"""

    def __init__(self):
        self.path = Path(tempfile.mkdtemp(prefix="cxx_ci_test_"))
        shutil.copy(str(ROOT / "ci.py"), str(self.path / "ci.py"))
        stages = self.path / "stages"
        stages.mkdir()
        for name in STAGE_NAMES:
            shutil.copy(str(ROOT / "stages" / (name + ".py")),
                        str(stages / (name + ".py")))
        shutil.copytree(str(ROOT / "lib"), str(self.path / "lib"))
        shutil.copytree(str(ROOT / "config"), str(self.path / "config"))
        # 必须带上 .gitignore，否则 __pycache__ 会被提交并在后续运行中反复变化
        shutil.copy(str(ROOT / ".gitignore"), str(self.path / ".gitignore"))
        self.has_git = self._init_git()

    def _git(self, args):
        env = dict(os.environ)
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "test"
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "test@example.com"
        return subprocess.call(
            ["git"] + args, cwd=str(self.path), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _init_git(self):
        """建一个已提交的仓库，供 --apply 的干净检查使用。"""
        if self._git(["init", "-q"]) != 0:
            return False
        self._git(["add", "-A"])
        if self._git(["commit", "-q", "-m", "init"]) != 0:
            return False
        self.initial = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(self.path)).decode().strip()
        return True

    def reset(self):
        """回到初始提交，丢弃全部改动与新增文件。"""
        if not self.has_git:
            self.clear_stages()
            return
        self._git(["reset", "-q", "--hard", self.initial])
        # -x 连被 .gitignore 忽略的构建产物一并清掉，否则测试间会互相污染
        self._git(["clean", "-qfdx"])
        # 配置模板的白名单指向这两个目录，缺了会被判为路径不存在
        for name in ("src", "include"):
            directory = self.path / name
            if not directory.is_dir():
                directory.mkdir()
        self.configure(CONFIG_TEMPLATE)

    def commit(self, message):
        """提交当前改动，使工作区重新变干净。"""
        self._git(["add", "-A"])
        self._git(["commit", "-q", "-m", message])

    def stage(self, name, source):
        """覆写某个阶段脚本。"""
        with open(str(self.path / "stages" / (name + ".py")), "w",
                  encoding="utf-8") as handle:
            handle.write(source)

    def configure(self, text):
        """覆写沙箱内的项目配置。"""
        with open(str(self.path / "config" / "config.py"), "w",
                  encoding="utf-8") as handle:
            handle.write(text)

    def write_xfail(self, text, name="ci.xfail"):
        """在项目根写下 xfail 登记清单。"""
        with open(str(self.path / name), "w", encoding="utf-8") as handle:
            handle.write(text)
        return self.path / name

    def clear_stages(self):
        for name in STAGE_NAMES:
            self.stage(name, "")

    def run(self, script, args, env=None, cwd=None):
        """运行 ci.py 或某个阶段脚本，返回 (退出码, stdout, stderr)。"""
        environ = dict(os.environ)
        if env:
            environ.update(env)
        proc = subprocess.Popen(
            [sys.executable, str(self.path / script)] + args,
            cwd=str(cwd or self.path), env=environ,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out, err = proc.communicate()
        return (proc.returncode,
                out.decode("utf-8", "replace"),
                err.decode("utf-8", "replace"))

    def spawn(self, script, args):
        """启动脚本但不等待，供信号测试使用。"""
        return subprocess.Popen(
            [sys.executable, str(self.path / script)] + args,
            cwd=str(self.path),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def stage_pid(self):
        """阶段脚本写下的自身 PID，未写过返回 None。"""
        path = self.path / "stage.pid"
        if not path.is_file():
            return None
        with open(str(path)) as handle:
            return int(handle.read().strip())

    def stage_alive(self):
        """阶段子进程是否仍存活。"""
        pid = self.stage_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def kill_stage(self):
        """结束仍存活的阶段子进程。"""
        pid = self.stage_pid()
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def dispose(self):
        self.kill_stage()
        shutil.rmtree(str(self.path), ignore_errors=True)
