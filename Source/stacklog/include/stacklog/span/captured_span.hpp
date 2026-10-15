#ifndef INCLUDE_STACKLOG_SPAN_CAPTURED_SPAN_HPP
#define INCLUDE_STACKLOG_SPAN_CAPTURED_SPAN_HPP

#include <stacklog/common/compat.hpp>
#include <stacklog/core/id.hpp>
#include <stacklog/core/source_loc.hpp>
#include <stacklog/span/scoped_span.hpp>

namespace stacklog
{

/// 操作的交接令牌：把当前操作交给另一个线程接续。
///
/// 只能由 capture_span() 产生，只能交给 attach_span()；它是一份值快照，
/// 拷贝与移动都安全，取到之后一直有效，不随原线程的栈变化而失效。
/// 没有可写字段，所以拼不出语义上不成立的操作。
/// 只保存链与操作 id，因此不含任何内部类型。
class CapturedSpan
{
public:
    /// 空令牌：接续时退化为普通开始操作。
    CapturedSpan();

    /// 所属链。
    ChainId chain() const;

    /// 本操作 id。
    SpanId  span() const;

private:
    ChainId             m_chain;
    SpanId              m_span;

    friend CapturedSpan capture_span() noexcept;
    friend ScopedSpan   attach_span(const CapturedSpan& v_captured, const char* v_name, const SourceLoc& v_loc);
};

/// 取当前操作的值拷贝，供别的线程接续；空栈时返回空令牌。
///
/// 取到的令牌必须经同步边界交给目标线程（任务闭包按值传递、互斥量或原子量中转），
/// 不得经无同步的全局变量传递。
CapturedSpan                  capture_span() noexcept;

/// 在目标线程接续：以令牌的操作为父新建操作并入栈，链继承令牌的链。
/// 被接续的操作此时可能已经结束，这是合法的。
/// 令牌为空时退化为 start_span()：按目标线程栈的状态决定开链或继承。
STACKLOG_NODISCARD ScopedSpan attach_span(const CapturedSpan& v_captured, const char* v_name, const SourceLoc& v_loc);

} // namespace stacklog

#endif // INCLUDE_STACKLOG_SPAN_CAPTURED_SPAN_HPP
