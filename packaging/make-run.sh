#!/bin/bash
#-------------------------------------------------------------------------------
# 在 Ubuntu 16.04 上构建自解压安装包。
#
# 前置条件：
#   - 已安装 qtbase5-dev（Qt 5.5.1）与 g++
#   - 已执行 qmake && make，产物位于 build/number-maze
#
# 用法：packaging/make-run.sh [版本号]
# 输出：dist/NumberMaze-<版本>-qt5-x86_64.run
#-------------------------------------------------------------------------------
set -euo pipefail

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"

VERSION="${1:-1.0.0}"
APP_BIN="$PROJECT_DIR/build/number-maze"
STAGE="$PROJECT_DIR/packaging/stage"
PAYLOAD_DIR="$STAGE/payload"
DIST="$PROJECT_DIR/dist"
OUT="$DIST/NumberMaze-$VERSION-qt5-x86_64.run"

[ -x "$APP_BIN" ] || { echo "错误: 未找到 $APP_BIN，请先构建。" >&2; exit 1; }

QT_LIBDIR="/usr/lib/x86_64-linux-gnu"
QT_PLUGINDIR="$QT_LIBDIR/qt5/plugins"

[ -d "$QT_LIBDIR" ] || { echo "错误: 找不到 Qt 运行库目录。" >&2; exit 1; }

rm -rf "$STAGE"
mkdir -p "$PAYLOAD_DIR/bin" "$PAYLOAD_DIR/lib" \
         "$PAYLOAD_DIR/plugins/platforms" "$PAYLOAD_DIR/share"

install -m 755 "$APP_BIN" "$PAYLOAD_DIR/bin/number-maze"

find_lib()
{
    local name="$1" dir
    for dir in /lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu /lib /usr/lib; do
        if [ -f "$dir/$name" ]; then
            printf '%s\n' "$dir/$name"
            return 0
        fi
    done
    return 1
}

copy_lib()
{
    local name="$1" path
    path=$(find_lib "$name") || { echo "错误: 缺少运行库 $name" >&2; exit 1; }
    cp -L "$path" "$PAYLOAD_DIR/lib/$name"
}

# Qt 本体：Widgets 依赖 Gui/Core，XcbQpa 还依赖 DBus。
for lib in \
    libQt5Core.so.5 \
    libQt5Gui.so.5 \
    libQt5Widgets.so.5 \
    libQt5DBus.so.5 \
    libQt5XcbQpa.so.5
do
    copy_lib "$lib"
done

# 新版 Ubuntu 已不再提供的运行库，必须随包携带。
for lib in \
    libicuuc.so.55 \
    libicui18n.so.55 \
    libicudata.so.55 \
    libpcre16.so.3 \
    libpcre.so.3 \
    libpng12.so.0
do
    copy_lib "$lib"
done

# xcb 扩展库。libxcb.so.1 本身留在系统侧，避免与目标机的 X11 客户端库混用；
# 这些扩展库只依赖 libxcb 的稳定 ABI，打包后可在缺少它们的精简系统上运行。
for lib in /usr/lib/x86_64-linux-gnu/libxcb-*.so.* /lib/x86_64-linux-gnu/libxcb-*.so.*; do
    [ -f "$lib" ] || continue
    base=${lib##*/}
    # 只复制 soname（如 libxcb-image.so.0），跳过 .so.0.0.0 实体文件，避免重复。
    case "$base" in
        *.so.[0-9]) ;;
        *) continue ;;
    esac
    cp -L "$lib" "$PAYLOAD_DIR/lib/$base"
done

# 平台插件是 Qt 启动必需的；其余插件不打包，PNG 支持已内置于 QtGui。
install -m 755 "$QT_PLUGINDIR/platforms/libqxcb.so" \
    "$PAYLOAD_DIR/plugins/platforms/libqxcb.so"
# 便于无显示环境做自检；普通桌面运行不会用到。
install -m 755 "$QT_PLUGINDIR/platforms/libqoffscreen.so" \
    "$PAYLOAD_DIR/plugins/platforms/libqoffscreen.so"

install -m 644 "$PROJECT_DIR/resources/number-maze.svg" \
    "$PAYLOAD_DIR/share/number-maze.svg"
install -m 644 "$PROJECT_DIR/packaging/number-maze.desktop.in" \
    "$PAYLOAD_DIR/share/number-maze.desktop.in"

# 用随包库优先的方式自检，确保没有遗漏的依赖（目标机 16.04 的系统库可用）。
MISSING=$(LD_LIBRARY_PATH="$PAYLOAD_DIR/lib" ldd "$PAYLOAD_DIR/bin/number-maze" 2>/dev/null \
    | grep "not found" || true)
if [ -n "$MISSING" ]; then
    echo "错误: 以下运行库缺失：" >&2
    echo "$MISSING" >&2
    exit 1
fi

# 检查插件依赖；Qt 库由主程序加载，但共享库仍需能找到。
MISSING_PLUGIN=$(LD_LIBRARY_PATH="$PAYLOAD_DIR/lib" ldd "$PAYLOAD_DIR/plugins/platforms/libqxcb.so" 2>/dev/null \
    | grep "not found" || true)
if [ -n "$MISSING_PLUGIN" ]; then
    echo "错误: 平台插件依赖缺失：" >&2
    echo "$MISSING_PLUGIN" >&2
    exit 1
fi

mkdir -p "$DIST"
rm -f "$OUT"
tar -C "$STAGE" -czf "$STAGE/payload.tar.gz" payload
cat "$PROJECT_DIR/packaging/installer-header.sh" "$STAGE/payload.tar.gz" > "$OUT"
chmod 755 "$OUT"

echo "已生成: $OUT"
echo "大小: $(du -h "$OUT" | cut -f1)"
echo "SHA256: $(sha256sum "$OUT" | cut -d' ' -f1)"
