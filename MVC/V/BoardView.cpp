#include "BoardView.h"

#include <QFontMetrics>
#include <QMouseEvent>
#include <QPainter>

namespace mvc {

namespace {

// 配色：沿用原始 demo 的深色风格
const QColor kBoardColor(40, 44, 52);
const QColor kEdgeColor(96, 104, 118);
const QColor kEdgeHotColor(152, 162, 180);
const QColor kTileColor(70, 130, 220);
const QColor kTileHoverColor(120, 176, 255);
const QColor kTileMovableColor(58, 176, 122);
const QColor kTileTextColor(255, 255, 255);
const QColor kBlankColor(130, 136, 148);
const QColor kSolvedColor(255, 214, 102);

} // namespace

/**
 * @brief 构造视图并设置尺寸策略。
 */
BoardView::BoardView(QWidget *parent)
    : QWidget(parent)
{
    setMouseTracking(true);
    setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
    setMinimumSize(minimumSizeHint());
}

QSize BoardView::sizeHint() const
{
    return QSize(560, 560);
}

QSize BoardView::minimumSizeHint() const
{
    return QSize(320, 320);
}

// ---------- 由控制器喂入的数据 ----------

/**
 * @brief 设置完整局面并触发重绘。
 */
void BoardView::setBoard(const QVector<int> &board)
{
    m_board = board;

    // 依据节点总数推断方阵边长，使 View 不依赖 Core 的常量
    const int total = m_board.size();
    int side = 1;
    while (side * side < total)
        ++side;
    m_boardSize = side;

    if (m_movable.size() != total)
        m_movable = QVector<bool>(total, false);

    update();
}

/**
 * @brief 设置连接关系并触发重绘。
 */
void BoardView::setConnections(const QVector<QVector<int>> &adjacency)
{
    m_adjacency = adjacency;
    update();
}

/**
 * @brief 设置各节点是否可移动并触发重绘。
 */
void BoardView::setMovable(const QVector<bool> &movable)
{
    m_movable = movable;
    update();
}

/**
 * @brief 设置通关状态。
 */
void BoardView::setSolved(bool solved, int moves)
{
    if (m_solved == solved && m_solvedMoves == moves)
        return;

    m_solved = solved;
    m_solvedMoves = moves;

    if (m_solved)
        setCursor(Qt::ArrowCursor);

    update();
}

// ---------- 几何计算 ----------

/**
 * @brief 计算棋盘绘制区域。
 */
QRectF BoardView::boardRect() const
{
    const qreal side = qMin(width(), height());
    const qreal margin = side * 0.045;
    return QRectF((width() - side) / 2.0 + margin,
                  (height() - side) / 2.0 + margin,
                  side - 2 * margin,
                  side - 2 * margin);
}

/**
 * @brief 由行列换算节点索引。
 */
int BoardView::indexAt(int row, int col) const
{
    return row * m_boardSize + col;
}

/**
 * @brief 计算节点中心的屏幕坐标。
 */
QPointF BoardView::centerOf(int index) const
{
    const QRectF board = boardRect();
    const qreal cell = qMin(board.width(), board.height()) / m_boardSize;
    const int row = index / m_boardSize;
    const int col = index % m_boardSize;
    return QPointF(board.left() + (col + 0.5) * cell,
                   board.top() + (row + 0.5) * cell);
}

/**
 * @brief 计算节点半径，随控件尺寸缩放。
 */
qreal BoardView::circleRadius() const
{
    const QRectF board = boardRect();
    return qMin(board.width(), board.height()) / m_boardSize * 0.38;
}

/**
 * @brief 查找坐标命中的节点。
 */
int BoardView::nodeAt(const QPoint &pos) const
{
    const qreal r = circleRadius();
    const QPointF p(pos);
    for (int i = 0; i < nodeCount(); ++i) {
        const QPointF d = centerOf(i) - p;
        if (d.x() * d.x() + d.y() * d.y() <= r * r)
            return i;
    }
    return -1;
}

// ---------- 绘制 ----------

/**
 * @brief 绘制整个棋盘。
 */
void BoardView::paintEvent(QPaintEvent *)
{
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, true);
    p.setRenderHint(QPainter::TextAntialiasing, true);

    const qreal radius = circleRadius();

    drawBackground(p);
    drawConnections(p, radius);
    drawBlankMarker(p, radius);
    drawTiles(p, radius);

    if (m_solved)
        drawSolvedOverlay(p);
}

/**
 * @brief 绘制棋盘背景。
 */
void BoardView::drawBackground(QPainter &p) const
{
    const QRectF board = boardRect();
    const qreal corner = board.width() * 0.032;

    p.setPen(Qt::NoPen);
    p.setBrush(kBoardColor);
    p.drawRoundedRect(QRectF(rect()).adjusted(2, 2, -2, -2), corner, corner);
}

/**
 * @brief 绘制节点之间的连线（强制显示）。
 */
void BoardView::drawConnections(QPainter &p, qreal radius) const
{
    // 连线强制显示：拓扑含对角线连接，
    // 不画出连线玩家无从判断哪些节点可以移动。
    const qreal width = qMax(1.5, radius * 0.115);

    for (int i = 0; i < m_adjacency.size(); ++i) {
        for (int j : m_adjacency.at(i)) {
            if (j <= i || j >= nodeCount())
                continue;

            const bool hot = (i == m_hover || j == m_hover);
            p.setPen(QPen(hot ? kEdgeHotColor : kEdgeColor,
                          width, Qt::SolidLine, Qt::RoundCap));
            p.drawLine(centerOf(i), centerOf(j));
        }
    }
}

/**
 * @brief 绘制空格标记。
 */
void BoardView::drawBlankMarker(QPainter &p, qreal radius) const
{
    // 空格位置由局面数据推出，View 不需要向外部询问
    int blank = -1;
    for (int i = 0; i < nodeCount(); ++i) {
        if (m_board.at(i) == 0) {
            blank = i;
            break;
        }
    }
    if (blank < 0)
        return;

    p.setPen(QPen(kBlankColor, qMax(1.5, radius * 0.10), Qt::DashLine));
    p.setBrush(Qt::NoBrush);
    p.drawEllipse(centerOf(blank), radius, radius);
}

/**
 * @brief 绘制各节点上的数字圆片。
 */
void BoardView::drawTiles(QPainter &p, qreal radius) const
{
    QFont f = font();
    f.setBold(true);
    f.setPixelSize(qMax(10, int(radius * 1.05)));
    p.setFont(f);

    for (int i = 0; i < nodeCount(); ++i) {
        const int value = m_board.at(i);
        if (value == 0)
            continue;

        const QPointF c = centerOf(i);
        const bool movable = (i < m_movable.size()) && m_movable.at(i);

        QColor fill = kTileColor;
        if (i == m_hover && movable)
            fill = kTileHoverColor;
        else if (movable)
            fill = kTileMovableColor;

        // 轻微阴影，增加立体感
        p.setPen(Qt::NoPen);
        p.setBrush(QColor(0, 0, 0, 60));
        p.drawEllipse(c + QPointF(0, radius * 0.06), radius, radius);

        p.setBrush(fill);
        p.setPen(QPen(fill.darker(150), qMax(1.0, radius * 0.09)));
        p.drawEllipse(c, radius, radius);

        // 数字：按文本实际宽度收缩，避免两位数贴边
        const QString text = QString::number(value);
        QFontMetrics fm(f);
        const qreal maxWidth = radius * 1.44;
        if (fm.horizontalAdvance(text) > maxWidth) {
            QFont small = f;
            small.setPixelSize(qMax(8, int(f.pixelSize() * maxWidth
                                           / fm.horizontalAdvance(text))));
            p.setFont(small);
        }

        p.setPen(kTileTextColor);
        p.drawText(QRectF(c.x() - radius, c.y() - radius, 2 * radius, 2 * radius),
                   Qt::AlignCenter, text);
        p.setFont(f);
    }
}

/**
 * @brief 绘制通关覆盖层。
 */
void BoardView::drawSolvedOverlay(QPainter &p) const
{
    const QRectF board = boardRect();
    const qreal corner = board.width() * 0.03;

    p.setPen(Qt::NoPen);
    p.setBrush(QColor(0, 0, 0, 130));
    p.drawRoundedRect(board, corner, corner);

    QFont f = font();
    f.setBold(true);
    f.setPixelSize(qMax(16, int(board.width() * 0.085)));
    p.setFont(f);
    p.setPen(kSolvedColor);
    p.drawText(board, Qt::AlignCenter,
               QStringLiteral("完成！\n共 %1 步").arg(m_solvedMoves));
}

// ---------- 交互：只发信号，不做判断 ----------

/**
 * @brief 鼠标按下处理，仅把命中节点以信号抛出。
 */
void BoardView::mousePressEvent(QMouseEvent *event)
{
    if (event->button() != Qt::LeftButton) {
        QWidget::mousePressEvent(event);
        return;
    }

    const int idx = nodeAt(event->pos());
    if (idx >= 0)
        emit nodeClicked(idx);
}

/**
 * @brief 鼠标移动处理，更新悬停节点与指针形状。
 */
void BoardView::mouseMoveEvent(QMouseEvent *event)
{
    const int idx = nodeAt(event->pos());
    if (idx == m_hover)
        return;

    m_hover = idx;

    // 指针形状属于纯外观，依据外部喂入的"可移动"数据即可决定，
    // 无需询问 Model。
    const bool clickable = (idx >= 0 && idx < m_movable.size()
                            && m_movable.at(idx) && !m_solved);
    setCursor(clickable ? Qt::PointingHandCursor : Qt::ArrowCursor);
    update();
}

/**
 * @brief 鼠标离开处理，清除悬停状态。
 */
void BoardView::leaveEvent(QEvent *)
{
    if (m_hover == -1)
        return;

    m_hover = -1;
    setCursor(Qt::ArrowCursor);
    update();
}

} // namespace mvc
