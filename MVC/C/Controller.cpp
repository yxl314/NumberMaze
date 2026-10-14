#include "Controller.h"

#include <QAction>
#include <QLabel>
#include <QMainWindow>
#include <QMessageBox>
#include <QTime>

#include "BoardView.h"
#include "GameModel.h"

namespace mvc {

Controller::Controller(BoardView *view, GameModel *model, QMainWindow *window,
                       QObject *parent)
    : QObject(parent)
    , m_view(view)
    , m_model(model)
    , m_window(window)
{
}

void Controller::setStatusLabels(QLabel *difficulty, QLabel *moves,
                                 QLabel *time, QLabel *best)
{
    m_difficultyLabel = difficulty;
    m_movesLabel = moves;
    m_timeLabel = time;
    m_bestLabel = best;
}

void Controller::setUndoAction(QAction *action)
{
    m_undoAction = action;
}

// 这里是严格 MVC 的核心：V 与 M 之间的每一条通路都必须经过本函数建立的连接。
void Controller::initialize()
{
    // ---- View -> Controller -> Model ----
    // View 只抛出"用户点了哪个节点"，由本类决定调用 Model 的哪个函数
    connect(m_view, &BoardView::nodeClicked,
            this, &Controller::handleNodeClicked);

    // ---- Model -> Controller -> View ----
    // Model 只广播"我变了"，由本类把数据取出来喂给 View
    connect(m_model, &GameModel::boardChanged,
            this, &Controller::handleBoardChanged);
    connect(m_model, &GameModel::statusChanged,
            this, &Controller::handleStatusChanged);
    connect(m_model, &GameModel::solved,
            this, &Controller::handleSolved);

    // ---- 初始同步 ----
    // 把 View 需要的静态信息（拓扑）先喂进去，
    // 之后每次局面变化只需更新动态部分。
    m_view->setConnections(m_model->connectionData());
    syncBoardToView();
    refreshStatusBar();
    refreshWindowTitle();
}

// ---------- 来自 View ----------

void Controller::handleNodeClicked(int index)
{
    // 该不该移动、能不能移动都由 Model/Core 判断，
    // 控制器只做转发。
    m_model->requestMove(index);
}

// ---------- 来自 Model ----------

void Controller::handleBoardChanged()
{
    syncBoardToView();
}

void Controller::handleStatusChanged()
{
    refreshStatusBar();
    refreshWindowTitle();
}

void Controller::handleSolved(int moves, int seconds, bool isNewRecord)
{
    refreshStatusBar();

    const QTime t = QTime(0, 0).addSecs(seconds);
    QString text = QStringLiteral("恭喜完成！\n\n难度：%1\n用步数：%2\n耗时：%3")
                       .arg(m_model->difficultyText())
                       .arg(moves)
                       .arg(t.toString(QStringLiteral("hh:mm:ss")));
    if (isNewRecord)
        text += QStringLiteral("\n\n这是该难度的新纪录！");

    QMessageBox box(QMessageBox::Information, QStringLiteral("数字迷宫"),
                    text, QMessageBox::Ok, m_window);
    box.exec();
}

// ---------- 来自菜单 ----------

void Controller::handleNewGame(core::Difficulty level)
{
    m_model->startNewGame(level);
}

void Controller::handleRestart()
{
    m_model->restartCurrentGame();
}

void Controller::handleUndo()
{
    m_model->requestUndo();
}

// ---------- 数据搬运与界面刷新 ----------

void Controller::syncBoardToView()
{
    // 把 Model 的数据搬给 View。View 不认识 Model，
    // 因此这一步只能由控制器完成。
    m_view->setBoard(m_model->boardData());
    m_view->setMovable(m_model->movableData());
    m_view->setSolved(m_model->isSolved(), m_model->moveCount());
}

void Controller::refreshStatusBar()
{
    if (m_difficultyLabel)
        m_difficultyLabel->setText(
            QStringLiteral("难度：%1").arg(m_model->difficultyText()));

    if (m_movesLabel)
        m_movesLabel->setText(
            QStringLiteral("步数：%1").arg(m_model->moveCount()));

    if (m_timeLabel)
        m_timeLabel->setText(
            QStringLiteral("用时：%1")
                .arg(m_model->elapsed().toString(QStringLiteral("hh:mm:ss"))));

    if (m_bestLabel) {
        const int best = m_model->bestRecord(m_model->difficulty());
        m_bestLabel->setText(best > 0
                                 ? QStringLiteral("最佳：%1 步").arg(best)
                                 : QStringLiteral("最佳：暂无"));
    }

    if (m_undoAction)
        m_undoAction->setEnabled(m_model->canUndo());
}

void Controller::refreshWindowTitle()
{
    if (m_window)
        m_window->setWindowTitle(
            QStringLiteral("数字迷宫 - %1").arg(m_model->difficultyText()));
}

} // namespace mvc
