#ifndef INCLUDE_STACKLOG_TREE_TREE_BUILDER_HPP
#define INCLUDE_STACKLOG_TREE_TREE_BUILDER_HPP

#include <cstddef>
#include <map>
#include <string>
#include <vector>

#include <stacklog/core/record.hpp>
#include <stacklog/tree/span_node.hpp>

namespace stacklog
{

/// 记录流到调用链树。
///
/// 树形只由编号决定：同一批记录按任意顺序喂入，得到同一棵树。
/// 因此可以边读文件边喂，也可以先攒起来再喂，不影响结果。
///
/// 不完整的数据显式暴露而不是补全：孤儿（父记录缺失）、未闭合（缺 enter 或 exit）
/// 都保留并计数，残缺的子树不会静默消失。
class TreeBuilder
{
public:
    TreeBuilder();

    /// 喂入一条记录。与调用顺序无关。
    void                            add(const Record& v_record);

    /// 连边并定序。喂完记录后调用一次；重复调用只在有新记录时重做。
    void                            build();

    /// 全部节点，下标可用于 children 与 node()。
    const std::vector<SpanNode>&    nodes() const;

    /// 指定下标的节点。
    const SpanNode&                 node(std::size_t v_index) const;

    /// 根节点下标（父引用为全零），按开始时刻排序。
    const std::vector<std::size_t>& roots() const;

    /// 缺父的节点下标；它们的父操作没有出现在记录里。
    const std::vector<std::size_t>& orphans() const;

    /// 指定节点，或其所在链的父操作。
    const SpanNode*                 parent_of(std::size_t v_index) const;

    /// 缺父的节点数。
    std::size_t                     orphan_count() const;

    /// 没有 exit 记录的节点数。
    std::size_t                     open_count() const;

    /// 缺少 enter 但有 exit 的节点数（时长不可用，但结束时刻仍有效）。
    std::size_t                     missing_start_count() const;

    /// 同一操作出现了互相矛盾的重复记录（如同一操作两次 enter）的次数。
    std::size_t                     conflict_count() const;

    /// 清空，便于复用同一个 builder 分析下一批记录。
    void                            clear();

private:
    /// 连边与统计需要的附加状态，与 nodes_ 同下标。
    struct Marks
    {
        bool t_seen_enter;
        bool t_seen_exit;
    };

    void                               link();
    void                               sort_children();

    std::vector<SpanNode>              m_nodes;
    std::vector<Marks>                 m_marks;
    std::map<std::string, std::size_t> m_index;
    std::vector<std::size_t>           m_roots;
    std::vector<std::size_t>           m_orphans;
    bool                               m_linked;
    std::size_t                        m_conflicts;
};

} // namespace stacklog

#endif // INCLUDE_STACKLOG_TREE_TREE_BUILDER_HPP
