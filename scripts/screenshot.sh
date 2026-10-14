#!/usr/bin/env bash
# 自动化验证：在虚拟显示中启动游戏并截取界面。
# 目的是确认渲染真的可用，而不是只依赖"编译通过"。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/build/number-maze"
OUT_DIR="${1:-$ROOT/build/screenshots}"

if [ ! -x "$BIN" ]; then
    echo "未找到可执行文件: $BIN" >&2
    echo "请先构建: cmake -S . -B build && cmake --build build" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"
echo "截图输出目录: $OUT_DIR"

# 程序与 import 必须在同一个 X 显示里，否则 import 连到别的
# DISPLAY，抓不到窗口。内层脚本用 heredoc 传入，避免引号嵌套。
xvfb-run -a --server-args="-screen 0 1024x900x24" bash -s <<INNER
set -e
export QT_QPA_PLATFORM=xcb
export BIN="$BIN"
export OUT_DIR="$OUT_DIR"

"\$BIN" &
APP_PID=\$!
sleep 4

if ! kill -0 \$APP_PID 2>/dev/null; then
    echo "程序启动后立即退出" >&2
    exit 1
fi
echo "程序已启动 (PID \$APP_PID), DISPLAY=\$DISPLAY"

shoot() {
    if import -window root "\$OUT_DIR/\$1.png" 2>/dev/null; then
        echo "  已保存 \$1.png"
    else
        echo "  截图失败: \$1"
    fi
}

sleep 1; shoot "01-初始界面"
sleep 2; shoot "02-运行中"
sleep 2; shoot "03-运行中"

kill \$APP_PID 2>/dev/null || true
wait \$APP_PID 2>/dev/null || true
INNER

echo "完成。产物："
ls -la "$OUT_DIR"/*.png 2>/dev/null || echo "  (无截图)"
