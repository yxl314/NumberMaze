#include <string>

#include "test_util.hpp"

namespace
{

testing::RecordingSink g_sink;

void                   test_render_parse_round_trip()
{
    stacklog::ChainId chain;
    chain.t_origin.t_pid   = 4211;
    chain.t_origin.t_nonce = 0x9f3a2c17u;
    chain.t_seq            = 18446744073709551615ull;

    stacklog::ChainId parsed_chain;
    CHECK(stacklog::parse_chain_id(stacklog::to_string(chain).c_str(), parsed_chain));
    CHECK(parsed_chain == chain);
    CHECK(stacklog::to_string(chain) == "P-4211-9f3a2c17-18446744073709551615");

    stacklog::SpanId span;
    span.t_origin.t_pid   = 7;
    span.t_origin.t_nonce = 0x0000beefu;
    span.t_slot           = 65535;
    span.t_seq            = 9007199254740993ull;

    stacklog::SpanId parsed_span;
    CHECK(stacklog::parse_span_id(stacklog::to_string(span).c_str(), parsed_span));
    CHECK(parsed_span == span);
    CHECK(stacklog::to_string(span) == "OP-7-0000beef-65535-9007199254740993");
}

void test_parse_rejects_malformed()
{
    stacklog::ChainId chain;
    CHECK(!stacklog::parse_chain_id("", chain));
    CHECK(!stacklog::parse_chain_id("Q-1-00000000-1", chain));
    CHECK(!stacklog::parse_chain_id("P-1-0000000-1", chain));
    CHECK(!stacklog::parse_chain_id("P-1-00000000", chain));
    CHECK(!stacklog::parse_chain_id("P-1-00000000-1 ", chain));
    CHECK(!stacklog::parse_chain_id("P-1-0000000g-1", chain));
    CHECK(!stacklog::parse_chain_id("P-1-9F3A2C17-1", chain));

    stacklog::SpanId span;
    CHECK(!stacklog::parse_span_id("P-1-00000000-1", span));
    CHECK(!stacklog::parse_span_id("OP-1-00000000-0", span));
    CHECK(!stacklog::parse_span_id("OP-1-00000000-0-1x", span));
}

void test_null_semantics()
{
    stacklog::ChainId chain;
    chain.t_origin.t_pid   = 0;
    chain.t_origin.t_nonce = 0;
    chain.t_seq            = 0;
    CHECK(stacklog::is_null(chain));
    chain.t_seq = 1;
    CHECK(!stacklog::is_null(chain));

    stacklog::SpanId span;
    span.t_origin.t_pid   = 0;
    span.t_origin.t_nonce = 0;
    span.t_slot           = 0;
    span.t_seq            = 0;
    CHECK(stacklog::is_null(span));
    span.t_slot = 1;
    CHECK(!stacklog::is_null(span));
}

void test_spans_get_distinct_ids()
{
    stacklog::use_sink(g_sink);
    stacklog::set_level(stacklog::Level::Debug);

    stacklog::SpanId  first_span;
    stacklog::ChainId first_chain;
    stacklog::SpanId  second_span;
    {
        stacklog::ScopedSpan first  = stacklog::start_span("first", SL_HERE);
        first_span                  = first.span();
        first_chain                 = stacklog::capture_span().chain();
        stacklog::ScopedSpan nested = stacklog::start_span("nested", SL_HERE);
        second_span                 = nested.span();
        CHECK(stacklog::capture_span().chain() == first_chain); // 子操作继承链
    }

    stacklog::ChainId second_chain;
    {
        stacklog::ScopedSpan other = stacklog::start_span("v_other", SL_HERE);
        second_chain               = stacklog::capture_span().chain();
    }

    CHECK(first_span != second_span);
    CHECK(first_span.t_slot == second_span.t_slot);       // 同线程槽号一致
    CHECK(second_span.t_seq > first_span.t_seq);
    CHECK(!(first_chain == second_chain));            // 各自开链

    stacklog::flush();
    CHECK_EQ(testing::count_spans(g_sink.records()), 3u); // first、nested、other
    CHECK_EQ(testing::count_orphans(g_sink.records()), 0u);
    g_sink.clear();
}

} // namespace

int main()
{
    RUN(test_render_parse_round_trip);
    RUN(test_parse_rejects_malformed);
    RUN(test_null_semantics);
    RUN(test_spans_get_distinct_ids);
    std::printf("failures=%d\n", testing::failures());
    return testing::failures() == 0 ? 0 : 1;
}
