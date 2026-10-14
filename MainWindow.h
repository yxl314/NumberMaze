#ifndef MAINWINDOW_H
#define MAINWINDOW_H

#include <QMainWindow>

namespace mvc {
class BoardView;
class GameModel;
class Controller;
}

class QAction;
class QLabel;

// 应用主窗口：仅作为组合根（composition root）存在。
//
// 职责严格限定为三件事：
//  1. 实例化 View 与 Model
//  2. 创建 Controller 并把 V/M 注入，然后触发连接
//  3. 搭建菜单与状态栏这类"外壳"控件，并把它们登记给 Controller
//
// 它不参与游戏逻辑，也不做信号转接（那是 Controller 的事）。
class MainWindow : public QMainWindow
{
    Q_OBJECT

public:
    explicit MainWindow(QWidget *parent = nullptr);

private:
    void setupMenus();
    void setupToolBar();
    void setupStatusBar();

    mvc::BoardView *m_view = nullptr;
    mvc::GameModel *m_model = nullptr;
    mvc::Controller *m_controller = nullptr;

    QAction *m_undoAction = nullptr;

    QLabel *m_difficultyLabel = nullptr;
    QLabel *m_movesLabel = nullptr;
    QLabel *m_timeLabel = nullptr;
    QLabel *m_bestLabel = nullptr;
};

#endif // MAINWINDOW_H
