// §九验收：编译期裁剪只作用于消息宏，结构记录不受影响。
//
// 本文件在两套宏定义下各编译一次（见 tests/CMakeLists.txt）：
// 一次不裁剪，一次 STACKLOG_MIN_LEVEL=warn。两次都必须产生成对的
// enter/exit，裁剪版还不得残留被裁掉的格式串。
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "test_util.hpp"

#include <stacklog/io/record_file.hpp>
#include <stacklog/stacklog.hpp>
#include <stacklog/tree/tree.hpp>

namespace
{

void inner_function()
{
    SL_FUNCTION();
    LOG_DEBUG("trimmed-debug-message %d", 1);
    LOG_INFO("trimmed-info-message %d", 2);
    LOG_WARN("kept-warn-message %d", 3);
}

/// 读回一个记录文件。参数化路径，避免不同用例互相读到对方的文件。
std::vector<stacklog::Record> collect(const char* v_path)
{
    stacklog::RecordFileReader    reader(v_path);
    stacklog::Record              record;
    std::vector<stacklog::Record> records;
    while (reader.next(record))
    {
        records.push_back(record);
    }
    return records;
}

/// 结构记录必须成对：每个 enter 有对应的 exit，树不含未闭合节点。
void test_structure_survives_trimming()
{
    std::remove("stacklog_trim.bin");
    {
        stacklog::RecordFileSink sink("stacklog_trim.bin");
        CHECK(sink.is_open());
        stacklog::use_sink(sink);
        stacklog::set_flush_interval_ms(0);
        {
            SL_SPAN("outer");
            inner_function();
        }
        stacklog::flush();
    }

    const std::vector<stacklog::Record> records = collect("stacklog_trim.bin");
    std::size_t                         enters  = 0;
    std::size_t                         exits   = 0;
    for (std::size_t i = 0; i < records.size(); ++i)
    {
        if (records[i].t_kind == stacklog::Kind::Enter)
        {
            ++enters;
        }
        else if (records[i].t_kind == stacklog::Kind::Exit)
        {
            ++exits;
        }
    }
    // 无论是否裁剪，两个作用域的 enter/exit 都必须在
    CHECK_EQ(enters, 2u);
    CHECK_EQ(exits, 2u);

    stacklog::TreeBuilder      tree;
    stacklog::RecordFileReader reader("stacklog_trim.bin");
    stacklog::Record           record;
    while (reader.next(record))
    {
        tree.add(record);
    }
    tree.build();

    // 树完整：没有未闭合节点，节点数与根都对
    CHECK_EQ(tree.open_count(), 0u);
    CHECK_EQ(tree.nodes().size(), 2u);
    CHECK_EQ(tree.roots().size(), 1u);
    std::remove("stacklog_trim.bin");
}

/// 被裁掉的级别：宏实参不求值。用一个会崩溃的表达式当实参来证明这一点。
void test_trimmed_arguments_are_not_evaluated()
{
    // 下面这行在阈值为 warn 时整行消失，因此 *pointer 不会被求值；
    // 阈值为 debug 时不会走到这里（见 CMake 中本目标的定义）。
#if STACKLOG_MIN_LEVEL_IS_WARN
    const int* pointer = 0;
    LOG_DEBUG("would crash %d", *pointer);
    LOG_INFO("would crash %d", *pointer);
#endif
    CHECK(true);
}

/// 保留的级别照常产生事件。
void test_kept_levels_still_produce_events()
{
    std::remove("stacklog_trim_kept.bin");
    {
        stacklog::RecordFileSink sink("stacklog_trim_kept.bin");
        stacklog::use_sink(sink);
        stacklog::set_flush_interval_ms(0);
        LOG_WARN("kept-warn-message %d", 3);
        stacklog::flush();
    }

    const std::vector<stacklog::Record> records = collect("stacklog_trim_kept.bin");
    std::size_t                         events  = 0;
    for (std::size_t i = 0; i < records.size(); ++i)
    {
        if (records[i].t_kind == stacklog::Kind::Event)
        {
            ++events;
        }
    }
    CHECK_EQ(events, 1u);
    std::remove("stacklog_trim_kept.bin");
}

} // namespace

int main()
{
    RUN(test_structure_survives_trimming);
    RUN(test_trimmed_arguments_are_not_evaluated);
    RUN(test_kept_levels_still_produce_events);
    return testing::failures() == 0 ? 0 : 1;
}
