#ifndef SRC_PLATFORM_PLATFORM_HPP
#define SRC_PLATFORM_PLATFORM_HPP

// 平台层：系统调用集中在此，公开头不包含本文件。
// 每项操作返回是否成功，失败原因由调用方通过 status 与 message 反映。

#include <cstddef>
#include <string>

namespace singleproc
{
namespace platform
{

/// 互斥锁。持有期间其他实例无法取得独占权；进程终止时由内核释放。
class FileLock
{
public:
    FileLock();
    ~FileLock();

    /// 尝试以非阻塞方式取得独占锁。已持有或失败返回 false。
    bool acquire(const std::string& path);

    /// 释放。析构时自动调用。
    void release();

    bool held() const;

    /// 系统调用失败时的 errno，无失败为 0。
    int  last_error() const;

private:
    FileLock(const FileLock&)            = delete;
    FileLock& operator=(const FileLock&) = delete;

    int       fd_;
    int       error_;
};

/// 监听套接字。绑定成功即表示独占该地址。
class Listener
{
public:
    Listener();
    ~Listener();

    /// 建立监听。路径上已有文件时：
    /// 若能连上说明有实例在监听，返回 false 且 existing 为真；
    /// 若连接被拒说明是残留，删除后重建。
    bool listen_on(const std::string& path, bool& existing);

    /// 接受一条连接，带超时。没有连接到来时返回 true 且 accepted 为假。
    bool accept_with_timeout(int timeout_ms, int& client, bool& accepted);

    void close();

    bool listening() const;

    int  last_error() const;

private:
    Listener(const Listener&)            = delete;
    Listener& operator=(const Listener&) = delete;

    int       fd_;
    int       error_;
};

/// 连接已有实例。成功返回真并给出描述符。
bool connect_to(const std::string& path, int timeout_ms, int& client, int& error);

/// 关闭描述符。
void close_fd(int fd);

/// 发送一条消息：4 字节大端长度前缀 + 内容。
bool send_message(
    int fd, const std::string& message, std::size_t max_bytes, int timeout_ms, bool& too_large, int& error);

/// 接收一条消息。超时、超过上限、对端关闭分别反映在参数上。
bool recv_message(
    int fd, std::string& message, std::size_t max_bytes, int timeout_ms, bool& too_large, bool& timed_out, int& error);

/// 推导存放锁文件与套接字的目录。目录不存在时尝试建立。
/// fallback 为真表示未指定目录且 XDG_RUNTIME_DIR 不可用，退到了共享目录。
bool        resolve_directory(const std::string& requested, std::string& out, bool& fallback, int& error);

/// 拼出派生路径。超过平台限制时返回假。
bool        make_path(const std::string& directory, const std::string& name, const char* suffix, std::string& out);

/// 平台允许的套接字路径上限，用于诊断信息。
std::size_t socket_path_limit();

/// 删除文件，忽略"不存在"。
void        remove_file(const std::string& path);

} // namespace platform
} // namespace singleproc

#endif // SRC_PLATFORM_PLATFORM_HPP
