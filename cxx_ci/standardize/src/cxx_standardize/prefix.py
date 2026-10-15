#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前缀规范化：把"前缀用错"改成"前缀正确"。

clang-tidy 的 identifier-naming 只有一种修复动作——把配置里的 Prefix 拼到名字
前面。它**只会拼、不会剥**，所以遇到"名字上已经挂着别的前缀"时给出的是叠加结果：

    static const char g_digits[]      （静态常量，规范要求 s_）
      clang-tidy 报 invalid case style for static constant 'g_digits'
      clang-tidy 给的修复是 s_g_digits          ← 叠加，是垃圾名
      正确目标是 s_digits

但它**能正确报告**这类问题，而且把每一处出现的位置都指了出来（跨翻译单元也
指得出来）。于是分工很清楚：**位置由 clang-tidy 提供，文本由本模块算**。

算法（对应口径表的用法）：

  1. 由诊断消息取出 kind，从口径表查出该类要求的前缀 P（没有要求就是空串）；
  2. 把 clang-tidy 给出的文本剥掉它自己拼上的前缀，得到"已转好大小写的旧名"；
  3. 再剥掉旧名上那个用错的前缀（已知前缀集合取自口径表的值，不写死）；
  4. 拼上 P 得到目标名。

大小写不自己转：clang-tidy 的文本里已经转好了，剥前缀不影响它。自己写一份
CamelCase/snake_case 转换器只会引入新的出错面（缩写、数字、连续大写都是坑）。
"""
def known_prefixes(config):
    """口径表里出现过的全部前缀（已知前缀集合）。"""
    values = set()
    for prefix in (config.naming_prefix or {}).values():
        text = str(prefix or "")
        if text:
            values.add(text)
    return values


def _strip(text, prefix):
    """剥掉开头的 prefix，剥不掉返回 None。"""
    if prefix and text.startswith(prefix):
        return text[len(prefix):]
    return None


def _strip_any(text, prefixes):
    """剥掉开头任意一个已知前缀（长的优先，避免 `m_` 与 `m_` 之外的短前缀抢）。"""
    for prefix in sorted(prefixes, key=len, reverse=True):
        rest = _strip(text, prefix)
        if rest is not None:
            return rest
    return None


def target_name(kind, tidied, config, declared):
    """算出这个 kind 下正确的名字，算不出来返回空串。

    kind     诊断给出的种类（如 StaticConstant）
    tidied   clang-tidy 给出的替换文本（已转好大小写，但前缀可能叠加）
    declared 配置里该类声明的前缀（clang-tidy 拼上去的就是它）
    """
    wanted = str((config.naming_prefix or {}).get(kind, "") or "")
    body = str(tidied or "")
    if not body:
        return ""
    # 第一步：剥掉 clang-tidy 自己拼上的前缀，拿回"已转好大小写的旧名"
    if declared:
        rest = _strip(body, declared)
        if rest is None:
            # 文本开头不是它该拼的前缀，说明对这份配置的理解有偏差。不猜
            return ""
        body = rest
    # 第二步：剥掉旧名上用错的前缀
    stripped = _strip_any(body, known_prefixes(config))
    if stripped is not None:
        body = stripped
    if not body:
        # 剥完什么都不剩（名字就叫 `m_` 之类），不猜
        return ""
    # 第三步：拼上该类要求的前缀
    return wanted + body


def retarget(records, config, declared):
    """把替换记录里的文本改成正确的目标名。

    records   {(文件, 偏移): (长度, clang-tidy 给的文本, kind)}
    declared  {kind: 配置里声明的前缀}
    返回 (改好的记录, 被拒绝的说明, 被改写过的 (旧文本, 新文本))。
    **算不出目标名的一律拒绝**，不猜。
    """
    fixed, refused, rewritten = {}, [], []
    for key, record in records.items():
        length, text, kind = record[0], record[1], record[2]
        wanted = str((config.naming_prefix or {}).get(kind, "") or "")
        declared_prefix = str(declared.get(kind, "") or "")
        if not kind or (not wanted and not declared_prefix):
            # 这个 kind 不涉及前缀（类型名、方法名等），原样保留
            fixed[key] = (length, text, kind)
            continue
        target = target_name(kind, text, config, declared_prefix)
        if not target:
            refused.append("{}（{}）".format(text, kind))
            continue
        if target != text:
            rewritten.append((text, target))
        fixed[key] = (length, target, kind)
    return fixed, refused, rewritten


def _prefix_of(options, kind):
    return str(options.get(kind + "Prefix", "") or "")


def declared_prefixes(config):
    """从 .clang-tidy 读出每个 kind 声明的前缀。"""
    from . import naming_meta

    options = naming_meta.naming_options(config.clang_tidy_config)
    if options is None:
        return {}
    kinds = set(config.naming_prefix or {})
    # 局部四类在配置里带 `_` 前缀，但口径表没给它们要求（规范要求裸名）。
    # 它们的文本仍要剥前缀，因此也要读出来
    from .tidy import LOCAL_KINDS

    kinds.update(LOCAL_KINDS)
    return {kind: _prefix_of(options, kind) for kind in sorted(kinds)}
