#ifndef INCLUDE_STACKLOG_CORE_RECORD_HPP
#define INCLUDE_STACKLOG_CORE_RECORD_HPP

#include <cstddef>
#include <cstdint>

#include <stacklog/core/id.hpp>
#include <stacklog/core/source_loc.hpp>

namespace stacklog
{

/// 级别阈值：低于阈值的记录不产生。
enum class Level : std::uint8_t
{
    Debug = 0,
    Info  = 1,
    Warn  = 2,
    Error = 3
};

/// 记录种类：enter/exit 由守卫自动产生，event 由记录接口产生。
enum class Kind : std::uint8_t
{
    Enter = 0,
    Exit  = 1,
    Event = 2
};

/// 消息区容量，编译期可调；超长截断。
#ifndef STACKLOG_MESSAGE_CAPACITY
#define STACKLOG_MESSAGE_CAPACITY 128
#endif

constexpr std::size_t g_k_message_capacity = STACKLOG_MESSAGE_CAPACITY;

/// 操作名区容量，编译期可调；超长截断。
#ifndef STACKLOG_NAME_CAPACITY
#define STACKLOG_NAME_CAPACITY 32
#endif

constexpr std::size_t g_k_name_capacity = STACKLOG_NAME_CAPACITY;

/// 定长记录，无堆分配。sink 只在排空路径上读取。
///
/// 错误类的记录用三个要素描述：`code`（错误码）、`message`（可读原因）、
/// `name` 处承载的后端原始输出在 message 之后以换行分隔。
/// 布局任何变化都必须同步递增记录文件的布局版本。
struct Record
{
    ChainId       t_chain;     // 归属链
    SpanId        t_span;      // 产生记录的操作
    SpanId        t_parent;    // 父引用，根为全零
    ProcessId     t_emitter;   // 发出记录的进程实例，与 span.origin 不必相同
    std::uint32_t t_slot;      // 发出记录的线程槽
    std::uint64_t t_seq;       // 线程内记录序号，定序用
    std::uint64_t t_ts_steady; // 纳秒，用于耗时
    std::uint64_t t_ts_wall;   // 纳秒，用于跨线程对齐
    char          t_name[g_k_name_capacity]; // 操作名的内联副本，超长截断
    SourceLoc     t_loc;       // 指向操作的开操作位置，字符串为静态字面量
    Level         t_level;     // 严重度，只对事件有意义
    Kind          t_kind;      // 结构（enter/exit）还是事件，识别树靠它
    std::uint16_t t_len;       // message 的有效长度
    std::uint32_t t_code;      // 错误码，只对事件有意义；非错误事件为 0
    char          t_message[g_k_message_capacity]; // 错误场景下是"可读原因"
};

const char* level_name(Level v_level);
const char* kind_name(Kind v_kind);

} // namespace stacklog

#endif // INCLUDE_STACKLOG_CORE_RECORD_HPP
