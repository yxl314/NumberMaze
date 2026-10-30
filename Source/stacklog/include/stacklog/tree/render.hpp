#ifndef INCLUDE_STACKLOG_TREE_RENDER_HPP
#define INCLUDE_STACKLOG_TREE_RENDER_HPP

#include <cstddef>
#include <functional>
#include <iosfwd>
#include <string>

#include <stacklog/tree/span_node.hpp>
#include <stacklog/tree/tree_builder.hpp>

namespace stacklog
{

/// 遍历一棵树时给每个节点的回调：节点、深度、下标。
typedef std::function<void(const SpanNode&, int, std::size_t)> NodeVisitor;

/// 按深度优先遍历全部根节点与孤儿节点。
/// 孤儿先以一行说明列出，再逐棵输出——不静默丢弃。
void                                         walk_tree(const TreeBuilder& v_tree, const NodeVisitor& v_visit);

/// 缩进文本：每层两个空格，节点行带时长与事件。
///
/// 缩进只在**完整拓扑**上可靠。流式边收边缩进会让"层级"与"文本位置"互相矛盾：
/// 跨线程子操作的记录可能晚于父的 exit 到达。这里作用在已连好的树上，不存在该问题。
void                                         render_indent(std::ostream& v_out, const TreeBuilder& v_tree);

/// 嵌套 JSON：每个节点一个对象，children 为子节点数组。
void                                         render_json(std::ostream& v_out, const TreeBuilder& v_tree);

/// 子树过滤器：返回真表示该节点及其子树要输出。
typedef std::function<bool(const SpanNode&)> NodeFilter;

/// 全通过。
bool                                         accept_all(const SpanNode& v_node);

/// 缩进文本，可带过滤：被滤掉的节点连同其子树一起不输出。
void        render_indent_filtered(std::ostream& v_out, const TreeBuilder& v_tree, const NodeFilter& v_filter);

/// 一行摘要，便于工具报告本次解析的数据质量。
/// 例：节点 12，根 1，孤儿 0，未闭合 2，缺开始 0，冲突 0。
std::string summarize(const TreeBuilder& v_tree);

} // namespace stacklog

#endif // INCLUDE_STACKLOG_TREE_RENDER_HPP
