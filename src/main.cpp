#include <QApplication>

#include "gamewindow.h"

int main(int argc, char *argv[])
{
    QApplication app(argc, argv);

    QApplication::setApplicationName(QStringLiteral("数字迷宫"));
    QApplication::setOrganizationName(QStringLiteral("NumberMaze"));
    QApplication::setApplicationVersion(QStringLiteral("1.0"));

    GameWindow window;
    window.show();

    return app.exec();
}
