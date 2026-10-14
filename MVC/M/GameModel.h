#ifndef MVC_M_GAMEMODEL_H
#define MVC_M_GAMEMODEL_H

#include <QObject>
#include <QSettings>
#include <QString>
#include <QTime>
#include <QVector>

#include "GameState.h"

class QTimer;

namespace mvc {

// Model 层：数据 + Qt 相关的处理逻辑 + 调用底层后端（Core）。
//
// 严格 MVC 约束（重要）：
//  - 本类不知道 View 的存在，不持有任何 View 指针
//  - 状态变化一律通过信号广播，由控制器转接给 View
//  - 所有游戏规则都委托给 core::GameState，本类不自己实现规则
class GameModel : public QObject
{
    Q_OBJECT

public:
    explicit GameModel(QObject *parent = nullptr);

    // ---- 供控制器查询的只读数据 ----
    core::Difficulty difficulty() const { return m_difficulty; }
    QString difficultyText() const;
    int moveCount() const { return m_state.moveCount(); }
    int elapsedSeconds() const { return m_elapsedSeconds; }
    QTime elapsed() const { return QTime(0, 0).addSecs(m_elapsedSeconds); }
    bool canUndo() const { return m_state.canUndo(); }
    bool isSolved() const { return m_state.isSolved(); }
    int bestRecord(core::Difficulty level) const;

    // 把当前局面整理成 View 需要的形状。
    // 由控制器在收到 boardChanged 后调用并转交给 View——
    // 这样 View 与 Model 之间没有直接引用。
    QVector<int> boardData() const;
    QVector<QVector<int>> connectionData() const;
    QVector<bool> movableData() const;

public slots:
    // ---- 控制器调用的操作入口 ----
    void startNewGame(core::Difficulty level);
    void startNewGame();
    void restartCurrentGame();
    void requestMove(int index);
    void requestUndo();

signals:
    // ---- 广播给控制器的状态变化 ----
    // 局面改变：控制器据此把新数据转交给 View
    void boardChanged();

    // 状态栏相关信息改变（步数、用时、可撤销性、最佳成绩）
    void statusChanged();

    // 计时跳动
    void tick();

    // 通关：控制器据此弹出提示
    void solved(int moves, int seconds, bool isNewRecord);

private slots:
    void onTick();

private:
    void startTimer();
    void stopTimer();
    void saveBestRecord(core::Difficulty level, int moves);
    static QString recordKey(core::Difficulty level);

    core::GameState m_state;
    core::Difficulty m_difficulty = core::Difficulty::Normal;

    QTimer *m_timer = nullptr;
    int m_elapsedSeconds = 0;
    bool m_running = false;
    bool m_solvedNotified = false;

    QSettings m_settings;
};

} // namespace mvc

#endif // MVC_M_GAMEMODEL_H
