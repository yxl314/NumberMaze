#include "Graph.h"

#include <algorithm>

namespace core {

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

const std::vector<int> &Graph::neighbors(int index) const
{
    return m_adjacency[index];
}

bool Graph::areAdjacent(int a, int b) const
{
    const auto &na = m_adjacency[a];
    return std::find(na.begin(), na.end(), b) != na.end();
}

std::size_t Graph::degree(int index) const
{
    return m_adjacency[index].size();
}

int Graph::solvedValueAt(int index)
{
    // 已解状态：1..15 顺序排列，最后一个位置留空
    return (index == kNodeCount - 1) ? 0 : index + 1;
}

} // namespace core
