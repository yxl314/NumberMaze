#ifndef INCLUDE_STACKLOG_DETAIL_STATE_HPP
#define INCLUDE_STACKLOG_DETAIL_STATE_HPP

#include <cstddef>
#include <cstdint>
#include <mutex>

#include <stacklog/core/id.hpp>
#include <stacklog/core/record.hpp>
#include <stacklog/detail/ring.hpp>
#include <stacklog/io/sink.hpp>

namespace stacklog
{
namespace detail
{

// ---- 进程身份与计数器 ----

/// 本进程实例身份：pid 取自系统，nonce 首次使用时定值，fork 后由子进程重新生成。
const ProcessId& process_id();

/// 开链序号，进程内单调递增。
std::uint64_t    next_chain_seq();

/// 线程槽，进程内单调分配，永不复用，从 1 开始。
std::uint32_t    alloc_thread_slot();

// ---- 配置 ----

/// 当前 sink；0 表示使用内置 sink。
Sink*            configured_sink();

/// 替换 sink。owned 为 true 时旧的内置或已接管的 sink 由库释放。
void             set_configured_sink(Sink* v_sink, bool v_owned);

Level            min_level();
void             set_min_level(Level v_level);

std::size_t      ring_capacity();
void             set_ring_capacity(std::size_t v_records);

void             set_flush_interval_ms(std::size_t v_ms);

/// 达到或超过该级别的事件记录立即触发一次排空。默认关闭。
/// 只影响"何时排空"，不改无锁生产路径。
void             set_flush_on_level(Level v_level, bool v_enabled);

/// 是否应立即排空该级别的记录。
bool             should_flush_for(Level v_level);

/// 同步模式：记录写入后立刻排空本线程的环。
/// 仅用于排障，吞吐会明显下降。
void             set_sync_mode(bool v_enabled);

bool             sync_mode_enabled();

/// 唤醒后台排空线程立即排空一次。只唤醒，不在调用线程里排空。
void             wake_flusher();

/// 内置 sink：写标准错误。
Sink&            default_sink();

// ---- 生命周期 ----

/// 库是否仍在服务。进程退出后为 false，此后一切记录调用为无害丢弃。
bool             alive();
void             mark_dead();

/// 丢弃计数：缓冲满、关闭后调用、重入、线程上下文已析构。
void             count_drop();
std::uint64_t    dropped_count();

/// 首次使用时完成：装 atexit 兜底、装 fork 适配、按需启动排空线程。
/// 每次公共入口都会调用，就绪后只剩两次原子读。
void             ensure_initialized() noexcept;

/// 平台层：当前进程号；无进程概念的平台上返回 0。
std::uint32_t    current_pid();

/// 平台层：装 fork 适配；平台没有该接口时为空实现。
void             install_fork_guard();

/// 关闭：标记不再服务、停排空线程、排空、释放库持有的 sink。幂等。
void             shutdown_process() noexcept;

/// fork 的子侧处理器里调用：只写 POD 与原子量，真正的重建推迟到子进程下次进入本库。
void             note_fork_child();

// ---- 环形缓冲注册表与排空 ----

/// 排空与 sink 替换共用的串行点。
std::mutex&      state_mutex();

void             register_ring(Ring* v_ring);

/// 线程退出时调用：标记缓冲待回收，内存由排空路径释放。
void             release_ring(Ring* v_ring);

/// fork 之后的子进程用：整体换一份注册表与它的锁，不触碰继承的锁。
void             reset_registry_after_fork();

/// 排空全部注册的环形缓冲并 flush sink。多个调用方之间串行。
/// 排空一次所有线程缓冲。返回是否全部成功。
bool             drain_once();

/// 摘掉一个正在析构的 sink。若它正是当前配置的 sink，回退到默认 sink 并给出一行诊断。
/// 会先等当前一轮排空结束，因此不会再调用已经析构的对象。
void             forget_sink(Sink* v_sink);

void             start_flusher(std::size_t v_interval_ms);
void             stop_flusher();

/// 子进程首次进入时重建：换 nonce、弃继承的环形缓冲、重建锁与排空线程。
void             reset_after_fork();

} // namespace detail
} // namespace stacklog

#endif // INCLUDE_STACKLOG_DETAIL_STATE_HPP
