#include <thread>
#include <vector>

#include "test_util.hpp"

namespace
{

testing::RecordingSink g_sink;

void                   worker_attach(stacklog::CapturedSpan v_carried)
{
    stacklog::ScopedSpan guard = stacklog::attach_span(v_carried, "worker", SL_HERE);
    stacklog::log(stacklog::Level::Info, "done");
}

void test_cross_thread_tree()
{
    stacklog::use_sink(g_sink);
    stacklog::set_level(stacklog::Level::Info);
    g_sink.clear();

    stacklog::SpanId  dispatch_span;
    stacklog::ChainId chain;
    {
        stacklog::ScopedSpan request = stacklog::start_span("request", SL_HERE);
        {
            stacklog::ScopedSpan dispatch        = stacklog::start_span("dispatch", SL_HERE);
            dispatch_span                        = dispatch.span();
            chain                                = stacklog::capture_span().chain();
            const stacklog::CapturedSpan carried = stacklog::capture_span();
            std::thread                  worker(worker_attach, carried); // 按值传递：同步由线程构造保证
            worker.join();
        }
    }
    stacklog::flush();

    const std::vector<stacklog::Record> records = g_sink.records();
    stacklog::Record                    worker;
    CHECK(testing::find_record(records, "worker", stacklog::Kind::Enter, worker));
    CHECK(worker.t_parent == dispatch_span);       // 挂到被搬运的操作之下
    CHECK(worker.t_span != dispatch_span);         // 派生而非迁移
    CHECK(worker.t_chain == chain);                // 链继承
    CHECK_EQ(testing::count_orphans(records), 0u);
}

void test_ghost_parent()
{
    g_sink.clear();

    stacklog::CapturedSpan carried;
    stacklog::SpanId       ended_span;
    stacklog::ChainId      chain;
    {
        stacklog::ScopedSpan ending = stacklog::start_span("ending", SL_HERE);
        ended_span                  = ending.span();
        carried                     = stacklog::capture_span();
        chain                       = carried.chain();
    }

    std::thread attached_thread(worker_attach, carried); // 父操作已结束
    attached_thread.join();
    stacklog::flush();

    const std::vector<stacklog::Record> records = g_sink.records();
    stacklog::Record                    attached;
    CHECK(testing::find_record(records, "worker", stacklog::Kind::Enter, attached));
    CHECK(attached.t_parent == ended_span);
    CHECK(attached.t_chain == chain);
    CHECK_EQ(testing::count_orphans(records), 0u); // 父的 enter 记录仍在，树可重建
}

void test_attach_without_span_is_null()
{
    const stacklog::CapturedSpan empty = stacklog::capture_span();
    CHECK(stacklog::is_null(empty.chain()));

    stacklog::ScopedSpan guard = stacklog::attach_span(empty, "orphan", SL_HERE);
    CHECK(!stacklog::is_null(guard.span()));
    CHECK(!stacklog::is_null(stacklog::capture_span().chain())); // 无操作可接时自开一条链
}

void test_threads_get_distinct_slots()
{
    g_sink.clear();
    std::vector<std::thread> threads;
    threads.reserve(4);
    for (int i = 0; i < 4; ++i)
    {
        threads.push_back(std::thread(
            []
            {
                stacklog::ScopedSpan guard = stacklog::start_span("t_thread", SL_HERE);
                stacklog::log(stacklog::Level::Info, "hello");
            }));
    }
    for (std::size_t i = 0; i < threads.size(); ++i)
    {
        threads[i].join();
    }
    stacklog::flush();

    const std::vector<stacklog::Record> records = g_sink.records();
    std::set<unsigned long long>        slots;
    for (std::size_t i = 0; i < records.size(); ++i)
    {
        slots.insert(records[i].t_slot);
    }
    CHECK(slots.size() >= 2u);                   // 至少两个线程各自分到槽
    CHECK(slots.count(0u) == 0u);                // 槽号从 1 起
    CHECK_EQ(testing::count_orphans(records), 0u);
}

} // namespace

int main()
{
    RUN(test_cross_thread_tree);
    RUN(test_ghost_parent);
    RUN(test_attach_without_span_is_null);
    RUN(test_threads_get_distinct_slots);
    std::printf("failures=%d\n", testing::failures());
    return testing::failures() == 0 ? 0 : 1;
}
