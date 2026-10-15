#include <stacklog/logger.hpp>

#if !defined(_WIN32)
#include <unistd.h>
#endif

#include <cstdarg>
#include <cstdio>
#include <cstring>

#include <stacklog/common/diagnose.hpp>
#include <stacklog/common/file.hpp>
#include <stacklog/detail/context.hpp>
#include <stacklog/detail/state.hpp>

namespace stacklog
{

void shutdown() noexcept
{
    detail::shutdown_process();
}

bool flush()
{
    if (!detail::alive())
    {
        return true;
    }
    detail::ensure_initialized();
    return detail::drain_once();
}

void use_sink(Sink& v_sink)
{
    detail::ensure_initialized();
    detail::set_configured_sink(&v_sink, false);
}

bool log_to_file(const char* v_path, const RotatingOptions& v_options)
{
    detail::ensure_initialized();
    if (v_path == 0 || v_path[0] == '\0')
    {
        return false;
    }

    const std::string target(v_path);
    const std::string parent = common::parent_of(target);
    if (!parent.empty() && !common::make_directories(parent.c_str()))
    {
        common::diagnose_errno("创建日志目录失败");
        return false;
    }

    RotatingFileSink* sink = 0;
    try
    {
        sink = new RotatingFileSink(v_path, v_options);
    }
    catch (...)
    {
        return false;
    }
    bool usable = sink->is_open();
    if (usable)
    {
        // 与单文件版本同样的试探写：确认真的写得进去。
        sink->flush();
        std::FILE* probe = std::fopen(v_path, "ab+");
        usable           = false;
        if (probe != 0)
        {
            if (std::fseek(probe, 0, SEEK_END) == 0)
            {
                const long original = std::ftell(probe);
                const char byte     = '\0';
                usable              = std::fwrite(&byte, 1, 1, probe) == 1 && std::fflush(probe) == 0;
                if (usable && original >= 0)
                {
#if !defined(_WIN32)
                    const int fd = ::fileno(probe);
                    usable       = fd >= 0 && ::ftruncate(fd, original) == 0;
#endif
                }
            }
            std::fclose(probe);
        }
    }
    if (!usable)
    {
        common::diagnose_errno("日志文件不可写");
        delete sink;
        return false;
    }

    detail::set_configured_sink(sink, true);
    return true;
}

std::string default_log_path(const char* v_application)
{
    return common::default_log_path(v_application);
}

bool log_to_file(const char* v_path)
{
    detail::ensure_initialized();
    if (v_path == 0 || v_path[0] == '\0')
    {
        return false;
    }

    // 目标目录不存在就建出来；建不出来直接失败，不留半开状态。
    const std::string target(v_path);
    const std::string parent = common::parent_of(target);
    if (!parent.empty() && !common::make_directories(parent.c_str()))
    {
        common::diagnose_errno("创建日志目录失败");
        return false;
    }

    FileSink* sink = 0;
    try
    {
        sink = new FileSink(v_path);
    }
    catch (...)
    {
        return false;
    }
    if (!sink->is_open())
    {
        common::diagnose_errno("打开日志文件失败");
        delete sink;
        return false;
    }

    // 试探写：打开成功不等于写得进去（目录只读、配额用尽等）。
    // 直接写一个字节核对，不经 sink 的记录路径——避免为探测产出一条内容为空的
    // 假记录混进日志。探测后把文件截回原长度，不留痕迹。
    {
        std::FILE* probe  = std::fopen(v_path, "ab+");
        bool       usable = false;
        if (probe != 0)
        {
            if (std::fseek(probe, 0, SEEK_END) == 0)
            {
                const long original = std::ftell(probe);
                const char byte     = '\0';
                usable              = std::fwrite(&byte, 1, 1, probe) == 1 && std::fflush(probe) == 0;
                if (usable && original >= 0)
                {
                    // 截掉刚写入的探测字节，保持文件内容与探测前一致。
#if defined(_WIN32)
                    usable = std::fflush(probe) == 0 && ::SetEndOfFile(probe) != 0;
#else
                    const int fd = ::fileno(probe);
                    usable       = fd >= 0 && ::ftruncate(fd, original) == 0;
#endif
                }
            }
            std::fclose(probe);
        }
        if (!usable)
        {
            common::diagnose_errno("日志文件不可写");
            delete sink;
            return false;
        }
    }

    detail::set_configured_sink(sink, true);
    return true;
}

void set_level(Level v_level)
{
    detail::set_min_level(v_level);
}

void set_ring_capacity(std::size_t v_records)
{
    detail::set_ring_capacity(v_records);
}

void set_flush_on_level(Level v_level, bool v_enabled)
{
    detail::set_flush_on_level(v_level, v_enabled);
}

void set_sync_mode(bool v_enabled)
{
    detail::set_sync_mode(v_enabled);
}

void set_flush_interval_ms(std::size_t v_ms)
{
    detail::ensure_initialized();
    detail::set_flush_interval_ms(v_ms);
    if (v_ms == 0)
    {
        detail::stop_flusher();
    }
    else
    {
        detail::start_flusher(v_ms);
    }
}

bool enabled(Level v_level) noexcept
{
    return detail::level_enabled(v_level);
}

void log(Level v_level, const char* v_message) noexcept
{
    if (v_message == 0)
    {
        // 空消息是调用方的错误，计入丢弃计数而不是无声吞掉。
        detail::count_drop();
        return;
    }
    if (!detail::level_enabled(v_level))
    {
        return;
    }
    detail::ensure_initialized();
    detail::emit(Kind::Event, v_level, v_message, std::strlen(v_message));
}

void logf(Level v_level, const char* v_format, ...) noexcept
{
    if (v_format == 0)
    {
        detail::count_drop();
        return;
    }
    if (!detail::level_enabled(v_level))
    {
        return;
    }
    detail::ensure_initialized();
    Record* slot = detail::begin_record(Kind::Event, v_level);
    if (slot == 0)
    {
        return;
    }
    va_list args;
    va_start(args, v_format);
    const int written = std::vsnprintf(slot->t_message, g_k_message_capacity, v_format, args);
    va_end(args);
    std::size_t length = 0;
    if (written < 0)
    {
        static const char s_k_format_error[] = "<format error>";
        std::memcpy(slot->t_message, s_k_format_error, sizeof(s_k_format_error) - 1);
        length = sizeof(s_k_format_error) - 1;
    }
    else
    {
        const std::size_t count = static_cast<std::size_t>(written);
        length                  = count < g_k_message_capacity ? count : g_k_message_capacity - 1;
    }
    slot->t_message[length] = '\0';
    slot->t_len             = static_cast<std::uint16_t>(length);
    detail::commit_record(Kind::Event, v_level);
}

void log_error(std::uint32_t v_code, const char* v_reason, const char* v_raw) noexcept
{
    if (v_reason == 0)
    {
        detail::count_drop();
        return;
    }
    detail::ensure_initialized();

    // 原因与原始输出合并进 message：原因在前、原始输出在后，以换行分隔。
    // 这样不必为 raw 另开一块定长区，记录长度保持不变。
    char        combined[g_k_message_capacity];
    const int   written = (v_raw != 0 && v_raw[0] != '\0')
                              ? std::snprintf(combined, sizeof(combined), "%s\n%s", v_reason, v_raw)
                              : std::snprintf(combined, sizeof(combined), "%s", v_reason);
    std::size_t length  = 0;
    if (written > 0)
    {
        length = static_cast<std::size_t>(written);
        if (length >= sizeof(combined))
        {
            length = sizeof(combined) - 1;
        }
    }

    Record* slot = detail::begin_record(Kind::Event, Level::Error);
    if (slot == 0)
    {
        return;
    }
    slot->t_code = v_code;
    std::memcpy(slot->t_message, combined, length);
    slot->t_message[length] = '\0';
    slot->t_len             = static_cast<std::uint16_t>(length);
    detail::commit_record(Kind::Event, Level::Error);
}

SpanId current_span() noexcept
{
    const detail::Span* span = detail::top_span();
    if (span == 0)
    {
        return SpanId{{0, 0}, 0, 0};
    }
    return span->t_span;
}

std::uint64_t dropped_records() noexcept
{
    return detail::dropped_count();
}

} // namespace stacklog
