#include <stacklog/tree/tree_builder.hpp>

namespace stacklog
{

SpanNode::SpanNode(): t_span(), t_parent(), t_start(0), t_end(0), t_closed(false) {}

std::uint64_t duration_ns(const SpanNode& v_node)
{
    if (!v_node.t_closed || v_node.t_start == 0 || v_node.t_end < v_node.t_start)
    {
        return 0;
    }
    return v_node.t_end - v_node.t_start;
}

TreeBuilder::TreeBuilder(): m_linked(false), m_conflicts(0) {}

void TreeBuilder::clear()
{
    m_nodes.clear();
    m_marks.clear();
    m_index.clear();
    m_roots.clear();
    m_orphans.clear();
    m_linked    = false;
    m_conflicts = 0;
}

void TreeBuilder::add(const Record& v_record)
{
    if (is_null(v_record.t_span))
    {
        return;
    }

    const std::string                            key   = to_string(v_record.t_span);
    std::map<std::string, std::size_t>::iterator found = m_index.find(key);
    if (found == m_index.end())
    {
        SpanNode node;
        node.t_span   = v_record.t_span;
        node.t_parent = v_record.t_parent;
        m_nodes.push_back(node);
        Marks marks;
        marks.t_seen_enter = false;
        marks.t_seen_exit  = false;
        m_marks.push_back(marks);
        m_index[key] = m_nodes.size() - 1;
        found        = m_index.find(key);
    }

    const std::size_t at    = found->second;
    SpanNode&         node  = m_nodes[at];
    Marks&            marks = m_marks[at];

    // 操作名与父引用以先到者为准：同一条记录重复写出时它们不会变，
    // 真出现不同值属于数据异常，计入冲突而不是悄悄改写树形。
    if (node.t_name.empty())
    {
        node.t_name = v_record.t_name;
    }
    if (v_record.t_kind == Kind::Enter)
    {
        if (marks.t_seen_enter)
        {
            ++m_conflicts;
        }
        marks.t_seen_enter = true;
        if (node.t_start == 0 || v_record.t_ts_steady < node.t_start)
        {
            node.t_start = v_record.t_ts_steady;
        }
    }
    else if (v_record.t_kind == Kind::Exit)
    {
        if (marks.t_seen_exit)
        {
            ++m_conflicts;
        }
        marks.t_seen_exit = true;
        node.t_closed     = true;
        node.t_end        = v_record.t_ts_steady;
    }
    else
    {
        SpanEvent event;
        event.t_message   = v_record.t_message;
        event.t_ts_steady = v_record.t_ts_steady;
        event.t_level     = v_record.t_level;
        event.t_code      = v_record.t_code;
        node.t_events.push_back(event);
    }

    m_linked = false;
}

void TreeBuilder::link()
{
    m_roots.clear();
    m_orphans.clear();
    for (std::size_t i = 0; i < m_nodes.size(); ++i)
    {
        m_nodes[i].t_children.clear();
    }

    // 先判定每个节点的层级归属，再连边。父引用指向自己或构成环的记录属于
    // 损坏数据：既不能当根、也不能连边，否则渲染会无限递归。这类节点按孤儿处理。
    std::vector<int> state(m_nodes.size(), 0); // 0 未定，1 已定
    for (std::size_t i = 0; i < m_nodes.size(); ++i)
    {
        const SpanId& parent = m_nodes[i].t_parent;
        if (is_null(parent))
        {
            m_roots.push_back(i);
            state[i] = 1;
            continue;
        }
        const std::map<std::string, std::size_t>::const_iterator found = m_index.find(to_string(parent));
        if (found == m_index.end() || found->second == i)
        {
            // 父记录缺失，或父指向自己：按孤儿保留，子树仍挂在它下面。
            m_orphans.push_back(i);
            state[i] = 1;
            continue;
        }
    }

    // 连边前先排除会成环的父引用：沿父链走，若回到起点就说明成环。
    for (std::size_t i = 0; i < m_nodes.size(); ++i)
    {
        if (state[i] == 1)
        {
            continue;
        }
        bool        cyclic = false;
        std::size_t walk   = i;
        std::size_t steps  = 0;
        while (steps <= m_nodes.size())
        {
            const std::map<std::string, std::size_t>::const_iterator up =
                m_index.find(to_string(m_nodes[walk].t_parent));
            if (up == m_index.end() || is_null(m_nodes[walk].t_parent))
            {
                break;
            }
            walk = up->second;
            if (walk == i)
            {
                cyclic = true;
                break;
            }
            ++steps;
        }
        if (cyclic)
        {
            m_orphans.push_back(i);
            state[i] = 1;
        }
    }

    for (std::size_t i = 0; i < m_nodes.size(); ++i)
    {
        if (state[i] == 1)
        {
            continue;
        }
        const std::map<std::string, std::size_t>::const_iterator found = m_index.find(to_string(m_nodes[i].t_parent));
        m_nodes[found->second].t_children.push_back(i);
    }
}

void TreeBuilder::sort_children()
{
    // 插入排序：子节点数量通常很小，且要求与输入顺序无关。
    for (std::size_t i = 0; i < m_nodes.size(); ++i)
    {
        std::vector<std::size_t>& kids = m_nodes[i].t_children;
        for (std::size_t a = 1; a < kids.size(); ++a)
        {
            const std::size_t   value = kids[a];
            const std::uint64_t key   = m_nodes[value].t_start;
            std::size_t         b     = a;
            while (b > 0 && m_nodes[kids[b - 1]].t_start > key)
            {
                kids[b] = kids[b - 1];
                --b;
            }
            kids[b] = value;
        }
    }

    for (std::size_t a = 1; a < m_roots.size(); ++a)
    {
        const std::size_t   value = m_roots[a];
        const std::uint64_t key   = m_nodes[value].t_start;
        std::size_t         b     = a;
        while (b > 0 && m_nodes[m_roots[b - 1]].t_start > key)
        {
            m_roots[b] = m_roots[b - 1];
            --b;
        }
        m_roots[b] = value;
    }

    for (std::size_t a = 1; a < m_orphans.size(); ++a)
    {
        const std::size_t   value = m_orphans[a];
        const std::uint64_t key   = m_nodes[value].t_start;
        std::size_t         b     = a;
        while (b > 0 && m_nodes[m_orphans[b - 1]].t_start > key)
        {
            m_orphans[b] = m_orphans[b - 1];
            --b;
        }
        m_orphans[b] = value;
    }
}

void TreeBuilder::build()
{
    if (m_linked)
    {
        return;
    }
    link();
    sort_children();
    m_linked = true;
}

const std::vector<SpanNode>& TreeBuilder::nodes() const
{
    return m_nodes;
}

const SpanNode& TreeBuilder::node(std::size_t v_index) const
{
    return m_nodes[v_index];
}

const std::vector<std::size_t>& TreeBuilder::roots() const
{
    return m_roots;
}

const std::vector<std::size_t>& TreeBuilder::orphans() const
{
    return m_orphans;
}

const SpanNode* TreeBuilder::parent_of(std::size_t v_index) const
{
    const SpanId& parent = m_nodes[v_index].t_parent;
    if (is_null(parent))
    {
        return 0;
    }
    const std::map<std::string, std::size_t>::const_iterator found = m_index.find(to_string(parent));
    if (found == m_index.end())
    {
        return 0;
    }
    return &m_nodes[found->second];
}

std::size_t TreeBuilder::orphan_count() const
{
    return m_orphans.size();
}

std::size_t TreeBuilder::open_count() const
{
    std::size_t count = 0;
    for (std::size_t i = 0; i < m_marks.size(); ++i)
    {
        if (!m_marks[i].t_seen_exit)
        {
            ++count;
        }
    }
    return count;
}

std::size_t TreeBuilder::missing_start_count() const
{
    std::size_t count = 0;
    for (std::size_t i = 0; i < m_marks.size(); ++i)
    {
        if (!m_marks[i].t_seen_enter)
        {
            ++count;
        }
    }
    return count;
}

std::size_t TreeBuilder::conflict_count() const
{
    return m_conflicts;
}

} // namespace stacklog
