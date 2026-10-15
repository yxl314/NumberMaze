/**
 * @file BoardView.h
 * @brief 棋盘视图：绘制与用户交互信号的发出。
 *
 * 本文件属于 MVC 的 V 层。
 */

#ifndef MVC_V_BOARDVIEW_H
#define MVC_V_BOARDVIEW_H

#include <QVector>
#include <QWidget>

namespace mvc {

/**
 * @brief 棋盘视图，负责显示与上报用户操作。
 *
 * @par 职责
 * - 依据外部喂入的数据绘制棋盘、连线、数字与状态覆盖层；
 * - 把用户的鼠标操作以信号形式抛出；
 * - 依据外部喂入的"可移动"数据决定鼠标指针形状（纯外观）。
 *
 * @par 严格 MVC 约束
 * - 本类不知道模型的存在，既不持有其指针，也不包含其头文件；
 * - 所有绘制所需的数据都由外部通过槽函数喂入（见 setBoard 等），
 *   本类不主动向任何对象索取数据；
 * - 用户的鼠标操作只以信号形式抛出，自身不做任何业务判断。
 *
 * @par 关于连线与高亮
 * 二者为强制显示，不提供关闭选项。拓扑含对角线连接，无法凭位置直觉
 * 判断可移动性，这两种视觉辅助属于玩法必需的信息。
 */
class BoardView : public QWidget
{
    Q_OBJECT

public:
    /**
     * @brief 构造视图。
     *
     * @param parent Qt 父窗口。
     */
    explicit BoardView(QWidget *parent = nullptr);

    /**
     * @brief 返回推荐尺寸。
     * @return 推荐大小。
     */
    QSize sizeHint() const override;

    /**
     * @brief 返回最小尺寸。
     * @return 最小可接受大小。
     */
    QSize minimumSizeHint() const override;

public slots:
    // ---- 由控制器喂入的数据；视图不主动去取 ----

    /**
     * @brief 设置完整局面。
     *
     * @param board 各节点上的数字，0 表示空格。
     *
     * @note 方阵边长由数组长度推算，视图因此无需依赖 Core 的常量。
     */
    void setBoard(const QVector<int> &board);

    /**
     * @brief 设置棋盘连接关系，用于绘制连线。
     *
     * @param adjacency 外层索引为节点，内层为其邻居列表。
     *
     * @note 该数据在整个游戏期间不变，通常只需设置一次。
     */
    void setConnections(const QVector<QVector<int>> &adjacency);

    /**
     * @brief 设置各节点是否可移动，用于高亮。
     *
     * @param movable 与节点一一对应的布尔数组。
     */
    void setMovable(const QVector<bool> &movable);

    /**
     * @brief 设置通关状态。
     *
     * @param solved 是否已通关，决定是否绘制覆盖层。
     * @param moves 通关所用步数，显示在覆盖层上。
     */
    void setSolved(bool solved, int moves);

signals:
    // ---- 抛给控制器的用户操作 ----

    /**
     * @brief 用户点击了某个节点。
     *
     * 是否应当移动、能否移动，均由控制器转交模型判定，
     * 视图不参与判断。
     *
     * @param index 被点击节点的索引。
     */
    void nodeClicked(int index);

protected:
    /**
     * @brief 绘制事件。
     * @param event 绘制事件对象。
     */
    void paintEvent(QPaintEvent *event) override;

    /**
     * @brief 鼠标按下事件，转换为 nodeClicked 信号。
     * @param event 鼠标事件对象。
     */
    void mousePressEvent(QMouseEvent *event) override;

    /**
     * @brief 鼠标移动事件，用于更新悬停高亮与指针形状。
     * @param event 鼠标事件对象。
     */
    void mouseMoveEvent(QMouseEvent *event) override;

    /**
     * @brief 鼠标离开控件事件，清除悬停状态。
     * @param event 事件对象。
     */
    void leaveEvent(QEvent *event) override;

private:
    /**
     * @brief 绘制棋盘背景。
     * @param p 画笔。
     */
    void drawBackground(QPainter &p) const;

    /**
     * @brief 绘制节点之间的连线。
     * @param p 画笔。
     * @param radius 节点半径，用于推算线宽。
     */
    void drawConnections(QPainter &p, qreal radius) const;

    /**
     * @brief 绘制空格标记（虚线圆圈）。
     * @param p 画笔。
     * @param radius 节点半径。
     */
    void drawBlankMarker(QPainter &p, qreal radius) const;

    /**
     * @brief 绘制各节点上的数字圆片。
     * @param p 画笔。
     * @param radius 节点半径。
     */
    void drawTiles(QPainter &p, qreal radius) const;

    /**
     * @brief 绘制通关覆盖层。
     * @param p 画笔。
     */
    void drawSolvedOverlay(QPainter &p) const;

    /**
     * @brief 计算棋盘绘制区域。
     * @return 以正方形居中、四周留白的矩形。
     */
    QRectF boardRect() const;

    /**
     * @brief 计算节点中心的屏幕坐标。
     * @param index 节点索引。
     * @return 节点中心坐标。
     */
    QPointF centerOf(int index) const;

    /**
     * @brief 计算节点半径。
     *
     * 半径随控件尺寸缩放，使窗口缩放后布局保持协调。
     *
     * @return 半径（像素）。
     */
    qreal circleRadius() const;

    /**
     * @brief 查找坐标落在哪个节点上。
     *
     * @param pos 控件坐标。
     * @return 命中的节点索引；未命中任何节点时返回 -1。
     */
    int nodeAt(const QPoint &pos) const;

    /**
     * @brief 取得当前节点总数。
     * @return 节点数量，由已喂入的局面数据决定。
     */
    int nodeCount() const { return m_board.size(); }

    /**
     * @brief 由行列换算节点索引。
     *
     * @param row 行号，从 0 开始。
     * @param col 列号，从 0 开始。
     * @return 节点索引。
     */
    int indexAt(int row, int col) const;

    /// 当前局面，由 setBoard 喂入。
    QVector<int> m_board;

    /// 连接关系，由 setConnections 喂入。
    QVector<QVector<int>> m_adjacency;

    /// 各节点是否可移动，由 setMovable 喂入。
    QVector<bool> m_movable;

    /// 是否已通关。
    bool m_solved = false;

    /// 通关所用步数。
    int m_solvedMoves = 0;

    /// 方阵边长，由局面数据长度推算。
    int m_boardSize = 4;

    /// 当前鼠标悬停的节点索引；无悬停时为 -1。
    int m_hover = -1;
};

} // namespace mvc

#endif // MVC_V_BOARDVIEW_H
