#ifndef MVC_V_BOARDVIEW_H
#define MVC_V_BOARDVIEW_H

#include <QVector>
#include <QWidget>

namespace mvc {

// View 层：只负责"显示"和"发出用户操作信号"。
//
// 严格 MVC 约束（重要）：
//  - 本类不知道 Model 的存在，不持有、也不包含 Model 的头文件
//  - 所有绘制所需的数据都由外部通过槽函数喂进来（见 setBoard 等）
//  - 用户的鼠标操作只以信号形式抛出，自身不做任何业务判断
//
// 连接线与可移动高亮为强制显示：本项目拓扑含对角连接，
// 玩家无法凭位置直觉判断，这两种视觉辅助是玩法必需。
class BoardView : public QWidget
{
    Q_OBJECT

public:
    explicit BoardView(QWidget *parent = nullptr);

    QSize sizeHint() const override;
    QSize minimumSizeHint() const override;

public slots:
    // ---- 由控制器喂入的数据（View 不主动去取）----

    // 设置完整局面：board[i] 为节点 i 上的数字，0 表示空格
    void setBoard(const QVector<int> &board);

    // 设置每个节点的邻居，用于绘制连线
    void setConnections(const QVector<QVector<int>> &adjacency);

    // 设置哪些节点当前可移动（用于高亮）
    void setMovable(const QVector<bool> &movable);

    // 设置是否已通关（影响覆盖层与鼠标指针）
    void setSolved(bool solved, int moves);

signals:
    // ---- 抛给控制器的用户操作 ----
    void nodeClicked(int index);

protected:
    void paintEvent(QPaintEvent *event) override;
    void mousePressEvent(QMouseEvent *event) override;
    void mouseMoveEvent(QMouseEvent *event) override;
    void leaveEvent(QEvent *event) override;

private:
    void drawBackground(QPainter &p) const;
    void drawConnections(QPainter &p, qreal radius) const;
    void drawBlankMarker(QPainter &p, qreal radius) const;
    void drawTiles(QPainter &p, qreal radius) const;
    void drawSolvedOverlay(QPainter &p) const;

    QRectF boardRect() const;
    QPointF centerOf(int index) const;
    qreal circleRadius() const;
    int nodeAt(const QPoint &pos) const;
    int nodeCount() const { return m_board.size(); }
    int indexAt(int row, int col) const;

    // 视图自己的显示数据，全部来自外部喂入
    QVector<int> m_board;
    QVector<QVector<int>> m_adjacency;
    QVector<bool> m_movable;
    bool m_solved = false;
    int m_solvedMoves = 0;

    int m_boardSize = 4;   // 方阵边长
    int m_hover = -1;
};

} // namespace mvc

#endif // MVC_V_BOARDVIEW_H
