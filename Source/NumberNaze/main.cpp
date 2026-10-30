/**
 * @file main.cpp
 * @brief 程序入口：装配日志、单实例守卫与主窗口。
 */

#include <QApplication>
#include <QFont>
#include <QFontDatabase>
#include <QIcon>
#include <QMessageBox>
#include <QString>
#include <QStringList>

#include <singleproc/singleproc.hpp>
#include <stacklog/stacklog.hpp>

#include "MainWindow.h"

namespace
{

/// 单实例互斥与寻址所用的实例名。
constexpr const char* g_instance_name    = "number-maze";

/// 发现已有实例时发给它的请求内容。库不解释该内容，格式由本程序约定。
constexpr const char* g_raise_request    = "raise";

/// 应答内容：已有实例已把自己带到前台。
constexpr const char* g_raise_accepted   = "ok";

/// 日志落盘文件名所用的应用名。
constexpr const char* g_application_name = "number-maze";

/**
 * @brief 加载随程序内置的中文字体，避免目标系统缺少 CJK 字体时出现方框。
 *
 * @note 字体是 Noto Sans CJK SC 的子集，只包含本程序界面用到的字符，
 *       因此体积很小。加载失败不致命，退回系统默认字体。
 */
void                  setupUiFont()
{
    const int _font_id = QFontDatabase::addApplicationFont(QStringLiteral(":/fonts/NotoSansCJKsc-Regular.otf"));
    if (_font_id < 0)
    {
        return;
    }

    const QStringList families = QFontDatabase::applicationFontFamilies(_font_id);
    if (families.isEmpty())
    {
        return;
    }

    QFont f = QApplication::font();
    f.setFamily(families.at(0));
    QApplication::setFont(f);
}

/**
 * @brief 把日志配置为写入默认路径。
 *
 * @return 日志文件路径；无法落盘时返回空串。
 *
 * @note 无法落盘不是致命错误：后续记录会退回默认 sink，
 *       程序仍可正常运行，因此这里只返回结果而不中止。
 */
QString               setupLogging()
{
    const std::string path = stacklog::default_log_path(g_application_name);
    if (!stacklog::log_to_file(path.c_str()))
    {
        return QString();
    }

    return QString::fromStdString(path);
}

/**
 * @brief 处理来自新实例的请求。
 *
 * @param v_request 新实例发来的请求内容。
 * @return 回给新实例的应答内容。
 *
 * @note 运行在既有实例中。本程序只支持把窗口带到前台这一种请求，
 *       无法识别的请求一律回绝，不假装成功。
 */
std::string onRequest(const std::string& v_request)
{
    if (v_request != g_raise_request)
    {
        return "unknown";
    }

    for (QWidget* widget : QApplication::topLevelWidgets())
    {
        if (!widget->isWindow())
        {
            continue;
        }

        widget->show();
        widget->raise();
        widget->activateWindow();
        return g_raise_accepted;
    }

    return "no-window";
}

} // namespace

/**
 * @brief 程序入口。
 *
 * @param argc 命令行参数个数。
 * @param argv 命令行参数数组。
 * @return 进程退出码。
 *
 * @par 流程
 *  1. 初始化日志，使后续每一步都有据可查；
 *  2. 取单实例守卫：拿不到独占权就说明已有实例在运行，
 *     此时已由 onRequest 把旧窗口带到前台，本进程不再建窗口；
 *  3. 取得独占权后创建主窗口并进入事件循环。
 *
 * @note 日志在守卫之前初始化，这样"已有实例"这类正常结局也能留下记录。
 */
int main(int argc, char* argv[])
{
    QApplication app(argc, argv);

    // 固定使用 Qt 内置的 Fusion 风格：不依赖各发行版的主题插件，
    // 在 16.04 到 26.04 上外观一致，也不会因缺少 gtk 平台主题而回退失败。
    QApplication::setStyle(QStringLiteral("Fusion"));
    setupUiFont();
    QApplication::setWindowIcon(QIcon(QStringLiteral(":/icons/number-maze.png")));

    QApplication::setApplicationName(QStringLiteral("数字迷宫"));
    QApplication::setOrganizationName(QStringLiteral("NumberMaze"));
    QApplication::setApplicationVersion(QStringLiteral("2.0"));

    const QString _log_path = setupLogging();
    SL_SPAN("main");
    LOG_INFO("数字迷宫启动");

    singleproc::Options options;
    options.t_name    = g_instance_name;
    options.t_request = g_raise_request;

    const singleproc::Guard guard(options, &onRequest, nullptr);
    if (!guard.is_primary())
    {
        LOG_INFO("已有实例在运行，本次启动不再打开窗口");
        return 0;
    }

    if (_log_path.isEmpty())
    {
        LOG_WARN("日志未能落盘，仅在标准错误输出");
    }

    MainWindow window;
    window.show();

    return QApplication::exec();
}
