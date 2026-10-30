#ifndef INCLUDE_STACKLOG_IO_RECORD_FILE_HPP
#define INCLUDE_STACKLOG_IO_RECORD_FILE_HPP

#include <cstdint>
#include <cstdio>
#include <string>

#include <stacklog/core/record.hpp>
#include <stacklog/io/sink.hpp>

namespace stacklog
{

/// 结构化记录文件的字节序与布局标识。
///
/// 文件以固定头部开头，回读时先校验：非本库文件、或换过 Record 布局的旧文件，
/// 都会被明确拒绝，而不是把任意字节按 Record 解读。
struct RecordFileHeader
{
    std::uint32_t t_magic;         // 固定为 kRecordFileMagic
    std::uint16_t t_format_version; // 布局版本，Record 字段一改就递增
    std::uint16_t t_record_size;   // 写出时的 sizeof(Record)
    std::uint32_t t_reserved;      // 对齐占位，写出恒为 0
};

/// 文件标识，四个字节 'S''L''O''G'。
constexpr std::uint32_t g_k_record_file_magic          = 0x474F4C53u;

/// 当前布局版本。Record 增删字段或改变容量时必须递增。
constexpr std::uint16_t g_k_record_file_format_version = 2;

/// 结构化记录文件：每个记录按内存镜像顺序存放一个定长块。
///
/// 与文本 sink 的区别是回读不需要解析：读回即按块读回一个 Record，
/// 因此不存在"列序与转义成为永久契约"的负担，也不会有截断与精度损失。
/// 代价是文件不可读，给人看请另用文本 sink。
class RecordFileSink : public Sink
{
public:
    explicit RecordFileSink(const char* v_path);
    ~RecordFileSink();

    void write(const Record& v_record);
    void flush();

    /// 文件是否打开成功；失败时写入被丢弃。
    bool is_open() const;

private:
    RecordFileSink(const RecordFileSink&)            = delete;
    RecordFileSink& operator=(const RecordFileSink&) = delete;

    std::FILE*      m_file;
    bool            m_write_failure_reported;
};

/// 按块读回结构化记录文件。
///
/// 读回的是原始 Record，字段与写出时逐一相同。文件末尾不足一个块时丢弃残块
/// 并通过 truncated() 报出，已完整的部分照常可用。
class RecordFileReader
{
public:
    explicit RecordFileReader(const char* v_path);
    ~RecordFileReader();

    /// 读下一条记录。读到文件末尾或出错返回 false。
    bool        next(Record& v_out);

    /// 是否打开成功。
    bool        is_open() const;

    /// 是否遇到末尾残块（不足一个完整块）。
    bool        truncated() const;

    /// 读入出错时的错误说明，成功或末尾时为空。
    const char* error() const;

    /// 文件头是否合法（魔数、版本、记录大小都对得上）。
    /// 不合法时 next() 不产出任何记录，error() 说明原因。
    bool        header_ok() const;

private:
    RecordFileReader(const RecordFileReader&)            = delete;
    RecordFileReader& operator=(const RecordFileReader&) = delete;

    std::FILE*        m_file;
    bool              m_truncated;
    bool              m_failed;
    bool              m_header_ok;
    const char*       m_error;
    std::string       m_version_message; // 版本不符时的详细说明，含期望与实际
};

} // namespace stacklog

#endif // INCLUDE_STACKLOG_IO_RECORD_FILE_HPP
