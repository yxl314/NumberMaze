/**
 * @file Graph.h
 * @brief 棋盘的图拓扑结构。
 *
 * 本文件属于 Core 层，为纯 C++ 实现，不依赖任何 GUI 框架。
 */

#ifndef CORE_GRAPH_H
#define CORE_GRAPH_H

#include <array>
#include <cstddef>
#include <vector>

namespace core {

/// 棋盘边长（4x4）。
constexpr int kBoardSize = 4;

/// 节点总数。
constexpr int kNodeCount = kBoardSize * kBoardSize;

/**
 * @brief 棋盘的连接拓扑。
 *
 * @par 拓扑特征
 * 节点按 4x4 方阵摆放，但连接关系不是网格邻居：共 22 条边，其中包含
 * 多条对角线连接（例如 1-6、3-8）。因此无法凭位置直觉判断可移动性。
 *
 * @par 职责
 * 只描述谁和谁相连，不持有任何游戏状态。棋盘上的数字由 GameState
 * 负责，两者分离使局面可以被低成本地复制与撤销。
 *
 * @note 节点索引从 0 开始；边的定义以 1 起始书写，与节点编号习惯一致。
 */
class Graph
{
public:
    /**
     * @brief 构造拓扑，建立全部连接。
     *
     * 构造过程是幂等的：重复添加同一条边不会产生重复邻接项，
     * 边数也不会被重复累计。
     */
    Graph();

    /**
     * @brief 取得节点的邻居列表。
     *
     * @param index 节点索引，取值区间为 [0, kNodeCount)。
     * @return 邻居索引的常量引用；返回的容器归本对象所有，
     *         调用者不得修改或释放。
     */
    const std::vector<int> &neighbors(int index) const;

    /**
     * @brief 判断两个节点是否直接相连。
     *
     * @param a 第一个节点索引。
     * @param b 第二个节点索引。
     * @return 直接相连返回 true，否则返回 false。
     */
    bool areAdjacent(int a, int b) const;

    /**
     * @brief 取得节点的度数（即邻居数量）。
     *
     * @param index 节点索引。
     * @return 该节点的邻居数量。
     */
    std::size_t degree(int index) const;

    /**
     * @brief 取得图中边的总数。
     *
     * @return 边的条数，当前拓扑为 22。
     */
    std::size_t edgeCount() const { return m_edgeCount; }

    /**
     * @brief 取得节点所在的行。
     *
     * @param index 节点索引。
     * @return 行号，从 0 开始。
     */
    static int rowOf(int index) { return index / kBoardSize; }

    /**
     * @brief 取得节点所在的列。
     *
     * @param index 节点索引。
     * @return 列号，从 0 开始。
     */
    static int colOf(int index) { return index % kBoardSize; }

    /**
     * @brief 取得已解状态下该位置应有的数字。
     *
     * 已解状态为 1 到 15 依次排列，最后一个位置为 0，表示空格。
     *
     * @param index 节点索引。
     * @return 该位置应有的数字；0 表示空格。
     */
    static int solvedValueAt(int index);

private:
    /**
     * @brief 添加一条无向边。
     *
     * 若该边已存在则不重复添加，边数也不会重复累计。
     *
     * @param a 端点编号（1 起始）。
     * @param b 另一个端点编号（1 起始）。
     */
    void addEdge(int a, int b);

    /// 邻接表：m_adjacency[i] 存放节点 i 的所有邻居索引。
    std::array<std::vector<int>, kNodeCount> m_adjacency;

    /// 已添加的边数。
    std::size_t m_edgeCount = 0;
};

} // namespace core

#endif // CORE_GRAPH_H
