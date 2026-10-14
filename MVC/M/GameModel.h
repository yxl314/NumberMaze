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

// Model 层：持有游戏状态，向视图与控制器广播变化。
//
// 职责边界：
//  - 包装 core::GameState，把游戏规则与界面彻底隔离
//  - 管理计时、步数、最佳成绩等"会话级"状态
//  - 只发信号，不关心谁在监听，也不直接操作任何控件
class GameModel : public QObject
{
    Q_OBJECT

public:
    explicit GameModel(QObject *parent = nullptr);

    // ---- 状态查询 ----
    const core::GameState &state() const { return m_state; }
    const core::Graph &graph() const { return m_state.graph(); }

    int valueAt(int index) const { return m_state.valueAt(index); }
    int blankIndex() const { return m_state.blankIndex(); }
    bool isMovable(int index) const { return m_state.isMovable(index); }
    bool isSolved() const { return m_state.isSolved(); }
    int moveCount() const { return m_state.moveCount(); }
    bool canUndo() const { return m_state.canUndo(); }

    core::Difficulty difficulty() const { return m_difficulty; }
    QString difficultyText() const;

    QTime elapsed() const { return QTime(0, 0).addSecs(m_elapsedSeconds); }
    int elapsedSeconds() const { return m_elapsedSeconds; }

    int bestRecord(core::Difficulty level) const;

    // ---- 操作 ----
    void startNewGame(core::Difficulty level);
    void restartCurrentGame();
    void tryMoveAt(int index);
    void undo();

signals:
    // 棋盘内容发生变化（移动、撤销、重开、新局），视图应重绘
    void boardChanged();
    // 步数/用时/可撤销性等状态变化，控制器应刷新界面文字
    void statusChanged();
    // 计时器每秒跳动
    void tick();
    // 通关
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

    // 结束一局后置位，避免重复判定通关
    bool m_solvedNotified = false;

    QSettings m_settings;
};

} // namespace mvc

#endif // MVC_M_GAMEMODEL_H
