#ifndef INCLUDE_STACKLOG_COMMON_FILE_HPP
#define INCLUDE_STACKLOG_COMMON_FILE_HPP

#include <string>

namespace stacklog
{
namespace common
{

/// 逐级创建目录，已存在视为成功。
///
/// 平台专有的目录创建集中在这里；没有该接口的平台上返回 false，
/// 调用方据此把"无法建目录"如实报出，而不是当作成功继续。
bool        make_directories(const char* v_path);

/// 取路径的父目录。没有目录成分时返回空串。
std::string parent_of(const std::string& v_path);

/// 探测目标路径是否可写：建立并删除一个临时文件。
///
/// 只做权限与目录存在性判断，不碰目标文件本身——因此不会因探测而
/// 截断或创建调用方真正想写的文件。目标所在目录不存在时返回 false。
bool        is_writable_directory(const char* v_path);

/// 应用日志的默认目录，按三级回退：XDG_STATE_HOME（须为绝对路径）→
/// $HOME/.local/state → 当前目录。任一环境变量缺失或不合规即落到下一级。
std::string default_state_directory();

/// 默认日志文件的完整路径：<默认目录>/<应用名>/<应用名>.log。
std::string default_log_path(const char* v_application);

} // namespace common
} // namespace stacklog

#endif // INCLUDE_STACKLOG_COMMON_FILE_HPP
