#include "platform/platform.hpp"

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <vector>

namespace singleproc
{
namespace platform
{

namespace
{

/// 等待描述符可读或可写。返回 1 就绪、0 超时、-1 出错。
/// timeout_ms 为负表示一直等到就绪。
int wait_fd(int v_fd, bool v_for_write, int v_timeout_ms)
{
    fd_set set;
    FD_ZERO(&set);
    FD_SET(v_fd, &set);
    timeval  value;
    timeval* limit = 0;
    if (v_timeout_ms >= 0)
    {
        value.tv_sec  = v_timeout_ms / 1000;
        value.tv_usec = (v_timeout_ms % 1000) * 1000;
        limit         = &value;
    }
    return ::select(v_fd + 1, v_for_write ? 0 : &set, v_for_write ? &set : 0, 0, limit);
}

/// 在超时内把全部字节写出。
bool write_all(int v_fd, const char* v_data, std::size_t v_size, int v_timeout_ms, int& v_error)
{
    std::size_t sent = 0;
    while (sent < v_size)
    {
        const int ready = wait_fd(v_fd, true, v_timeout_ms);
        if (ready == 0)
        {
            v_error = ETIMEDOUT;
            return false;
        }
        if (ready < 0)
        {
            v_error = errno;
            return false;
        }
        const ssize_t written = ::send(v_fd, v_data + sent, v_size - sent, MSG_NOSIGNAL);
        if (written > 0)
        {
            sent += static_cast<std::size_t>(written);
            continue;
        }
        if (written < 0 && errno == EINTR)
        {
            continue;
        }
        v_error = written < 0 ? errno : EPIPE;
        return false;
    }
    return true;
}

/// 在超时内读满指定字节。
bool read_all(int v_fd, char* v_data, std::size_t v_size, int v_timeout_ms, int& v_error)
{
    std::size_t got = 0;
    while (got < v_size)
    {
        const int ready = wait_fd(v_fd, false, v_timeout_ms);
        if (ready == 0)
        {
            v_error = ETIMEDOUT;
            return false;
        }
        if (ready < 0)
        {
            v_error = errno;
            return false;
        }
        const ssize_t read = ::recv(v_fd, v_data + got, v_size - got, 0);
        if (read > 0)
        {
            got += static_cast<std::size_t>(read);
            continue;
        }
        if (read < 0 && errno == EINTR)
        {
            continue;
        }
        v_error = read == 0 ? ECONNRESET : errno;
        return false;
    }
    return true;
}

/// 填一个 unix 域地址。路径过长返回假。
bool fill_address(const std::string& v_path, sockaddr_un& v_addr)
{
    if (v_path.size() >= sizeof(v_addr.sun_path))
    {
        return false;
    }
    std::memset(&v_addr, 0, sizeof(v_addr));
    v_addr.sun_family = AF_UNIX;
    std::strncpy(v_addr.sun_path, v_path.c_str(), sizeof(v_addr.sun_path) - 1);
    return true;
}

} // namespace

FileLock::FileLock(): fd_(-1), error_(0) {}

FileLock::~FileLock()
{
    release();
}

bool FileLock::acquire(const std::string& v_path)
{
    release();
    // 权限收紧：锁文件只给当前用户。
    fd_ = ::open(v_path.c_str(), O_CREAT | O_RDWR, 0600);
    if (fd_ < 0)
    {
        error_ = errno;
        return false;
    }
    if (::flock(fd_, LOCK_EX | LOCK_NB) != 0)
    {
        error_ = errno;
        ::close(fd_);
        fd_ = -1;
        return false;
    }
    error_ = 0;
    return true;
}

void FileLock::release()
{
    if (fd_ < 0)
    {
        return;
    }
    // 关闭即释放锁，不依赖显式解锁，这样异常路径也不会把锁留下。
    ::close(fd_);
    fd_ = -1;
}

bool FileLock::held() const
{
    return fd_ >= 0;
}

int FileLock::last_error() const
{
    return error_;
}

Listener::Listener(): fd_(-1), error_(0) {}

Listener::~Listener()
{
    close();
}

bool Listener::listen_on(const std::string& v_path, bool& v_existing)
{
    v_existing = false;
    close();
    error_ = 0;

    int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0)
    {
        error_ = errno;
        return false;
    }

    sockaddr_un addr;
    if (!fill_address(v_path, addr))
    {
        error_ = ENAMETOOLONG;
        ::close(fd);
        return false;
    }

    if (::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0)
    {
        if (errno != EADDRINUSE)
        {
            error_ = errno;
            ::close(fd);
            return false;
        }

        // 地址被占用：区分"确有实例在监听"与"崩溃留下的死文件"。
        int probe       = -1;
        int probe_error = 0;
        if (connect_to(v_path, 200, probe, probe_error))
        {
            // 连上了：确有实例。让调用方去与它交互。
            close_fd(probe);
            v_existing = true;
            error_     = EADDRINUSE;
            ::close(fd);
            return false;
        }
        if (probe_error != ECONNREFUSED && probe_error != ENOENT)
        {
            // 既不是"有人在听"，也不是明确的无人在听：不猜，报错。
            error_ = probe_error;
            ::close(fd);
            return false;
        }

        // 连接被拒：是残留文件，删掉重建。
        remove_file(v_path);
        if (::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0)
        {
            error_ = errno;
            ::close(fd);
            return false;
        }
    }

    if (::listen(fd, 8) != 0)
    {
        error_ = errno;
        ::close(fd);
        return false;
    }

    // 只允许当前用户连接。
    ::chmod(v_path.c_str(), 0600);
    fd_ = fd;
    return true;
}

bool Listener::accept_with_timeout(int v_timeout_ms, int& v_client, bool& v_accepted)
{
    v_accepted = false;
    v_client   = -1;
    if (fd_ < 0)
    {
        error_ = EBADF;
        return false;
    }
    const int ready = wait_fd(fd_, false, v_timeout_ms);
    if (ready == 0)
    {
        return true;
    }
    if (ready < 0)
    {
        error_ = errno;
        return false;
    }
    const int fd = ::accept(fd_, 0, 0);
    if (fd < 0)
    {
        if (errno == EINTR || errno == ECONNABORTED)
        {
            return true;
        }
        error_ = errno;
        return false;
    }
    v_client   = fd;
    v_accepted = true;
    return true;
}

void Listener::close()
{
    if (fd_ >= 0)
    {
        ::close(fd_);
        fd_ = -1;
    }
}

bool Listener::listening() const
{
    return fd_ >= 0;
}

int Listener::last_error() const
{
    return error_;
}

bool connect_to(const std::string& v_path, int v_timeout_ms, int& v_client, int& v_error)
{
    v_client = -1;
    v_error  = 0;
    int fd   = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0)
    {
        v_error = errno;
        return false;
    }

    sockaddr_un addr;
    if (!fill_address(v_path, addr))
    {
        v_error = ENAMETOOLONG;
        ::close(fd);
        return false;
    }

    // unix 域连接在同一个系统调用内完成；用非阻塞 + select 来处理"没人应答"。
    const int flags = ::fcntl(fd, F_GETFL, 0);
    ::fcntl(fd, F_SETFL, flags | O_NONBLOCK);

    const int result = ::connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr));
    if (result != 0 && errno != EINPROGRESS && errno != EAGAIN)
    {
        v_error = errno;
        ::close(fd);
        return false;
    }
    if (result != 0)
    {
        const int ready = wait_fd(fd, true, v_timeout_ms);
        if (ready <= 0)
        {
            v_error = ready == 0 ? ETIMEDOUT : errno;
            ::close(fd);
            return false;
        }
        int       so_error = 0;
        socklen_t length   = sizeof(so_error);
        if (::getsockopt(fd, SOL_SOCKET, SO_ERROR, &so_error, &length) != 0 || so_error != 0)
        {
            v_error = so_error != 0 ? so_error : errno;
            ::close(fd);
            return false;
        }
    }

    ::fcntl(fd, F_SETFL, flags);
    v_client = fd;
    return true;
}

void close_fd(int v_fd)
{
    if (v_fd >= 0)
    {
        ::close(v_fd);
    }
}

bool send_message(
    int v_fd, const std::string& v_message, std::size_t v_max_bytes, int v_timeout_ms, bool& v_too_large, int& v_error)
{
    v_too_large = false;
    v_error     = 0;
    if (v_message.size() > v_max_bytes)
    {
        v_too_large = true;
        return false;
    }

    const unsigned long size      = v_message.size();
    const char          header[4] = {static_cast<char>((size >> 24) & 0xFF),
                                     static_cast<char>((size >> 16) & 0xFF),
                                     static_cast<char>((size >> 8) & 0xFF),
                                     static_cast<char>(size & 0xFF)};
    if (!write_all(v_fd, header, sizeof(header), v_timeout_ms, v_error))
    {
        return false;
    }
    if (v_message.empty())
    {
        return true;
    }
    return write_all(v_fd, v_message.data(), v_message.size(), v_timeout_ms, v_error);
}

bool recv_message(int          v_fd,
                  std::string& v_message,
                  std::size_t  v_max_bytes,
                  int          v_timeout_ms,
                  bool&        v_too_large,
                  bool&        v_timed_out,
                  int&         v_error)
{
    v_too_large = false;
    v_timed_out = false;
    v_error     = 0;
    v_message.clear();

    char header[4];
    if (!read_all(v_fd, header, sizeof(header), v_timeout_ms, v_error))
    {
        v_timed_out = v_error == ETIMEDOUT;
        return false;
    }
    const std::size_t size = (static_cast<std::size_t>(static_cast<unsigned char>(header[0])) << 24) |
                             (static_cast<std::size_t>(static_cast<unsigned char>(header[1])) << 16) |
                             (static_cast<std::size_t>(static_cast<unsigned char>(header[2])) << 8) |
                             static_cast<std::size_t>(static_cast<unsigned char>(header[3]));
    if (size > v_max_bytes)
    {
        // 超限即拒收：不截断、不丢弃后继续，让调用方知道这条消息不完整。
        v_too_large = true;
        return false;
    }
    if (size == 0)
    {
        return true;
    }

    std::vector<char> buffer(size);
    if (!read_all(v_fd, buffer.data(), size, v_timeout_ms, v_error))
    {
        v_timed_out = v_error == ETIMEDOUT;
        return false;
    }
    v_message.assign(buffer.data(), size);
    return true;
}

bool resolve_directory(const std::string& v_requested, std::string& v_out, bool& v_fallback, int& v_error)
{
    v_error    = 0;
    v_fallback = false;
    if (!v_requested.empty())
    {
        v_out = v_requested;
    }
    else
    {
        const char* runtime = ::getenv("XDG_RUNTIME_DIR");
        if (runtime != 0 && runtime[0] != '\0')
        {
            v_out = runtime;
        }
        else
        {
            // 退到共享目录：这在多用户机器上不是期望的位置，必须让调用方看得见，
            // 不能悄悄换了路线。
            v_out      = "/tmp";
            v_fallback = true;
        }
    }

    struct stat info;
    if (::stat(v_out.c_str(), &info) == 0)
    {
        if (!S_ISDIR(info.st_mode))
        {
            v_error = ENOTDIR;
            return false;
        }
        return true;
    }

    if (::mkdir(v_out.c_str(), 0700) != 0 && errno != EEXIST)
    {
        v_error = errno;
        return false;
    }
    return true;
}

bool make_path(const std::string& v_directory, const std::string& v_name, const char* v_suffix, std::string& v_out)
{
    v_out = v_directory;
    if (!v_out.empty() && v_out[v_out.size() - 1] != '/')
    {
        v_out.push_back('/');
    }
    v_out += v_name;
    v_out += v_suffix;
    return v_out.size() < socket_path_limit();
}

std::size_t socket_path_limit()
{
    return sizeof(sockaddr_un().sun_path);
}

void remove_file(const std::string& v_path)
{
    ::unlink(v_path.c_str());
}

} // namespace platform
} // namespace singleproc
