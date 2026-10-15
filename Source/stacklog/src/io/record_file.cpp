#include <stacklog/io/record_file.hpp>

#include <cerrno>
#include <cstdio>
#include <cstring>

#include <stacklog/common/diagnose.hpp>

namespace stacklog
{

namespace
{

/// 记录布局的编译期保护：字段增删或容量调整会让断言失败，从而必须同步递增
/// 布局版本，避免悄悄破坏存量二进制文件。
static_assert(sizeof(Record) == 296, "Record 布局已变化：请同步更新 kRecordFileFormatVersion");

RecordFileHeader make_header()
{
    RecordFileHeader header;
    header.t_magic          = g_k_record_file_magic;
    header.t_format_version = g_k_record_file_format_version;
    header.t_record_size    = static_cast<std::uint16_t>(sizeof(Record));
    header.t_reserved       = 0;
    return header;
}

/// 文件为空时补写头部；已有内容（含头部）则原样追加。
bool ensure_header(std::FILE* v_file)
{
    if (std::fseek(v_file, 0, SEEK_END) != 0)
    {
        return false;
    }
    if (std::ftell(v_file) != 0)
    {
        return true; // 已有内容，头部在首次创建时写过
    }
    const RecordFileHeader header = make_header();
    return std::fwrite(&header, sizeof(header), 1, v_file) == 1;
}

} // namespace

RecordFileSink::RecordFileSink(const char* v_path): m_file(0), m_write_failure_reported(false)
{
    if (v_path != 0)
    {
        m_file = std::fopen(v_path, "ab");
    }
    // 头部写不进去就不能假装能用：后续记录会缺少可校验的前提。
    if (m_file != 0 && !ensure_header(m_file))
    {
        m_write_failure_reported = true;
        common::diagnose_errno("写记录文件头部失败");
        std::fclose(m_file);
        m_file = 0;
    }
}

RecordFileSink::~RecordFileSink()
{
    if (m_file != 0)
    {
        std::fclose(m_file);
    }
}

void RecordFileSink::write(const Record& v_record)
{
    if (m_file == 0)
    {
        return;
    }
    const std::size_t written = std::fwrite(&v_record, sizeof(v_record), 1, m_file);
    if (written != 1 && !m_write_failure_reported)
    {
        m_write_failure_reported = true;
        // 结构化文件无法用文本兜底（会把二进制格式弄脏），因此如实报出，
        // 由调用方从 dropped_records() 看到丢失。
        common::diagnose_errno("写结构化记录文件失败，后续记录可能丢失");
    }
}

void RecordFileSink::flush()
{
    if (m_file == 0)
    {
        return;
    }
    // 写错误通常在 fflush 时才暴露：fwrite 只是把数据放进 libc 缓冲。
    if (std::fflush(m_file) != 0 && !m_write_failure_reported)
    {
        m_write_failure_reported = true;
        common::diagnose_errno("写结构化记录文件失败，后续记录可能丢失");
    }
}

bool RecordFileSink::is_open() const
{
    return m_file != 0;
}

RecordFileReader::RecordFileReader(const char* v_path)
    : m_file(0), m_truncated(false), m_failed(false), m_header_ok(false), m_error("")
{
    if (v_path != 0)
    {
        m_file = std::fopen(v_path, "rb");
    }
    if (m_file == 0)
    {
        m_failed = true;
        m_error  = "打不开记录文件";
        return;
    }

    RecordFileHeader header;
    if (std::fread(&header, sizeof(header), 1, m_file) != 1)
    {
        m_failed = true;
        m_error  = "不是记录文件：头部缺失或不完整";
        return;
    }
    if (header.t_magic != g_k_record_file_magic)
    {
        m_failed = true;
        m_error  = "不是记录文件：标识不匹配";
        return;
    }
    if (header.t_format_version != g_k_record_file_format_version)
    {
        m_failed = true;
        char buffer[160];
        std::snprintf(buffer,
                      sizeof(buffer),
                      "记录文件布局版本不符：本库期望 %u，文件为 %u",
                      static_cast<unsigned>(g_k_record_file_format_version),
                      static_cast<unsigned>(header.t_format_version));
        m_version_message = buffer;
        m_error           = m_version_message.c_str();
        return;
    }
    if (header.t_record_size != sizeof(Record))
    {
        m_failed = true;
        char buffer[160];
        std::snprintf(buffer,
                      sizeof(buffer),
                      "记录长度不符：本库为 %u，文件为 %u",
                      static_cast<unsigned>(sizeof(Record)),
                      static_cast<unsigned>(header.t_record_size));
        m_version_message = buffer;
        m_error           = m_version_message.c_str();
        return;
    }
    m_header_ok = true;
}

RecordFileReader::~RecordFileReader()
{
    if (m_file != 0)
    {
        std::fclose(m_file);
    }
}

bool RecordFileReader::next(Record& v_out)
{
    if (m_file == 0 || m_failed || !m_header_ok)
    {
        return false;
    }

    // 按字节读：只有恰好读满一个块才算一条记录。fread 读半个块时也会消耗掉
    // 那些字节并置 feof，因此不能用"块数"判断末尾是否有残块。
    const std::size_t want = sizeof(v_out);
    const std::size_t got  = std::fread(&v_out, 1, want, m_file);
    if (got == want)
    {
        return true;
    }

    if (got > 0)
    {
        // 末尾剩下不足一个块：写入过程被打断留下的残块，不能按记录解读。
        m_truncated = true;
        m_error     = "记录文件末尾有残块";
        return false;
    }

    if (std::ferror(m_file))
    {
        m_failed = true;
        m_error  = std::strerror(errno);
    }
    return false;
}

bool RecordFileReader::is_open() const
{
    return m_file != 0;
}

bool RecordFileReader::truncated() const
{
    return m_truncated;
}

const char* RecordFileReader::error() const
{
    return m_error;
}

bool RecordFileReader::header_ok() const
{
    return m_header_ok;
}

} // namespace stacklog
