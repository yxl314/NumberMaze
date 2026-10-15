#include <stacklog/detail/context.hpp>

#include <atomic>
#include <cstring>
#include <new>

#include <stacklog/common/text.hpp>
#include <stacklog/common/time.hpp>
#include <stacklog/detail/state.hpp>

namespace stacklog
{
namespace detail
{
namespace
{

/// 本线程环形缓冲，首次记录时分配。分配失败返回 0。
Ring* ensure_ring(ThreadContext& v_context)
{
    if (v_context.t_ring != 0)
    {
        return v_context.t_ring;
    }
    Ring* ring = 0;
    try
    {
        ring = new Ring(ring_capacity());
    }
    catch (const std::bad_alloc&)
    {
        return 0;
    }
    v_context.t_ring = ring;
    register_ring(ring);
    return ring;
}

/// 填身份、归属与时间戳；message 由调用方填。
void fill_record(Record& v_record, const Span* v_span, ThreadContext& v_context, Kind v_kind, Level v_level)
{
    std::memset(&v_record, 0, sizeof(v_record));
    if (v_span != 0)
    {
        v_record.t_chain  = v_span->t_chain;
        v_record.t_span   = v_span->t_span;
        v_record.t_parent = v_span->t_parent;
        common::copy_truncated(v_record.t_name, v_span->t_name);
        v_record.t_loc = v_span->t_loc;
    }
    else
    {
        v_record.t_name[0]        = '\0';
        v_record.t_loc.t_file     = "";
        v_record.t_loc.t_line     = 0;
        v_record.t_loc.t_function = "";
    }
    v_record.t_emitter    = process_id();
    v_record.t_slot       = v_context.t_slot;
    v_record.t_seq        = v_context.t_rec_seq;
    v_record.t_ts_steady  = common::steady_nanos();
    v_record.t_ts_wall    = common::wall_nanos();
    v_record.t_level      = v_level;
    v_record.t_kind       = v_kind;
    v_record.t_len        = 0;
    v_record.t_message[0] = '\0';
}

} // namespace

ThreadContext::ThreadContext()
    : t_ring(0), t_top(g_k_no_span), t_slot(0), t_span_seq(0), t_rec_seq(0), t_inside_log(false), t_dead(false)
{
    try
    {
        t_spans.reserve(g_k_span_reserve);
    }
    catch (const std::bad_alloc&)
    {
        // 预留失败不影响使用：操作池按需增长，增长失败时 push_span 记溢出。
    }
}

ThreadContext::~ThreadContext()
{
    t_dead = true;
    if (t_ring != 0)
    {
        release_ring(t_ring);
        t_ring = 0;
    }
}

ThreadContext& ctx()
{
    static thread_local ThreadContext s_context;
    return s_context;
}

std::uint32_t ensure_thread_slot()
{
    ThreadContext& context = ctx();
    if (context.t_slot == 0)
    {
        context.t_slot = alloc_thread_slot();
    }
    return context.t_slot;
}

PushResult push_span(const Span& v_span)
{
    ThreadContext& context = ctx();
    PushResult     result;
    result.t_prev = context.t_top;
    result.t_self = g_k_no_span;
    try
    {
        context.t_spans.push_back(v_span);
    }
    catch (const std::bad_alloc&)
    {
        // 操作池增长失败：该操作不入栈，其记录随之丢失，计一次丢弃。
        count_drop();
        return result;
    }
    result.t_self = static_cast<std::uint32_t>(context.t_spans.size() - 1);
    context.t_top = result.t_self;
    return result;
}

void pop_to(std::uint32_t v_prev)
{
    ctx().t_top = v_prev;
}

const Span* span_at(std::uint32_t v_index)
{
    ThreadContext& context = ctx();
    if (v_index == g_k_no_span || v_index >= context.t_spans.size())
    {
        return 0;
    }
    return &context.t_spans[v_index];
}

const Span* top_span()
{
    ThreadContext& context = ctx();
    if (context.t_top == g_k_no_span || context.t_top >= context.t_spans.size())
    {
        return 0;
    }
    return &context.t_spans[context.t_top];
}

bool level_enabled(Level v_level)
{
    if (!alive())
    {
        return false;
    }
    return static_cast<int>(v_level) >= static_cast<int>(min_level());
}

Record* begin_record(Kind v_kind, Level v_level)
{
    ThreadContext& context = ctx();
    if (context.t_dead || context.t_inside_log)
    {
        count_drop();
        return 0;
    }
    // 级别只过滤事件：结构记录是调用链树的节点，被过滤掉会让树消失，
    // 并让留下的事件带上无法解析的父引用。
    if (v_kind == Kind::Event && !level_enabled(v_level))
    {
        return 0;
    }
    Ring* ring = ensure_ring(context);
    if (ring == 0)
    {
        count_drop();
        return 0;
    }
    Record* slot = ring->reserve();
    if (slot == 0)
    {
        count_drop();
        return 0;
    }
    fill_record(*slot, top_span(), context, v_kind, v_level);
    return slot;
}

void commit_record(Kind v_kind, Level v_level)
{
    ThreadContext& context = ctx();
    if (context.t_ring == 0)
    {
        return;
    }
    context.t_ring->commit();
    ++context.t_rec_seq;

    // 刷盘策略只影响"何时排空"，都发生在提交之后，因此不改生产路径的无锁特性。
    // 结构记录不参与：它们数量固定且不由级别决定，为它们唤醒没有意义。
    if (v_kind != Kind::Event)
    {
        return;
    }
    if (should_flush_for(v_level))
    {
        wake_flusher();
    }
    if (sync_mode_enabled())
    {
        drain_once();
    }
}

void emit(Kind v_kind, Level v_level, const char* v_message, std::size_t v_len)
{
    Record* slot = begin_record(v_kind, v_level);
    if (slot == 0)
    {
        return;
    }
    std::size_t length = v_len;
    if (v_message == 0)
    {
        length = 0;
    }
    if (length >= g_k_message_capacity)
    {
        length = g_k_message_capacity - 1;
    }
    if (length > 0)
    {
        std::memcpy(slot->t_message, v_message, length);
    }
    slot->t_message[length] = '\0';
    slot->t_len             = static_cast<std::uint16_t>(length);
    commit_record(v_kind, v_level);
}

OpenedSpan open_span(const char* v_name, const SourceLoc& v_loc, const ChainId& v_chain, const SpanId& v_parent)
{
    ensure_initialized();
    const ProcessId     origin = process_id();
    const std::uint32_t slot   = ensure_thread_slot();

    Span                span;
    span.t_chain         = v_chain;
    span.t_span.t_origin = origin;
    span.t_span.t_slot   = slot;
    span.t_span.t_seq    = ctx().t_span_seq++;
    span.t_parent        = v_parent;
    common::copy_truncated(span.t_name, v_name);
    span.t_loc       = v_loc;
    span.t_ts_steady = common::steady_nanos();
    span.t_ts_wall   = common::wall_nanos();

    OpenedSpan opened;
    opened.t_pushed = push_span(span);
    opened.t_slot   = slot;
    if (opened.t_pushed.t_self != g_k_no_span)
    {
        emit(Kind::Enter, Level::Info, 0, 0);
    }
    return opened;
}

} // namespace detail
} // namespace stacklog
