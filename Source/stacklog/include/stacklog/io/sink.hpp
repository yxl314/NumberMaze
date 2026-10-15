#ifndef INCLUDE_STACKLOG_IO_SINK_HPP
#define INCLUDE_STACKLOG_IO_SINK_HPP

#include <cstdint>
#include <cstdio>
#include <string>

#include <stacklog/core/record.hpp>

namespace stacklog
{

/// 消费端接口。只在排空路径上调用（后台排空线程，或手动 flush 的调用线程），
/// 同一时刻只有一个调用者；实现内部不得再调用本库的记录接口。
///
/// 生存期：交给 use_sink 之后，析构时会自动被库摘掉并回退到默认 sink，
/// 因此不存在"库调用已析构 sink"的窗口；但那之后到重新登记之间的记录会走默认 sink。
class Sink
{
public:
    virtual ~Sink();

    /// 消费一条记录。
    virtual void write(const Record& v_record) = 0;

    /// 落盘。
    virtual void flush()                       = 0;

    /// 注销回调：由库在登记时设置，析构时调用，使库在 sink 消失之前摘掉它。
    /// 自定义 sink 不必关心，库自身用它来消除"退出排空碰到已析构 sink"的窗口。
    typedef void (*DestroyHook)(Sink* v_sink);
    void        set_destroy_hook(DestroyHook v_hook);
    DestroyHook destroy_hook() const;

protected:
    Sink();

private:
    Sink(const Sink&)                  = delete;
    Sink&       operator=(const Sink&) = delete;

    DestroyHook m_on_destroy;
};

/// 渲染成一行文本：链 操作 父 时间 槽 级别 种类 操作名 位置 消息。
/// 时间为不含空格的本地时间 `YYYY-MM-DDTHH:MM:SS.mmm`。
/// 消息里的控制字符转义，保证一条记录一行。
void format_record(const Record& v_record, std::string& v_out);

/// 追加写入文件。
///
/// 打开失败时 is_open() 为 false，此时写入被丢弃并返回假。
/// 打开成功后若写入持续失败（磁盘满、坏盘），记录不再丢弃：转为写标准错误，
/// 使"日志写不进去"这件事本身可见。降级只提示一次，不逐条刷屏。
class FileSink : public Sink
{
public:
    explicit FileSink(const char* v_path);
    ~FileSink();

    void write(const Record& v_record);
    void flush();

    /// 文件是否打开成功。
    bool is_open() const;

    /// 是否已因写入失败降级到标准错误。
    bool degraded_to_stderr() const;

private:
    FileSink(const FileSink&)              = delete;
    FileSink&   operator=(const FileSink&) = delete;

    /// 写入失败后转入的标准错误通道。
    void        write_to_stderr(const Record& v_record);

    std::FILE*  m_file;
    std::string m_line;
    bool        m_write_failure_reported; // 降级提示只打一次
    bool        m_degraded;               // 已转入标准错误
};

/// 轮转参数：单文件上限与保留份数。
struct RotatingOptions
{
    std::uint64_t t_max_bytes; // 单文件上限，超过即轮转；0 表示不轮转
    std::uint32_t t_max_files; // 归档份数上限，至少保留 1 份当前文件

    RotatingOptions();
};

/// 带轮转的文本文件 sink。
///
/// 现行文件为 <路径>，归档依次为 <路径>.1 … <路径>.N，N 取 max_files。
/// 轮转时先删最老的，再把归档链整体后移，最后把现行文件移到 .1。
///
/// 归档失败（目标被占、目录只读）不会让日志丢失：继续追加现行文件，
/// 并停止本进程后续的轮转尝试，只提示一次；某次归档成功即恢复尝试。
/// 计数用累计写入字节，不做每行 stat。
class RotatingFileSink : public Sink
{
public:
    RotatingFileSink(const char* v_path, const RotatingOptions& v_options);
    ~RotatingFileSink();

    void          write(const Record& v_record);
    void          flush();

    /// 当前文件是否打开成功。
    bool          is_open() const;

    /// 是否因归档失败而暂停轮转。
    bool          rotation_suspended() const;

    /// 已完成多少次轮转。
    std::uint64_t rotation_count() const;

private:
    RotatingFileSink(const RotatingFileSink&)            = delete;
    RotatingFileSink& operator=(const RotatingFileSink&) = delete;

    /// 打开现行文件并把累计字节数按已有大小初始化。
    bool              open_current();
    /// 执行一次轮转。返回是否成功（源文件不存在也算成功）。
    bool              rotate();
    /// 把 from 改名到 to；源不存在视为成功。禁 system("mv")。
    bool              rename_file(const std::string& v_from, const std::string& v_to);
    /// 归档路径：<现行路径>.<index>。
    std::string       archive_path(std::uint32_t v_index) const;
    void              write_to_stderr(const Record& v_record);

    std::string       m_path;
    RotatingOptions   m_options;
    std::FILE*        m_file;
    std::uint64_t     m_written_bytes; // 现行文件已写字节，含打开时的既有大小
    std::uint64_t     m_rotations;
    bool              m_suspended;   // 归档失败后暂停轮转，成功即复位
    bool              m_notice_shown; // 归档失败提示只打一次
    std::string       m_line;
};

/// 标准错误 sink，可选按级别着色。
///
/// 颜色默认自动检测：仅在标准错误是交互终端且 `NO_COLOR` 为空时才着色。
/// 检测每次写出时现判，不缓存环境状态。
class StderrSink : public Sink
{
public:
    enum ColorMode
    {
        ColorAuto,   // 自动检测（默认）
        ColorAlways, // 强制着色
        ColorNever   // 强制不着色
    };

    explicit StderrSink(ColorMode v_mode = ColorAuto);
    ~StderrSink();

    void write(const Record& v_record);
    void flush();

    /// 本次写出是否会着色（按当前环境判定）。
    bool colors_enabled() const;

private:
    StderrSink(const StderrSink&)            = delete;
    StderrSink& operator=(const StderrSink&) = delete;

    ColorMode   m_mode;
    std::string m_line;
};

/// 把记录同时送往主 sink 与镜像 sink。
///
/// 主 sink 先写；镜像失败不影响主通道，因此"落文件 + 打终端"时
/// 终端慢或坏不会拖累文件。写入仍只在排空线程单点发生，无需加锁。
class TeeSink : public Sink
{
public:
    TeeSink(Sink& v_primary, Sink& v_mirror);
    ~TeeSink();

    void write(const Record& v_record);
    void flush();

private:
    TeeSink(const TeeSink&)            = delete;
    TeeSink& operator=(const TeeSink&) = delete;

    Sink*    m_first;
    Sink*    m_second;
};

} // namespace stacklog

#endif // INCLUDE_STACKLOG_IO_SINK_HPP
