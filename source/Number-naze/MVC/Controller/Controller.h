/**
 * @file Controller.h
 * @brief 控制器：严格 MVC 的连接器。
 *
 * 本文件属于 MVC 的 C 层。
 */

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

/**
 * @brief 控制器，负责在视图与模型之间牵线。
 *
 * @par 职责
 * - 把视图发出的用户操作信号，接到模型的槽函数上；
 * - 把模型发出的状态变化信号，转接给视图的槽，并把数据搬运过去；
 * - 更新菜单、状态栏等外围界面的文字与可用状态。
 *
 * @par 严格约束
 * - 视图与模型互不知晓，双方都不持有对方指针，因此两者之间的
 *   每一条数据通路都必须经过本类；
 * - 本类不含游戏规则（那些在 Core 层），也不含绘制代码（在视图层）。
 *
 * @par 数据流
 * 用户点击视图 → 视图发出 nodeClicked → 本类调用模型的 requestMove
 * → 模型委托 Core 执行并广播 boardChanged → 本类取出数据并调用视图
 * 的 setBoard 等方法 → 视图重绘。
 */
class Controller : public QObject
{
    Q_OBJECT

public:
    /**
     * @brief 构造控制器。
     *
     * 视图与模型由 MainWindow 创建后注入；控制器不负责它们的生命周期。
     *
     * @param view 视图对象，不得为空。
     * @param model 模型对象，不得为空。
     * @param window 主窗口，用于状态栏与弹窗的父对象，可为 nullptr。
     * @param parent Qt 父对象。
     */
    Controller(BoardView *view, GameModel *model, QMainWindow *window,
               QObject *parent = nullptr);

    /**
     * @brief 建立全部连接并触发首次数据同步。
     *
     * 调用一次即可。其中会把拓扑这类不常变化的数据先喂给视图，
     * 并刷新一次状态栏与窗口标题。
     */
    void initialize();

    /**
     * @brief 登记状态栏上的四个标签。
     *
     * @param difficulty 难度标签。
     * @param moves 步数标签。
     * @param time 用时标签。
     * @param best 最佳成绩标签。
     *
     * @note 允许传入 nullptr，对应项将被跳过而不刷新。
     */
    void setStatusLabels(QLabel *difficulty, QLabel *moves,
                         QLabel *time, QLabel *best);

    /**
     * @brief 登记悔棋动作，用于同步其可用状态。
     *
     * @param action 悔棋动作，可为 nullptr。
     */
    void setUndoAction(QAction *action);

public slots:
    // ---- 菜单等外壳控件的动作入口 ----
    // 声明为 public 是因为 MainWindow 需要直接连接它们。

    /**
     * @brief 处理"新游戏"菜单项。
     *
     * @param level 目标难度。
     */
    void handleNewGame(core::Difficulty level);

    /**
     * @brief 处理"重开本局"动作。
     */
    void handleRestart();

    /**
     * @brief 处理"悔棋"动作。
     */
    void handleUndo();

private slots:
    // ---- 来自视图 ----

    /**
     * @brief 处理视图上报的节点点击。
     *
     * 该不该移动、能不能移动由模型与 Core 判断，本槽只做转发。
     *
     * @param index 被点击的节点索引。
     */
    void handleNodeClicked(int index);

    // ---- 来自模型 ----

    /**
     * @brief 处理模型广播的局面变化，把新数据搬运给视图。
     */
    void handleBoardChanged();

    /**
     * @brief 处理模型广播的状态变化，刷新状态栏与窗口标题。
     */
    void handleStatusChanged();

    /**
     * @brief 处理模型广播的通关事件，弹窗告知玩家。
     *
     * @param moves 通关所用步数。
     * @param seconds 通关所用秒数。
     * @param isNewRecord 是否刷新了最佳成绩。
     */
    void handleSolved(int moves, int seconds, bool isNewRecord);

private:
    /**
     * @brief 把模型的局面、可移动性与通关状态搬运给视图。
     *
     * 由于视图不认识模型，这一步只能由控制器完成。
     */
    void syncBoardToView();

    /**
     * @brief 刷新状态栏上的难度、步数、用时与最佳成绩。
     */
    void refreshStatusBar();

    /**
     * @brief 刷新窗口标题，使其反映当前难度。
     */
    void refreshWindowTitle();

    /// 视图对象，由外部注入。
    BoardView *m_view = nullptr;

    /// 模型对象，由外部注入。
    GameModel *m_model = nullptr;

    /// 主窗口，仅用于状态栏与弹窗的父对象。
    QMainWindow *m_window = nullptr;

    /// 状态栏：难度标签。
    QLabel *m_difficultyLabel = nullptr;

    /// 状态栏：步数标签。
    QLabel *m_movesLabel = nullptr;

    /// 状态栏：用时标签。
    QLabel *m_timeLabel = nullptr;

    /// 状态栏：最佳成绩标签。
    QLabel *m_bestLabel = nullptr;

    /// 悔棋动作，用于同步可用状态。
    QAction *m_undoAction = nullptr;
};

} // namespace mvc

#endif // MVC_C_CONTROLLER_H
