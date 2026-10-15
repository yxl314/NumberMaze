#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部面边界：detail:: 归属与头文件宏守卫。

两条纪律都是"仓内代码的内部面"问题，与禁改区写保护（那是"不许写"）不同：

  - detail:: 只应出现在模块自己的目录里。越出模块目录去用别人的 detail::
    意味着绕过门面直接调内部实现，只能人工改回公开入口，因此**不提供自动修**。
  - 头文件的宏守卫名由相对路径决定：路径即身份，天然唯一，移动文件即强制改名。

判据都在配置里（DETAIL_DIRS / DETAIL_MARK / GUARD_EXTS），本模块不写死任何
具体项目的事实。
"""

import os
import re
import sys
if sys.version_info < (3, 4):
    sys.exit("需要 Python 3.4 或更高版本，请用 python3 运行")

from . import paths, source

# detail:: 检查要覆盖全部源文件；宏守卫只对头文件有意义，再由 GUARD_EXTS 筛一次
SOURCE_EXTS = (".h", ".hh", ".hpp", ".hxx", ".h++", ".inl", ".ipp",
               ".c", ".cc", ".cpp", ".cxx", ".c++", ".cu", ".cuh")

# 守卫名只由路径字符决定，非字母数字一律换成下划线
_GUARD_ALLOWED = re.compile(r"[^A-Za-z0-9]")
_IFNDEF = re.compile(r"^\s*#\s*ifndef\s+([A-Za-z_]\w*)\s*$")
_DEFINE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)\s*$")


def strip_comments(text):
    """把注释换成等长空格，保持行列对齐。

    在剥除结果上搜关键字，注释里提到 detail:: 才不会误报——这是"允许在注释里
    说明内部实现"的前提。字面量只跳过不替换：它是代码，原样留着；不跳过的话
    字符串里的 // 会把后半行当注释，真正的代码反而被剥掉。
    """
    out = []
    index = 0
    size = len(text)
    while index < size:
        char = text[index]
        if char == "R" and text.startswith('R"', index):
            end = _skip_raw(text, index)
            out.append(text[index:end])
            index = end
            continue
        if char in "\"'":
            end = _skip_literal(text, index)
            out.append(text[index:end])
            index = end
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = size if end < 0 else end
            out.append(_blank(text[index:end]))
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = size if end < 0 else end + 2
            out.append(_blank(text[index:end]))
            index = end
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _blank(chunk):
    """等长空白，保留换行（行号与列号都不能变）。"""
    return "".join("\n" if item == "\n" else " " for item in chunk)


def _skip_literal(text, index):
    """跳过字符串或字符字面量，返回结束后的下标。"""
    quote = text[index]
    index += 1
    size = len(text)
    while index < size:
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == quote:
            return index + 1
        index += 1
    return size


def _skip_raw(text, index):
    """跳过 C++11 原始字符串，定界符随写法定。"""
    start = text.find("(", index + 2)
    if start < 0:
        return len(text)
    delimiter = text[index + 2:start]
    closing = ")" + delimiter + '"'
    end = text.find(closing, start)
    return len(text) if end < 0 else end + len(closing)


def read_text(path):
    """读文本；读不了返回空串（调用方按"没有内容"处理）。

    读不了的情况真实存在：悬空软链、权限不足、文件在两次遍历之间被删掉。以前
    这里直接 open，一个读不到的文件就让整段只读检查崩栈退出，同一轮里别的结论
    一条都报不出来——而 docstring 承诺的是返回空串。
    """
    try:
        handle = open(str(path), "rb")
    except (IOError, OSError):
        return ""
    try:
        return handle.read().decode("utf-8", "replace")
    except (IOError, OSError):
        return ""
    finally:
        handle.close()


def source_files(dirs, exts):
    """递归收集受管目录下的源码文件，按路径排序。"""
    found = []
    for directory in dirs:
        if os.path.isfile(directory):
            found.append(directory)
            continue
        if not os.path.isdir(directory):
            continue
        for root, names, files in os.walk(directory):
            names.sort()
            for name in sorted(files):
                if os.path.splitext(name)[1] in exts:
                    found.append(os.path.join(root, name))
    return sorted(found)


def find_detail_leaks(files, detail_dirs, mark):
    """找出模块目录之外出现 mark 的位置，返回 [(文件, 行号, 行内容)]。"""
    inside = [os.path.abspath(item) for item in detail_dirs]
    leaks = []
    for path in files:
        target = os.path.abspath(path)
        if any(paths.is_within(target, item) for item in inside):
            continue
        text = strip_comments(read_text(path))
        for number, line in enumerate(text.splitlines(), 1):
            if mark in line:
                leaks.append((path, number, line.strip()))
    return leaks


def expected_guard(path, root):
    """期望的宏守卫名：相对仓库根的路径，非字母数字换下划线后全大写。"""
    target = os.path.abspath(path)
    base = os.path.abspath(root) if root else ""
    if base and paths.is_within(target, base):
        target = os.path.relpath(target, base)
    return _GUARD_ALLOWED.sub("_", target).upper()


def actual_guard(text):
    """取文件现有的守卫，返回 (名字, 是否残缺)。

    首个 `#ifndef X` 且其后有配对的 `#define X` 才算完整；只有 ifndef 没有
    配对 define 的算残缺——这种写法等于守卫没生效，比缺失更隐蔽。
    """
    name = ""
    for line in text.splitlines():
        if not name:
            match = _IFNDEF.match(line)
            if match:
                name = match.group(1)
            continue
        match = _DEFINE.match(line)
        if match and match.group(1) == name:
            return name, False
    return name, bool(name)


def guard_report(files, root, exts):
    """检查守卫，返回 [(文件, 期望名, 实际名, 问题码)]。

    问题码：missing 缺失、misnamed 名字不符、broken 残缺。
    """
    report = []
    for path in files:
        if os.path.splitext(path)[1] not in exts:
            continue
        want = expected_guard(path, root)
        found, broken = actual_guard(read_text(path))
        if broken:
            report.append((path, want, found, "broken"))
        elif not found:
            report.append((path, want, "", "missing"))
        elif found != want:
            report.append((path, want, found, "misnamed"))
    return report


def fix_guards(report, config=None):
    """按报告补齐或改正守卫，返回改动过的文件列表。

    只动守卫三处（#ifndef / #define / 末尾 #endif 的注释），不碰文件其余内容：
    插入点取"文首空行与注释块之后"，改名只在原有行上原地替换。

    写回一律走 source：判定不出编码的文件跳过并说明，绝不用 UTF-8 覆盖掉用户
    的 GBK/其它编码存量文件。跳过的文件仍留在 report 里，由调用方照常判红。
    """
    changed = []
    for path, want, found, problem in report:
        text, info = source.read_source(path, config)
        if text is None:
            sys.stderr.write("编码无法确定（{}）: {}，本文件不会被改写\n".format(
                info.reason, path))
            continue
        if problem == "missing":
            text = _insert_guard(text, want)
        elif problem == "broken":
            text = _complete_guard(text, found)
        else:
            text = _rename_guard(text, found, want)
        hit, why = paths.write_blocked(config, path)
        if hit:
            # 工程内的软链指向工程外时，按链接路径看它在范围里，实际被改写的
            # 是外面那个共享头文件——而这份命令是本工具自己在输出里给出的
            sys.stderr.write("拒绝改写 {}（{}）\n".format(path, why))
            continue
        code, message = source.write_source(path, text, info)
        if code != 0:
            sys.stderr.write("{}\n".format(message))
            continue
        changed.append(path)
    return changed


def _insert_guard(text, want):
    """在文首空行与注释块之后插入守卫，并在文末补 #endif。"""
    lines = text.splitlines(True)
    index = 0
    while index < len(lines) and (not lines[index].strip()
                                  or lines[index].lstrip().startswith(
                                      ("//", "/*", "*"))):
        index += 1
    block = ["#ifndef {}\n".format(want), "#define {}\n".format(want), "\n"]
    body = lines[:index] + block + lines[index:]
    if body and not body[-1].endswith("\n"):
        body[-1] = body[-1] + "\n"
    body.append("#endif  // {}\n".format(want))
    return "".join(body)


def _complete_guard(text, name):
    """补上配对的 #define，并把 #endif 的注释统一成守卫名。

    残缺的守卫等于没生效，但既然 #ifndef 已经写了，就地补一行比整块重插更贴近
    作者原意，也不会把原有的注释挤走。
    """
    lines = []
    inserted = False
    for line in text.splitlines(True):
        lines.append(line)
        if not inserted and _IFNDEF.match(line):
            lines.append("#define {}\n".format(name))
            inserted = True
    body = "".join(lines)
    return _ensure_endif_comment(body, name)


def _ensure_endif_comment(text, name):
    """文末的 #endif 带上守卫名注释；没有 #endif 就补一个。"""
    lines = text.splitlines(True)
    if not lines:
        return "#endif  // {}\n".format(name)
    if not lines[-1].endswith("\n"):
        lines[-1] = lines[-1] + "\n"
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].lstrip().startswith("#endif"):
            lines[index] = "#endif  // {}\n".format(name)
            return "".join(lines)
    lines.append("#endif  // {}\n".format(name))
    return "".join(lines)


def _rename_guard(text, found, want):
    """原地三处同步改名。"""
    lines = []
    for line in text.splitlines(True):
        if _IFNDEF.match(line) and found in line:
            line = line.replace(found, want)
        elif _DEFINE.match(line) and found in line:
            line = line.replace(found, want)
        elif line.lstrip().startswith("#endif") and found in line:
            line = line.replace(found, want)
        lines.append(line)
    return "".join(lines)


def describe(report):
    """把守卫报告渲染成一行行结论。"""
    notes = []
    for path, want, found, problem in report:
        if problem == "missing":
            notes.append("{}: 缺少宏守卫，期望 {}".format(path, want))
        elif problem == "broken":
            notes.append("{}: 宏守卫残缺（有 #ifndef {} 却没有配对 #define）"
                         .format(path, found))
        else:
            notes.append("{}: 宏守卫名不符，现为 {}，期望 {}"
                         .format(path, found, want))
    return notes
