#!/bin/sh
# 现代机器上的"一把梭"：顺序调用两个入口。
#
# 只依赖 CONTRACT.md 的 CLI 契约，不 import 任何一侧的内部模块——一旦它 import
# 了，就构成新的耦合，"改一侧不会碰另一侧"随之失效。
#
# 用法: ./run_all.sh [ci.py 的参数...]
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)

# 项目配置放在工具目录之外：工具目录常常是个子模块，配置写进去就提交不进
# 项目自己的版本库，重新拉一次工具还会把它覆盖掉。约定放 <项目>/config/ 下，
# 没有就用工具自带的缺省（CI 系统分开签出工具与项目时用环境变量指定）
ci_config="${CXX_CI_CONFIG:-$HERE/../config/ci.py}"
# 末尾不清理：风格与排版要读构建树里的编译数据库。cmake 会把数据库导出到
# 构建目录（清掉构建树也还在），qmake 没有这个能力，只能由使用方从 Makefile
# 现推一份——构建树没了就推不出来。清理留给使用者自己做
if [ -f "$ci_config" ]; then
    echo "== 基础流程（ci） =="
    python3 "$HERE/ci/ci.py" --config "$ci_config" --no-clean-after "$@"
else
    echo "== 基础流程（ci） =="
    python3 "$HERE/ci/ci.py" --no-clean-after "$@"
fi

echo "== 风格与排版（standardize） =="
# 只调命令行。工具没装就直说该装什么：另备一条"从源码直跑"的路，等于两个项目
# 的边界可以随时绕过，CLI 契约也就不再是唯一接口
if ! command -v cxx-standardize >/dev/null 2>&1; then
    echo "找不到 cxx-standardize，请先安装：" >&2
    echo "  pipx install \"$HERE/standardize\"    # 或 pip install \"$HERE/standardize\"" >&2
    echo "本脚本只走命令行：从源码直跑等于给两个项目之间开第二条通路，" >&2
    echo "CLI 契约就不再是唯一接口，因此这里不做源码回退。" >&2
    exit 2
fi
# 装成包之后工具目录里没有 config/，因此由这里把项目配置的位置告诉它
standardize_config="${CXX_STANDARDIZE_CONFIG:-$HERE/../config/standardize.py}"
if [ ! -f "$standardize_config" ]; then
    standardize_config="$HERE/standardize/config/config.py"
fi

# 契约版本校验已下移到各侧入口：ci.py 与 cxx-standardize 各自在加载配置时拿
# 配置声明的 CONTRACT_VERSION 与本工具实现比对，不一致就直接退 2。编排脚本因此
# 退化为纯编排，不再读任一侧的内部模块。
cxx-standardize --config "$standardize_config" check
cxx-standardize --config "$standardize_config" guard

echo "全部通过"
