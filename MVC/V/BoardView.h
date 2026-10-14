#ifndef MVC_V_BOARDVIEW_H
#define MVC_V_BOARDVIEW_H

#include <QWidget>

#include "GameModel.h"

namespace mvc {

// View 层：只负责绘制棋盘与上报鼠标交互，不持有游戏规则。
//
// 连接线与"可移动"高亮为强制显示：
// 本项目拓扑含对角连接，玩家无法凭位置直觉判断，
// 因此这两种视觉辅助是玩法必需，不提供关闭选项。
class BoardView : public QWidget
{
    Q_OBJECT

public:
    explicit BoardView(GameModel *model, QWidget *parent = nullptr);

    QSize sizeHint() const override;
    QSize minimumSizeHint() const override;

signals:
    // 用户点击了某个节点，交由控制器决定如何响应
    void nodeClicked(int index);

protected:
    void paintEvent(QPaintEvent *event) override;
    void mousePressEvent(QMouseEvent *event) override;
    void mouseMoveEvent(QMouseEvent *event) override;
    void leaveEvent(QEvent *event) override;

private:
    void drawBackground(QPainter &p) const;
    void drawConnections(QPainter &p, qreal radius) const;
    void drawBlankMarker(QPainter &p, int blank, qreal radius) const;
    void drawTiles(QPainter &p, qreal radius) const;
    void drawSolvedOverlay(QPainter &p) const;

    QRectF boardRect() const;
    QPointF centerOf(int index) const;
    qreal circleRadius() const;
    int nodeAt(const QPoint &pos) const;

    GameModel *m_model = nullptr;
    int m_hover = -1;
};

} // namespace mvc

#endif // MVC_V_BOARDVIEW_H
