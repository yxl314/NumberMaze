#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""valgrind XML 解析与归因的单元测试。

用真实 XML 文本而不是现成对象做夹具：解析与归因是两件事，只有走一遍真实
文本才能同时覆盖 <xwhat> 缺失、<obj> 缺失与多 <stack> 这些分叉。
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import errors as errcodes  # noqa: E402
from lib import paths, valgrind  # noqa: E402
from lib.config import TOOL_ROOT, example_path, load_config  # noqa: E402



PROJECT = "/proj/build"

# 一条纯外部泄漏：栈顶是系统库，下面是第三方库
ALL_EXTERNAL = """<?xml version="1.0" encoding="UTF-8"?>
<valgrindoutput>
  <error>
    <kind>Leak_DefinitelyLost</kind>
    <xwhat><text>128 bytes in 1 blocks are definitely lost</text></xwhat>
    <stack>
      <frame><ip>0x1000</ip><obj>/usr/lib/libc.so.6</obj><fn>malloc</fn></frame>
      <frame><ip>0x2000</ip><obj>/opt/vendor/libbackend.so</obj><fn>start</fn></frame>
    </stack>
  </error>
</valgrindoutput>
"""

# 两条错误：第一条项目帧在外部帧之上，第二条纯外部
MIXED = """<?xml version="1.0" encoding="UTF-8"?>
<valgrindoutput>
  <error>
    <kind>Leak_DefinitelyLost</kind>
    <xwhat><text>64 bytes in 1 blocks are definitely lost</text></xwhat>
    <stack>
      <frame><ip>0x1</ip><obj>/proj/build/app</obj><fn>main</fn></frame>
      <frame><ip>0x2</ip><obj>/usr/lib/libc.so.6</obj><fn>malloc</fn></frame>
    </stack>
  </error>
  <error>
    <kind>Leak_DefinitelyLost</kind>
    <xwhat><text>256 bytes in 2 blocks are definitely lost</text></xwhat>
    <stack>
      <frame><ip>0x3</ip><obj>/opt/vendor/libbackend.so</obj><fn>vendor_open</fn></frame>
    </stack>
  </error>
</valgrindoutput>
"""

# 整条栈只有 <ip>，没有任何可归属的 <obj>
UNKNOWN = """<?xml version="1.0" encoding="UTF-8"?>
<valgrindoutput>
  <error>
    <kind>Leak_DefinitelyLost</kind>
    <xwhat><text>32 bytes in 1 blocks are definitely lost</text></xwhat>
    <stack>
      <frame><ip>0xaaaa</ip></frame>
      <frame><ip>0xbbbb</ip><fn>unknown_fn</fn></frame>
    </stack>
  </error>
</valgrindoutput>
"""

# 栈顶 unknown，下一个帧可归属：应跳过第一个取项目帧
UNKNOWN_THEN_PROJECT = """<?xml version="1.0" encoding="UTF-8"?>
<valgrindoutput>
  <error>
    <kind>InvalidRead</kind>
    <xwhat><text>Invalid read of size 4</text></xwhat>
    <stack>
      <frame><ip>0x1</ip></frame>
      <frame><ip>0x2</ip><obj>/proj/build/app</obj><fn>main</fn></frame>
    </stack>
  </error>
</valgrindoutput>
"""

# 项目帧但类型是 still-reachable：默认应按类型忽略
STILL_REACHABLE = """<?xml version="1.0" encoding="UTF-8"?>
<valgrindoutput>
  <error>
    <kind>Leak_StillReachable</kind>
    <xwhat><text>4096 bytes in 1 blocks are still reachable</text></xwhat>
    <stack>
      <frame><ip>0x1</ip><obj>/proj/build/app</obj><fn>main</fn></frame>
    </stack>
  </error>
</valgrindoutput>
"""

# 只有 stack 没有 xwhat，且帧缺 obj / fn
NO_XWHAT = """<?xml version="1.0" encoding="UTF-8"?>
<valgrindoutput>
  <error>
    <kind>InvalidFree</kind>
    <stack>
      <frame><ip>0x1</ip></frame>
      <frame><ip>0x2</ip><obj>/proj/build/app</obj></frame>
    </stack>
  </error>
</valgrindoutput>
"""

# 一条错误带两条 stack：帧要按出现顺序摊平
MULTI_STACK = """<?xml version="1.0" encoding="UTF-8"?>
<valgrindoutput>
  <error>
    <kind>MismatchedFree</kind>
    <xwhat><text>Mismatched free() / delete / delete []</text></xwhat>
    <stack>
      <frame><ip>0x1</ip><obj>/proj/build/app</obj><fn>a</fn></frame>
    </stack>
    <stack>
      <frame><ip>0x2</ip><obj>/usr/lib/libc.so.6</obj><fn>free</fn></frame>
      <frame><ip>0x3</ip></frame>
    </stack>
  </error>
</valgrindoutput>
"""


# valgrind 3.26 的真实版式：still-reachable 计数为 0 时 <still_reachable>
# 没有闭合标签，整份报告不合法。下面是按其字节结构裁出的最小复现。
VALGRIND_326 = """<?xml version="1.0"?>
<valgrindoutput>
<protocolversion>6</protocolversion>
<error>
  <kind>Leak_DefinitelyLost</kind>
  <xwhat><text>128 bytes in 1 blocks are definitely lost</text></xwhat>
  <stack>
    <frame><ip>0x1</ip><obj>/proj/build/app</obj><fn>leak</fn></frame>
  </stack>
</error>
<leak_summary>
  <definitely_lost>
    <bytes>128</bytes>
    <blocks>1</blocks>
  </definitely_lost>
  <indirectly_lost>
    <bytes>0</bytes>
    <blocks>0</blocks>
  </indirectly_lost>
  <possibly_lost>
    <bytes>0</bytes>
    <blocks>0</blocks>
  </possibly_lost>
  <still_reachable>
    <bytes>0</bytes>
    <blocks>0</blocks>
  <suppressed>
    <bytes>0</bytes>
    <blocks>0</blocks>
  </suppressed>
</leak_summary>
</valgrindoutput>
"""


# 真实 valgrind 的分配栈顶端固定是它自己的 malloc 替换代码，下面才是调用点
PRELOAD_THEN_PROJECT = """<?xml version="1.0" encoding="UTF-8"?>
<valgrindoutput>
  <error>
    <kind>Leak_DefinitelyLost</kind>
    <xwhat><text>128 bytes in 1 blocks are definitely lost</text></xwhat>
    <stack>
      <frame><ip>0x1</ip>
        <obj>/usr/libexec/valgrind/vgpreload_memcheck-amd64-linux.so</obj>
        <fn>malloc</fn></frame>
      <frame><ip>0x2</ip><obj>/proj/build/app</obj><fn>leak</fn></frame>
      <frame><ip>0x3</ip><obj>/usr/lib/libc.so.6</obj><fn>__libc_start_main</fn></frame>
    </stack>
  </error>
</valgrindoutput>
"""

PRELOAD_THEN_EXTERNAL = """<?xml version="1.0" encoding="UTF-8"?>
<valgrindoutput>
  <error>
    <kind>Leak_DefinitelyLost</kind>
    <xwhat><text>48 bytes in 1 blocks are definitely lost</text></xwhat>
    <stack>
      <frame><ip>0x1</ip>
        <obj>/usr/lib/valgrind/vgpreload_memcheck-amd64-linux.so</obj>
        <fn>malloc</fn></frame>
      <frame><ip>0x2</ip><obj>/opt/vendor/libbackend.so</obj><fn>open</fn></frame>
    </stack>
  </error>
</valgrindoutput>
"""


def write(tmp, name, text):
    """把 XML 夹具写成文件，返回路径。"""
    path = os.path.join(tmp, name)
    handle = open(path, "wb")
    try:
        handle.write(text.encode("utf-8"))
    finally:
        handle.close()
    return path


def fixture(text):
    """在临时目录里落一个夹具文件。"""
    return write(tempfile.mkdtemp(), "memcheck.xml", text)


def attribute_all(errors, **kwargs):
    """按同样的参数给一批错误归因。"""
    return [valgrind.attribute(error, [PROJECT], **kwargs) for error in errors]


class TestParseXml:
    def test_reads_kind_text_and_frames(self):
        errors, code, message = valgrind.parse_xml(fixture(ALL_EXTERNAL))
        assert errcodes.OK == code, message
        assert 1 == len(errors)
        error = errors[0]
        assert "Leak_DefinitelyLost" == error.kind
        assert "128 bytes in 1 blocks are definitely lost" == error.text
        assert [valgrind.Frame("/usr/lib/libc.so.6", "malloc"),
                valgrind.Frame("/opt/vendor/libbackend.so", "start")] == error.frames

    def test_missing_xwhat_keeps_error(self):
        errors, code, message = valgrind.parse_xml(fixture(NO_XWHAT))
        assert errcodes.OK == code, message
        assert "" == errors[0].text
        # 后一个帧有 obj 没有 fn，不能因此丢掉整条栈
        assert valgrind.Frame("/proj/build/app", None) == errors[0].frames[1]

    def test_frame_without_obj_is_unknown(self):
        errors, _, _ = valgrind.parse_xml(fixture(UNKNOWN))
        assert [None, None] == [frame.obj for frame in errors[0].frames]

    def test_multiple_stacks_are_flattened(self):
        errors, code, message = valgrind.parse_xml(fixture(MULTI_STACK))
        assert errcodes.OK == code, message
        assert 3 == len(errors[0].frames)
        assert "/usr/lib/libc.so.6" == errors[0].frames[1].obj

    def test_no_error_elements_is_empty(self):
        path = fixture("<valgrindoutput></valgrindoutput>")
        errors, code, message = valgrind.parse_xml(path)
        assert errcodes.OK == code, message
        assert [] == errors

    def test_truncated_xml_returns_code(self):
        errors, code, message = valgrind.parse_xml(
            fixture(ALL_EXTERNAL[: len(ALL_EXTERNAL) // 2]))
        assert errors is None
        assert errcodes.XML_MALFORMED == code
        assert "结构损坏" in message

    def test_empty_file_returns_code(self):
        errors, code, _ = valgrind.parse_xml(fixture(""))
        assert errors is None
        assert errcodes.XML_MALFORMED == code

    def test_missing_file_returns_code(self):
        path = os.path.join(tempfile.mkdtemp(), "not-there.xml")
        errors, code, message = valgrind.parse_xml(path)
        assert errors is None
        assert errcodes.XML_UNREADABLE == code
        assert "无法读取" in message

    def test_repairs_real_valgrind_326_empty_still_reachable(self):
        """valgrind 3.26 在该处漏闭合标签，修复后必须能解析出错误。"""
        errors, code, message = valgrind.parse_xml(fixture(VALGRIND_326))
        assert errcodes.OK == code, message
        assert 1 == len(errors)
        assert "/proj/build/app" == errors[0].frames[0].obj

    def test_repair_does_not_hide_truncated_document(self):
        # 截断发生在漏标签之后：补完仍缺 </leak_summary>，必须继续报错
        cut = VALGRIND_326.index("</suppressed>") + len("</suppressed>")
        errors, code, _ = valgrind.parse_xml(fixture(VALGRIND_326[:cut]))
        assert errors is None
        assert errcodes.XML_MALFORMED == code


class TestAttribute:
    def test_all_external_frames_are_ignored(self):
        errors, _, _ = valgrind.parse_xml(fixture(ALL_EXTERNAL))
        verdicts = attribute_all(errors)
        assert [valgrind.VERDICT_IGNORED] == [item[0] for item in verdicts]
        assert "/usr/lib/libc.so.6" == verdicts[0][1].obj

    def test_project_frame_above_external_frame_fails(self):
        errors, _, _ = valgrind.parse_xml(fixture(MIXED))
        verdicts = attribute_all(errors)
        assert valgrind.VERDICT_FAILURE == verdicts[0][0]
        assert "/proj/build/app" == verdicts[0][1].obj
        assert valgrind.VERDICT_IGNORED == verdicts[1][0]

    def test_unknown_frames_are_skipped_until_attributable(self):
        errors, _, _ = valgrind.parse_xml(fixture(UNKNOWN_THEN_PROJECT))
        decision, frame, reason = valgrind.attribute(errors[0], [PROJECT])
        assert valgrind.VERDICT_FAILURE == decision
        assert "/proj/build/app" == frame.obj
        assert "项目根内" in reason

    def test_all_unknown_frames_fail_with_marker(self):
        errors, _, _ = valgrind.parse_xml(fixture(UNKNOWN))
        decision, frame, reason = valgrind.attribute(errors[0], [PROJECT])
        assert valgrind.VERDICT_FAILURE == decision
        assert frame is None
        assert "归属不明" in reason

    def test_scope_all_counts_out_of_scope(self):
        errors, _, _ = valgrind.parse_xml(fixture(ALL_EXTERNAL))
        decision, _, reason = valgrind.attribute(
            errors[0], [PROJECT], outside=valgrind.OUTSIDE_FAIL)
        assert valgrind.VERDICT_FAILURE == decision
        assert "outside=OUTSIDE_FAIL" in reason

    def test_ignore_objects_hit(self):
        errors, _, _ = valgrind.parse_xml(fixture(MIXED))
        decision, frame, reason = valgrind.attribute(
            errors[0], [PROJECT], ignore_objects=("/proj/build/app",))
        assert valgrind.VERDICT_IGNORED == decision
        assert "/proj/build/app" == frame.obj
        assert "命中忽略对象" in reason

    def test_ignore_prefixes_hit(self):
        errors, _, _ = valgrind.parse_xml(fixture(MIXED))
        decision, _, reason = valgrind.attribute(
            errors[1], [PROJECT], ignore_prefixes=("/opt/vendor",))
        assert valgrind.VERDICT_IGNORED == decision
        assert "命中忽略前缀" in reason

    def test_out_of_scope_is_ignored_by_default_scope(self):
        errors, _, _ = valgrind.parse_xml(fixture(ALL_EXTERNAL))
        errors[0] = errors[0]._replace(
            frames=[valgrind.Frame("/somewhere/else/libfoo.so", "x")])
        decision, _, reason = valgrind.attribute(errors[0], [PROJECT])
        assert valgrind.VERDICT_IGNORED == decision
        assert "outside=OUTSIDE_IGNORE" in reason

    def test_valgrind_preload_frame_is_skipped_to_project(self):
        """栈顶是 valgrind 自己的 malloc 包装时，必须继续往下找到调用点。"""
        errors, _, _ = valgrind.parse_xml(fixture(PRELOAD_THEN_PROJECT))
        decision, frame, reason = valgrind.attribute(
            errors[0], [PROJECT], ignore_prefixes=valgrind.SYSTEM_PREFIXES)
        assert valgrind.VERDICT_FAILURE == decision
        assert "/proj/build/app" == frame.obj
        assert "项目根内" in reason

    def test_valgrind_preload_frame_then_external_is_ignored(self):
        errors, _, _ = valgrind.parse_xml(fixture(PRELOAD_THEN_EXTERNAL))
        decision, frame, _ = valgrind.attribute(
            errors[0], [PROJECT], ignore_prefixes=valgrind.SYSTEM_PREFIXES)
        assert valgrind.VERDICT_IGNORED == decision
        assert "/opt/vendor/libbackend.so" == frame.obj


class TestSummarize:
    def test_all_external_passes_for_project_scope(self):
        errors, _, _ = valgrind.parse_xml(fixture(ALL_EXTERNAL))
        summary = valgrind.summarize(errors, attribute_all(errors))
        assert 0 == summary.count
        assert 1 == summary.ignored
        assert 1 == summary.buckets["system"]
        assert 0 == summary.buckets["external"]
        assert "域内问题 0 处" in summary.headline

    def test_mixed_frames_only_domain_entries_listed(self):
        errors, _, _ = valgrind.parse_xml(fixture(MIXED))
        summary = valgrind.summarize(errors, attribute_all(errors))
        assert 1 == summary.count
        assert 1 == summary.ignored
        assert 0 == summary.buckets["system"]
        assert 1 == summary.buckets["external"]
        details = "\n".join(summary.details)
        assert "64 bytes in 1 blocks" in details
        assert "256 bytes in 2 blocks" not in details

    def test_all_unknown_is_failure_and_marked(self):
        errors, _, _ = valgrind.parse_xml(fixture(UNKNOWN))
        summary = valgrind.summarize(errors, attribute_all(errors))
        assert 1 == summary.count
        assert 0 == summary.ignored
        assert 0 == summary.buckets["external"] + summary.buckets["system"]
        assert "归属不明" in "\n".join(summary.details)

    def test_scope_all_counts_every_external_error(self):
        errors, _, _ = valgrind.parse_xml(fixture(ALL_EXTERNAL))
        verdicts = [valgrind.attribute(error, [PROJECT], outside=valgrind.OUTSIDE_FAIL)
                    for error in errors]
        summary = valgrind.summarize(errors, verdicts)
        assert 1 == summary.count
        assert 0 == summary.ignored

    def test_ignore_kinds_ignores_still_reachable(self):
        errors, _, _ = valgrind.parse_xml(fixture(STILL_REACHABLE))
        verdicts = attribute_all(errors)
        # 归因本身判失败，类型忽略是汇总层的策略
        assert valgrind.VERDICT_FAILURE == verdicts[0][0]
        summary = valgrind.summarize(errors, verdicts)
        assert 0 == summary.count
        assert 1 == summary.ignored
        assert 1 == summary.buckets["kind"]
        assert "类型 1" in summary.headline

    def test_ignore_kinds_accepts_xml_spelling(self):
        errors, _, _ = valgrind.parse_xml(fixture(STILL_REACHABLE))
        summary = valgrind.summarize(
            errors, attribute_all(errors), ignore_kinds=("Leak_StillReachable",))
        assert 0 == summary.count
        assert 1 == summary.ignored

    def test_other_kinds_still_counted(self):
        errors, _, _ = valgrind.parse_xml(fixture(MIXED))
        summary = valgrind.summarize(
            errors, attribute_all(errors), ignore_kinds=("still-reachable",))
        assert 1 == summary.count
        assert 0 == summary.buckets["kind"]

    def test_external_and_system_are_counted_separately(self):
        errors, _, _ = valgrind.parse_xml(fixture(MIXED))
        errors[0] = errors[0]._replace(
            frames=[valgrind.Frame("/usr/lib/libc.so.6", "malloc")])
        summary = valgrind.summarize(errors, attribute_all(errors))
        assert 0 == summary.count
        assert 2 == summary.ignored
        assert 1 == summary.buckets["system"]
        assert 1 == summary.buckets["external"]


class TestPublicSurface:
    def test_public_names_are_exported(self):
        for name in ("Frame", "OUTSIDE_FAIL", "OUTSIDE_IGNORE",
                     "SYSTEM_PREFIXES", "VERDICT_FAILURE", "VERDICT_IGNORED",
                     "XML_MALFORMED", "XML_UNREADABLE", "attribute",
                     "parse_xml", "signature",
                     "summarize", "symbol_prefix"):
            assert hasattr(valgrind, name), name
            


# 静态链接：vendored 库链进可执行文件后，帧的归属对象就是可执行文件本身，
# 路径级忽略对它完全无效——那个路径正是项目自己的产物。符号名是此时唯一
# 还能区分"这段代码谁写的"的依据。
STATIC_LINKED = """<?xml version="1.0" encoding="UTF-8"?>
<valgrindoutput>
  <error>
    <kind>Leak_DefinitelyLost</kind>
    <xwhat><text>40 bytes in 1 blocks are definitely lost</text></xwhat>
    <stack>
      <frame><ip>0x1</ip><obj>/proj/build/app</obj>
        <fn>NormalButton::NormalButton_init()</fn></frame>
      <frame><ip>0x2</ip><obj>/usr/lib/libc.so.6</obj><fn>malloc</fn></frame>
    </stack>
  </error>
  <error>
    <kind>Leak_PossiblyLost</kind>
    <xwhat><text>64 bytes in 1 blocks are possibly lost</text></xwhat>
    <stack>
      <frame><ip>0x3</ip><obj>/proj/build/app</obj>
        <fn>QMetaObject::activate(QObject*, int, void**)</fn></frame>
    </stack>
  </error>
  <error>
    <kind>Leak_DefinitelyLost</kind>
    <xwhat><text>24 bytes in 1 blocks are definitely lost</text></xwhat>
    <stack>
      <frame><ip>0x4</ip><obj>/proj/build/app</obj>
        <fn>Menu::make_menus()</fn></frame>
    </stack>
  </error>
  <error>
    <kind>Leak_DefinitelyLost</kind>
    <xwhat><text>8 bytes in 1 blocks are definitely lost</text></xwhat>
    <stack>
      <frame><ip>0x5</ip><obj>/proj/build/app</obj>
        <fn>TextMessageBox::tr_set_message_info(QString const&amp;)</fn></frame>
    </stack>
  </error>
</valgrindoutput>
"""


class TestSymbolPrefix:
    def test_prefix_drops_parameters_and_last_segment(self):
        assert "NormalButton::" == valgrind.symbol_prefix(
            "NormalButton::NormalButton_init()")
        assert "QMetaObject::" == valgrind.symbol_prefix(
            "QMetaObject::activate(QObject*, int, void**)")
        assert "Ns::Class::" == valgrind.symbol_prefix("Ns::Class::method()")

    def test_free_function_keeps_whole_name(self):
        assert "main" == valgrind.symbol_prefix("main")
        assert "operator new" == valgrind.symbol_prefix("operator new")

    def test_empty_input(self):
        assert "" == valgrind.symbol_prefix("")
        assert "" == valgrind.symbol_prefix(None)


class TestSignature:
    def test_same_stack_same_signature(self):
        errors, _, _ = valgrind.parse_xml(fixture(STATIC_LINKED))
        assert valgrind.signature(errors[0]) == valgrind.signature(errors[0])

    def test_different_stack_different_signature(self):
        errors, _, _ = valgrind.parse_xml(fixture(STATIC_LINKED))
        assert valgrind.signature(errors[0]) != valgrind.signature(errors[2])

    def test_signature_ignores_bytes(self):
        """同一处泄漏每次漏多少会变，签名认的是"哪条栈"。"""
        errors, _, _ = valgrind.parse_xml(fixture(STATIC_LINKED))
        changed = errors[0]._replace(text="4096 bytes in 8 blocks")
        assert valgrind.signature(errors[0]) == valgrind.signature(changed)


class TestIgnoreSymbols:
    def test_symbol_prefix_turns_vendored_frames_external(self):
        errors, _, _ = valgrind.parse_xml(fixture(STATIC_LINKED))
        decision, frame, reason = valgrind.attribute(
            errors[0], [PROJECT], ignore_symbols=("NormalButton::",))
        assert valgrind.VERDICT_IGNORED == decision
        assert "/proj/build/app" == frame.obj
        assert "命中忽略符号" in reason

    def test_symbol_ignore_beats_project_root(self):
        """符号级忽略必须排在项目根判定之前，否则静态链接下毫无作用。"""
        errors, _, _ = valgrind.parse_xml(fixture(STATIC_LINKED))
        decision, _, reason = valgrind.attribute(
            errors[0], [PROJECT], ignore_symbols=("NormalButton::",))
        assert valgrind.VERDICT_IGNORED == decision
        assert "项目根内" not in reason

    def test_unlisted_symbol_still_fails(self):
        errors, _, _ = valgrind.parse_xml(fixture(STATIC_LINKED))
        decision, _, reason = valgrind.attribute(
            errors[2], [PROJECT], ignore_symbols=("NormalButton::",
                                                  "QMetaObject::"))
        assert valgrind.VERDICT_FAILURE == decision
        assert "项目根内" in reason

    def test_configured_prefixes_zero_the_domain(self):
        errors, _, _ = valgrind.parse_xml(fixture(STATIC_LINKED))
        verdicts = attribute_all(
            errors, ignore_symbols=("NormalButton::", "QMetaObject::",
                                    "TextMessageBox::"))
        summary = valgrind.summarize(errors, verdicts)
        assert 1 == summary.count
        assert 3 == summary.ignored


class TestDefaultIgnoreKinds:
    """缺省不计入失败的类别只有一处定义，抖动源默认不在里面。"""

    def test_default_ignores_possibly_lost(self):
        errors, _, _ = valgrind.parse_xml(fixture(STATIC_LINKED))
        verdicts = attribute_all(errors)
        assert valgrind.VERDICT_FAILURE == verdicts[1][0]
        summary = valgrind.summarize(errors, verdicts)
        assert 3 == summary.count
        assert 1 == summary.buckets["kind"]

    def test_ignoring_possibly_lost_can_be_turned_off(self):
        errors, _, _ = valgrind.parse_xml(fixture(STATIC_LINKED))
        summary = valgrind.summarize(errors, attribute_all(errors),
                                   ignore_kinds=("still-reachable",))
        assert 4 == summary.count

    def test_config_default_matches_the_module_constant(self):
        """缺省值只有一处定义：配置的缺省直接取自归因模块。"""
        loaded = load_config(str(example_path()))[0]
        assert list(valgrind.DEFAULT_IGNORE_KINDS) == \
            loaded.memcheck_ignore_kinds


class TestAggregation:
    def test_dominant_prefix_is_listed(self):
        """面对几十条域内问题，使用者需要先看清"大头是谁"。"""
        errors, _, _ = valgrind.parse_xml(fixture(STATIC_LINKED))
        verdicts = attribute_all(errors)
        summary = valgrind.summarize(errors, verdicts)
        # 夹具里那条 PossiblyLost 按缺省类别不计入失败
        assert 3 == summary.count
        assert 1 == summary.ignored
        assert "按符号前缀聚合 Top" in summary.top
        assert "NormalButton:: 1" in summary.top

    def test_ranking_is_by_count_descending(self):
        errors, _, _ = valgrind.parse_xml(fixture(STATIC_LINKED))
        # 让 NormalButton:: 占大头：真实仓库里 35 条域内问题有 13 条同属一个前缀
        errors = errors + [errors[0]] * 3
        verdicts = attribute_all(errors)
        summary = valgrind.summarize(errors, verdicts)
        assert summary.top.index("NormalButton:: 4") < summary.top.index("Menu::")

    def test_top_limit_is_honoured(self):
        errors, _, _ = valgrind.parse_xml(fixture(STATIC_LINKED))
        verdicts = attribute_all(errors)
        summary = valgrind.summarize(errors, verdicts, top=1)
        assert "Top 1" in summary.top

    def test_single_failure_has_no_aggregation_line(self):
        errors, _, _ = valgrind.parse_xml(fixture(STATIC_LINKED))
        verdicts = attribute_all(errors, ignore_symbols=(
            "NormalButton::", "QMetaObject::", "TextMessageBox::"))
        summary = valgrind.summarize(errors, verdicts)
        assert "" == summary.top

    def test_each_failure_line_carries_a_signature(self):
        errors, _, _ = valgrind.parse_xml(fixture(STATIC_LINKED))
        verdicts = attribute_all(errors)
        summary = valgrind.summarize(errors, verdicts)
        details = "\n".join(summary.details)
        assert "sig:" in details
        assert valgrind.signature(errors[0]) in details
