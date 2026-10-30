#ifndef INCLUDE_STACKLOG_STACKLOG_HPP
#define INCLUDE_STACKLOG_STACKLOG_HPP

/// 伞头：公开面全部经此引入。
///
/// 零配置即可用：不调用 init() 也能开始操作与记录，默认输出到标准错误。
/// 需要定制时用 set_level() / log_to_file() / use_sink()。

#include <stacklog/core/core.hpp>
#include <stacklog/io/io.hpp>
#include <stacklog/span/span.hpp>

#include <stacklog/logger.hpp>
#include <stacklog/macros.hpp>

#endif // INCLUDE_STACKLOG_STACKLOG_HPP
