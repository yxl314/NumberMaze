#include "gamewindow.h"

#include <QAction>
#include <QActionGroup>
#include <QApplication>
#include <QDateTime>
#include <QLabel>
#include <QMenu>
#include <QMenuBar>
#include <QMessageBox>
#include <QStatusBar>
#include <QTimer>
#include <QTime>
#include <QToolBar>

#include "puzzlewidget.h"

namespace {

const int kSecondInterval = 1000;

} // namespace

GameWindow::GameWindow(QWidget *parent)
    : QMainWindow(parent)
{
    setWindowTitle(QStringLiteral("数字迷宫"));
    m_puzzle = new PuzzleWidget(this);
    setCentralWidget(m_puzzle);

    buildActions();
    buildMenus();
    buildStatusBar();

    m_timer = new QTimer(this);
    m_timer->setInterval(kSecondInterval);
    connect(m_timer, &QTimer::timeout, this, &GameWindow::onTick);

    connect(m_puzzle, &PuzzleWidget::solved, this, &GameWindow::onSolved);
    connect(m_puzzle, &PuzzleWidget::stateChanged, this, &GameWindow::onStateChanged);

    resize(620, 720);
    onNewGame(m_difficulty);
}

QString GameWindow::difficultyName(Difficulty level)
{
    switch (level) {
    case Difficulty::Easy:   return QStringLiteral("简单");
    case Difficulty::Normal: return QStringLiteral("普通");
    case Difficulty::Hard:   return QStringLiteral("困难");
    case Difficulty::Expert: return QStringLiteral("专家");
    }
    return QStringLiteral("普通");
}

QString GameWindow::recordKey(Difficulty level)
{
    return QStringLiteral("records/%1").arg(int(level));
}

void GameWindow::buildActions()
{
    m_undoAction = new QAction(QStringLiteral("悔棋(&U)"), this);
    m_undoAction->setShortcut(QKeySequence::Undo);
    connect(m_undoAction, &QAction::triggered, this, &GameWindow::onUndo);

    m_showConnectionsAction = new QAction(QStringLiteral("显示连线(&L)"), this);
    m_showConnectionsAction->setCheckable(true);
    m_showConnectionsAction->setChecked(true);
    connect(m_showConnectionsAction, &QAction::toggled,
            m_puzzle, &PuzzleWidget::setShowConnections);

    m_showHintsAction = new QAction(QStringLiteral("高亮可移动(&H)"), this);
    m_showHintsAction->setCheckable(true);
    m_showHintsAction->setChecked(true);
    connect(m_showHintsAction, &QAction::toggled,
            m_puzzle, &PuzzleWidget::setShowHints);
}

void GameWindow::buildMenus()
{
    QMenu *gameMenu = menuBar()->addMenu(QStringLiteral("游戏(&G)"));

    QMenu *newMenu = gameMenu->addMenu(QStringLiteral("新游戏(&N)"));
    QActionGroup *group = new QActionGroup(this);
    group->setExclusive(true);

    const QList<Difficulty> levels = {
        Difficulty::Easy, Difficulty::Normal, Difficulty::Hard, Difficulty::Expert
    };
    for (Difficulty level : levels) {
        QAction *act = newMenu->addAction(difficultyName(level));
        act->setCheckable(true);
        act->setChecked(level == m_difficulty);
        group->addAction(act);
        connect(act, &QAction::triggered, this, [this, level] { onNewGame(level); });
    }

    gameMenu->addSeparator();
    QAction *restartAct = gameMenu->addAction(QStringLiteral("重开本局(&R)"));
    restartAct->setShortcut(QKeySequence(QStringLiteral("Ctrl+R")));
    connect(restartAct, &QAction::triggered, this, &GameWindow::onRestart);

    gameMenu->addAction(m_undoAction);
    gameMenu->addSeparator();

    QAction *quitAct = gameMenu->addAction(QStringLiteral("退出(&Q)"));
    quitAct->setShortcut(QKeySequence::Quit);
    connect(quitAct, &QAction::triggered, qApp, &QApplication::quit);

    QMenu *viewMenu = menuBar()->addMenu(QStringLiteral("显示(&V)"));
    viewMenu->addAction(m_showConnectionsAction);
    viewMenu->addAction(m_showHintsAction);

    QMenu *helpMenu = menuBar()->addMenu(QStringLiteral("帮助(&H)"));
    QAction *aboutAct = helpMenu->addAction(QStringLiteral("关于(&A)"));
    connect(aboutAct, &QAction::triggered, this, [this] {
        QMessageBox::about(this, QStringLiteral("关于 数字迷宫"),
            QStringLiteral(
                "<h3>数字迷宫</h3>"
                "<p>把 1 到 15 的数字重新排回顺序，空格位于右下角。</p>"
                "<p>本作的连接并非规则的上下左右网格，而是包含对角线在内的"
                "特殊拓扑，因此不能凭空间直觉判断，难度高于普通数字华容道。</p>"
                "<p>点击与空格相连的数字即可将其滑入空格。</p>"));
    });
}

void GameWindow::buildStatusBar()
{
    m_difficultyLabel = new QLabel(this);
    m_movesLabel = new QLabel(this);
    m_timeLabel = new QLabel(this);
    m_bestLabel = new QLabel(this);

    statusBar()->addWidget(m_difficultyLabel);
    statusBar()->addWidget(m_movesLabel);
    statusBar()->addWidget(m_timeLabel);
    statusBar()->addPermanentWidget(m_bestLabel);

    QToolBar *bar = addToolBar(QStringLiteral("操作"));
    bar->setMovable(false);
    bar->addAction(m_undoAction);
    bar->addSeparator();
    bar->addAction(m_showConnectionsAction);
    bar->addAction(m_showHintsAction);
}

void GameWindow::onNewGame(Difficulty level)
{
    m_difficulty = level;
    m_elapsedSeconds = 0;
    m_running = false;
    stopTimer();

    m_puzzle->newGame(level);
    refreshStatus();
}

void GameWindow::onRestart()
{
    m_elapsedSeconds = 0;
    m_running = false;
    stopTimer();

    m_puzzle->restart();
    refreshStatus();
}

void GameWindow::onUndo()
{
    if (m_puzzle->undo())
        refreshStatus();
}

void GameWindow::onSolved(int moves)
{
    stopTimer();
    const int best = bestRecord(m_difficulty);
    const bool isNewRecord = (best <= 0 || moves < best);
    if (isNewRecord)
        saveBestRecord(m_difficulty, moves);

    refreshStatus();

    QString text = QStringLiteral("恭喜完成！\n\n难度：%1\n用步数：%2\n耗时：%3")
                       .arg(difficultyName(m_difficulty))
                       .arg(moves)
                       .arg(m_timeLabel->text().section(QStringLiteral("："), 1));
    if (isNewRecord)
        text += QStringLiteral("\n\n这是该难度的新纪录！");

    QMessageBox box(QMessageBox::Information, QStringLiteral("数字迷宫"), text,
                    QMessageBox::Ok, this);
    box.exec();
}

void GameWindow::onStateChanged()
{
    startTimerIfNeeded();
    refreshStatus();
}

void GameWindow::onTick()
{
    ++m_elapsedSeconds;
    refreshStatus();
}

void GameWindow::startTimerIfNeeded()
{
    if (m_puzzle->model().isSolved())
        return;
    if (m_puzzle->model().moveCount() <= 0)
        return;
    if (m_running)
        return;

    m_running = true;
    m_timer->start();
}

void GameWindow::stopTimer()
{
    m_running = false;
    if (m_timer)
        m_timer->stop();
}

void GameWindow::refreshStatus()
{
    const GraphModel &model = m_puzzle->model();

    m_difficultyLabel->setText(QStringLiteral("难度：%1").arg(difficultyName(m_difficulty)));
    m_movesLabel->setText(QStringLiteral("步数：%1").arg(model.moveCount()));
    m_timeLabel->setText(QStringLiteral("用时：%1")
                             .arg(QTime(0, 0).addSecs(m_elapsedSeconds)
                                      .toString(QStringLiteral("hh:mm:ss"))));

    const int best = bestRecord(m_difficulty);
    m_bestLabel->setText(best > 0 ? QStringLiteral("最佳：%1 步").arg(best)
                                  : QStringLiteral("最佳：暂无"));

    m_undoAction->setEnabled(model.canUndo());
}

void GameWindow::saveBestRecord(Difficulty level, int moves)
{
    m_settings.setValue(recordKey(level), moves);
}

int GameWindow::bestRecord(Difficulty level) const
{
    return m_settings.value(recordKey(level), 0).toInt();
}
