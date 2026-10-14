/**
 * @file GameModel.h
 * @brief 游戏模型层：数据、Qt 处理逻辑，以及调用 Core 后端。
 *
 * 本文件属于 MVC 的 M 层。
 */

#ifndef MVC_M_GAMEMODEL_H
#define MVC_M_GAMEMODEL_H

#include <QObject>
#include <QSettings>
#include <QString>
#include <QTime>
#include <QVector>

#include "GameState.h"

class QTimer;

namespace mvc {

/**
 * @brief 游戏模型：持有状态、广播变化、计时与成绩记录。
 *
 * @par 职责
 * - 包装 core::GameState，把游戏规则与界面彻底隔离；
 * - 管理计时、步数、最佳成绩等会话级状态；
 * - 把局面整理成视图可直接使用的形状，供控制器搬运。
 *
 * @par 严格 MVC 约束
 * - 本类不知道视图的存在，不持有任何视图指针；
 * - 状态变化一律通过信号广播，由控制器转接给视图；
 * - 所有游戏规则都委托给 core::GameState，本类不自行实现规则。
 */
class GameModel : public QObject
{
    Q_OBJECT

public:
    /**
     * @brief 构造模型。
     *
     * @param parent Qt 父对象。
     */
    explicit GameModel(QObject *parent = nullptr);

    // ---- 供控制器查询的只读数据 ----

    /**
     * @brief 取得当前难度。
     * @return 难度级别。
     */
    core::Difficulty difficulty() const { return m_difficulty; }

    /**
     * @brief 取得当前难度的中文名称。
     * @return 名称字符串。
     */
    QString difficultyText() const;

    /**
     * @brief 取得玩家已走的步数。
     * @return 步数；打乱不计入其中。
     */
    int moveCount() const { return m_state.moveCount(); }

    /**
     * @brief 取得已用时间的秒数。
     * @return 秒数。
     */
    int elapsedSeconds() const { return m_elapsedSeconds; }

    /**
     * @brief 取得已用时间。
     * @return 以 QTime 表示的时长，便于格式化输出。
     */
    QTime elapsed() const { return QTime(0, 0).addSecs(m_elapsedSeconds); }

    /**
     * @brief 是否还有可撤销的步骤。
     * @return 存在撤销历史返回 true。
     */
    bool canUndo() const { return m_state.canUndo(); }

    /**
     * @brief 当前局面是否已还原。
     * @return 已还原返回 true。
     */
    bool isSolved() const { return m_state.isSolved(); }

    /**
     * @brief 取得指定难度的最佳成绩。
     *
     * @param level 难度级别。
     * @return 最少步数；尚无记录时返回 0。
     */
    int bestRecord(core::Difficulty level) const;

    // ---- 供控制器搬运给视图的数据 ----

    /**
     * @brief 取得当前局面。
     *
     * 返回值已整理为视图所需的形状，使视图无需接触 core 命名空间。
     *
     * @return 长度为节点总数的数组，元素为各节点上的数字，0 表示空格。
     */
    QVector<int> boardData() const;

    /**
     * @brief 取得棋盘拓扑的邻接表。
     *
     * 该数据在整个游戏期间不变，控制器只需在初始化时搬运一次。
     *
     * @return 外层索引为节点，内层为其邻居列表。
     */
    QVector<QVector<int>> connectionData() const;

    /**
     * @brief 取得各节点当前是否可移动。
     *
     * @return 与节点一一对应的布尔数组，true 表示可移动。
     */
    QVector<bool> movableData() const;

public slots:
    // ---- 控制器调用的操作入口 ----

    /**
     * @brief 以指定难度开始新的一局。
     *
     * @param level 难度级别。
     */
    void startNewGame(core::Difficulty level);

    /**
     * @brief 以当前难度重新开始一局。
     */
    void startNewGame();

    /**
     * @brief 重开本局：清零计时与步数并重新打乱。
     */
    void restartCurrentGame();

    /**
     * @brief 请求把指定节点上的数字移入空格。
     *
     * 该请求是否成立由 core::GameState 判断，本函数只做转发。
     *
     * @param index 节点索引。
     */
    void requestMove(int index);

    /**
     * @brief 请求撤销上一步。
     */
    void requestUndo();

signals:
    // ---- 广播给控制器的状态变化 ----

    /**
     * @brief 局面已改变。
     *
     * 控制器收到后应取出 boardData() 等数据并转交给视图。
     */
    void boardChanged();

    /**
     * @brief 状态栏相关信息已改变。
     *
     * 涉及步数、用时、可撤销性与最佳成绩。
     */
    void statusChanged();

    /**
     * @brief 计时跳动（每秒一次）。
     */
    void tick();

    /**
     * @brief 本局已通关。
     *
     * @param moves 通关所用步数。
     * @param seconds 通关所用秒数。
     * @param isNewRecord 是否刷新了该难度的最佳成绩。
     */
    void solved(int moves, int seconds, bool isNewRecord);

private slots:
    /**
     * @brief 计时器超时处理，累加秒数并广播变化。
     */
    void onTick();

private:
    /**
     * @brief 启动计时器；已在运行时不重复启动。
     */
    void startTimer();

    /**
     * @brief 停止计时器。
     */
    void stopTimer();

    /**
     * @brief 保存某难度的最佳成绩。
     *
     * @param level 难度级别。
     * @param moves 步数。
     */
    void saveBestRecord(core::Difficulty level, int moves);

    /**
     * @brief 生成某难度在配置中的键名。
     *
     * @param level 难度级别。
     * @return 配置键字符串。
     */
    static QString recordKey(core::Difficulty level);

    /// 游戏状态与规则（来自 Core 层）。
    core::GameState m_state;

    /// 当前难度。
    core::Difficulty m_difficulty = core::Difficulty::Normal;

    /// 计时器。
    QTimer *m_timer = nullptr;

    /// 已用秒数。
    int m_elapsedSeconds = 0;

    /// 计时器是否正在运行。
    bool m_running = false;

    /// 本局是否已判定过通关，避免重复广播 solved 信号。
    bool m_solvedNotified = false;

    /// 成绩持久化。
    QSettings m_settings;
};

} // namespace mvc

#endif // MVC_M_GAMEMODEL_H
