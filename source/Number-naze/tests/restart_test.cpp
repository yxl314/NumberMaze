/**
 * @file restart_test.cpp
 * @brief 验证局面快照与恢复的行为。
 *
 * 覆盖"重开本局"所依赖的两项能力：快照能完整复位局面，
 * 以及快照长度不符时明确失败而非静默忽略。
 *
 * 本文件不依赖 Qt，可单独编译运行。
 */

#include "GameState.h"
#include <cstdio>
#include <vector>

/**
 * @brief 运行快照与恢复的验证用例。
 * @return 失败项数量；0 表示全部通过。
 */
int main() {
    int fail = 0;
    auto check = [&](bool ok, const char *what) {
        std::printf("  [%s] %s\n", ok ? "通过" : "失败", what);
        if (!ok) ++fail;
    };

    core::GameState st;

    // 模拟"新局"：打乱并保存快照
    st.scramble(core::Difficulty::Normal);
    const std::vector<int> initial = st.board();
    std::printf("  初始局面首元素=%d, 步数=%d\n", initial[0], st.moveCount());

    // 走几步制造差异
    for (int i = 0; i < 5; ++i) {
        for (int k = 0; k < core::kNodeCount; ++k) {
            if (st.isMovable(k)) { st.move(k); break; }
        }
    }
    const bool moved = (st.board() != initial);
    check(moved, "走动后局面与初始不同");
    check(st.moveCount() > 0, "走动后步数大于 0");

    // 模拟"重开本局"：恢复快照
    const bool ok = st.restore(initial);
    check(ok, "restore 返回成功");
    check(st.board() == initial, "局面已复位到初始快照");
    check(st.moveCount() == 0, "步数已清零");
    check(!st.canUndo(), "撤销历史已清空");

    // 边界：错误长度必须失败而非静默接受
    const bool bad = st.restore(std::vector<int>(3, 0));
    check(!bad, "长度不符时 restore 返回失败");
    check(st.board() == initial, "失败后局面保持不变");

    std::printf("\n结论：%s（失败 %d 项）\n", fail == 0 ? "全部通过" : "存在问题", fail);
    return fail;
}
