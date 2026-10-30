#include <stacklog/common/diagnose.hpp>
#include <stacklog/detail/state.hpp>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <mutex>
#include <random>
#include <vector>
#include <thread>

#include <stacklog/detail/context.hpp>

namespace stacklog
{
namespace detail
{
namespace
{

// 进程级静态状态：全部为 POD 或原子量，避免静态初始化与销毁顺序问题。
std::atomic<std::uint32_t> g_next_slot(1);
std::atomic<std::uint64_t> g_chain_seq(1);
std::atomic<std::uint64_t> g_dropped(0);
std::atomic<bool>          g_alive(true);
std::atomic<bool>          g_ready(false);
std::atomic<bool>          g_fork_pending(false);
std::atomic<Sink*>         g_sink(0);
std::atomic<int>           g_level(static_cast<int>(Level::Info));
std::atomic<int>           g_flush_on_level(-1); // 负值表示未启用
std::atomic<bool>          g_sync_mode(false);
std::atomic<std::size_t>   g_ring_capacity(256);
std::atomic<std::size_t>   g_flush_interval(100);

ProcessId                  g_process_id = {0, 0};
std::mutex                 g_init_mutex;
Sink*                      g_owned_sink = 0;

void                       flusher_loop();

/// 排空线程与它的唤醒件。
///
/// 与注册表一样按"永不析构的堆单例"持有：进程退出阶段仍要能用，且 fork 之后的子进程
/// 只能整体弃置它。继承来的条件变量可能仍有等待者，而那个线程在子进程里并不存在，
/// 销毁它会阻塞，故子进程一律换新对象，绝不触碰旧对象。
struct FlusherState
{
    std::mutex              t_mutex;
    std::condition_variable t_cv;
    std::thread*            t_thread;
    bool                    t_stop;
    bool                    t_pending; // 已请求立即排空一次（闩锁，避免通知丢失）
    std::size_t             t_interval_ms;

    FlusherState(): t_thread(0), t_stop(false), t_pending(false), t_interval_ms(100) {}

private:
    FlusherState(const FlusherState&)            = delete;
    FlusherState& operator=(const FlusherState&) = delete;
};

FlusherState*& flusher_slot()
{
    static FlusherState* s_state = new FlusherState();
    return s_state;
}

FlusherState& flusher()
{
    return *flusher_slot();
}

void flusher_loop()
{
    FlusherState& state = flusher();
    for (;;)
    {
        std::unique_lock<std::mutex> lock(state.t_mutex);
        if (state.t_stop)
        {
            return;
        }
        if (!state.t_pending)
        {
            const std::size_t interval = state.t_interval_ms == 0 ? 100 : state.t_interval_ms;
            // 用带谓词的等待：请求可能在进入等待之前就到达，
            // 只看返回值会把那次通知丢掉。
            state.t_cv.wait_for(
                lock, std::chrono::milliseconds(interval), [&state] { return state.t_pending || state.t_stop; });
        }
        if (state.t_stop)
        {
            return;
        }
        state.t_pending = false;
        lock.unlock();
        drain_once();
    }
}

void init_nonce()
{
    std::random_device device;
    std::uint64_t      mixed = (static_cast<std::uint64_t>(device()) << 32) ^ static_cast<std::uint64_t>(device());
    mixed ^= static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch())
            .count());
    mixed ^= static_cast<std::uint64_t>(current_pid()) << 17;
    std::uint32_t nonce = static_cast<std::uint32_t>(mixed ^ (mixed >> 32));
    if (nonce == 0)
    {
        nonce = 0xA5A5A5A5u;
    }
    g_process_id.t_nonce = nonce;
}

/// atexit 兜底：排空、停排空线程、再排一次。
void exit_flush()
{
    if (!alive())
    {
        return;
    }
    drain_once();
    stop_flusher();
    drain_once();
}

} // namespace

const ProcessId& process_id()
{
    ensure_initialized();
    return g_process_id;
}

std::uint64_t next_chain_seq()
{
    return g_chain_seq.fetch_add(1, std::memory_order_relaxed);
}

std::uint32_t alloc_thread_slot()
{
    return g_next_slot.fetch_add(1, std::memory_order_relaxed);
}

Sink* configured_sink()
{
    return g_sink.load(std::memory_order_acquire);
}

void set_configured_sink(Sink* v_sink, bool v_owned)
{
    std::lock_guard<std::mutex> lock(state_mutex());
    Sink*                       previous_owned = g_owned_sink;
    Sink*                       previous       = g_sink.load(std::memory_order_relaxed);
    if (previous != 0 && previous != v_sink)
    {
        previous->set_destroy_hook(0);
    }
    if (v_sink != 0)
    {
        v_sink->set_destroy_hook(&forget_sink);
    }
    g_sink.store(v_sink, std::memory_order_release);
    g_owned_sink = v_owned ? v_sink : 0;
    if (previous_owned != 0 && previous_owned != v_sink)
    {
        delete previous_owned;
    }
}

void forget_sink(Sink* v_sink)
{
    // 与排空共用同一把锁：拿到锁就说明没有一轮排空正在使用这个 sink。
    std::lock_guard<std::mutex> lock(state_mutex());
    if (g_sink.load(std::memory_order_relaxed) != v_sink)
    {
        return;
    }
    g_sink.store(0, std::memory_order_release);
    g_owned_sink = 0;
    common::diagnose("sink 已析构：回退到默认 sink，重新登记前记录写标准错误");
}

Level min_level()
{
    return static_cast<Level>(g_level.load(std::memory_order_relaxed));
}

void set_min_level(Level v_level)
{
    g_level.store(static_cast<int>(v_level), std::memory_order_relaxed);
}

std::size_t ring_capacity()
{
    return g_ring_capacity.load(std::memory_order_relaxed);
}

void set_ring_capacity(std::size_t v_records)
{
    if (v_records > 0)
    {
        g_ring_capacity.store(v_records, std::memory_order_relaxed);
    }
}

void set_flush_interval_ms(std::size_t v_ms)
{
    g_flush_interval.store(v_ms, std::memory_order_relaxed);
}

bool alive()
{
    return g_alive.load(std::memory_order_acquire);
}

void mark_dead()
{
    g_alive.store(false, std::memory_order_release);
}

void count_drop()
{
    g_dropped.fetch_add(1, std::memory_order_relaxed);
}

std::uint64_t dropped_count()
{
    return g_dropped.load(std::memory_order_relaxed);
}

/// 首次进入时的惰性初始化；已就绪时只有两次原子读。
void ensure_initialized() noexcept
{
    if (g_ready.load(std::memory_order_acquire) && !g_fork_pending.load(std::memory_order_acquire))
    {
        return;
    }
    try
    {
        std::lock_guard<std::mutex> lock(g_init_mutex);
        if (g_fork_pending.exchange(false, std::memory_order_acq_rel))
        {
            reset_after_fork();
        }
        if (!g_ready.load(std::memory_order_relaxed))
        {
            g_process_id.t_pid = current_pid();
            init_nonce();
            install_fork_guard();
            std::atexit(&exit_flush);
            start_flusher(g_flush_interval.load(std::memory_order_relaxed));
            g_ready.store(true, std::memory_order_release);
        }
    }
    catch (...)
    {
        // 初始化失败不阻断记录：退化为无排空线程，仍可排队与手动 flush。
        // 这条路从此走的是"只手动 flush"，不说明白会让使用者以为后台一直在排空。
        static std::atomic<bool> s_reported(false);
        if (!s_reported.exchange(true, std::memory_order_relaxed))
        {
            common::diagnose("初始化失败：后台排空与进程钩子未启用，记录只在手动 flush 时写出");
        }
    }
}

void note_fork_child()
{
    g_fork_pending.store(true, std::memory_order_release);
}

void reset_after_fork()
{
    // 子进程里：换 nonce，弃继承的注册表、排空状态与本线程环形缓冲。
    // 继承的互斥量与条件变量不销毁也不复用：它们可能仍被不存在的线程持有。
    g_process_id.t_pid = current_pid();
    init_nonce();
    ctx().t_ring = 0;
    reset_registry_after_fork();
    flusher_slot() = new FlusherState();
    start_flusher(g_flush_interval.load(std::memory_order_relaxed));
}

void start_flusher(std::size_t v_interval_ms)
{
    FlusherState&               state = flusher();
    std::lock_guard<std::mutex> lock(state.t_mutex);
    state.t_interval_ms = v_interval_ms;
    if (v_interval_ms == 0 || state.t_thread != 0)
    {
        return;
    }
    state.t_stop = false;
    try
    {
        state.t_thread = new std::thread(&flusher_loop);
    }
    catch (...)
    {
        state.t_thread = 0;
        static std::atomic<bool> s_reported(false);
        if (!s_reported.exchange(true, std::memory_order_relaxed))
        {
            common::diagnose("后台排空线程创建失败：记录只在手动 flush 时写出");
        }
    }
}

void set_flush_on_level(Level v_level, bool v_enabled)
{
    g_flush_on_level.store(v_enabled ? static_cast<int>(v_level) : -1, std::memory_order_relaxed);
}

bool should_flush_for(Level v_level)
{
    const int threshold = g_flush_on_level.load(std::memory_order_relaxed);
    if (threshold < 0)
    {
        return false;
    }
    return static_cast<int>(v_level) >= threshold;
}

void set_sync_mode(bool v_enabled)
{
    g_sync_mode.store(v_enabled, std::memory_order_relaxed);
}

bool sync_mode_enabled()
{
    return g_sync_mode.load(std::memory_order_relaxed);
}

void wake_flusher()
{
    // 置闩锁再通知：排空线程可能正处在两次等待之间，只发通知会丢。
    // 只唤醒，不在生产线程里排空，因此热路径不因它阻塞在 sink 上。
    FlusherState& state = flusher();
    {
        std::lock_guard<std::mutex> lock(state.t_mutex);
        state.t_pending = true;
    }
    state.t_cv.notify_all();
}

void stop_flusher()
{
    FlusherState& state  = flusher();
    std::thread*  thread = 0;
    {
        std::lock_guard<std::mutex> lock(state.t_mutex);
        state.t_stop   = true;
        thread         = state.t_thread;
        state.t_thread = 0;
    }
    state.t_cv.notify_all();
    if (thread != 0)
    {
        if (thread->joinable())
        {
            thread->join();
        }
        delete thread;
    }
}

void shutdown_process() noexcept
{
    mark_dead();
    stop_flusher();
    drain_once();
    {
        std::lock_guard<std::mutex> lock(state_mutex());
        if (g_owned_sink != 0)
        {
            g_owned_sink->set_destroy_hook(0);
            delete g_owned_sink;
            g_owned_sink = 0;
            g_sink.store(0, std::memory_order_release);
        }
    }
}

// ---- 生产者注册表与排空（声明见 detail/state.hpp）----

namespace
{

struct Registry
{
    std::mutex         t_mutex;
    std::vector<Ring*> t_rings;
};

/// 永不析构的堆单例：排空与 sink 替换在进程退出阶段仍要能用，fork 之后的子进程
/// 只能整体弃置它（继承来的互斥量可能仍被不存在的线程持有）。
Registry*& registry_slot()
{
    static Registry* s_instance = new Registry();
    return s_instance;
}

Registry& registry()
{
    return *registry_slot();
}

} // namespace

std::mutex& state_mutex()
{
    return registry().t_mutex;
}

void reset_registry_after_fork()
{
    registry_slot() = new Registry();
}

void register_ring(Ring* v_ring)
{
    std::lock_guard<std::mutex> lock(registry().t_mutex);
    registry().t_rings.push_back(v_ring);
}

void release_ring(Ring* v_ring)
{
    // 内存由排空路径在取空后释放。
    v_ring->mark_dead();
}

bool drain_once()
{
    // 已在排空中：sink 内部再触发排空属于误用，直接返回，避免自锁。
    if (ctx().t_inside_log)
    {
        return true;
    }

    std::lock_guard<std::mutex> lock(registry().t_mutex);

    Sink*                       sink = configured_sink();
    if (sink == 0)
    {
        sink = &default_sink();
    }

    // 排空期间禁止记录：sink 内部再调用记录接口属于误用，直接丢弃。
    ThreadContext& context         = ctx();
    const bool     previous_inside = context.t_inside_log;
    context.t_inside_log           = true;

    std::vector<Ring*>& rings      = registry().t_rings;
    Record              record;
    bool                failed = false;
    for (std::size_t i = 0; i < rings.size();)
    {
        Ring* ring = rings[i];
        try
        {
            while (ring->pop(record))
            {
                sink->write(record);
            }
            sink->flush();
        }
        catch (...)
        {
            // sink 抛异常不得影响其他缓冲与调用方；本条记录已经取出，只能计丢弃。
            failed = true;
            count_drop();
            static std::atomic<bool> s_reported(false);
            if (!s_reported.exchange(true, std::memory_order_relaxed))
            {
                common::diagnose("sink 抛出异常：本轮及后续写出的记录可能丢失");
            }
        }
        if (ring->dead() && ring->drained())
        {
            delete ring;
            rings[i] = rings.back();
            rings.pop_back();
            continue;
        }
        ++i;
    }

    context.t_inside_log = previous_inside;
    return !failed;
}

} // namespace detail
} // namespace stacklog
