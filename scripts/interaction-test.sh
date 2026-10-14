#!/usr/bin/env bash
# 交互验证：在虚拟显示中启动游戏，模拟鼠标点击可移动的节点，
# 并通过截图像素对比确认界面确实发生了变化。
#
# 这一步的意义：证明"点击 -> 数字移动 -> 重绘"整条链路可用，
# 而不是只证明程序能启动。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/build/number-maze"
OUT_DIR="${1:-$ROOT/build/interaction}"

if [ ! -x "$BIN" ]; then
    echo "未找到可执行文件: $BIN" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"
echo "交互测试输出: $OUT_DIR"

xvfb-run -a --server-args="-screen 0 1024x900x24" bash -s <<INNER
set -e
export QT_QPA_PLATFORM=xcb
export BIN="$BIN"
export OUT_DIR="$OUT_DIR"

"\$BIN" &
APP_PID=\$!
sleep 4

if ! kill -0 \$APP_PID 2>/dev/null; then
    echo "程序启动失败" >&2
    exit 1
fi
echo "程序已启动 (PID \$APP_PID)"

shoot() {
    import -window root "\$OUT_DIR/\$1.png" 2>/dev/null && echo "  截图: \$1"
}

# 找到游戏窗口并激活
WID=\$(xdotool search --name "数字迷宫" | head -1)
if [ -z "\$WID" ]; then
    echo "未找到游戏窗口" >&2
    exit 1
fi
echo "窗口 ID: \$WID"
xdotool windowactivate --sync \$WID 2>/dev/null || true
sleep 1

shoot "01-点击前"

# 在棋盘中心区域做若干次点击。可移动的节点在空格周围，
# 这里用网格法遍历各节点中心位置，点击后有变化的即为成功。
# 棋盘几何：4x4，窗口居中，四周约 4.5% 留白。
eval \$(xdotool getwindowgeometry --shell \$WID)
echo "窗口尺寸: \${WIDTH}x\${HEIGHT}"

# 计算第 (row,col) 个节点中心，row/col 从 0 开始
node_xy() {
    python3 - "\$WIDTH" "\$HEIGHT" "\$1" "\$2" <<'PY'
import sys
w, h, row, col = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
side = min(w, h)
margin = side * 0.045
board = side - 2 * margin
cell = board / 4
x = (w - side) / 2 + margin + (col + 0.5) * cell
y = (h - side) / 2 + margin + (row + 0.5) * cell
print(int(x), int(y))
PY
}

CLICKS=0
for row in 0 1 2 3; do
    for col in 0 1 2 3; do
        read -r X Y <<< "\$(node_xy \$row \$col)"
        xdotool mousemove --sync \$X \$Y
        sleep 0.2
        xdotool click 1
        sleep 0.4
        CLICKS=\$((CLICKS+1))
    done
done
echo "已发送 \$CLICKS 次点击"
sleep 1

shoot "02-点击后"

# 另存一张窗口本身的截图（只含游戏窗口）
import -window \$WID "\$OUT_DIR/03-窗口.png" 2>/dev/null && echo "  截图: 03-窗口"

kill \$APP_PID 2>/dev/null || true
wait \$APP_PID 2>/dev/null || true
INNER

echo "完成，产物："
ls -la "$OUT_DIR"/*.png 2>/dev/null | awk '{print "  " $5, $9}'

# 像素级对比：确认界面确实变了
if [ -f "$OUT_DIR/01-点击前.png" ] && [ -f "$OUT_DIR/02-点击后.png" ]; then
    if cmp -s "$OUT_DIR/01-点击前.png" "$OUT_DIR/02-点击后.png"; then
        echo "结论: 界面未变化 —— 交互可能失效"
        exit 2
    else
        echo "结论: 界面已变化 —— 点击交互生效"
    fi
fi
