#include <stacklog/span/scoped_span.hpp>

#include <stacklog/detail/context.hpp>
#include <stacklog/detail/state.hpp>

namespace stacklog
{

ScopedSpan::ScopedSpan(std::uint32_t v_prev, std::uint32_t v_self, std::uint32_t v_slot) noexcept
    : m_prev(v_prev), m_self(v_self), m_slot(v_slot), m_active(v_self != detail::g_k_no_span)
{}

ScopedSpan::ScopedSpan(ScopedSpan&& v_other) noexcept
    : m_prev(v_other.m_prev), m_self(v_other.m_self), m_slot(v_other.m_slot), m_active(v_other.m_active)
{
    v_other.m_active = false;
}

ScopedSpan& ScopedSpan::operator=(ScopedSpan&& v_other) noexcept
{
    if (this != &v_other)
    {
        release();
        m_prev           = v_other.m_prev;
        m_self           = v_other.m_self;
        m_slot           = v_other.m_slot;
        m_active         = v_other.m_active;
        v_other.m_active = false;
    }
    return *this;
}

ScopedSpan::~ScopedSpan() noexcept
{
    release();
}

void ScopedSpan::release() noexcept
{
    if (!m_active)
    {
        return;
    }
    m_active                       = false;

    detail::ThreadContext& context = detail::ctx();
    if (context.t_dead || context.t_inside_log || context.t_slot != m_slot)
    {
        // 线程上下文已析构、正在排空、或跨线程析构：都不动栈，只计一次丢弃。
        detail::count_drop();
        return;
    }
    detail::emit(Kind::Exit, Level::Info, 0, 0);
    detail::pop_to(m_prev);
}

SpanId ScopedSpan::span() const noexcept
{
    if (!m_active)
    {
        return SpanId{{0, 0}, 0, 0};
    }
    const detail::Span* span = detail::span_at(m_self);
    if (span == 0)
    {
        return SpanId{{0, 0}, 0, 0};
    }
    return span->t_span;
}

ScopedSpan start_span(const char* v_name, const SourceLoc& v_loc)
{
    const detail::Span* parent = detail::top_span();
    ChainId             chain;
    SpanId              parent_span = {{0, 0}, 0, 0};
    if (parent != 0)
    {
        chain       = parent->t_chain;
        parent_span = parent->t_span;
    }
    else
    {
        // 栈空：此处就是一条新链的起点。
        chain.t_origin = detail::process_id();
        chain.t_seq    = detail::next_chain_seq();
    }
    const detail::OpenedSpan opened = detail::open_span(v_name, v_loc, chain, parent_span);
    return ScopedSpan(opened.t_pushed.t_prev, opened.t_pushed.t_self, opened.t_slot);
}

} // namespace stacklog
