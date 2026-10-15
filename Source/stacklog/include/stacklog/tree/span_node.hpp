#ifndef INCLUDE_STACKLOG_TREE_SPAN_NODE_HPP
#define INCLUDE_STACKLOG_TREE_SPAN_NODE_HPP

#include <cstddef>
#include <string>
#include <vector>

#include <stacklog/core/id.hpp>
#include <stacklog/core/record.hpp>

namespace stacklog
{

/// 挂在操作上的一条事件记录。
struct SpanEvent
{
    std::string   t_message;
    std::uint64_t t_ts_steady; // 纳秒
    Level         t_level;
    std::uint32_t t_code;      // 错误码，非错误事件为 0
};

/// 树上的一棵子树。
///
/// children 是**下标**而不是子节点本身：节点存放在稳定容器里，
/// 按引用连接才不会在连边时把还没接上的子树复制走。
struct SpanNode
{
    SpanId                   t_span;
    SpanId                   t_parent; // 全零表示这是链的根
    std::string              t_name;
    std::uint64_t            t_start; // 开始时刻，纳秒；0 表示没有 enter 记录
    std::uint64_t            t_end; // 结束时刻，纳秒；0 表示没有 exit 记录
    bool                     t_closed; // 是否见过 exit 记录
    std::vector<SpanEvent>   t_events;
    std::vector<std::size_t> t_children;

    SpanNode();
};

/// 操作时长（纳秒）。未闭合或缺少 enter 时返回 0。
std::uint64_t duration_ns(const SpanNode& v_node);

} // namespace stacklog

#endif // INCLUDE_STACKLOG_TREE_SPAN_NODE_HPP
