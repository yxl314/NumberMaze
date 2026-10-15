#include <stacklog/tree/render.hpp>

#include <ostream>
#include <sstream>

namespace stacklog
{

namespace
{

void escape_into(std::ostream& v_out, const std::string& v_text)
{
    for (std::size_t i = 0; i < v_text.size(); ++i)
    {
        const char c = v_text[i];
        switch (c)
        {
        case '"':
            v_out << "\\\"";
            break;
        case '\\':
            v_out << "\\\\";
            break;
        case '\n':
            v_out << "\\n";
            break;
        case '\r':
            v_out << "\\r";
            break;
        case '\t':
            v_out << "\\t";
            break;
        default:
            if (static_cast<unsigned char>(c) < 0x20)
            {
                v_out << "\\u00";
                const char* digits = "0123456789abcdef";
                v_out << digits[(static_cast<unsigned char>(c) >> 4) & 0xF];
                v_out << digits[static_cast<unsigned char>(c) & 0xF];
            }
            else
            {
                v_out << c;
            }
        }
    }
}

void print_node_line(std::ostream& v_out, const TreeBuilder& v_tree, std::size_t v_index, int v_depth);

void walk_indices(const TreeBuilder&              v_tree,
                  const std::vector<std::size_t>& v_indices,
                  int                             v_depth,
                  const NodeVisitor&              v_visit)
{
    for (std::size_t i = 0; i < v_indices.size(); ++i)
    {
        const SpanNode& node = v_tree.node(v_indices[i]);
        v_visit(node, v_depth, v_indices[i]);
        walk_indices(v_tree, node.t_children, v_depth + 1, v_visit);
    }
}

void indent(std::ostream& v_out, int v_depth)
{
    for (int i = 0; i < v_depth; ++i)
    {
        v_out << "  ";
    }
}

/// 只打印一个节点自身这一行，不递归。
void print_node_line(std::ostream& v_out, const TreeBuilder& v_tree, std::size_t v_index, int v_depth)
{
    const SpanNode& node = v_tree.node(v_index);
    indent(v_out, v_depth);
    v_out << (node.t_name.empty() ? "(无名)" : node.t_name);
    if (node.t_closed && duration_ns(node) != 0)
    {
        v_out << "  " << duration_ns(node) / 1000 << "us";
    }
    else if (!node.t_closed)
    {
        v_out << "  (未闭合)";
    }
    for (std::size_t i = 0; i < node.t_events.size(); ++i)
    {
        v_out << "  [" << level_name(node.t_events[i].t_level) << "] ";
        escape_into(v_out, node.t_events[i].t_message);
    }
    v_out << "\n";
}

void render_json_node(std::ostream& v_out, const TreeBuilder& v_tree, std::size_t v_index, int v_depth)
{
    const SpanNode& node = v_tree.node(v_index);
    indent(v_out, v_depth);
    v_out << "{\"name\":\"";
    escape_into(v_out, node.t_name);
    v_out << "\",\"span\":\"" << to_string(node.t_span) << "\"";
    v_out << ",\"parent\":\"" << to_string(node.t_parent) << "\"";
    v_out << ",\"duration_us\":" << (duration_ns(node) / 1000);
    v_out << ",\"closed\":" << (node.t_closed ? "true" : "false");
    v_out << ",\"events\":[";
    for (std::size_t i = 0; i < node.t_events.size(); ++i)
    {
        if (i != 0)
        {
            v_out << ",";
        }
        v_out << "{\"level\":\"" << level_name(node.t_events[i].t_level) << "\",\"code\":" << node.t_events[i].t_code
              << ",\"message\":\"";
        escape_into(v_out, node.t_events[i].t_message);
        v_out << "\"}";
    }
    v_out << "]";
    if (node.t_children.empty())
    {
        v_out << "}";
        return;
    }
    v_out << ",\"children\":[\n";
    for (std::size_t i = 0; i < node.t_children.size(); ++i)
    {
        render_json_node(v_out, v_tree, node.t_children[i], v_depth + 1);
        v_out << (i + 1 == node.t_children.size() ? "\n" : ",\n");
    }
    indent(v_out, v_depth);
    v_out << "]}";
}

} // namespace

void walk_tree(const TreeBuilder& v_tree, const NodeVisitor& v_visit)
{
    walk_indices(v_tree, v_tree.roots(), 0, v_visit);
    // 缺父的子树单独列出，避免因为父记录缺失而整棵消失。
    walk_indices(v_tree, v_tree.orphans(), 1, v_visit);
}

bool accept_all(const SpanNode& v_node)
{
    (void)v_node;
    return true;
}

namespace
{

void render_node_filtered(
    std::ostream& v_out, const TreeBuilder& v_tree, std::size_t v_index, int v_depth, const NodeFilter& v_filter)
{
    if (!v_filter(v_tree.node(v_index)))
    {
        return;
    }
    print_node_line(v_out, v_tree, v_index, v_depth);
    const SpanNode& node = v_tree.node(v_index);
    for (std::size_t i = 0; i < node.t_children.size(); ++i)
    {
        render_node_filtered(v_out, v_tree, node.t_children[i], v_depth + 1, v_filter);
    }
}

} // namespace

void render_indent(std::ostream& v_out, const TreeBuilder& v_tree)
{
    render_indent_filtered(v_out, v_tree, &accept_all);
}

void render_indent_filtered(std::ostream& v_out, const TreeBuilder& v_tree, const NodeFilter& v_filter)
{
    for (std::size_t i = 0; i < v_tree.roots().size(); ++i)
    {
        render_node_filtered(v_out, v_tree, v_tree.roots()[i], 0, v_filter);
    }
    if (!v_tree.orphans().empty())
    {
        v_out << "(父操作缺失，共 " << v_tree.orphans().size() << " 个)\n";
        for (std::size_t i = 0; i < v_tree.orphans().size(); ++i)
        {
            render_node_filtered(v_out, v_tree, v_tree.orphans()[i], 1, v_filter);
        }
    }
}

void render_json(std::ostream& v_out, const TreeBuilder& v_tree)
{
    v_out << "{\n";
    v_out << "  \"summary\": \"" << summarize(v_tree) << "\",\n";
    v_out << "  \"roots\": [";
    if (v_tree.roots().empty())
    {
        v_out << "],\n";
    }
    else
    {
        v_out << "\n";
        for (std::size_t i = 0; i < v_tree.roots().size(); ++i)
        {
            render_json_node(v_out, v_tree, v_tree.roots()[i], 2);
            v_out << (i + 1 == v_tree.roots().size() ? "\n" : ",\n");
        }
        v_out << "  ],\n";
    }
    v_out << "  \"orphans\": [";
    if (v_tree.orphans().empty())
    {
        v_out << "]\n";
    }
    else
    {
        v_out << "\n";
        for (std::size_t i = 0; i < v_tree.orphans().size(); ++i)
        {
            render_json_node(v_out, v_tree, v_tree.orphans()[i], 2);
            v_out << (i + 1 == v_tree.orphans().size() ? "\n" : ",\n");
        }
        v_out << "  ]\n";
    }
    v_out << "}\n";
}

std::string summarize(const TreeBuilder& v_tree)
{
    std::ostringstream out;
    out << "节点 " << v_tree.nodes().size();
    out << "，根 " << v_tree.roots().size();
    out << "，孤儿 " << v_tree.orphan_count();
    out << "，未闭合 " << v_tree.open_count();
    out << "，缺开始 " << v_tree.missing_start_count();
    out << "，冲突 " << v_tree.conflict_count();
    return out.str();
}

} // namespace stacklog
