#include "puzzlewidget.h"

#include <QMouseEvent>
#include <QPainter>
#include <QPainterPath>
#include <QFontMetrics>

namespace {

// 配色：沿用原始 demo 的深色风格，并补充状态色
const QColor kBoardColor(40, 44, 52);
const QColor kEdgeColor(96, 104, 118);
const QColor kEdgeHotColor(150, 160, 178);
const QColor kTileColor(70, 130, 220);
const QColor kTileHoverColor(120, 176, 255);
const QColor kTileMovableColor(58, 176, 122);
const QColor kTileTextColor(255, 255, 255);
const QColor kBlankColor(130, 136, 148);
const QColor kSolvedColor(255, 214, 102);

} // namespace

PuzzleWidget::PuzzleWidget(QWidget *parent)
    : QWidget(parent)
{
    setMinimumSize(360, 360);
    setMouseTracking(true);
    setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
    m_initialState = m_model.snapshot();
}

void PuzzleWidget::newGame(Difficulty level)
{
    m_model.scramble(level);
    m_initialState = m_model.snapshot();
    m_solvedMoves = -1;
    m_hover = -1;
    update();
    emit stateChanged();
}

void PuzzleWidget::restart()
{
    m_model.reset();
    // 回到本轮初始局面，而不是回到已解状态
    for (int i = 0; i < m_model.CellCount; ++i)
        m_model.setValueAt(i, m_initialState.value(i));
    m_model.resetMoveCount();
    m_model.clearHistory();
    m_solvedMoves = -1;
    m_hover = -1;
    update();
    emit stateChanged();
}

bool PuzzleWidget::undo()
{
    if (!m_model.undo())
        return false;
    if (m_solvedMoves >= 0)
        m_solvedMoves = -1;
    update();
    emit stateChanged();
    return true;
}

void PuzzleWidget::setShowConnections(bool on)
{
    if (m_showConnections == on)
        return;
    m_showConnections = on;
    update();
}

void PuzzleWidget::setShowHints(bool on)
{
    if (m_showHints == on)
        return;
    m_showHints = on;
    update();
}

QRectF PuzzleWidget::boardRect() const
{
    // 以正方形居中，四周按比例留白
    const qreal side = qMin(width(), height());
    const qreal margin = side * 0.045;
    return QRectF((width() - side) / 2.0 + margin,
                  (height() - side) / 2.0 + margin,
                  side - 2 * margin,
                  side - 2 * margin);
}

QPointF PuzzleWidget::centerOf(int index) const
{
    const QRectF board = boardRect();
    const qreal cell = qMin(board.width(), board.height()) / GraphModel::N;
    const int row = index / GraphModel::N;
    const int col = index % GraphModel::N;
    return QPointF(board.left() + (col + 0.5) * cell,
                   board.top() + (row + 0.5) * cell);
}

qreal PuzzleWidget::circleRadius() const
{
    const QRectF board = boardRect();
    return qMin(board.width(), board.height()) / GraphModel::N * 0.38;
}

int PuzzleWidget::nodeAt(const QPoint &pos) const
{
    const qreal r = circleRadius();
    const QPointF p(pos);
    for (int i = 0; i < GraphModel::CellCount; ++i) {
        const QPointF d = centerOf(i) - p;
        if (d.x() * d.x() + d.y() * d.y() <= r * r)
            return i;
    }
    return -1;
}

void PuzzleWidget::paintEvent(QPaintEvent *)
{
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, true);
    p.setRenderHint(QPainter::TextAntialiasing, true);

    const QRectF board = boardRect();
    const qreal radius = circleRadius();
    const int blank = m_model.blankIndex();

    // 背景
    p.setPen(Qt::NoPen);
    p.setBrush(kBoardColor);
    p.drawRoundedRect(QRectF(rect()).adjusted(2, 2, -2, -2),
                      board.width() * 0.032, board.width() * 0.032);

    if (m_showConnections)
        drawConnections(p, board, radius);

    drawBlankMarker(p, blank, radius);
    drawNodes(p, radius);

    if (m_model.isSolved())
        drawSolvedOverlay(p, board);
}

void PuzzleWidget::drawConnections(QPainter &p, const QRectF &, qreal radius)
{
    // 连线是重要的可玩性辅助：本项目的连接并非规则网格，
    // 玩家需要看清哪些节点彼此相连。
    for (int i = 0; i < GraphModel::CellCount; ++i) {
        for (int j : m_model.neighbors(i)) {
            if (j <= i)
                continue;
            const bool involvesBlank = (i == m_hover || j == m_hover);
            p.setPen(QPen(involvesBlank ? kEdgeHotColor : kEdgeColor,
                          qMax(1.5, radius * 0.115),
                          Qt::SolidLine, Qt::RoundCap));
            p.drawLine(centerOf(i), centerOf(j));
        }
    }
}

void PuzzleWidget::drawBlankMarker(QPainter &p, int blank, qreal radius)
{
    if (blank < 0)
        return;
    p.setPen(QPen(kBlankColor, qMax(1.5, radius * 0.10), Qt::DashLine));
    p.setBrush(Qt::NoBrush);
    p.drawEllipse(centerOf(blank), radius, radius);
}

void PuzzleWidget::drawNodes(QPainter &p, qreal radius)
{
    QFont f = font();
    f.setBold(true);
    // 字号随圆半径缩放，保证窗口缩放后数字仍然清晰且不溢出
    f.setPixelSize(qMax(10, int(radius * 1.05)));
    p.setFont(f);

    for (int i = 0; i < GraphModel::CellCount; ++i) {
        const int value = m_model.valueAt(i);
        if (value == 0)
            continue;

        const QPointF c = centerOf(i);
        const bool movable = m_showHints && m_model.isMovable(i);

        QColor fill = kTileColor;
        if (i == m_hover && movable)
            fill = kTileHoverColor;
        else if (movable)
            fill = kTileMovableColor;

        // 阴影，让圆片有轻微立体感
        p.setPen(Qt::NoPen);
        p.setBrush(QColor(0, 0, 0, 60));
        p.drawEllipse(c + QPointF(0, radius * 0.06), radius, radius);

        p.setBrush(fill);
        p.setPen(QPen(fill.darker(150), qMax(1.0, radius * 0.09)));
        p.drawEllipse(c, radius, radius);

        // 数字：按文本实际宽度收缩，避免两位数贴边或溢出圆形。
        p.setPen(kTileTextColor);
        const QString text = QString::number(value);
        QFontMetrics fm(f);
        const qreal maxWidth = radius * 1.44;
        if (fm.horizontalAdvance(text) > maxWidth) {
            QFont small = f;
            small.setPixelSize(qMax(8, int(f.pixelSize() * maxWidth
                                           / fm.horizontalAdvance(text))));
            p.setFont(small);
        }
        p.drawText(QRectF(c.x() - radius, c.y() - radius, 2 * radius, 2 * radius),
                   Qt::AlignCenter, text);
        p.setFont(f);
    }
}

void PuzzleWidget::drawSolvedOverlay(QPainter &p, const QRectF &board)
{
    p.setPen(Qt::NoPen);
    p.setBrush(QColor(0, 0, 0, 130));
    p.drawRoundedRect(board, board.width() * 0.03, board.width() * 0.03);

    QFont f = font();
    f.setBold(true);
    f.setPixelSize(qMax(16, int(board.width() * 0.085)));
    p.setFont(f);
    p.setPen(kSolvedColor);
    p.drawText(board, Qt::AlignCenter,
               QStringLiteral("完成！\n共 %1 步").arg(m_model.moveCount()));
}

void PuzzleWidget::mousePressEvent(QMouseEvent *event)
{
    if (event->button() != Qt::LeftButton) {
        QWidget::mousePressEvent(event);
        return;
    }

    const int idx = nodeAt(event->pos());
    if (idx < 0 || m_model.isSolved())
        return;

    if (m_model.tryMove(idx)) {
        emit moveMade();

        if (m_model.isSolved()) {
            m_solvedMoves = m_model.moveCount();
            emit solved(m_solvedMoves);
        }
        update();
        emit stateChanged();
    }
}

void PuzzleWidget::mouseMoveEvent(QMouseEvent *event)
{
    const int idx = nodeAt(event->pos());
    if (idx == m_hover)
        return;

    m_hover = idx;
    const bool clickable = m_model.isMovable(idx) && !m_model.isSolved();
    setCursor(clickable ? Qt::PointingHandCursor : Qt::ArrowCursor);
    update();
}

void PuzzleWidget::leaveEvent(QEvent *)
{
    if (m_hover == -1)
        return;
    m_hover = -1;
    setCursor(Qt::ArrowCursor);
    update();
}
