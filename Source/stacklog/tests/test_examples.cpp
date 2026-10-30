#include <cstdio>
#include <thread>

#include "test_util.hpp"

namespace
{

testing::RecordingSink g_sink;

/// 接续搬运过来的操作后记录：跨线程派生的最小用例。
void                   worker(stacklog::CapturedSpan v_carried)
{
    SL_ATTACH(v_carried, "worker");
    LOG_INFO("done");
}

/// 统计写入条数并原样打印消息的自定义 sink。
class CountingSink : public stacklog::Sink
{
public:
    CountingSink(): m_count(0) {}

    void write(const stacklog::Record& v_record)
    {
        ++m_count;
        std::fwrite(v_record.t_message, 1, v_record.t_len, stdout);
        std::fputc('\n', stdout);
    }
    void        flush() {}

    std::size_t count() const
    {
        return m_count;
    }

private:
    std::size_t m_count;
};

int compute(int v_n)
{
    SL_FUNCTION();
    LOG_DEBUG("n=%d", v_n);
    return v_n * 2;
}

void test_minimal_example()
{
    stacklog::use_sink(g_sink);
    stacklog::set_level(stacklog::Level::Debug);
    g_sink.clear();

    {
        SL_SPAN("request");
        CHECK_EQ(compute(3), 6);
    }
    stacklog::flush();

    CHECK_EQ(testing::count_spans(g_sink.records()), 2u); // request、compute
    CHECK_EQ(testing::count_orphans(g_sink.records()), 0u);
}

void test_cross_thread_example()
{
    g_sink.clear();
    {
        SL_SPAN("dispatch");
        const stacklog::CapturedSpan carried = stacklog::capture_span();
        std::thread                  thread(worker, carried);
        thread.join();
    }
    stacklog::flush();

    stacklog::Record attached;
    CHECK(testing::find_record(g_sink.records(), "worker", stacklog::Kind::Enter, attached));
    CHECK(!stacklog::is_null(attached.t_parent));
}

void test_custom_sink_example()
{
    CountingSink counting;
    stacklog::use_sink(counting);
    {
        SL_SPAN("counting");
        LOG_INFO("hello");
    }
    stacklog::flush();

    CHECK(counting.count() >= 3u);
    stacklog::use_sink(g_sink);
}

void test_function_scope_naming()
{
    g_sink.clear();
    compute(1);
    stacklog::flush();

    stacklog::Record named;
    CHECK(testing::find_record(g_sink.records(), "compute", stacklog::Kind::Enter, named));
    CHECK(named.t_loc.t_line > 0);
    CHECK(named.t_loc.t_file != 0);
}

} // namespace

int main()
{
    RUN(test_minimal_example);
    RUN(test_cross_thread_example);
    RUN(test_custom_sink_example);
    RUN(test_function_scope_naming);
    std::printf("failures=%d\n", testing::failures());
    return testing::failures() == 0 ? 0 : 1;
}
