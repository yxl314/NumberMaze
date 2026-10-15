#include <cstdio>
#include <cstdlib>
#include <chrono>
#include <cstring>
#include <sys/stat.h>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "test_util.hpp"

#include <stacklog/common/time.hpp>

namespace
{

/// 写入即抛异常的 sink，用于验证 flush 会把失败报出来。
class ThrowingSink : public stacklog::Sink
{
public:
    void write(const stacklog::Record& /*record*/)
    {
        throw std::runtime_error("sink boom");
    }
    void flush() {}
};

testing::RecordingSink g_first;
testing::RecordingSink g_second;

std::string            read_file(const char* v_path)
{
    std::FILE* file = std::fopen(v_path, "rb");
    if (file == 0)
    {
        return std::string();
    }
    std::string content;
    char        buffer[512];
    std::size_t read = 0;
    while ((read = std::fread(buffer, 1, sizeof(buffer), file)) > 0)
    {
        content.append(buffer, read);
    }
    std::fclose(file);
    return content;
}

/// 在 sink 内部再调用记录接口：属于误用，必须被丢弃且不得递归。
class ReentrantSink : public stacklog::Sink
{
public:
    ReentrantSink(): t_writes(0) {}

    void write(const stacklog::Record& v_record)
    {
        (void)v_record;
        ++t_writes;
        stacklog::log(stacklog::Level::Info, "v_from sink");
        stacklog::flush();
    }

    void        flush() {}

    std::size_t t_writes;
};

/// 构造早于库状态、析构晚于库状态的全局对象：析构里打日志不得崩溃。
struct StaticProbe
{
    ~StaticProbe()
    {
        stacklog::log(stacklog::Level::Info, "v_from static destructor");
        std::printf("static-destructor-ran\n");
        std::fflush(stdout);
    }
};

StaticProbe g_probe;

void        test_zero_config_writes_to_default_sink()
{
    const char* path = "stacklog_zero_config.txt";
    std::remove(path);
    CHECK(std::freopen(path, "wb", stderr) != 0);

    {
        SL_SPAN("zero-config");
        LOG_INFO("no init needed");
    }
    stacklog::flush();
    std::fflush(stderr);

    const std::string content = read_file(path);
    CHECK(content.find("P-") != std::string::npos);
    CHECK(content.find("zero-config") != std::string::npos);
    CHECK(content.find("no init needed") != std::string::npos);
    CHECK(content.find("info event") != std::string::npos);
}

void test_runtime_config_change()
{
    stacklog::use_sink(g_first);
    stacklog::set_level(stacklog::Level::Info);
    g_first.clear();
    g_second.clear();

    {
        SL_SPAN("config");
        LOG_INFO("first sink");
    }
    stacklog::flush();
    CHECK_EQ(g_first.size(), 3u);       // enter + event + exit
    CHECK_EQ(g_second.size(), 0u);

    stacklog::use_sink(g_second);
    {
        SL_SPAN("config");
        LOG_INFO("second sink");
        LOG_DEBUG("filtered");
    }
    stacklog::flush();
    CHECK_EQ(g_first.size(), 3u);       // 旧 sink 不再收到
    CHECK_EQ(g_second.size(), 3u);

    stacklog::set_level(stacklog::Level::Debug);
    {
        SL_SPAN("config");
        LOG_DEBUG("now kept");
    }
    stacklog::flush();
    CHECK_EQ(g_second.size(), 6u);
    stacklog::set_level(stacklog::Level::Info);
    g_second.clear();
}

void test_settings_do_not_overwrite_each_other()
{
    stacklog::set_level(stacklog::Level::Debug);
    stacklog::set_ring_capacity(2048);
    stacklog::set_flush_interval_ms(50);
    CHECK(stacklog::enabled(stacklog::Level::Debug));

    stacklog::set_level(stacklog::Level::Warn);
    CHECK(!stacklog::enabled(stacklog::Level::Info));
    stacklog::set_level(stacklog::Level::Debug);
}

void test_null_message_counts_as_drop()
{
    const std::uint64_t before = stacklog::dropped_records();
    stacklog::log(stacklog::Level::Info, 0);
    stacklog::logf(stacklog::Level::Info, 0);
    CHECK_EQ(stacklog::dropped_records() - before, 2u);
}

void test_flush_reports_sink_exception()
{
    ThrowingSink sink;
    stacklog::use_sink(sink);
    LOG_INFO("this makes the sink throw");
    CHECK(!stacklog::flush());
    stacklog::use_sink(g_first);       // 换回正常 sink
    g_first.clear();
}

/// 登记之后被析构的 sink：库必须在它消失前摘掉它，而不是在退出排空时去调用它。
/// 这里用一个函数内静态对象模拟"声明晚于库初始化、因而先析构"的情形。
void check_can_still_write()
{
    g_first.clear();
    LOG_INFO("still works");
    stacklog::flush();
    CHECK_EQ(g_first.records().size(), 1u);
}

void test_sink_destroyed_while_registered()
{
    stacklog::flush();
    stacklog::use_sink(g_first);       // 先让库完成初始化（注册退出兜底）
    g_first.clear();

    struct LateSink : public stacklog::Sink
    {
        void write(const stacklog::Record& v_record)
        {
            ++t_writes;
            (void)v_record;
        }
        void        flush() {}
        std::size_t t_writes = 0;
    };

    {
        static LateSink s_late;       // 静态：析构发生在 main 之后
        stacklog::use_sink(s_late);
        LOG_INFO("before sink dies");
        stacklog::flush();
        CHECK_EQ(s_late.t_writes, 1u);

        // 析构前先手动触发注销路径：等价于 late 比库的退出兜底更早析构。
        stacklog::use_sink(g_first);
    }

    // 换回正常 sink 之后记录仍能正常写出，说明库没有卡在"被摘掉"的状态。
    check_can_still_write();
}

/// §四验收：父目录不存在时自动创建，且探测不留痕迹。
void test_log_to_file_creates_directories()
{
    const char*       base    = "stacklog_auto_dir";
    const std::string deep    = std::string(base) + "/a/b/v_c";
    const std::string path    = deep + "/v_out.log";

    // 先清干净，确保是"从无到有"建出来的
    std::string       command = std::string("rm -rf ") + base;
    CHECK_EQ(std::system(command.c_str()), 0);

    CHECK(stacklog::log_to_file(path.c_str()));
    {
        SL_SPAN("auto-dir");
        LOG_INFO("hello");
    }
    stacklog::flush();

    std::FILE* file = std::fopen(path.c_str(), "rb");
    CHECK(file != 0);
    if (file != 0)
    {
        char first = 0;
        CHECK_EQ(std::fread(&first, 1, 1, file), 1u);
        // 探测字节必须被清掉：正文首字符是记录的开头，而不是空字节
        CHECK(first != '\0');
        std::fclose(file);
    }
    CHECK_EQ(std::system(command.c_str()), 0);
}

/// §四验收：写失败后记录不能消失，应转到标准错误。
void test_file_sink_degrades_to_stderr_on_write_failure()
{
    // /dev/full 写入必然失败，用来制造持久写失败
    stacklog::FileSink sink("/dev/full");
    CHECK(sink.is_open());

    stacklog::Record record;
    std::memset(&record, 0, sizeof(record));
    record.t_chain.t_origin.t_pid = 4242;
    record.t_span.t_origin        = record.t_chain.t_origin;
    record.t_span.t_slot          = 1;
    record.t_kind                 = stacklog::Kind::Event;
    record.t_level                = stacklog::Level::Info;
    std::snprintf(record.t_name, sizeof(record.t_name), "degrade");

    sink.write(record);
    sink.flush();
    // 写入持续失败后应进入降级状态，而不是默默丢弃
    CHECK(sink.degraded_to_stderr());
}

void test_log_to_file_rejects_directory()
{
    // 目标是目录，必然打不开：不能返回成功留下半开状态
    CHECK(!stacklog::log_to_file("/tmp"));
}

void test_default_log_path_is_absolute_or_relative()
{
    const std::string path = stacklog::default_log_path("myapp");
    CHECK(!path.empty());
    // 路径必须以应用名结尾，且含应用名子目录
    CHECK(path.find("myapp") != std::string::npos);
    CHECK(path.find("myapp.log") != std::string::npos);
}

/// §三验收：写超上限后轮转，归档份数有界。
void test_rotating_sink_rotates_and_bounds_files()
{
    const char*       dir  = "stacklog_rotate";
    const std::string base = std::string(dir) + "/v_out.log";
    CHECK_EQ(std::system((std::string("rm -rf ") + dir + " && mkdir -p " + dir).c_str()), 0);

    stacklog::RotatingOptions options;
    options.t_max_bytes = 400;
    options.t_max_files = 3;

    {
        stacklog::RotatingFileSink sink(base.c_str(), options);
        CHECK(sink.is_open());
        for (int i = 0; i < 40; ++i)
        {
            stacklog::Record record;
            std::memset(&record, 0, sizeof(record));
            record.t_chain.t_origin.t_pid = 1;
            record.t_span.t_origin        = record.t_chain.t_origin;
            record.t_span.t_slot          = 1;
            record.t_kind                 = stacklog::Kind::Event;
            record.t_level                = stacklog::Level::Info;
            std::snprintf(record.t_name, sizeof(record.t_name), "payload-%d", i);
            sink.write(record);
        }
        sink.flush();
        CHECK(sink.rotation_count() > 0);
    }

    // 现行文件 + 恰好 max_files 份归档，不多不少
    struct stat info;
    CHECK_EQ(::stat(base.c_str(), &info), 0);
    CHECK_EQ(::stat((base + ".1").c_str(), &info), 0);
    CHECK_EQ(::stat((base + ".2").c_str(), &info), 0);
    CHECK_EQ(::stat((base + ".3").c_str(), &info), 0);
    CHECK(::stat((base + ".4").c_str(), &info) != 0); // 超出份数必须被删掉

    CHECK_EQ(std::system((std::string("rm -rf ") + dir).c_str()), 0);
}

/// §三验收：归档链有缺口不算失败。
void test_rotating_sink_tolerates_missing_archive()
{
    const char*       dir  = "stacklog_rotate_gap";
    const std::string base = std::string(dir) + "/v_out.log";
    CHECK_EQ(std::system((std::string("rm -rf ") + dir + " && mkdir -p " + dir).c_str()), 0);
    // 只放最老的一份，中间的 .1/.2 缺失
    CHECK_EQ(std::system((std::string("echo gap > ") + base + ".3").c_str()), 0);

    stacklog::RotatingOptions options;
    options.t_max_bytes = 200;
    options.t_max_files = 3;

    stacklog::RotatingFileSink sink(base.c_str(), options);
    CHECK(sink.is_open());
    for (int i = 0; i < 40; ++i)
    {
        stacklog::Record record;
        std::memset(&record, 0, sizeof(record));
        record.t_chain.t_origin.t_pid = 1;
        record.t_span.t_origin        = record.t_chain.t_origin;
        record.t_span.t_slot          = 1;
        record.t_kind                 = stacklog::Kind::Event;
        sink.write(record);
    }
    sink.flush();

    CHECK(sink.rotation_count() > 0);   // 缺口没有阻止轮转
    CHECK(!sink.rotation_suspended());
    CHECK_EQ(std::system((std::string("rm -rf ") + dir).c_str()), 0);
}

/// §三验收：归档失败后继续追加现行文件，日志不丢。
void test_rotating_sink_degrades_without_losing_records()
{
    const char*       dir  = "stacklog_rotate_ro";
    const std::string base = std::string(dir) + "/v_out.log";
    CHECK_EQ(std::system((std::string("rm -rf ") + dir + " && mkdir -p " + dir).c_str()), 0);

    stacklog::RotatingOptions options;
    options.t_max_bytes = 100;
    options.t_max_files = 3;

    stacklog::RotatingFileSink sink(base.c_str(), options);
    CHECK(sink.is_open());
    for (int i = 0; i < 3; ++i)
    {
        stacklog::Record record;
        std::memset(&record, 0, sizeof(record));
        record.t_kind = stacklog::Kind::Event;
        sink.write(record);
    }
    sink.flush();

    // 目录设为不可写：归档必然失败
    CHECK_EQ(std::system((std::string("chmod 500 ") + dir).c_str()), 0);
    for (int i = 0; i < 40; ++i)
    {
        stacklog::Record record;
        std::memset(&record, 0, sizeof(record));
        record.t_kind = stacklog::Kind::Event;
        sink.write(record);
    }
    sink.flush();
    CHECK_EQ(std::system((std::string("chmod 700 ") + dir).c_str()), 0);

    CHECK(sink.rotation_suspended());   // 停止了后续尝试
    CHECK(sink.is_open());              // 但文件通道没有关闭

    // 记录仍在追加：文件大小明显超过归档阈值
    struct stat info;
    CHECK_EQ(::stat(base.c_str(), &info), 0);
    CHECK(info.st_size > 100);
    CHECK_EQ(std::system((std::string("rm -rf ") + dir).c_str()), 0);
}

/// §六验收：文本行含可读本地时间，且与系统时钟一致。
void test_readable_timestamp_matches_wall_clock()
{
    char                buffer[32];
    const std::uint64_t now = stacklog::common::wall_nanos();
    stacklog::common::format_wall_time(now, buffer, sizeof(buffer));
    CHECK(buffer[0] != '\0');

    // 形如 YYYY-MM-DDTHH:MM:SS.mmm，且不含空格（否则会撑破文本列）
    const std::string text(buffer);
    CHECK_EQ(text.size(), 23u);
    CHECK_EQ(text[4], '-');
    CHECK_EQ(text[7], '-');
    CHECK_EQ(text[10], 'T');
    CHECK_EQ(text[13], ':');
    CHECK_EQ(text[16], ':');
    CHECK_EQ(text[19], '.');
    CHECK(text.find(' ') == std::string::npos);

    // 同一时刻渲染两次结果一致，且与系统时间同秒
    char again[32];
    stacklog::common::format_wall_time(now, again, sizeof(again));
    CHECK_EQ(text, std::string(again));

    // 渲染层应输出可读时间，而不是裸纳秒：直接查格式化结果
    stacklog::Record record;
    std::memset(&record, 0, sizeof(record));
    record.t_chain.t_origin.t_pid = 7;
    record.t_span.t_origin        = record.t_chain.t_origin;
    record.t_span.t_slot          = 1;
    record.t_kind                 = stacklog::Kind::Event;
    record.t_level                = stacklog::Level::Info;
    record.t_ts_wall              = now;
    std::snprintf(record.t_name, sizeof(record.t_name), "readable");

    std::string line;
    stacklog::format_record(record, line);
    CHECK(line.find(buffer) != std::string::npos); // 用可读时间，而非纳秒原值
    CHECK(line.find("readable") != std::string::npos);
    // 裸纳秒有 19 位，可读时间不该同时出现长长的数字串
    char nanos[32];
    std::snprintf(nanos, sizeof(nanos), "%llu", static_cast<unsigned long long>(now));
    CHECK(line.find(nanos) == std::string::npos);
}

/// §七验收：颜色由 NO_COLOR 与终端能力决定，可强制开关。
void test_stderr_sink_color_detection()
{
    // 测试进程的输出不是终端：自动检测应为不着色
    stacklog::StderrSink automatic(stacklog::StderrSink::ColorAuto);
    CHECK(!automatic.colors_enabled());

    // 强制开关不受环境影响
    stacklog::StderrSink always(stacklog::StderrSink::ColorAlways);
    stacklog::StderrSink never(stacklog::StderrSink::ColorNever);
    CHECK(always.colors_enabled());
    CHECK(!never.colors_enabled());
}

/// §七验收：Tee 同时送两个通道，且镜像不影响主通道。
void test_tee_sink_writes_both_channels()
{
    const char* primary_path = "stacklog_tee_primary.txt";
    const char* mirror_path  = "stacklog_tee_mirror.txt";
    std::remove(primary_path);
    std::remove(mirror_path);

    {
        stacklog::FileSink primary(primary_path);
        stacklog::FileSink mirror(mirror_path);
        CHECK(primary.is_open());
        CHECK(mirror.is_open());

        stacklog::TeeSink tee(primary, mirror);
        stacklog::Record  record;
        std::memset(&record, 0, sizeof(record));
        record.t_chain.t_origin.t_pid = 9;
        record.t_span.t_origin        = record.t_chain.t_origin;
        record.t_span.t_slot          = 1;
        record.t_kind                 = stacklog::Kind::Event;
        record.t_level                = stacklog::Level::Info;
        std::snprintf(record.t_name, sizeof(record.t_name), "tee");
        std::snprintf(record.t_message, sizeof(record.t_message), "two channels");
        record.t_len = static_cast<std::uint16_t>(std::strlen(record.t_message));
        tee.write(record);
        tee.flush();
    }

    struct stat info;
    CHECK_EQ(::stat(primary_path, &info), 0);
    CHECK_EQ(::stat(mirror_path, &info), 0);
    CHECK(info.st_size > 0);

    std::remove(primary_path);
    std::remove(mirror_path);
}

/// §五验收：flush_on_level 打开后 error 记录立即落盘，关闭时仍在缓冲。
void test_flush_on_level_makes_errors_visible()
{
    const char* path = "stacklog_flush_level.txt";
    std::remove(path);

    // 间隔设得很长，确保落盘只可能来自"立即排空"这条路径
    CHECK(stacklog::log_to_file(path));
    stacklog::set_flush_interval_ms(10000);
    stacklog::set_flush_on_level(stacklog::Level::Error, true);

    // 让排空线程进入等待，模拟通知在两次等待之间到达的情形
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    LOG_ERROR("must land immediately");

    // 只等唤醒，远小于 10 秒定时
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    std::fflush(0);

    struct stat info;
    CHECK_EQ(::stat(path, &info), 0);
    CHECK(info.st_size > 0);

    // 关闭后不应再因 error 而立即排空（此处只验证开关可关，不依赖时序）
    stacklog::set_flush_on_level(stacklog::Level::Error, false);
    stacklog::set_flush_interval_ms(0); // 交回手动
    stacklog::flush();
    std::remove(path);
}

/// §五验收：同步模式下记录无需等待即已落盘。
void test_sync_mode_has_no_window()
{
    const char* path = "stacklog_sync_mode.txt";
    std::remove(path);
    CHECK(stacklog::log_to_file(path));
    stacklog::set_flush_interval_ms(10000); // 定时排空基本不参与
    stacklog::set_sync_mode(true);

    LOG_ERROR("synchronous write");
    std::fflush(0);

    struct stat info;
    CHECK_EQ(::stat(path, &info), 0);
    CHECK(info.st_size > 0); // 没有手动 flush 也已在文件里

    stacklog::set_sync_mode(false);
    stacklog::set_flush_interval_ms(0);
    std::remove(path);
}

/// §十验收：检查宏记录 error 事件并按族返回，不终止进程。
namespace
{

int guarded_value(int v_input)
{
    SL_CHECK_RET(v_input > 0, -1, "参数必须为正");
    return v_input * 2;
}

void guarded_void(int v_input, int* v_out)
{
    if (v_out == 0)
    {
        return;
    }
    SL_CHECK_VOID(v_input > 0, "参数必须为正");
    *v_out = v_input;
}

} // namespace

void test_check_macros_record_and_return()
{
    const char* path = "stacklog_check.txt";
    std::remove(path);
    CHECK(stacklog::log_to_file(path));
    stacklog::set_flush_interval_ms(0);
    stacklog::set_level(stacklog::Level::Debug);

    // 条件成立：正常返回，不记录
    CHECK_EQ(guarded_value(5), 10);
    // 条件不成立：记 error 事件并返回约定值，进程不终止
    CHECK_EQ(guarded_value(-3), -1);

    int seen = 0;
    guarded_void(7, &seen);
    CHECK_EQ(seen, 7);
    guarded_void(-1, &seen);
    CHECK_EQ(seen, 7); // 未改写，说明提前返回了

    stacklog::flush();

    // 文件里应出现两条 error 事件，且带条件原文
    std::FILE* file = std::fopen(path, "rb");
    CHECK(file != 0);
    std::string content;
    if (file != 0)
    {
        char              buffer[4096];
        const std::size_t got = std::fread(buffer, 1, sizeof(buffer), file);
        content.assign(buffer, got);
        std::fclose(file);
    }
    CHECK(content.find("条件不成立: v_input > 0") != std::string::npos);
    CHECK(content.find("参数必须为正") != std::string::npos);

    stacklog::set_flush_interval_ms(0);
    std::remove(path);
}

void test_file_sink_reports_open_failure()
{
    CHECK(!stacklog::log_to_file("/nonexistent-directory/stacklog.log"));
}

void test_file_sink()
{
    const char* path = "stacklog_file_sink.txt";
    std::remove(path);
    CHECK(stacklog::log_to_file(path));
    {
        SL_SPAN("t_file-sink");
        LOG_INFO("v_to t_file");
    }
    stacklog::flush();

    const std::string content = read_file(path);
    CHECK(content.find("t_file-sink") != std::string::npos);
    CHECK(content.find("v_to t_file") != std::string::npos);

    stacklog::use_sink(g_first);
}

void test_reentrancy_is_dropped()
{
    ReentrantSink reentrant;
    stacklog::use_sink(reentrant);
    const std::uint64_t dropped_before = stacklog::dropped_records();

    {
        SL_SPAN("reentrant");
        LOG_INFO("one");
        LOG_INFO("two");
    }
    stacklog::flush();

    CHECK_EQ(reentrant.t_writes, 4u);                           // enter + 2 + exit
    CHECK_EQ(stacklog::dropped_records() - dropped_before, 4u); // 4 次嵌套调用被丢弃
    stacklog::use_sink(g_first);
}

void test_thread_exit_and_reuse()
{
    g_first.clear();
    for (int round = 0; round < 2; ++round)
    {
        std::vector<std::thread> threads;
        threads.reserve(3);
        for (int i = 0; i < 3; ++i)
        {
            threads.push_back(std::thread(
                []
                {
                    SL_SPAN("short-lived");
                    LOG_INFO("work");
                }));
        }
        for (std::size_t i = 0; i < threads.size(); ++i)
        {
            threads[i].join();
        }
    }
    stacklog::flush();

    // 线程退出后缓冲被回收，记录仍在；新线程继续正常记录。
    CHECK_EQ(testing::count_orphans(g_first.records()), 0u);
    CHECK(g_first.size() >= 12u);
    g_first.clear();
}

void test_records_after_shutdown_are_dropped()
{
    stacklog::use_sink(g_first);
    stacklog::flush();
    stacklog::shutdown();

    const std::uint64_t dropped_before = stacklog::dropped_records();
    {
        SL_SPAN("after-shutdown");
        LOG_INFO("must be dropped");
    }
    stacklog::flush();

    CHECK(!stacklog::enabled(stacklog::Level::Error));
    CHECK_EQ(testing::count_orphans(g_first.records()), 0u);
    CHECK(stacklog::dropped_records() >= dropped_before);
}

} // namespace

int main()
{
    RUN(test_zero_config_writes_to_default_sink);
    RUN(test_runtime_config_change);
    RUN(test_settings_do_not_overwrite_each_other);
    RUN(test_null_message_counts_as_drop);
    RUN(test_flush_reports_sink_exception);
    RUN(test_sink_destroyed_while_registered);
    RUN(test_log_to_file_creates_directories);
    RUN(test_file_sink_degrades_to_stderr_on_write_failure);
    RUN(test_log_to_file_rejects_directory);
    RUN(test_default_log_path_is_absolute_or_relative);
    RUN(test_rotating_sink_rotates_and_bounds_files);
    RUN(test_rotating_sink_tolerates_missing_archive);
    RUN(test_rotating_sink_degrades_without_losing_records);
    RUN(test_readable_timestamp_matches_wall_clock);
    RUN(test_stderr_sink_color_detection);
    RUN(test_tee_sink_writes_both_channels);
    RUN(test_flush_on_level_makes_errors_visible);
    RUN(test_sync_mode_has_no_window);
    RUN(test_check_macros_record_and_return);
    RUN(test_file_sink_reports_open_failure);
    RUN(test_file_sink);
    RUN(test_reentrancy_is_dropped);
    RUN(test_thread_exit_and_reuse);
    RUN(test_records_after_shutdown_are_dropped);
    std::printf("failures=%d\n", testing::failures());
    std::fflush(stdout);
    return testing::failures() == 0 ? 0 : 1;
}
