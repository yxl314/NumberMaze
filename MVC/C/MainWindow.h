#ifndef MVC_C_MAINWINDOW_H
#define MVC_C_MAINWINDOW_H

#include <QMainWindow>

#include "GameModel.h"

class QAction;
class QActionGroup;
class QLabel;

namespace mvc {

class BoardView;

// Controller 层：连接 Model 与 View，承载所有界面编排逻辑。
//
// 职责边界：
//  - 接收 View 的交互事件，转成对 Model 的操作
//  - 监听 Model 信号，更新界面文字与菜单状态
//  - 不含任何游戏规则（那些在 core::GameState）
//  - 不含任何绘制代码（那些在 BoardView）
class MainWindow : public QMainWindow
{
    Q_OBJECT

public:
    explicit MainWindow(QWidget *parent = nullptr);

private slots:
    void onNewGame(core::Difficulty level);
    void onRestart();
    void onUndo();
    void onNodeClicked(int index);
    void onBoardChanged();
    void onStatusChanged();
    void onSolved(int moves, int seconds, bool isNewRecord);
    void showAbout();

private:
    void setupActions();
    void setupMenus();
    void setupToolBar();
    void setupStatusBar();
    void refreshStatus();
    void refreshWindowTitle();

    GameModel *m_model = nullptr;
    BoardView *m_view = nullptr;

    QLabel *m_difficultyLabel = nullptr;
    QLabel *m_movesLabel = nullptr;
    QLabel *m_timeLabel = nullptr;
    QLabel *m_bestLabel = nullptr;

    QAction *m_undoAction = nullptr;
    QActionGroup *m_difficultyGroup = nullptr;
};

} // namespace mvc

#endif // MVC_C_MAINWINDOW_H
