#include <stacklog/span/captured_span.hpp>

#include <stacklog/detail/context.hpp>

namespace stacklog
{

CapturedSpan::CapturedSpan()
{
    m_chain.t_origin.t_pid   = 0;
    m_chain.t_origin.t_nonce = 0;
    m_chain.t_seq            = 0;
    m_span.t_origin.t_pid    = 0;
    m_span.t_origin.t_nonce  = 0;
    m_span.t_slot            = 0;
    m_span.t_seq             = 0;
}

ChainId CapturedSpan::chain() const
{
    return m_chain;
}

SpanId CapturedSpan::span() const
{
    return m_span;
}

CapturedSpan capture_span() noexcept
{
    CapturedSpan        captured;
    const detail::Span* top = detail::top_span();
    if (top != 0)
    {
        captured.m_chain = top->t_chain;
        captured.m_span  = top->t_span;
    }
    return captured;
}

ScopedSpan attach_span(const CapturedSpan& v_captured, const char* v_name, const SourceLoc& v_loc)
{
    // 没有可接续的操作时退化为普通开始操作：按本线程栈的状态决定开链或继承。
    if (is_null(v_captured.m_chain))
    {
        return start_span(v_name, v_loc);
    }
    const detail::OpenedSpan opened = detail::open_span(v_name, v_loc, v_captured.m_chain, v_captured.m_span);
    return ScopedSpan(opened.t_pushed.t_prev, opened.t_pushed.t_self, opened.t_slot);
}

} // namespace stacklog
