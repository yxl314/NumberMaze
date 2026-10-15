#!/bin/sh
# 两个项目各有独立的测试与运行环境，因此分两次跑：
#   ci 的测试要在 Python 3.4 上也能过，standardize 的测试需要 3.11 与 clang 17。
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)

echo "== ci（Python >= 3.4） =="
python3 "$HERE/ci/tests/run.py"

echo "== standardize（Python >= 3.11） =="
if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; then
    echo "Python 低于 3.11，跳过 standardize 的测试" >&2
    exit 0
fi
cd "$HERE/standardize" && python3 -m pytest tests -q

# 解释器矩阵：standardize 声明支持的每一档都跑一遍。
# 只跑当前解释器时，"另一档上挂了"要等下一个使用者才暴露。
echo "== standardize 解释器矩阵 =="
for name in python3.11 python3.12 python3.13 python3.14; do
    if ! command -v "$name" >/dev/null 2>&1; then
        echo "  $name 未安装，跳过"
        continue
    fi
    echo "  -- $name --"
    (cd "$HERE/standardize" && "$name" -m pytest tests -q) || exit 1
done
