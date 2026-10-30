QT += core
QT -= gui

TARGET   = model-test
TEMPLATE = app
CONFIG  += console c++14
CONFIG  -= app_bundle

QMAKE_CXXFLAGS += -std=c++14 -Wall -Wextra
DESTDIR     = $$PWD/build-model
OBJECTS_DIR = $$PWD/build-model/obj
MOC_DIR     = $$PWD/build-model/moc

INCLUDEPATH += $$PWD/../Core $$PWD/../MVC/Model

HEADERS += $$PWD/../MVC/Model/GameModel.h
SOURCES += \
    $$PWD/model_test.cpp \
    $$PWD/../MVC/Model/GameModel.cpp \
    $$PWD/../Core/GameState.cpp \
    $$PWD/../Core/Graph.cpp
