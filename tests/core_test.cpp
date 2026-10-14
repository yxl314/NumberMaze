#include "GameState.h"
#include "Graph.h"

#include <algorithm>
#include <cstdio>
#include <vector>

// Core 层自测。不依赖 Qt，可脱离构建系统单独编译：
//
//   g++ -std=c++17 -Wall -Wextra -ICore tests/core_test.cpp
//       Core/Graph.cpp Core/GameState.cpp -o core_test
//   ./core_test

namespace {

// 确定性随机源，使测试结果可复现
class FixedRandom : public core::GameState::Random
{
public:
    explicit FixedRandom(unsigned seed) : m_state(seed) {}

    int next(int bound) override
    {
        m_state = m_state * 1103515245u + 12345u;
        return int((m_state >> 16) % unsigned(bound));
    }

private:
    unsigned m_state;
};

int g_failures = 0;

void check(bool ok, const char *what)
{
    std::printf("  [%s] %s\n", ok ? "通过" : "失败", what);
    if (!ok)
        ++g_failures;
}

void testGraph()
{
    std::puts("=== 图结构 ===");
    core::Graph g;

    check(g.edgeCount() == 22, "边数为 22");
    check(g.neighbors(0).size() == 3, "节点 1 的度为 3");
    check(g.degree(4) == 2, "节点 5 的度为 2");

    // 对角线连接是难度的主要来源，必须存在
    check(g.areAdjacent(0, 5), "节点 1 与 6 相连（对角连接）");
    check(g.areAdjacent(2, 7), "节点 3 与 8 相连（对角连接）");
    check(!g.areAdjacent(0, 3), "节点 1 与 4 不相连");

    int deg2 = 0;
    int deg3 = 0;
    for (int i = 0; i < core::kNodeCount; ++i) {
        const auto d = g.degree(i);
        if (d == 2)
            ++deg2;
        else if (d == 3)
            ++deg3;
    }
    check(deg2 == 4 && deg3 == 12, "度数分布：4 个度 2、12 个度 3");

    check(core::Graph::rowOf(5) == 1 && core::Graph::colOf(5) == 1,
          "节点索引到行列的换算正确");
}

void testBasics()
{
    std::puts("=== 基本状态 ===");
    FixedRandom rng(12345);
    core::GameState st(&rng);

    check(st.isSolved(), "初始为已解状态");
    check(st.valueAt(15) == 0, "空格位于末位");
    check(st.blankIndex() == 15, "空格索引为 15");
    check(!st.isMovable(15), "空格自身不可移动");
    check(!st.canUndo(), "初始没有可撤销步骤");
    check(st.moveCount() == 0, "初始步数为 0");

    // 非相邻节点不可移动
    int far = -1;
    for (int i = 0; i < core::kNodeCount; ++i) {
        if (i != st.blankIndex() && !st.graph().areAdjacent(i, st.blankIndex())) {
            far = i;
            break;
        }
    }
    if (far >= 0)
        check(!st.isMovable(far), "非相邻节点不可移动");
}

void testMoveUndo()
{
    std::puts("=== 移动与撤销 ===");
    FixedRandom rng(999);
    core::GameState st(&rng);

    int target = -1;
    for (int i = 0; i < core::kNodeCount; ++i) {
        if (st.isMovable(i)) {
            target = i;
            break;
        }
    }

    check(target >= 0, "存在可移动节点");
    check(st.move(target), "执行一次移动");
    check(st.moveCount() == 1, "步数计为 1");
    check(!st.isSolved(), "移动后不再是已解状态");
    check(st.canUndo(), "此时可以撤销");
    check(st.undo(), "执行撤销");
    check(st.isSolved(), "撤销后回到已解状态");
    check(st.moveCount() == 0, "撤销后步数归零");
    check(!st.canUndo(), "撤销后无更多历史");
}

void testScramble()
{
    std::puts("=== 打乱 ===");
    FixedRandom rng(2024);

    const core::Difficulty levels[] = {
        core::Difficulty::Easy, core::Difficulty::Normal,
        core::Difficulty::Hard, core::Difficulty::Expert
    };

    bool allOk = true;
    for (core::Difficulty lvl : levels) {
        core::GameState st(&rng);
        st.scramble(lvl);

        const bool scrambled = !st.isSolved();
        const std::vector<int> scrambledBoard = st.board();

        // 打乱不计入步数，也不产生撤销历史
        const bool noHistory = !st.canUndo();
        const bool zeroMoves = (st.moveCount() == 0);

        // 走一步再撤销，应精确回到打乱后的局面（而非已解局面）
        int idx = -1;
        for (int i = 0; i < core::kNodeCount; ++i) {
            if (st.isMovable(i)) {
                idx = i;
                break;
            }
        }
        st.move(idx);
        const bool movedAway = (st.board() != scrambledBoard);
        st.undo();
        const bool restored = (st.board() == scrambledBoard);

        std::printf("  难度 %s：打乱生效=%s 无历史=%s 步数为0=%s 撤销回打乱态=%s\n",
                    core::difficultyName(lvl),
                    scrambled ? "是" : "否",
                    noHistory ? "是" : "否",
                    zeroMoves ? "是" : "否",
                    restored ? "是" : "否");

        if (!scrambled || !noHistory || !zeroMoves || !movedAway || !restored)
            allOk = false;
    }
    check(allOk, "四档难度打乱均正确，且不进入撤销历史");
}

// 打乱由合法移动构成，因此把这些移动逐步撤销必然回到已解状态。
void testSolvabilityByUndo()
{
    std::puts("=== 可解性：移动全部撤销即复原 ===");
    FixedRandom rng(7);

    bool allOk = true;
    for (int trial = 0; trial < 10; ++trial) {
        core::GameState st(&rng);
        const int steps = 20 + trial * 17;
        int previousBlank = -1;

        for (int k = 0; k < steps; ++k) {
            const int blank = st.blankIndex();
            std::vector<int> candidates;
            for (int nb : st.graph().neighbors(blank)) {
                if (nb != previousBlank)
                    candidates.push_back(nb);
            }
            if (candidates.empty()) {
                for (int nb : st.graph().neighbors(blank))
                    candidates.push_back(nb);
            }
            previousBlank = blank;

            const auto pick = candidates[static_cast<std::size_t>(
                rng.next(static_cast<int>(candidates.size())))];
            if (!st.move(pick)) {
                allOk = false;
                break;
            }
        }

        const bool scrambled = !st.isSolved();
        int undone = 0;
        while (st.undo())
            ++undone;

        if (!scrambled || undone != steps || !st.isSolved()) {
            std::printf("  第 %d 轮失败：走 %d 步、撤销 %d 步、已解=%s\n",
                        trial, steps, undone, st.isSolved() ? "是" : "否");
            allOk = false;
        }
    }
    check(allOk, "10 轮「走出 N 步后撤销 N 步必复原」全部成立");
}

} // namespace

/**
 * @brief 运行 Core 层的全部验证用例。
 * @return 失败项数量；0 表示全部通过。
 */
int main()
{
    testGraph();
    testBasics();
    testMoveUndo();
    testScramble();
    testSolvabilityByUndo();

    std::printf("\n结论：%s（失败 %d 项）\n",
                g_failures == 0 ? "全部通过" : "存在问题", g_failures);
    return g_failures == 0 ? 0 : 1;
}
