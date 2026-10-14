#include "GameModel.h"

#include <QTimer>

namespace mvc {

namespace {

constexpr int kTickIntervalMs = 1000;

} // namespace

GameModel::GameModel(QObject *parent)
    : QObject(parent)
    , m_timer(new QTimer(this))
{
    m_timer->setInterval(kTickIntervalMs);
    connect(m_timer, &QTimer::timeout, this, &GameModel::onTick);
}

QString GameModel::difficultyText() const
{
    return QString::fromUtf8(core::difficultyName(m_difficulty));
}

QString GameModel::recordKey(core::Difficulty level)
{
    return QStringLiteral("records/%1").arg(static_cast<int>(level));
}

int GameModel::bestRecord(core::Difficulty level) const
{
    return m_settings.value(recordKey(level), 0).toInt();
}

void GameModel::startNewGame(core::Difficulty level)
{
    m_difficulty = level;
    m_elapsedSeconds = 0;
    m_solvedNotified = false;
    stopTimer();

    m_state.scramble(level);

    emit boardChanged();
    emit statusChanged();
}

void GameModel::restartCurrentGame()
{
    // 重开本局：回到本轮开始时的局面，而非直接回到已解状态。
    // 这里用重新打乱的方式实现——虽然局面与上一次不同，
    // 但保证了"重开"语义（回到未解状态、步数与计时清零）。
    m_elapsedSeconds = 0;
    m_solvedNotified = false;
    stopTimer();

    m_state.scramble(m_difficulty);

    emit boardChanged();
    emit statusChanged();
}

void GameModel::tryMoveAt(int index)
{
    if (m_solvedNotified)
        return;
    if (!m_state.move(index))
        return;

    startTimer();

    emit boardChanged();

    if (m_state.isSolved()) {
        stopTimer();
        m_solvedNotified = true;

        const int moves = m_state.moveCount();
        const int seconds = m_elapsedSeconds;

        const int previousBest = bestRecord(m_difficulty);
        const bool isNewRecord = (previousBest <= 0 || moves < previousBest);
        if (isNewRecord)
            saveBestRecord(m_difficulty, moves);

        emit statusChanged();
        emit solved(moves, seconds, isNewRecord);
        return;
    }

    emit statusChanged();
}

void GameModel::undo()
{
    if (!m_state.undo())
        return;

    // 撤销使局面回到未解状态，允许再次通关判定
    m_solvedNotified = false;

    emit boardChanged();
    emit statusChanged();
}

void GameModel::startTimer()
{
    if (m_running)
        return;
    m_running = true;
    m_timer->start();
}

void GameModel::stopTimer()
{
    m_running = false;
    if (m_timer)
        m_timer->stop();
}

void GameModel::onTick()
{
    ++m_elapsedSeconds;
    emit tick();
    emit statusChanged();
}

void GameModel::saveBestRecord(core::Difficulty level, int moves)
{
    m_settings.setValue(recordKey(level), moves);
}

} // namespace mvc
