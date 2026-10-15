#ifndef INCLUDE_STACKLOG_COMMON_TIME_HPP
#define INCLUDE_STACKLOG_COMMON_TIME_HPP

#include <cstddef>
#include <cstdint>

namespace stacklog
{
namespace common
{

/// 单调时钟，纳秒。用于耗时计算，不受系统时间调整影响。
std::uint64_t steady_nanos();

/// 系统时钟，纳秒。用于跨线程、跨进程对齐。
std::uint64_t wall_nanos();

/// 把系统时钟纳秒渲染成本地时间 `YYYY-MM-DDTHH:MM:SS.mmm`。
/// 用 T 分隔日期与时刻，使结果不含空格，可直接作为一个文本列。
///
/// 只用于人读的渲染层，机器可读的定长记录仍走纳秒原值。
/// 时区取本地；没有本地时间接口的平台按 UTC 渲染。
/// 秒为负或超出可表示范围时写入空串，不猜测。
void          format_wall_time(std::uint64_t v_wall_nanos, char* v_out, std::size_t v_capacity);

} // namespace common
} // namespace stacklog

#endif  // INCLUDE_STACKLOG_COMMON_TIME_HPP
