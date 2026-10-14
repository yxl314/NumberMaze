#ifndef GAMEWINDOW_H
#define GAMEWINDOW_H

#include <QMainWindow>
#include <QSettings>

#include "graphmodel.h"

class PuzzleWidget;
class QLabel;
class QTimer;
class QAction;

// 主窗口：承载游戏画布，并提供菜单、工具栏与状态栏。
class GameWindow : public QMainWindow
{
    Q_OBJECT

public:
    explicit GameWindow(QWidget *parent = nullptr);

private slots:
    void onNewGame(Difficulty level);
    void onRestart();
    void onUndo();
    void onSolved(int moves);
    void onStateChanged();
    void onTick();

private:
    void buildActions();
    void buildMenus();
    void buildStatusBar();
    void startTimerIfNeeded();
    void stopTimer();
    void refreshStatus();
    void saveBestRecord(Difficulty level, int moves);
    int bestRecord(Difficulty level) const;

    static QString difficultyName(Difficulty level);
    static QString recordKey(Difficulty level);

    PuzzleWidget *m_puzzle = nullptr;
    QLabel *m_difficultyLabel = nullptr;
    QLabel *m_movesLabel = nullptr;
    QLabel *m_timeLabel = nullptr;
    QLabel *m_bestLabel = nullptr;

    QAction *m_undoAction = nullptr;
    QAction *m_showConnectionsAction = nullptr;
    QAction *m_showHintsAction = nullptr;

    QTimer *m_timer = nullptr;
    QSettings m_settings;

    Difficulty m_difficulty = Difficulty::Normal;
    int m_elapsedSeconds = 0;
    bool m_running = false;
};

#endif // GAMEWINDOW_H
