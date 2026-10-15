#!/bin/sh
# 配置并构建，供风格检查产出可用的编译数据库。
#
# 单独一条 cmake --build 不够：构建树可能被清理过，此时没有 CMakeCache.txt，
# 构建会直接失败，编译数据库也就无从更新。因此这里先配置再构建。
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
BUILD_DIR="$ROOT/build"

if [ ! -f "$BUILD_DIR/CMakeCache.txt" ]; then
    cmake -S "$ROOT" -B "$BUILD_DIR" -G Ninja -DCMAKE_BUILD_TYPE=Release
fi

exec cmake --build "$BUILD_DIR"
