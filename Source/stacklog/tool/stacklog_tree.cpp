// 记录文件 → 调用链树。只做后期分析，不参与记录产生。
//
// 用法：stacklog_tree <记录文件> [--format indent|json] [--min-duration-us N] [--chain <链编号>]
#include <cstdio>
#include <iostream>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <stacklog/stacklog.hpp>
#include <stacklog/tree/tree.hpp>

namespace
{

void usage(std::FILE* v_out)
{
    std::fputs("用法: stacklog_tree <记录文件> [选项]\n", v_out);
    std::fputs("  --format indent|json     输出形状，默认 indent\n", v_out);
    std::fputs("  --min-duration-us N      只输出时长不小于 N 微秒的节点及其子树\n", v_out);
    std::fputs("  --chain <链编号>          只看指定链，可重复\n", v_out);
    std::fputs("  --summary-only           只打印数据质量摘要\n", v_out);
}

/// 链过滤与时长过滤：任一不满足即整棵子树不输出。
class Filter
{
public:
    Filter(const std::vector<std::string>& v_chains,
           std::uint64_t                   v_min_duration_us,
           const stacklog::TreeBuilder&    v_tree)
        : m_chains(v_chains), m_min_duration_us(v_min_duration_us), m_tree(v_tree)
    {}

    bool operator()(const stacklog::SpanNode& v_node) const
    {
        if (stacklog::duration_ns(v_node) / 1000 < m_min_duration_us)
        {
            return false;
        }
        if (m_chains.empty())
        {
            return true;
        }
        const std::string chain = stacklog::to_string(v_node.t_span);
        for (std::size_t i = 0; i < m_chains.size(); ++i)
        {
            // 操作 id 形如 OP-<pid>-<nonce>-<slot>-<seq>，链是它的前缀部分。
            if (chain.compare(0, m_chains[i].size(), m_chains[i]) == 0)
            {
                return true;
            }
        }
        return false;
    }

private:
    const std::vector<std::string>& m_chains;
    std::uint64_t                   m_min_duration_us;
    const stacklog::TreeBuilder&    m_tree;
};

} // namespace

int main(int argc, char** argv)
{
    if (argc < 2)
    {
        usage(stderr);
        return 2;
    }

    const char*              path            = argv[1];
    std::string              format          = "indent";
    std::uint64_t            min_duration_us = 0;
    bool                     summary_only    = false;
    std::vector<std::string> chains;

    for (int i = 2; i < argc; ++i)
    {
        if (std::strcmp(argv[i], "--format") == 0 && i + 1 < argc)
        {
            format = argv[++i];
        }
        else if (std::strcmp(argv[i], "--min-duration-us") == 0 && i + 1 < argc)
        {
            min_duration_us = std::strtoull(argv[++i], 0, 10);
        }
        else if (std::strcmp(argv[i], "--chain") == 0 && i + 1 < argc)
        {
            chains.push_back(argv[++i]);
        }
        else if (std::strcmp(argv[i], "--summary-only") == 0)
        {
            summary_only = true;
        }
        else if (std::strcmp(argv[i], "--help") == 0 || std::strcmp(argv[i], "-h") == 0)
        {
            usage(stdout);
            return 0;
        }
        else
        {
            std::fprintf(stderr, "未知选项: %s\n", argv[i]);
            usage(stderr);
            return 2;
        }
    }

    stacklog::RecordFileReader reader(path);
    if (!reader.is_open())
    {
        std::fprintf(stderr, "打不开记录文件: %s\n", path);
        return 1;
    }

    stacklog::TreeBuilder tree;
    stacklog::Record      record;
    std::size_t           read = 0;
    while (reader.next(record))
    {
        // 过滤在连好树之后按节点判断，因此这里全量读入。
        tree.add(record);
        ++read;
    }
    tree.build();

    if (reader.truncated())
    {
        std::fprintf(stderr, "记录文件末尾有残块，已丢弃；已完整部分照常分析\n");
    }
    else if (reader.error()[0] != '\0')
    {
        std::fprintf(stderr, "读入中断: %s\n", reader.error());
    }

    std::fprintf(stderr, "读入 %zu 条记录\n", read);
    std::fprintf(stderr, "%s\n", stacklog::summarize(tree).c_str());

    if (summary_only)
    {
        return 0;
    }

    const Filter filter(chains, min_duration_us, tree);
    if (format == "json")
    {
        stacklog::render_json(std::cout, tree);
    }
    else if (format == "indent")
    {
        if (chains.empty() && min_duration_us == 0)
        {
            stacklog::render_indent(std::cout, tree);
        }
        else
        {
            stacklog::render_indent_filtered(std::cout, tree, filter);
        }
    }
    else
    {
        std::fprintf(stderr, "未知输出形状: %s\n", format.c_str());
        return 2;
    }
    return 0;
}
