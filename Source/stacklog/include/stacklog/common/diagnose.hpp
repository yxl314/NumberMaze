#ifndef INCLUDE_STACKLOG_COMMON_DIAGNOSE_HPP
#define INCLUDE_STACKLOG_COMMON_DIAGNOSE_HPP

namespace stacklog
{
namespace common
{

/// 向标准错误写一行诊断，带 "stacklog: " 前缀。
///
/// 不经过 sink、不取锁、不分配，因此在初始化失败、排空线程缺席、进程收尾等
/// 任何阶段都可调用：这些阶段恰恰是记录通路本身不可用的时候。
void diagnose(const char* v_message);

/// 同上，并在末尾附上当前 errno 的描述。
void diagnose_errno(const char* v_message);

} // namespace common
} // namespace stacklog

#endif      // INCLUDE_STACKLOG_COMMON_DIAGNOSE_HPP
