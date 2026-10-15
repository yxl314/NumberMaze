cmake_minimum_required(VERSION 3.16)

project(number-maze
    VERSION 1.0.0
    DESCRIPTION "数字迷宫 - 基于特殊图连接拓扑的数字滑块游戏"
    LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)

# 源码根目录。集中定义一次，避免各处的路径拼接散落不一致。
set(SOURCE_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/source/Number-naze)

# ---------------------------------------------------------------------------
# Core：游戏核心，纯 C++
#
# 刻意做成独立静态库且不链接任何 Qt 目标，以此保证分层边界：
# 一旦有人在 Core 里写了 Qt 代码，编译就会失败。
# ---------------------------------------------------------------------------
add_library(number-maze-core STATIC
    ${SOURCE_ROOT}/Core/Graph.h
    ${SOURCE_ROOT}/Core/Graph.cpp
    ${SOURCE_ROOT}/Core/GameState.h
    ${SOURCE_ROOT}/Core/GameState.cpp
    ${SOURCE_ROOT}/Core/Difficulty.h
)
target_include_directories(number-maze-core PUBLIC ${SOURCE_ROOT}/Core)
target_compile_options(number-maze-core PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra>
)

# ---------------------------------------------------------------------------
# 应用本体：MVC 三层 + 入口
# ---------------------------------------------------------------------------
find_package(Qt6 REQUIRED COMPONENTS Widgets)

add_executable(number-maze
    ${SOURCE_ROOT}/main.cpp
    ${SOURCE_ROOT}/MVC/MainWindow.h
    ${SOURCE_ROOT}/MVC/MainWindow.cpp
    ${SOURCE_ROOT}/MVC/Model/GameModel.h
    ${SOURCE_ROOT}/MVC/Model/GameModel.cpp
    ${SOURCE_ROOT}/MVC/View/BoardView.h
    ${SOURCE_ROOT}/MVC/View/BoardView.cpp
    ${SOURCE_ROOT}/MVC/Controller/Controller.h
    ${SOURCE_ROOT}/MVC/Controller/Controller.cpp
)

target_link_libraries(number-maze PRIVATE
    number-maze-core
    Qt6::Widgets
)

# MVC 各层按目录组织，需要把各层的头文件目录都加入搜索路径，
# 这样层与层之间直接用文件名互相包含，避免写长长的相对路径。
target_include_directories(number-maze PRIVATE
    ${SOURCE_ROOT}
    ${SOURCE_ROOT}/MVC
    ${SOURCE_ROOT}/MVC/Model
    ${SOURCE_ROOT}/MVC/View
    ${SOURCE_ROOT}/MVC/Controller
)

target_compile_options(number-maze PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra>
)

# ---------------------------------------------------------------------------
# Core 自测（不依赖 Qt，可独立运行）
# ---------------------------------------------------------------------------
add_executable(core-test ${SOURCE_ROOT}/tests/core_test.cpp)
target_link_libraries(core-test PRIVATE number-maze-core)
target_compile_options(core-test PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra>
)

add_executable(restart-test ${SOURCE_ROOT}/tests/restart_test.cpp)
target_link_libraries(restart-test PRIVATE number-maze-core)
target_compile_options(restart-test PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra>
)

enable_testing()
add_test(NAME core-test COMMAND core-test)
add_test(NAME restart-test COMMAND restart-test)

install(TARGETS number-maze RUNTIME DESTINATION bin)
