#ifndef INCLUDE_STACKLOG_SPAN_SCOPED_SPAN_HPP
#define INCLUDE_STACKLOG_SPAN_SCOPED_SPAN_HPP

#include <cstdint>

#include <stacklog/common/compat.hpp>
#include <stacklog/core/id.hpp>
#include <stacklog/core/source_loc.hpp>

namespace stacklog
{

class CapturedSpan;

/// RAII 守卫：构造入栈并写一条 enter 记录，析构写 exit 记录并恢复栈顶。
///
/// 析构恢复构造时保存的前一栈顶，不弹栈顶，因此非后进先出的销毁顺序不会吃掉别人的操作。
/// 不可拷贝；移动后源转为不活动，不得二次出栈。守卫只在开操作线程上生效。
class ScopedSpan
{
public:
    ScopedSpan(const ScopedSpan&)            = delete;
    ScopedSpan& operator=(const ScopedSpan&) = delete;
    ScopedSpan(ScopedSpan&& v_other) noexcept;
    ScopedSpan& operator=(ScopedSpan&& v_other) noexcept;
    ~ScopedSpan() noexcept;

    /// 本操作操作 id；不活动时返回全零。
    SpanId span() const noexcept;

private:
    friend ScopedSpan start_span(const char* v_name, const SourceLoc& v_loc);
    friend ScopedSpan attach_span(const CapturedSpan& v_captured, const char* v_name, const SourceLoc& v_loc);

    ScopedSpan(std::uint32_t v_prev, std::uint32_t v_self, std::uint32_t v_slot) noexcept;
    void          release() noexcept;

    std::uint32_t m_prev;
    std::uint32_t m_self;
    std::uint32_t m_slot;
    bool          m_active;
};

/// 开作用域。链在此开始或继承：本线程栈为空时分配新链，栈非空时继承栈顶的链；
/// 操作池的惰性创建与链的判定无关。enter/exit 记录取 Level::info。
STACKLOG_NODISCARD ScopedSpan start_span(const char* v_name, const SourceLoc& v_loc);

} // namespace stacklog

#endif // INCLUDE_STACKLOG_SPAN_SCOPED_SPAN_HPP
