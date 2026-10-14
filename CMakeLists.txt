cmake_minimum_required(VERSION 3.16)

project(number-maze
    VERSION 1.0.0
    DESCRIPTION "数字迷宫 - 基于特殊图连接拓扑的数字滑块游戏"
    LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# 让 CMake 自动处理 Qt 的 moc/uic/rcc
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_AUTOUIC ON)

find_package(Qt6 REQUIRED COMPONENTS Widgets)

add_executable(number-maze
    src/main.cpp
    src/graphmodel.h
    src/graphmodel.cpp
    src/puzzlewidget.h
    src/puzzlewidget.cpp
    src/gamewindow.h
    src/gamewindow.cpp
)

target_link_libraries(number-maze PRIVATE Qt6::Widgets)

# 开启常用告警，便于及早发现问题
target_compile_options(number-maze PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra>
)

install(TARGETS number-maze RUNTIME DESTINATION bin)
