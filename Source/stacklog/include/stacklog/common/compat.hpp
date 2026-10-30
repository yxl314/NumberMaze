#ifndef INCLUDE_STACKLOG_COMMON_COMPAT_HPP
#define INCLUDE_STACKLOG_COMMON_COMPAT_HPP

/// 与领域无关的基础设施：编译器差异集中在此，其余代码只用 C++11 标准设施。

#if defined(__cplusplus) && __cplusplus >= 201703L
#define STACKLOG_NODISCARD [[nodiscard]]
#elif defined(__GNUC__) || defined(__clang__)
#define STACKLOG_NODISCARD __attribute__((warn_unused_result))
#else
#define STACKLOG_NODISCARD
#endif

/// 按 printf 规则检查变参：格式串与实参不匹配时给出编译期警告。
/// 参数是格式串所在的位置与首个变参的位置，均从 1 开始。
#if defined(__GNUC__) || defined(__clang__)
#define STACKLOG_PRINTF(format_index, first_argument) __attribute__((format(printf, format_index, first_argument)))
#else
#define STACKLOG_PRINTF(format_index, first_argument)
#endif

#endif // INCLUDE_STACKLOG_COMMON_COMPAT_HPP
