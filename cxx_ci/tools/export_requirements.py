#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 pyproject.toml 生成 requirements/ 清单。

`requirements/*.txt` 是能直接喂给 `pip install -r`、离线镜像与内部审计的清单。
为避免"清单与 pyproject 各自漂"，内容一律由本工具从 `standardize/pyproject.toml`
生成，不手抄。`ci/` 侧零第三方依赖，清单恒为空（只有头注释），让"零依赖"成为
可被 pip 与审计看见的事实。

    python3 tools/export_requirements.py            # 写盘
    python3 tools/export_requirements.py --check    # 只比对，不一致非零退出

本工具是本仓库的开发工具（用到 Python 3.11+ 的 tomllib），不属于 ci 或
standardize 任一侧，不参与两侧的契约。
"""

import argparse
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "standardize" / "pyproject.toml"
OUT_DIR = ROOT / "requirements"

GENERATED_BY = ("由 tools/export_requirements.py 从 standardize/pyproject.toml "
                "生成，请勿手改。")

CI_HEADER = """\
# ci 侧依赖清单：空清单 + 头注释。
#
# 本侧零第三方依赖，只用标准库，Python >= 3.4（老机器上不需要 pip）。
# 本文件用于占位与审计：任何被加进来的包都应视为违反设计。
#
# {generated}"""

STANDARDIZE_HEADER = """\
# standardize 侧依赖清单（Python >= 3.11）。
#
#     pip install -r requirements/standardize.txt
#
# 含运行依赖、可选 pip clang 工具与开发依赖（测试与静态检查），分节列出。
#
# {generated}"""


def _read_pyproject():
    with open(PYPROJECT, "rb") as handle:
        return tomllib.load(handle)


def _render(header, packages):
    """头注释 + 空行 + 逐行包名，末尾补一个换行。"""
    body = header.format(generated=GENERATED_BY).rstrip("\n").splitlines()
    if packages:
        body.append("")
        body.extend(packages)
    return "\n".join(body) + "\n"


def _render_sections(header, sections):
    """头注释 + 逐节（注释行 + 包行），节间空行，末尾补一个换行。"""
    body = header.format(generated=GENERATED_BY).rstrip("\n").splitlines()
    for comments, packages in sections:
        body.append("")
        body.extend(comments)
        body.extend(packages)
    return "\n".join(body) + "\n"


def expected_files():
    """本应写盘的两份清单：相对名 → 内容。

    只有两个模块各自的清单：`ci/` 零第三方依赖，恒为空（只有头注释）；
    `standardize/` 的运行、可选 pip clang 与开发依赖都归 standardize.txt。
    """
    project = _read_pyproject()["project"]
    optional = project.get("optional-dependencies", {})
    sections = (
        (["# --- 运行依赖 ---"],
         project.get("dependencies", [])),
        (["# --- 可选：机器上没有系统 clang 17 时安装 ---",
          "# clang-format / clang-tidy 自带二进制，版本由包钉定；要改用系统那一份，",
          "# 在配置里写 CLANG_FORMAT_BIN / CLANG_TIDY_BIN 显式指定。"],
         optional.get("clang17", [])),
        (["# --- 开发依赖：测试与静态检查，不影响使用者 ---"],
         optional.get("dev", [])),
    )
    return {
        "ci.txt": _render(CI_HEADER, []),
        "standardize.txt": _render_sections(STANDARDIZE_HEADER, sections),
    }


def check(files):
    """比对盘上文件与应有内容，返回不一致的相对名列表。"""
    problems = []
    for name, text in sorted(files.items()):
        path = OUT_DIR / name
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != text:
            problems.append(name)
    if OUT_DIR.is_dir():
        for path in sorted(OUT_DIR.glob("*.txt")):
            if path.name not in files:
                problems.append(path.name + "（多余）")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="只比对不写盘；不一致时非零退出（供测试钉住不漂）")
    args = parser.parse_args(argv)

    files = expected_files()
    if args.check:
        problems = check(files)
        if problems:
            print("requirements 与 pyproject.toml 不一致: " + "、".join(problems),
                  file=sys.stderr)
            print("请运行 python3 tools/export_requirements.py 重新生成",
                  file=sys.stderr)
            return 1
        print("requirements 与 pyproject.toml 一致")
        return 0

    OUT_DIR.mkdir(exist_ok=True)
    for path in sorted(OUT_DIR.glob("*.txt")):
        if path.name not in files:
            path.unlink()
            print("删除 " + str(path))
    for name, text in sorted(files.items()):
        (OUT_DIR / name).write_text(text, encoding="utf-8")
        print("写入 " + str(OUT_DIR / name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
