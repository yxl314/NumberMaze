#include "GameModel.h"

#include <QTimer>

namespace mvc
{

namespace
{

constexpr int g_tick_interval_ms = 1000;

} // namespace

/**
 * @brief 构造模型并配置计时器。
 */
GameModel::GameModel(QObject* v_parent): QObject(v_parent), m_timer(new QTimer(this))
{
    m_timer->setInterval(g_tick_interval_ms);
    connect(m_timer, &QTimer::timeout, this, &GameModel::onTick);
}

/**
 * @brief 取得当前难度的中文名称。
 */
QString GameModel::difficultyText() const
{
    return QString::fromUtf8(core::difficultyName(m_difficulty));
}

/**
 * @brief 生成某难度在配置中的键名。
 */
QString GameModel::recordKey(core::Difficulty v_level)
{
    return QStringLiteral("records/%1").arg(static_cast<int>(v_level));
}

/**
 * @brief 取得指定难度的最佳成绩。
 */
int GameModel::bestRecord(core::Difficulty v_level) const
{
    return m_settings.value(recordKey(v_level), 0).toInt();
}

// ---------- 供控制器搬运给 View 的数据 ----------

/**
 * @brief 取得当前局面，供控制器搬运给视图。
 */
QVector<int> GameModel::boardData() const
{
    const std::vector<int> raw = m_state.board();
    QVector<int>           out;
    out.reserve(static_cast<int>(raw.size()));
    for (int v : raw)
    {
        out.append(v);
    }
    return out;
}

/**
 * @brief 取得棋盘拓扑的邻接表。
 */
QVector<QVector<int>> GameModel::connectionData() const
{
    const core::Graph&    g = m_state.graph();
    QVector<QVector<int>> out;
    out.reserve(core::g_node_count);
    for (int i = 0; i < core::g_node_count; ++i)
    {
        QVector<int> nbrs;
        nbrs.reserve(static_cast<int>(g.degree(i)));
        for (int nb : g.neighbors(i))
        {
            nbrs.append(nb);
        }
        out.append(nbrs);
    }
    return out;
}

/**
 * @brief 取得各节点当前是否可移动。
 */
QVector<bool> GameModel::movableData() const
{
    QVector<bool> out;
    out.reserve(core::g_node_count);
    for (int i = 0; i < core::g_node_count; ++i)
    {
        out.append(m_state.isMovable(i));
    }
    return out;
}

// ---------- 操作 ----------

/**
 * @brief 以指定难度开始新的一局。
 */
void GameModel::startNewGame(core::Difficulty v_level)
{
    m_difficulty     = v_level;
    m_elapsedSeconds = 0;
    m_solvedNotified = false;
    stopTimer();

    m_state.scramble(v_level);

    emit boardChanged();
    emit statusChanged();
}

/**
 * @brief 以当前难度开始新的一局。
 */
void GameModel::startNewGame()
{
    startNewGame(m_difficulty);
}

/**
 * @brief 重开本局：清零计时与步数并重新打乱。
 */
void GameModel::restartCurrentGame()
{
    m_elapsedSeconds = 0;
    m_solvedNotified = false;
    stopTimer();

    // 恢复到本局开始的局面，使"重开"名副其实
    m_state.restore(m_initialBoard);

    emit boardChanged();
    emit statusChanged();
}

/**
 * @brief 请求把指定节点上的数字移入空格。
 */
void GameModel::requestMove(int v_index)
{
    if (m_solvedNotified)
    {
        return;
    }
    if (!m_state.move(v_index))
    {
        return;
    }

    startTimer();
    emit boardChanged();

    if (m_state.isSolved())
    {
        stopTimer();
        m_solvedNotified          = true;

        const int  moves          = m_state.moveCount();
        const int  seconds        = m_elapsedSeconds;

        const int  previous       = bestRecord(m_difficulty);
        const bool _is_new_record = (previous <= 0 || moves < previous);
        if (_is_new_record)
        {
            saveBestRecord(m_difficulty, moves);
        }

        emit statusChanged();
        emit solved(moves, seconds, _is_new_record);
        return;
    }

    emit statusChanged();
}

/**
 * @brief 请求撤销上一步。
 */
void GameModel::requestUndo()
{
    if (!m_state.undo())
    {
        return;
    }

    // 撤销后局面不再处于通关状态，允许再次判定
    m_solvedNotified = false;

    emit boardChanged();
    emit statusChanged();
}

// ---------- 计时 ----------

/**
 * @brief 启动计时器；已在运行时不重复启动。
 */
void GameModel::startTimer()
{
    if (m_running)
    {
        return;
    }
    m_running = true;
    m_timer->start();
}

/**
 * @brief 停止计时器。
 */
void GameModel::stopTimer()
{
    m_running = false;
    if (m_timer)
    {
        m_timer->stop();
    }
}

/**
 * @brief 计时器超时处理，累加秒数并广播变化。
 */
void GameModel::onTick()
{
    ++m_elapsedSeconds;
    emit tick();
    emit statusChanged();
}

/**
 * @brief 保存某难度的最佳成绩。
 */
void GameModel::saveBestRecord(core::Difficulty v_level, int v_moves)
{
    m_settings.setValue(recordKey(v_level), v_moves);
}

} // namespace mvc
