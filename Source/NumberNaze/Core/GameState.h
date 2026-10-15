/**
 * @file GameState.h
 * @brief 棋盘状态与游戏规则。
 *
 * 本文件属于 Core 层，为纯 C++ 实现，不依赖任何 GUI 框架。
 */

#ifndef SOURCE_NUMBERNAZE_CORE_GAMESTATE_H
#define SOURCE_NUMBERNAZE_CORE_GAMESTATE_H

#include <array>
#include <cstdint>
#include <vector>

#include "Difficulty.h"
#include "Graph.h"

namespace core
{

/**
 * @brief 游戏状态与规则。
 *
 * @par 职责
 * 持有棋盘布局、撤销历史与步数，并实现移动、打乱、撤销与胜利判定。
 * 不涉及任何界面逻辑。
 *
 * @par 设计要点
 * - 棋盘布局用定长数组表示，值 0 代表空格；
 * - 打乱通过在图上执行合法的随机游走完成，因此局面必然可解；
 * - 维护撤销栈以支持悔棋；
 * - 拓扑（Graph）与局面（棋盘数组）分离，使局面可被廉价地复制与撤销。
 *
 * @par 可解性说明
 * 打乱并非随机排列，而是从已解状态出发执行若干次合法移动。由于每个
 * 合法移动都可逆，沿这串移动的反方向走回去必然回到已解状态。因此本类
 * 不需要标准华容道那套"逆序数加空格行号"的奇偶性判据——那套判据只在
 * 随机排列初始局面时才需要。
 */
class GameState
{
public:
    /**
     * @brief 随机源接口。
     *
     * 以接口形式注入，便于测试时使用确定性随机序列，
     * 使打乱结果可复现。
     */
    class Random
    {
    public:
        /// 虚析构函数，保证派生类可被正确析构。
        virtual ~Random()             = default;

        /**
         * @brief 产生一个随机整数。
         *
         * @param v_bound 上界（不含），必须为正数。
         * @return 位于 [0, v_bound) 区间内的整数。
         */
        virtual int next(int v_bound) = 0;
    };

    /**
     * @brief 默认构造，内部使用自带随机源。
     *
     * 构造后棋盘处于已解状态。
     */
    GameState();

    /**
     * @brief 使用外部随机源构造，便于测试复现。
     *
     * @param v_random 随机源指针；由调用者保证其生命周期长于本对象。
     *               传入 nullptr 时行为等同于默认构造。
     *
     * @note 构造后棋盘处于已解状态。
     */
    explicit GameState(Random* v_random);

    /**
     * @brief 打乱为新局。
     *
     * 从已解状态出发执行若干次合法移动，步数由难度决定。
     * 因每一步均合法且可逆，所得局面必然可解。
     *
     * @param v_level 难度级别，决定打乱步数。
     *
     * @note 打乱属于"设定初始局面"，不计入撤销历史，也不计入步数。
     *       这可避免玩家用悔棋一路退回已解状态。
     */
    void scramble(Difficulty v_level);

    /**
     * @brief 把指定节点上的数字移入空格。
     *
     * @param v_index 待移动的节点索引。
     * @return 移动成功返回 true；该节点为空、与空格不相邻或索引越界时
     *         返回 false，且状态保持不变。
     *
     * @note 移动成功后步数加一，并在撤销栈中记录一步。
     */
    bool move(int v_index);

    /**
     * @brief 撤销上一步移动。
     *
     * @return 撤销成功返回 true；撤销栈为空时返回 false。
     *
     * @note 撤销成功时步数减一。
     */
    bool undo();

    /**
     * @brief 是否还有可撤销的步骤。
     *
     * @return 存在撤销历史返回 true。
     */
    bool canUndo() const
    {
        return !m_history.empty();
    }

    /**
     * @brief 判断是否已经还原成解。
     *
     * @return 各位置数字均与已解状态一致时返回 true。
     */
    bool isSolved() const;

    /**
     * @brief 取得空格所在的节点索引。
     *
     * @return 空格所在索引；若棋盘数据异常导致找不到空格，返回 -1。
     */
    int  blankIndex() const;

    /**
     * @brief 取得指定节点上的数字。
     *
     * @param v_index 节点索引。
     * @return 该节点上的数字；0 表示空格。
     */
    int  valueAt(int v_index) const
    {
        return m_board[v_index];
    }

    /**
     * @brief 判断指定节点当前是否可以移动。
     *
     * 可移动的条件是：该节点非空，且与空格直接相连。
     *
     * @param v_index 节点索引。
     * @return 可以移动返回 true。
     */
    bool isMovable(int v_index) const;

    /**
     * @brief 取得玩家已走的步数。
     *
     * @return 步数；打乱不计入其中。
     */
    int  moveCount() const
    {
        return m_moveCount;
    }

    /**
     * @brief 取得棋盘拓扑。
     *
     * @return 拓扑对象的常量引用。
     */
    const Graph& graph() const
    {
        return m_graph;
    }

    /**
     * @brief 取得当前局面的快照。
     *
     * @return 长度为 g_node_count 的数组，元素为各节点上的数字。
     *         可用于存档或与另一局面逐项比较。
     */
    std::vector<int> board() const;

    /**
     * @brief 恢复到先前保存的局面。
     *
     * 用于"重开本局"：配合 board() 保存的初始快照，把棋盘复位到
     * 本局开始时的状态。
     *
     * @param v_snapshot 由 board() 取得的快照。
     * @return 恢复成功返回 true；快照长度不符时返回 false 且状态不变。
     *
     * @note 恢复后清空撤销历史与步数，因快照属于新一局的起点。
     */
    bool             restore(const std::vector<int>& v_snapshot);

private:
    /**
     * @brief 重置为已解状态。
     *
     * 棋盘恢复为 1 到 15 依次排列、空格居末，并清空撤销历史与步数。
     * 仅供构造与打乱使用。
     */
    void reset();

    /// 撤销栈：按顺序记录每次移动前的空格位置。
    using History = std::vector<int>;

    /// 棋盘拓扑，整个游戏期间保持不变。
    Graph                         m_graph;

    /// 各节点上的数字，0 表示空格。
    std::array<int, g_node_count> m_board{};

    /// 撤销栈。
    History                       m_history;

    /// 玩家已走的步数。
    int                           m_moveCount = 0;

    /// 外部注入的随机源；为空时使用内置实现。
    Random*                       m_random    = nullptr;

    /**
     * @brief 取得可用的随机源。
     *
     * @return 若构造时注入了随机源则返回之，否则返回内置的默认随机源。
     */
    Random*                       randomSource();
};

} // namespace core

#endif // SOURCE_NUMBERNAZE_CORE_GAMESTATE_H
