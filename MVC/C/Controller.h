#ifndef MVC_C_CONTROLLER_H
#define MVC_C_CONTROLLER_H

#include <QObject>

#include "Difficulty.h"

class QMainWindow;
class QLabel;
class QAction;

namespace mvc {

class BoardView;
class GameModel;

// Controller 层：严格 MVC 的连接器。
//
// 职责（也是本类存在的全部理由）：
//  - 把 View 发出的用户操作信号，接到 Model 的槽函数上
//  - 把 Model 发出的状态变化信号，转接给 View 的槽（喂数据）
//  - 更新菜单、状态栏等"外围"界面文字
//
// 严格约束：
//  - View 与 Model 互不知晓，双方都不持有对方指针
//  - 本类不含游戏规则（在 Core），也不含绘制代码（在 View）
class Controller : public QObject
{
    Q_OBJECT

public:
    // view/model 由 MainWindow 创建并注入；window 用于菜单与状态栏
    Controller(BoardView *view, GameModel *model, QMainWindow *window,
               QObject *parent = nullptr);

    // 建立全部连接，并触发首次数据同步
    void initialize();

    // 供 MainWindow 在构建菜单/状态栏时登记控件，
    // 这样 Controller 只更新它该更新的那几件东西。
    void setStatusLabels(QLabel *difficulty, QLabel *moves,
                         QLabel *time, QLabel *best);
    void setUndoAction(QAction *action);

public slots:
    // 菜单等外壳控件的动作入口。
    // 声明为 public 是因为 MainWindow 需要直接连接它们。
    void handleNewGame(core::Difficulty level);
    void handleRestart();
    void handleUndo();

private slots:
    // 来自 View
    void handleNodeClicked(int index);

    // 来自 Model
    void handleBoardChanged();
    void handleStatusChanged();
    void handleSolved(int moves, int seconds, bool isNewRecord);

private:
    void syncBoardToView();
    void refreshStatusBar();
    void refreshWindowTitle();

    BoardView *m_view = nullptr;
    GameModel *m_model = nullptr;
    QMainWindow *m_window = nullptr;

    QLabel *m_difficultyLabel = nullptr;
    QLabel *m_movesLabel = nullptr;
    QLabel *m_timeLabel = nullptr;
    QLabel *m_bestLabel = nullptr;
    QAction *m_undoAction = nullptr;
};

} // namespace mvc

#endif // MVC_C_CONTROLLER_H
