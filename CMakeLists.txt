cmake_minimum_required(VERSION 3.16)

project(number-maze
    VERSION 1.0.0
    DESCRIPTION "数字迷宫 - 基于特殊图连接拓扑的数字滑块游戏"
    LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)

# ---------------------------------------------------------------------------
# Core：游戏核心，纯 C++
#
# 刻意做成独立静态库且不链接任何 Qt 目标，以此保证分层边界：
# 一旦有人在 Core 里写了 Qt 代码，编译就会失败。
# ---------------------------------------------------------------------------
add_library(number-maze-core STATIC
    Core/Graph.h
    Core/Graph.cpp
    Core/GameState.h
    Core/GameState.cpp
    Core/Difficulty.h
)
target_include_directories(number-maze-core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/Core)
target_compile_options(number-maze-core PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra>
)

# ---------------------------------------------------------------------------
# 应用本体：MVC 三层 + 入口
# ---------------------------------------------------------------------------
find_package(Qt6 REQUIRED COMPONENTS Widgets)

add_executable(number-maze
    main.cpp
    MainWindow.h
    MainWindow.cpp
    MVC/M/GameModel.h
    MVC/M/GameModel.cpp
    MVC/V/BoardView.h
    MVC/V/BoardView.cpp
    MVC/C/Controller.h
    MVC/C/Controller.cpp
)

target_link_libraries(number-maze PRIVATE
    number-maze-core
    Qt6::Widgets
)

# MVC 各层按目录组织，需要把各层的头文件目录都加入搜索路径，
# 这样层与层之间直接用文件名互相包含，避免写长长的相对路径。
target_include_directories(number-maze PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/MVC/M
    ${CMAKE_CURRENT_SOURCE_DIR}/MVC/V
    ${CMAKE_CURRENT_SOURCE_DIR}/MVC/C
)

target_compile_options(number-maze PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra>
)

# ---------------------------------------------------------------------------
# Core 自测（不依赖 Qt，可独立运行）
# ---------------------------------------------------------------------------
add_executable(core-test tests/core_test.cpp)
target_link_libraries(core-test PRIVATE number-maze-core)
target_compile_options(core-test PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra>
)

add_executable(restart-test tests/restart_test.cpp)
target_link_libraries(restart-test PRIVATE number-maze-core)
target_compile_options(restart-test PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra>
)

enable_testing()
add_test(NAME core-test COMMAND core-test)
add_test(NAME restart-test COMMAND restart-test)

install(TARGETS number-maze RUNTIME DESTINATION bin)
