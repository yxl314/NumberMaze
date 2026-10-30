#ifndef INCLUDE_STACKLOG_LOGGER_HPP
#define INCLUDE_STACKLOG_LOGGER_HPP

#include <stacklog/common/compat.hpp>
#include <stacklog/core/id.hpp>
#include <stacklog/core/record.hpp>
#include <stacklog/io/sink.hpp>

namespace stacklog
{

// ---- 生命周期：都可选，不调用也能用 ----

/// 停止排空线程、排空全部缓冲、落盘。之后一切记录调用为无害丢弃。
void        shutdown() noexcept;

/// 手动排空一次。返回是否全部成功：sink 抛出异常时为 false。
/// 自定义 sink 自行吞掉写错误时库看不到，那种情况由 sink 负责上报。
bool        flush();

// ---- 配置：随时可调，互不覆盖 ----

/// 借用调用方提供的 sink：库只持有引用，不接管生存期，
/// 因此它必须活过使用期（包括进程退出时的那一次兜底排空）。
void        use_sink(Sink& v_sink);

/// 由库建立文件 sink 并接管它，因此调用方不需要维护任何对象的生存期。
/// 父目录不存在时会自动逐级创建。返回是否真正可写；失败时输出保持原样，
/// 不改变已有配置。
bool        log_to_file(const char* v_path);

/// 由库建立带轮转的文件 sink 并接管它。超过 max_bytes 即归档为 .1 … .N，
/// 保留 max_files 份，磁盘占用有界。父目录不存在时自动创建。
/// 归档失败不影响写入：继续追加现行文件，只提示一次。
bool        log_to_file(const char* v_path, const RotatingOptions& v_options);

/// 默认日志文件路径：<状态目录>/<应用名>/<应用名>.log。
///
/// 状态目录按三级回退：XDG_STATE_HOME（须为绝对路径）→ $HOME/.local/state
/// → 当前目录。只返回路径，不写盘；配合 log_to_file 的自动建目录即可零配置落盘。
std::string default_log_path(const char* v_application);

/// 事件记录的级别阈值。结构记录（enter/exit）不受它影响：那些记录是调用链树的
/// 节点，一旦被过滤，树就消失，留下的事件也会带上无法解析的父引用。
/// 要少收结构记录，请在 sink 里按 Kind 丢弃。
void        set_level(Level v_level);

/// 每线程环形缓冲条数，默认 256。线程首次记录时才按当时的值建缓冲，
/// 因此已开始记录的线程不受后续修改影响。
void        set_ring_capacity(std::size_t v_records);

/// 后台排空间隔，默认 100 毫秒；置 0 表示只手动 flush。
void        set_flush_interval_ms(std::size_t v_ms);

/// 达到或超过该级别的事件记录立即触发一次排空，默认关闭。
///
/// 用途是"错误日志必须尽快落盘"：默认 100 毫秒的批量排空留下一个崩溃窗口，
/// 打开这个开关后 error 级记录会在写出后立刻被排空线程处理。
/// 只改变何时排空，不动无锁生产路径。
void        set_flush_on_level(Level v_level, bool v_enabled);

/// 同步模式：写出一条事件记录后立刻排空本线程的环。
/// 行为对齐同步直写，崩溃不丢已返回的记录；代价是吞吐明显下降，仅用于排障。
void        set_sync_mode(bool v_enabled);

/// 某级别的事件记录是否会被写出，供便捷宏在格式化之前先判。
bool        enabled(Level v_level) noexcept;

// ---- 记录 ----

/// 写一条事件记录，归属当前操作。
void        log(Level v_level, const char* v_message) noexcept;

/// 按 printf 格式写一条事件记录；超过消息区容量的部分截断。
/// 编译器支持时按 printf 规则检查格式串与实参。
STACKLOG_PRINTF(2, 3)
void          logf(Level v_level, const char* v_format, ...) noexcept;

/// 写一条结构化错误记录：错误码 + 可读原因 + 后端原始输出。
///
/// 错误不该是一句糊在一起的文本：分开承载后，事后可按错误码检索、按原因归类，
/// 原始输出保留现场。`raw` 可为空。
void          log_error(std::uint32_t v_code, const char* v_reason, const char* v_raw) noexcept;

/// 内省

/// 当前栈顶操作的操作 id；空栈返回全零。
SpanId        current_span() noexcept;

/// 丢弃计数：缓冲满、关闭后调用、重入、线程上下文已析构、守卫跨线程析构、
/// 操作池增长失败。
std::uint64_t dropped_records() noexcept;

} // namespace stacklog

#endif // INCLUDE_STACKLOG_LOGGER_HPP
