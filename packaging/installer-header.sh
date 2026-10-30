#!/bin/sh
#-------------------------------------------------------------------------------
# 数字迷宫 (Number Maze) 1.0.0 —— 自解压安装包
#
# 用法：
#   ./NumberMaze-1.0.0-qt5-x86_64.run                 安装到 ~/.local
#   ./NumberMaze-1.0.0-qt5-x86_64.run --prefix DIR    安装到指定前缀
#   ./NumberMaze-1.0.0-qt5-x86_64.run --uninstall     卸载
#   ./NumberMaze-1.0.0-qt5-x86_64.run --help          显示帮助
#
# 安装包自带 Qt 5.5 运行库与 xcb 平台插件，可在 Ubuntu 16.04 ~ 26.04
# （以及同代 glibc 的发行版）上运行，不要求目标机安装 Qt。
#-------------------------------------------------------------------------------
set -u

APP_NAME="number-maze"
APP_TITLE="数字迷宫"
APP_VERSION="1.0.0"
PREFIX="${PREFIX:-$HOME/.local}"
ACTION="install"

usage()
{
    cat <<'USAGE_EOF'
用法: NumberMaze-1.0.0-qt5-x86_64.run [选项]

选项:
  -p, --prefix DIR   安装到指定前缀（默认 ~/.local）
      --uninstall    从安装前缀卸载
  -h, --help         显示本帮助
  -v, --version      显示版本

安装后的启动方式:
  ~/.local/bin/number-maze
  或在应用菜单中搜索“数字迷宫”
USAGE_EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        -p|--prefix)
            [ $# -ge 2 ] || { echo "错误: $1 需要一个目录参数" >&2; exit 1; }
            PREFIX="$2"
            shift 2
            ;;
        --uninstall)
            ACTION="uninstall"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -v|--version)
            echo "$APP_TITLE $APP_VERSION"
            exit 0
            ;;
        *)
            echo "错误: 未知参数 $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

# 本包只包含 x86_64 二进制。
if [ "$(uname -m)" != "x86_64" ]; then
    echo "错误: 本安装包仅支持 x86_64，当前架构为 $(uname -m)。" >&2
    exit 1
fi

# 安装前缀统一为绝对路径，避免相对路径在启动脚本里失效。
case "$PREFIX" in
    /*) ;;
    *) PREFIX="$(pwd)/$PREFIX" ;;
esac
APPDIR="$PREFIX/lib/$APP_NAME"
DESKTOP_FILE="$PREFIX/share/applications/$APP_NAME.desktop"
ICON_FILE="$PREFIX/share/icons/hicolor/scalable/apps/$APP_NAME.svg"

uninstall_app()
{
    rm -f "$PREFIX/bin/$APP_NAME"
    rm -rf "$APPDIR"
    rm -f "$DESKTOP_FILE" "$ICON_FILE"
    echo "已卸载 $APP_TITLE（前缀: $PREFIX）"
}

if [ "$ACTION" = "uninstall" ]; then
    uninstall_app
    exit 0
fi

# ---- 找到嵌入的归档并解压到临时目录 -----------------------------------------
PAYLOAD_LINE=$(awk '/^__PAYLOAD_BELOW__$/ { print NR + 1; exit 0 }' "$0")
if [ -z "${PAYLOAD_LINE:-}" ]; then
    echo "错误: 安装包已损坏，找不到归档标记。" >&2
    exit 1
fi

TMPDIR_BASE="${TMPDIR:-/tmp}"
TMP_DIR=$(mktemp -d "$TMPDIR_BASE/number-maze.XXXXXX") || {
    echo "错误: 无法创建临时目录。" >&2
    exit 1
}
# shellcheck disable=SC2064
trap "rm -rf '$TMP_DIR'" EXIT HUP INT TERM

if ! tail -n +"$PAYLOAD_LINE" "$0" | tar -xzf - -C "$TMP_DIR"; then
    echo "错误: 解压安装包失败。" >&2
    exit 1
fi

if [ ! -f "$TMP_DIR/payload/bin/$APP_NAME" ]; then
    echo "错误: 安装包内容不完整。" >&2
    exit 1
fi

# ---- 安装文件 ---------------------------------------------------------------
mkdir -p "$PREFIX/bin" "$APPDIR" "$PREFIX/share/applications" \
         "$PREFIX/share/icons/hicolor/scalable/apps"
rm -rf "$APPDIR"
mkdir -p "$APPDIR"
cp -a "$TMP_DIR/payload/." "$APPDIR/"

# 启动器：设置随包运行库与 Qt 插件路径，然后启动真正的二进制。
cat > "$PREFIX/bin/$APP_NAME" <<'LAUNCHER_EOF'
#!/bin/sh
# 数字迷宫启动器：使用随包携带的 Qt 5 运行库。
SELF="$0"
case "$SELF" in
    */*) ;;
    *) SELF=$(command -v "$SELF" 2>/dev/null || printf '%s' "$0") ;;
esac
BIN_DIR=$(CDPATH= cd -- "$(dirname -- "$SELF")" && pwd)
APPDIR=$(CDPATH= cd -- "$BIN_DIR/../lib/number-maze" 2>/dev/null && pwd)
if [ -z "${APPDIR:-}" ] || [ ! -x "$APPDIR/bin/number-maze" ]; then
    echo "错误: 找不到数字迷宫程序文件，请重新安装。" >&2
    exit 1
fi
LD_LIBRARY_PATH="$APPDIR/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# 只使用随包插件，避免误加载目标机上版本不匹配的 Qt 插件。
QT_PLUGIN_PATH="$APPDIR/plugins"
QT_QPA_PLATFORM_PLUGIN_PATH="$APPDIR/plugins/platforms"
export LD_LIBRARY_PATH QT_PLUGIN_PATH QT_QPA_PLATFORM_PLUGIN_PATH
# 本包只带 xcb 插件。桌面会话未显式指定平台时用 xcb：
# Wayland 桌面上会通过 XWayland 运行，无需目标机安装 Qt Wayland 插件。
if [ -z "${QT_QPA_PLATFORM:-}" ]; then
    QT_QPA_PLATFORM=xcb
    export QT_QPA_PLATFORM
fi
exec "$APPDIR/bin/number-maze" "$@"
LAUNCHER_EOF
chmod 755 "$PREFIX/bin/$APP_NAME"

cp -f "$APPDIR/share/number-maze.svg" "$ICON_FILE"
sed -e "s|@EXEC@|$PREFIX/bin/$APP_NAME|g" \
    -e "s|@ICON@|$ICON_FILE|g" \
    "$APPDIR/share/number-maze.desktop.in" > "$DESKTOP_FILE"
chmod 644 "$DESKTOP_FILE"

# 更新桌面数据库（没有该命令的环境直接跳过）。
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$PREFIX/share/applications" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "$APP_TITLE $APP_VERSION 已安装到: $PREFIX"
echo "启动命令: $PREFIX/bin/$APP_NAME"
echo "卸载命令: $0 --uninstall"

exit 0
__PAYLOAD_BELOW__
