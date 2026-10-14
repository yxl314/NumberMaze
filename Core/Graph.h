#ifndef CORE_GRAPH_H
#define CORE_GRAPH_H

#include <array>
#include <cstddef>
#include <vector>

namespace core {

// 棋盘边长（4x4 = 16 个节点）
constexpr int kBoardSize = 4;
constexpr int kNodeCount = kBoardSize * kBoardSize;

// 图的拓扑结构。
//
// 注意：节点虽按 4x4 方阵摆放，但连接关系并非网格邻居，
// 而是本项目专门设计的拓扑，共 22 条边，其中包含对角线连接
// （例如 1-6、3-8）。这正是本游戏不能靠空间直觉判断、
// 难度高于普通数字华容道的原因。
//
// 该类只描述"谁和谁相连"，不持有任何游戏状态，属于纯数据。
class Graph
{
public:
    Graph();

    // 节点 index 的邻居列表（index 从 0 开始）
    const std::vector<int> &neighbors(int index) const;

    // 两个节点是否直接相连
    bool areAdjacent(int a, int b) const;

    // 节点度数
    std::size_t degree(int index) const;

    // 边的总数
    std::size_t edgeCount() const { return m_edgeCount; }

    // 节点在方阵中的行列（用于绘制，行/列均从 0 开始）
    static int rowOf(int index) { return index / kBoardSize; }
    static int colOf(int index) { return index % kBoardSize; }
    static int indexOf(int row, int col) { return row * kBoardSize + col; }

    // 已解状态下，各位置应放置的数字（最后一个位置为 0，表示空格）
    static int solvedValueAt(int index);

private:
    void addEdge(int a, int b);

    std::array<std::vector<int>, kNodeCount> m_adjacency;
    std::size_t m_edgeCount = 0;
};

} // namespace core

#endif // CORE_GRAPH_H
