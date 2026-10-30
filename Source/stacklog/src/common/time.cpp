#include <stacklog/common/time.hpp>

#include <chrono>
#include <cstdio>
#include <cstring>
#include <ctime>

namespace stacklog
{
namespace common
{

std::uint64_t steady_nanos()
{
    const std::chrono::steady_clock::duration since = std::chrono::steady_clock::now().time_since_epoch();
    return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(since).count());
}

std::uint64_t wall_nanos()
{
    const std::chrono::system_clock::duration since = std::chrono::system_clock::now().time_since_epoch();
    return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(since).count());
}

void format_wall_time(std::uint64_t v_wall_nanos, char* v_out, std::size_t v_capacity)
{
    if (v_out == 0 || v_capacity == 0)
    {
        return;
    }
    v_out[0]                    = '\0';

    const std::time_t   seconds = static_cast<std::time_t>(v_wall_nanos / 1000000000ull);
    const std::uint64_t millis  = (v_wall_nanos % 1000000000ull) / 1000000ull;

    std::tm             parts;
    std::memset(&parts, 0, sizeof(parts));
#if defined(_WIN32)
    if (::localtime_s(&parts, &seconds) != 0)
        return;
#else
    // 本地时区；没有该接口的平台退 UTC，宁可时间对不上时区也不留空。
    if (::localtime_r(&seconds, &parts) == 0)
    {
        return;
    }
#endif

    // 用 T 分隔日期与时刻：文本行按空格分列，列内不能含空格，
    // 否则字段数会随时间漂移，读的人与解析的人都得猜。
    std::snprintf(v_out,
                  v_capacity,
                  "%04d-%02d-%02dT%02d:%02d:%02d.%03u",
                  parts.tm_year + 1900,
                  parts.tm_mon + 1,
                  parts.tm_mday,
                  parts.tm_hour,
                  parts.tm_min,
                  parts.tm_sec,
                  static_cast<unsigned>(millis));
}

} // namespace common
} // namespace stacklog
