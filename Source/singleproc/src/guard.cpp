#include <singleproc/guard.hpp>

#include <cerrno>
#include <cstdio>
#include <cstring>

#include "platform/platform.hpp"

namespace singleproc
{

namespace
{

const char* const g_k_no_message = "正常";

std::string       no_request(const std::string& /*unused*/)
{
    return std::string();
}

bool keep_running(const std::string& /*unused*/)
{
    return true;
}

} // namespace

struct Guard::Impl
{
    Options            t_options;
    OnRequest          t_on_request;
    OnResponse         t_on_response;
    bool               t_has_response_handler;
    Status             t_status;
    std::string        t_directory;
    std::string        t_lock_path;
    std::string        t_socket_path;
    bool               t_fallback_directory;
    std::string        t_request;
    std::string        t_response;
    const char*        t_message;
    platform::FileLock t_lock;
    platform::Listener t_listener;

    // 初始化顺序与成员声明一致，避免仅由声明顺序决定的隐患。
    Impl()
        : t_has_response_handler(false), t_status(Status::Primary), t_fallback_directory(false),
          t_message(g_k_no_message)

    {}

    /// 次实例：连上已有实例、交换一条请求与应答，并把应答交给使用者回调。
    /// 任何一步失败都落成明确的状态，不静默继续。
    void talk_to_existing();
};

void Guard::Impl::talk_to_existing()
{
    int client = -1;
    int error  = 0;
    if (!platform::connect_to(t_socket_path, t_options.t_connect_timeout_ms, client, error))
    {
        if (error == ETIMEDOUT)
        {
            t_status  = Status::PeerTimeout;
            t_message = "连接已有实例超时";
        }
        else
        {
            t_status  = Status::FailedConnect;
            t_message = "无法连接已有实例";
        }
        return;
    }

    bool too_large = false;
    if (!platform::send_message(
            client, t_request, t_options.t_max_message_bytes, t_options.t_request_timeout_ms, too_large, error))
    {
        platform::close_fd(client);
        t_status  = too_large ? Status::MessageTooLarge : Status::FailedConnect;
        t_message = too_large ? "请求超过长度上限" : "发送请求失败";
        return;
    }

    bool timed_out = false;
    if (!platform::recv_message(client,
                                t_response,
                                t_options.t_max_message_bytes,
                                t_options.t_request_timeout_ms,
                                too_large,
                                timed_out,
                                error))
    {
        platform::close_fd(client);
        if (too_large)
        {
            t_status  = Status::MessageTooLarge;
            t_message = "应答超过长度上限";
        }
        else if (timed_out)
        {
            t_status  = Status::PeerTimeout;
            t_message = "等待已有实例应答超时";
        }
        else
        {
            t_status  = Status::FailedConnect;
            t_message = "已有实例连接中断";
        }
        return;
    }
    platform::close_fd(client);

    // 交换完成：这才是 secondary 的准确含义。
    t_status  = Status::Secondary;
    t_message = "已有实例在运行，已完成一次请求应答交换";

    // 是否继续运行由使用者决定；库只把结果交出去，不代为退出。
    t_on_response(t_response);
}

const char* status_name(Status v_status)
{
    switch (v_status)
    {
    case Status::Primary:
        return "primary";
    case Status::Secondary:
        return "secondary";
    case Status::OwnedByOther:
        return "owned_by_other";
    case Status::FailedLock:
        return "failed_lock";
    case Status::FailedSocket:
        return "failed_socket";
    case Status::FailedConnect:
        return "failed_connect";
    case Status::PeerTimeout:
        return "peer_timeout";
    case Status::MessageTooLarge:
        return "message_too_large";
    case Status::NotPrimary:
        return "not_primary";
    case Status::PathTooLong:
        return "path_too_long";
    }
    return "unknown";
}

Options::Options(): t_connect_timeout_ms(200), t_request_timeout_ms(500), t_max_message_bytes(1024 * 1024) {}

Guard::Guard(const Options& v_options, const OnRequest& v_on_request, const OnResponse& v_on_response)
    : m_impl(new Impl())
{
    m_impl->t_options              = v_options;
    m_impl->t_request              = v_options.t_request; // 次实例要发的内容由调用方给定
    // 回调为空时用"空请求 + 不反对继续"的等价实现，避免在调用点上判空。
    m_impl->t_on_request           = v_on_request ? v_on_request : OnRequest(&no_request);
    m_impl->t_on_response          = v_on_response ? v_on_response : OnResponse(&keep_running);
    m_impl->t_has_response_handler = static_cast<bool>(v_on_response);

    if (v_options.t_name.empty())
    {
        m_impl->t_status  = Status::FailedLock;
        m_impl->t_message = "实例名为空";
        return;
    }

    // 路径推导失败与路径过长都不静默改用别的名字。
    std::string directory;
    int         error = 0;
    if (!platform::resolve_directory(v_options.t_directory, directory, m_impl->t_fallback_directory, error))
    {
        m_impl->t_status  = Status::FailedLock;
        m_impl->t_message = "目录不可用";
        return;
    }
    m_impl->t_directory = directory;
    if (!platform::make_path(directory, v_options.t_name, ".lock", m_impl->t_lock_path))
    {
        m_impl->t_status  = Status::PathTooLong;
        m_impl->t_message = "锁文件路径超过平台限制";
        return;
    }
    if (!platform::make_path(directory, v_options.t_name, ".sock", m_impl->t_socket_path))
    {
        m_impl->t_status  = Status::PathTooLong;
        m_impl->t_message = "套接字路径超过平台限制";
        return;
    }

    // 第一步：取锁。拿不到就说明确有实例在跑。
    if (!m_impl->t_lock.acquire(m_impl->t_lock_path))
    {
        const int lock_error = m_impl->t_lock.last_error();
        if (lock_error != EWOULDBLOCK && lock_error != EAGAIN && lock_error != EACCES)
        {
            m_impl->t_status  = Status::FailedLock;
            m_impl->t_message = "锁文件无法建立";
            return;
        }
        m_impl->t_status          = Status::OwnedByOther;
        m_impl->t_message         = "已有实例在运行";
        // 只有调用方表达了交互意图（给了要发的内容或给了处理应答的回调）才去连它。
        // 否则每次启动都要白等一个超时，纯互斥的用法不该付这个代价。
        const bool wants_exchange = !m_impl->t_options.t_request.empty() || m_impl->t_has_response_handler;
        if (wants_exchange)
        {
            m_impl->talk_to_existing();
        }
        return;
    }

    // 第二步：建立监听。已有实例在监听时不会走到这里——锁已经保证了独占。
    bool existing = false;
    if (!m_impl->t_listener.listen_on(m_impl->t_socket_path, existing))
    {
        if (existing)
        {
            // 理论上不可达：拿到锁却有人在监听，说明锁与套接字不同步。
            // 不猜，如实报出来。
            m_impl->t_status  = Status::FailedSocket;
            m_impl->t_message = "拿到锁但地址已被监听";
            m_impl->t_lock.release();
            return;
        }
        const int listen_error = m_impl->t_listener.last_error();
        m_impl->t_status       = listen_error == ENAMETOOLONG ? Status::PathTooLong : Status::FailedSocket;
        m_impl->t_message = listen_error == ENAMETOOLONG ? "套接字路径超过平台限制" : "套接字建立失败";
        m_impl->t_lock.release();
        return;
    }

    m_impl->t_status  = Status::Primary;
    m_impl->t_message = g_k_no_message;
}

Guard::~Guard()
{
    if (m_impl == 0)
    {
        return;
    }
    // 只有真正建立过监听的本人才删除套接字文件。次实例从未建立它，
    // 顺手删掉会把正在运行的主实例的地址抹掉，后续连接全部落空。
    if (m_impl->t_listener.listening())
    {
        // 先关监听再删文件：顺序反了会让下一个实例在残留地址上短暂看到"有人在监听"。
        m_impl->t_listener.close();
        platform::remove_file(m_impl->t_socket_path);
    }
    m_impl->t_lock.release();
    delete m_impl;
}

Status Guard::status() const
{
    return m_impl->t_status;
}

bool Guard::is_primary() const
{
    return m_impl->t_status == Status::Primary;
}

const std::string& Guard::request() const
{
    return m_impl->t_request;
}

const std::string& Guard::response() const
{
    return m_impl->t_response;
}

const char* Guard::message() const
{
    return m_impl->t_message;
}

const std::string& Guard::directory() const
{
    return m_impl->t_directory;
}

bool Guard::used_fallback_directory() const
{
    return m_impl->t_fallback_directory;
}

Status Guard::serve_once()
{
    if (m_impl->t_status != Status::Primary)
    {
        return Status::NotPrimary;
    }

    int  client   = -1;
    bool accepted = false;
    // 负数表示一直等到有连接为止：写成 while(...) serve_once() 才不会空转。
    if (!m_impl->t_listener.accept_with_timeout(-1, client, accepted))
    {
        return Status::FailedSocket;
    }
    if (!accepted)
    {
        return Status::NotPrimary;
    }

    std::string request;
    bool        too_large = false;
    bool        timed_out = false;
    int         error     = 0;
    if (!platform::recv_message(client,
                                request,
                                m_impl->t_options.t_max_message_bytes,
                                m_impl->t_options.t_request_timeout_ms,
                                too_large,
                                timed_out,
                                error))
    {
        platform::close_fd(client);
        if (too_large)
        {
            return Status::MessageTooLarge;
        }
        if (timed_out)
        {
            return Status::PeerTimeout;
        }
        return Status::FailedConnect;
    }

    const std::string response = m_impl->t_on_request(request);
    if (!platform::send_message(client,
                                response,
                                m_impl->t_options.t_max_message_bytes,
                                m_impl->t_options.t_request_timeout_ms,
                                too_large,
                                error))
    {
        platform::close_fd(client);
        return too_large ? Status::MessageTooLarge : Status::FailedConnect;
    }

    platform::close_fd(client);
    return Status::Primary;
}

} // namespace singleproc
