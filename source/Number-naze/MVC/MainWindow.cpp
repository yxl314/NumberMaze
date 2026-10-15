#include "MainWindow.h"

#include <QAction>
#include <QApplication>
#include <QLabel>
#include <QMenu>
#include <QMenuBar>
#include <QMessageBox>
#include <QStatusBar>
#include <QToolBar>

#include "BoardView.h"
#include "Controller.h"
#include "GameModel.h"

/**
 * @brief 构造主窗口，按顺序完成组装。
 */
MainWindow::MainWindow(QWidget *parent)
    : QMainWindow(parent)
{
    setWindowTitle(QStringLiteral("数字迷宫"));

    // ---- 1. 实例化 View 与 Model ----
    // 两者互不相识：构造 View 时不传 Model，构造 Model 时不知道 View。
    m_view = new mvc::BoardView(this);
    m_model = new mvc::GameModel(this);

    setCentralWidget(m_view);

    // ---- 2. 创建控制器并注入 V/M ----
    // 必须先于菜单创建：菜单动作要连接到控制器上。
    m_controller = new mvc::Controller(m_view, m_model, this, this);

    // ---- 3. 搭建外壳控件（菜单、工具栏、状态栏）----
    setupMenus();
    setupToolBar();
    setupStatusBar();

    // ---- 4. 登记控件并建立全部连接 ----
    m_controller->setStatusLabels(m_difficultyLabel, m_movesLabel,
                                  m_timeLabel, m_bestLabel);
    m_controller->setUndoAction(m_undoAction);
    m_controller->initialize();

    resize(620, 760);

    // 开局：交给 Model 产生首局，变化会经 Controller 流到 View
    m_model->startNewGame(core::Difficulty::Normal);
}

/**
 * @brief 搭建菜单栏。
 */
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
        connect(act, &QAction::triggered, this, [this, lvl] {
            m_controller->handleNewGame(lvl);
        });
    }

    gameMenu->addSeparator();

    QAction *restartAct = gameMenu->addAction(QStringLiteral("重开本局(&R)"));
    restartAct->setShortcut(QKeySequence(QStringLiteral("Ctrl+R")));
    connect(restartAct, &QAction::triggered,
            m_controller, &mvc::Controller::handleRestart);

    m_undoAction = gameMenu->addAction(QStringLiteral("悔棋(&U)"));
    m_undoAction->setShortcut(QKeySequence::Undo);
    m_undoAction->setEnabled(false);
    connect(m_undoAction, &QAction::triggered,
            m_controller, &mvc::Controller::handleUndo);

    gameMenu->addSeparator();

    QAction *quitAct = gameMenu->addAction(QStringLiteral("退出(&Q)"));
    quitAct->setShortcut(QKeySequence::Quit);
    connect(quitAct, &QAction::triggered, qApp, &QApplication::quit);

    QMenu *helpMenu = menuBar()->addMenu(QStringLiteral("帮助(&H)"));
    QAction *aboutAct = helpMenu->addAction(QStringLiteral("关于(&A)"));
    connect(aboutAct, &QAction::triggered, this, [this] {
        QMessageBox::about(this, QStringLiteral("关于 数字迷宫"),
            QStringLiteral(
                "<h3>数字迷宫</h3>"
                "<p>把 1 到 15 的数字重新排回顺序，空格归位右下角即可通关。</p>"
                "<p>本作的连接并非规则网格，而是包含对角线在内的特殊拓扑，"
                "因此不能凭空间直觉判断可移动性，难度高于普通数字华容道。</p>"
                "<p>点击与空格相连的数字即可将其滑入空格。"
                "绿色高亮表示当前可移动，连线展示了完整的连接关系。</p>"));
    });
}

/**
 * @brief 搭建工具栏。
 */
void MainWindow::setupToolBar()
{
    QToolBar *bar = addToolBar(QStringLiteral("操作"));
    bar->setMovable(false);

    QAction *restartAct = new QAction(QStringLiteral("重开本局(&R)"), this);
    connect(restartAct, &QAction::triggered,
            m_controller, &mvc::Controller::handleRestart);

    bar->addAction(restartAct);
    bar->addAction(m_undoAction);
}

/**
 * @brief 搭建状态栏并创建四个标签。
 */
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
