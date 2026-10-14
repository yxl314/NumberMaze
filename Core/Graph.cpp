#include "Graph.h"

#include <algorithm>

namespace core {

/**
 * @brief 构造拓扑，登记本项目设计的全部连接关系。
 */
Graph::Graph()
{
    // 本项目的连接关系（使用 1-based 编号书写，便于与设计稿对照）。
    // 其中 (1,6)、(3,8) 等为对角线连接，是难度的主要来源。
    addEdge(1, 5);  addEdge(1, 6);  addEdge(1, 2);
    addEdge(2, 5);  addEdge(2, 7);
    addEdge(3, 6);  addEdge(3, 8);  addEdge(3, 4);
    addEdge(4, 7);  addEdge(4, 8);
    addEdge(6, 11);
    addEdge(7, 10);
    addEdge(9, 13); addEdge(9, 14);
    addEdge(10, 13); addEdge(10, 15);
    addEdge(11, 14); addEdge(11, 16);
    addEdge(12, 15); addEdge(12, 16);
    addEdge(13, 14);
    addEdge(15, 16);
}

/**
 * @brief 添加一条无向边；先查重再插入，重复调用不会产生重复项。
 */
void Graph::addEdge(int a, int b)
{
    const int ia = a - 1;
    const int ib = b - 1;

    auto &na = m_adjacency[ia];
    if (std::find(na.begin(), na.end(), ib) == na.end()) {
        na.push_back(ib);
        m_adjacency[ib].push_back(ia);
        ++m_edgeCount;
    }
}

/**
 * @brief 取得节点的邻居列表。
 */
const std::vector<int> &Graph::neighbors(int index) const
{
    return m_adjacency[index];
}

/**
 * @brief 判断两个节点是否直接相连。
 */
bool Graph::areAdjacent(int a, int b) const
{
    const auto &na = m_adjacency[a];
    return std::find(na.begin(), na.end(), b) != na.end();
}

/**
 * @brief 取得节点的度数。
 */
std::size_t Graph::degree(int index) const
{
    return m_adjacency[index].size();
}

/**
 * @brief 取得已解状态下该位置应有的数字。
 */
int Graph::solvedValueAt(int index)
{
    // 已解状态：1..15 顺序排列，最后一个位置留空
    return (index == kNodeCount - 1) ? 0 : index + 1;
}

} // namespace core
