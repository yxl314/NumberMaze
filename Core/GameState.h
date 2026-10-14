#ifndef CORE_GAMESTATE_H
#define CORE_GAMESTATE_H

#include <array>
#include <cstdint>
#include <vector>

#include "Difficulty.h"
#include "Graph.h"

namespace core {

// 游戏状态与规则。纯 C++，不依赖任何 GUI 框架。
//
// 设计要点：
//  - 棋盘布局用数组表示，值 0 代表空格
//  - 打乱通过在图上执行合法的随机游走完成，因此局面必然可解
//  - 维护撤销栈以支持悔棋
class GameState
{
public:
    // 随机源：允许注入以便测试可复现。返回 [0, bound) 内的整数。
    class Random
    {
    public:
        virtual ~Random() = default;
        virtual int next(int bound) = 0;
    };

    GameState();
    explicit GameState(Random *random);

    // 重置为已解状态
    void reset();

    // 打乱为新局。步数来自难度设定。
    // 打乱不计入撤销历史，避免玩家用悔棋退回已解状态。
    void scramble(Difficulty level);

    // 把节点 index 处的数字移入空格。不可移动时返回 false。
    bool move(int index);

    // 撤销上一步。无历史可撤销时返回 false。
    bool undo();

    bool canUndo() const { return !m_history.empty(); }

    // 是否已还原
    bool isSolved() const;

    // 空格所在节点；异常情况下返回 -1
    int blankIndex() const;

    // 节点 index 处的数字（0 表示空格）
    int valueAt(int index) const { return m_board[index]; }

    // 节点 index 是否可移动（非空且与空格相邻）
    bool isMovable(int index) const;

    int moveCount() const { return m_moveCount; }

    const Graph &graph() const { return m_graph; }

    // 棋盘快照，用于存档或对比
    std::vector<int> board() const;

    // 直接设置棋盘。仅供测试与恢复局面使用，不改变撤销历史。
    void setBoard(const std::vector<int> &board);

private:
    // 记录一次移动：移动前的空格位置。
    // 撤销时把该位置上的数字与当前空格交换即可复原。
    using History = std::vector<int>;

    Graph m_graph;
    std::array<int, kNodeCount> m_board {};
    History m_history;
    int m_moveCount = 0;

    Random *m_random = nullptr;
    Random *randomSource();
};

} // namespace core

#endif // CORE_GAMESTATE_H
