#ifndef INCLUDE_STACKLOG_DETAIL_SPAN_HPP
#define INCLUDE_STACKLOG_DETAIL_SPAN_HPP

#include <cstddef>
#include <cstdint>

#include <stacklog/core/id.hpp>
#include <stacklog/core/record.hpp>
#include <stacklog/core/source_loc.hpp>

namespace stacklog
{
namespace detail
{

/// 一个操作的全部身份与起止信息。存放在线程操作池里，栈顶是池内索引。
/// 库内部类型：对外交接用 CapturedSpan 这个不透明令牌。
struct Span
{
    ChainId       t_chain;   // 所属链
    SpanId        t_span;    // 本操作操作 id
    SpanId        t_parent;  // 父操作 id，根为全零
    char          t_name[g_k_name_capacity]; // 操作名的内联副本，超长截断
    SourceLoc     t_loc;     // 开操作位置
    std::uint64_t t_ts_steady; // 开始时刻，纳秒
    std::uint64_t t_ts_wall; // 开始时刻，纳秒
};

/// 空栈索引。操作池索引从 0 开始，故空栈不能用 0 表示。
constexpr std::uint32_t g_k_no_span      = 0xFFFFFFFFu;

/// 操作池首次使用时预留的槽位数；超出时按需增长，增长失败该操作不入栈。
constexpr std::size_t   g_k_span_reserve = 128;

} // namespace detail
} // namespace stacklog

#endif // INCLUDE_STACKLOG_DETAIL_SPAN_HPP
