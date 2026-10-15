"""跨模块共享的数据类型。

只放数据结构，不放逻辑：两侧（style / format）与各 provider 交换的就是这些。
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Level(StrEnum):
    """诊断级别。取值与 clang-tidy 的 Level 字段一一对应。"""

    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"


@dataclass(frozen=True)
class Finding:
    """一条结构化诊断。

    check 是 clang-tidy 的 DiagnosticName，权威字段——不再从人读文本的方括号
    里猜（真检查名后面还会跟 -warnings-as-errors 这类别名）。
    """

    file: Path
    line: int
    column: int
    level: Level
    message: str
    check: str
    # 这条诊断的替换会写到哪些文件。通常就是 file 自己，但改名类修复会跨文件，
    # 落点可能落在处理范围之外——"改谁"与"写到哪"是两件事，必须分开知道
    targets: tuple[Path, ...] = ()

    def describe(self) -> str:
        """渲染成人读的一行（编译器版式 + 检查名）。"""
        text = f"{self.file}:{self.line}:{self.column}: {self.level.value}: {self.message}"
        return f"{text} [{self.check}]" if self.check else text


@dataclass(frozen=True)
class Entry:
    """编译数据库里的一条编译命令。"""

    directory: Path
    file: Path
    arguments: tuple[str, ...] = ()

    def to_json(self) -> dict:
        """转成 clang 编译数据库的条目形状。"""
        return {
            "directory": str(self.directory),
            "file": str(self.file),
            "arguments": list(self.arguments),
        }


@dataclass
class ClangTools:
    """一次运行里解析到的 clang 工具。"""

    clang_format: Path | None = None
    clang_tidy: Path | None = None

    def banner(self) -> list[str]:
        """启动横幅：实际用到的路径与版本，供人核对版本漂移。"""
        from . import fmt

        lines = []
        for name, path in (("clang-format", self.clang_format),
                           ("clang-tidy", self.clang_tidy)):
            if path is None:
                continue
            lines.append(f"{name}: {path} ({fmt.version_text(path) or '版本无法识别'})")
        return lines
