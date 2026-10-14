#include "MainWindow.h"

#include <QAction>
#include <QActionGroup>
#include <QApplication>
#include <QLabel>
#include <QMenu>
#include <QMenuBar>
#include <QMessageBox>
#include <QStatusBar>
#include <QToolBar>

#include "BoardView.h"

namespace mvc {

MainWindow::MainWindow(QWidget *parent)
    : QMainWindow(parent)
    , m_model(new GameModel(this))
    , m_view(new BoardView(m_model, this))
{
    setWindowTitle(QStringLiteral("数字迷宫"));
    setCentralWidget(m_view);

    setupActions();
    setupMenus();
    setupToolBar();
    setupStatusBar();

    // View -> Controller -> Model
    connect(m_view, &BoardView::nodeClicked, this, &MainWindow::onNodeClicked);

    // Model -> Controller -> View
    connect(m_model, &GameModel::boardChanged, this, &MainWindow::onBoardChanged);
    connect(m_model, &GameModel::statusChanged, this, &MainWindow::onStatusChanged);
    connect(m_model, &GameModel::solved, this, &MainWindow::onSolved);

    resize(620, 760);
    m_model->startNewGame(core::Difficulty::Normal);
}

void MainWindow::setupActions()
{
    m_undoAction = new QAction(QStringLiteral("悔棋(&U)"), this);
    m_undoAction->setShortcut(QKeySequence::Undo);
    m_undoAction->setEnabled(false);
    connect(m_undoAction, &QAction::triggered, this, &MainWindow::onUndo);

    m_difficultyGroup = new QActionGroup(this);
    m_difficultyGroup->setExclusive(true);
}

void MainWindow::setupMenus()
{
    QMenu *gameMenu = menuBar()->addMenu(QStringLiteral("游戏(&G)"));

    QMenu *newMenu = gameMenu->addMenu(QStringLiteral("新游戏(&N)"));
    const core::Difficulty levels[] = {
        core::Difficulty::Easy, core::Difficulty::Normal,
        core::Difficulty::Hard, core::Difficulty::Expert
    };
    for (core::Difficulty lvl : levels) {
        QAction *act = newMenu->addAction(QString::fromUtf8(core::difficultyName(lvl)));
        act->setCheckable(true);
        act->setChecked(lvl == core::Difficulty::Normal);
        m_difficultyGroup->addAction(act);
        connect(act, &QAction::triggered, this, [this, lvl] { onNewGame(lvl); });
    }

    gameMenu->addSeparator();

    QAction *restartAct = gameMenu->addAction(QStringLiteral("重开本局(&R)"));
    restartAct->setShortcut(QKeySequence(QStringLiteral("Ctrl+R")));
    connect(restartAct, &QAction::triggered, this, &MainWindow::onRestart);

    gameMenu->addAction(m_undoAction);
    gameMenu->addSeparator();

    QAction *quitAct = gameMenu->addAction(QStringLiteral("退出(&Q)"));
    quitAct->setShortcut(QKeySequence::Quit);
    connect(quitAct, &QAction::triggered, qApp, &QApplication::quit);

    QMenu *helpMenu = menuBar()->addMenu(QStringLiteral("帮助(&H)"));
    QAction *aboutAct = helpMenu->addAction(QStringLiteral("关于(&A)"));
    connect(aboutAct, &QAction::triggered, this, &MainWindow::showAbout);
}

void MainWindow::setupToolBar()
{
    QToolBar *bar = addToolBar(QStringLiteral("操作"));
    bar->setMovable(false);

    QAction *restartAct = new QAction(QStringLiteral("重开本局(&R)"), this);
    connect(restartAct, &QAction::triggered, this, &MainWindow::onRestart);

    bar->addAction(restartAct);
    bar->addAction(m_undoAction);
}

void MainWindow::setupStatusBar()
{
    m_difficultyLabel = new QLabel(this);
    m_movesLabel = new QLabel(this);
    m_timeLabel = new QLabel(this);
    m_bestLabel = new QLabel(this);

    statusBar()->addWidget(m_difficultyLabel);
    statusBar()->addWidget(m_movesLabel);
    statusBar()->addWidget(m_timeLabel);
    statusBar()->addPermanentWidget(m_bestLabel);
}

// ---------- 槽函数 ----------

void MainWindow::onNewGame(core::Difficulty level)
{
    m_model->startNewGame(level);
    refreshWindowTitle();
}

void MainWindow::onRestart()
{
    m_model->restartCurrentGame();
}

void MainWindow::onUndo()
{
    m_model->undo();
}

void MainWindow::onNodeClicked(int index)
{
    // 该不该移动、能不能移动，由 Model/Core 判断；
    // 控制器只负责把意图转发过去。
    m_model->tryMoveAt(index);
}

void MainWindow::onBoardChanged()
{
    m_view->update();
    refreshStatus();
}

void MainWindow::onStatusChanged()
{
    refreshStatus();
}

void MainWindow::onSolved(int moves, int seconds, bool isNewRecord)
{
    refreshStatus();

    const QTime t = QTime(0, 0).addSecs(seconds);
    QString text = QStringLiteral("恭喜完成！\n\n难度：%1\n用步数：%2\n耗时：%3")
                       .arg(m_model->difficultyText())
                       .arg(moves)
                       .arg(t.toString(QStringLiteral("hh:mm:ss")));
    if (isNewRecord)
        text += QStringLiteral("\n\n这是该难度的新纪录！");

    QMessageBox box(QMessageBox::Information, QStringLiteral("数字迷宫"),
                    text, QMessageBox::Ok, this);
    box.exec();
}

void MainWindow::showAbout()
{
    QMessageBox::about(this, QStringLiteral("关于 数字迷宫"),
        QStringLiteral(
            "<h3>数字迷宫</h3>"
            "<p>把 1 到 15 的数字重新排回顺序，空格归位右下角即可通关。</p>"
            "<p>本作的连接并非规则网格，而是包含对角线在内的特殊拓扑，"
            "因此不能凭空间直觉判断可移动性，难度高于普通数字华容道。</p>"
            "<p>点击与空格相连的数字即可将其滑入空格。"
            "绿色高亮表示当前可移动，连线展示了完整的连接关系。</p>"));
}

// ---------- 界面刷新 ----------

void MainWindow::refreshStatus()
{
    m_difficultyLabel->setText(
        QStringLiteral("难度：%1").arg(m_model->difficultyText()));
    m_movesLabel->setText(
        QStringLiteral("步数：%1").arg(m_model->moveCount()));
    m_timeLabel->setText(
        QStringLiteral("用时：%1")
            .arg(m_model->elapsed().toString(QStringLiteral("hh:mm:ss"))));

    const int best = m_model->bestRecord(m_model->difficulty());
    m_bestLabel->setText(best > 0 ? QStringLiteral("最佳：%1 步").arg(best)
                                  : QStringLiteral("最佳：暂无"));

    if (m_undoAction)
        m_undoAction->setEnabled(m_model->canUndo());
}

void MainWindow::refreshWindowTitle()
{
    setWindowTitle(QStringLiteral("数字迷宫 - %1").arg(m_model->difficultyText()));
}

} // namespace mvc
