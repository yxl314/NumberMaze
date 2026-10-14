#ifndef GRAPHMODEL_H
#define GRAPHMODEL_H

#include <QList>
#include <QSet>
#include <QVector>

// 难度级别：以打乱所需的反向移动步数衡量
enum class Difficulty {
    Easy = 0,     // 简单
    Normal = 1,   // 普通
    Hard = 2,     // 困难
    Expert = 3    // 专家
};

// 单个图节点：value 为节点上的数字，0 表示空格
struct Node
{
    int value;
    QSet<int> adj;

    Node() : value(0) {}
};

// 数字迷宫的核心数据模型。
//
// 图结构来自项目的特殊设计：节点按 4x4 方阵摆放，但连接关系并非
// 网格邻居，而是包含若干对角线连接的自定义拓扑（22 条边）。
// 这使得玩家无法凭空间直觉判断可移动性，难度高于普通数字华容道。
class GraphModel
{
public:
    static const int N = 4;
    static const int CellCount = N * N;

    GraphModel();

    // 重置为已解状态（1..15 顺序排列，空格在右下角）
    void reset();

    // 按指定难度打乱。
    // 实现要点：打乱通过在图上执行合法的反向随机游走完成，
    // 因此得到的局面必然可解——沿着记录下的移动反方向即可复原。
    void scramble(Difficulty level);

    // 尝试把 idx 处的数字移入空格。成功返回 true。
    bool tryMove(int idx);

    // 撤销上一步。用于悔棋，同时也是"打乱必然可解"的自然验证手段。
    bool undo();

    // 是否有可撤销的步骤
    bool canUndo() const { return !m_history.isEmpty(); }

    // 清空撤销历史（打乱/重开后调用）
    void clearHistory() { m_history.clear(); }

    // 空格所在节点下标
    int blankIndex() const;

    // 节点 at 是否可以移动（非空 且 与空格相邻）
    bool isMovable(int idx) const;

    // 是否已还原成解
    bool isSolved() const;

    // 当前节点上的数字（0 表示空格）
    int valueAt(int idx) const { return m_nodes[idx].value; }
    const QSet<int> &neighbors(int idx) const { return m_nodes[idx].adj; }
    bool areAdjacent(int a, int b) const { return m_nodes[a].adj.contains(b); }

    int moveCount() const { return m_moveCount; }
    void resetMoveCount() { m_moveCount = 0; }

    // 用于存档/比较的状态快照
    QVector<int> snapshot() const;

private:
    void buildGraph();
    void addConnection(int a, int b);
    static int minScrambleSteps(Difficulty level);

    QVector<Node> m_nodes;
    int m_moveCount = 0;

    // 撤销栈：记录每次移动时"被移动节点"的下标。
    // 由于移动是交换，撤销只需把同一节点再交换一次。
    QVector<int> m_history;
};

#endif // GRAPHMODEL_H
