// 树解析器用例：顺序无关、多叉、跨线程、孤儿、未闭合、多链、截断、时长。
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "test_util.hpp"

#include <stacklog/io/record_file.hpp>
#include <stacklog/tree/tree.hpp>

namespace
{

/// 造一条记录，字段直填，避免依赖库的运行状态。
/// 造一条记录。has_parent 为假时父引用保持全零——只有全零才表示"没有父"，
/// 带来源却序号为 0 的父引用是**指向序号 0 的操作**，不是空。
stacklog::Record make_record(stacklog::Kind v_kind,
                             std::uint64_t  v_chain_seq,
                             std::uint64_t  v_span_seq,
                             bool           v_has_parent,
                             std::uint64_t  v_parent_seq,
                             const char*    v_name,
                             std::uint64_t  v_ts)
{
    stacklog::Record record;
    std::memset(&record, 0, sizeof(record));
    record.t_chain.t_origin.t_pid   = 4211;
    record.t_chain.t_origin.t_nonce = 0x9f3a2c17u;
    record.t_chain.t_seq            = v_chain_seq;
    record.t_span.t_origin          = record.t_chain.t_origin;
    record.t_span.t_slot            = 1;
    record.t_span.t_seq             = v_span_seq;
    if (v_has_parent)
    {
        record.t_parent.t_origin = record.t_chain.t_origin;
        record.t_parent.t_slot   = 1;
        record.t_parent.t_seq    = v_parent_seq;
    }
    record.t_emitter   = record.t_chain.t_origin;
    record.t_kind      = v_kind;
    record.t_level     = stacklog::Level::Info;
    record.t_ts_steady = v_ts;
    std::snprintf(record.t_name, sizeof(record.t_name), "%s", v_name);
    return record;
}

void prepare_file(const char* v_path)
{
    std::remove(v_path);
}

void fill_tree(stacklog::TreeBuilder& v_tree, const std::vector<stacklog::Record>& v_records)
{
    for (std::size_t i = 0; i < v_records.size(); ++i)
    {
        v_tree.add(v_records[i]);
    }
    v_tree.build();
}

std::string render(const stacklog::TreeBuilder& v_tree)
{
    std::ostringstream out;
    stacklog::render_indent(out, v_tree);
    return out.str();
}

/// 一棵三叉树：root 下三个子节点，其中一个还有子节点。
std::vector<stacklog::Record> sample()
{
    std::vector<stacklog::Record> records;
    records.push_back(make_record(stacklog::Kind::Enter, 1, 0, false, 0, "root", 1000));
    records.push_back(make_record(stacklog::Kind::Enter, 1, 1, true, 0, "child-a", 1100));
    records.push_back(make_record(stacklog::Kind::Enter, 1, 2, true, 0, "child-b", 1200));
    records.push_back(make_record(stacklog::Kind::Enter, 1, 3, true, 0, "child-v_c", 1300));
    records.push_back(make_record(stacklog::Kind::Enter, 1, 4, true, 3, "grandchild", 1400));
    records.push_back(make_record(stacklog::Kind::Exit, 1, 4, true, 3, "grandchild", 1500));
    records.push_back(make_record(stacklog::Kind::Exit, 1, 3, true, 0, "child-v_c", 1600));
    records.push_back(make_record(stacklog::Kind::Exit, 1, 2, true, 0, "child-b", 1700));
    records.push_back(make_record(stacklog::Kind::Exit, 1, 1, true, 0, "child-a", 1800));
    records.push_back(make_record(stacklog::Kind::Exit, 1, 0, false, 0, "root", 1900));
    return records;
}

void test_order_independence()
{
    const std::vector<stacklog::Record> records = sample();

    stacklog::TreeBuilder               forward;
    fill_tree(forward, records);

    std::vector<stacklog::Record> reversed(records.rbegin(), records.rend());
    stacklog::TreeBuilder         backward;
    fill_tree(backward, reversed);

    // 用固定步长的确定性置换，避免依赖标准库已废弃的随机洗牌
    std::vector<stacklog::Record> shuffled;
    for (std::size_t step = 3; step > 0; --step)
    {
        for (std::size_t i = step - 1; i < records.size(); i += 3)
        {
            shuffled.push_back(records[i]);
        }
    }
    stacklog::TreeBuilder mixed;
    fill_tree(mixed, shuffled);
    CHECK_EQ(shuffled.size(), records.size());

    CHECK_EQ(render(forward), render(backward));
    CHECK_EQ(render(forward), render(mixed));
    CHECK_EQ(forward.nodes().size(), 5u);
}

void test_multi_fork()
{
    stacklog::TreeBuilder tree;
    fill_tree(tree, sample());

    CHECK_EQ(tree.roots().size(), 1u);
    const stacklog::SpanNode& root = tree.node(tree.roots()[0]);
    CHECK_EQ(root.t_children.size(), 3u);
    // 同级按开始时刻排序，与喂入顺序无关
    CHECK_EQ(tree.node(root.t_children[0]).t_name, std::string("child-a"));
    CHECK_EQ(tree.node(root.t_children[1]).t_name, std::string("child-b"));
    CHECK_EQ(tree.node(root.t_children[2]).t_name, std::string("child-v_c"));
    // child-c 下挂一个孙节点
    CHECK_EQ(tree.node(root.t_children[2]).t_children.size(), 1u);
    const std::size_t grandchild = tree.node(root.t_children[2]).t_children[0];
    CHECK_EQ(tree.node(grandchild).t_name, std::string("grandchild"));
}

void test_orphan_kept()
{
    std::vector<stacklog::Record> records;
    // 父 seq 9 从未出现：节点保留，子树仍挂在它下面
    records.push_back(make_record(stacklog::Kind::Enter, 1, 10, true, 9, "lost-parent", 1000));
    records.push_back(make_record(stacklog::Kind::Enter, 1, 11, true, 10, "still-nested", 1100));
    records.push_back(make_record(stacklog::Kind::Exit, 1, 11, true, 10, "still-nested", 1200));
    records.push_back(make_record(stacklog::Kind::Exit, 1, 10, true, 9, "lost-parent", 1300));

    stacklog::TreeBuilder tree;
    fill_tree(tree, records);

    CHECK_EQ(tree.nodes().size(), 2u);
    CHECK_EQ(tree.orphan_count(), 1u);
    CHECK_EQ(tree.roots().size(), 0u);
    // 子树没有因为缺父而消失
    CHECK_EQ(tree.node(tree.orphans()[0]).t_children.size(), 1u);

    std::ostringstream out;
    stacklog::render_indent(out, tree);
    CHECK(out.str().find("lost-parent") != std::string::npos);
    CHECK(out.str().find("still-nested") != std::string::npos);
}

void test_unclosed()
{
    std::vector<stacklog::Record> records;
    records.push_back(make_record(stacklog::Kind::Enter, 1, 0, false, 0, "no-exit", 1000));
    records.push_back(make_record(stacklog::Kind::Exit, 1, 1, true, 0, "no-enter", 1100));

    stacklog::TreeBuilder tree;
    fill_tree(tree, records);

    CHECK_EQ(tree.open_count(), 1u);     // no-exit 没有 exit
    CHECK_EQ(tree.missing_start_count(), 1u); // no-enter 没有 enter
    // 缺 exit 的节点不给时长
    CHECK_EQ(stacklog::duration_ns(tree.node(0)), 0u);
}

void test_duration()
{
    stacklog::TreeBuilder tree;
    fill_tree(tree, sample());

    const stacklog::SpanNode& root = tree.node(tree.roots()[0]);
    CHECK_EQ(stacklog::duration_ns(root), 900u);
    // 父的时长不小于任一子节点
    for (std::size_t i = 0; i < root.t_children.size(); ++i)
    {
        CHECK(stacklog::duration_ns(tree.node(root.t_children[i])) <= stacklog::duration_ns(root));
    }
}

void test_multiple_chains()
{
    std::vector<stacklog::Record> records;
    records.push_back(make_record(stacklog::Kind::Enter, 1, 0, false, 0, "chain-1", 1000));
    records.push_back(make_record(stacklog::Kind::Exit, 1, 0, false, 0, "chain-1", 1100));
    // 操作 id 由来源进程、线程槽与线程内序号组成，不含链号：
    // 两条链要靠槽号或序号区分，否则会被当成同一个操作。
    stacklog::Record second = make_record(stacklog::Kind::Enter, 2, 0, false, 0, "chain-2", 1200);
    second.t_span.t_slot    = 2; // 父引用必须保持全零，动它就变成"有父"了
    records.push_back(second);
    stacklog::Record second_exit = make_record(stacklog::Kind::Exit, 2, 0, false, 0, "chain-2", 1300);
    second_exit.t_span.t_slot    = 2;
    records.push_back(second_exit);

    stacklog::TreeBuilder tree;
    fill_tree(tree, records);

    CHECK_EQ(tree.roots().size(), 2u);
    CHECK_EQ(tree.node(tree.roots()[0]).t_name, std::string("chain-1"));
    CHECK_EQ(tree.node(tree.roots()[1]).t_name, std::string("chain-2"));
}

void test_duplicate_records_counted()
{
    std::vector<stacklog::Record> records;
    records.push_back(make_record(stacklog::Kind::Enter, 1, 0, false, 0, "dup", 1000));
    records.push_back(make_record(stacklog::Kind::Enter, 1, 0, false, 0, "dup", 1000));

    stacklog::TreeBuilder tree;
    fill_tree(tree, records);

    CHECK_EQ(tree.nodes().size(), 1u);
    CHECK_EQ(tree.conflict_count(), 1u);
}

void test_self_parent_is_not_a_cycle()
{
    std::vector<stacklog::Record> records;
    // 父引用指向自己：不能当根、不能连边，须按孤儿保留而不是消失
    records.push_back(make_record(stacklog::Kind::Enter, 1, 7, true, 7, "t_self-parent", 1000));

    stacklog::TreeBuilder tree;
    fill_tree(tree, records);

    CHECK_EQ(tree.nodes().size(), 1u);
    CHECK_EQ(tree.orphan_count(), 1u);
    CHECK_EQ(tree.roots().size(), 0u);
    // 渲染必须有界：成环会无限递归
    std::ostringstream out;
    stacklog::render_indent(out, tree);
    CHECK(out.str().find("t_self-parent") != std::string::npos);
}

void test_parent_cycle_is_bounded()
{
    std::vector<stacklog::Record> records;
    records.push_back(make_record(stacklog::Kind::Enter, 1, 1, true, 2, "A", 1000));
    records.push_back(make_record(stacklog::Kind::Enter, 1, 2, true, 1, "B", 1100));

    stacklog::TreeBuilder tree;
    fill_tree(tree, records);

    CHECK_EQ(tree.nodes().size(), 2u);
    CHECK_EQ(tree.orphan_count(), 2u);
    std::ostringstream out;
    stacklog::render_indent(out, tree);
    CHECK(out.str().size() < 1000u); // 有界即未无限递归
}

/// §八验收：结构化错误记录含错误码、可读原因与后端原始输出。
void test_structured_error_record()
{
    prepare_file("stacklog_err.bin");
    const char* path = "stacklog_err.bin";

    {
        stacklog::RecordFileSink sink(path);
        CHECK(sink.is_open());
        stacklog::Record record;
        std::memset(&record, 0, sizeof(record));
        record.t_chain.t_origin.t_pid = 5;
        record.t_span.t_origin        = record.t_chain.t_origin;
        record.t_span.t_slot          = 1;
        record.t_kind                 = stacklog::Kind::Event;
        record.t_level                = stacklog::Level::Error;
        record.t_code                 = 0x0000A1B2u;
        std::snprintf(record.t_message, sizeof(record.t_message), "无法打开配置文件");
        record.t_len = static_cast<std::uint16_t>(std::strlen(record.t_message));
        sink.write(record);
        sink.flush();
    }

    stacklog::RecordFileReader reader(path);
    CHECK(reader.header_ok());
    stacklog::Record record;
    CHECK(reader.next(record));
    CHECK_EQ(record.t_code, 0x0000A1B2u);
    CHECK_EQ(std::string(record.t_message), std::string("无法打开配置文件"));

    // 文本渲染要含错误码，且原因与原始输出都在
    std::string line;
    stacklog::format_record(record, line);
    CHECK(line.find("code=0x0000A1B2") != std::string::npos);
    CHECK(line.find("无法打开配置文件") != std::string::npos);

    // 建树后事件也带上错误码，JSON 里有同名字段
    stacklog::TreeBuilder      tree;
    stacklog::RecordFileReader again(path);
    stacklog::Record           item;
    while (again.next(item))
    {
        tree.add(item);
    }
    tree.build();
    CHECK_EQ(tree.nodes().size(), 1u);
    const stacklog::SpanNode& node = tree.node(0);
    CHECK_EQ(node.t_events.size(), 1u);
    CHECK_EQ(node.t_events[0].t_code, 0x0000A1B2u);

    std::ostringstream json;
    stacklog::render_json(json, tree);
    CHECK(json.str().find("\"code\":41394") != std::string::npos); // 0x0000A1B2
    std::remove(path);
}

void test_record_file_roundtrip()
{
    const char* path = "stacklog_records.bin";
    std::remove(path);

    {
        stacklog::RecordFileSink sink(path);
        CHECK(sink.is_open());
        const std::vector<stacklog::Record> records = sample();
        for (std::size_t i = 0; i < records.size(); ++i)
        {
            sink.write(records[i]);
        }
        sink.flush();
    }

    stacklog::RecordFileReader reader(path);
    CHECK(reader.is_open());
    CHECK(!reader.truncated());

    stacklog::TreeBuilder tree;
    stacklog::Record      record;
    std::size_t           count = 0;
    while (reader.next(record))
    {
        CHECK_EQ(record.t_chain.t_seq, 1u);
        tree.add(record);
        ++count;
    }
    tree.build();

    CHECK_EQ(count, 10u);
    CHECK_EQ(tree.nodes().size(), 5u);
    CHECK_EQ(tree.orphan_count(), 0u);
    // 读回后成树的结果与直接喂入一致
    stacklog::TreeBuilder direct;
    fill_tree(direct, sample());
    CHECK_EQ(render(tree), render(direct));
    std::remove(path);
}

/// §二验收：非本库文件、截断头部、旧版本头部都必须被明确拒绝。
void test_header_rejects_foreign_file()
{
    const char* path = "stacklog_records_foreign.bin";
    {
        // 写一个纯文本文件，冒充记录文件
        std::FILE* file = std::fopen(path, "wb");
        CHECK(file != 0);
        std::fputs("P-1 OP-1 OP-0 123 1 info enter foo /tmp/x.cpp:1 hello\n", file);
        std::fclose(file);
    }

    stacklog::RecordFileReader reader(path);
    CHECK(reader.is_open());
    CHECK(!reader.header_ok());
    CHECK(!reader.truncated());
    CHECK(reader.error()[0] != '\0');       // 有明确原因
    stacklog::Record record;
    CHECK(!reader.next(record));            // 不产出任何记录

    // 错误串要指出问题所在，而不是笼统一句失败
    std::printf("    非本库文件: %s\n", reader.error());
    std::remove(path);
}

void test_header_rejects_truncated_header()
{
    const char* path = "stacklog_records_badhdr.bin";
    {
        // 只写头部的前几个字节
        std::FILE* file = std::fopen(path, "wb");
        CHECK(file != 0);
        const std::uint32_t magic = stacklog::g_k_record_file_magic;
        std::fwrite(&magic, sizeof(magic), 1, file);
        std::fclose(file);
    }

    stacklog::RecordFileReader reader(path);
    CHECK(!reader.header_ok());
    CHECK(reader.error()[0] != '\0');
    std::printf("    头部残缺:   %s\n", reader.error());
    std::remove(path);
}

void test_header_rejects_wrong_version()
{
    const char* path = "stacklog_records_oldver.bin";
    {
        // 造一个版本号不同的头部
        std::FILE* file = std::fopen(path, "wb");
        CHECK(file != 0);
        stacklog::RecordFileHeader header;
        header.t_magic          = stacklog::g_k_record_file_magic;
        header.t_format_version = static_cast<std::uint16_t>(stacklog::g_k_record_file_format_version + 1);
        header.t_record_size    = static_cast<std::uint16_t>(sizeof(stacklog::Record));
        header.t_reserved       = 0;
        std::fwrite(&header, sizeof(header), 1, file);
        std::fclose(file);
    }

    stacklog::RecordFileReader reader(path);
    CHECK(!reader.header_ok());
    // 说明里要同时给出期望值与文件里的实际值
    const std::string message = reader.error();
    CHECK(message.find("期望") != std::string::npos);
    CHECK(message.find("文件") != std::string::npos);
    std::printf("    版本不符:   %s\n", message.c_str());
    std::remove(path);
}

void test_header_rejects_wrong_record_size()
{
    const char* path = "stacklog_records_badsize.bin";
    {
        std::FILE* file = std::fopen(path, "wb");
        CHECK(file != 0);
        stacklog::RecordFileHeader header;
        header.t_magic          = stacklog::g_k_record_file_magic;
        header.t_format_version = stacklog::g_k_record_file_format_version;
        header.t_record_size    = static_cast<std::uint16_t>(sizeof(stacklog::Record) + 8);
        header.t_reserved       = 0;
        std::fwrite(&header, sizeof(header), 1, file);
        std::fclose(file);
    }

    stacklog::RecordFileReader reader(path);
    CHECK(!reader.header_ok());
    stacklog::Record record;
    CHECK(!reader.next(record));            // 长度对不上就不该产出记录
    std::printf("    长度不符:   %s\n", reader.error());
    std::remove(path);
}

void test_header_written_once_on_append()
{
    const char* path = "stacklog_records_hdr.bin";
    std::remove(path);
    const std::vector<stacklog::Record> records = sample();
    {
        stacklog::RecordFileSink sink(path);
        CHECK(sink.is_open());
        sink.write(records[0]);
        sink.flush();
    }
    {
        // 再次以追加方式打开：不能再写一个头部，否则读回会在中间撞上它
        stacklog::RecordFileSink sink(path);
        CHECK(sink.is_open());
        for (std::size_t i = 1; i < records.size(); ++i)
        {
            sink.write(records[i]);
        }
        sink.flush();
    }

    stacklog::RecordFileReader reader(path);
    CHECK(reader.header_ok());
    std::size_t      count = 0;
    stacklog::Record record;
    while (reader.next(record))
    {
        ++count;
    }
    CHECK_EQ(count, records.size());
    CHECK(!reader.truncated());
    std::remove(path);
}

void test_truncated_tail_reported()
{
    const char* path = "stacklog_records_trunc.bin";
    std::remove(path);
    {
        stacklog::RecordFileSink            sink(path);
        const std::vector<stacklog::Record> records = sample();
        for (std::size_t i = 0; i < records.size(); ++i)
        {
            sink.write(records[i]);
        }
        sink.flush();
    }
    // 砍掉最后一个块的一部分，模拟写入中途被打断
    {
        std::ifstream     in(path, std::ios::binary);
        std::vector<char> data((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
        in.close();
        data.resize(data.size() - sizeof(stacklog::Record) / 2);
        std::ofstream out(path, std::ios::binary | std::ios::trunc);
        out.write(data.empty() ? 0 : data.data(), static_cast<std::streamsize>(data.size()));
    }

    stacklog::RecordFileReader reader(path);
    std::size_t                count = 0;
    stacklog::Record           record;
    while (reader.next(record))
    {
        ++count;
    }

    CHECK_EQ(count, 9u);         // 完整部分照常读回
    CHECK(reader.truncated());  // 残块被报出
    std::remove(path);
}

} // namespace

int main()
{
    RUN(test_order_independence);
    RUN(test_multi_fork);
    RUN(test_orphan_kept);
    RUN(test_unclosed);
    RUN(test_duration);
    RUN(test_multiple_chains);
    RUN(test_duplicate_records_counted);
    RUN(test_self_parent_is_not_a_cycle);
    RUN(test_parent_cycle_is_bounded);
    RUN(test_structured_error_record);
    RUN(test_record_file_roundtrip);
    RUN(test_header_rejects_foreign_file);
    RUN(test_header_rejects_truncated_header);
    RUN(test_header_rejects_wrong_version);
    RUN(test_header_rejects_wrong_record_size);
    RUN(test_header_written_once_on_append);
    RUN(test_truncated_tail_reported);
    return testing::failures() == 0 ? 0 : 1;
}
