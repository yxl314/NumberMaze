#include <stacklog/io/sink.hpp>

#include <stacklog/common/diagnose.hpp>
#include <stacklog/common/time.hpp>

#include <cerrno>
#include <cstdlib>
#include <cstdio>
#include <cstring>

#if !defined(_WIN32)
#include <unistd.h>
#endif

#include <stacklog/detail/state.hpp>

namespace stacklog
{
namespace
{

void append_u64(std::string& v_out, std::uint64_t v_value)
{
    v_out += std::to_string(v_value);
}

/// 控制字符转义，保证一条记录占一行且可逐字段切分。
void append_escaped(std::string& v_out, const char* v_text, std::size_t v_len)
{
    static const char s_digits[] = "0123456789abcdef";
    for (std::size_t i = 0; i < v_len; ++i)
    {
        const unsigned char c = static_cast<unsigned char>(v_text[i]);
        if (c == '\n')
        {
            v_out += "\\n";
        }
        else if (c == '\r')
        {
            v_out += "\\r";
        }
        else if (c == '\t')
        {
            v_out += "\\t";
        }
        else if (c < 0x20 || c == 0x7F)
        {
            v_out += "\\x";
            v_out.push_back(s_digits[(c >> 4) & 0xFu]);
            v_out.push_back(s_digits[c & 0xFu]);
        }
        else
        {
            v_out.push_back(static_cast<char>(c));
        }
    }
}

/// 内置 sink：写标准错误，不持有任何需要释放的资源。

} // namespace

Sink::Sink(): m_on_destroy(0) {}

Sink::~Sink()
{
    if (m_on_destroy != 0)
    {
        m_on_destroy(this);
    }
}

void Sink::set_destroy_hook(DestroyHook v_hook)
{
    m_on_destroy = v_hook;
}

Sink::DestroyHook Sink::destroy_hook() const
{
    return m_on_destroy;
}

void format_record(const Record& v_record, std::string& v_out)
{
    v_out.clear();
    v_out.reserve(160 + v_record.t_len);
    v_out += to_string(v_record.t_chain);
    v_out.push_back(' ');
    v_out += to_string(v_record.t_span);
    v_out.push_back(' ');
    v_out += to_string(v_record.t_parent);
    v_out.push_back(' ');
    // 人读渲染：本地时间；机器可读的定长记录仍保留纳秒原值。
    char readable[32];
    common::format_wall_time(v_record.t_ts_wall, readable, sizeof(readable));
    if (readable[0] != '\0')
    {
        v_out += readable;
    }
    else
    {
        append_u64(v_out, v_record.t_ts_wall);
    }
    v_out.push_back(' ');
    append_u64(v_out, v_record.t_slot);
    v_out.push_back(' ');
    v_out += level_name(v_record.t_level);
    v_out.push_back(' ');
    v_out += kind_name(v_record.t_kind);
    v_out.push_back(' ');
    v_out += v_record.t_name;
    v_out.push_back(' ');
    v_out += v_record.t_loc.t_file == 0 ? "" : v_record.t_loc.t_file;
    v_out.push_back(':');
    append_u64(v_out, static_cast<std::uint64_t>(v_record.t_loc.t_line));
    v_out.push_back(' ');
    // 结构化错误：错误码单独成列，便于事后按码检索；原因与原始输出在 message 里。
    if (v_record.t_code != 0)
    {
        char code_text[32];
        std::snprintf(code_text, sizeof(code_text), "code=0x%08X", static_cast<unsigned>(v_record.t_code));
        v_out += code_text;
        v_out.push_back(' ');
    }
    append_escaped(v_out, v_record.t_message, v_record.t_len);
}

FileSink::FileSink(const char* v_path): m_file(0), m_write_failure_reported(false), m_degraded(false)
{
    if (v_path != 0)
    {
        m_file = std::fopen(v_path, "ab");
    }
}

FileSink::~FileSink()
{
    if (m_file != 0)
    {
        std::fflush(m_file);
        std::fclose(m_file);
        m_file = 0;
    }
}

void FileSink::write(const Record& v_record)
{
    if (m_degraded)
    {
        write_to_stderr(v_record);
        return;
    }
    if (m_file == 0)
    {
        return;
    }

    format_record(v_record, m_line);
    m_line.push_back('\n');
    const std::size_t written = std::fwrite(m_line.data(), 1, m_line.size(), m_file);
    if (written == m_line.size())
    {
        return;
    }

    // 写入失败：记录不能就此消失。转入标准错误，并只提示一次原因。
    m_degraded = true;
    if (!m_write_failure_reported)
    {
        m_write_failure_reported = true;
        common::diagnose_errno("写文件 sink 失败，后续记录改写到标准错误");
    }
    write_to_stderr(v_record);
}

void FileSink::write_to_stderr(const Record& v_record)
{
    format_record(v_record, m_line);
    m_line.push_back('\n');
    std::fwrite(m_line.data(), 1, m_line.size(), stderr);
}

bool FileSink::degraded_to_stderr() const
{
    return m_degraded;
}

void FileSink::flush()
{
    if (m_file == 0 || m_degraded)
    {
        return;
    }
    // 写错误通常在 fflush 时才暴露：fwrite 只是把数据放进 libc 缓冲。
    if (std::fflush(m_file) == 0)
    {
        return;
    }
    m_degraded = true;
    if (!m_write_failure_reported)
    {
        m_write_failure_reported = true;
        common::diagnose_errno("写文件 sink 失败，后续记录改写到标准错误");
    }
}

bool FileSink::is_open() const
{
    return m_file != 0;
}

namespace detail
{

Sink& default_sink()
{
    static Sink* s_sink = new StderrSink();
    return *s_sink;
}

} // namespace detail
RotatingOptions::RotatingOptions()
    : t_max_bytes(5ull * 1024ull * 1024ull), // 在 64 位里算，不在 32 位算完再拓宽
      t_max_files(5u)
{}

RotatingFileSink::RotatingFileSink(const char* v_path, const RotatingOptions& v_options)
    : m_path(v_path != 0 ? v_path : ""), m_options(v_options), m_file(0), m_written_bytes(0), m_rotations(0),
      m_suspended(false), m_notice_shown(false)
{
    if (m_options.t_max_files < 1)
    {
        m_options.t_max_files = 1;
    }
    open_current();
}

RotatingFileSink::~RotatingFileSink()
{
    if (m_file != 0)
    {
        std::fclose(m_file);
    }
}

bool RotatingFileSink::open_current()
{
    if (m_file != 0)
    {
        std::fclose(m_file);
        m_file = 0;
    }
    if (m_path.empty())
    {
        return false;
    }

    // 打开后记住既有大小：计数从已有内容继续，避免重启后立刻误轮转。
    m_file = std::fopen(m_path.c_str(), "ab+");
    if (m_file == 0)
    {
        return false;
    }
    m_written_bytes = 0;
    if (std::fseek(m_file, 0, SEEK_END) == 0)
    {
        const long size = std::ftell(m_file);
        if (size > 0)
        {
            m_written_bytes = static_cast<std::uint64_t>(size);
        }
    }
    return true;
}

std::string RotatingFileSink::archive_path(std::uint32_t v_index) const
{
    char suffix[24];
    std::snprintf(suffix, sizeof(suffix), ".%u", static_cast<unsigned>(v_index));
    return m_path + suffix;
}

bool RotatingFileSink::rename_file(const std::string& v_from, const std::string& v_to)
{
    // 源文件不存在不算失败：历史归档链有缺口不是故障。
    if (std::rename(v_from.c_str(), v_to.c_str()) == 0)
    {
        return true;
    }
    if (errno == ENOENT)
    {
        return true;
    }
    return false;
}

bool RotatingFileSink::rotate()
{
    // 归档链整体后移：先删最老的，再从后往前改名，最后现行 -> .1。
    const std::string oldest = archive_path(m_options.t_max_files);
    std::remove(oldest.c_str());

    for (std::uint32_t index = m_options.t_max_files; index > 1; --index)
    {
        if (!rename_file(archive_path(index - 1), archive_path(index)))
        {
            return false;
        }
    }
    return rename_file(m_path, archive_path(1));
}

void RotatingFileSink::write(const Record& v_record)
{
    if (m_file == 0)
    {
        return;
    }

    format_record(v_record, m_line);
    m_line.push_back('\n');
    const std::size_t written = std::fwrite(m_line.data(), 1, m_line.size(), m_file);
    if (written != m_line.size())
    {
        write_to_stderr(v_record);
        return;
    }
    m_written_bytes += m_line.size();

    if (m_options.t_max_bytes == 0 || m_suspended || m_written_bytes < m_options.t_max_bytes)
    {
        return;
    }

    if (rotate())
    {
        ++m_rotations;
        m_suspended    = false; // 归档恢复，下次异常可以再提示
        m_notice_shown = false;
        // 无条件重开现行文件：归档失败也绝不留在"已关闭"态。
        open_current();
        return;
    }

    // 归档失败：继续追加现行文件，停止后续尝试，只提示一次。
    m_suspended = true;
    if (!m_notice_shown)
    {
        m_notice_shown = true;
        common::diagnose_errno("日志归档失败，继续写现行文件并不再尝试轮转");
    }
}

void RotatingFileSink::write_to_stderr(const Record& v_record)
{
    format_record(v_record, m_line);
    m_line.push_back('\n');
    std::fwrite(m_line.data(), 1, m_line.size(), stderr);
}

void RotatingFileSink::flush()
{
    if (m_file == 0)
    {
        return;
    }
    if (std::fflush(m_file) != 0)
    {
        common::diagnose_errno("写文件 sink 失败");
    }
}

bool RotatingFileSink::is_open() const
{
    return m_file != 0;
}

bool RotatingFileSink::rotation_suspended() const
{
    return m_suspended;
}

std::uint64_t RotatingFileSink::rotation_count() const
{
    return m_rotations;
}

namespace
{

/// 级别到 ANSI 颜色的映射，与常见终端约定一致。
const char* level_color(Level v_level)
{
    switch (v_level)
    {
    case Level::Debug:
        return "\033[90m"; // 灰
    case Level::Info:
        return "\033[32m"; // 绿
    case Level::Warn:
        return "\033[33m"; // 黄
    case Level::Error:
        return "\033[91m"; // 亮红
    }
    return "";
}

/// 颜色能力检测：每次现判，不缓存。
bool stderr_supports_color()
{
    // NO_COLOR 非空即禁色（业界约定）
    const char* no_color = std::getenv("NO_COLOR");
    if (no_color != 0 && no_color[0] != '\0')
    {
        return false;
    }
#if defined(_WIN32)
    return false;
#else
    return ::isatty(::fileno(stderr)) == 1;
#endif
}

} // namespace

StderrSink::StderrSink(ColorMode v_mode): m_mode(v_mode) {}

StderrSink::~StderrSink() {}

bool StderrSink::colors_enabled() const
{
    if (m_mode == ColorAlways)
    {
        return true;
    }
    if (m_mode == ColorNever)
    {
        return false;
    }
    return stderr_supports_color();
}

void StderrSink::write(const Record& v_record)
{
    format_record(v_record, m_line);
    m_line.push_back('\n');
    if (!colors_enabled())
    {
        std::fwrite(m_line.data(), 1, m_line.size(), stderr);
        return;
    }
    const char* color = level_color(v_record.t_level);
    std::fwrite(color, 1, std::strlen(color), stderr);
    std::fwrite(m_line.data(), 1, m_line.size(), stderr);
    std::fputs("\033[0m", stderr);
}

void StderrSink::flush()
{
    std::fflush(stderr);
}

TeeSink::TeeSink(Sink& v_primary, Sink& v_mirror): m_first(&v_primary), m_second(&v_mirror) {}

TeeSink::~TeeSink() {}

void TeeSink::write(const Record& v_record)
{
    // 主通道先写：镜像出问题不能影响主通道，也不能让记录丢失。
    m_first->write(v_record);
    if (m_second != 0)
    {
        m_second->write(v_record);
    }
}

void TeeSink::flush()
{
    m_first->flush();
    if (m_second != 0)
    {
        m_second->flush();
    }
}

} // namespace stacklog
