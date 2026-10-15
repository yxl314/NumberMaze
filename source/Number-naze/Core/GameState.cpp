#include "GameState.h"

#include <algorithm>
#include <random>

namespace core {

namespace {

// 默认随机源：仅在未注入外部随机源时使用
class DefaultRandom : public GameState::Random
{
public:
    int next(int bound) override
    {
        if (bound <= 0)
            return 0;
        std::uniform_int_distribution<int> dist(0, bound - 1);
        return dist(m_engine);
    }

private:
    std::mt19937 m_engine { std::random_device {}() };
};

} // namespace

/**
 * @brief 默认构造，使用内置随机源。
 */
GameState::GameState()
    : m_random(nullptr)
{
    reset();
}

/**
 * @brief 使用外部随机源构造，便于测试复现。
 */
GameState::GameState(Random *random)
    : m_random(random)
{
    reset();
}

/**
 * @brief 取得可用的随机源。
 */
GameState::Random *GameState::randomSource()
{
    if (m_random != nullptr)
        return m_random;

    // 懒初始化的默认随机源
    static DefaultRandom fallback;
    return &fallback;
}

/**
 * @brief 重置为已解状态，并清空历史与步数。
 */
void GameState::reset()
{
    for (int i = 0; i < kNodeCount; ++i)
        m_board[i] = Graph::solvedValueAt(i);

    m_history.clear();
    m_moveCount = 0;
}

/**
 * @brief 取得空格所在索引。
 */
int GameState::blankIndex() const
{
    for (int i = 0; i < kNodeCount; ++i) {
        if (m_board[i] == 0)
            return i;
    }
    return -1;
}

/**
 * @brief 打乱为新局，所得局面必然可解。
 */
void GameState::scramble(Difficulty level)
{
    reset();

    const int steps = scrambleSteps(level);
    int previousBlank = -1;

    // 从已解状态出发执行若干次合法移动。
    // 因为每一步都合法且可逆，所以得到的局面必然可解：
    // 沿着这串移动的反方向走回去即可还原。
    //
    // 同时刻意避开"立刻退回上一步"，否则会原地打转，
    // 实际有效打乱程度远低于设定步数。
    for (int s = 0; s < steps; ++s) {
        const int blank = blankIndex();
        if (blank < 0)
            break;

        std::vector<int> candidates;
        candidates.reserve(m_graph.degree(blank));
        for (int nb : m_graph.neighbors(blank)) {
            if (nb != previousBlank)
                candidates.push_back(nb);
        }
        if (candidates.empty()) {
            for (int nb : m_graph.neighbors(blank))
                candidates.push_back(nb);
        }
        if (candidates.empty())
            break;

        int pick = -1;
        if (candidates.size() == 1) {
            pick = candidates.front();
        } else {
            pick = candidates[static_cast<std::size_t>(
                randomSource()->next(static_cast<int>(candidates.size())))];
        }

        std::swap(m_board[blank], m_board[pick]);
        previousBlank = blank;
    }

    // 打乱是"设定初始局面"，不进入撤销历史
    m_history.clear();
    m_moveCount = 0;
}

/**
 * @brief 判断节点是否可移动。
 */
bool GameState::isMovable(int index) const
{
    if (index < 0 || index >= kNodeCount)
        return false;
    if (m_board[index] == 0)
        return false;

    const int blank = blankIndex();
    return blank >= 0 && m_graph.areAdjacent(index, blank);
}

/**
 * @brief 把指定节点上的数字移入空格。
 */
bool GameState::move(int index)
{
    if (!isMovable(index))
        return false;

    const int blank = blankIndex();
    std::swap(m_board[index], m_board[blank]);
    ++m_moveCount;

    // 记录移动前的空格位置，撤销时据此还原
    m_history.push_back(blank);
    return true;
}

/**
 * @brief 撤销上一步移动。
 */
bool GameState::undo()
{
    if (m_history.empty())
        return false;

    const int from = m_history.back();
    m_history.pop_back();

    const int blank = blankIndex();
    std::swap(m_board[from], m_board[blank]);

    if (m_moveCount > 0)
        --m_moveCount;
    return true;
}

/**
 * @brief 判断是否已经还原成解。
 */
bool GameState::isSolved() const
{
    for (int i = 0; i < kNodeCount; ++i) {
        if (m_board[i] != Graph::solvedValueAt(i))
            return false;
    }
    return true;
}

/**
 * @brief 取得当前局面的快照。
 */
std::vector<int> GameState::board() const
{
    return std::vector<int>(m_board.begin(), m_board.end());
}

/**
 * @brief 恢复到先前保存的局面。
 */
bool GameState::restore(const std::vector<int> &snapshot)
{
    if (snapshot.size() != static_cast<std::size_t>(kNodeCount))
        return false;

    std::copy(snapshot.begin(), snapshot.end(), m_board.begin());
    m_history.clear();
    m_moveCount = 0;
    return true;
}

} // namespace core
