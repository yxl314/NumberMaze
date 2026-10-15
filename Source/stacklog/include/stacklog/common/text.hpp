#ifndef INCLUDE_STACKLOG_COMMON_TEXT_HPP
#define INCLUDE_STACKLOG_COMMON_TEXT_HPP

#include <cstddef>

namespace stacklog
{
namespace common
{

/// 拷贝字符串并截断到目标数组能容纳的长度，结尾补 NUL。
/// source 为空指针时写入空串。数组长度由类型给出，不会越界。
template <std::size_t N> void copy_truncated(char (&v_destination)[N], const char* v_source)
{
    if (N == 0)
    {
        return;
    }
    if (v_source == 0)
    {
        v_destination[0] = '\0';
        return;
    }
    std::size_t length = 0;
    while (length + 1 < N && v_source[length] != '\0')
    {
        ++length;
    }
    for (std::size_t i = 0; i < length; ++i)
    {
        v_destination[i] = v_source[i];
    }
    v_destination[length] = '\0';
}

} // namespace common
} // namespace stacklog

#endif  // INCLUDE_STACKLOG_COMMON_TEXT_HPP
