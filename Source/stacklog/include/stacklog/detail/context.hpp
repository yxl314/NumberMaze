#ifndef INCLUDE_STACKLOG_DETAIL_CONTEXT_HPP
#define INCLUDE_STACKLOG_DETAIL_CONTEXT_HPP

#include <cstddef>
#include <cstdint>
#include <vector>

#include <stacklog/detail/span.hpp>
#include <stacklog/core/record.hpp>
#include <stacklog/detail/ring.hpp>

namespace stacklog
{
namespace detail
{

/// 线程上下文：操作池、栈顶、序号、环形缓冲、重入标记。随线程退出析构。
struct ThreadContext
{
    ThreadContext();
    ~ThreadContext();

    std::vector<Span> t_spans; // 操作池，索引即操作句柄
    Ring*             t_ring; // 首次记录时分配，线程退出后交给排空机制回收
    std::uint32_t     t_top; // 栈顶索引，kNoSpan 表示空栈
    std::uint32_t     t_slot; // 线程槽，0 表示未分配
    std::uint64_t     t_span_seq; // 线程内操作序号
    std::uint64_t     t_rec_seq; // 线程内记录序号
    bool              t_inside_log; // 重入标记
    bool              t_dead; // 线程上下文已析构

private:
    ThreadContext(const ThreadContext&)            = delete;
    ThreadContext& operator=(const ThreadContext&) = delete;
};

/// 本线程上下文。函数局部线程局部静态，C++11 起保证线程安全的惰性构造。
ThreadContext& ctx();

/// 本线程的槽号，首次调用时分配，此后不变。
std::uint32_t  ensure_thread_slot();

/// 入栈结果：prev 为入栈前的栈顶，self 为新操作索引（失败时为 kNoSpan）。
struct PushResult
{
    std::uint32_t t_prev;
    std::uint32_t t_self;
};

/// 把操作压入本线程操作池，失败（增长不出内存）时 self 为 kNoSpan 并累加溢出计数。
PushResult push_span(const Span& v_span);

/// 恢复栈顶到指定索引。不弹栈顶，只回退到调用方自己的父操作。
void       pop_to(std::uint32_t v_prev);

/// 压操作结果：守卫需要的三个字段。
struct OpenedSpan
{
    PushResult    t_pushed;
    std::uint32_t t_slot;
};

/// 组装一个操作并压入本线程操作池，随后写一条 enter 记录。
/// 链与父引用由调用方给定：开新链时链号由 process_id() 与 next_chain_seq() 现取，
/// 父引用为全零；继承时取栈顶操作的链与操作 id。
OpenedSpan  open_span(const char* v_name, const SourceLoc& v_loc, const ChainId& v_chain, const SpanId& v_parent);

/// 操作池内指定索引的操作；索引无效返回 0。
const Span* span_at(std::uint32_t v_index);

/// 当前栈顶操作；空栈返回 0。
const Span* top_span();

/// 级别阈值判定：阈值可运行中替换。
bool        level_enabled(Level v_level);

/// 写一条记录，归属当前栈顶操作；栈空时写出无操作记录。
void        emit(Kind v_kind, Level v_level, const char* v_message, std::size_t v_len);

/// 预留一条记录并填好身份与时间，调用方只填 message 与 len；失败（被阈值过滤或缓冲满）
/// 返回 0，此时不得调用 commit_record。
Record*     begin_record(Kind v_kind, Level v_level);

/// 发布 begin_record 预留的记录。
void        commit_record(Kind v_kind, Level v_level);

} // namespace detail
} // namespace stacklog

#endif // INCLUDE_STACKLOG_DETAIL_CONTEXT_HPP
