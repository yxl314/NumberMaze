#include <map>
#include <set>
#include <string>
#include <thread>
#include <vector>

#include "test_util.hpp"

namespace
{

testing::RecordingSink g_sink;

const int              g_k_threads    = 4;
const int              g_k_iterations = 200;

void                   worker(int v_index)
{
    SL_SPAN("t_thread-root");
    for (int i = 0; i < g_k_iterations; ++i)
    {
        SL_SPAN("iteration");
        LOG_INFO("step %d of %d", v_index, i);
    }
}

struct SpanInfo
{
    std::string t_chain;
    std::string t_parent;
    std::string t_name;
    bool        t_identified;
    int         t_enters;
    int         t_exits;

    SpanInfo(): t_identified(false), t_enters(0), t_exits(0) {}
};

void test_no_loss_no_duplicate_no_orphan()
{
    stacklog::use_sink(g_sink);
    stacklog::set_level(stacklog::Level::Info);
    stacklog::set_ring_capacity(4096); // 足够装下每个线程的全部记录，不应发生丢弃
    stacklog::set_flush_interval_ms(1000);
    g_sink.clear();

    const std::uint64_t      dropped_before = stacklog::dropped_records();

    std::vector<std::thread> threads;
    threads.reserve(g_k_threads);
    for (int i = 0; i < g_k_threads; ++i)
    {
        threads.push_back(std::thread(worker, i));
    }
    for (std::size_t i = 0; i < threads.size(); ++i)
    {
        threads[i].join();
    }
    stacklog::flush();

    const std::vector<stacklog::Record> records  = g_sink.records();
    const std::uint64_t                 expected = static_cast<std::uint64_t>(g_k_threads) * (2 + 3 * g_k_iterations);
    const std::uint64_t                 dropped  = stacklog::dropped_records() - dropped_before;

    CHECK_EQ(records.size() + dropped, expected);
    CHECK_EQ(dropped, 0u);
    CHECK_EQ(testing::count_orphans(records), 0u);
    CHECK_EQ(testing::count_spans(records), static_cast<std::size_t>(g_k_threads) * (1 + g_k_iterations));

    // 同一操作的多条记录必须身份一致，且每个操作恰好一进一出。
    std::map<std::string, SpanInfo> spans;
    std::size_t                     mismatched = 0;
    for (std::size_t i = 0; i < records.size(); ++i)
    {
        SpanInfo&         info   = spans[stacklog::to_string(records[i].t_span)];
        const std::string chain  = stacklog::to_string(records[i].t_chain);
        const std::string parent = stacklog::to_string(records[i].t_parent);
        const std::string name   = records[i].t_name;
        if (!info.t_identified)
        {
            info.t_chain      = chain;
            info.t_parent     = parent;
            info.t_name       = name;
            info.t_identified = true;
        }
        else if (info.t_chain != chain || info.t_parent != parent || info.t_name != name)
        {
            ++mismatched;
        }
        if (records[i].t_kind == stacklog::Kind::Enter)
        {
            ++info.t_enters;
        }
        else if (records[i].t_kind == stacklog::Kind::Exit)
        {
            ++info.t_exits;
        }
    }

    std::size_t unbalanced = 0;
    for (std::map<std::string, SpanInfo>::const_iterator it = spans.begin(); it != spans.end(); ++it)
    {
        if (it->second.t_enters != 1 || it->second.t_exits != 1)
        {
            ++unbalanced;
        }
    }
    CHECK_EQ(unbalanced, 0u);
    CHECK_EQ(mismatched, 0u);
}

void overflow_worker()
{
    SL_SPAN("overflow");
    for (int i = 0; i < 200; ++i)
    {
        LOG_INFO("v_record %d", i);
    }
}

void test_ring_overflow_is_counted()
{
    stacklog::use_sink(g_sink);
    stacklog::set_level(stacklog::Level::Info);
    stacklog::set_ring_capacity(8);    // 故意做小：排空跟不上必然丢弃
    stacklog::set_flush_interval_ms(1000);
    g_sink.clear();

    const std::uint64_t dropped_before = stacklog::dropped_records();
    // 新线程才会按新容量建缓冲：容量在首次记录时确定。
    std::thread         producer(overflow_worker);
    producer.join();
    stacklog::flush();

    const std::uint64_t dropped  = stacklog::dropped_records() - dropped_before;
    const std::size_t   recorded = g_sink.size();
    CHECK(recorded > 0u);
    CHECK(dropped > 0u);
    CHECK_EQ(recorded + dropped, 202u); // enter + 200 + exit
}

} // namespace

int main()
{
    RUN(test_no_loss_no_duplicate_no_orphan);
    RUN(test_ring_overflow_is_counted);
    std::printf("failures=%d\n", testing::failures());
    return testing::failures() == 0 ? 0 : 1;
}
