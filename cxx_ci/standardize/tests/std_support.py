"""集成测试夹具：造一个临时项目与一份配置，跑真实的 CLI。"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

CONFIG_TEMPLATE = '''\
from pathlib import Path

CONTRACT_VERSION = 3
ROOT = Path(__file__).resolve().parents[2]
BUILD_TOOL = "cmake"
BUILD_DIR = ROOT / "build"
CXX_STANDARD = "c++11"
CLANG_FORMAT_CONFIG = ROOT / ".clang-format"
CLANG_TIDY_CONFIG = ROOT / ".clang-tidy"
CLANG_MAJOR = None
CLANG_TIDY_EXTRA_ARGS = []
WHITELIST = [ROOT / "src"]
BLACKLIST = []
SCOPE = {"project": [ROOT / "src"], "excluded": [ROOT / "build"]}
FORMAT_COMMENTS = "placeholder"
# 修复必须验证构建，因此模板给一条必然成功的验证指令
VERIFY_COMMAND = ["true"]
FROZEN = []
TIDY_HEADER_FILTER = ""
AUDIT_FILES_GLOBS = []
'''

# 假的 clang-format：问版本时报版本，给内容时原样回显（等价于"已排版"）
ECHO_FORMAT = """#!/bin/sh
case "$1" in
  --version) echo 'Ubuntu clang-format version 17.0.6' ;;
  *) cat ;;
esac
"""


class Project:
    """一个临时项目：源码、配置、假的 clang 工具。"""

    def __init__(self):
        self.path = Path(tempfile.mkdtemp(prefix="cxx_std_test_"))
        self.write("src/a.cpp", "int main() { return 0; }\n")
        self.write(".clang-format", "BasedOnStyle: LLVM\n")
        self.write(".clang-tidy", 'Checks: "*"\n')
        # 编译数据库：cmake provider 只认同目录有 CMakeCache.txt 的那份
        self.write("build/CMakeCache.txt", "CMAKE_CACHEFILE_DIR:INTERNAL=x\n")
        self.write("build/compile_commands.json", json.dumps([{
            "directory": str(self.path),
            "command": "g++ -std=c++11 -c src/a.cpp",
            "file": str(self.path / "src" / "a.cpp"),
        }]))
        self.configure(CONFIG_TEMPLATE)

    # ------------------------------------------------------------ 文件
    def write(self, relative, text):
        path = self.path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def configure(self, text):
        """覆写项目配置。"""
        self.write("std/config/config.py", text)

    def read(self, relative):
        return (self.path / relative).read_text(encoding="utf-8")

    # ------------------------------------------------------------ 假工具
    def fake_tool(self, name, body):
        """在临时目录里放一个假可执行文件，返回加进 PATH 的环境。"""
        directory = Path(tempfile.mkdtemp(prefix="cxx_std_tool_"))
        tool = directory / name
        tool.write_text(body, encoding="utf-8")
        tool.chmod(0o755)
        return directory

    def env(self, *directories):
        environ = dict(os.environ)
        head = os.pathsep.join(str(item) for item in directories)
        environ["PATH"] = head + os.pathsep + environ.get("PATH", "")
        return environ

    # ------------------------------------------------------------ 运行
    def run(self, args, env=None, cwd=None):
        """跑一次 CLI，返回 (退出码, stdout, stderr)。

        配置走 --config 显式指定：装成包时工具目录里没有 config/，这正是使用者
        的常态。
        """
        environ = dict(os.environ)
        environ["PYTHONPATH"] = str(SRC)
        if env:
            environ.update(env)
        command = [sys.executable, "-m", "cxx_standardize.cli"]
        if "--config" not in args and "--version" not in args:
            command += ["--config", str(self.path / "std" / "config"
                                        / "config.py")]
        proc = subprocess.run(
            command + list(args),
            cwd=str(cwd or self.path), env=environ,
            capture_output=True, text=True, check=False)
        return proc.returncode, proc.stdout, proc.stderr

    def dispose(self):
        shutil.rmtree(self.path, ignore_errors=True)
