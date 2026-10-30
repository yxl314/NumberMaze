#-------------------------------------------------------------------------------
# 数字迷宫 (number-maze) —— Qt 5 版本
#
# 目标：在 Ubuntu 16.04（Qt 5.5.1 / g++ 5.4）上构建，
#       同时可在 Ubuntu 16.04 ~ 26.04 上运行。
#
# 与 Qt 6 版本共用同一套 Core / MVC 源码，仅构建系统与少量 API 不同。
#-------------------------------------------------------------------------------

QT += core gui widgets

TARGET   = number-maze
TEMPLATE = app

CONFIG += release
CONFIG -= app_bundle
CONFIG -= qtquickcompiler

# Qt 5.5 的 qmake 尚不支持 CONFIG += c++14，直接指定标准。
QMAKE_CXXFLAGS += -std=c++14
QMAKE_CXXFLAGS += -Wall -Wextra
QMAKE_CXXFLAGS_RELEASE += -O2

# 安装包使用相对布局，二进制本身不写死运行库路径，由启动脚本设置。
DEFINES += QT_DEPRECATED_WARNINGS

SOURCE_ROOT = $$PWD/Source/NumberNaze

INCLUDEPATH += \
    $$SOURCE_ROOT \
    $$SOURCE_ROOT/Core \
    $$SOURCE_ROOT/MVC \
    $$SOURCE_ROOT/MVC/Model \
    $$SOURCE_ROOT/MVC/View \
    $$SOURCE_ROOT/MVC/Controller \
    $$PWD/Source/singleproc/include \
    $$PWD/Source/singleproc/src \
    $$PWD/Source/stacklog/include \
    $$PWD/Source/stacklog/src

HEADERS += \
    $$SOURCE_ROOT/Core/Difficulty.h \
    $$SOURCE_ROOT/Core/Graph.h \
    $$SOURCE_ROOT/Core/GameState.h \
    $$SOURCE_ROOT/MVC/MainWindow.h \
    $$SOURCE_ROOT/MVC/Model/GameModel.h \
    $$SOURCE_ROOT/MVC/View/BoardView.h \
    $$SOURCE_ROOT/MVC/Controller/Controller.h

SOURCES += \
    $$SOURCE_ROOT/main.cpp \
    $$SOURCE_ROOT/Core/Graph.cpp \
    $$SOURCE_ROOT/Core/GameState.cpp \
    $$SOURCE_ROOT/MVC/MainWindow.cpp \
    $$SOURCE_ROOT/MVC/Model/GameModel.cpp \
    $$SOURCE_ROOT/MVC/View/BoardView.cpp \
    $$SOURCE_ROOT/MVC/Controller/Controller.cpp \
    $$PWD/Source/singleproc/src/guard.cpp \
    $$PWD/Source/singleproc/src/platform/platform_posix.cpp \
    $$PWD/Source/stacklog/src/logger.cpp \
    $$PWD/Source/stacklog/src/common/diagnose.cpp \
    $$PWD/Source/stacklog/src/common/file.cpp \
    $$PWD/Source/stacklog/src/common/time.cpp \
    $$PWD/Source/stacklog/src/core/id.cpp \
    $$PWD/Source/stacklog/src/core/record.cpp \
    $$PWD/Source/stacklog/src/span/captured_span.cpp \
    $$PWD/Source/stacklog/src/span/scoped_span.cpp \
    $$PWD/Source/stacklog/src/io/record_file.cpp \
    $$PWD/Source/stacklog/src/io/sink.cpp \
    $$PWD/Source/stacklog/src/tree/render.cpp \
    $$PWD/Source/stacklog/src/tree/tree_builder.cpp \
    $$PWD/Source/stacklog/src/detail/context.cpp \
    $$PWD/Source/stacklog/src/detail/ring.cpp \
    $$PWD/Source/stacklog/src/detail/state.cpp \
    $$PWD/Source/stacklog/src/detail/fork_guard.cpp

RESOURCES += resources/resources.qrc

# stacklog 使用 std::thread，需显式链接 pthread。
unix: LIBS += -pthread

# 构建产物集中到 build/，避免污染源码目录。
DESTDIR     = $$PWD/build
OBJECTS_DIR = $$PWD/build/obj
MOC_DIR     = $$PWD/build/moc
RCC_DIR     = $$PWD/build/rcc
