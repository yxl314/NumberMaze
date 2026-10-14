#include "graphmodel.h"

#include <QRandomGenerator>
#include <algorithm>

GraphModel::GraphModel()
{
    buildGraph();
    reset();
}

void GraphModel::buildGraph()
{
    m_nodes.clear();
    m_nodes.resize(CellCount);
    for (int i = 0; i < CellCount; ++i)
        m_nodes[i].value = (i == CellCount - 1) ? 0 : i + 1;

    // 项目特有的连接关系（1-based 编号，与原设计一致）。
    // 注意其中包含对角线连接，例如 (1,6)、(3,8)，这正是本游戏
    // 区别于标准数字华容道的难点所在。
    addConnection(1, 5);  addConnection(1, 6);  addConnection(1, 2);
    addConnection(2, 5);  addConnection(2, 7);
    addConnection(3, 6);  addConnection(3, 8);  addConnection(3, 4);
    addConnection(4, 7);  addConnection(4, 8);
    addConnection(6, 11);
    addConnection(7, 10);
    addConnection(9, 13); addConnection(9, 14);
    addConnection(10, 13); addConnection(10, 15);
    addConnection(11, 14); addConnection(11, 16);
    addConnection(12, 15); addConnection(12, 16);
    addConnection(13, 14);
    addConnection(15, 16);
}

void GraphModel::addConnection(int a, int b)
{
    const int ia = a - 1;
    const int ib = b - 1;
    m_nodes[ia].adj.insert(ib);
    m_nodes[ib].adj.insert(ia);
}

void GraphModel::reset()
{
    for (int i = 0; i < CellCount; ++i)
        m_nodes[i].value = (i == CellCount - 1) ? 0 : i + 1;
    m_moveCount = 0;
    m_history.clear();
}

int GraphModel::minScrambleSteps(Difficulty level)
{
    switch (level) {
    case Difficulty::Easy:   return 20;
    case Difficulty::Normal: return 60;
    case Difficulty::Hard:   return 150;
    case Difficulty::Expert: return 300;
    }
    return 60;
}

void GraphModel::scramble(Difficulty level)
{
    reset();

    const int steps = minScrambleSteps(level);
    int previousBlank = -1;

    // 反向随机游走：每一步都是合法移动，因此得到的局面必然可解。
    // 这里刻意避开"立刻退回上一步"，否则会原地打转、实际有效步数远低于设定值。
    for (int s = 0; s < steps; ++s) {
        const int blank = blankIndex();
        QList<int> candidates;
        candidates.reserve(m_nodes[blank].adj.size());
        for (int nb : m_nodes[blank].adj) {
            if (nb != previousBlank)
                candidates.append(nb);
        }
        if (candidates.isEmpty()) {
            for (int nb : m_nodes[blank].adj)
                candidates.append(nb);
        }

        const int pick = candidates.at(
            QRandomGenerator::global()->bounded(int(candidates.size())));

        std::swap(m_nodes[blank].value, m_nodes[pick].value);
        previousBlank = blank;
    }

    m_moveCount = 0;

    // 打乱属于"设定初始局面"，不进入撤销历史。
    // 因此玩家不能用悔棋一路退回被解状态。
    m_history.clear();
}

int GraphModel::blankIndex() const
{
    for (int i = 0; i < m_nodes.size(); ++i) {
        if (m_nodes[i].value == 0)
            return i;
    }
    return -1;
}

bool GraphModel::isMovable(int idx) const
{
    if (idx < 0 || idx >= m_nodes.size())
        return false;
    if (m_nodes[idx].value == 0)
        return false;
    const int blank = blankIndex();
    return blank >= 0 && m_nodes[idx].adj.contains(blank);
}

bool GraphModel::tryMove(int idx)
{
    if (!isMovable(idx))
        return false;

    const int blank = blankIndex();
    std::swap(m_nodes[idx].value, m_nodes[blank].value);
    ++m_moveCount;
    // 记录"移动前的空格位置"：撤销时把数字换回那里即可复原。
    m_history.append(blank);
    return true;
}

bool GraphModel::undo()
{
    if (m_history.isEmpty())
        return false;

    // 历史里存的是移动前的空格位置，也就是数字移动之后所在的位置。
    const int from = m_history.takeLast();
    const int blank = blankIndex();

    // 交换即还原（from 与当前空格必然相邻，因为上一步刚换过）。
    std::swap(m_nodes[from].value, m_nodes[blank].value);
    if (m_moveCount > 0)
        --m_moveCount;
    return true;
}

bool GraphModel::isSolved() const
{
    for (int i = 0; i < m_nodes.size(); ++i) {
        const int expected = (i == CellCount - 1) ? 0 : i + 1;
        if (m_nodes[i].value != expected)
            return false;
    }
    return true;
}

QVector<int> GraphModel::snapshot() const
{
    QVector<int> out;
    out.reserve(m_nodes.size());
    for (const Node &n : m_nodes)
        out.append(n.value);
    return out;
}
