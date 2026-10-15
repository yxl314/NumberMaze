"""排版基线的指纹与一次性切换。

`protect-column` 要求行尾注释的列号在排版前后不变，存量仓库不可能天生满足，
首次接入必然被拒。这不是工具坏了，而是"基线切换"的一次性动作没做完：本模块
记录那次切换的指纹，让"第一次"和"之后每次"有可区分的依据。

指纹由三部分组成：clang-format 的版本、注释模式、排版配置内容。任一变化都会
让排版结果不同——同一份输入在不同主版本下判定不一致，同一个工具换一份
`.clang-format` 会整仓重排，因此只记版本号不够。配置摘要算的是文件内容，
配置文件改了名字但内容没变时基线依旧有效。
"""

import hashlib
import json
import os
from pathlib import Path

from .errors import Code

# 基线记录的固定文件名，放在日志目录下（清理阶段会保留该目录）
BASELINE_NAME = "format-baseline.json"

# 参与比对的字段，顺序即提示信息里的顺序
PARTS = ("version", "comments", "config_digest")

# 没有配置文件时的固定标记：clang-format 会按文件名逐级向上查找配置，用上
# 哪一份取决于调用者的目录，无法进指纹，只能记下"没有显式配置"这件事
BUILTIN = "builtin"

# 字段的中文名，用于说清是版本还是配置变了
_PART_NAMES = {
    "version": "clang-format 版本",
    "comments": "注释模式",
    "config_digest": "排版配置",
}


def config_digest(path: Path | str | None) -> str:
    """排版配置内容摘要；没有配置文件时返回固定标记。

    读不动的文件（权限、目录、竞态删除）一律退成 BUILTIN：这份摘要只用于
    判断"配置是否变过"，读不到内容时宁可当作没有显式配置，也不能让一次
    读取失败把指纹变成随机值。
    """
    if not path:
        return BUILTIN
    target = Path(path)
    try:
        if not target.is_file():
            return BUILTIN
        data = target.read_bytes()
    except OSError:
        return BUILTIN
    return hashlib.sha256(data).hexdigest()


def fingerprint(binary: Path | str | None, version_text: str, comments: str,
                config_file: Path | str | None) -> str:
    """构造当前基线记录，返回它的 JSON 文本。

    binary/version_text 是 clang-format 的路径与自报版本行，comments 是生效
    的注释模式，config_file 是参与指纹的配置文件。文本里的 fingerprint 字段
    才是用于比对的摘要，其余字段保留下来，是为了在漂移时能说清是哪一项变了，
    而不是只报一句"基线不一致"。

    返回文本而不是字典：这份记录要跨环节传递并原样落盘，文本可直接比较、
    记录与哈希，读回来与这里产出的内容一致；需要按字段查看时用 json.loads，
    write/compare 本身两种形状都接受。
    """
    digest = config_digest(config_file)
    # 材料里必须带上工具**解析到的真实路径**：主版本号相同不代表排版结果相同，
    # 系统包与 pip wheel 都报 17.0.6，换 PATH 顺序就会换来源而指纹毫无反应——
    # 那样的基线挡不住无声换源，而基线机制存在的理由正是"杜绝无声重排"
    # realpath 只做规范化，不碰文件系统（缺路径也能算出结果），因此不需要兜异常
    resolved = os.path.realpath(str(binary)) if binary else ""
    material = "\n".join([version_text or str(binary or ""), comments, digest,
                           resolved])
    record = {
        "tool": str(binary or ""),
        "resolved": resolved,
        "version": version_text or "",
        "comments": comments,
        "config": str(config_file or ""),
        "config_digest": digest,
        "fingerprint": hashlib.sha256(material.encode("utf-8")).hexdigest(),
    }
    return json.dumps(record, sort_keys=True)


def record_path(log_dir: Path | str | None) -> Path | None:
    """基线记录的路径；未配置日志目录时返回 None。

    用 None 而不是空路径表示"没地方放"：空字符串会被 Path 解释成当前目录，
    那会把基线悄悄写进调用者的工作目录。
    """
    if not log_dir:
        return None
    return Path(log_dir) / BASELINE_NAME


def _as_record(current: dict | str | bytes | None) -> dict | None:
    """把传入的指纹统一成记录字典。

    记录既可能是 fingerprint 产出的 JSON 文本（跨环节传递、写盘用的形状），
    也可能已经是解析过的字典（read 的产物）。两种都接受，形状完全不对时返回
    None，由调用方转成错误码。
    """
    if isinstance(current, dict):
        return current
    if isinstance(current, (str, bytes, bytearray)):
        try:
            parsed = json.loads(current)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def read(log_dir: Path | str | None) -> tuple[dict | None, Code, str]:
    """读基线记录，返回 (记录或 None, 错误码, 说明)。

    记录不存在不算错：还没做过一次性切换的仓库就是没有基线，是否要建立
    由使用者决定。结构损坏或缺指纹才是错误——那说明上一次写入没完成。
    """
    path = record_path(log_dir)
    if path is None or not path.is_file():
        return None, Code.OK, ""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, Code.BASELINE_BAD_FILE, f"无法读取排版基线: {path}: {exc}"
    try:
        record = json.loads(data.decode("utf-8", "replace"))
    except ValueError as exc:
        return None, Code.BASELINE_BAD_FILE, f"排版基线结构损坏: {path}: {exc}"
    if not isinstance(record, dict) or "fingerprint" not in record:
        return None, Code.BASELINE_BAD_FILE, f"排版基线缺少指纹字段: {path}"
    return record, Code.OK, ""


def write(log_dir: Path | str | None,
          current: dict | str) -> tuple[Path | None, Code, str]:
    """写基线记录，返回 (路径, 错误码, 说明)。

    先写目录再写文件：日志目录可能还没建（例如只跑 format 就被拒），而
    "写不进去"必须是可报的错误——基线没落下就宣称一次性切换完成，下一次
    运行会再次被拒，使用者会以为工具坏了。
    """
    path = record_path(log_dir)
    if path is None:
        return None, Code.BASELINE_UNWRITABLE, "未配置日志目录，无法记录排版基线"
    record = _as_record(current)
    if record is None:
        return None, Code.BASELINE_UNWRITABLE, (
            f"排版基线内容不是记录: {current!r}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(record, indent=2, sort_keys=True) + "\n"
        path.write_text(text, encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc:
        return None, Code.BASELINE_UNWRITABLE, f"无法写入排版基线: {path}: {exc}"
    return path, Code.OK, ""


def _changed_parts(record: dict, current: dict) -> list[str]:
    """列出指纹不同的组成部分，便于说清是版本还是配置变了。"""
    return [_PART_NAMES[key] for key in PARTS
            if record.get(key) != current.get(key)]


def compare(record: dict | str | None,
            current: dict | str) -> tuple[Code, str]:
    """比对基线与当前指纹，返回 (错误码, 说明)。

    没有基线时返回 OK：是否做一次性切换由使用者决定，工具不替他拒绝。指纹
    变了就必须重新确认——否则工具升级或换配置之后整仓被无声重排，diff 里
    看不出这是版本漂移造成的。说明里点名变化的部分，并把下一步动作写清楚。

    record 既接受 read 解析出的字典，也接受 fingerprint 产出的文本：两者是
    同一条记录的不同形状，比对逻辑不该要求调用方先做转换。
    """
    if record is None:
        return Code.OK, ""
    stored = _as_record(record)
    if stored is None:
        return Code.BASELINE_BAD_FILE, "排版基线记录结构损坏，无法比对"
    other = _as_record(current)
    if other is None:
        return Code.BASELINE_CHANGED, (
            "排版基线无法比对（当前指纹结构损坏），需重新确认后再纳入门禁：\n"
            "确认后请重新执行 --accept-baseline 建立新基线")
    if stored.get("fingerprint") == other.get("fingerprint"):
        return Code.OK, ""
    parts = _changed_parts(stored, other)
    detail = "、".join(parts) if parts else "基线内容"
    return Code.BASELINE_CHANGED, (
        f"排版基线已变（{detail}），需重新确认后再纳入门禁：\n"
        f"  旧: {stored.get('version', '')} {stored.get('comments', '')}\n"
        f"  新: {other.get('version', '')} {other.get('comments', '')}\n"
        f"确认后请重新执行 --accept-baseline 建立新基线")
