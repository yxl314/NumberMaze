#ifndef INCLUDE_SINGLEPROC_SINGLEPROC_HPP
#define INCLUDE_SINGLEPROC_SINGLEPROC_HPP

/// 伞头：公开面全部经此引入。
///
/// 同一用户、同一台机器内只允许一个实例独占运行；新实例启动时若发现已有实例，
/// 双方可以交换消息，交换之后各自做什么由使用者决定。

#include <singleproc/callback.hpp>
#include <singleproc/guard.hpp>
#include <singleproc/options.hpp>

#endif // INCLUDE_SINGLEPROC_SINGLEPROC_HPP
