/**
 * @file model_test.cpp
 * @brief GameModel 自测：重点覆盖"重开本局"的快照语义。
 *
 * 不依赖 GUI，可用 QCoreApplication 运行：
 *
 *   qmake model-test.pro && make && ./model-test
 */

#include <QCoreApplication>
#include <QVector>

#include <cstdio>

#include "GameModel.h"

namespace
{

int g_failures = 0;

void check(bool v_ok, const char* v_what)
{
    std::printf("  [%s] %s\n", v_ok ? "通过" : "失败", v_what);
    if (!v_ok)
    {
        ++g_failures;
    }
}

int firstMovable(const QVector<bool>& v_movable)
{
    for (int i = 0; i < v_movable.size(); ++i)
    {
        if (v_movable.at(i))
        {
            return i;
        }
    }
    return -1;
}

} // namespace

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);
    QCoreApplication::setOrganizationName(QStringLiteral("NumberMazeTest"));
    QCoreApplication::setApplicationName(QStringLiteral("number-maze-model-test"));

    mvc::GameModel model;
    model.startNewGame(core::Difficulty::Normal);

    const QVector<int> initial = model.boardData();
    check(initial.size() == core::g_node_count, "开局局面包含 16 个节点");
    check(!model.isSolved(), "开局不是已解状态");
    check(model.moveCount() == 0, "开局步数为 0");
    check(!model.canUndo(), "开局没有可撤销步骤");

    const int movable = firstMovable(model.movableData());
    check(movable >= 0, "开局存在可移动节点");

    model.requestMove(movable);
    check(model.moveCount() == 1, "移动一步后步数为 1");
    check(model.canUndo(), "移动后可以悔棋");

    model.restartCurrentGame();
    check(model.boardData() == initial, "重开本局恢复到开局局面");
    check(model.moveCount() == 0, "重开本局后步数归零");
    check(!model.canUndo(), "重开本局后撤销历史清空");
    check(model.elapsedSeconds() == 0, "重开本局后计时归零");

    // 换一个难度重开，局面应仍然可玩且步数为零
    model.startNewGame(core::Difficulty::Hard);
    check(model.difficulty() == core::Difficulty::Hard, "新游戏切换难度");
    check(model.moveCount() == 0, "新游戏步数为 0");
    check(!model.isSolved(), "新游戏不是已解状态");

    std::printf("结论：%s（失败 %d 项）\n", g_failures == 0 ? "全部通过" : "存在失败", g_failures);
    return g_failures == 0 ? 0 : 1;
}
