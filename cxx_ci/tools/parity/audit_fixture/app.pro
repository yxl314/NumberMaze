# qmake 工程文件。本对照统一走 cmake：两侧配置的 build_file 都指向本目录的
# CMakeLists.txt，ci 侧据此判定非 qmake。
#
# 这里刻意写一个 CMakeLists 里**完全不出现**的编译标准记号：两侧审计若在
# cmake 模式下误读了 .pro，expected.json 的 std_token 集合就会多出一条，
# 对照立刻失败（用和 CMakeLists 注释里相同的值就区分不出来了）
QMAKE_CXXFLAGS += -std=gnu++98

TEMPLATE = app
CONFIG += console
TARGET = parity_fixture
