#include <cstring>
#include <stdexcept>
#include <utility>
#include <vector>

#include "test_util.hpp"

namespace
{

testing::RecordingSink g_sink;

std::size_t            count_kind(const std::vector<stacklog::Record>& v_records, stacklog::Kind v_kind)
{
    std::size_t count = 0;
    for (std::size_t i = 0; i < v_records.size(); ++i)
    {
        if (v_records[i].t_kind == v_kind)
        {
            ++count;
        }
    }
    return count;
}

void test_exception_restores_stack()
{
    stacklog::use_sink(g_sink);
    stacklog::set_level(stacklog::Level::Info);
    const stacklog::SpanId before = stacklog::current_span();
    CHECK(stacklog::is_null(before));

    try
    {
        stacklog::ScopedSpan guard = stacklog::start_span("boom", SL_HERE);
        CHECK(!(stacklog::current_span() == before));
        throw std::runtime_error("boom");
    }
    catch (const std::runtime_error&)
    {}

    CHECK(stacklog::current_span() == before);
}

void test_out_of_order_destruction()
{
    stacklog::ScopedSpan*  outer      = new stacklog::ScopedSpan(stacklog::start_span("outer", SL_HERE));
    const stacklog::SpanId outer_span = stacklog::current_span();
    CHECK(!stacklog::is_null(outer_span));

    stacklog::ScopedSpan* inner = new stacklog::ScopedSpan(stacklog::start_span("inner", SL_HERE));
    CHECK(!(stacklog::current_span() == outer_span));

    delete inner;
    // 恢复构造时保存的栈顶，而不是弹掉当时的栈顶。
    CHECK(stacklog::current_span() == outer_span);

    delete outer;
    CHECK(stacklog::is_null(stacklog::current_span()));
}

void test_moved_guard_releases_once()
{
    const stacklog::SpanId before = stacklog::current_span();
    {
        stacklog::ScopedSpan   first      = stacklog::start_span("first", SL_HERE);
        const stacklog::SpanId first_span = stacklog::current_span();
        {
            stacklog::ScopedSpan second = std::move(first);
            CHECK(stacklog::current_span() == first_span);
            CHECK(second.span() == first_span);
        }
        // 移动目标已析构：栈顶回到 first 的父操作，而 first 已不活动，析构不得二次出栈。
        CHECK(!(stacklog::current_span() == first_span));
    }
    CHECK(stacklog::current_span() == before);
}

void test_enter_exit_records_pair_up()
{
    g_sink.clear();
    stacklog::SpanId span;
    {
        stacklog::ScopedSpan guard = stacklog::start_span("paired", SL_HERE);
        span                       = guard.span();
        stacklog::log(stacklog::Level::Info, "inside");
    }
    stacklog::flush();

    const std::vector<stacklog::Record> records = g_sink.records();
    stacklog::Record                    enter;
    stacklog::Record                    exit;
    CHECK(testing::find_record(records, "paired", stacklog::Kind::Enter, enter));
    CHECK(testing::find_record(records, "paired", stacklog::Kind::Exit, exit));
    CHECK(enter.t_span == span);
    CHECK(exit.t_span == span);
    CHECK(exit.t_parent == enter.t_parent);
    CHECK(exit.t_ts_steady >= enter.t_ts_steady);
    CHECK_EQ(count_kind(records, stacklog::Kind::Event), 1u);
}

void test_level_threshold_filters()
{
    stacklog::flush();
    g_sink.clear();
    stacklog::set_level(stacklog::Level::Warn);
    {
        stacklog::ScopedSpan guard = stacklog::start_span("filtered", SL_HERE);
        LOG_DEBUG("dropped by level");
        LOG_INFO("dropped by level");
        LOG_WARN("kept");
    }
    stacklog::flush();

    const std::vector<stacklog::Record> records = g_sink.records();
    // 事件按级别过滤：只留 warn。
    CHECK_EQ(count_kind(records, stacklog::Kind::Event), 1u);
    // 结构记录不受级别影响，否则整棵树消失。
    CHECK_EQ(count_kind(records, stacklog::Kind::Enter), 1u);
    CHECK_EQ(count_kind(records, stacklog::Kind::Exit), 1u);
    // 留下来的事件必须能解析到父操作，否则树无法重建。
    CHECK_EQ(testing::count_orphans(records), 0u);
    for (std::size_t i = 0; i < records.size(); ++i)
    {
        if (records[i].t_kind == stacklog::Kind::Event)
        {
            CHECK(records[i].t_level >= stacklog::Level::Warn);
        }
    }
    stacklog::set_level(stacklog::Level::Info);
}

void test_message_truncation_and_escaping()
{
    g_sink.clear();
    {
        stacklog::ScopedSpan guard = stacklog::start_span("escaped", SL_HERE);
        std::string          long_message(4 * stacklog::g_k_message_capacity, 'x');
        stacklog::log(stacklog::Level::Info, long_message.c_str());
        LOG_INFO("line1\nline2\tend");
    }
    stacklog::flush();

    const std::vector<stacklog::Record> records       = g_sink.records();
    std::size_t                         long_count    = 0;
    std::size_t                         escaped_count = 0;
    for (std::size_t i = 0; i < records.size(); ++i)
    {
        if (records[i].t_kind != stacklog::Kind::Event)
        {
            continue;
        }
        CHECK(records[i].t_len < stacklog::g_k_message_capacity);
        CHECK(records[i].t_message[records[i].t_len] == '\0');
        if (records[i].t_len == stacklog::g_k_message_capacity - 1)
        {
            ++long_count;
        }
        if (std::string(records[i].t_message).find("line1") == 0)
        {
            ++escaped_count;
            std::string rendered;
            stacklog::format_record(records[i], rendered);
            CHECK(rendered.find("line1\\nline2\\tend") != std::string::npos);
        }
    }
    CHECK_EQ(long_count, 1u);
    CHECK_EQ(escaped_count, 1u);
}

void test_chain_starts_at_empty_stack()
{
    // 栈空 → 新链：同一线程里两个相继的作用域各成一条链。
    stacklog::ChainId first_chain;
    stacklog::ChainId second_chain;
    {
        stacklog::ScopedSpan first = stacklog::start_span("chain-a", SL_HERE);
        first_chain                = stacklog::capture_span().chain();
    }
    {
        stacklog::ScopedSpan second = stacklog::start_span("chain-b", SL_HERE);
        second_chain                = stacklog::capture_span().chain();
    }
    CHECK(!(first_chain == second_chain));

    // 栈非空 → 继承栈顶的链。
    stacklog::ChainId outer_chain;
    stacklog::ChainId inner_chain;
    {
        stacklog::ScopedSpan outer = stacklog::start_span("outer", SL_HERE);
        outer_chain                = stacklog::capture_span().chain();
        stacklog::ScopedSpan inner = stacklog::start_span("inner", SL_HERE);
        inner_chain                = stacklog::capture_span().chain();
    }
    CHECK(outer_chain == inner_chain);
    CHECK(!(outer_chain == first_chain));

    // 外层操作跨两轮迭代：两轮落进同一条链——隐式开链的既有后果。
    stacklog::ChainId iterations[2];
    {
        stacklog::ScopedSpan loop = stacklog::start_span("loop", SL_HERE);
        for (int i = 0; i < 2; ++i)
        {
            stacklog::ScopedSpan round = stacklog::start_span("round", SL_HERE);
            iterations[i]              = stacklog::capture_span().chain();
        }
    }
    CHECK(iterations[0] == iterations[1]);
    CHECK(!(iterations[1] == second_chain));
}

void test_temporary_name_is_copied()
{
    stacklog::flush();
    g_sink.clear();
    {
        // 操作名指向临时字符串：语句结束即析构。名字必须已被拷进操作与记录。
        SL_SPAN(std::string("temporary-name").c_str());
        LOG_INFO("inside");
    }
    stacklog::flush();

    const std::vector<stacklog::Record> records = g_sink.records();
    std::size_t                         matched = 0;
    for (std::size_t i = 0; i < records.size(); ++i)
    {
        CHECK(std::string(records[i].t_name) == "temporary-name");
        ++matched;
    }
    CHECK_EQ(matched, 3u);                   // enter + event + exit
}

void test_long_name_is_truncated()
{
    stacklog::flush();
    g_sink.clear();
    std::string long_name(4 * stacklog::g_k_name_capacity, 'n');
    {
        stacklog::ScopedSpan guard = stacklog::start_span(long_name.c_str(), SL_HERE);
    }
    stacklog::flush();

    const std::vector<stacklog::Record> records = g_sink.records();
    CHECK(!records.empty());
    for (std::size_t i = 0; i < records.size(); ++i)
    {
        CHECK_EQ(std::strlen(records[i].t_name), stacklog::g_k_name_capacity - 1);
    }
}

} // namespace

int main()
{
    RUN(test_exception_restores_stack);
    RUN(test_out_of_order_destruction);
    RUN(test_moved_guard_releases_once);
    RUN(test_enter_exit_records_pair_up);
    RUN(test_level_threshold_filters);
    RUN(test_message_truncation_and_escaping);
    RUN(test_chain_starts_at_empty_stack);
    RUN(test_temporary_name_is_copied);
    RUN(test_long_name_is_truncated);
    std::printf("failures=%d\n", testing::failures());
    return testing::failures() == 0 ? 0 : 1;
}
