#ifndef INCLUDE_SINGLEPROC_OPTIONS_HPP
#define INCLUDE_SINGLEPROC_OPTIONS_HPP

#include <cstddef>
#include <string>

namespace singleproc
{

/// 启动参数。均有默认值，默认值下由实例名派生出锁文件与套接字路径。
struct Options
{
    /// 实例名，必填。互斥与寻址都以它为准。
    std::string t_name;

    /// 存放锁文件与套接字的目录。空表示按规则推导：
    /// 环境变量 XDG_RUNTIME_DIR，未设置时用 /tmp。
    std::string t_directory;

    /// 连接已有实例的超时，毫秒。
    int         t_connect_timeout_ms;

    /// 等待已有实例应答的超时，毫秒。
    int         t_request_timeout_ms;

    /// 单条请求或应答的字节上限。
    std::size_t t_max_message_bytes;

    /// 发现已有实例时发给它的内容。库不解释内容，格式由使用者决定。
    std::string t_request;

    Options();
};

} // namespace singleproc

#endif // INCLUDE_SINGLEPROC_OPTIONS_HPP
