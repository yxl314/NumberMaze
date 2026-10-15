/**
 * @file MainWindow.h
 * @brief 应用主窗口，作为整个程序的组合根。
 */

#ifndef SOURCE_NUMBERNAZE_MVC_MAINWINDOW_H
#define SOURCE_NUMBERNAZE_MVC_MAINWINDOW_H

#include <QMainWindow>

namespace mvc
{
class BoardView;
class GameModel;
class Controller;
} // namespace mvc

class QAction;
class QLabel;

/**
 * @brief 主窗口：组合根（composition root）。
 *
 * @par 职责
 * 严格限定为三件事：
 *  1. 实例化视图与模型（两者互不相识，构造时不互相传递）；
 *  2. 创建控制器并把视图、模型注入，随后触发连接；
 *  3. 搭建菜单、工具栏与状态栏这类外壳控件，并把它们登记给控制器。
 *
 * @par 不负责的事
 * 本类不参与游戏逻辑，也不做信号转接——后者是控制器的职责。
 * 本类只是把各个部件组装起来并显示窗口。
 */
class MainWindow : public QMainWindow
{
    Q_OBJECT

public:
    /**
     * @brief 构造主窗口，完成全部部件的组装。
     *
     * @param v_parent Qt 父窗口。
     */
    explicit MainWindow(QWidget* v_parent = nullptr);

private:
    /**
     * @brief 搭建菜单栏，包含难度选择、重开、悔棋与退出。
     *
     * @note 必须在控制器创建之后调用，因为菜单动作要连接到控制器上。
     */
    void             setupMenus();

    /**
     * @brief 搭建工具栏，包含重开本局与悔棋两个快捷入口。
     */
    void             setupToolBar();

    /**
     * @brief 搭建状态栏，创建难度、步数、用时与最佳成绩四个标签。
     */
    void             setupStatusBar();

    /// 视图对象，由本类创建并交由控制器使用。
    mvc::BoardView*  m_view            = nullptr;

    /// 模型对象，由本类创建并交由控制器使用。
    mvc::GameModel*  m_model           = nullptr;

    /// 控制器对象，由本类创建并注入视图与模型。
    mvc::Controller* m_controller      = nullptr;

    /// 悔棋动作，需登记给控制器以同步可用状态。
    QAction*         m_undoAction      = nullptr;

    /// 状态栏：难度标签。
    QLabel*          m_difficultyLabel = nullptr;

    /// 状态栏：步数标签。
    QLabel*          m_movesLabel      = nullptr;

    /// 状态栏：用时标签。
    QLabel*          m_timeLabel       = nullptr;

    /// 状态栏：最佳成绩标签。
    QLabel*          m_bestLabel       = nullptr;
};

#endif // SOURCE_NUMBERNAZE_MVC_MAINWINDOW_H
