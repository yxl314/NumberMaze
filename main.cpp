#include <QApplication>

#include "MainWindow.h"

int main(int argc, char *argv[])
{
    QApplication app(argc, argv);

    QApplication::setApplicationName(QStringLiteral("数字迷宫"));
    QApplication::setOrganizationName(QStringLiteral("NumberMaze"));
    QApplication::setApplicationVersion(QStringLiteral("1.0"));

    mvc::MainWindow window;
    window.show();

    return app.exec();
}
