#ifndef INCLUDE_STACKLOG_CORE_SOURCE_LOC_HPP
#define INCLUDE_STACKLOG_CORE_SOURCE_LOC_HPP

namespace stacklog
{

/// 开操作位置三要素。所指字符串须为静态字面量：记录只存指针，不复制内容。
struct SourceLoc
{
    const char* t_file;
    int         t_line;
    const char* t_function;
};

} // namespace stacklog

// 宏续行按可读性手工对齐。
// clang-format off
/// 在调用点捕获开操作位置。
#define SL_HERE                                                                                                        \
    ::stacklog::SourceLoc                                                                                              \
    {                                                                                                                  \
        __FILE__, __LINE__, __func__                                                                                   \
    }
// clang-format on

#endif // INCLUDE_STACKLOG_CORE_SOURCE_LOC_HPP
