#include <stacklog/common/diagnose.hpp>
#include <stacklog/detail/state.hpp>

#if defined(__unix__) || defined(__APPLE__)
#include <pthread.h>
#include <unistd.h>
#define STACKLOG_POSIX_FORK 1
#endif

#if defined(STACKLOG_POSIX_FORK)
#include <mutex>
#endif

namespace stacklog
{
namespace detail
{

std::uint32_t current_pid()
{
#if defined(STACKLOG_POSIX_FORK)
    return static_cast<std::uint32_t>(::getpid());
#else
    return 0;
#endif
}

#if defined(STACKLOG_POSIX_FORK)
namespace
{

/// fork 前把 sink 缓冲写出去，减少子进程继承的重复输出。
/// 处理器在调用线程的普通上下文里运行，取锁后即刻释放，不跨 fork 持有。
void on_fork_prepare()
{
    std::lock_guard<std::mutex> lock(state_mutex());
    Sink*                       sink = configured_sink();
    if (sink == 0)
    {
        sink = &default_sink();
    }
    try
    {
        sink->flush();
    }
    catch (...)
    {
        common::diagnose("进程收尾排空时 sink 抛出异常：最后一批记录可能丢失");
    }
}

void on_fork_parent() {}

/// 子侧处理器只写一个标志：重建锁、缓冲与排空线程推迟到子进程下次进入本库。
/// 处理器里不加锁、不分配、不销毁，避免碰到可能被其他线程持有的状态。
void on_fork_child()
{
    note_fork_child();
}

bool g_installed = false;

} // namespace

void install_fork_guard()
{
    if (g_installed)
    {
        return;
    }
    g_installed = true;
    ::pthread_atfork(&on_fork_prepare, &on_fork_parent, &on_fork_child);
}

#else

void install_fork_guard() {}

#endif

} // namespace detail
} // namespace stacklog
