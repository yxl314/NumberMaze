#ifndef INCLUDE_STACKLOG_MACROS_HPP
#define INCLUDE_STACKLOG_MACROS_HPP

#include <stacklog/span/captured_span.hpp>
#include <stacklog/logger.hpp>
#include <stacklog/span/scoped_span.hpp>
#include <stacklog/core/source_loc.hpp>

/// 结构宏用 SL_ 前缀，消息宏用 LOG_ 前缀：两族各管一件事，互不重叠。
///
/// 双层展开：先展开 __LINE__ 再粘贴，C++11 下拼接唯一变量名的标准写法。
#define SL_CONCAT_(a, b) a##b
#define SL_CONCAT(a, b) SL_CONCAT_(a, b)

// 宏续行按可读性手工对齐，不交给排版工具重排。
// clang-format off
/// 开始一个操作，操作名即宏实参。栈空处开出的就是一条新链的根操作。
#define SL_SPAN(name) auto SL_CONCAT(stacklog_guard_, __LINE__) = ::stacklog::start_span((name), SL_HERE)

/// 以 __func__ 为操作名的作用域。
#define SL_FUNCTION() SL_SPAN(__func__)

/// 在目标线程接续被搬运的操作。
#define SL_ATTACH(captured, name)                                                                                      \
    auto SL_CONCAT(stacklog_attach_, __LINE__) = ::stacklog::attach_span((captured), (name), SL_HERE)

/// 先判阈值，再格式化：低于阈值时连格式化都不做。
#define SL_LOGF_(level_value, ...)                                                                                     \
    do                                                                                                                 \
    {                                                                                                                  \
        if (::stacklog::enabled(level_value))                                                                          \
            ::stacklog::logf((level_value), __VA_ARGS__);                                                              \
    }                                                                                                                  \
    while (0)

/// 编译期级别裁剪：低于阈值的消息宏展开成空操作，实参不求值、无符号残留。
///
/// 只作用于消息宏。结构宏（SL_SPAN / SL_FUNCTION / SL_ATTACH）不参与裁剪：
/// enter/exit 是调用链树的节点，剥掉它们会让离线重建的树残缺。
///
/// 阈值取级别名，缺省 debug 即不裁剪。用法：编译时加 -DSTACKLOG_MIN_LEVEL=warn
///
/// 判定靠"按阈值名查表"：每个级别列出各阈值下的去留，宏只做一次拼接。
/// 不在宏里做数值比较——C++11 的预处理器没有比较运算，绕成整数再回填名字
/// 又会因为 ## 抑制展开而失效，直接把阈值名拼进表项名最省事也最可靠。
#ifndef STACKLOG_MIN_LEVEL
#define STACKLOG_MIN_LEVEL debug
#endif

/// 阈值 <= 该级别则保留：阈值为 debug 时四级全留，为 warn 时只留 warn 与 error。
#define SL_KEEP_DEBUG_debug 1
#define SL_KEEP_INFO_debug 1
#define SL_KEEP_INFO_info 1
#define SL_KEEP_WARN_debug 1
#define SL_KEEP_WARN_info 1
#define SL_KEEP_WARN_warn 1
#define SL_KEEP_ERROR_debug 1
#define SL_KEEP_ERROR_info 1
#define SL_KEEP_ERROR_warn 1
#define SL_KEEP_ERROR_error 1

/// 阈值高于该级别时表项缺项，按 0 处理（裁掉），而不是让编译失败。
#ifndef SL_KEEP_DEBUG_info
#define SL_KEEP_DEBUG_info 0
#endif
#ifndef SL_KEEP_DEBUG_warn
#define SL_KEEP_DEBUG_warn 0
#endif
#ifndef SL_KEEP_DEBUG_error
#define SL_KEEP_DEBUG_error 0
#endif
#ifndef SL_KEEP_INFO_warn
#define SL_KEEP_INFO_warn 0
#endif
#ifndef SL_KEEP_INFO_error
#define SL_KEEP_INFO_error 0
#endif
#ifndef SL_KEEP_WARN_error
#define SL_KEEP_WARN_error 0
#endif

/// 保留：正常展开（仍会先判运行期阈值，编译期阈值只做粗筛）。
#define SL_LOGF_IF_1(level_value, ...) SL_LOGF_(level_value, __VA_ARGS__)
/// 裁掉：实参不求值，连格式串常量都不留下。
#define SL_LOGF_IF_0(level_value, ...)                                                                                 \
    do                                                                                                                 \
    {                                                                                                                  \
    }                                                                                                                  \
    while (0)

#define SL_LOGF_TRIMMED_(level_name, level_value, ...)                                                                 \
    SL_CONCAT(SL_LOGF_IF_, SL_CONCAT(SL_KEEP_##level_name##_, STACKLOG_MIN_LEVEL))(level_value, __VA_ARGS__)

#define LOG_DEBUG(...) SL_LOGF_TRIMMED_(DEBUG, ::stacklog::Level::Debug, __VA_ARGS__)
#define LOG_INFO(...) SL_LOGF_TRIMMED_(INFO, ::stacklog::Level::Info, __VA_ARGS__)
#define LOG_WARN(...) SL_LOGF_TRIMMED_(WARN, ::stacklog::Level::Warn, __VA_ARGS__)
#define LOG_ERROR(...) SL_LOGF_TRIMMED_(ERROR, ::stacklog::Level::Error, __VA_ARGS__)
// clang-format on/// 运行时参数检查：条件为假就记一条 error 事件，再按族决定去向。
///
/// 与断言的区别是发布版恒生效——参数非法不该崩进程，也不该被静默放过。
/// 条件原文由宏自动附上，调用方仍可给人话说明。
#define SL_CHECK(condition, ...)                                                                                       \
    do                                                                                                                 \
    {                                                                                                                  \
        if (!(condition))                                                                                              \
            ::stacklog::log_error(0, "条件不成立: " #condition, "" __VA_ARGS__);                                       \
    }                                                                                                                  \
    while (0)

/// 条件为假记 error 事件并返回给定值。
#define SL_CHECK_RET(condition, return_value, ...)                                                                     \
    do                                                                                                                 \
    {                                                                                                                  \
        if (!(condition))                                                                                              \
        {                                                                                                              \
            ::stacklog::log_error(0, "条件不成立: " #condition, "" __VA_ARGS__);                                       \
            return (return_value);                                                                                     \
        }                                                                                                              \
    }                                                                                                                  \
    while (0)

/// 条件为假记 error 事件并返回（用于 void 函数）。
#define SL_CHECK_VOID(condition, ...)                                                                                  \
    do                                                                                                                 \
    {                                                                                                                  \
        if (!(condition))                                                                                              \
        {                                                                                                              \
            ::stacklog::log_error(0, "条件不成立: " #condition, "" __VA_ARGS__);                                       \
            return;                                                                                                    \
        }                                                                                                              \
    }                                                                                                                  \
    while (0)
// clang-format on

#endif // INCLUDE_STACKLOG_MACROS_HPP
