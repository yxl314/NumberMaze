#ifndef PUZZLEWIDGET_H
#define PUZZLEWIDGET_H

#include <QWidget>
#include <QPointF>
#include <QHash>

#include "graphmodel.h"

// 游戏画布：负责绘制图结构、节点数字，并处理鼠标交互。
//
// 由于本项目的连接拓扑不是规则网格（含对角线），绘制时会明确画出
// 节点之间的连线，帮助玩家判断哪些节点可以移动。
class PuzzleWidget : public QWidget
{
    Q_OBJECT

public:
    explicit PuzzleWidget(QWidget *parent = nullptr);

    GraphModel &model() { return m_model; }
    const GraphModel &model() const { return m_model; }

    void newGame(Difficulty level);
    void restart();                 // 回到本轮初始局面
    bool undo();                    // 悔棋

    void setShowConnections(bool on);
    bool showConnections() const { return m_showConnections; }

    void setShowHints(bool on);     // 是否高亮"当前可移动"的节点
    bool showHints() const { return m_showHints; }

    QSize sizeHint() const override { return QSize(560, 560); }

signals:
    void moveMade();
    void solved(int moves);
    void stateChanged();

protected:
    void paintEvent(QPaintEvent *event) override;
    void mousePressEvent(QMouseEvent *event) override;
    void mouseMoveEvent(QMouseEvent *event) override;
    void leaveEvent(QEvent *event) override;

private:
    // 棋盘几何：以正方形居中绘制，四周留白
    QRectF boardRect() const;
    QPointF centerOf(int index) const;
    qreal circleRadius() const;
    int nodeAt(const QPoint &pos) const;

    void drawConnections(QPainter &p, const QRectF &board, qreal radius);
    void drawBlankMarker(QPainter &p, int blank, qreal radius);
    void drawNodes(QPainter &p, qreal radius);
    void drawSolvedOverlay(QPainter &p, const QRectF &board);

    GraphModel m_model;
    QVector<int> m_initialState;    // 本轮初始局面，用于"重新开始"

    int m_hover = -1;
    bool m_showConnections = true;
    bool m_showHints = true;
    int m_solvedMoves = -1;
};

#endif // PUZZLEWIDGET_H
