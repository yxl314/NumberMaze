"""增量取集：把"本次改了什么"映射成需要处理的文件。

门禁成本应当与改动规模相关，而不是与仓库规模相关。非 git 环境或区间取不到
时退回全量并给出原因（不做自己的一套版本控制解析）——增量是加速手段，不是
准入条件。

只用 git 的"名字列表"接口（`diff --name-only`），不解析 diff 内容：这里只
需要知道改了哪些路径，改动内容由后续的排版与风格检查去看。
"""

import shutil
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

from .errors import Code

# 认作头文件的扩展名：改到头文件会影响包含它的源文件，需要一并处理
HEADER_SUFFIXES = (".h", ".hpp", ".hh", ".hxx")


def _git(args: Sequence[str], root: Path) -> tuple[str, Code, str]:
    """执行 git 命令，返回 (输出文本, 错误码, 说明)。

    git 不存在与"命令返回非零"要分开：前者是环境缺工具（GIT_UNAVAILABLE），
    后者才可能是"这不是仓库"或基线区间取不到。命令一律设 timeout，避免在
    网络文件系统或锁住的仓库上把门禁挂死。
    """
    try:
        done = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return "", Code.GIT_UNAVAILABLE, f"无法执行 git: {exc}"
    if done.returncode != 0:
        detail = done.stderr.strip() or done.stdout.strip()
        return "", Code.GIT_UNAVAILABLE, (
            f"git {' '.join(args)} 失败: {detail}")
    return done.stdout, Code.OK, ""


def is_repository(root: Path | str) -> bool:
    """判断目录是否在 git 仓库内。

    用 `rev-parse --git-dir` 而不是检查 `.git` 目录：子目录、worktree、
    裸仓库与 `GIT_DIR` 环境变量下前者都成立，后者会漏判。
    """
    _, code, _ = _git(["rev-parse", "--git-dir"], Path(root))
    return code == Code.OK


def changed_paths(root: Path | str,
                  base: str | None = None) -> tuple[list[Path], Code, str]:
    """取改动集，返回 (绝对路径列表, 错误码, 说明)。

    base 为 None 时取工作区与暂存区相对 HEAD 的改动；给出 base 时取该区间
    （base...HEAD）内的改动。路径一律是 pathlib.Path，与候选文件直接按值
    比较，不再依赖字符串归一化；形式（绝对或相对）与传入的 root 一致。

    目录本身不是 git 仓库时返回 NOT_A_REPOSITORY，与"git 不可用"分开报：
    前者是本项目的正常使用场景（源码包接入），后者是环境问题。
    """
    root = Path(root)
    if shutil.which("git") is None:
        return [], Code.GIT_UNAVAILABLE, "无法执行 git: PATH 上找不到 git"
    if not is_repository(root):
        return [], Code.NOT_A_REPOSITORY, "不是 git 仓库，退回全量处理"
    revision = f"{base}...HEAD" if base else "HEAD"
    args = ["diff", "--name-only", "--diff-filter=ACMR", revision]
    text, code, message = _git(args, root)
    if code != Code.OK:
        return [], code, message
    paths = [root / line.strip() for line in text.splitlines() if line.strip()]
    return paths, Code.OK, ""


def select(changed: Iterable[Path | str], candidates: Iterable[Path | str],
           dependencies=None,
           suffix: str | None = None) -> tuple[list[Path], list[str]]:
    """把改动集映射到待处理文件，返回 (选中的文件, 提示行)。

    改动的文件本身必定入选。改动的头文件还要带出受它影响的候选文件，这靠
    `dependencies(path) -> (包含的头文件集合, 错误码, 说明)` 给出——由编译器
    自己的 `-MM` 算出来的精确包含关系，间接包含与同名头文件都认。

    这里不准备第二套实现：`dependencies` 缺席或解析不出来时不做文本比对，
    而是把该候选**一并选中**并在提示里说明。多处理一个文件只是慢一点，漏掉
    受影响的翻译单元则会给出"检查通过"的假结论。

    suffix 用于筛掉不在处理范围内的扩展名。返回顺序沿用 candidates，让上层
    的并行与报告输出稳定。
    """
    touched = [Path(item) for item in changed]
    candidate_paths = [Path(item) for item in candidates]
    selected: list[Path] = []
    headers: list[Path] = []
    for path in touched:
        if suffix and not str(path).endswith(suffix):
            continue
        if str(path).endswith(HEADER_SUFFIXES):
            headers.append(path)
        else:
            selected.append(path)
    notes: list[str] = []
    if headers and dependencies is not None:
        wanted = {Path(item).resolve() for item in headers}
        unresolved = 0
        for path in candidate_paths:
            if path in selected:
                continue
            found, code, _ = dependencies(path)
            if code != Code.OK:
                unresolved += 1
                selected.append(path)
                continue
            if found & wanted:
                selected.append(path)
        if unresolved:
            notes.append(
                f"{unresolved} 个文件的依赖关系解析不出来，已一并处理以免漏检")
    return [path for path in candidate_paths if path in selected], notes
